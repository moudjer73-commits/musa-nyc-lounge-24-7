"""S2: feedback_policy.py (S0 5.2 and 5.3): P0/P1/P4 classes, delivery routing, budget and the six gates."""

from __future__ import annotations

import itertools

import pytest

import feedback_policy as FP
import interaction
import validation as V
from schemas import (
    DOMAIN_BY_KIND,
    Classification,
    CommunicativeAssessment,
    ComprehensionStatus,
    Delivery,
    DiagnosticV2,
    Domain,
    FeedbackPreference,
    P0Signal,
    PolicyDecisionV2,
    PriorityClass,
    classification_from_kind,
    confidence_band_for,
)
from validation import ScoredDiagnostic

LIGHT, ACTIVE, SILENT = FeedbackPreference.LIGHT, FeedbackPreference.ACTIVE, FeedbackPreference.SILENT
P0, P1, P4 = PriorityClass.P0, PriorityClass.P1, PriorityClass.P4


def make(did, *, severity="medium", kind="grammar", score=60.0, learning=2, impact=2, raw_index=0, conf=0.9):
    cls = classification_from_kind(kind)
    d = DiagnosticV2(
        diagnostic_id=did,
        original_span="x y",
        normalized_pattern_id="PAT_x",
        kind=kind,
        severity=severity,
        alternative="z",
        alternative_status="required" if cls == Classification.ERROR else "optional",
        intended_meaning="m",
        explanation_en="Explain it well.",
        preserves_intended_meaning=True,
        context_fit_verified=True,
        confidence_band=confidence_band_for(conf),
        confidence_score=conf,
        domain=DOMAIN_BY_KIND.get(kind, Domain.LINGUISTIC),
        classification=cls,
        repair_scope="local",
        span_start=0,
        span_end=3,
        communicative_impact=impact,
        learning_value=learning,
        linguistic_confidence=conf,
    )
    return ScoredDiagnostic(d, score, "global", conf, 0, raw_index)


def rt(turns=10, cards=0, since_p0=99, strain=0.0):
    return FP.RuntimeState(turns_since_visible_card=turns, cards_in_scene=cards, turns_since_p0=since_p0, strain=strain)


def inputs(**kw):
    kw.setdefault("turn_event_id", "ev_turn")
    kw.setdefault("scene_turn_index", 5)
    kw.setdefault("runtime", rt())
    return FP.PolicyInputs(**kw)


def decision(result, did):
    return next(d for d in result.decisions if d.diagnostic_id == did)


def delivered(result):
    return [d for d in result.decisions if d.delivery in (Delivery.IMMEDIATE_FOLLOWUP, Delivery.END_OF_TURN)]


# ----------------------------------------------------------------------------- 5.2: classes


def test_p1_is_an_error_with_high_severity_or_medium_severity_and_score_at_least_55():
    cfg = FP.DEFAULT_POLICY_CONFIG
    none: set[str] = set()
    assert FP.classify_priority(make("dg_00000001", severity="high", score=40.0), none, cfg) == P1
    assert FP.classify_priority(make("dg_00000001", severity="medium", score=55.0), none, cfg) == P1
    assert FP.classify_priority(make("dg_00000001", severity="medium", score=54.99), none, cfg) == P4
    assert FP.classify_priority(make("dg_00000001", severity="low", score=99.0), none, cfg) == P4


@pytest.mark.parametrize("kind", ["optional_upgrade", "acceptable_variation"])
def test_upgrades_and_alternatives_are_never_p1(kind):
    assert FP.classify_priority(make("dg_00000001", kind=kind, severity="high", score=90.0), set()) == P4


def test_p0_when_the_responder_is_not_ok_uses_the_top_error_as_the_carrier():
    a, b = make("dg_0000000a", score=70.0), make("dg_0000000b", score=80.0, raw_index=1)
    up = make("dg_0000000c", kind="optional_upgrade", score=99.0, raw_index=2)
    for status in (ComprehensionStatus.AMBIGUOUS, ComprehensionStatus.REVERSED):
        assert FP.p0_carrier_ids(inputs(comprehension_status=status), [a, b, up]) == {"dg_0000000b"}
    assert FP.p0_carrier_ids(inputs(comprehension_status=ComprehensionStatus.AMBIGUOUS), [up]) == set()
    assert FP.p0_carrier_ids(inputs(), [a, b, up]) == set()


