"""Pydantic v2 models: every JSON contract between the code and the LLM prompts.

The field names here are the field names used inside prompts/*.md. A test
(tests/test_prompts.py) checks that every field of every model that an LLM must produce
appears in the matching prompt, so the prompt text and the schema cannot silently drift.

Leniency policy: types are strict, list *lengths* are lenient. If a model returns four
findings instead of three we keep the first three rather than failing the whole turn.
"""

from __future__ import annotations

import warnings
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .registers import formality_of

# `register` is a field name in the prompt contracts (JSON is the source of truth). Pydantic warns
# that it shadows ABCMeta.register, which this code base never uses, so the warning is noise.
warnings.filterwarnings("ignore", message='Field name "register".*shadows an attribute')

# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------

Register = Literal["casual", "warm", "witty", "diplomatic", "executive", "analytical"]
REGISTERS: tuple[str, ...] = (
    "casual", "warm", "witty", "diplomatic", "executive", "analytical",
)
AlternativeRegister = Literal["casual", "warm", "witty", "diplomatic", "executive", "analytical", "neutral"]

Cefr = Literal["B1", "B2", "C1", "C2"]
Category = Literal["comprehension", "pragmatic", "register", "collocation", "grammar", "fluency"]
DiscourseState = Literal["storytelling", "emotional", "frustrated", "out_of_frame", "normal"]
Move = Literal[
    "REACT", "SHARE", "BUILD", "TEASE", "ASK",
    "PUSHBACK", "CLARIFY", "BACKCHANNEL", "PIVOT", "WRAP",
]
MOVES: tuple[str, ...] = (
    "REACT", "SHARE", "BUILD", "TEASE", "ASK",
    "PUSHBACK", "CLARIFY", "BACKCHANNEL", "PIVOT", "WRAP",
)
Strictness = Literal["light", "normal", "coach"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _truncate(limit: int):
    def _inner(value: Any) -> Any:
        if isinstance(value, list) and len(value) > limit:
            return value[:limit]
        return value

    return _inner


# --------------------------------------------------------------------------------------
# Analyst output (prompts/analyst_system.md + _partials/finding_schema.md)
# --------------------------------------------------------------------------------------


class Alternative(_Model):
    text: str
    register: AlternativeRegister = "neutral"
    nuance: str = ""
    best_for: str = ""


class Finding(_Model):
    span: str
    category: Category
    l1_transfer: bool = False
    l1_pattern: Optional[str] = None
    l1_source_ar: Optional[str] = None
    severity: Annotated[int, Field(ge=1, le=3)]
    teachable_now: bool = True
    pattern_key: str
    listener_effect: str = ""
    why_en: str = ""
    ar_note: str = ""
    recast_form: Optional[str] = None
    recast_meaning: Optional[str] = None
    alternatives: list[Alternative] = Field(default_factory=list)

    _trim_alts = field_validator("alternatives", mode="before")(_truncate(3))

    @field_validator("pattern_key")
    @classmethod
    def _normalise_key(cls, v: str) -> str:
        return v.strip().replace(" ", "_")

    @model_validator(mode="after")
    def _consistency(self) -> "Finding":
        # l1_transfer only means something when a concrete Arabic source is named.
        if self.l1_transfer and not (self.l1_source_ar and self.l1_source_ar.strip()):
            self.l1_transfer = False
            self.l1_pattern = None
        if not self.l1_transfer:
            self.l1_pattern = None
        return self


class UsedTarget(_Model):
    target_id: str
    form_used: str
    correct: bool
    naturalness: Annotated[int, Field(ge=1, le=3)] = 2
    echo: bool = False


class Analysis(_Model):
    comprehension_ok: bool = True
    clarification_needed: bool = False
    clarification_focus: Optional[str] = None
    discourse_state: DiscourseState = "normal"
    findings: list[Finding] = Field(default_factory=list)
    used_targets: list[UsedTarget] = Field(default_factory=list)
    observed_level: Optional[Cefr] = None

    _trim_findings = field_validator("findings", mode="before")(_truncate(3))


# --------------------------------------------------------------------------------------
# Persona Card / Scene Bible (prompts/scene_generator_system.md)
# --------------------------------------------------------------------------------------


class SpeechSample(_Model):
    move: str
    line: str


class Voice(_Model):
    sentence_length: Literal["short", "mixed", "long"] = "mixed"
    energy: Literal["low", "medium", "high"] = "medium"
    humor: Literal["none", "dry", "gentle", "goofy", "sarcastic"] = "none"
    habits: list[str] = Field(default_factory=list)
    favorite_fillers: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)


