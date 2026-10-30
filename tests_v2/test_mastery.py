from __future__ import annotations

from coach.mastery import (
    DAY, Event, Stage, compute_stage, is_due, next_due, retrieval_streak, stable_criteria_met, workout_event_fields,
)

T0 = 1_800_000_000.0


def ev(day: float, type_: str, correct=None, **kw) -> Event:
    return Event(ts=T0 + day * DAY, type=type_, correct=correct, **kw)


def spont(day, session, register, **kw) -> Event:
    return ev(day, "spontaneous", True, session_id=session, register=register, naturalness=3, **kw)


def test_empty_history_is_unseen():
    assert compute_stage([], T0) == Stage.UNSEEN
    assert next_due([]) is None


def test_the_ladder_climbs_one_rung_at_a_time():
    events = [ev(0, "exposure")]
    assert compute_stage(events, T0 + DAY) == Stage.SEEN
    events.append(ev(1, "recognized", True))
    assert compute_stage(events, T0 + 2 * DAY) == Stage.RECOGNIZED
    events.append(ev(2, "prompted", True, outcome="target_correct", hint_level=0))
    assert compute_stage(events, T0 + 3 * DAY) == Stage.PRODUCED_PROMPTED
    events.append(spont(3, 1, "casual"))
    assert compute_stage(events, T0 + 4 * DAY) == Stage.PRODUCED_SPONTANEOUS


def test_a_full_reveal_never_counts_as_production():
    events = [ev(0, "exposure"), ev(1, "prompted", True, hint_level=3)]
    assert compute_stage(events, T0 + 2 * DAY) == Stage.SEEN


def test_stilted_spontaneous_use_does_not_count():
    events = [ev(0, "exposure"), ev(1, "spontaneous", True, session_id=1, register="casual", naturalness=1)]
    assert compute_stage(events, T0 + 2 * DAY) == Stage.SEEN


def test_incorrect_attempts_do_not_climb():
    events = [ev(0, "exposure"), ev(1, "prompted", False, outcome="target_attempt")]
    assert compute_stage(events, T0 + 2 * DAY) == Stage.SEEN


def test_stable_needs_three_sessions_two_registers_and_a_week():
    good = [spont(0, 1, "casual"), spont(3, 2, "diplomatic"), spont(8, 3, "casual")]
    assert stable_criteria_met(good)
    assert compute_stage(good, T0 + 9 * DAY) == Stage.STABLE

    assert compute_stage([spont(0, 1, "casual"), spont(3, 2, "diplomatic"), spont(6, 3, "casual")], T0 + 7 * DAY) == Stage.PRODUCED_SPONTANEOUS  # 6 days
    assert compute_stage([spont(0, 1, "casual"), spont(3, 2, "casual"), spont(8, 3, "casual")], T0 + 9 * DAY) == Stage.PRODUCED_SPONTANEOUS      # 1 register
    assert compute_stage([spont(0, 1, "casual"), spont(3, 1, "diplomatic"), spont(8, 2, "casual")], T0 + 9 * DAY) == Stage.PRODUCED_SPONTANEOUS  # 2 sessions
    # three uses in one conversation are one data point, not three
    assert compute_stage([spont(0, 1, "casual"), spont(0.1, 1, "diplomatic"), spont(8, 1, "casual")], T0 + 9 * DAY) == Stage.PRODUCED_SPONTANEOUS


def test_stable_decays_without_use():
    good = [spont(0, 1, "casual"), spont(3, 2, "diplomatic"), spont(8, 3, "casual")]
    assert compute_stage(good, T0 + 8 * DAY + 89 * DAY) == Stage.STABLE
    assert compute_stage(good, T0 + 8 * DAY + 91 * DAY) == Stage.PRODUCED_SPONTANEOUS


def test_a_miss_after_progress_costs_one_rung():
    base = [ev(0, "exposure"), ev(1, "prompted", True, hint_level=0), spont(2, 1, "casual")]
    assert compute_stage(base, T0 + 3 * DAY) == Stage.PRODUCED_SPONTANEOUS
    with_miss = base + [ev(5, "prompted", False, outcome="miss")]
    assert compute_stage(with_miss, T0 + 6 * DAY) == Stage.PRODUCED_PROMPTED
    recovered = with_miss + [ev(6, "prompted", True, outcome="target_correct", hint_level=0)]
    assert compute_stage(recovered, T0 + 7 * DAY) == Stage.PRODUCED_SPONTANEOUS


def test_a_miss_cannot_push_below_seen():
    assert compute_stage([ev(0, "exposure"), ev(1, "prompted", False, outcome="miss")], T0 + 2 * DAY) == Stage.SEEN


def test_opportunities_never_move_the_stage():
    assert compute_stage([ev(0, "exposure"), ev(1, "opportunity"), ev(2, "opportunity")], T0 + 3 * DAY) == Stage.SEEN


# ------------------------------------------------------------------ schedule


def test_first_review_is_a_day_after_first_exposure():
    assert next_due([ev(0, "exposure")]) == T0 + DAY


def test_intervals_after_successes_are_3_7_21_days():
    events = [ev(0, "exposure")]
    t = 1.0
    expected = [3, 7, 21, 21]
    for gap in expected:
        events.append(ev(t, "prompted", True, outcome="target_correct", hint_level=0))
        assert next_due(events) == T0 + (t + gap) * DAY
        t += gap


def test_attempt_repeats_tomorrow_keeping_the_streak_a_miss_resets_it():
    events = [ev(0, "exposure"), ev(1, "prompted", True), ev(4, "prompted", True)]
    assert retrieval_streak(events) == 2
    attempt = events + [ev(11, "prompted", False, outcome="target_attempt")]
    assert next_due(attempt) == T0 + 12 * DAY and retrieval_streak(attempt) == 2
    miss = events + [ev(11, "prompted", False, outcome="miss")]
    assert next_due(miss) == T0 + 12 * DAY and retrieval_streak(miss) == 0
    again = miss + [ev(12, "prompted", True)]
    assert next_due(again) == T0 + 15 * DAY       # back to the 3-day rung


def test_is_due():
    events = [ev(0, "exposure")]
    assert not is_due(events, T0 + 0.5 * DAY)
    assert is_due(events, T0 + 1 * DAY)


# ------------------------------------------------------------------ drills write prompted events only


def test_workout_outcomes_map_to_prompted_events_only():
    ok = workout_event_fields("target_correct", naturalness=3, hint_level=0)
    assert ok == {"type": "prompted", "correct": True, "outcome": "target_correct", "naturalness": 3}
    att = workout_event_fields("target_attempt", naturalness=2, hint_level=1)
    assert att["type"] == "prompted" and att["correct"] is False
    miss = workout_event_fields("miss", naturalness=None, hint_level=None)
    assert miss["type"] == "prompted" and miss["outcome"] == "miss"
    reveal = workout_event_fields("target_correct", naturalness=3, hint_level=3)
    assert reveal["type"] == "exposure"
    for o in ("target_correct", "target_attempt", "equivalent", "miss"):
        assert workout_event_fields(o, naturalness=2, hint_level=0)["type"] != "spontaneous"
