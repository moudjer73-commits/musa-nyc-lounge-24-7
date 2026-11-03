"""feedback_queue.py (S0 sections 5.8 and 5.9, owner Claude): the deferred feedback queue and the scene debrief.

Pure functions over rows of ``feedback_item_projection``; the database layer (``database.py``) loads the
rows and calls them. No clock reads (callers pass ``now``), no LLM: the debrief is a template and every
number in it comes from a projection.

Queue selection (5.8):

* Candidates: ``status == pending`` and ``delivery == deferred``, created within 21 days. An older item is
  a candidate only if the same pattern recurred within 21 days.
* Group by ``pattern_id``. The representative is the highest ``priority_score`` (ties: newest). The other
  items stay pending and are counted ("seen N times").
* Order by representative score, newest first on ties; at most 5 per Review session or debrief.
* Delivering the representative merges the pending items of the same pattern created on or before it
  (``merge_targets``; the projection rebuild applies the same rule when it replays the card event).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

QUEUE_WINDOW_DAYS = 21
QUEUE_LIMIT = 5
DEBRIEF_TOP = 3


@dataclass(frozen=True)
class QueueItem:
    diagnostic_id: str
    learner_id: int
    pattern_id: str
    priority_score: float
    status: str
    delivery: str
    created_at: datetime
    original_span: str = ""
    alternative: str = ""
    explanation_en: Optional[str] = None
    explanation_ar: Optional[str] = None


@dataclass(frozen=True)
class QueueEntry:
    representative: QueueItem
    seen_count: int  # pending items of the pattern, the representative included
    member_ids: tuple[str, ...]  # every pending item of the group, representative first


def _recent(item: QueueItem, now: datetime) -> bool:
    return now - item.created_at <= timedelta(days=QUEUE_WINDOW_DAYS)


def candidates(items: Sequence[QueueItem], now: datetime) -> list[QueueItem]:
    """Pending, deferred, recent; or older but with the same pattern seen again in the window.
    ``items`` should hold every item of the learner so a recurrence is visible whatever its status."""
    recent_patterns = {(i.learner_id, i.pattern_id) for i in items if i.status != "retracted" and _recent(i, now)}
    return [
        i
        for i in items
        if i.status == "pending"
        and i.delivery == "deferred"
        and (_recent(i, now) or (i.learner_id, i.pattern_id) in recent_patterns)
    ]


def select_queue(items: Sequence[QueueItem], now: datetime, limit: int = QUEUE_LIMIT) -> list[QueueEntry]:
    groups: dict[tuple[int, str], list[QueueItem]] = {}
    for item in candidates(items, now):
        groups.setdefault((item.learner_id, item.pattern_id), []).append(item)
    entries: list[QueueEntry] = []
    for members in groups.values():
        members.sort(key=lambda i: (-i.priority_score, -i.created_at.timestamp(), i.diagnostic_id))
        entries.append(QueueEntry(members[0], len(members), tuple(m.diagnostic_id for m in members)))
    entries.sort(
        key=lambda e: (-e.representative.priority_score, -e.representative.created_at.timestamp(), e.representative.diagnostic_id)
    )
    return entries[: max(int(limit), 0)]


def merge_targets(delivered: QueueItem, items: Iterable[QueueItem]) -> list[str]:
    """Ids of the pending items of the same learner and pattern created on or before ``delivered``."""
    return [
        i.diagnostic_id
        for i in items
        if i.diagnostic_id != delivered.diagnostic_id
        and i.learner_id == delivered.learner_id
        and i.pattern_id == delivered.pattern_id
        and i.status == "pending"
        and i.created_at <= delivered.created_at
    ]


# ------------------------------------------------------------------------------ 5.9 debrief


@dataclass(frozen=True)
class DebriefCounts:
    turns: int
    cards_shown: int
    targets_used: int


def render_debrief(entries: Sequence[QueueEntry], counts: DebriefCounts) -> str:
    """Telegram HTML, templated: the top 3 entries plus counts. No generated prose."""
    lines = [
        "<b>Scene debrief</b>",
        f"Turns: {int(counts.turns)} · Cards shown: {int(counts.cards_shown)} · Target phrases used: {int(counts.targets_used)}",
        "",
    ]
    top = list(entries)[:DEBRIEF_TOP]
    if not top:
        lines.append("Nothing waiting for review from this scene.")
        return "\n".join(lines)
    lines.append("Worth another look:")
    for number, entry in enumerate(top, start=1):
        item = entry.representative
        line = f"{number}. <i>{html.escape(item.original_span)}</i> → <b>{html.escape(item.alternative)}</b>"
        if entry.seen_count > 1:
            line += f" (seen {entry.seen_count} times)"
        lines.append(line)
    return "\n".join(lines)
