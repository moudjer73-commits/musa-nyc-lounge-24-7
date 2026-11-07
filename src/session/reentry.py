from datetime import datetime, timezone
from pydantic import BaseModel, Field
from src.db.chunk_repo import ChunkRepository
from src.models.chunk import ChunkMasteryRecord, RetentionState
from src.models.interaction_reentry import ReEntryBlock, ReEntryTargetOutcome

REENTRY_AWAY_THRESHOLD_DAYS = 10

REENTRY_BLOCKS: list[tuple[ReEntryBlock, int, str]] = [
    ("warm_natural_talk_2m", 2, "2 min: Warm natural talk—ease back into spontaneous flow."),
    ("unprompted_retrieval_3m", 3, "3 min: Unprompted retrieval of previously mastered targets."),
    ("skill_and_difficult_target_3m", 3, "3 min: One interactional skill + one challenging target."),
    ("comparison_with_last_evidence_2m", 2, "2 min: Honest comparison with your last recorded evidence."),
]


class ReEntrySessionState(BaseModel):
    user_id: int
    days_away: int
    block_index: int = 0
    focus_skill: str = "polite_interruption"
    difficult_chunk_id: str = "disagree_intel_01"
    outcomes: list[ReEntryTargetOutcome] = Field(default_factory=list)

    @property
    def current_block(self) -> ReEntryBlock:
        if self.block_index >= len(REENTRY_BLOCKS):
            return "completed"
        return REENTRY_BLOCKS[self.block_index][0]

    @property
    def total_planned_minutes(self) -> int:
        return sum(b[1] for b in REENTRY_BLOCKS)


class ReEntrySessionManager:
    def __init__(self, chunk_repo: ChunkRepository) -> None:
        self.chunk_repo = chunk_repo
        self._sessions: dict[int, ReEntrySessionState] = {}

    def needs_reentry_session(self, last_active_iso: str | None, now: datetime | None = None) -> tuple[bool, int]:
        if not last_active_iso:
            return False, 0
        ref = now or datetime.now(timezone.utc)
        last_dt = datetime.fromisoformat(last_active_iso)
        days = max(0, (ref - last_dt).days)
        return (days >= REENTRY_AWAY_THRESHOLD_DAYS), days

    def start_reentry_session(self, user_id: int, days_away: int) -> ReEntrySessionState:
        records = self.chunk_repo.list_user_records(user_id)
        diff_id = records[0].chunk_id if records else "disagree_intel_01"
        state = ReEntrySessionState(user_id=user_id, days_away=days_away, difficult_chunk_id=diff_id)
        self._sessions[user_id] = state
        return state

    def advance_block(self, user_id: int) -> tuple[ReEntryBlock, str]:
        state = self._sessions.get(user_id) or self.start_reentry_session(user_id, days_away=10)
        state.block_index = min(state.block_index + 1, len(REENTRY_BLOCKS))
        if state.current_block == "completed":
            return ("completed", "10-minute reactivation session complete! Welcome back.")
        return (state.current_block, REENTRY_BLOCKS[state.block_index][2])

    def classify_target_on_return(
        self,
        record: ChunkMasteryRecord,
        retrieved_unprompted: bool,
        retrieved_after_cue: bool,
        collocation_intact: bool,
        evidence_clear: bool = True,
    ) -> ReEntryTargetOutcome:
        """Blueprint 4.10: Classify targets (retained / weakened / temporarily_inaccessible / uncertain).
        CRITICAL: Do NOT demote E3/E4 automatically."""
        prev_level = record.e_level
        if not evidence_clear:
            status: RetentionState = "uncertain"
            comparison = "Evidence inconclusive in this turn; keeping prior level intact."
        elif retrieved_unprompted and collocation_intact:
            status = "retained"
            comparison = f"Matches your prior {prev_level} independent retrieval."
        elif retrieved_unprompted and not collocation_intact:
            status = "weakened"
            comparison = f"Core chunk retrieved, but collocation slipped compared to last {prev_level} session."
        elif retrieved_after_cue:
            status = "temporarily_inaccessible"
            comparison = f"Temporarily inaccessible without a cue after break; {prev_level} preserved."
        else:
            status = "temporarily_inaccessible"
            comparison = f"Needs a quick refresher tomorrow; {prev_level} preserved without automatic demotion."

        record.retention_state = status
        # Strictly preserve E3/E4 (no automatic demotion)
        self.chunk_repo.save_record(record)
        return ReEntryTargetOutcome(
            chunk_id=record.chunk_id,
            previous_e_level=prev_level,
            new_e_level=record.e_level,
            classification=status,
            evidence_comparison=comparison,
        )
