import copy
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from enum import StrEnum
from typing import Any, Literal, Mapping, Optional
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


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
    REVIEW_RETRIEVAL = "review_retrieval"


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


class DecodePhase(str, Enum):
    CHALLENGE_SENT = "challenge_sent"
    AWAITING_LEARNER = "awaiting_learner"
    FEEDBACK_READY = "feedback_ready"
    COMPLETED = "completed"


class DecodeEvaluationResult(BaseModel):
    comprehension_accurate: bool = True
    coach_feedback_en: str = Field(default="Spot on—you caught the real subtext.", min_length=5, max_length=320)
    coach_feedback_ar: str = Field(default="أصبت في التقاط المعنى المبطن للمتحدث.", min_length=5, max_length=240)



# ======================================================================================
# V3.0 contracts (S0 sections 2, 3 and 3.3). Owner: Claude. Read by Gemini; changes to
# anything below go through a contract bump.
# ======================================================================================

CONTRACT_VERSION = "3.0.0-s2"

SCHEMA_VERSION_V1 = "1.0"
SCHEMA_VERSION_V2 = "2.0"
RUBRIC_VERSION_V1 = "v1.0"
RUBRIC_VERSION_V2 = "v2.0"


# ---------------------------------------------------------------- section 2: enums
# Storage values are lowercase snake_case, except PhraseStage (unchanged: it sits behind a CHECK).


class Domain(StrEnum):
    LINGUISTIC = "linguistic"
    PRAGMATIC = "pragmatic"
    DISCOURSE = "discourse"
    PHONOLOGICAL = "phonological"  # reserved for V3.1; validation rejects it


class Classification(StrEnum):
    ERROR = "error"
    UPGRADE = "upgrade"
    ALTERNATIVE = "alternative"


class RepairScope(StrEnum):
    LOCAL = "local"
    PHRASE_SPAN = "phrase_span"
    UTTERANCE_REWRITE = "utterance_rewrite"


class Timing(StrEnum):
    IMMEDIATE = "immediate"
    END_OF_TURN = "end_of_turn"
    DEFERRED = "deferred"


class Delivery(StrEnum):
    IMMEDIATE_FOLLOWUP = "immediate_followup"
    END_OF_TURN = "end_of_turn"
    DEFERRED = "deferred"
    SUPPRESSED = "suppressed"