class PersonaCard(_Model):
    name: str
    role_in_scene: str
    age_range: str = ""
    background: str = ""
    default_register: Register = "casual"
    voice: Voice = Field(default_factory=Voice)
    wants_in_scene: str = ""
    attitude_to_learner: str = ""
    never_does: list[str] = Field(default_factory=list)
    speech_samples: list[SpeechSample] = Field(default_factory=list)


class Setting(_Model):
    where: str = ""
    when: str = ""
    atmosphere: str = ""


class OtherPerson(_Model):
    name: str
    relation: str = ""
    facts: list[str] = Field(default_factory=list)


class Beat(_Model):
    id: str
    description: str
    earliest_turn: int = 1


class NeedSlot(_Model):
    target_id: str
    slot: str


class RegisterShiftPlan(_Model):
    at_beat: str
    to: Register
    why: str = ""


class RegisterPlan(_Model):
    default: Register = "casual"
    shifts: list[RegisterShiftPlan] = Field(default_factory=list)


class SceneBible(_Model):
    title: str
    setting: Setting = Field(default_factory=Setting)
    situation: str = ""
    established_facts: list[str] = Field(default_factory=list)
    people: list[OtherPerson] = Field(default_factory=list)
    stakes: str = ""
    open_threads: list[str] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    need_slots: list[NeedSlot] = Field(default_factory=list)
    register_plan: RegisterPlan = Field(default_factory=RegisterPlan)
    opening_intent: str = ""
    max_turns: Annotated[int, Field(ge=4, le=40)] = 12
    forbidden: list[str] = Field(default_factory=list)


class PersonaScene(_Model):
    persona: PersonaCard
    scene: SceneBible


# --------------------------------------------------------------------------------------
# Director brief and Responder output (prompts/responder_*.md)
# --------------------------------------------------------------------------------------


class RecastBrief(_Model):
    id: str
    learner_said: str
    natural_form: str
    meaning: str = ""


class StealthTarget(_Model):
    id: str
    expression: str
    meaning: str = ""
    register: str = ""
    opportunity_hint: str = ""


class BeatBrief(_Model):
    id: str
    description: str


class LearnerSignal(_Model):
    energy: Literal["low", "normal", "high"] = "normal"
    out_of_frame: bool = False
    comprehension_issue: bool = False


class DirectorBrief(_Model):
    is_opening: bool = False
    register: Register = "casual"
    dialogue_move: Move
    length_words: int
    end_with_question: bool
    recast: Optional[RecastBrief] = None
    stealth_targets: list[StealthTarget] = Field(default_factory=list)
    scene_beat: Optional[BeatBrief] = None
    learner_signal: LearnerSignal = Field(default_factory=LearnerSignal)
    avoid_openers: list[str] = Field(default_factory=list)
    clarification_focus: Optional[str] = None
    rewrite_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _question_rule(self) -> "DirectorBrief":
        # Questions are only ever allowed for ASK and CLARIFY (responder_system.md section 4).
        expected = self.dialogue_move in ("ASK", "CLARIFY")
        if self.end_with_question != expected:
            raise ValueError("end_with_question must be true exactly for ASK and CLARIFY")
        if self.recast is not None and self.stealth_targets:
            raise ValueError("a recast turn must not also carry stealth targets")
        return self


