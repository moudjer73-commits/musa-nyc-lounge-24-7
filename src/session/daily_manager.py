from typing import Literal
from pydantic import BaseModel, Field
from src.analyst.engine import AnalystEngine
from src.chunks.scheduler import AcquisitionScheduler, ChunkBank
from src.db.chunk_repo import ChunkRepository

SessionPhase = Literal[
    "warm_natural_talk",
    "exposure_to_chunks",
    "guided_interaction",
    "spontaneous_production",
    "one_new_situation",
    "feedback_one_target",
    "completed",
]

DayCode = Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7"]

PHASE_SPECS: list[tuple[SessionPhase, int, str]] = [
    ("warm_natural_talk", 1, "1 min: Warm natural talk—settle into the scene."),
    ("exposure_to_chunks", 2, "2 min: Natural exposure to 2–3 target chunks inside conversation."),
    ("guided_interaction", 2, "2 min: Guided interaction carrying today's interactional skill."),
    ("spontaneous_production", 3, "3 min: Unprompted spontaneous production (no target hints shown)."),
    ("one_new_situation", 1, "1 min: Quick shift to one new situation/angle."),
    ("feedback_one_target", 1, "1 min: Focused feedback with ONE improvement target."),
]

WEEKLY_PLAN: dict[DayCode, tuple[str, str]] = {
    "D1": ("acquisition", "polite_disagreement_in_context"),
    "D2": ("retrieval_without_showing_targets", "clarification_and_repair"),
    "D3": ("transfer_to_another_topic", "holding_the_floor"),
    "D4": ("transfer_to_another_register", "diplomatic_pushback"),
    "D5": ("integrated_conversation", "reciprocal_small_talk_and_pivot"),
    "D6": ("listening_podcast_and_prosody_lab", "backchanneling_and_stance"),
    "D7": ("pressure_tests", "real_time_self_repair"),
}


class DailySessionState(BaseModel):
    user_id: int
    session_id: str
    day_code: DayCode = "D1"
    day_focus: str = "acquisition"
    interactional_situation: str = "polite_disagreement_in_context"
    phase_index: int = 0
    target_chunk_ids: list[str] = Field(default_factory=list)
    show_targets_to_learner: bool = True

    @property
    def current_phase(self) -> SessionPhase:
        if self.phase_index >= len(PHASE_SPECS):
            return "completed"
        return PHASE_SPECS[self.phase_index][0]

    @property
    def planned_minutes(self) -> int:
        return sum(spec[1] for spec in PHASE_SPECS)


class DailySessionManager:
    def __init__(
        self,
        chunk_repo: ChunkRepository,
        bank: ChunkBank,
        scheduler: AcquisitionScheduler,
        analyst: AnalystEngine,
    ) -> None:
        self.chunk_repo = chunk_repo
        self.bank = bank
        self.scheduler = scheduler
        self.analyst = analyst
        self._active: dict[int, DailySessionState] = {}

    def start_session(self, user_id: int, day_number: int = 1) -> DailySessionState:
        day_idx = ((day_number - 1) % 7) + 1
        day_code: DayCode = f"D{day_idx}"  # type: ignore[assignment]
        focus, situation = WEEKLY_PLAN[day_code]
        # Blueprint Section 3: D2 is retrieval without showing targets; never overfill
        chunks = self.bank.select_chunks(limit=2)
        state = DailySessionState(
            user_id=user_id,
            session_id=f"daily_{user_id}_{day_code}",
            day_code=day_code,
            day_focus=focus,
            interactional_situation=situation,
            phase_index=0,
            target_chunk_ids=[c.chunk_id for c in chunks],
            show_targets_to_learner=(day_code == "D1"),
        )
        self._active[user_id] = state
        return state

    def advance_phase(self, user_id: int) -> tuple[SessionPhase, str]:
        state = self._active.get(user_id) or self.start_session(user_id)
        state.phase_index = min(state.phase_index + 1, len(PHASE_SPECS))
        if state.current_phase == "spontaneous_production":
            state.show_targets_to_learner = False
        if state.current_phase == "feedback_one_target":
            targets = self.analyst.repo.get_session_summary_targets(
                user_id, state.session_id, max_targets=1
            )
            if targets:
                t = targets[0]
                return (
                    "feedback_one_target",
                    f"1-Min Feedback (ONE Target): Instead of \"{t['evidence_span']}\", use \"{t['suggested_alternative']}\".",
                )
            return ("feedback_one_target", "1-Min Feedback (ONE Target): Clean production—keep your current pacing!")
        if state.current_phase == "completed":
            return ("completed", "10-minute session complete! Do not add filler material—rest and let it consolidate.")
        return (state.current_phase, PHASE_SPECS[state.phase_index][2])
