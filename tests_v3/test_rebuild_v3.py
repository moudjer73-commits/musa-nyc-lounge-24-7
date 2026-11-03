"""S3: the V3 phrase projection (mastery + scheduler replayed by the rebuild) and the database helpers
the orchestrator uses: due phrases, spontaneous candidates, the review queue, debrief counts, taken ids."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

import validation as V
from helpers_s3 import (
    LEARNER,
    at,
    attempt,
    evidence,
    exposure,
    expression,
    next_review,
    phrase_row,
    tiny_seed,
    when,
)
from feedback_queue import DebriefCounts
from retrieval import JudgeShed, JudgeVerdict, grade_attempt, to_attempt_event
from schemas import PromptKind, RetrievalOrigin
from test_validation import TEXT, raw_diag

P = "come_to_think_of_it"
PROJECTIONS = (
    "session_state_projection",
    "phrase_state_projection",
    "error_pattern_projection",
    "feedback_item_projection",
    "phrase_evidence",
)

# a phrase that reaches Stable only through the whole gate (5 successes, 3 independent, 2 families,
# 2 delayed successes with a gap of at least 7 days, no failure, a success within 90 days)
MAIN_PLAN = [
    (0, dict(family="workplace")),
    (1, dict(family="friendships", due=True)),
    (4, dict(family="workplace", due=True)),
    (11, dict(family="friendships", due=True)),
    (25, dict(family="workplace", due=True, origin="spontaneous")),
]


@pytest.fixture
def lab(db):
    db.seed_catalog(tiny_seed(expression(P)))
    return db


def run_plan(db, plan, phrase=P, prefix="k"):
    for n, (day, kw) in enumerate(plan):
        attempt(db, phrase, day, f"{prefix}{n}", **kw)


def dump(db):
    with db.read() as conn:
        return {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in PROJECTIONS}


# ------------------------------------------------------------------------------ the first attempt


def test_a_first_success_creates_the_phrase_row_and_enters_the_schedule(lab):
    attempt(lab, P, 0, "k0", family="workplace")
    row = phrase_row(lab, P)
    assert (row["success_count"], row["independent_success_count"], row["spontaneous_success_count"]) == (1, 1, 0)
    assert (row["schedule_step"], row["next_review_at_utc"]) == (0, at(1))
    assert (row["last_outcome"], row["last_success_at_utc"]) == ("success", at(0))
    assert row["home_context_family"] == "workplace" and json.loads(row["context_families_json"]) == ["workplace"]
    assert row["current_stage"] == "Produced-independently"
    assert (row["dim_p"], row["dim_i"], row["dim_a"]) == (92.5, 92.5, 75.0)
    assert row["dim_r"] is None and row["dim_t"] is None and row["mastery_index"] is None
    assert row["unresolved_critical_errors"] == 0 and row["updated_at_utc"] == at(0)
    rows = evidence(lab, P)
    assert [(r["dimension"], r["score"], r["weight"]) for r in rows] == [
        ("independence", 92.5, 1.0), ("pragmatic", 75.0, 1.0), ("production", 92.5, 1.0),
    ]
    assert all((r["origin"], r["context_family"], r["is_delayed"], r["gap_days"], r["echo"], r["retracted"]) == ("review", "workplace", 0, None, 0, 0) for r in rows)


def test_a_first_failure_or_partial_leaves_the_phrase_unscheduled(lab):
    attempt(lab, P, 0, "k0", outcome="failure", linguistic=0, functional=1)
    row = phrase_row(lab, P)
    assert row["success_count"] == 0 and row["next_review_at_utc"] is None and row["last_outcome"] == "failure"
    assert row["current_stage"] == "Unseen"  # no exposure, no success: nothing below Seen to fall to
    attempt(lab, P, 1, "k1", outcome="partial", linguistic=2, functional=2)
    assert phrase_row(lab, P)["next_review_at_utc"] is None


def test_an_exposure_then_a_due_attempt_is_a_delayed_attempt(lab):
    exposure(lab, P, 0, "x0")
    assert phrase_row(lab, P)["current_stage"] == "Seen"
    attempt(lab, P, 2, "k0", family="workplace", due=True)
    rows = {r["dimension"]: r for r in evidence(lab, P)}
    assert rows["production"]["is_delayed"] == 1 and rows["production"]["weight"] == 1.5
    assert rows["independence"]["weight"] == 1.5 and rows["production"]["gap_days"] == pytest.approx(2.0)
    assert rows["pragmatic"]["weight"] == 1.0


def test_a_hinted_success_is_evidence_with_the_lower_weight_and_not_independent(lab):
    attempt(lab, P, 0, "k0", outcome="hinted_success", hint_level=1, family="workplace")
    row = phrase_row(lab, P)
    assert row["success_count"] == 1 and row["independent_success_count"] == 0
    assert row["current_stage"] == "Produced-with-prompt"
    assert {r["dimension"]: r["weight"] for r in evidence(lab, P)}["production"] == 0.8


# ------------------------------------------------------------------------------ the 60-day simulation


def test_sixty_day_simulation_reaches_stable_only_through_the_full_gate(lab):
    seen = []
    for n, (day, kw) in enumerate(MAIN_PLAN):
        attempt(lab, P, day, f"k{n}", **kw)
        row = phrase_row(lab, P)
        seen.append((row["current_stage"], row["schedule_step"], row["next_review_at_utc"]))
    assert seen == [
        ("Produced-independently", 0, at(1)),
        ("Produced-independently", 1, at(4)),
        ("Produced-independently", 2, at(11)),
        ("Produced-independently", 3, at(25)),  # four successes: not Stable yet
        ("Stable", 4, at(55)),
    ]
    row = phrase_row(lab, P)
    assert (row["success_count"], row["independent_success_count"], row["spontaneous_success_count"]) == (5, 5, 1)
    assert (row["delayed_success_count"], row["max_delayed_gap_days"]) == (4, pytest.approx(14.0))
    assert json.loads(row["context_families_json"]) == ["friendships", "workplace"]
    assert row["dim_t"] == 92.5  # two transfer rows, both 92.5


def test_a_due_failure_drops_two_steps_and_a_rung_and_the_next_success_restores_stable(lab):
    run_plan(lab, MAIN_PLAN)
    attempt(lab, P, 55, "kf", outcome="failure", linguistic=0, functional=0, due=True)
    row = phrase_row(lab, P)
    assert (row["current_stage"], row["schedule_step"], row["next_review_at_utc"]) == ("Produced-independently", 2, at(62))
    assert row["last_outcome"] == "failure" and row["success_count"] == 5
    attempt(lab, P, 62, "kg", family="friendships", due=True)
    row = phrase_row(lab, P)
    assert (row["current_stage"], row["schedule_step"], row["next_review_at_utc"]) == ("Stable", 3, at(76))
    assert row["success_count"] == 6


def test_stable_goes_stale_at_read_time_after_ninety_days_without_a_success(lab):
    run_plan(lab, MAIN_PLAN)
    stored = phrase_row(lab, P)["current_stage"]
    assert stored == "Stable"
    fresh = {s["phrase_id"]: s for s in lab.get_phrase_states(LEARNER, now=when(25 + 90))}
    stale = {s["phrase_id"]: s for s in lab.get_phrase_states(LEARNER, now=when(25 + 91))}
    assert fresh[P]["effective_stage"] == "Stable"
    assert stale[P]["effective_stage"] == "Produced-independently"
    assert stale[P]["current_stage"] == "Stable"  # the projection itself never reads a clock


@pytest.mark.parametrize(
    "name,plan,expected",
    [
        (
            "one_family_only",
            [(d, {**kw, "family": "workplace"}) for d, kw in MAIN_PLAN],
            "Produced-spontaneously",
        ),
        (
            "never_delayed",
            [(d, dict(family="workplace" if d % 2 else "friendships", origin="spontaneous" if d == 5 else "review")) for d in range(6)],
            "Produced-spontaneously",
        ),
        (
            "gaps_under_seven_days",
            [(d, dict(family="workplace" if d % 2 else "friendships", due=d > 0, origin="spontaneous" if d == 7 else "review")) for d in range(8)],
            "Produced-spontaneously",
        ),
        (
            "hinted_only",
            [(d, {**kw, "outcome": "hinted_success", "hint_level": 1}) for d, kw in MAIN_PLAN],
            "Produced-with-prompt",
        ),
        (
            "latest_outcome_is_a_failure",
            [*MAIN_PLAN, (26, dict(outcome="failure", linguistic=0, functional=0))],
            "Produced-independently",
        ),
        (
            "only_four_successes",
            MAIN_PLAN[:4],
            "Produced-independently",
        ),
    ],
)
def test_stable_needs_every_condition_of_the_gate(lab, name, plan, expected):
    run_plan(lab, plan)
    assert phrase_row(lab, P)["current_stage"] == expected, name


def test_general_context_successes_never_count_as_a_second_family(lab):
    plan = [(d, {**kw, "family": "general" if d % 2 else "workplace"}) for d, kw in MAIN_PLAN]
    run_plan(lab, plan)
    row = phrase_row(lab, P)
    assert row["current_stage"] != "Stable" and row["dim_t"] is None


# ------------------------------------------------------------------------------ echo, retraction, unknown phrases


def test_an_echo_writes_no_evidence_and_changes_no_state(lab):
    attempt(lab, P, 0, "k0", family="workplace")
    state, rows = phrase_row(lab, P), evidence(lab, P)
    attempt(lab, P, 1, "k1", outcome="failure", linguistic=None, functional=None, echo=True, due=True)
    assert phrase_row(lab, P) == state and evidence(lab, P) == rows


def test_an_echo_alone_creates_no_phrase_row(lab):
    attempt(lab, P, 0, "k0", outcome="failure", linguistic=None, functional=None, echo=True)
    with lab.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM phrase_state_projection WHERE phrase_id = ?", (P,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM phrase_evidence WHERE phrase_id = ?", (P,)).fetchone()[0] == 0


def retract(db, event_id, key="k_ret", day=2):
    db.append_event_ex(
        learner_id=LEARNER, session_id="s1", event_type="phrase_evidence_retracted",
        payload={"target_event_id": event_id, "reason": "judge_overruled"},
        correlation_id=f"corr_{key}", idempotency_key=key, occurred_at_utc=at(day),
    )


def test_a_retracted_attempt_leaves_the_state_but_its_rows_stay_flagged(lab):
    first = attempt(lab, P, 0, "k0", family="workplace")
    second = attempt(lab, P, 1, "k1", family="friendships", due=True)
    assert phrase_row(lab, P)["success_count"] == 2
    retract(lab, second.event_id)
    row = phrase_row(lab, P)
    assert (row["success_count"], row["schedule_step"], row["next_review_at_utc"]) == (1, 0, at(1))
    assert row["last_evidence_event_id"] == first.event_id and row["dim_t"] is None
    rows = evidence(lab, P)
    assert {r["retracted"] for r in rows if r["source_event_id"] == second.event_id} == {1}
    assert {r["retracted"] for r in rows if r["source_event_id"] == first.event_id} == {0}


def test_retracting_the_only_attempt_removes_the_phrase_row(lab):
    only = attempt(lab, P, 0, "k0", family="workplace")
    retract(lab, only.event_id)
    with lab.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM phrase_state_projection WHERE phrase_id = ?", (P,)).fetchone()[0] == 0


def test_an_attempt_on_a_phrase_that_is_not_in_the_catalog_is_ignored(lab):
    attempt(lab, "not_in_the_catalog", 0, "k0")
    with lab.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM phrase_state_projection WHERE phrase_id = 'not_in_the_catalog'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM phrase_evidence WHERE phrase_id = 'not_in_the_catalog'").fetchone()[0] == 0


def test_learners_do_not_affect_each_other(lab):
    lab.upsert_learner(1002)
    lab.create_session("s2", 1002, started_at_utc=at(0))
    attempt(lab, P, 0, "a0", family="workplace")
    before = phrase_row(lab, P)
    attempt(lab, P, 1, "b0", session="s2", learner=1002, outcome="failure", linguistic=0, functional=0, due=True)
    assert phrase_row(lab, P) == before
    assert phrase_row(lab, P, 1002)["next_review_at_utc"] is None


# ------------------------------------------------------------------------------ legacy evidence and the hybrid stage


def legacy_assessment(db, phrase, day, key, evidence_kind, **over):
    payload = {
        "target_id": phrase, "evidence": evidence_kind, "was_revealed_in_task": False, "was_directly_cued": False,
        "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": True,
        "is_novel_context": False, "register_profile": "casual", "training_mode": "open_scene",
    }
    payload.update(over)
    return db.append_event_ex(
        learner_id=LEARNER, session_id="s1", event_type="phrase_production_assessed", payload=payload,
        correlation_id=f"corr_{key}", idempotency_key=key, occurred_at_utc=at(day),
    )


def test_a_legacy_phrase_keeps_the_v2_stage_while_the_v3_columns_fill_in(lab):
    legacy_assessment(lab, "plate_is_full", 0, "l0", "independent_production")
    row = phrase_row(lab, "plate_is_full")
    assert row["current_stage"] == "Produced-independently"  # the v2 ladder
    assert (row["success_count"], row["independent_success_count"]) == (1, 1)
    assert (row["schedule_step"], row["next_review_at_utc"]) == (0, at(1))
    assert {(r["dimension"], r["score"], r["origin"]) for r in evidence(lab, "plate_is_full")} == {
        ("production", 75.0, "drill"), ("independence", 75.0, "drill"),
    }
    assert row["dim_p"] == 75.0 and row["dim_i"] == 75.0


def test_a_cued_legacy_production_is_a_hinted_success_and_recognition_is_not_a_success(lab):
    legacy_assessment(lab, "talk_something_over", 0, "l1", "cued_production", was_directly_cued=True)
    cued = phrase_row(lab, "talk_something_over")
    assert cued["current_stage"] == "Produced-with-prompt" and cued["independent_success_count"] == 0
    legacy_assessment(lab, "push_back_on", 0, "l2", "recognition")
    recognised = phrase_row(lab, "push_back_on")
    assert recognised["current_stage"] == "Recognized" and recognised["success_count"] == 0
    assert recognised["next_review_at_utc"] is None and recognised["dim_r"] == 100.0


def test_a_native_attempt_switches_a_legacy_phrase_to_the_v3_stage_and_counts_the_legacy_success(lab):
    legacy_assessment(lab, "plate_is_full", 0, "l0", "independent_production")
    attempt(lab, "plate_is_full", 1, "n0", due=True)
    row = phrase_row(lab, "plate_is_full")
    assert row["success_count"] == 2 and row["independent_success_count"] == 2
    assert (row["schedule_step"], row["next_review_at_utc"]) == (1, at(4))
    assert row["current_stage"] == "Produced-independently"
    attempt(lab, "plate_is_full", 4, "n1", outcome="failure", linguistic=0, functional=0, due=True)
    assert phrase_row(lab, "plate_is_full")["current_stage"] == "Produced-with-prompt"  # one rung below


# ------------------------------------------------------------------------------ determinism


def test_the_rebuild_is_deterministic_and_equal_to_the_incrementally_built_state(lab, clock):
    exposure(lab, P, 0, "x0")
    run_plan(lab, MAIN_PLAN)
    attempt(lab, P, 55, "kf", outcome="failure", linguistic=0, functional=0, due=True)
    attempt(lab, P, 56, "kecho", outcome="failure", linguistic=None, functional=None, echo=True)
    legacy_assessment(lab, "plate_is_full", 3, "l0", "spontaneous_production")
    attempt(lab, "plate_is_full", 9, "n0", due=True)
    incremental = dump(lab)
    clock.advance(timedelta(days=400))  # a rebuild must not depend on when it runs
    lab.rebuild_all_projections()
    first = dump(lab)
    lab.rebuild_all_projections()
    second = dump(lab)
    assert incremental == first == second
    assert first["phrase_evidence"] and first["phrase_state_projection"]


# ------------------------------------------------------------------------------ due phrases (S0 5.6)


@pytest.fixture
def seven(db):
    db.seed_catalog(
        tiny_seed(*[expression(f"p{i}", f"quux {i} zed", families=("workplace", "friendships")) for i in range(7)])
    )
    for i in range(7):
        attempt(db, f"p{i}", i, f"first{i}", family="workplace")
    return db


def test_due_phrases_are_the_most_overdue_first_and_at_most_five(seven):
    plans = seven.get_due_phrases(LEARNER, when(10))
    assert [p.phrase_id for p in plans] == ["p0", "p1", "p2", "p3", "p4"]
    assert plans[0].days_overdue == pytest.approx(9.0) and plans[0].step == 0
    assert len(seven.get_due_phrases(LEARNER, when(10), limit=2)) == 2


def test_only_phrases_whose_time_has_come_are_due(seven):
    assert seven.get_due_phrases(LEARNER, when(0.5)) == []
    assert [p.phrase_id for p in seven.get_due_phrases(LEARNER, when(3))] == ["p0", "p1", "p2"]  # next_review <= now


def test_review_plans_rotate_the_family_and_the_prompt_kind_from_the_last_attempt(seven):
    first = seven.get_due_phrases(LEARNER, when(10))[0]
    assert (first.family, first.prompt_kind) == ("friendships", PromptKind.SITUATION)
    attempt(seven, "p0", 10, "second0", family="friendships", prompt_kind="situation", due=True)
    assert next_review(seven, "p0") == when(13)
    second = [p for p in seven.get_due_phrases(LEARNER, when(14), limit=10) if p.phrase_id == "p0"][0]
    assert (second.family, second.step, second.prompt_kind) == ("workplace", 1, PromptKind.FUNCTION)


def test_an_unscheduled_phrase_is_never_due(lab):
    exposure(lab, P, 0, "x0")
    attempt(lab, P, 1, "k0", outcome="failure", linguistic=0, functional=0)
    assert lab.get_due_phrases(LEARNER, when(100)) == []


def test_a_shed_judge_writes_nothing_and_the_item_stays_due_until_a_check_succeeds(lab):
    attempt(lab, P, 0, "k0", family="workplace")
    entry = lab.get_phrase_entry(P)
    assert [p.phrase_id for p in lab.get_due_phrases(LEARNER, when(2))] == [P]

    def shed(_request):
        raise JudgeShed("quota")

    result = grade_attempt("it occurs to me now", entry.target(), judge=shed)
    assert result.shed and not result.records_evidence  # the caller appends nothing
    with lab.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_type = 'retrieval_attempted'").fetchone()[0] == 1
    assert [p.phrase_id for p in lab.get_due_phrases(LEARNER, when(2))] == [P]

    graded = grade_attempt("it occurs to me now", entry.target(), judge=lambda r: JudgeVerdict(2, 2), context_family="friendships")
    event = to_attempt_event(
        graded, phrase_id=P, origin=RetrievalOrigin.REVIEW, prompt_kind=PromptKind.MEANING,
        context_family="friendships", due=True, schedule_step_before=0,
    )
    lab.append_event_ex(
        learner_id=LEARNER, session_id="s1", event_type="retrieval_attempted", payload=event,
        correlation_id="corr_retry", idempotency_key="retry", occurred_at_utc=at(3),
    )
    row = phrase_row(lab, P)
    assert (row["schedule_step"], row["next_review_at_utc"]) == (1, at(6))
    # production: 92.5 (weight 1.0, first attempt) and 75.0 (weight 1.5, a delayed one)
    assert row["dim_p"] == pytest.approx((92.5 * 1.0 + 75.0 * 1.5) / 2.5)
    assert lab.get_due_phrases(LEARNER, when(3)) == []


# ------------------------------------------------------------------------------ spontaneous candidates


def test_spontaneous_targets_are_scheduled_phrases_that_are_due_or_at_step_one_and_match_the_register(lab):
    assert lab.get_spontaneous_candidates(LEARNER, "casual", when(0)) == []  # nothing scheduled yet
    attempt(lab, P, 0, "k0", family="workplace")
    assert lab.get_spontaneous_candidates(LEARNER, "casual", when(0.5)) == []  # step 0, not due
    assert lab.get_spontaneous_candidates(LEARNER, "casual", when(1.5)) == [P]  # due
    assert lab.get_spontaneous_candidates(LEARNER, "executive", when(1.5)) == []  # register not in the catalog row
    attempt(lab, P, 1.5, "k1", family="friendships", due=True)  # step 1, next review in 3 days
    assert lab.get_spontaneous_candidates(LEARNER, "diplomatic", when(2)) == [P]


# ------------------------------------------------------------------------------ feedback queue, debrief, taken ids


def put_feedback(db, dg, *, learner=LEARNER, pattern="grammar:x", score=10.0, delivery="deferred", status="pending", day=0.0):
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO feedback_item_projection (
                diagnostic_id, learner_id, session_id, turn_event_seq, pattern_id, context_family, domain,
                classification, repair_scope, kind, severity, original_span, alternative, priority_class,
                priority_score, delivery, status, created_at_utc
            ) VALUES (?, ?, 's1', 1, ?, 'general', 'linguistic', 'error', 'phrase_span', 'grammar', 'medium',
                      'discuss about', 'discuss', 2, ?, ?, ?, ?)
            """,
            (dg, learner, pattern, score, delivery, status, at(day)),
        )


