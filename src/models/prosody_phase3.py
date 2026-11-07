from typing import Literal
from pydantic import BaseModel, Field

DialectRegion = Literal["Maghrebi", "Egyptian", "Levantine", "Gulf", "unspecified"]
ProsodyTargetCode = Literal[
    "p_vs_b", "american_r", "word_stress_and_vowel_reduction", "sentence_intonation",
    "french_uvular_r_and_final_stress_transfer", "epenthetic_vowel_clusters",
    "creaky_voice", "wide_pitch_excursions",
]
LearnerAmbition = Literal["intelligibility_and_presence", "near_native_polish"]
EvidenceStrength = Literal["well_supported_core", "thin_evidence_hypothesis"]

class ProsodyDrillItem(BaseModel):
    target_code: ProsodyTargetCode
    title: str
    recognition_only: bool = False
    requires_french_background: bool = False
    dialect_hypothesis: DialectRegion | None = None
    evidence_strength: EvidenceStrength = "well_supported_core"
    minimal_pair_or_prompt: str
    recheck_in_free_talk_weekly: bool = True

class ProsodySessionBlock(BaseModel):
    user_id: int
    duration_minutes: int = 5
    isolated_from_free_talk: bool = True
    ambition: LearnerAmbition = "intelligibility_and_presence"
    active_drills: list[ProsodyDrillItem] = Field(default_factory=list)
    feedback_disclaimer: str = (
        "Automated pronunciation feedback is a practice aid, not a judge. "
        "Goal: intelligibility and presence."
    )

class HumorExercise(BaseModel):
    exercise_id: str
    mode: Literal["recognition", "production"]
    register_setting: str
    prompt_text: str
    explanation: str

class AggregateGroupMilestone(BaseModel):
    milestone_name: str
    leaderboard_enabled: bool = False
    private_records_exposed: bool = False
    total_active_learners: int = 0
    collective_sessions_completed: int = 0
    collective_e3_targets_reached: int = 0

class PilotMetricsSummary(BaseModel):
    user_id: int
    days_of_use: int = 0
    targets_reaching_e3: int = 0
    avg_correction_usefulness_rating: float = 0.0
    fixed_task_before_after_delta: float | None = None
