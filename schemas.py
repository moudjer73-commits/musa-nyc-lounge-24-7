from enum import StrEnum
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegisterProfile(StrEnum):
    CASUAL = "casual"
    WARM = "warm"
    WITTY = "witty"
    DIPLOMATIC = "diplomatic"
    EXECUTIVE = "executive"
    ANALYTICAL = "analytical"


class DirectnessLevel(StrEnum):
    SOFT = "soft"
    BALANCED = "balanced"
    DIRECT = "direct"


class TrainingMode(StrEnum):
    OPEN_SCENE = "open_scene"
    REGISTER_SHIFT = "register_shift"
    PRAGMATIC_DILEMMA = "pragmatic_dilemma"
    THINK_ALOUD = "think_aloud"
    LISTEN_AND_DECODE = "listen_and_decode"
    PHRASE_WORKOUT = "phrase_workout"
    ECHO_PROSODY = "echo_prosody"


class SceneMode(StrEnum):
    REAL_CONVERSATION = "real_conversation"
    EXPLICIT_SIMULATION = "explicit_simulation"
    MICRO_DRILL = "micro_drill"


class ConversationalAct(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    REACT = "react"
    CONTRIBUTE = "contribute"
    CLARIFY = "clarify"
    ASK = "ask"
    CHALLENGE = "challenge"
    CLOSE = "close"


class SurfaceStyle(StrEnum):
    PLAIN = "plain"
    FRAGMENT = "fragment"
    HUMOROUS = "humorous"
    HEDGED = "hedged"
    CONCISE = "concise"


class PersonaCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str = "alex"
    name: str = "Alex"
    role: str = "33-year-old American conversational partner & colleague"
    relationship: str = "friendly peer"
    style: str = "Grounded, observant, natural spoken rhythm, zero textbook lecturing, never fabricates parallel 'me-too' personal stories."


class SceneBible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    scene_mode: SceneMode = SceneMode.REAL_CONVERSATION
    setting: str = "Everyday conversation"
    relationship: str = "friendly peer"
    established_facts: list[str] = Field(
        default_factory=lambda: ["Respond directly to the learner's real topic; never invent fake crises or 'me-too' mirroring stories."]
    )
    counterpart_goal: str = "Engage naturally and authentically"
    counterpart_pushback_plan: Optional[str] = None


class DialogueDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    act: ConversationalAct
    style: SurfaceStyle
    allow_question: bool
    max_questions: int = Field(ge=0, le=1)
    max_sentences: int = Field(default=2, ge=1, le=4)
    min_words: int = Field(default=4, ge=1, le=30)
    max_words: int = Field(default=40, ge=10, le=90)
    target_bubbles: int = Field(default=1, ge=1, le=2)
    avoid_openers: list[str] = Field(default_factory=list)
    strain_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_code: str = "standard_flow"

    @model_validator(mode="after")
    def enforce_question_contract(self) -> "DialogueDecision":
        if self.act == ConversationalAct.CLARIFY and not self.allow_question:
            raise ValueError("CLARIFY act must allow a question.")
        if self.act in (ConversationalAct.ASK, ConversationalAct.CLARIFY) and not self.allow_question:
            raise ValueError("ASK or CLARIFY act requires allow_question=True.")
        if not self.allow_question and self.max_questions != 0:
            raise ValueError("When allow_question is False, max_questions must equal 0.")
        if self.allow_question and self.max_questions != 1:
            raise ValueError("When allow_question is True, max_questions must equal 1.")
        return self


class IssueKind(StrEnum):
    GRAMMAR = "grammar"
    COLLOCATION = "collocation"
    IDIOMATICITY = "idiomaticity"
    REGISTER = "register"
    PRAGMATICS = "pragmatics"
    DISCOURSE = "discourse"
    OPTIONAL_UPGRADE = "optional_upgrade"
    ACCEPTABLE_VARIATION = "acceptable_variation"
    INSUFFICIENT_CONTEXT = "insufficient_context"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AlternativeStatus(StrEnum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class ProductionEvidence(StrEnum):
    NONE = "none"
    RECOGNITION = "recognition"
    CUED_PRODUCTION = "cued_production"
    INDEPENDENT_PRODUCTION = "independent_production"
    SPONTANEOUS_PRODUCTION = "spontaneous_production"


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CandidateAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(..., min_length=1)
    register_profile: RegisterProfile = RegisterProfile.CASUAL
    directness: DirectnessLevel = DirectnessLevel.BALANCED
    pragmatic_effect: str = Field(..., min_length=1, max_length=160)


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnostic_id: str = Field(..., min_length=1)
    original_span: str = Field(..., min_length=1)
    normalized_pattern_id: str = Field(..., min_length=3)
    relevant_context_family: str = Field(default="global", min_length=1)
    intended_meaning: Optional[str] = None
    kind: IssueKind
    severity: Severity
    alternative: Optional[str] = None
    secondary_alternatives: list[CandidateAlternative] = Field(default_factory=list, max_length=2)
    alternative_status: AlternativeStatus
    recast_candidate: Optional[str] = None
    listener_effect: Optional[str] = Field(default=None, max_length=200)
    explanation_en: str = Field(..., min_length=5, max_length=240)
    explanation_ar: Optional[str] = Field(default=None, max_length=200)
    l1_pattern: Optional[str] = Field(default=None, max_length=64)
    l1_source_ar: Optional[str] = Field(default=None, max_length=80)
    l1_transfer_likely: bool = False
    l1_transfer_evidence: Optional[str] = None
    teachable_now: bool = True
    preserves_intended_meaning: bool
    context_fit_verified: bool
    confidence_band: ConfidenceBand
    confidence_score: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def enforce_fail_closed_invariants(self) -> "Diagnostic":
        if self.confidence_band == ConfidenceBand.LOW and self.confidence_score >= 0.65:
            raise ValueError("ConfidenceBand.LOW requires confidence_score < 0.65.")
        if self.confidence_band == ConfidenceBand.MEDIUM and not (0.65 <= self.confidence_score < 0.80):
            raise ValueError("ConfidenceBand.MEDIUM requires 0.65 <= confidence_score < 0.80.")
        if self.confidence_band == ConfidenceBand.HIGH and self.confidence_score < 0.80:
            raise ValueError("ConfidenceBand.HIGH requires confidence_score >= 0.80.")

        if self.kind in (
            IssueKind.OPTIONAL_UPGRADE,
            IssueKind.ACCEPTABLE_VARIATION,
            IssueKind.INSUFFICIENT_CONTEXT,
        ):
            if self.alternative_status == AlternativeStatus.REQUIRED:
                raise ValueError(f"{self.kind.value} cannot have alternative_status=REQUIRED.")

        if self.alternative_status == AlternativeStatus.REQUIRED:
            if not (self.alternative and self.alternative.strip()):
                raise ValueError("REQUIRED correction must include a non-empty alternative.")
            if not (self.intended_meaning and self.intended_meaning.strip()):
                raise ValueError("REQUIRED correction must establish a non-empty intended_meaning.")
            if not self.preserves_intended_meaning:
                raise ValueError("REQUIRED correction must preserve intended meaning.")

        if self.l1_transfer_likely and not (self.l1_transfer_evidence and self.l1_transfer_evidence.strip()):
            raise ValueError("l1_transfer_likely=True requires non-empty l1_transfer_evidence.")

        return self


class TargetUseAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(..., min_length=1)
    matched_span: str = Field(..., min_length=1)
    was_revealed_in_task: bool = False
    was_directly_cued: bool = False
    fits_communicative_context: bool = False
    fits_register: bool = False
    phrase_specific_rubric_passed: bool = False
    evidence: ProductionEvidence = ProductionEvidence.NONE
    confidence_band: ConfidenceBand = ConfidenceBand.LOW

    @model_validator(mode="after")
    def enforce_evidence_honesty(self) -> "TargetUseAssessment":
        if self.was_revealed_in_task:
            if self.evidence in (
                ProductionEvidence.INDEPENDENT_PRODUCTION,
                ProductionEvidence.SPONTANEOUS_PRODUCTION,
            ):
                self.evidence = ProductionEvidence.NONE
        elif self.was_directly_cued:
            if self.evidence in (
                ProductionEvidence.INDEPENDENT_PRODUCTION,
                ProductionEvidence.SPONTANEOUS_PRODUCTION,
            ):
                self.evidence = ProductionEvidence.CUED_PRODUCTION

        if not (
            self.fits_communicative_context
            and self.fits_register
            and self.phrase_specific_rubric_passed
        ):
            self.evidence = ProductionEvidence.NONE

        if self.confidence_band == ConfidenceBand.LOW:
            self.evidence = ProductionEvidence.NONE

        return self


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    communicative_intention: str = "share_comment"
    learner_invites_question: bool = False
    communicative_goal_achieved: bool = True
    pragmatic_cave_in: bool = False
    requires_clarification: bool = False
    in_emotional_or_story_flow: bool = False
    diagnostics: list[Diagnostic] = Field(default_factory=list, max_length=4)
    target_assessments: list[TargetUseAssessment] = Field(default_factory=list, max_length=3)


class ResponderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(..., min_length=1)
    second_bubble_text: Optional[str] = None
    recast_used: bool = False
    elicitation_used: bool = False


class InterventionLevel(StrEnum):
    NONE = "none"
    IMPLICIT_RECAST = "implicit_recast"
    BRIEF_CARD = "brief_card"
    EXPLICIT_COACHING = "explicit_coaching"
    DEFER_TO_REVIEW = "defer_to_review"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention: InterventionLevel = InterventionLevel.NONE
    clarification_needed: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    selected_diagnostic_id: Optional[str] = None
    workout_candidate_pattern_id: Optional[str] = None
    workout_candidate_context_family: Optional[str] = None
    advisory_recast_candidate: Optional[str] = None
    advisory_stealth_target_id: Optional[str] = None
    stealth_elicitation_mode: Literal["none", "organic", "gentle_opening", "explicit_challenge"] = "none"
    eligible_recurrence_keys: list[str] = Field(default_factory=list)
    schedule_micro_workout: bool = False
    defer_workout_until_scene_end: bool = False


class RegisterShiftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    communicative_intention: str
    casual_friend_version: str
    diplomatic_colleague_version: str
    executive_manager_version: str
    key_pragmatic_distinction_en: str
    key_pragmatic_distinction_ar: str
    practice_prompt: str


class DynamicDilemmaScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting: str = Field(..., min_length=5, max_length=140)
    relationship: str = Field(..., min_length=3, max_length=80)
    register_profile: RegisterProfile = RegisterProfile.DIPLOMATIC
    established_facts: list[str] = Field(..., min_length=2, max_length=4)
    counterpart_goal: str = Field(..., min_length=5, max_length=160)
    counterpart_pushback_plan: str = Field(default="Push back gently once before accepting a compromise.", max_length=180)
    learner_pragmatic_goal: str = Field(..., min_length=5, max_length=160)
    opening_line: str = Field(..., min_length=5, max_length=240)


class DynamicWorkoutScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    communicative_intention: str = Field(..., min_length=3, max_length=100)
    situation_setup: str = Field(..., min_length=10, max_length=260)
    prompt_question: str = Field(..., min_length=5, max_length=120)


class ListenDecodeChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_line: str = Field(..., min_length=5, max_length=160)
    spoken_clip_text: str = Field(..., min_length=5, max_length=200)
    full_unreduced_form: str = Field(..., min_length=5, max_length=220)
    acoustic_reduction_note: str = Field(..., min_length=5, max_length=220)
    pragmatic_subtext_en: str = Field(..., min_length=5, max_length=220)
    pragmatic_subtext_ar: str = Field(..., min_length=5, max_length=200)
    decode_question: str = Field(..., min_length=5, max_length=160)


class DebriefShiftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    you_said: str
    natural_alternative: str
    why_it_matters: str
    arabic_note: Optional[str] = None


class SceneDebriefResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_outcome: str
    spontaneous_wins: list[str] = Field(default_factory=list, max_length=3)
    top_shifts: list[DebriefShiftItem] = Field(default_factory=list, max_length=3)
    closing_note: str