def test_get_taken_diagnostic_ids_is_global_by_default_and_can_be_scoped_to_a_learner(lab):
    assert lab.get_taken_diagnostic_ids() == frozenset()
    lab.upsert_learner(1002)
    put_feedback(lab, "dg_00000001")
    put_feedback(lab, "dg_00000002", learner=1002)
    assert lab.get_taken_diagnostic_ids() == {"dg_00000001", "dg_00000002"}
    assert lab.get_taken_diagnostic_ids(LEARNER) == {"dg_00000001"}
    assert lab.get_taken_diagnostic_ids(1002) == {"dg_00000002"}


def test_the_lazy_container_answers_membership_with_one_lookup(lab):
    put_feedback(lab, "dg_0000000a")
    taken = lab.taken_diagnostic_ids()
    assert "dg_0000000a" in taken and "dg_0000000b" not in taken and 5 not in taken


def test_validate_analysis_avoids_stored_diagnostic_ids_through_the_lazy_container(lab):
    first = V.diagnostic_id_for("ev_turn0001", 0)
    put_feedback(lab, first)
    result = V.validate_analysis(
        {"diagnostics": [raw_diag()]}, learner_text=TEXT, turn_event_id="ev_turn0001", taken_ids=lab.taken_diagnostic_ids()
    )
    assert result.analysis.diagnostics[0].diagnostic_id == V.diagnostic_id_for("ev_turn0001", 0, 1)
    free = V.validate_analysis({"diagnostics": [raw_diag()]}, learner_text=TEXT, turn_event_id="ev_turn0001")
    assert free.analysis.diagnostics[0].diagnostic_id == first


