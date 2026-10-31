"""The mastery ladder and the spaced-repetition schedule, computed from events.

Honest tracking rule: the database stores *events* (what happened, when, in which session
and register), never a mutable "level" or "score". The stage of an expression is a pure
function of its event history, so it can be recomputed, audited and changed without data loss.

Ladder
------
UNSEEN -> SEEN -> RECOGNIZED -> PRODUCED_PROMPTED -> PRODUCED_SPONTANEOUS -> STABLE

  SEEN                  any exposure (a card, a recast, a modelled line).
  RECOGNIZED            a correct recognition/comprehension event.
  PRODUCED_PROMPTED     a correct production inside a drill (Phrase Workout etc.), with at most
                        a light hint (hint_level < 3: a full reveal never counts).
  PRODUCED_SPONTANEOUS  the learner used it correctly and naturally (naturalness >= 2) in FREE
                        conversation, and it was not an echo of what the character or a card
                        had just said. Only free scenes can write `spontaneous` events.
  STABLE                spontaneous correct use in >= 3 different sessions, in >= 2 different
                        FORMALITY buckets (informal / neutral / formal, see registers.py; casual and
                        warm count as one), with the first and last qualifying events >= 7 days
                        apart. An expression that is only natural in one bucket needs just one.

Lapses and decay
----------------
  * If the most recent production attempt was a `miss`, the stage drops one rung.
  * STABLE decays to PRODUCED_SPONTANEOUS after 90 days without a qualifying event.

Schedule (spaced retrieval)
---------------------------
  never retrieved      due 1 day after the first exposure
  after a success      due in 3, then 7, then 21 days (by consecutive-success streak)
  after an attempt     due in 1 day (streak kept)           [outcome: target_attempt / equivalent]
  after a miss         due in 1 day (streak reset)
Retrieval events are `recognized`, `prompted`, `spontaneous`. `exposure` and `opportunity`
events never move the stage or the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence

from .registers import formality_of

DAY = 86400.0
STABLE_MIN_SESSIONS = 3
STABLE_MIN_FORMALITIES = 2
STABLE_MIN_SPAN_DAYS = 7
STABLE_DECAY_DAYS = 90
MAX_LIGHT_HINT_LEVEL = 2          # hint_level 3 = the answer was revealed
SUCCESS_INTERVAL_DAYS = (3, 7, 21)
FIRST_REVIEW_DAYS = 1
RETRY_DAYS = 1

RETRIEVAL_TYPES = ("recognized", "prompted", "spontaneous")


class Stage(IntEnum):
    UNSEEN = 0
    SEEN = 1
    RECOGNIZED = 2
    PRODUCED_PROMPTED = 3
    PRODUCED_SPONTANEOUS = 4
    STABLE = 5


@dataclass(frozen=True)
class Event:
    ts: float
    type: str                       # exposure|recognized|prompted|spontaneous|opportunity
    correct: bool | None = None
    session_id: int | None = None
    register: str | None = None
    naturalness: int | None = None
    hint_level: int | None = None
    outcome: str | None = None      # target_correct|target_attempt|equivalent|miss|None


def _is_good_spontaneous(e: Event) -> bool:
    return e.type == "spontaneous" and bool(e.correct) and (e.naturalness is None or e.naturalness >= 2)


def stable_criteria_met(spontaneous_events: Sequence[Event], *, min_formalities: int = STABLE_MIN_FORMALITIES) -> bool:
    if not spontaneous_events:
        return False
    evs = sorted(spontaneous_events, key=lambda e: e.ts)
    sessions = {e.session_id for e in evs if e.session_id is not None}
    formalities = {formality_of(e.register) for e in evs} - {None}
    span_days = (evs[-1].ts - evs[0].ts) / DAY
    return (
        len(sessions) >= STABLE_MIN_SESSIONS
        and len(formalities) >= min_formalities
        and span_days >= STABLE_MIN_SPAN_DAYS
    )


def compute_stage(events: Iterable[Event], now: float, *, min_formalities: int = STABLE_MIN_FORMALITIES) -> Stage:
    evs = sorted(events, key=lambda e: e.ts)
    if not evs:
        return Stage.UNSEEN

    stage = Stage.SEEN
    if any(e.type == "recognized" and e.correct for e in evs):
        stage = max(stage, Stage.RECOGNIZED)
    if any(
        e.type == "prompted" and e.correct and (e.hint_level is None or e.hint_level <= MAX_LIGHT_HINT_LEVEL)
        for e in evs
    ):
        stage = max(stage, Stage.PRODUCED_PROMPTED)

    spontaneous = [e for e in evs if _is_good_spontaneous(e)]
    if spontaneous:
        stage = max(stage, Stage.PRODUCED_SPONTANEOUS)
        if stable_criteria_met(spontaneous, min_formalities=min_formalities):
            decayed = (now - spontaneous[-1].ts) > STABLE_DECAY_DAYS * DAY
            stage = max(stage, Stage.PRODUCED_SPONTANEOUS if decayed else Stage.STABLE)

    attempts = [e for e in evs if e.type in ("prompted", "spontaneous")]
    if attempts and attempts[-1].outcome == "miss":
        stage = Stage(max(int(stage) - 1, int(Stage.SEEN)))
    return stage


def retrieval_streak(events: Iterable[Event]) -> int:
    """Consecutive successful retrievals counted back from the latest; a miss resets, an attempt keeps."""
    streak = 0
    for e in sorted(events, key=lambda e: e.ts):
        if e.type not in RETRIEVAL_TYPES:
            continue
        if e.correct:
            streak += 1
        elif e.outcome == "miss":
            streak = 0
    return streak


def next_due(events: Iterable[Event]) -> float | None:
    """Unix time when the expression should next be retrieved, or None if it was never seen."""
    evs = sorted(events, key=lambda e: e.ts)
    if not evs:
        return None
    retrievals = [e for e in evs if e.type in RETRIEVAL_TYPES]
    if not retrievals:
        first_exposure = next((e.ts for e in evs if e.type == "exposure"), evs[0].ts)
        return first_exposure + FIRST_REVIEW_DAYS * DAY
    last = retrievals[-1]
    if not last.correct:
        return last.ts + RETRY_DAYS * DAY
    streak = retrieval_streak(evs)
    interval = SUCCESS_INTERVAL_DAYS[min(streak, len(SUCCESS_INTERVAL_DAYS)) - 1]
    return last.ts + interval * DAY


def is_due(events: Iterable[Event], now: float) -> bool:
    due = next_due(events)
    return due is not None and due <= now


# --------------------------------------------------------------------------------------
# How the other modes write events (docs/table in prompts/mode_phrase_workout.md)
# --------------------------------------------------------------------------------------


def workout_event_fields(outcome: str, *, naturalness: int | None, hint_level: int | None) -> dict:
    """Map a Phrase Workout evaluation to the event row it must produce.

    A drill can only ever write `prompted` events: it never counts toward spontaneous or Stable.
    A full reveal (hint_level 3) is recorded as an exposure, not as production.
    """
    if hint_level is not None and hint_level >= 3:
        return {"type": "exposure", "correct": None, "outcome": None, "naturalness": None}
    return {
        "type": "prompted",
        "correct": outcome == "target_correct",
        "outcome": outcome,
        "naturalness": naturalness,
    }
