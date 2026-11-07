from typing import Literal
from pydantic import BaseModel, Field

ErrorCategory = Literal["A", "B", "C", "D"]
ErrorPriorityType = Literal["meaning", "lexis", "register", "grammar", "pronunciation", "none"]
CorrectionAction = Literal[
    "none",
    "silent_log_only",
    "silent_recast",
    "explicit_correction_retry",
    "immediate_meaning_repair",
    "end_of_session_summary",
]
HintStep = Literal["describe_it", "first_letter", "sentence_starter", "arabic_fallback"]

PRIORITY_RANK: dict[ErrorPriorityType, int] = {
    "meaning": 1,
    "lexis": 2,
    "register": 3,
    "grammar": 4,
    "pronunciation": 5,
    "none": 99,
}

CONFIDENCE_THRESHOLD = 0.75


class CalqueEntry(BaseModel):
    pattern_id: str
    non_native_phrase: str
    natural_alternative: str
    error_type: ErrorPriorityType = "lexis"
    cause_status: Literal["arabic_calque_hypothesis", "uncertain"] = "arabic_calque_hypothesis"
    verified_by_native: bool = False


class AnalystLLMOutput(BaseModel):
    category: ErrorCategory = "C"
    error_type: ErrorPriorityType = "none"
    error_signature: str = "none"
    evidence_span: str = ""
    suggested_alternative: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    meaning_broken: bool = False
    is_recurring_natural_grammar: bool = False
    arabic_pragmatic_note: str = ""


class CorrectionDecision(BaseModel):
    action: CorrectionAction = "none"
    visible_to_learner: bool = False
    recast_phrase_for_alex: str | None = None
    learner_feedback_message: str | None = None
    retry_prompt: str | None = None
    arabic_post_turn_note: str | None = None
    raw_analysis: AnalystLLMOutput
    occurrence_count: int = 0