def test_p0_when_the_analyst_flags_it_needs_high_severity():
    high, med = make("dg_0000000a", severity="high"), make("dg_0000000b", severity="medium", raw_index=1)
    signal = P0Signal(candidate=True, line="Did you mean Monday?")
    assert FP.p0_carrier_ids(inputs(p0=signal), [high, med]) == {"dg_0000000a"}
    assert FP.p0_carrier_ids(inputs(p0=signal), [med]) == set()
    assert FP.p0_carrier_ids(inputs(p0=P0Signal(candidate=False)), [high]) == set()


def test_p2_and_p3_are_never_produced():
    for sev, kind, score in itertools.product(["low", "medium", "high"], ["grammar", "pragmatics", "optional_upgrade"], [0.0, 30.0, 60.0, 100.0]):
        assert FP.classify_priority(make("dg_00000001", severity=sev, kind=kind, score=score), set()) in (P0, P1, P4)


# ----------------------------------------------------------------------------- 5.3: budget


@pytest.mark.parametrize(
    "pref,strain,gap",
    [(SILENT, 0.0, None), (LIGHT, 0.0, 6), (ACTIVE, 0.0, 3), (LIGHT, 0.6, 8), (ACTIVE, 0.6, 5), (ACTIVE, 0.59, 3), (SILENT, 0.9, None)],
)
def test_required_gap(pref, strain, gap):
    assert FP.required_gap(pref, strain) == gap


@pytest.mark.parametrize(
    "turns,pref,allowed",
    [(5, LIGHT, False), (6, LIGHT, True), (2, ACTIVE, False), (3, ACTIVE, True), (10, SILENT, False)],
)
def test_budget_gap_by_preference(turns, pref, allowed):
    b = FP.compute_budget(inputs(feedback_preference=pref, runtime=rt(turns=turns)))
    assert b.allowed is allowed and b.preference == pref and b.turns_since_visible_card == turns


def test_budget_strain_raises_the_gap_by_two():
    assert FP.compute_budget(inputs(feedback_preference=ACTIVE, runtime=rt(turns=4, strain=0.0))).allowed
    assert not FP.compute_budget(inputs(feedback_preference=ACTIVE, runtime=rt(turns=4, strain=0.6))).allowed
    assert FP.compute_budget(inputs(feedback_preference=ACTIVE, runtime=rt(turns=5, strain=0.6))).allowed


@pytest.mark.parametrize("turn,allowed", [(1, False), (2, False), (3, True)])
def test_no_cards_in_the_first_two_learner_turns_of_a_scene(turn, allowed):
    assert FP.compute_budget(inputs(feedback_preference=ACTIVE, scene_turn_index=turn)).allowed is allowed


def test_flow_and_scene_limit_close_the_budget():
    flow = CommunicativeAssessment(in_emotional_or_story_flow=True)
    assert not FP.compute_budget(inputs(feedback_preference=ACTIVE, communicative=flow)).allowed
    assert FP.compute_budget(inputs(feedback_preference=ACTIVE, runtime=rt(cards=5))).allowed
    assert not FP.compute_budget(inputs(feedback_preference=ACTIVE, runtime=rt(cards=6))).allowed


def test_budget_snapshot_reports_the_runtime_state():
    b = FP.compute_budget(inputs(runtime=rt(turns=7, cards=2, strain=0.4)))
    assert (b.turns_since_visible_card, b.cards_in_scene, b.strain) == (7, 2, 0.4)


# ----------------------------------------------------------------------------- 5.3: P0


def p0_case(*, asked=False, since_p0=99, line="Did you mean Monday?", **kw):
    carrier = make("dg_000000a0", score=80.0)
    other = make("dg_000000b0", score=70.0, raw_index=1)
    return FP.resolve_interaction_policy(
        inputs(comprehension_status=ComprehensionStatus.AMBIGUOUS, responder_asked_clarification=asked,
               p0=P0Signal(candidate=False, line=line), runtime=rt(since_p0=since_p0), **kw),
        [carrier, other],
    )


