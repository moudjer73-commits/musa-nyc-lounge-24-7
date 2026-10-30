from __future__ import annotations

import pytest

from coach.models import Analysis
from coach.policy import (
    CARD_THRESHOLD, CorrectionBudget, DRILL_THRESHOLD, decide, priority, suggest_drill_mode,
)
from coach.state import compute_strain

from conftest import mk_finding, mk_state

BUDGET = CorrectionBudget()
COLLOC = "collocation.COLLOC_LIGHT_VERB.take_decision"


def analysis(*findings, **kw) -> Analysis:
    return Analysis(findings=list(findings), **kw)


def test_no_findings_is_silent():
    d = decide(analysis(), BUDGET, mk_state())
    assert d.action == "silent" and d.finding is None


def test_first_time_collocation_is_a_silent_recast_not_a_card():
    d = decide(analysis(mk_finding()), BUDGET, mk_state())
    assert d.action == "recast" and d.wants_recast
    assert not d.recast_also          # the recast IS the action


def test_second_time_the_card_is_offered_and_the_recast_still_happens():
    d = decide(analysis(mk_finding()), BUDGET, mk_state(pattern_counts={COLLOC: 1}))
    assert d.action == "offer_card" and d.recast_also and d.wants_recast


def test_pragmatic_issue_outranks_the_same_severity_collocation():
    prag = mk_finding(category="pragmatic", pattern_key="pragmatic.DIRECTNESS.bare_refusal", span="No, I can't")
    s = mk_state()
    assert priority(prag, 0, s) > priority(mk_finding(), 0, s)
    assert decide(analysis(prag), BUDGET, s).action == "offer_card"


def test_minor_grammar_slip_stays_silent_but_is_logged_by_the_pipeline():
    g = mk_finding(category="grammar", severity=1, pattern_key="grammar.GEN.article", recast_form=None, l1_transfer=False)
    d = decide(analysis(g), BUDGET, mk_state())
    assert d.action == "silent" and d.finding is None


def test_cefr_shifts_the_thresholds():
    prag1 = mk_finding(category="pragmatic", severity=1, pattern_key="pragmatic.X.y", span="thanks a lot, really")
    assert decide(analysis(prag1), BUDGET, mk_state(cefr="B1")).action == "silent"
    assert decide(analysis(prag1), BUDGET, mk_state(cefr="B2")).action == "recast"


# ------------------------------------------------------------------ flow gating


def test_storytelling_lets_the_story_run_but_still_recasts():
    d = decide(analysis(mk_finding(), discourse_state="storytelling"), BUDGET, mk_state(pattern_counts={COLLOC: 1}))
    assert d.action == "recast"


def test_emotional_moment_gets_no_visible_or_hidden_teaching_only_the_recap():
    d = decide(analysis(mk_finding(), discourse_state="emotional"), BUDGET, mk_state(pattern_counts={COLLOC: 1}))
    assert d.action == "defer" and not d.wants_recast
    assert len(d.deferred) == 1


@pytest.mark.parametrize("state_name", ["frustrated", "out_of_frame"])
def test_frustration_or_stepping_out_adds_no_load(state_name):
    d = decide(
        analysis(mk_finding(), discourse_state=state_name, clarification_needed=True),
        BUDGET, mk_state(pattern_counts={COLLOC: 1}),
    )
    assert d.action == "silent" and d.deferred == ()


# ------------------------------------------------------------------ clarify


def test_comprehension_breakdown_is_clarified_in_scene():
    comp = mk_finding(category="comprehension", severity=3, pattern_key="comprehension.GEN.x", span="then angry the boss")
    d = decide(analysis(comp, clarification_needed=True, clarification_focus="who was angry"), BUDGET, mk_state())
    assert d.action == "clarify" and d.finding is comp


def test_clarify_is_allowed_even_in_an_emotional_moment():
    d = decide(analysis(clarification_needed=True, discourse_state="emotional"), BUDGET, mk_state())
    assert d.action == "clarify"


# ------------------------------------------------------------------ budget


def test_budget_gap_by_strictness_and_strain():
    assert BUDGET.effective_gap(mk_state(strictness="light")) == 6
    assert BUDGET.effective_gap(mk_state(strictness="normal")) == 4
    assert BUDGET.effective_gap(mk_state(strictness="coach")) == 2
    strained = mk_state(recent_words=[3, 3, 3], baseline_words=12.0)
    assert strained.strain >= 0.5
    assert BUDGET.effective_gap(strained) == 6


