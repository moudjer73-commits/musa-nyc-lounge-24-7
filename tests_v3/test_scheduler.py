"""S3: scheduler.py (S0 5.6). Pure functions, no database."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scheduler
from scheduler import (
    DUE_LIMIT_PER_SESSION,
    INTERVALS_DAYS,
    MAX_STEP,
    ReviewState,
    ScheduleState,
    advance,
    days_overdue,
    due_at,
    eligible_for_spontaneous,
    interval_days,
    is_due,
    pick_family,
    pick_prompt_kind,
    plan_review,
    select_due,
)
from schemas import PromptKind

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def days(n: float) -> timedelta:
    return timedelta(days=n)


def test_constants_match_the_contract():
    assert INTERVALS_DAYS == (1, 3, 7, 14, 30, 60)
    assert MAX_STEP == 5
    assert DUE_LIMIT_PER_SESSION == 5


@pytest.mark.parametrize("step,expected", [(0, 1), (1, 3), (2, 7), (3, 14), (4, 30), (5, 60), (9, 60), (-3, 1)])
def test_interval_days_clamps(step, expected):
    assert interval_days(step) == expected


def test_due_at_adds_the_interval_of_the_step():
    assert due_at(NOW, 2) == NOW + days(7)


def test_schedule_state_rejects_a_step_outside_0_to_5():
    with pytest.raises(ValueError):
        ScheduleState(6, NOW)
    with pytest.raises(ValueError):
        ScheduleState(-1, NOW)


# ------------------------------------------------------------------------------ entering the schedule


@pytest.mark.parametrize("outcome", ["success", "hinted_success"])
def test_a_first_success_from_any_origin_enters_at_step_0_due_in_one_day(outcome):
    for due_flag in (False, True):
        state = advance(None, outcome, NOW, due=due_flag)
        assert state == ScheduleState(0, NOW + days(1))


@pytest.mark.parametrize("outcome", ["partial", "failure"])
def test_a_first_partial_or_failure_does_not_enter_the_schedule(outcome):
    assert advance(None, outcome, NOW, due=False) is None


# ------------------------------------------------------------------------------ moving along it


def test_an_unscheduled_attempt_changes_nothing_whatever_the_outcome():
    state = ScheduleState(2, NOW + days(3))
    for outcome in ("success", "partial", "hinted_success", "failure"):
        assert advance(state, outcome, NOW, due=False) is state


def test_a_due_success_moves_up_one_step_and_the_next_review_counts_from_the_attempt():
    state = ScheduleState(1, NOW - days(1))
    after = advance(state, "success", NOW, due=True)
    assert after == ScheduleState(2, NOW + days(7))


def test_the_step_never_passes_5():
    state = ScheduleState(5, NOW - days(1))
    assert advance(state, "success", NOW, due=True) == ScheduleState(5, NOW + days(60))


@pytest.mark.parametrize("outcome", ["partial", "hinted_success"])
def test_a_due_partial_or_hinted_success_keeps_the_step_but_restarts_its_interval(outcome):
    state = ScheduleState(3, NOW - days(2))
    assert advance(state, outcome, NOW, due=True) == ScheduleState(3, NOW + days(14))


@pytest.mark.parametrize("step,expected", [(5, 3), (4, 2), (3, 1), (2, 0), (1, 0), (0, 0)])
def test_a_due_failure_drops_two_steps_with_a_floor_of_zero(step, expected):
    state = ScheduleState(step, NOW - days(1))
    after = advance(state, "failure", NOW, due=True)
    assert after.step == expected
    assert after.next_review_at == NOW + days(INTERVALS_DAYS[expected])


def test_lateness_is_never_penalised():
    state = ScheduleState(1, NOW - days(40))  # forty days late
    after = advance(state, "success", NOW, due=True)
    assert after == ScheduleState(2, NOW + days(7))


def test_an_unknown_outcome_is_an_error_not_a_silent_no_op():
    with pytest.raises(ValueError):
        advance(ScheduleState(0, NOW), "banana", NOW, due=True)


def test_walking_the_whole_ladder_of_successes_uses_each_interval_once():
    state = advance(None, "success", NOW, due=False)
    clock = NOW
    gaps = []
    for _ in range(6):
        gaps.append((state.next_review_at - clock).days)
        clock = state.next_review_at
        state = advance(state, "success", clock, due=True)
    assert gaps == [1, 3, 7, 14, 30, 60]
    assert state.step == 5


# ------------------------------------------------------------------------------ is_due


def test_is_due_is_inclusive_and_false_without_a_schedule():
    state = ScheduleState(0, NOW)
    assert is_due(state, NOW)
    assert not is_due(state, NOW - timedelta(seconds=1))
    assert not is_due(None, NOW)


# ------------------------------------------------------------------------------ selection


def rs(phrase_id: str, step: int, due_days_ago: float, **kw) -> ReviewState:
    return ReviewState(phrase_id, step, NOW - days(due_days_ago), **kw)


def test_select_due_keeps_only_due_phrases_most_overdue_first():
    states = [rs("c", 1, 1), rs("a", 1, 5), rs("b", 1, 3), rs("future", 1, -2)]
    assert [s.phrase_id for s in select_due(states, NOW)] == ["a", "b", "c"]


def test_ties_on_lateness_go_to_the_lower_step_then_the_phrase_id():
    states = [rs("z", 3, 2), rs("b", 1, 2), rs("a", 1, 2)]
    assert [s.phrase_id for s in select_due(states, NOW)] == ["a", "b", "z"]


def test_at_most_five_per_session_and_the_limit_is_adjustable():
    states = [rs(f"p{i}", 0, i + 1) for i in range(9)]
    assert len(select_due(states, NOW)) == 5
    assert [s.phrase_id for s in select_due(states, NOW)][:2] == ["p8", "p7"]
    assert len(select_due(states, NOW, limit=2)) == 2
    assert select_due(states, NOW, limit=0) == []


def test_days_overdue_is_fractional_and_signed():
    assert days_overdue(NOW - timedelta(hours=12), NOW) == pytest.approx(0.5)
    assert days_overdue(NOW + days(1), NOW) == pytest.approx(-1.0)


# ------------------------------------------------------------------------------ rotation


def test_pick_family_walks_the_catalog_order_and_wraps():
    families = ("workplace", "friendships", "family")
    assert pick_family(families, None) == "workplace"
    assert pick_family(families, "workplace") == "friendships"
    assert pick_family(families, "family") == "workplace"


def test_pick_family_starts_over_when_the_last_family_is_no_longer_in_the_catalog():
    assert pick_family(("workplace", "friendships"), "academic") == "workplace"


def test_pick_family_with_nothing_falls_back_to_general():
    assert pick_family((), None) == "general"
    assert pick_family(("general",), "general") == "general"


def test_consecutive_reviews_of_a_multi_family_phrase_use_different_families():
    families = ("workplace", "friendships")
    first = pick_family(families, None)
    second = pick_family(families, first)
    assert first != second


def test_prompt_kind_rotates_meaning_situation_function():
    seen = []
    last = None
    for _ in range(6):
        kind = pick_prompt_kind(last)
        seen.append(kind.value)
        last = kind.value
    assert seen == ["meaning", "situation", "function", "meaning", "situation", "function"]


def test_spontaneous_never_appears_in_review_and_restarts_the_rotation():
    assert pick_prompt_kind("spontaneous") == PromptKind.MEANING
    assert pick_prompt_kind(None) == PromptKind.MEANING
    assert PromptKind.SPONTANEOUS not in scheduler.REVIEW_PROMPT_KINDS


def test_plan_review_combines_selection_and_rotation():
    states = [
        rs("a", 2, 4, catalog_families=("workplace", "friendships"), last_family="workplace", last_prompt_kind="meaning"),
        rs("b", 0, 1),
    ]
    plans = plan_review(states, NOW)
    assert [p.phrase_id for p in plans] == ["a", "b"]
    assert plans[0].family == "friendships"
    assert plans[0].prompt_kind == PromptKind.SITUATION
    assert plans[0].days_overdue == pytest.approx(4.0)
    assert plans[1].family == "general"
    assert plans[1].prompt_kind == PromptKind.MEANING


# ------------------------------------------------------------------------------ spontaneous eligibility


def test_spontaneous_targets_must_be_scheduled_and_either_due_or_at_step_1_plus():
    assert not eligible_for_spontaneous(None, NOW)
    assert not eligible_for_spontaneous(ScheduleState(0, NOW + days(1)), NOW)
    assert eligible_for_spontaneous(ScheduleState(0, NOW - days(1)), NOW)
    assert eligible_for_spontaneous(ScheduleState(1, NOW + days(2)), NOW)


# ------------------------------------------------------------------------------ purity


@pytest.mark.parametrize("module", ["scheduler", "mastery", "retrieval", "feedback_queue", "catalog"])
def test_s3_modules_never_read_the_wall_clock(module):
    source = (Path(__file__).resolve().parents[1] / f"{module}.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"datetime\.(now|utcnow|today)\s*\(|time\.time\s*\(|SystemClock", code), module