def test_path_a_the_reply_already_asked_so_the_p0_is_deferred_as_handled_in_reply():
    r = p0_case(asked=True)
    assert decision(r, "dg_000000a0").delivery == Delivery.DEFERRED
    assert decision(r, "dg_000000a0").reasons == ["handled_in_reply"]
    assert r.selected_diagnostic_id is None and r.pending_clarification is None


def test_path_b_follows_up_immediately_after_three_quiet_turns():
    r = p0_case(since_p0=3)
    assert decision(r, "dg_000000a0").delivery == Delivery.IMMEDIATE_FOLLOWUP
    assert decision(r, "dg_000000a0").priority_class == P0
    assert r.selected_diagnostic_id == "dg_000000a0" and r.pending_clarification is None


def test_path_b_cooldown_defers_and_keeps_the_line_for_the_next_brief():
    r = p0_case(since_p0=2)
    d = decision(r, "dg_000000a0")
    assert d.delivery == Delivery.DEFERRED and d.reasons == ["p0_cooldown"]
    assert r.selected_diagnostic_id is None and r.pending_clarification == "Did you mean Monday?"
    assert p0_case(since_p0=2, line=None).pending_clarification is None


def test_any_p0_suppresses_every_other_card_this_turn():
    for kwargs in (dict(asked=True), dict(since_p0=3), dict(since_p0=1)):
        r = p0_case(feedback_preference=ACTIVE, **kwargs)
        other = decision(r, "dg_000000b0")
        assert other.delivery == Delivery.DEFERRED and other.reasons == ["suppressed_by_p0"]
        assert len(delivered(r)) <= 1


def test_p0_ignores_warmup_flow_silence_the_gap_and_an_active_interaction():
    flow = CommunicativeAssessment(in_emotional_or_story_flow=True)
    r = p0_case(since_p0=3, feedback_preference=SILENT, scene_turn_index=1, communicative=flow, has_active_interaction=True)
    assert decision(r, "dg_000000a0").delivery == Delivery.IMMEDIATE_FOLLOWUP


def test_p0_is_exempt_from_the_queue_floor():
    low = make("dg_000000a0", score=5.0)
    r = FP.resolve_interaction_policy(inputs(comprehension_status=ComprehensionStatus.REVERSED, runtime=rt(since_p0=0)), [low])
    assert decision(r, "dg_000000a0").delivery == Delivery.DEFERRED


def test_a_second_p0_carrier_is_superseded():
    a, b = make("dg_000000a0", severity="high", score=90.0), make("dg_000000b0", severity="high", score=80.0, raw_index=1)
    r = FP.resolve_interaction_policy(inputs(p0=P0Signal(candidate=True)), [b, a])
    assert decision(r, "dg_000000a0").delivery == Delivery.IMMEDIATE_FOLLOWUP
    assert decision(r, "dg_000000b0").reasons == ["superseded_by_p0"]


def test_a_confused_turn_with_no_error_carrier_still_delivers_no_card():
    up = make("dg_000000c0", kind="optional_upgrade", score=90.0)
    r = FP.resolve_interaction_policy(inputs(comprehension_status=ComprehensionStatus.AMBIGUOUS, feedback_preference=ACTIVE), [up])
    d = decision(r, "dg_000000c0")
    assert d.priority_class == P4 and d.delivery == Delivery.DEFERRED and d.reasons == ["suppressed_by_p0"]
    assert r.selected_diagnostic_id is None


# ----------------------------------------------------------------------------- 5.3: P1


def test_p1_delivers_the_highest_score_once_the_budget_allows():
    a, b = make("dg_0000000a", severity="high", score=70.0), make("dg_0000000b", severity="high", score=90.0, raw_index=1)
    r = FP.resolve_interaction_policy(inputs(), [a, b])
    assert r.selected_diagnostic_id == "dg_0000000b"
    assert decision(r, "dg_0000000b").delivery == Delivery.END_OF_TURN
    assert decision(r, "dg_0000000a").reasons == ["one_card_per_turn"]
    assert [d.diagnostic_id for d in r.decisions] == ["dg_0000000b", "dg_0000000a"]  # score order


