import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.config import DATA_DIR
from src.models.chunk import ChunkMasteryRecord, FunctionalChunk, ProductionAttempt
from src.models.register_scene import LearningStage, SettingType, SpeechFunction

REVIEW_INTERVAL_DAYS = {
    "effortless": 7,
    "slow": 3,
    "hesitant": 2,
    "failed": 1,
    "successful_transfer": 14,
}


class ChunkBank:
    def __init__(self, json_path: Path = DATA_DIR / "vetted_chunks.json") -> None:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        self.chunks: dict[str, FunctionalChunk] = {
            item["chunk_id"]: FunctionalChunk.model_validate(item) for item in raw
        }

    def get_chunk(self, chunk_id: str) -> FunctionalChunk:
        return self.chunks[chunk_id]

    def select_chunks(
        self, register_type: SettingType | None = None, function: SpeechFunction | None = None, limit: int = 3
    ) -> list[FunctionalChunk]:
        results = list(self.chunks.values())
        if register_type:
            results = [c for c in results if c.register_type == register_type] or results
        if function:
            results = [c for c in results if c.function == function] or results
        return results[:limit]

    def get_varied_prompt(self, chunk_id: str, last_idx: int) -> tuple[int, str, str]:
        """Returns (new_idx, retrieval_prompt, example) guaranteed not to repeat last_idx."""
        chunk = self.chunks[chunk_id]
        next_idx = (last_idx + 1) % len(chunk.retrieval_prompts)
        ex_idx = next_idx % len(chunk.examples)
        return next_idx, chunk.retrieval_prompts[next_idx], chunk.examples[ex_idx]


class AcquisitionScheduler:
    def evaluate_attempt(
        self,
        record: ChunkMasteryRecord,
        attempt: ProductionAttempt,
        now: datetime | None = None,
    ) -> ChunkMasteryRecord:
        ref_now = now or datetime.now(timezone.utc)
        valid_execution = attempt.collocation_accurate and attempt.meaning_and_function_accurate

        if not valid_execution or attempt.speed == "failed":
            record.retry_in_same_session = True
            record.interval_days = REVIEW_INTERVAL_DAYS["failed"]
            record.next_review_at = (ref_now + timedelta(days=1)).isoformat()
            if record.e_level in ("E3", "E4"):
                record.retention_state = "weakened"
            elif record.e_level == "E0":
                record.e_level = "E1"
            return record

        record.retry_in_same_session = False
        record.retention_state = "active"
        if attempt.context_register not in record.succeeded_registers:
            record.succeeded_registers.append(attempt.context_register)

        # Blueprint 4.4: Immediate echo, displayed target, or hint assistance caps evidence at E2
        if attempt.target_displayed or attempt.assisted_by_hint or attempt.immediate_echo_of_alex:
            if record.e_level in ("E0", "E1"):
                record.e_level = "E2"
        else:
            # Independent unprompted retrieval in a distinct session
            if attempt.session_id not in record.unprompted_session_ids:
                record.unprompted_session_ids.append(attempt.session_id)

            if len(record.unprompted_session_ids) == 1 and record.e_level in ("E0", "E1"):
                record.e_level = "E2"
            elif len(record.unprompted_session_ids) >= 2:
                is_transfer = (
                    attempt.transferred_to_new_context
                    or attempt.context_register != record.baseline_register
                    or len(record.succeeded_registers) >= 2
                )
                if is_transfer and record.e_level in ("E3", "E4"):
                    record.e_level = "E4"
                elif record.e_level in ("E0", "E1", "E2"):
                    record.e_level = "E4" if attempt.transferred_to_new_context else "E3"

        effective_speed = "successful_transfer" if record.e_level == "E4" and attempt.transferred_to_new_context else attempt.speed
        days = 1 if (effective_speed == "hesitant" and record.e_level in ("E0", "E1", "E2")) else REVIEW_INTERVAL_DAYS[effective_speed]
        record.interval_days = days
        record.next_review_at = (ref_now + timedelta(days=days)).isoformat()
        return record

    def evaluate_learner_stage(
        self, e3_plus_count: int, sustained_3min: bool, three_context_pragmatics: bool, three_register_agility: bool, expressive_control: bool, s1_threshold: int = 40
    ) -> LearningStage:
        if expressive_control and three_register_agility:
            return "S5"
        if three_register_agility and three_context_pragmatics:
            return "S4"
        if three_context_pragmatics and sustained_3min:
            return "S3"
        if sustained_3min and e3_plus_count >= s1_threshold:
            return "S2"
        return "S1"
