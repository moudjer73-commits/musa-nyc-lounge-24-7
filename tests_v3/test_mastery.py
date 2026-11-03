"""S3: mastery.py (S0 5.5). Pure functions and the PhraseTracker, no database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import mastery
from mastery import (
    AttemptRecord,
    PhraseSnapshot,
    PhraseTracker,
    delay_since,
    derive_stage,
    dimension_scores,
    effective_stage,
    evidence_rows,
    is_new_family,
    level_score,
    mastery_index,
    one_rung_below,
    quality,
    recognition_row,
    stable_gate,
)
from schemas import PhraseStage

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
FAMILIES = ("workplace", "friendships", "general")


def d(n: float) -> datetime:
    return T0 + timedelta(days=n)


# ------------------------------------------------------------------------------ scores


@pytest.mark.parametrize("level,score", [(0, 0.0), (1, 40.0), (2, 75.0), (3, 100.0), (None, 0.0)])
def test_level_score(level, score):
    assert level_score(level) == score


@pytest.mark.parametrize("bad", [4, -1])
def test_level_score_rejects_an_out_of_range_ordinal(bad):
    with pytest.raises(ValueError):
        level_score(bad)


@pytest.mark.parametrize(
    "ling,func,outcome,expected",
    [
        (3, 3, "success", 100.0),
        (3, 2, "success", 92.5),  # 0.70 x 100 + 0.30 x 75
        (2, 2, "success", 75.0),
        (2, 2, "partial", 50.0),  # capped by the outcome
        (3, 3, "hinted_success", 100.0),
        (3, 3, "failure", 0.0),
        (None, None, "success", 0.0),
    ],
)
def test_quality_is_the_weighted_mix_capped_by_the_outcome(ling, func, outcome, expected):
    assert quality(ling, func, outcome) == expected


def test_new_family_needs_a_catalog_non_general_family_that_differs_from_home():
    assert is_new_family("friendships", FAMILIES, "workplace")
    assert not is_new_family("workplace", FAMILIES, "workplace")
    assert not is_new_family("general", FAMILIES, "workplace")
    assert not is_new_family("academic", FAMILIES, "workplace")  # not in this phrase's catalog families
    assert not is_new_family("friendships", FAMILIES, None)  # nothing to transfer from yet
    assert not is_new_family("", FAMILIES, "workplace")


# ------------------------------------------------------------------------------ evidence rows


def rows_by_dim(**kw):
    base = dict(outcome="success", linguistic_level=3, functional_level=2, hint_level=0, is_delayed=False, echo=False, new_family=False)
    base.update(kw)
    return {dim: (score, weight) for dim, score, weight in evidence_rows(**base)}


def test_a_plain_success_writes_production_independence_pragmatic():
    assert rows_by_dim() == {"production": (92.5, 1.0), "independence": (92.5, 1.0), "pragmatic": (75.0, 1.0)}


def test_a_delayed_success_weights_production_and_independence_at_1_5():
    got = rows_by_dim(is_delayed=True)
    assert got["production"] == (92.5, 1.5)
    assert got["independence"] == (92.5, 1.5)
    assert got["pragmatic"] == (75.0, 1.0)


def test_a_hinted_success_weighs_0_8_and_earns_no_independence():
    got = rows_by_dim(outcome="hinted_success", hint_level=1)
    assert got["production"] == (92.5, 0.8)
    assert got["independence"] == (0.0, 1.0)


def test_a_new_family_adds_transfer_and_lifts_the_pragmatic_weight():
    got = rows_by_dim(new_family=True)
    assert got["transfer"] == (92.5, 1.5)
    assert got["pragmatic"] == (75.0, 1.5)


def test_partial_independence_is_capped_at_50():
    got = rows_by_dim(outcome="partial", linguistic_level=2, functional_level=2)
    assert got["production"] == (50.0, 1.0)
    assert got["independence"] == (50.0, 1.0)


def test_a_failure_scores_zero_everywhere_including_pragmatic():
    got = rows_by_dim(outcome="failure", linguistic_level=0, functional_level=3)
    assert got == {"production": (0.0, 1.0), "independence": (0.0, 1.0), "pragmatic": (0.0, 1.0)}


def test_an_echo_writes_no_rows():
    assert evidence_rows(outcome="success", linguistic_level=3, functional_level=3, hint_level=0, is_delayed=False, echo=True, new_family=True) == []


def test_recognition_rows():
    assert recognition_row(True) == ("recognition", 100.0, 1.0)
    assert recognition_row(False) == ("recognition", 0.0, 1.0)


# ------------------------------------------------------------------------------ dimension scores and the index


def test_dimension_score_is_the_weighted_mean_and_none_without_rows():
    scores = dimension_scores([("production", 100.0, 1.0), ("production", 50.0, 3.0), ("transfer", 80.0, 1.5)])
    assert scores["production"] == pytest.approx((100 + 150) / 4)
    assert scores["transfer"] == 80.0
    assert scores["recognition"] is None


def test_mastery_index_needs_every_dimension_and_uses_the_contract_weights():
    dims = {"recognition": 100.0, "production": 50.0, "independence": 50.0, "transfer": 0.0, "pragmatic": 100.0}
    assert mastery_index(dims) == pytest.approx(0.10 * 100 + 0.30 * 50 + 0.20 * 50 + 0.20 * 0 + 0.20 * 100)
    assert mastery_index({**dims, "transfer": None}) is None
    assert sum(mastery.MASTERY_WEIGHTS.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------------------ delay


def test_delay_needs_a_reference_a_due_attempt_and_a_day():
    assert delay_since(None, d(5), True) == (False, None)
    assert delay_since(d(0), d(5), False) == (False, 5.0)
    assert delay_since(d(0), d(0.5), True) == (False, 0.5)
    assert delay_since(d(0), d(1), True) == (True, 1.0)


# ------------------------------------------------------------------------------ tracker


def rec(event_id: str, day: float, outcome: str = "success", **kw) -> AttemptRecord:
    base = dict(origin="review", hint_level=0, echo=False, context_family="general", due=False, linguistic_level=3, functional_level=2)
    base.update(kw)
    return AttemptRecord(event_id=event_id, occurred_at=d(day), outcome=outcome, **base)


def tracker(**kw) -> PhraseTracker:
    return PhraseTracker(catalog_families=FAMILIES, **kw)


def test_a_success_updates_the_counters_and_enters_the_schedule():
    t = tracker()
    rows = t.on_attempt(rec("e1", 0, context_family="workplace"))
    snap = t.snapshot()
    assert [r.dimension for r in rows] == ["production", "independence", "pragmatic"]
    assert (snap.success_count, snap.independent_success_count, snap.spontaneous_success_count) == (1, 1, 0)
    assert snap.home_context_family == "workplace"
    assert snap.schedule_step == 0 and snap.next_review_at == d(1)
    assert snap.last_outcome == "success" and snap.last_success_at == d(0)
    assert snap.native_attempts == 1


def test_a_hinted_success_counts_as_a_success_but_not_as_independent():
    t = tracker()
    t.on_attempt(rec("e1", 0, "hinted_success", hint_level=1))
    snap = t.snapshot()
    assert (snap.success_count, snap.independent_success_count) == (1, 0)


def test_a_partial_is_not_a_success_and_does_not_enter_the_schedule():
    t = tracker()
    t.on_attempt(rec("e1", 0, "partial", linguistic_level=2, functional_level=2))
    snap = t.snapshot()
    assert snap.success_count == 0 and snap.next_review_at is None and snap.last_outcome == "partial"


def test_a_spontaneous_success_counts_as_spontaneous():
    t = tracker()
    t.on_attempt(rec("e1", 0, origin="spontaneous"))
    assert t.snapshot().spontaneous_success_count == 1


def test_an_echo_changes_nothing_and_writes_no_rows():
    t = tracker()
    assert t.on_attempt(rec("e1", 0, echo=True)) == []
    snap = t.snapshot()
    assert snap.native_attempts == 0 and snap.success_count == 0 and snap.next_review_at is None and snap.last_outcome is None


def test_a_retracted_attempt_returns_flagged_rows_and_changes_nothing():
    t = tracker()
    rows = t.on_attempt(rec("e1", 0), retracted=True)
    assert rows and all(r.retracted for r in rows)
    assert t.snapshot().native_attempts == 0 and t.snapshot().success_count == 0
    assert all(v is None for v in t.dims().values())


def test_delayed_successes_are_counted_with_the_longest_gap():
    t = tracker()
    t.on_attempt(rec("e1", 0, context_family="workplace"))
    t.on_attempt(rec("e2", 1, due=True))
    t.on_attempt(rec("e3", 8, due=True))
    snap = t.snapshot()
    assert snap.delayed_success_count == 2
    assert snap.max_delayed_gap_days == pytest.approx(7.0)


def test_an_attempt_that_is_not_due_is_never_delayed():
    t = tracker()
    t.on_attempt(rec("e1", 0))
    rows = t.on_attempt(rec("e2", 5, due=False))
    assert not any(r.is_delayed for r in rows)
    assert t.snapshot().delayed_success_count == 0


def test_the_first_attempt_after_an_exposure_measures_from_the_exposure():
    t = tracker()
    t.on_exposure(d(0))
    rows = t.on_attempt(rec("e1", 2, due=True))
    assert all(r.is_delayed for r in rows if r.dimension in ("production", "independence"))
    assert rows[0].gap_days == pytest.approx(2.0)


def test_transfer_only_after_a_success_in_a_different_catalog_family():
    t = tracker()
    first = t.on_attempt(rec("e1", 0, context_family="workplace"))
    same = t.on_attempt(rec("e2", 1, context_family="workplace", due=True))
    other = t.on_attempt(rec("e3", 4, context_family="friendships", due=True))
    general = t.on_attempt(rec("e4", 11, context_family="general", due=True))
    assert "transfer" not in {r.dimension for r in first + same + general}
    assert "transfer" in {r.dimension for r in other}
    assert t.snapshot().families == ["friendships", "general", "workplace"]


def test_dimension_scores_come_from_the_non_retracted_rows_only():
    t = tracker()
    t.on_recognition(d(0), True)
    t.on_attempt(rec("e1", 1, context_family="workplace"))
    t.on_attempt(rec("e2", 2, context_family="workplace"), retracted=True)
    snap = t.snapshot()
    assert snap.dims["recognition"] == 100.0
    assert snap.dims["production"] == 92.5
    assert snap.dims["transfer"] is None and snap.mastery_index is None


def test_legacy_successes_are_unscheduled_evidence_and_the_first_one_enters_the_schedule():
    t = tracker()
    t.on_legacy(at=d(0), evidence="independent_production", origin="drill", hint_level=0, rows=(("production", 75.0, 1.0), ("independence", 75.0, 1.0)))
    snap = t.snapshot()
    assert snap.success_count == 1 and snap.independent_success_count == 1
    assert snap.next_review_at == d(1) and snap.schedule_step == 0
    assert snap.native_attempts == 0  # a legacy-only phrase keeps the v2 stage ladder (S0 4.3)
    assert snap.dims["production"] == 75.0


def test_a_cued_legacy_success_is_a_hinted_success():
    t = tracker()
    t.on_legacy(at=d(0), evidence="cued_production", origin="drill", hint_level=1, rows=(("production", 75.0, 0.8),))
    snap = t.snapshot()
    assert snap.success_count == 1 and snap.independent_success_count == 0 and snap.last_outcome == "hinted_success"


def test_legacy_recognition_feeds_the_recognition_dimension_only():
    t = tracker()
    t.on_legacy(at=d(0), evidence="recognition", origin="drill", hint_level=0, rows=(("recognition", 100.0, 1.0),))
    snap = t.snapshot()
    assert snap.success_count == 0 and snap.dims["recognition"] == 100.0 and snap.next_review_at is None
    assert t.stage(d(1)) == PhraseStage.RECOGNIZED


# ------------------------------------------------------------------------------ the Stable gate


def stable_snapshot(**over) -> PhraseSnapshot:
    base = dict(
        exposure_count=1,
        success_count=5,
        independent_success_count=3,
        spontaneous_success_count=1,
        delayed_success_count=2,
        max_delayed_gap_days=7.0,
        families=["friendships", "workplace"],
        last_outcome="success",
        last_success_at=d(10),
        unresolved_critical_errors=0,
    )
    base.update(over)
    return PhraseSnapshot(**base)


NOW = d(11)


def test_a_snapshot_that_meets_every_condition_is_stable():
    gate = stable_gate(stable_snapshot(), NOW)
    assert all(gate.values())
    assert derive_stage(stable_snapshot(), recognized=True, now=NOW) == PhraseStage.STABLE


@pytest.mark.parametrize(
    "key,over",
    [
        ("successes", dict(success_count=4)),
        ("independent", dict(independent_success_count=2)),
        ("families", dict(families=["workplace", "general"])),
        ("delayed", dict(delayed_success_count=1)),
        ("delayed", dict(max_delayed_gap_days=6.9)),
        ("no_critical_errors", dict(unresolved_critical_errors=1)),
        ("latest_not_failure", dict(last_outcome="failure")),
        ("recent_success", dict(last_success_at=d(11) - timedelta(days=91))),
        ("recent_success", dict(last_success_at=None)),
    ],
)
def test_each_missing_condition_blocks_stable_on_its_own(key, over):
    snap = stable_snapshot(**over)
    assert stable_gate(snap, NOW)[key] is False
    assert derive_stage(snap, recognized=True, now=NOW) != PhraseStage.STABLE


def test_the_general_family_never_counts_toward_the_two_family_condition():
    snap = stable_snapshot(families=["general", "workplace"])
    assert stable_gate(snap, NOW)["families"] is False


# ------------------------------------------------------------------------------ the ladder


def test_ladder_rungs():
    ladder = lambda recognized=False, **kw: derive_stage(PhraseSnapshot(**kw), recognized=recognized, now=NOW)
    assert ladder() == PhraseStage.UNSEEN
    assert ladder(exposure_count=1) == PhraseStage.SEEN
    assert ladder(exposure_count=1, recognized=True) == PhraseStage.RECOGNIZED
    assert ladder(success_count=1) == PhraseStage.PRODUCED_WITH_PROMPT
    assert ladder(success_count=2, independent_success_count=1) == PhraseStage.PRODUCED_INDEPENDENTLY
    assert ladder(success_count=2, independent_success_count=1, spontaneous_success_count=1) == PhraseStage.PRODUCED_SPONTANEOUSLY


def test_a_failure_as_the_latest_outcome_drops_one_rung_and_the_next_success_restores_it():
    snap = stable_snapshot()
    assert derive_stage(snap, recognized=True, now=NOW) == PhraseStage.STABLE
    snap.last_outcome = "failure"
    # the gate fails, so the evidence gives Produced-spontaneously; one rung below that:
    assert derive_stage(snap, recognized=True, now=NOW) == PhraseStage.PRODUCED_INDEPENDENTLY
    snap.last_outcome = "success"
    assert derive_stage(snap, recognized=True, now=NOW) == PhraseStage.STABLE


def test_one_rung_below_never_goes_below_seen():
    assert one_rung_below(PhraseStage.STABLE) == PhraseStage.PRODUCED_SPONTANEOUSLY
    assert one_rung_below(PhraseStage.PRODUCED_WITH_PROMPT) == PhraseStage.RECOGNIZED
    assert one_rung_below(PhraseStage.RECOGNIZED) == PhraseStage.SEEN
    assert one_rung_below(PhraseStage.SEEN) == PhraseStage.SEEN
    assert one_rung_below(PhraseStage.UNSEEN) == PhraseStage.UNSEEN


def test_stable_goes_stale_at_read_time_after_90_days_without_a_success():
    last = d(0)
    assert effective_stage("Stable", last, d(90)) == PhraseStage.STABLE
    assert effective_stage("Stable", last, d(90) + timedelta(seconds=1)) == PhraseStage.PRODUCED_INDEPENDENTLY
    assert effective_stage("Stable", None, d(1)) == PhraseStage.PRODUCED_INDEPENDENTLY
    assert effective_stage("Seen", None, d(500)) == PhraseStage.SEEN


def test_a_failed_attempt_through_the_tracker_lowers_the_stage():
    t = tracker()
    t.on_exposure(d(0))
    t.on_attempt(rec("e1", 0))
    assert t.stage(d(0)) == PhraseStage.PRODUCED_INDEPENDENTLY
    t.on_attempt(rec("e2", 1, "failure", due=True, linguistic_level=0, functional_level=0))
    assert t.stage(d(1)) == PhraseStage.PRODUCED_WITH_PROMPT  # one rung below Produced-independently
    t.on_attempt(rec("e3", 2, due=False))
    assert t.stage(d(2)) == PhraseStage.PRODUCED_INDEPENDENTLY