def test_p1_is_deferred_when_the_budget_does_not_allow():
    a = make("dg_0000000a", severity="high", score=70.0)
    for kw, reason in [
        (dict(runtime=rt(turns=2)), "gap_not_satisfied"),
        (dict(scene_turn_index=2), "scene_warmup"),
        (dict(feedback_preference=SILENT), "preference_silent"),
        (dict(runtime=rt(cards=6)), "scene_card_limit"),
        (dict(communicative=CommunicativeAssessment(in_emotional_or_story_flow=True)), "emotional_or_story_flow"),
        (dict(has_active_interaction=True), "active_interaction"),
    ]:
        r = FP.resolve_interaction_policy(inputs(**kw), [a])
        d = decision(r, "dg_0000000a")
        assert d.delivery == Delivery.DEFERRED and reason in d.reasons, (kw, d.reasons)
        assert r.selected_diagnostic_id is None


def test_pragmatic_high_p1_skips_the_warmup_and_needs_only_a_gap_of_two():
    pragmatic = make("dg_0000000a", kind="pragmatics", severity="high", score=70.0)
    grammar = make("dg_0000000b", kind="grammar", severity="high", score=70.0, raw_index=1)
    quick = dict(scene_turn_index=1, runtime=rt(turns=2))
    r = FP.resolve_interaction_policy(inputs(**quick), [pragmatic])
    d = decision(r, "dg_0000000a")
    assert d.delivery == Delivery.END_OF_TURN and "pragmatic_high_exception" in d.reasons
    assert FP.resolve_interaction_policy(inputs(**quick), [grammar]).selected_diagnostic_id is None
    assert FP.resolve_interaction_policy(inputs(scene_turn_index=1, runtime=rt(turns=1)), [pragmatic]).selected_diagnostic_id is None


def test_pragmatic_high_exception_still_respects_silence_flow_and_the_scene_limit():
    p = make("dg_0000000a", kind="register", severity="high", score=70.0)
    base = dict(scene_turn_index=1, runtime=rt(turns=2))
    assert FP.resolve_interaction_policy(inputs(**base, feedback_preference=SILENT), [p]).selected_diagnostic_id is None
    assert FP.resolve_interaction_policy(inputs(**base, communicative=CommunicativeAssessment(in_emotional_or_story_flow=True)), [p]).selected_diagnostic_id is None
    assert FP.resolve_interaction_policy(inputs(scene_turn_index=1, runtime=rt(turns=2, cards=6)), [p]).selected_diagnostic_id is None
    assert FP.resolve_interaction_policy(inputs(**base), [p]).selected_diagnostic_id == "dg_0000000a"


def test_strain_does_not_block_a_p1_but_lengthens_its_gap():
    a = make("dg_0000000a", severity="high", score=70.0)
    assert FP.resolve_interaction_policy(inputs(runtime=rt(turns=6, strain=0.9)), [a]).selected_diagnostic_id is None
    assert FP.resolve_interaction_policy(inputs(runtime=rt(turns=8, strain=0.9)), [a]).selected_diagnostic_id == "dg_0000000a"


# ----------------------------------------------------------------------------- 5.3: P4 and the six gates


def p4(did="dg_000000d0", **kw):
    kw.setdefault("severity", "low")
    kw.setdefault("score", 40.0)
    return make(did, **kw)


def test_p4_is_delivered_only_when_the_preference_is_active():
    d = p4()
    assert FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [d]).selected_diagnostic_id == "dg_000000d0"
    for pref in (LIGHT, SILENT):
        r = FP.resolve_interaction_policy(inputs(feedback_preference=pref), [d])
        assert r.selected_diagnostic_id is None
        assert "preference_not_active" in decision(r, "dg_000000d0").reasons


GATE_CASES = [
    ("scene_stability", dict(has_active_interaction=True)),
    ("timing", dict(runtime=rt(turns=2))),
    ("cognitive", dict(runtime=rt(strain=0.6))),
    ("cognitive", dict(communicative=CommunicativeAssessment(in_emotional_or_story_flow=True))),
    ("scope", dict(runtime=rt(cards=6))),
]


@pytest.mark.parametrize("gate,kw", GATE_CASES)
def test_each_gate_blocks_p4_and_is_recorded(gate, kw):
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE, **kw), [p4()])
    d = decision(r, "dg_000000d0")
    assert d.gates[gate] is False
    assert d.delivery == Delivery.DEFERRED and f"gate_{gate}" in d.reasons
    assert set(d.gates) == set(FP.GATE_NAMES)