class PriorityClass(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2  # curveball: reserved for V3.1
    P3 = 3  # prosody: reserved for V3.1
    P4 = 4


class ComprehensionStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    REVERSED = "reversed"


class FeedbackStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    RETRACTED = "retracted"
    MERGED = "merged"


class FeedbackPreference(StrEnum):
    SILENT = "silent"
    LIGHT = "light"
    ACTIVE = "active"


class InteractionObject(StrEnum):
    UPGRADE = "upgrade"
    RECONSTRUCTION = "reconstruction"
    REVIEW_ITEM = "review_item"
    DECODE = "decode"
    WORKOUT = "workout"


class InteractionState(StrEnum):
    """LIVE_SCENE is the absence of an ``active_interaction`` row, so it is not a member.
    RETURN_TO_SCENE is a transition, not a state."""

    UPGRADE_VIEW = "upgrade_view"
    RECONSTRUCTION_ACTIVE = "reconstruction_active"
    RECONSTRUCTION_FEEDBACK = "reconstruction_feedback"
    REVIEW_ACTIVE = "review_active"
    DECODE_AWAIT = "decode_await"
    WORKOUT_ACTIVE = "workout_active"


class TurnOrigin(StrEnum):
    SCENE = "scene"
    RECONSTRUCTION = "reconstruction"
    REVIEW = "review"
    DRILL = "drill"


class RetrievalOutcome(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    HINTED_SUCCESS = "hinted_success"


class RetrievalOrigin(StrEnum):
    REVIEW = "review"
    SPONTANEOUS = "spontaneous"
    RECONSTRUCTION = "reconstruction"
    DRILL = "drill"


class PromptKind(StrEnum):
    MEANING = "meaning"
    SITUATION = "situation"
    FUNCTION = "function"
    SPONTANEOUS = "spontaneous"


class MatchedBy(StrEnum):
    PYTHON_EXACT = "python_exact"
    PYTHON_FUZZY = "python_fuzzy"
    LLM_JUDGE = "llm_judge"


class EvidenceDimension(StrEnum):
    RECOGNITION = "recognition"
    PRODUCTION = "production"
    INDEPENDENCE = "independence"
    TRANSFER = "transfer"
    PRAGMATIC = "pragmatic"


class Ordinal(IntEnum):
    """The LLM returns 0 to 3; Python maps to a score (mastery.py: 0.0, 40.0, 75.0, 100.0)."""

    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3


class PhraseStage(StrEnum):
    UNSEEN = "Unseen"
    SEEN = "Seen"
    RECOGNIZED = "Recognized"
    PRODUCED_WITH_PROMPT = "Produced-with-prompt"
    PRODUCED_INDEPENDENTLY = "Produced-independently"
    PRODUCED_SPONTANEOUSLY = "Produced-spontaneously"
    STABLE = "Stable"


class ContextFamily(StrEnum):
    WORKPLACE = "workplace"
    FRIENDSHIPS = "friendships"
    NEGOTIATION = "negotiation"
    ACADEMIC = "academic"
    CUSTOMER_SERVICE = "customer_service"
    FAMILY = "family"
    SOCIAL_GROUP = "social_group"
    CONFLICT = "conflict"
    PRESENTATION = "presentation"
    GENERAL = "general"  # never counts toward transfer


class LexicalStructureType(StrEnum):
    FIXED_CHUNK = "fixed_chunk"
    SEMI_FIXED_FRAME = "semi_fixed_frame"
    DISCOURSE_TEMPLATE = "discourse_template"


class SkipReason(StrEnum):
    TRIVIAL = "trivial"
    SHED = "shed"
    DEADLINE = "deadline"
    ERROR = "error"


class DropReason(StrEnum):
    SCHEMA = "schema"
    SPAN_MISSING = "span_missing"
    SPAN_BOUNDARY = "span_boundary"
    OVERLAP = "overlap"
    DOMAIN_MODALITY = "domain_modality"
    CLASSIFICATION_MISMATCH = "classification_mismatch"
    ABSTAIN = "abstain"
    ECHO = "echo"
    PATTERN_CODE = "pattern_code"
    NOTE_TONE = "note_tone"
    NOOP = "noop"  # S0 5.1 step 5; missing from the S1 enum (11 reasons in S0, 10 in S1), added in S2


# CallClass is defined by Gemini's transport (llm_transport.py) and is not duplicated here.
# TrainingMode (above) gains review_retrieval; the keyboard map in S0 section 2 is unchanged otherwise.


# ---------------------------------------------------------------- event type registry (3.1)

LIVE_EVENT_TYPES: tuple[str, ...] = (
    "session_started",
    "session_ended",
    "learner_message_received",
    "analyst_diagnosis_recorded",
    "policy_decision_recorded",
    "responder_reply_delivered",
    "feedback_card_delivered",
    "micro_workout_scheduled",
    "micro_workout_completed",
    "phrase_exposure_recorded",
    "phrase_production_assessed",
    "diagnostic_retracted",
    "phrase_evidence_retracted",
    "review_completed",
)
V3_EVENT_TYPES: tuple[str, ...] = (
    "reconstruction_attempted",
    "retrieval_attempted",
    "analysis_skipped",
)
EVENT_TYPE_REGISTRY: dict[str, int] = {
    **{name: 0 for name in LIVE_EVENT_TYPES},
    **{name: 1 for name in V3_EVENT_TYPES},
}


# ---------------------------------------------------------------- section 3.2: payload shapes


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallMetaDict(_StrictModel):
    """Copy of the transport's ``CallMeta`` stored for audit (``project_label`` is not stored)."""

    model: str
    attempts: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    thinking_budget: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    @classmethod
    def from_meta(cls, meta: Any) -> "CallMetaDict":
        """Build from ``llm_transport.CallMeta`` (or any object with the same attributes)."""
        return cls(
            model=str(meta.model),
            attempts=int(meta.attempts),
            latency_ms=int(meta.latency_ms),
            thinking_budget=getattr(meta, "thinking_budget", None),
            input_tokens=getattr(meta, "input_tokens", None),
            output_tokens=getattr(meta, "output_tokens", None),
        )


class LearnerMessageV2(_StrictModel):
    text: str
    input_modality: Literal["text", "voice"] = "text"
    origin: TurnOrigin = TurnOrigin.SCENE
    session_revision: int = Field(ge=0)  # value AFTER this message was accepted (scene turns only bump it)
    interaction_id: Optional[int] = None
    telegram_message_id: Optional[int] = None


class ResponderReplyV2(_StrictModel):
    kind: Literal["opening", "reply", "p0_followup"]
    turn_event_id: Optional[str] = None
    reply_text: str
    second_bubble_text: Optional[str] = None
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK
    recast_used: bool = False
    elicitation_used: bool = False
    telegram_message_ids: list[int] = Field(default_factory=list)
    call_meta: Optional[CallMetaDict] = None


CONFIDENCE_BAND_MEDIUM_FLOOR = 0.65
CONFIDENCE_BAND_HIGH_FLOOR = 0.80


def confidence_band_for(score: float) -> ConfidenceBand:
    """Band derived by code from the applicable confidence (same cut points as ``Diagnostic``)."""
    if score >= CONFIDENCE_BAND_HIGH_FLOOR:
        return ConfidenceBand.HIGH
    if score >= CONFIDENCE_BAND_MEDIUM_FLOOR:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


# v1 kind -> V3 classification (S0 section 2). insufficient_context is never a finding.
def classification_from_kind(kind: str) -> Optional[Classification]:
    if kind == IssueKind.INSUFFICIENT_CONTEXT.value:
        return None
    if kind == IssueKind.OPTIONAL_UPGRADE.value:
        return Classification.UPGRADE
    if kind == IssueKind.ACCEPTABLE_VARIATION.value:
        return Classification.ALTERNATIVE
    return Classification.ERROR


# v1 kind -> V3 domain (S0 section 3.3).
DOMAIN_BY_KIND: dict[str, Domain] = {
    IssueKind.GRAMMAR.value: Domain.LINGUISTIC,
    IssueKind.COLLOCATION.value: Domain.LINGUISTIC,
    IssueKind.IDIOMATICITY.value: Domain.LINGUISTIC,
    IssueKind.REGISTER.value: Domain.PRAGMATIC,
    IssueKind.PRAGMATICS.value: Domain.PRAGMATIC,
    IssueKind.DISCOURSE.value: Domain.DISCOURSE,
}

DIAGNOSTIC_ID_PATTERN = r"^dg_[0-9a-f]{8}$"


class DiagnosticV2(Diagnostic):
    """Live ``Diagnostic`` fields kept; V3.0 fields added. ``alternative`` stays the replacement
    text (the full rewrite when ``repair_scope`` is utterance_rewrite). ``confidence_band`` is
    derived by code from the minimum of the non-null confidences."""

    diagnostic_id: str = Field(..., pattern=DIAGNOSTIC_ID_PATTERN)  # 'dg_' + 8 hex, assigned by code
    domain: Domain
    classification: Classification
    repair_scope: RepairScope
    timing_hint: Optional[Timing] = None
    span_start: int = Field(ge=0)  # char offsets into the learner text, filled by validation
    span_end: int = Field(ge=0)
    communicative_impact: Ordinal
    learning_value: Ordinal
    linguistic_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    pragmatic_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    acoustic_confidence: Optional[float] = None  # must be None in V3.0

    @model_validator(mode="after")
    def enforce_v2_invariants(self) -> "DiagnosticV2":
        if self.acoustic_confidence is not None:
            raise ValueError("acoustic_confidence must be None in V3.0")
        if self.span_end < self.span_start:
            raise ValueError("span_end must be >= span_start")
        expected = classification_from_kind(self.kind.value)
        if expected is None:
            raise ValueError("insufficient_context is never a finding")
        if self.classification != expected:
            raise ValueError(
                f"classification {self.classification.value} does not match kind {self.kind.value} "
                f"(expected {expected.value})"
            )
        if self.classification == Classification.ERROR:
            if self.alternative_status == AlternativeStatus.OPTIONAL:
                raise ValueError("an error requires alternative_status required or recommended")
        elif self.alternative_status != AlternativeStatus.OPTIONAL:
            raise ValueError("an upgrade or alternative requires alternative_status optional")
        if self.linguistic_confidence is None and self.pragmatic_confidence is None:
            raise ValueError("at least one of linguistic_confidence or pragmatic_confidence is required")
        return self


class CommunicativeAssessment(_StrictModel):
    """The live ``Analysis`` flags, unchanged."""

    communicative_intention: str = "share_comment"
    learner_invites_question: bool = False
    communicative_goal_achieved: bool = True
    pragmatic_cave_in: bool = False
    requires_clarification: bool = False
    in_emotional_or_story_flow: bool = False


TargetAssessment = TargetUseAssessment  # live shape, echo-guarded


class P0Signal(_StrictModel):
    candidate: bool = False
    line: Optional[str] = None  # max 20 words

    @model_validator(mode="after")
    def enforce_line_length(self) -> "P0Signal":
        if self.line is not None and len(self.line.split()) > 20:
            raise ValueError("p0.line is limited to 20 words")
        return self


class DropRecord(_StrictModel):
    ref: str
    reason: DropReason


class AnalysisV2(_StrictModel):
    turn_event_id: str
    rubric_version: str = RUBRIC_VERSION_V2
    communicative: CommunicativeAssessment = Field(default_factory=CommunicativeAssessment)
    diagnostics: list[DiagnosticV2] = Field(default_factory=list)  # validated survivors only
    target_assessments: list[TargetAssessment] = Field(default_factory=list)
    p0: P0Signal = Field(default_factory=P0Signal)
    dropped: list[DropRecord] = Field(default_factory=list)
    call_meta: Optional[CallMetaDict] = None


class FindingDecision(_StrictModel):
    diagnostic_id: str
    priority_class: PriorityClass
    priority_score: float = Field(ge=0.0, le=100.0)
    delivery: Delivery
    reasons: list[str] = Field(default_factory=list)
    gates: dict[str, bool] = Field(default_factory=dict)


class BudgetSnapshot(_StrictModel):
    preference: FeedbackPreference = FeedbackPreference.LIGHT
    turns_since_visible_card: int = Field(default=4, ge=0)
    cards_in_scene: int = Field(default=0, ge=0)
    strain: float = Field(default=0.0, ge=0.0, le=1.0)
    allowed: bool = False


class PolicyDecisionV2(_StrictModel):
    turn_event_id: str
    decisions: list[FindingDecision] = Field(default_factory=list)
    selected_diagnostic_id: Optional[str] = None
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)
    curveball_turn: bool = False  # always False in V3.0
    pending_clarification: Optional[str] = None

    @model_validator(mode="after")
    def enforce_no_curveball(self) -> "PolicyDecisionV2":
        if self.curveball_turn:
            raise ValueError("curveball_turn is always False in V3.0")
        return self


class FeedbackCardDeliveredV2(_StrictModel):
    diagnostic_id: str
    card_id: str
    trigger: Literal["end_of_turn", "tap", "review", "debrief"]
    telegram_message_id: Optional[int] = None


class ReconstructionAttemptedV2(_StrictModel):
    interaction_id: int
    diagnostic_id: str
    attempt_text: str
    linguistic_level: Ordinal
    functional_level: Ordinal
    outcome: Literal["success", "partial", "failure"]
    functional_equivalent_credited: bool
    matched_by: MatchedBy


class RetrievalAttemptedV2(_StrictModel):
    phrase_id: str
    origin: RetrievalOrigin
    prompt_kind: PromptKind
    context_family: ContextFamily
    outcome: RetrievalOutcome
    hint_level: Literal[0, 1, 2]
    linguistic_level: Optional[Ordinal] = None
    functional_level: Optional[Ordinal] = None
    matched_form: Optional[str] = None
    matched_by: MatchedBy
    echo: bool
    due: bool  # attempt made at or after next_review_at
    schedule_step_before: int = Field(ge=0)
    interaction_id: Optional[int] = None
    call_meta: Optional[CallMetaDict] = None


class AnalysisSkippedV2(_StrictModel):
    turn_event_id: str
    reason: SkipReason
    detail: Optional[str] = None


# Event types whose V2 payload is a Pydantic model. The other types keep their v1 (dict) shape.
PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "learner_message_received": LearnerMessageV2,
    "analyst_diagnosis_recorded": AnalysisV2,
    "policy_decision_recorded": PolicyDecisionV2,
    "responder_reply_delivered": ResponderReplyV2,
    "feedback_card_delivered": FeedbackCardDeliveredV2,
    "reconstruction_attempted": ReconstructionAttemptedV2,
    "retrieval_attempted": RetrievalAttemptedV2,
    "analysis_skipped": AnalysisSkippedV2,
}

