"""feedback_policy.py (S0 sections 5.2 and 5.3, owner Claude): which card, if any, the learner sees now.

Pure functions, no I/O, no clock. The orchestrator hands over the validated and scored diagnostics
(``validation.ScoredDiagnostic``), the Responder's status and the session runtime state; the result is
a ``PolicyDecisionV2`` with one ``FindingDecision`` per diagnostic (class, score, delivery, reasons and
the six gate results). ``interaction.py`` re-exports ``resolve_interaction_policy``.

"LLMs propose, code decides": the Analyst's ``timing_hint`` is recorded but never consulted.

Order (S0 5.3): P0, then P1, then P4. At most one visible card per turn. ``curveball_turn`` is always
False in V3.0, so the ``scene_stability`` gate reduces to "no active interaction".

Interpretations the contract leaves open (listed again in S2_HANDOFF.md):

* A P0 needs a carrier diagnostic. When the Responder's status is not ok, the carrier is the
  highest-scoring ``error`` diagnostic; when the Analyst set ``p0.candidate``, every high-severity
  diagnostic is a carrier. The rest keep their normal class and are deferred behind the P0.
* ``responder_asked_clarification`` (Path A) is an input set by the orchestrator from the Responder
  output; this module does not guess it from the reply text.
* P0 ignores the active-interaction rule and the queue floor; P1 and P4 respect both.
* The exception "P1 with severity high and domain pragmatic" uses gap 2 and no warm-up, still needs a
  non-silent preference, no flow, and fewer than 6 cards; the strain bonus does not apply to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from schemas import (
    BudgetSnapshot,
    Classification,
    CommunicativeAssessment,
    ComprehensionStatus,
    Delivery,
    Domain,
    FeedbackPreference,
    FindingDecision,
    P0Signal,
    PolicyDecisionV2,
    PriorityClass,
    Severity,
)
from validation import ScoredDiagnostic

GATE_NAMES = (
    "scene_stability",
    "timing",
    "cognitive",
    "scope",
    "no_higher_priority",
    "learning_value",
)


@dataclass(frozen=True)
class PolicyConfig:
    """S0 O4: starting values, not calibrated."""

    light_gap: int = 6
    active_gap: int = 3
    strain_threshold: float = 0.6
    strain_gap_bonus: int = 2
    warmup_turns: int = 2
    max_cards_per_scene: int = 6
    p0_cooldown_turns: int = 3
    p1_score_floor: float = 55.0
    queue_floor: float = 25.0
    pragmatic_high_gap: int = 2
    min_learning_value: int = 2


DEFAULT_POLICY_CONFIG = PolicyConfig()


@dataclass(frozen=True)
class RuntimeState:
    """Session runtime counters (``session_state_projection``) plus the learner strain in 0 to 1
    (``policy_engine.compute_learner_strain``)."""

    turns_since_visible_card: int = 4
    cards_in_scene: int = 0
    turns_since_p0: int = 99
    strain: float = 0.0


@dataclass(frozen=True)
class PolicyInputs:
    turn_event_id: str
    scene_turn_index: int  # 1-based position of this learner turn in the scene
    feedback_preference: FeedbackPreference = FeedbackPreference.LIGHT
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK
    responder_asked_clarification: bool = False  # Path A
    communicative: CommunicativeAssessment = field(default_factory=CommunicativeAssessment)
    p0: P0Signal = field(default_factory=P0Signal)
    runtime: RuntimeState = field(default_factory=RuntimeState)
    has_active_interaction: bool = False
    curveball_turn: bool = False  # accepted for symmetry with the contract; V3.0 never sets it


# ------------------------------------------------------------------------------ budget


def required_gap(preference: FeedbackPreference, strain: float, cfg: PolicyConfig = DEFAULT_POLICY_CONFIG) -> Optional[int]:
    """Minimum learner turns between visible cards: light 6, active 3, +2 under strain; None for silent."""
    if preference == FeedbackPreference.SILENT:
        return None
    base = cfg.light_gap if preference == FeedbackPreference.LIGHT else cfg.active_gap
    return base + (cfg.strain_gap_bonus if strain >= cfg.strain_threshold else 0)


def is_pragmatic_high(s: ScoredDiagnostic) -> bool:
    d = s.diagnostic
    return d.severity == Severity.HIGH and d.domain == Domain.PRAGMATIC


def budget_failures(inputs: PolicyInputs, cfg: PolicyConfig = DEFAULT_POLICY_CONFIG, *, exception: bool = False) -> list[str]:
    """Why the budget does not allow an end_of_turn card now (empty = allowed)."""
    rt = inputs.runtime
    failures: list[str] = []
    if inputs.feedback_preference == FeedbackPreference.SILENT:
        failures.append("preference_silent")
    if inputs.communicative.in_emotional_or_story_flow:
        failures.append("emotional_or_story_flow")
    if rt.cards_in_scene >= cfg.max_cards_per_scene:
        failures.append("scene_card_limit")
    if not exception and inputs.scene_turn_index <= cfg.warmup_turns:
        failures.append("scene_warmup")
    gap = cfg.pragmatic_high_gap if exception else required_gap(inputs.feedback_preference, rt.strain, cfg)
    if gap is not None and rt.turns_since_visible_card < gap:
        failures.append("gap_not_satisfied")
    return failures


def compute_budget(inputs: PolicyInputs, cfg: PolicyConfig = DEFAULT_POLICY_CONFIG) -> BudgetSnapshot:
    rt = inputs.runtime
    return BudgetSnapshot(
        preference=inputs.feedback_preference,
        turns_since_visible_card=max(rt.turns_since_visible_card, 0),
        cards_in_scene=max(rt.cards_in_scene, 0),
        strain=min(max(rt.strain, 0.0), 1.0),
        allowed=not budget_failures(inputs, cfg),
    )


# ------------------------------------------------------------------------------ priority class (5.2)


def p0_carrier_ids(inputs: PolicyInputs, scored: Sequence[ScoredDiagnostic]) -> set[str]:
    carriers: set[str] = set()
    if inputs.comprehension_status != ComprehensionStatus.OK:
        errors = [s for s in scored if s.diagnostic.classification == Classification.ERROR]
        if errors:
            top = sorted(errors, key=lambda s: (-s.score, s.raw_index))[0]
            carriers.add(top.diagnostic.diagnostic_id)
    if inputs.p0.candidate:
        carriers.update(s.diagnostic.diagnostic_id for s in scored if s.diagnostic.severity == Severity.HIGH)
    return carriers


def classify_priority(s: ScoredDiagnostic, carriers: set[str], cfg: PolicyConfig = DEFAULT_POLICY_CONFIG) -> PriorityClass:
    """First match wins. P2 and P3 are reserved for V3.1."""
    d = s.diagnostic
    if d.diagnostic_id in carriers:
        return PriorityClass.P0
    if d.classification == Classification.ERROR and (
        d.severity == Severity.HIGH or (d.severity == Severity.MEDIUM and s.score >= cfg.p1_score_floor)
    ):
        return PriorityClass.P1
    return PriorityClass.P4


# ------------------------------------------------------------------------------ resolution (5.3)


def _gates(
    s: ScoredDiagnostic,
    cls: PriorityClass,
    classes: dict[str, PriorityClass],
    inputs: PolicyInputs,
    cfg: PolicyConfig,
) -> dict[str, bool]:
    rt = inputs.runtime
    gap = cfg.pragmatic_high_gap if (cls == PriorityClass.P1 and is_pragmatic_high(s)) else required_gap(
        inputs.feedback_preference, rt.strain, cfg
    )
    others = [c for k, c in classes.items() if k != s.diagnostic.diagnostic_id]
    return {
        "scene_stability": not inputs.has_active_interaction,  # curveball_turn is always False in V3.0
        "timing": gap is not None and rt.turns_since_visible_card >= gap,
        "cognitive": rt.strain < cfg.strain_threshold and not inputs.communicative.in_emotional_or_story_flow,
        "scope": rt.cards_in_scene < cfg.max_cards_per_scene,
        "no_higher_priority": not any(c < cls for c in others),
        "learning_value": int(s.diagnostic.learning_value) >= cfg.min_learning_value,
    }


def resolve_interaction_policy(
    inputs: PolicyInputs,
    scored: Sequence[ScoredDiagnostic],
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
) -> PolicyDecisionV2:
    cfg = config
    ordered = sorted(scored, key=lambda s: (-s.score, s.raw_index))
    carriers = p0_carrier_ids(inputs, ordered)
    classes = {s.diagnostic.diagnostic_id: classify_priority(s, carriers, cfg) for s in ordered}
    gate_results = {s.diagnostic.diagnostic_id: _gates(s, classes[s.diagnostic.diagnostic_id], classes, inputs, cfg) for s in ordered}
    budget = compute_budget(inputs, cfg)

    decided: dict[str, tuple[Delivery, list[str]]] = {}
    selected: Optional[str] = None
    pending_clarification: Optional[str] = None

    def put(s: ScoredDiagnostic, delivery: Delivery, reasons: list[str]) -> None:
        decided[s.diagnostic.diagnostic_id] = (delivery, reasons)

    def undecided(cls: PriorityClass) -> list[ScoredDiagnostic]:
        return [s for s in ordered if classes[s.diagnostic.diagnostic_id] == cls and s.diagnostic.diagnostic_id not in decided]

    # Queue floor: below it a finding stays in the log (and counts toward the pattern projection) but is
    # not queued. P0 is exempt.
    for s in ordered:
        if classes[s.diagnostic.diagnostic_id] != PriorityClass.P0 and s.score < cfg.queue_floor:
            put(s, Delivery.SUPPRESSED, ["queue_floor"])

    # 1. P0. Any P0, delivered or not, suppresses every other card this turn.
    p0_items = undecided(PriorityClass.P0)
    p0_active = bool(p0_items) or inputs.comprehension_status != ComprehensionStatus.OK
    if p0_items:
        lead = p0_items[0]
        if inputs.responder_asked_clarification:  # Path A: the reply already is the clarification
            put(lead, Delivery.DEFERRED, ["handled_in_reply"])
        elif inputs.runtime.turns_since_p0 >= cfg.p0_cooldown_turns:  # Path B
            put(lead, Delivery.IMMEDIATE_FOLLOWUP, ["p0_followup"])
            selected = lead.diagnostic.diagnostic_id
        else:
            put(lead, Delivery.DEFERRED, ["p0_cooldown"])
            if inputs.p0.line:
                pending_clarification = inputs.p0.line
        for other in p0_items[1:]:
            put(other, Delivery.DEFERRED, ["superseded_by_p0"])
    if p0_active:
        for s in ordered:
            if s.diagnostic.diagnostic_id not in decided:
                put(s, Delivery.DEFERRED, ["suppressed_by_p0"])

    # 2. P1: the highest score the budget allows, else deferred.
    if not p0_active:
        for s in undecided(PriorityClass.P1):
            did = s.diagnostic.diagnostic_id
            if selected is not None:
                put(s, Delivery.DEFERRED, ["one_card_per_turn"])
                continue
            failures = budget_failures(inputs, cfg, exception=is_pragmatic_high(s))
            if inputs.has_active_interaction:
                failures.append("active_interaction")
            if failures:
                put(s, Delivery.DEFERRED, failures)
                continue
            reasons = ["p1_end_of_turn"] + (["pragmatic_high_exception"] if is_pragmatic_high(s) else [])
            put(s, Delivery.END_OF_TURN, reasons)
            selected = did

    # 3. P4: only when active, nothing higher was delivered, and all six gates pass.
    if not p0_active:
        for s in undecided(PriorityClass.P4):
            did = s.diagnostic.diagnostic_id
            if selected is not None:
                put(s, Delivery.DEFERRED, ["one_card_per_turn"])
                continue
            failed = [name for name in GATE_NAMES if not gate_results[did][name]]
            if inputs.feedback_preference != FeedbackPreference.ACTIVE:
                failed.insert(0, "preference_not_active")
            if inputs.scene_turn_index <= cfg.warmup_turns:
                failed.append("scene_warmup")
            if failed:
                put(s, Delivery.DEFERRED, [f"gate_{name}" if name in GATE_NAMES else name for name in failed])
                continue
            put(s, Delivery.END_OF_TURN, ["p4_end_of_turn"])
            selected = did

    decisions: list[FindingDecision] = []
    for s in ordered:
        did = s.diagnostic.diagnostic_id
        delivery, reasons = decided.get(did, (Delivery.DEFERRED, ["not_selected"]))
        decisions.append(
            FindingDecision(
                diagnostic_id=did,
                priority_class=classes[did],
                priority_score=s.score,
                delivery=delivery,
                reasons=reasons,
                gates=gate_results[did],
            )
        )
    return PolicyDecisionV2(
        turn_event_id=inputs.turn_event_id,
        decisions=decisions,
        selected_diagnostic_id=selected,
        budget=budget,
        curveball_turn=False,
        pending_clarification=pending_clarification,
    )