def test_learning_value_gate_needs_two_and_the_next_candidate_can_still_win():
    weak, strong = p4("dg_000000d1", score=45.0, learning=1), p4("dg_000000d2", score=40.0, learning=2, raw_index=1)
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [weak, strong])
    assert decision(r, "dg_000000d1").gates["learning_value"] is False
    assert "gate_learning_value" in decision(r, "dg_000000d1").reasons
    assert r.selected_diagnostic_id == "dg_000000d2"


def test_no_higher_priority_gate_sees_a_p1_even_when_that_p1_is_deferred():
    p1 = make("dg_0000000a", severity="high", score=70.0)
    low = p4()
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE, runtime=rt(turns=4)), [p1, low])  # active gap 3: both allowed
    assert r.selected_diagnostic_id == "dg_0000000a"
    assert decision(r, "dg_000000d0").reasons == ["one_card_per_turn"]
    assert decision(r, "dg_000000d0").gates["no_higher_priority"] is False
    assert decision(r, "dg_0000000a").gates["no_higher_priority"] is True


def test_a_delivered_p1_blocks_every_p4():
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [make("dg_0000000a", severity="high", score=70.0), p4("dg_000000d1"), p4("dg_000000d2", raw_index=2)])
    assert len(delivered(r)) == 1 and r.selected_diagnostic_id == "dg_0000000a"


def test_at_most_one_p4_and_it_is_the_highest_passing_score():
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [p4("dg_000000d1", score=30.0), p4("dg_000000d2", score=44.0, raw_index=1)])
    assert r.selected_diagnostic_id == "dg_000000d2" and len(delivered(r)) == 1


def test_p4_respects_the_warmup():
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE, scene_turn_index=2), [p4()])
    assert r.selected_diagnostic_id is None and "scene_warmup" in decision(r, "dg_000000d0").reasons


def test_upgrades_are_p4_and_deliverable_on_an_active_budget():
    up = make("dg_000000e0", kind="optional_upgrade", severity="low", score=40.0)
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [up])
    assert r.selected_diagnostic_id == "dg_000000e0" and decision(r, "dg_000000e0").priority_class == P4


# ----------------------------------------------------------------------------- queue floor


@pytest.mark.parametrize("score,delivery", [(24.99, Delivery.SUPPRESSED), (25.0, Delivery.DEFERRED), (0.0, Delivery.SUPPRESSED)])
def test_queue_floor(score, delivery):
    r = FP.resolve_interaction_policy(inputs(feedback_preference=LIGHT), [p4(score=score)])
    d = decision(r, "dg_000000d0")
    assert d.delivery == delivery
    if delivery == Delivery.SUPPRESSED:
        assert d.reasons == ["queue_floor"]


def test_a_suppressed_finding_is_never_selected_even_on_an_active_budget():
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), [p4(score=10.0)])
    assert r.selected_diagnostic_id is None and decision(r, "dg_000000d0").delivery == Delivery.SUPPRESSED


# ----------------------------------------------------------------------------- output contract


def test_the_result_is_a_valid_policy_decision_with_curveball_false():
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE, curveball_turn=True), [p4()])
    assert isinstance(r, PolicyDecisionV2) and r.curveball_turn is False
    assert PolicyDecisionV2.model_validate(r.model_dump(mode="json")) == r
    assert r.turn_event_id == "ev_turn"


def test_no_diagnostics_gives_an_empty_decision_with_the_budget():
    r = FP.resolve_interaction_policy(inputs(), [])
    assert r.decisions == [] and r.selected_diagnostic_id is None and r.budget.preference == LIGHT


def test_every_diagnostic_gets_exactly_one_decision_with_six_gates():
    ds = [make(f"dg_0000000{i}", severity=s, score=sc, raw_index=i) for i, (s, sc) in enumerate([("high", 80.0), ("medium", 58.0), ("low", 30.0), ("low", 10.0)])]
    r = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), ds)
    assert sorted(d.diagnostic_id for d in r.decisions) == sorted(s.diagnostic.diagnostic_id for s in ds)
    assert all(set(d.gates) == set(FP.GATE_NAMES) for d in r.decisions)