# Introduced in V3.0: there is no v1 row of these types, so every row is schema 2.0.
V2_ONLY_EVENT_TYPES: frozenset[str] = frozenset(V3_EVENT_TYPES)


# ---------------------------------------------------------------- section 3.3: upcast (v1 to v2)


class UpcastError(ValueError):
    """A row cannot be read: unknown event type, unsupported schema_version, or (strict mode)
    a payload that does not satisfy its model."""


@dataclass(frozen=True)
class EventV2:
    """One event as readers see it, whatever schema it was written with.

    ``payload`` is the V2 model for the types in ``PAYLOAD_MODELS`` (or a plain dict for the
    types whose shape is unchanged, or when ``validate=False``). ``legacy`` keeps the original
    v1 payload of a v1 row for the few fields V2 dropped (``selected_pattern_id``,
    ``asked_optional_question``, ...). ``legacy_diagnostic_ids`` maps each assigned ``dg_`` id
    back to the v1 ``diagnostic_id`` so v1 retractions and cards still resolve.
    """

    event_seq: int
    event_id: str
    learner_id: int
    session_id: str
    event_type: str
    occurred_at_utc: str
    recorded_at_utc: str
    correlation_id: str
    causation_id: Optional[str]
    idempotency_key: str
    source_schema_version: str
    model_version: Optional[str]
    rubric_version: Optional[str]
    payload: Any
    legacy: Mapping[str, Any] = field(default_factory=dict)
    legacy_diagnostic_ids: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def is_legacy(self) -> bool:
        return self.source_schema_version.split(".", 1)[0] == "1"

    def payload_dict(self) -> dict[str, Any]:
        return dump_payload(self.payload)


