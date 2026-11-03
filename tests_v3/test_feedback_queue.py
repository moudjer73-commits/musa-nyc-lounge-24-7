"""S3: feedback_queue.py (S0 5.8 deferred queue, 5.9 debrief). Pure functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feedback_queue import (
    DEBRIEF_TOP,
    DebriefCounts,
    QUEUE_LIMIT,
    QUEUE_WINDOW_DAYS,
    QueueItem,
    candidates,
    merge_targets,
    render_debrief,
    select_queue,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def item(dg: str, pattern: str = "grammar:x", score: float = 10.0, age_days: float = 1.0, **over) -> QueueItem:
    base = dict(
        diagnostic_id=dg, learner_id=1, pattern_id=pattern, priority_score=score, status="pending", delivery="deferred",
        created_at=NOW - timedelta(days=age_days), original_span="discuss about", alternative="discuss",
    )
    base.update(over)
    return QueueItem(**base)


def test_constants():
    assert (QUEUE_WINDOW_DAYS, QUEUE_LIMIT, DEBRIEF_TOP) == (21, 5, 3)


# ------------------------------------------------------------------------------ candidates


def test_only_pending_deferred_items_are_candidates():
    items = [
        item("a"),
        item("b", status="delivered"),
        item("c", status="merged"),
        item("d", status="retracted"),
        item("e", delivery="in_scene"),
    ]
    assert [i.diagnostic_id for i in candidates(items, NOW)] == ["a"]


def test_an_old_item_is_a_candidate_only_if_its_pattern_recurred_within_21_days():
    old = item("old", age_days=40)
    assert candidates([old], NOW) == []
    recurred = item("new", age_days=2, status="delivered")  # any non-retracted recent item of the pattern counts
    assert [i.diagnostic_id for i in candidates([old, recurred], NOW)] == ["old"]


def test_a_retracted_recurrence_does_not_revive_an_old_item():
    old = item("old", age_days=40)
    ghost = item("ghost", age_days=2, status="retracted")
    assert candidates([old, ghost], NOW) == []


def test_the_recurrence_must_be_the_same_learner_and_pattern():
    old = item("old", age_days=40)
    other_pattern = item("p2", pattern="grammar:y", age_days=1)
    other_learner = item("l2", learner_id=2, age_days=1)
    assert [i.diagnostic_id for i in candidates([old, other_pattern, other_learner], NOW)] == ["p2", "l2"]


def test_the_window_edge_is_inclusive():
    assert candidates([item("edge", age_days=21)], NOW)
    assert not candidates([item("past", age_days=21.001)], NOW)


# ------------------------------------------------------------------------------ select_queue


def test_items_of_a_pattern_collapse_into_one_entry_with_the_highest_score_as_representative():
    items = [item("lo", score=5, age_days=1), item("hi", score=20, age_days=3), item("mid", score=9, age_days=2)]
    entries = select_queue(items, NOW)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.representative.diagnostic_id == "hi"
    assert entry.seen_count == 3
    assert entry.member_ids == ("hi", "mid", "lo")


def test_a_score_tie_inside_a_pattern_goes_to_the_newest_item():
    entries = select_queue([item("older", score=7, age_days=5), item("newer", score=7, age_days=1)], NOW)
    assert entries[0].representative.diagnostic_id == "newer"


def test_entries_are_ordered_by_score_then_newest_first():
    items = [
        item("a", "p1", score=5, age_days=1),
        item("b", "p2", score=9, age_days=4),
        item("c", "p3", score=9, age_days=2),
    ]
    assert [e.representative.diagnostic_id for e in select_queue(items, NOW)] == ["c", "b", "a"]


def test_at_most_five_entries_by_default_and_the_limit_is_adjustable():
    items = [item(f"i{n}", f"p{n}", score=float(n)) for n in range(8)]
    assert len(select_queue(items, NOW)) == 5
    assert [e.representative.diagnostic_id for e in select_queue(items, NOW)][0] == "i7"
    assert len(select_queue(items, NOW, limit=2)) == 2
    assert select_queue(items, NOW, limit=0) == []


def test_two_learners_with_the_same_pattern_never_share_an_entry():
    entries = select_queue([item("a", learner_id=1), item("b", learner_id=2)], NOW)
    assert len(entries) == 2


def test_selection_is_deterministic_whatever_the_input_order():
    items = [item(f"i{n}", f"p{n % 3}", score=float(n % 4), age_days=float(n)) for n in range(9)]
    forward = select_queue(items, NOW)
    backward = select_queue(list(reversed(items)), NOW)
    assert forward == backward


# ------------------------------------------------------------------------------ merging


def test_delivering_the_representative_merges_the_pending_items_created_on_or_before_it():
    delivered = item("rep", age_days=2)
    items = [
        delivered,
        item("older", age_days=5),
        item("same_time", age_days=2),
        item("newer", age_days=1),  # created after the delivered item: stays pending
        item("done", age_days=6, status="delivered"),
        item("other", "grammar:y", age_days=5),
    ]
    assert sorted(merge_targets(delivered, items)) == ["older", "same_time"]


# ------------------------------------------------------------------------------ debrief


def test_the_debrief_is_templated_from_the_top_three_and_the_counts():
    entries = select_queue([item(f"i{n}", f"p{n}", score=float(10 - n)) for n in range(5)], NOW)
    text = render_debrief(entries, DebriefCounts(turns=12, cards_shown=3, targets_used=2))
    assert "Turns: 12" in text and "Cards shown: 3" in text and "Target phrases used: 2" in text
    assert text.count("→") == 3
    assert "4." not in text


def test_the_debrief_shows_seen_counts_only_when_more_than_once():
    entries = select_queue([item("a", "p1", score=9), item("b", "p1", score=8), item("c", "p2", score=7)], NOW)
    text = render_debrief(entries, DebriefCounts(1, 0, 0))
    assert "seen 2 times" in text
    assert text.count("seen") == 1


def test_the_debrief_escapes_html_and_handles_an_empty_queue():
    entry = select_queue([item("a", original_span="a <b> & c", alternative="x < y")], NOW)
    text = render_debrief(entry, DebriefCounts(1, 1, 1))
    assert "&lt;b&gt;" in text and "&amp;" in text and "<b> & c" not in text
    assert "Nothing waiting for review" in render_debrief([], DebriefCounts(0, 0, 0))


def test_the_debrief_makes_no_model_call_and_is_pure():
    entries = select_queue([item("a")], NOW)
    counts = DebriefCounts(2, 1, 1)
    assert render_debrief(entries, counts) == render_debrief(entries, counts)
