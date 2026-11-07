from typing import Literal
from pydantic import BaseModel, Field
from src.models.register_scene import LearningStage, SettingType

CheckpointCode = Literal["day_0", "week_4", "week_8"]
PressureTestType = Literal["monologue_90s", "live_register_switch", "self_repair_wait"]


class MonologueChallenge(BaseModel):
    user_id: int
    topic: str
    duration_seconds: int = 90
    skippable: bool = True
    skipped_topics: list[str] = Field(default_factory=list)
    min_words_threshold: int = 110
    max_hesitation_Pause_ratio: float = 0.25


class SelfRepairState(BaseModel):
    user_id: int
    active: bool = True
    waited_once: bool = False
    stalled_span: str = ""


class BenchmarkRubricScore(BaseModel):
    user_id: int
    checkpoint: CheckpointCode
    task_prompt: str
    fluency_retrieval: float = Field(ge=0.0, le=4.0)
    lexical_precision: float = Field(ge=0.0, le=4.0)
    register_fit: float = Field(ge=0.0, le=4.0)
    pragmatic_control: float = Field(ge=0.0, le=4.0)
    recorded_at: str = ""

    @property
    def composite_score(self) -> float:
        return round(
            (self.fluency_retrieval + self.lexical_precision + self.register_fit + self.pragmatic_control) / 4.0,
            2,
        )


class ProgressReport(BaseModel):
    user_id: int
    is_private: bool = True
    leaderboard_enabled: bool = False
    current_competence: dict[str, float]
    growth_vs_baseline: dict[str, float]
    retention_counts: dict[str, int]
    e3_plus_total: int = 0
    benchmark_delta: float | None = None
    active_stage: LearningStage = "S1"
    target_register_shift: SettingType | None = None
