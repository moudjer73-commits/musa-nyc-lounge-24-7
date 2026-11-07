from typing import Literal
from pydantic import BaseModel, Field
from src.models.chunk import ELevel, RetentionState
from src.models.register_scene import SettingType

InteractionSkill = Literal[
    "turn_taking",
    "backchannels",
    "polite_interruption",
    "floor_holding",
    "repair_clarification",
    "topic_change",
    "closing",
]

BackchannelFunction = Literal["continuer", "empathy", "agreement", "surprise", "clarification_prompt"]
ReEntryBlock = Literal[
    "warm_natural_talk_2m",
    "unprompted_retrieval_3m",
    "skill_and_difficult_target_3m",
    "comparison_with_last_evidence_2m",
    "completed",
]


class InteractionDimensionScores(BaseModel):
    recognition: float = Field(default=2.0, ge=0.0, le=4.0)
    timing: float = Field(default=2.0, ge=0.0, le=4.0)
    pragmatic_appropriateness: float = Field(default=2.0, ge=0.0, le=4.0)
    execution: float = Field(default=2.0, ge=0.0, le=4.0)
    transfer: float = Field(default=2.0, ge=0.0, le=4.0)

    @property
    def average(self) -> float:
        return round(
            (
                self.recognition
                + self.timing
                + self.pragmatic_appropriateness
                + self.execution
                + self.transfer
            )
            / 5.0,
            2,
        )


class StorytellingDiagnostic(BaseModel):
    has_orientation: bool = False
    has_complication: bool = False
    has_development: bool = False
    has_evaluation: bool = False
    has_resolution: bool = False
    missing_elements: list[str] = Field(default_factory=list)
    diagnostic_note: str = ""


class SmallTalkTurnEvaluation(BaseModel):
    has_reciprocal_sharing: bool = False
    ended_with_question: bool = False
    interview_penalty_applied: bool = False
    feedback_note: str = ""


class ReEntryTargetOutcome(BaseModel):
    chunk_id: str
    previous_e_level: ELevel
    new_e_level: ELevel
    classification: RetentionState
    evidence_comparison: str