def test_the_review_queue_groups_by_pattern_and_leaves_out_what_is_not_deferred_and_pending(lab):
    put_feedback(lab, "dg_a0000001", pattern="grammar:a", score=5, day=5)
    put_feedback(lab, "dg_a0000002", pattern="grammar:a", score=9, day=8)
    put_feedback(lab, "dg_b0000001", pattern="grammar:b", score=7, day=6)
    put_feedback(lab, "dg_c0000001", pattern="grammar:c", score=99, day=6, delivery="in_scene")
    put_feedback(lab, "dg_d0000001", pattern="grammar:d", score=99, day=6, status="retracted")
    queue = lab.get_review_queue(LEARNER, when(10))
    assert [e.representative.diagnostic_id for e in queue] == ["dg_a0000002", "dg_b0000001"]
    assert queue[0].seen_count == 2 and queue[0].member_ids == ("dg_a0000002", "dg_a0000001")
    assert len(lab.get_review_queue(LEARNER, when(10), limit=1)) == 1
    assert lab.get_review_queue(LEARNER, when(60)) == []  # everything is older than 21 days with no recurrence


def test_debrief_counts_turns_cards_and_distinct_target_phrases_used(lab):
    for n in range(3):
        lab.accept_scene_turn(learner_id=LEARNER, session_id="s1", text=f"turn {n}", idempotency_key=f"t{n}", correlation_id=f"c{n}")
    legacy_assessment(lab, "plate_is_full", 1, "u1", "independent_production")
    legacy_assessment(lab, "plate_is_full", 2, "u1b", "spontaneous_production")  # same phrase: counted once
    legacy_assessment(lab, "push_back_on", 3, "u2", "cued_production", was_directly_cued=True)  # cued: not "used"
    legacy_assessment(lab, "talk_something_over", 4, "u3", "independent_production", failed_attempt=True)
    attempt(lab, P, 5, "r1", origin="spontaneous", family="workplace")
    counts = lab.get_debrief_counts("s1")
    assert counts.turns == 3 and counts.cards_shown == 0
    assert counts.targets_used == 2  # plate_is_full (once) and the spontaneous retrieval of the new phrase
    with lab.transaction() as conn:
        conn.execute("UPDATE session_state_projection SET cards_in_scene = 2 WHERE session_id = 's1'")
    assert lab.get_debrief_counts("s1").cards_shown == 2


def test_debrief_counts_exclude_echoes_failures_and_retracted_evidence(lab):
    echo = attempt(lab, P, 1, "e1", outcome="failure", linguistic=None, functional=None, echo=True)
    miss = attempt(lab, P, 2, "e2", outcome="failure", linguistic=0, functional=0)
    good = legacy_assessment(lab, "plate_is_full", 3, "e3", "independent_production")
    assert lab.get_debrief_counts("s1").targets_used == 1
    retract(lab, good.event_id, key="e_ret", day=4)
    assert lab.get_debrief_counts("s1").targets_used == 0
    assert echo.inserted and miss.inserted
    assert lab.get_debrief_counts("no_such_session") == DebriefCounts(0, 0, 0)