def test_at_most_one_visible_correction_every_four_turns():
    f = mk_finding()
    s = lambda since: mk_state(pattern_counts={COLLOC: 1}, turns_since_card=since)  # noqa: E731
    assert decide(analysis(f), BUDGET, s(3)).action == "recast"          # budget closed: silent fallback
    assert decide(analysis(f), BUDGET, s(4)).action == "offer_card"


def test_warmup_turns_never_get_a_visible_correction():
    f = mk_finding()
    d = decide(analysis(f), BUDGET, mk_state(pattern_counts={COLLOC: 2}, turn_index=2))
    assert d.action == "recast"


def test_session_card_cap():
    f = mk_finding()
    d = decide(analysis(f), BUDGET, mk_state(pattern_counts={COLLOC: 2}, cards_this_session=6))
    assert d.action == "recast"


def test_critical_pragmatic_issue_uses_the_reduced_gap_or_waits_for_the_recap():
    crit = mk_finding(category="pragmatic", severity=3, pattern_key="pragmatic.DIRECTNESS.insult", span="you are stupid")
    assert decide(analysis(crit), BUDGET, mk_state(turns_since_card=2)).action == "offer_card"
    d = decide(analysis(crit), BUDGET, mk_state(turns_since_card=1))
    assert d.action == "defer" and d.deferred == (crit,)


def test_strain_lowers_priority():
    f = mk_finding()
    calm = priority(f, 0, mk_state())
    tense = priority(f, 0, mk_state(recent_words=[2, 2, 2], baseline_words=12.0))
    assert tense < calm


# ------------------------------------------------------------------ drills


def test_third_occurrence_queues_a_drill_alongside_the_card():
    d = decide(analysis(mk_finding()), BUDGET, mk_state(pattern_counts={COLLOC: DRILL_THRESHOLD - 1}))
    assert d.drill_patterns == (COLLOC,)
    assert d.action == "offer_card"


def test_a_pattern_that_is_below_every_threshold_still_becomes_a_drill_on_the_third_time():
    f = mk_finding(category="fluency", severity=1, pattern_key="fluency.restarts", recast_form=None, l1_transfer=False)
    d = decide(analysis(f), BUDGET, mk_state(pattern_counts={"fluency.restarts": 2}))
    assert d.action == "queue_drill" and d.drill_patterns == ("fluency.restarts",)


def test_drill_cooldown_and_queue_dedupe():
    f = mk_finding(category="fluency", severity=1, pattern_key="fluency.restarts", recast_form=None, l1_transfer=False)
    for kw in ({"drilled_recently": ["fluency.restarts"]}, {"queued_drills": ["fluency.restarts"]}):
        d = decide(analysis(f), BUDGET, mk_state(pattern_counts={"fluency.restarts": 5}, **kw))
        assert d.drill_patterns == () and d.action == "silent"


def test_no_drills_while_telling_a_story():
    d = decide(analysis(mk_finding(), discourse_state="storytelling"), BUDGET, mk_state(pattern_counts={COLLOC: 5}))
    assert d.drill_patterns == ()


def test_drill_mode_suggestions():
    assert suggest_drill_mode(mk_finding()) == "phrase_workout"
    assert suggest_drill_mode(mk_finding(category="pragmatic", l1_pattern="DIRECTNESS", pattern_key="pragmatic.DIRECTNESS.x")) == "pragmatic_dilemma"
    assert suggest_drill_mode(mk_finding(category="register", l1_pattern="OVER_FORMAL", pattern_key="register.OVER_FORMAL.x")) == "register_shift"
    assert suggest_drill_mode(mk_finding(category="register", l1_pattern="DISCOURSE_MARKER_GAP", pattern_key="register.D.x")) == "register_shift"


# ------------------------------------------------------------------ strain


def test_strain_from_length_and_hesitation():
    assert compute_strain([12, 12, 12], 12.0) == 0.0
    assert compute_strain([6, 6, 6], 12.0) == 0.5
    assert compute_strain([12, 12, 12], 12.0, hesitation_ratio=0.8) == 0.8
    assert compute_strain([], 12.0) == 0.0
    assert compute_strain([30, 30, 30], 12.0) == 0.0          # long answers are not strain


def test_card_threshold_is_what_the_docs_say():
    assert CARD_THRESHOLD == 2.0
