from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator
from src.config import GoalType, get_initial_register_weights, normalize_register_weights

ConfidenceLabel = Literal["provisional", "supported", "well_established"]
CorrectionStyle = Literal["immediate", "delayed", "selective"]
ArabicSupport = Literal["none", "short_post_turn_notes", "bilingual"]
DialectStatus = Literal["hypothesis", "supported", "rejected"]


def compute_confidence_label(evidence_count: int, sessions_count: int) -> ConfidenceLabel:
    if evidence_count >= 6 and sessions_count >= 2:
        return "well_established"
    if evidence_count >= 3:
        return "supported"
    return "provisional"


class DialectHypothesis(BaseModel):
    dialect_name: str = "unspecified"
    status: DialectStatus = "hypothesis"
    confidence: ConfidenceLabel = "provisional"
    evidence_spans: list[str] = Field(default_factory=list)

    def record_observation(self, span: str, supports: bool, sessions_count: int) -> None:
        if span and span not in self.evidence_spans:
            self.evidence_spans.append(span)
        self.confidence = compute_confidence_label(len(self.evidence_spans), sessions_count)
        if len(self.evidence_spans) >= 2:
            self.status = "supported" if supports else "rejected"


class DimensionScore(BaseModel):
    score: float = Field(default=2.0, ge=0.0, le=4.0)
    confidence: ConfidenceLabel = "provisional"
    evidence_spans: list[str] = Field(default_factory=list)
    sub_breakdown: dict[str, float] = Field(default_factory=dict)

    def update_with_evidence(self, score: float, span: str, sessions_count: int) -> None:
        self.score = round(max(0.0, min(4.0, score)), 2)
        if span and span not in self.evidence_spans:
            self.evidence_spans.append(span)
        self.confidence = compute_confidence_label(len(self.evidence_spans), sessions_count)


class MVPProfileDimensions(BaseModel):
    retrieval_independence: DimensionScore = Field(default_factory=DimensionScore)
    linguistic_quality: DimensionScore = Field(default_factory=DimensionScore)
    interaction: DimensionScore = Field(default_factory=DimensionScore)
    register_x_function: DimensionScore = Field(default_factory=DimensionScore)


class FullProfileDimensions(BaseModel):
    lexicon: DimensionScore = Field(default_factory=DimensionScore)
    grammar: DimensionScore = Field(default_factory=DimensionScore)
    fluency_retrieval: DimensionScore = Field(default_factory=DimensionScore)
    pronunciation: DimensionScore = Field(default_factory=DimensionScore)
    listening: DimensionScore = Field(default_factory=DimensionScore)
    pragmatics: DimensionScore = Field(default_factory=DimensionScore)
    interaction: DimensionScore = Field(default_factory=DimensionScore)
    register_control: DimensionScore = Field(default_factory=DimensionScore)


class LearnerProfile(BaseModel):
    user_id: int
    goal: GoalType = "general"
    interests: list[str] = Field(default_factory=list)
    topics_to_avoid: list[str] = Field(default_factory=lambda: ["politics", "religion"])
    dialect: DialectHypothesis = Field(default_factory=DialectHypothesis)
    other_languages: list[str] = Field(default_factory=list)
    correction_style: CorrectionStyle = "selective"
    arabic_support: ArabicSupport = "none"
    register_weights: dict[str, int] = Field(default_factory=dict)
    onboarding_session: int = Field(default=1, ge=1, le=4)
    onboarding_turns_in_session: int = 0
    onboarding_completed: bool = False
    completed_sessions_count: int = 0
    last_calibrated_at: Optional[str] = None
    retain_raw_audio: bool = False
    voice_replies: bool = True  # Alex answers with a voice note after each reply (toggle: /voice)
    mvp_dimensions: MVPProfileDimensions = Field(default_factory=MVPProfileDimensions)
    full_dimensions: FullProfileDimensions = Field(default_factory=FullProfileDimensions)

    @model_validator(mode="after")
    def ensure_valid_weights(self) -> "LearnerProfile":
        if not self.register_weights:
            self.register_weights = get_initial_register_weights(self.goal)
        else:
            self.register_weights = normalize_register_weights(self.register_weights)
        return self

    def mark_calibrated(self) -> None:
        self.last_calibrated_at = datetime.now(timezone.utc).isoformat()