def dump_payload(payload: Any) -> dict[str, Any]:
    """JSON-compatible dict of a payload given as a model or a dict (never mutates the input)."""
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, Mapping):
        return copy.deepcopy(dict(payload))
    raise TypeError(f"payload must be a BaseModel or a mapping, got {type(payload).__name__}")


def legacy_diagnostic_id(event_id: str, ordinal: int) -> str:
    """``'dg_' + first 8 hex of sha1(event_id + ordinal)``, ordinal 0-based in the v1 list."""
    digest = hashlib.sha1(f"{event_id}{ordinal}".encode("utf-8")).hexdigest()
    return "dg_" + digest[:8]


def _find_span(text: Optional[str], needle: str) -> tuple[int, int]:
    """First match of ``needle`` in ``text`` as (start, end), else (0, 0)."""
    if not text or not needle:
        return (0, 0)
    idx = text.find(needle)
    if idx >= 0:
        return (idx, idx + len(needle))
    lowered_text, lowered_needle = text.lower(), needle.lower()
    if len(lowered_text) == len(text):
        idx = lowered_text.find(lowered_needle)
        if idx >= 0:
            return (idx, idx + len(needle))
    return (0, 0)


def _upcast_analysis_v1(
    event_id: str,
    causation_id: Optional[str],
    rubric_version: Optional[str],
    payload: Mapping[str, Any],
    learner_text: Optional[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    diagnostics: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    aliases: dict[str, str] = {}
    for ordinal, raw in enumerate(payload.get("diagnostics") or []):
        if not isinstance(raw, Mapping):
            continue
        dg_id = legacy_diagnostic_id(event_id, ordinal)
        aliases[dg_id] = str(raw.get("diagnostic_id", ""))
        kind = str(raw.get("kind", ""))
        classification = classification_from_kind(kind)
        if classification is None:
            dropped.append({"ref": dg_id, "reason": DropReason.CLASSIFICATION_MISMATCH.value})
            continue
        domain = DOMAIN_BY_KIND.get(kind, Domain.LINGUISTIC)
        score = raw.get("confidence_score")
        span_start, span_end = _find_span(learner_text, str(raw.get("original_span", "")))
        item = copy.deepcopy(dict(raw))
        item.update(
            {
                "diagnostic_id": dg_id,
                "domain": domain.value,
                "classification": classification.value,
                "repair_scope": RepairScope.PHRASE_SPAN.value,
                "timing_hint": None,
                "span_start": span_start,
                "span_end": span_end,
                "communicative_impact": 2,
                "learning_value": 2,
                "linguistic_confidence": score if domain != Domain.PRAGMATIC else None,
                "pragmatic_confidence": score if domain == Domain.PRAGMATIC else None,
                "acoustic_confidence": None,
            }
        )
        diagnostics.append(item)
    communicative = {
        "communicative_intention": payload.get("communicative_intention", "share_comment"),
        "learner_invites_question": bool(payload.get("learner_invites_question", False)),
        "communicative_goal_achieved": bool(payload.get("communicative_goal_achieved", True)),
        "pragmatic_cave_in": bool(payload.get("pragmatic_cave_in", False)),
        "requires_clarification": bool(payload.get("requires_clarification", False)),
        "in_emotional_or_story_flow": bool(payload.get("in_emotional_or_story_flow", False)),
    }
    upcast_payload = {
        "turn_event_id": causation_id or "",
        "rubric_version": rubric_version or RUBRIC_VERSION_V1,
        "communicative": communicative,
        "diagnostics": diagnostics,
        "target_assessments": copy.deepcopy(list(payload.get("target_assessments") or [])),
        "p0": {"candidate": False, "line": None},
        "dropped": dropped,
        "call_meta": None,
    }
    return upcast_payload, aliases


def _upcast_policy_v1(causation_id: Optional[str], payload: Mapping[str, Any]) -> dict[str, Any]:
    intervention = str(payload.get("intervention", InterventionLevel.NONE.value))
    card_selected = intervention == InterventionLevel.BRIEF_CARD.value
    selected = payload.get("selected_diagnostic_id")
    decisions: list[dict[str, Any]] = []
    if selected:
        decisions.append(
            {
                "diagnostic_id": str(selected),
                "priority_class": PriorityClass.P1.value if card_selected else PriorityClass.P4.value,
                "priority_score": 0.0,
                "delivery": Delivery.END_OF_TURN.value if card_selected else Delivery.DEFERRED.value,
                "reasons": [str(r) for r in (payload.get("reason_codes") or [])],
                "gates": {},
            }
        )
    raw_budget = payload.get("budget") if isinstance(payload.get("budget"), Mapping) else payload
    budget = {
        "preference": raw_budget.get("preference", FeedbackPreference.LIGHT.value),
        "turns_since_visible_card": raw_budget.get("turns_since_visible_card", 4),
        "cards_in_scene": raw_budget.get("cards_in_scene", 0),
        "strain": raw_budget.get("strain", 0.0),
        "allowed": bool(raw_budget.get("allowed", card_selected)),
    }
    return {
        "turn_event_id": causation_id or "",
        "decisions": decisions,
        "selected_diagnostic_id": str(selected) if selected else None,
        "budget": budget,
        "curveball_turn": False,
        "pending_clarification": None,
    }


def _upcast_reply_v1(causation_id: Optional[str], payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "reply",
        "turn_event_id": causation_id,
        "reply_text": str(payload.get("reply_text", "")),
        "second_bubble_text": payload.get("second_bubble_text"),
        "comprehension_status": ComprehensionStatus.OK.value,
        "recast_used": bool(payload.get("recast_used", False)),
        "elicitation_used": False,
        "telegram_message_ids": [],
        "call_meta": None,
    }


def _upcast_message_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": str(payload.get("text", "")),
        "input_modality": "voice" if payload.get("is_voice") else "text",
        "origin": TurnOrigin.SCENE.value,
        "session_revision": 0,
        "interaction_id": None,
        "telegram_message_id": None,
    }


def _upcast_card_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    # S0 is silent on this type. v1 cards were only ever delivered by the lightbulb tap, the v1
    # row has no card id, and its diagnostic id (when present) is the v1 id, not a dg_ id.
    return {
        "diagnostic_id": str(payload.get("diagnostic_id", "")),
        "card_id": str(payload.get("card_id", "")),
        "trigger": "tap",
        "telegram_message_id": None,
    }


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None and default is not None else value


def upcast(
    row: Any,
    *,
    validate: bool = True,
    strict: bool = False,
    learner_text: Optional[str] = None,
) -> EventV2:
    """Read one ``event_log`` row (``sqlite3.Row`` or mapping) as a V2 event. Pure: it never
    touches the log and never mutates ``row``; the same row always gives the same result.

    * ``validate=True`` builds the V2 payload model. A payload that does not satisfy it comes
      back as a plain dict with a warning (``strict=True`` raises ``UpcastError`` instead), so a
      projection rebuild is never bricked by one odd historical row.
    * ``validate=False`` is the fast path used by projection rebuilds: the same shaping, no models.
    * ``learner_text``, when given, is used to fill ``span_start``/``span_end`` of upcast
      diagnostics (first match); otherwise they are 0, 0.
    """
    event_type = str(row["event_type"])
    if event_type not in EVENT_TYPE_REGISTRY:
        raise UpcastError(f"unknown event_type {event_type!r}")
    schema_version = str(_row_get(row, "schema_version", SCHEMA_VERSION_V1))
    major = schema_version.split(".", 1)[0]
    if major not in ("1", "2"):
        raise UpcastError(f"unsupported schema_version {schema_version!r}")
    if major == "1" and event_type in V2_ONLY_EVENT_TYPES:
        raise UpcastError(f"{event_type} has no v1 shape (schema_version {schema_version!r})")

    raw_json = row["payload_json"]
    try:
        raw = json.loads(raw_json) if isinstance(raw_json, (str, bytes)) else copy.deepcopy(raw_json)
    except json.JSONDecodeError as exc:
        raise UpcastError(f"payload_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise UpcastError("payload_json must be a JSON object")

    event_id = str(row["event_id"])
    causation_id = _row_get(row, "causation_id")
    rubric_version = _row_get(row, "rubric_version")
    legacy: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    warnings: list[str] = []

    if major == "1" and event_type in PAYLOAD_MODELS:
        legacy = copy.deepcopy(raw)
        if event_type == "analyst_diagnosis_recorded":
            shaped, aliases = _upcast_analysis_v1(event_id, causation_id, rubric_version, raw, learner_text)
            legacy = {}
        elif event_type == "policy_decision_recorded":
            shaped = _upcast_policy_v1(causation_id, raw)
        elif event_type == "responder_reply_delivered":
            shaped = _upcast_reply_v1(causation_id, raw)
        elif event_type == "learner_message_received":
            shaped = _upcast_message_v1(raw)
        else:  # feedback_card_delivered
            shaped = _upcast_card_v1(raw)
    else:
        shaped = raw

    payload: Any = shaped
    model = PAYLOAD_MODELS.get(event_type)
    if validate and model is not None:
        try:
            payload = model.model_validate(shaped)
        except ValidationError as exc:
            if strict:
                raise UpcastError(f"{event_type} {event_id}: {exc.error_count()} validation error(s): {exc}") from exc
            warnings.append(f"{event_type} payload does not satisfy {model.__name__} ({exc.error_count()} errors)")

    return EventV2(
        event_seq=int(_row_get(row, "event_seq", 0)),
        event_id=event_id,
        learner_id=int(row["learner_id"]),
        session_id=str(row["session_id"]),
        event_type=event_type,
        occurred_at_utc=str(row["occurred_at_utc"]),
        recorded_at_utc=str(_row_get(row, "recorded_at_utc", row["occurred_at_utc"])),
        correlation_id=str(_row_get(row, "correlation_id", "")),
        causation_id=causation_id,
        idempotency_key=str(_row_get(row, "idempotency_key", "")),
        source_schema_version=schema_version,
        model_version=_row_get(row, "model_version"),
        rubric_version=rubric_version,
        payload=payload,
        legacy=legacy,
        legacy_diagnostic_ids=aliases,
        warnings=tuple(warnings),
    )
