"""scheduler.py (S0 section 5.6, owner Claude): spaced retrieval, pure functions only.

``INTERVALS_DAYS = (1, 3, 7, 14, 30, 60)``; ``schedule_step`` is the index used for the NEXT review.

* A phrase enters the schedule on its first success from any origin: step 0, due in 1 day.
* A scheduled attempt is one made at or after ``next_review_at`` (the event's ``due`` flag). Only
  scheduled attempts move the step:
  success: ``step = min(step + 1, 5)``; partial or hinted_success: unchanged; failure: ``max(step - 2, 0)``.
  In every case ``next_review_at = now + INTERVALS[step]`` with the new step.
* Unscheduled attempts (practice before due, spontaneous scene use, reconstruction) change nothing.
* Lateness is never penalised: the interval is counted from the attempt, however late it was.
* Echo attempts and attempts without an outcome never reach this module.
* Due selection: ``next_review_at <= now``, most overdue first, ties by the lower step, at most 5 per session.
* Context rotation walks the catalog families in order, one step past the family used last; the prompt
  kind rotates meaning, situation, function. ``spontaneous`` never appears in Review.

No clock reads here: every function takes ``now``. Python 3.11 compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from schemas import PromptKind, RetrievalOutcome

INTERVALS_DAYS: tuple[int, ...] = (1, 3, 7, 14, 30, 60)
MAX_STEP = len(INTERVALS_DAYS) - 1
DUE_LIMIT_PER_SESSION = 5
REVIEW_PROMPT_KINDS: tuple[PromptKind, ...] = (PromptKind.MEANING, PromptKind.SITUATION, PromptKind.FUNCTION)
GENERAL_FAMILY = "general"

_SUCCESSES = (RetrievalOutcome.SUCCESS.value, RetrievalOutcome.HINTED_SUCCESS.value)


@dataclass(frozen=True)
class ScheduleState:
    step: int  # 0..5, the interval index used for the next review
    next_review_at: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.step <= MAX_STEP:
            raise ValueError(f"schedule step must be 0..{MAX_STEP}, got {self.step}")


def interval_days(step: int) -> int:
    return INTERVALS_DAYS[min(max(int(step), 0), MAX_STEP)]


def due_at(now: datetime, step: int) -> datetime:
    return now + timedelta(days=interval_days(step))


def is_due(state: Optional[ScheduleState], now: datetime) -> bool:
    """Whether an attempt made at ``now`` is a scheduled one."""
    return state is not None and now >= state.next_review_at


def advance(
    state: Optional[ScheduleState], outcome: str, now: datetime, *, due: bool
) -> Optional[ScheduleState]:
    """The schedule after one non-echo attempt. ``due`` is the attempt's own flag (made at or after
    ``next_review_at``). Returns ``state`` itself when nothing changes."""
    outcome = str(outcome)
    if state is None:
        if outcome in _SUCCESSES:  # first success from any origin
            return ScheduleState(0, due_at(now, 0))
        return None
    if not due:
        return state  # unscheduled attempt: evidence only
    if outcome == RetrievalOutcome.SUCCESS.value:
        step = min(state.step + 1, MAX_STEP)
    elif outcome in (RetrievalOutcome.PARTIAL.value, RetrievalOutcome.HINTED_SUCCESS.value):
        step = state.step
    elif outcome == RetrievalOutcome.FAILURE.value:
        step = max(state.step - 2, 0)
    else:
        raise ValueError(f"unknown outcome {outcome!r}")
    return ScheduleState(step, due_at(now, step))


@dataclass(frozen=True)
class ReviewState:
    """What due selection and rotation need to know about one scheduled phrase."""

    phrase_id: str
    step: int
    next_review_at: datetime
    catalog_families: tuple[str, ...] = (GENERAL_FAMILY,)
    last_family: Optional[str] = None
    last_prompt_kind: Optional[str] = None


def days_overdue(next_review_at: datetime, now: datetime) -> float:
    return (now - next_review_at).total_seconds() / 86400.0


def select_due(states: Sequence[ReviewState], now: datetime, limit: int = DUE_LIMIT_PER_SESSION) -> list[ReviewState]:
    """Due phrases, most overdue first, ties by the lower step, then by phrase id (deterministic)."""
    due = [s for s in states if s.next_review_at <= now]
    due.sort(key=lambda s: (-days_overdue(s.next_review_at, now), s.step, s.phrase_id))
    return due[: max(int(limit), 0)]


def pick_family(catalog_families: Sequence[str], last_family: Optional[str]) -> str:
    """The catalog family after ``last_family`` (cyclic, catalog order), so consecutive reviews differ
    whenever the phrase has more than one family."""
    families = [f for f in catalog_families if f] or [GENERAL_FAMILY]
    if last_family in families:
        return families[(families.index(last_family) + 1) % len(families)]
    return families[0]


def pick_prompt_kind(last_kind: Optional[str]) -> PromptKind:
    """meaning -> situation -> function -> meaning. ``spontaneous`` (or nothing) restarts at meaning."""
    kinds = [k.value for k in REVIEW_PROMPT_KINDS]
    if last_kind in kinds:
        return REVIEW_PROMPT_KINDS[(kinds.index(last_kind) + 1) % len(kinds)]
    return REVIEW_PROMPT_KINDS[0]


@dataclass(frozen=True)
class ReviewPlan:
    phrase_id: str
    step: int
    family: str
    prompt_kind: PromptKind
    days_overdue: float


def plan_review(states: Sequence[ReviewState], now: datetime, limit: int = DUE_LIMIT_PER_SESSION) -> list[ReviewPlan]:
    return [
        ReviewPlan(
            s.phrase_id,
            s.step,
            pick_family(s.catalog_families, s.last_family),
            pick_prompt_kind(s.last_prompt_kind),
            round(days_overdue(s.next_review_at, now), 4),
        )
        for s in select_due(states, now, limit)
    ]


def eligible_for_spontaneous(state: Optional[ScheduleState], now: datetime) -> bool:
    """Scene stealth targets are restricted to phrases that are due or at step 1 or higher. An unused
    opportunity writes nothing: absence is not failure."""
    return state is not None and (state.next_review_at <= now or state.step >= 1)