class ResponderReply(_Model):
    bubbles: list[str]
    recast_applied: list[str] = Field(default_factory=list)
    opportunities_created: list[str] = Field(default_factory=list)
    scene_beat_used: Optional[str] = None

    @field_validator("bubbles", mode="before")
    @classmethod
    def _bubbles(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            v = [b for b in v if isinstance(b, str) and b.strip()][:2]
        return v

    @model_validator(mode="after")
    def _non_empty(self) -> "ResponderReply":
        if not self.bubbles:
            raise ValueError("at least one non-empty bubble is required")
        return self

    @property
    def text(self) -> str:
        return "\n".join(self.bubbles)


class HtmlOut(_Model):
    html: str


class Grounding(_Model):
    """One number the debrief quotes, and the `session_facts` field it comes from."""

    field: str
    value: int


class DebriefOut(HtmlOut):
    grounding: list[Grounding] = Field(default_factory=list)


class InterlocutorReply(_Model):
    bubbles: list[str]

    @field_validator("bubbles", mode="before")
    @classmethod
    def _bubbles(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = [v]
        return [b for b in v if isinstance(b, str) and b.strip()][:1]


# --------------------------------------------------------------------------------------
# Vault / mastery
# --------------------------------------------------------------------------------------


class Variant(_Model):
    """Another way to say the same thing, with the registers in which THAT form is natural."""

    form: str
    register_tags: list[str] = Field(default_factory=list)


class SessionFacts(_Model):
    """Observable facts about a finished scene, computed by code from the turn log.

    The Debrief Writer may only open with an observation that one of these fields supports.
    All numbers are whole numbers so that a cited figure can be matched exactly.
    """

    learner_turns: int = 0
    avg_words: Optional[int] = None
    longest_turn_words: Optional[int] = None
    first_half_avg_words: Optional[int] = None     # only from 4 learner turns on
    second_half_avg_words: Optional[int] = None
    length_trend: Optional[Literal["longer", "shorter", "steady"]] = None
    questions_asked: int = 0                       # learner turns that contain a question mark
    voice_turns: int = 0
    text_turns: int = 0
    beats_reached: int = 0
    beats_total: int = 0
    cards_shown: int = 0


class Expression(_Model):
    """An item in "My Phrase Vault". One per user per expression.

    `register_tags` is the range of THIS exact `form` (not the union of its alternatives'), and each
    entry of `variants` carries its own range. `acceptable_forms` is the flat superset used only to
    recognise use and to keep the character from saying the target; it says nothing about where a
    form is natural.
    """

    id: str
    form: str
    meaning: str = ""
    register_tags: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    l1_trap: Optional[str] = None
    acceptable_forms: list[str] = Field(default_factory=list)
    opportunity_hint: Optional[str] = None


# --------------------------------------------------------------------------------------
# Modes (prompts/mode_*.md)
# --------------------------------------------------------------------------------------


class Listener(_Model):
    id: str
    name: str
    relationship: str
    register: Register
    cares_about: str = ""
    moment: str = ""


class RegisterShiftSetup(_Model):
    title: str
    intent: str
    core_message: str
    listeners: list[Listener]
    learner_prompt: str

    @field_validator("listeners")
    @classmethod
    def _three(cls, v: list[Listener]) -> list[Listener]:
        if len(v) != 3:
            raise ValueError("exactly three listeners are required")
        if len({x.register for x in v}) != 3:
            raise ValueError("the three listeners must use three different registers")
        if len({formality_of(x.register) for x in v}) < 2:
            raise ValueError("the listeners must differ in formality (casual + warm + witty is one formality, not a shift)")
        return v


class RegisterShiftVersionEval(_Model):
    listener_id: str
    fit: Annotated[int, Field(ge=1, le=3)]
    direction: Literal["too_formal", "too_blunt", "too_casual", "too_hedged", "too_intense", "none"] = "none"
    finding: Optional[Finding] = None


class MostEffective(_Model):
    listener_id: str
    why: str = ""


class ModelVersion(_Model):
    listener_id: str
    text: str


class RegisterShiftEval(_Model):
    per_version: list[RegisterShiftVersionEval]
    differentiation: Annotated[int, Field(ge=1, le=3)]
    most_effective: MostEffective
    pivot_move: str = ""
    model_versions: list[ModelVersion] = Field(default_factory=list)
    used_targets: list[UsedTarget] = Field(default_factory=list)


class OtherPersonShort(_Model):
    name: str
    relation: str = ""
    history: str = ""


class DilemmaOption(_Model):
    label: Literal["soft", "balanced", "firm"]
    text: str
    effect_on_listener: str = ""
    risk: str = ""
    best_when: str = ""


class DilemmaSetup(_Model):
    title: str
    skill_focus: str
    register: Register
    situation: str
    other_person: OtherPersonShort
    learner_goal: str
    relationship_goal: str
    learner_prompt: str
    three_options: list[DilemmaOption]

    @field_validator("three_options")
    @classmethod
    def _three(cls, v: list[DilemmaOption]) -> list[DilemmaOption]:
        if len(v) != 3:
            raise ValueError("exactly three options are required")
        return v


class DilemmaEval(_Model):
    achieves_goal: Annotated[int, Field(ge=1, le=3)]
    protects_relationship: Annotated[int, Field(ge=1, le=3)]
    closest_option: Literal["soft", "balanced", "firm", "none"]
    tone_position: Annotated[float, Field(ge=0, le=10)]
    what_it_does: str
    finding: Optional[Finding] = None
    keep: Optional[str] = None
    used_targets: list[UsedTarget] = Field(default_factory=list)


class ThinkAloudSetup(_Model):
    title: str
    topic: str
    assigned_position: str
    learner_prompt: str
    focus_moves: list[str]
    counterpoints: list[str]
    closing_move: str
    min_turns: int = 3
    max_turns: int = 6


class MoveObserved(_Model):
    move: str
    count: int
    examples: list[str] = Field(default_factory=list)


class PhraseBankItem(_Model):
    move: str
    phrase: str
    note: str = ""


class ThinkAloudEval(_Model):
    moves_observed: list[MoveObserved] = Field(default_factory=list)
    moves_missing: list[str] = Field(default_factory=list)
    structure: Literal["list_of_claims", "claim_reason", "developed"]
    hedging_calibration: Literal["under_hedged", "calibrated", "over_hedged"]
    fluency_note: Optional[str] = None
    findings: list[Finding] = Field(default_factory=list)
    phrase_bank: list[PhraseBankItem] = Field(default_factory=list)
    used_targets: list[UsedTarget] = Field(default_factory=list)

    _trim_findings = field_validator("findings", mode="before")(_truncate(2))
    _trim_bank = field_validator("phrase_bank", mode="before")(_truncate(3))


class NearMiss(_Model):
    form: str
    reason: str = ""


class WorkoutSetup(_Model):
    context_id: str
    situation: str
    response_slot: str
    support: Optional[str] = None
    acceptable_forms: list[str]
    near_misses: list[NearMiss] = Field(default_factory=list)
    register: Register = "casual"
    bridge: Optional[str] = None  # only present in REINFORCE output


class WorkoutEval(_Model):
    outcome: Literal["target_correct", "target_attempt", "equivalent", "miss"]
    naturalness: Optional[Annotated[int, Field(ge=1, le=3)]] = None
    register_fit: bool = True
    issue: Optional[str] = None
    l1_note: Optional[Finding] = None
    feedback_text: str = ""
    schedule_hint: Literal["advance", "repeat_soon", "reset"]


# --------------------------------------------------------------------------------------
# Pipeline result
# --------------------------------------------------------------------------------------


class TurnResult(_Model):
    bubbles: list[str]
    speaker: str
    action: str
    card_finding_id: Optional[int] = None     # show a 💡 button that opens this finding's card
    proactive_card: bool = False              # critical issue: the UI may show the card right away
    clarify: bool = False
    out_of_frame: bool = False
    scene_over: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)
