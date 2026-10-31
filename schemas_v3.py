"""V3 contracts: enums, ordinal scale, V2 event payload models, and upcast()."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Domain",
    "Classification",
    "RepairScope",
    "Timing",
    "Delivery",
    "PriorityClass",
    "ComprehensionStatus",
    "FeedbackStatus",
    "InteractionObject",
    "InteractionState",
    "TurnOrigin",
    "RetrievalOutcome",
    "RetrievalOrigin",
    "PromptKind",
    "MatchedBy",
    "EvidenceDimension",
    "PhraseStage",
    "ContextFamily",
    "LexicalStructureType",
    "SkipReason",
    "DropReason",
    "CallClass",
    "Ordinal",
    "ORDINAL_SCORE",
    "CallMetaDict",
    "LearnerMessageV2",
    "ResponderReplyV2",
    "DiagnosticV2",
    "POSignal",
    "DropRecord",
    "AnalysisV2",
    "FindingDecision",
    "BudgetSnapshot",
    "PolicyDecisionV2",
    "FeedbackCardDeliveredV2",
    "ReconstructionAttemptedV2",
    "RetrievalAttemptedV2",
    "AnalysisSkippedV2",
    "make_diagnostic_id",
    "upcast",
]


class Domain(StrEnum):
    LINGUISTIC = "linguistic"
    PRAGMATIC = "pragmatic"
    DISCOURSE = "discourse"
    PHONOLOGICAL = "phonological"


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
    P2 = 2
    P3 = 3
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


class InteractionObject(StrEnum):
    UPGRADE = "upgrade"
    RECONSTRUCTION = "reconstruction"
    REVIEW_ITEM = "review_item"
    DECODE = "decode"
    WORKOUT = "workout"


class InteractionState(StrEnum):
    UPGRADE_VIEW = "UPGRADE_VIEW"
    RECONSTRUCTION_ACTIVE = "RECONSTRUCTION_ACTIVE"
    RECONSTRUCTION_FEEDBACK = "RECONSTRUCTION_FEEDBACK"
    REVIEW_ACTIVE = "REVIEW_ACTIVE"
    DECODE_AWAIT = "DECODE_AWAIT"
    WORKOUT_ACTIVE = "WORKOUT_ACTIVE"


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


Ordinal = Literal[0, 1, 2, 3]
ORDINAL_SCORE: dict[int, float] = {0: 0.0, 1: 40.0, 2: 75.0, 3: 100.0}


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
    GENERAL = "general"


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
    NOOP = "noop"
    OVERLAP = "overlap"
    DOMAIN_MODALITY = "domain_modality"
    CLASSIFICATION_MISMATCH = "classification_mismatch"
    ABSTAIN = "abstain"
    ECHO = "echo"
    PATTERN_CODE = "pattern_code"
    NOTE_TONE = "note_tone"


class CallClass(StrEnum):
    RESPONDER = "responder"
    ANALYST = "analyst"
    JUDGE = "judge"
    GENERATOR = "generator"


class CallMetaDict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    attempts: int
    latency_ms: int
    thinking_budget: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class LearnerMessageV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    input_modality: Literal["text", "voice"] = "text"
    origin: TurnOrigin = TurnOrigin.SCENE
    session_revision: int
    interaction_id: int | None = None
    telegram_message_id: int | None = None


class ResponderReplyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["opening", "reply", "p0_followup"] = "reply"
    turn_event_id: str | None = None
    reply_text: str
    second_bubble_text: str | None = None
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK
    recast_used: bool = False
    elicitation_used: bool = False
    telegram_message_ids: list[int] = Field(default_factory=list)
    call_meta: CallMetaDict | None = None


class DiagnosticV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    severity: str = "medium"
    original_span: str
    alternative: str
    alternative_status: str = "recommended"
    explanation_en: str = ""
    explanation_ar: str | None = None
    listener_effect: str | None = None
    secondary_register_label: str | None = None
    secondary_register_form: str | None = None
    pattern_id: str = "general"
    confidence_score: float = 0.85
    confidence_band: str = "medium"
    diagnostic_id: str
    domain: Domain
    classification: Classification
    repair_scope: RepairScope
    timing_hint: Timing | None = None
    span_start: int = 0
    span_end: int = 0
    communicative_impact: Ordinal = 2
    learning_value: Ordinal = 2
    linguistic_confidence: float | None = None
    pragmatic_confidence: float | None = None
    acoustic_confidence: float | None = None


class POSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: bool = False
    line: str | None = None


class DropRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    reason: DropReason


class AnalysisV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_event_id: str
    rubric_version: str = "v2.0"
    communicative: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[DiagnosticV2] = Field(default_factory=list)
    target_assessments: list[dict[str, Any]] = Field(default_factory=list)
    p0: POSignal = Field(default_factory=POSignal)
    dropped: list[DropRecord] = Field(default_factory=list)
    call_meta: CallMetaDict | None = None


class FindingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnostic_id: str
    priority_class: PriorityClass
    priority_score: float
    delivery: Delivery
    reasons: list[str] = Field(default_factory=list)
    gates: dict[str, bool] = Field(default_factory=dict)


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference: str = "active"
    turns_since_visible_card: int = 0
    cards_in_scene: int = 0
    strain: float = 0.0
    allowed: bool = True


class PolicyDecisionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_event_id: str
    decisions: list[FindingDecision] = Field(default_factory=list)
    selected_diagnostic_id: str | None = None
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)
    curveball_turn: bool = False
    pending_clarification: str | None = None


class FeedbackCardDeliveredV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnostic_id: str
    card_id: str
    trigger: Literal["end_of_turn", "tap", "review", "debrief"]
    telegram_message_id: int | None = None


class ReconstructionAttemptedV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interaction_id: int
    diagnostic_id: str
    attempt_text: str
    linguistic_level: Ordinal
    functional_level: Ordinal
    outcome: Literal["success", "partial", "failure"]
    functional_equivalent_credited: bool = False
    matched_by: MatchedBy


class RetrievalAttemptedV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase_id: str
    origin: RetrievalOrigin
    prompt_kind: PromptKind
    context_family: ContextFamily
    outcome: RetrievalOutcome
    hint_level: Literal[0, 1, 2] = 0
    linguistic_level: Ordinal | None = None
    functional_level: Ordinal | None = None
    matched_form: str | None = None
    matched_by: MatchedBy
    echo: bool = False
    due: bool = False
    schedule_step_before: int = 0
    interaction_id: int | None = None
    call_meta: CallMetaDict | None = None


class AnalysisSkippedV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_event_id: str
    reason: SkipReason
    detail: str | None = None


def make_diagnostic_id(event_id: Any, idx: int) -> str:
    digest = hashlib.sha1(f"{event_id}:{idx}".encode("utf-8"), usedforsecurity=False).hexdigest()
    return "dg_" + digest[:8]


_PRAGMATIC_KINDS = frozenset({"register", "pragmatics"})
_DISCOURSE_KINDS = frozenset({"discourse"})
_END_OF_TURN_VALUES = frozenset({"card", "active", "light", "end_of_turn"})


def upcast(row: Mapping[str, Any]) -> dict[str, Any]:
    """Pure read-time v1 -> v2 event upcaster (Section 3.3)."""
    out: dict[str, Any] = dict(row)
    raw = out.get("payload_json")
    if isinstance(raw, str):
        payload = json.loads(raw) if raw.strip() else {}
    elif isinstance(raw, Mapping):
        payload = copy.deepcopy(dict(raw))
    elif isinstance(out.get("payload"), Mapping):
        payload = copy.deepcopy(dict(out["payload"]))
    else:
        payload = {}

    if str(out.get("schema_version") or "1.0") == "2.0":
        out["payload"] = payload
        if not isinstance(out.get("payload_json"), str):
            out["payload_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return out

    ev_type = str(out.get("event_type") or "")
    ev_id = out.get("event_id") or ""

    if ev_type == "analyst_diagnosis_recorded":
        raw_diags = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else []
        up_diags: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_diags):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "grammar").strip().lower()
            if kind == "insufficient_context":
                continue
            if kind in _PRAGMATIC_KINDS:
                domain = "pragmatic"
            elif kind in _DISCOURSE_KINDS:
                domain = "discourse"
            else:
                domain = "linguistic"
            if kind == "optional_upgrade":
                classification = "upgrade"
            elif kind == "acceptable_variation":
                classification = "alternative"
            else:
                classification = "error"
            conf = float(item.get("confidence_score", 0.85))
            d_copy = dict(item)
            d_copy.update(
                {
                    "diagnostic_id": str(item.get("diagnostic_id") or make_diagnostic_id(ev_id, idx)),
                    "domain": domain,
                    "classification": classification,
                    "repair_scope": "phrase_span",
                    "timing_hint": None,
                    "communicative_impact": 2,
                    "learning_value": 2,
                    "linguistic_confidence": conf if domain != "pragmatic" else None,
                    "pragmatic_confidence": conf if domain == "pragmatic" else None,
                    "acoustic_confidence": None,
                    "span_start": int(item.get("span_start", 0) or 0),
                    "span_end": int(item.get("span_end", 0) or 0),
                }
            )
            up_diags.append(d_copy)
        payload = dict(payload)
        payload["diagnostics"] = up_diags
        payload.setdefault("turn_event_id", str(out.get("causation_id") or payload.get("turn_event_id") or ""))
        payload.setdefault("rubric_version", str(out.get("rubric_version") or "v1.0"))
        payload.setdefault("p0", {"candidate": False, "line": None})
        payload.setdefault("dropped", [])

    elif ev_type == "policy_decision_recorded":
        payload = dict(payload)
        interv = str(payload.get("intervention") or payload.get("decision") or "").strip().lower()
        delivery = "end_of_turn" if interv in _END_OF_TURN_VALUES else "deferred"
        selected = delivery == "end_of_turn" or bool(payload.get("selected_diagnostic_id") or payload.get("card_id"))
        payload["delivery"] = delivery
        payload["priority_class"] = 1 if selected else 4
        payload["priority_score"] = float(payload.get("priority_score", 0.0) or 0.0)
        payload["curveball_turn"] = False
        payload["pending_clarification"] = None

    elif ev_type == "responder_reply_delivered":
        payload = dict(payload)
        payload["kind"] = str(payload.get("kind") or "reply")
        payload["comprehension_status"] = str(payload.get("comprehension_status") or "ok")
        payload.setdefault("telegram_message_ids", [])

    elif ev_type == "learner_message_received":
        payload = dict(payload)
        is_voice = bool(payload.get("is_voice") or payload.get("is_voice_message") or payload.get("voice"))
        payload["origin"] = str(payload.get("origin") or "scene")
        payload["input_modality"] = str(payload.get("input_modality") or ("voice" if is_voice else "text"))
        payload["session_revision"] = int(payload.get("session_revision", 0) or 0)

    out["payload"] = payload
    out["payload_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return out