def test_the_analyst_timing_hint_is_not_consulted():
    base = make("dg_000000d0", severity="low", score=40.0)
    hinted = ScoredDiagnostic(base.diagnostic.model_copy(update={"timing_hint": "immediate"}), 40.0, "global", 0.9, 0, 0)
    assert FP.resolve_interaction_policy(inputs(feedback_preference=LIGHT), [hinted]).selected_diagnostic_id is None


def test_the_resolver_is_reachable_from_interaction_py():
    assert interaction.resolve_interaction_policy is FP.resolve_interaction_policy
    assert interaction.PolicyInputs is FP.PolicyInputs


def test_resolution_is_deterministic_and_does_not_depend_on_input_order():
    ds = [make("dg_0000000a", severity="high", score=70.0), p4("dg_000000d1", raw_index=1), p4("dg_000000d2", score=44.0, raw_index=2)]
    a = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), ds)
    b = FP.resolve_interaction_policy(inputs(feedback_preference=ACTIVE), list(reversed(ds)))
    assert a == b


def test_the_grid_never_shows_more_than_one_card_and_never_a_card_beside_a_p0():
    flows = [CommunicativeAssessment(), CommunicativeAssessment(in_emotional_or_story_flow=True)]
    sets = [
        [make("dg_0000000a", severity="high", score=70.0), p4("dg_000000d1", raw_index=1)],
        [make("dg_0000000a", kind="pragmatics", severity="high", score=70.0), make("dg_0000000b", severity="medium", score=60.0, raw_index=1)],
        [p4("dg_000000d1"), p4("dg_000000d2", raw_index=1), make("dg_000000e0", kind="optional_upgrade", score=50.0, raw_index=2)],
    ]
    for pref, strain, flow, interaction_open, status, asked, turn, cards, gap, since_p0, ds in itertools.product(
        [LIGHT, ACTIVE, SILENT], [0.0, 0.7], flows, [False, True], [ComprehensionStatus.OK, ComprehensionStatus.AMBIGUOUS],
        [False, True], [1, 3], [0, 6], [1, 3, 8], [0, 5], sets,
    ):
        r = FP.resolve_interaction_policy(
            inputs(feedback_preference=pref, communicative=flow, has_active_interaction=interaction_open, comprehension_status=status,
                   responder_asked_clarification=asked, scene_turn_index=turn, runtime=rt(turns=gap, cards=cards, since_p0=since_p0, strain=strain)),
            ds,
        )
        shown = delivered(r)
        assert len(shown) <= 1
        assert (r.selected_diagnostic_id is None) == (not shown)
        has_p0 = any(d.priority_class == P0 for d in r.decisions) or status != ComprehensionStatus.OK
        if has_p0:
            assert all(d.delivery != Delivery.END_OF_TURN for d in r.decisions)
        for d in r.decisions:
            if d.delivery == Delivery.END_OF_TURN:
                assert pref != SILENT and not flow.in_emotional_or_story_flow and cards < 6 and not interaction_open
            if d.delivery == Delivery.SUPPRESSED:
                assert d.priority_score < 25.0


# ----------------------------------------------------------------------------- validation feeds the resolver


def test_validated_diagnostics_flow_into_the_resolver():
    text = "We discussed about the plan yesterday."
    raw = {
        "diagnostics": [
            {
                "original_span": "discussed about", "normalized_pattern_id": "COLLOC_PREP_VERB:discuss_about", "kind": "collocation",
                "severity": "high", "alternative": "discussed", "alternative_status": "required", "intended_meaning": "talk about a plan",
                "explanation_en": "In English, 'discuss' takes its object directly.", "preserves_intended_meaning": True,
                "context_fit_verified": True, "repair_scope": "phrase_span", "communicative_impact": 3, "learning_value": 3,
                "linguistic_confidence": 0.95,
            }
        ]
    }
    v = V.validate_analysis(raw, turn_event_id="ev_turn", learner_text=text, prior_counts={"COLLOC_PREP_VERB:discuss_about:global": 4})
    r = FP.resolve_interaction_policy(inputs(), v.scored)
    d = r.decisions[0]
    assert d.priority_class == P1 and d.priority_score == v.scored[0].score and d.delivery == Delivery.END_OF_TURN
    assert r.selected_diagnostic_id == v.analysis.diagnostics[0].diagnostic_id
