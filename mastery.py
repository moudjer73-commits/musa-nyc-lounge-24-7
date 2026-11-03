"""mastery.py (S0 section 5.5, owner Claude): evidence rows, dimension scores, stage derivation.

"LLMs propose, code decides": the judge returns ordinals 0 to 3, this module owns every score,
weight, threshold and stage. Pure code: nothing reads a clock or a database; ``PhraseTracker`` is
fed events in order (the projection rebuild does that) and exposes the columns of
``phrase_state_projection``.

Choices the contract leaves open (listed in S3_HANDOFF.md):

* ``success`` for the counters means ``success`` or ``hinted_success`` (S0: both count for
  Produced-with-prompt); ``independent`` means ``success`` with ``hint_level == 0``.
* ``is_delayed`` measures from the last success, else from the most recent exposure at or before the attempt.
* Legacy v1 successes (cued, independent, spontaneous production) are successes too and enter the
  schedule on their own timestamp (S0 5.6: "first success, from any origin").
* Stage staleness (Stable with no success for 90 days) is a read-time rule: ``effective_stage``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Collection, Iterable, Mapping, Optional, Sequence

import scheduler
from schemas import EvidenceDimension, PhraseStage, RetrievalOrigin, RetrievalOutcome

ORDINAL_SCORE: dict[int, float] = {0: 0.0, 1: 40.0, 2: 75.0, 3: 100.0}
Q_WEIGHT_LINGUISTIC = 0.70
Q_WEIGHT_FUNCTIONAL = 0.30

SUCCESS = RetrievalOutcome.SUCCESS.value
HINTED = RetrievalOutcome.HINTED_SUCCESS.value
PARTIAL = RetrievalOutcome.PARTIAL.value
FAILURE = RetrievalOutcome.FAILURE.value

OUTCOME_CAP: dict[str, float] = {SUCCESS: 100.0, HINTED: 100.0, PARTIAL: 50.0, FAILURE: 0.0}
INDEPENDENCE_CAP: dict[str, float] = {SUCCESS: 100.0, PARTIAL: 50.0, HINTED: 0.0, FAILURE: 0.0}

WEIGHT_BASE = 1.0
WEIGHT_HINTED = 0.8
WEIGHT_DELAYED = 1.5
WEIGHT_TRANSFER = 1.5
WEIGHT_PRAGMATIC_NEW_FAMILY = 1.5
GENERAL = "general"

DIMENSIONS = ("recognition", "production", "independence", "transfer", "pragmatic")
MASTERY_WEIGHTS: dict[str, float] = {
    "recognition": 0.10, "production": 0.30, "independence": 0.20, "transfer": 0.20, "pragmatic": 0.20,
}

STABLE_MIN_SUCCESSES = 5
STABLE_MIN_INDEPENDENT = 3
STABLE_MIN_FAMILIES = 2
STABLE_MIN_DELAYED = 2
STABLE_MIN_DELAYED_GAP_DAYS = 7.0
STABLE_RECENCY_DAYS = 90
DELAYED_MIN_GAP_DAYS = 1.0
RECOGNIZED_MIN_SCORE = 50.0

STAGE_ORDER: tuple[PhraseStage, ...] = (
    PhraseStage.UNSEEN,
    PhraseStage.SEEN,
    PhraseStage.RECOGNIZED,
    PhraseStage.PRODUCED_WITH_PROMPT,
    PhraseStage.PRODUCED_INDEPENDENTLY,
    PhraseStage.PRODUCED_SPONTANEOUSLY,
    PhraseStage.STABLE,
)


# ------------------------------------------------------------------------------ scores


def level_score(level: Optional[int]) -> float:
    """The judge's ordinal 0..3 as a 0..100 score. ``None`` (no judgement) counts as 0."""
    if level is None:
        return 0.0
    if level not in ORDINAL_SCORE:
        raise ValueError(f"level must be 0..3, got {level!r}")
    return ORDINAL_SCORE[int(level)]


def quality(linguistic_level: Optional[int], functional_level: Optional[int], outcome: str) -> float:
    """q = min(cap, round(0.70 L + 0.30 F, 2)); the cap comes from the outcome."""
    raw = round(Q_WEIGHT_LINGUISTIC * level_score(linguistic_level) + Q_WEIGHT_FUNCTIONAL * level_score(functional_level), 2)
    return min(OUTCOME_CAP[str(outcome)], raw)


def is_new_family(family: str, catalog_families: Collection[str], home_family: Optional[str]) -> bool:
    """The transfer condition: not general, in the catalog's families, and different from the home
    family (the family of the first successful production). No home yet means nothing to transfer from."""
    return bool(family) and family != GENERAL and family in catalog_families and home_family is not None and family != home_family


def evidence_rows(
    *,
    outcome: str,
    linguistic_level: Optional[int],
    functional_level: Optional[int],
    hint_level: int,
    is_delayed: bool,
    echo: bool,
    new_family: bool,
) -> list[tuple[str, float, float]]:
    """(dimension, score, weight) rows for one retrieval attempt. An echo attempt emits none."""
    if echo:
        return []
    outcome = str(outcome)
    q = quality(linguistic_level, functional_level, outcome)
    production_weight = WEIGHT_HINTED if hint_level > 0 else (WEIGHT_DELAYED if is_delayed else WEIGHT_BASE)
    rows: list[tuple[str, float, float]] = [
        ("production", q, production_weight),
        ("independence", min(INDEPENDENCE_CAP[outcome], q), WEIGHT_DELAYED if is_delayed else WEIGHT_BASE),
    ]
    if new_family:
        rows.append(("transfer", q, WEIGHT_TRANSFER))
    pragmatic = 0.0 if outcome == FAILURE else level_score(functional_level)
    rows.append(("pragmatic", pragmatic, WEIGHT_PRAGMATIC_NEW_FAMILY if new_family else WEIGHT_BASE))
    return rows


def recognition_row(correct: bool) -> tuple[str, float, float]:
    """Listen & Decode and v1 recognition evidence: 100 or 0, weight 1.0."""
    return ("recognition", 100.0 if correct else 0.0, WEIGHT_BASE)


def dimension_scores(rows: Iterable[tuple[str, float, float]]) -> dict[str, Optional[float]]:
    """S_d = sum(w x s) / sum(w) over the given (non-retracted) rows, None for a dimension with no rows."""
    num: dict[str, float] = {d: 0.0 for d in DIMENSIONS}
    den: dict[str, float] = {d: 0.0 for d in DIMENSIONS}
    for dimension, score, weight in rows:
        if dimension in num:
            num[dimension] += weight * score
            den[dimension] += weight
    return {d: (num[d] / den[d] if den[d] > 0 else None) for d in DIMENSIONS}


def mastery_index(dims: Mapping[str, Optional[float]]) -> Optional[float]:
    """M = 0.10 R + 0.30 P + 0.20 I + 0.20 T + 0.20 A, None if any dimension is None. Display only: never a gate."""
    if any(dims.get(d) is None for d in DIMENSIONS):
        return None
    return sum(MASTERY_WEIGHTS[d] * float(dims[d]) for d in DIMENSIONS)  # type: ignore[arg-type]


def delay_since(reference: Optional[datetime], now: datetime, due: bool) -> tuple[bool, Optional[float]]:
    """(is_delayed, gap_days): the attempt was due and at least 1 day passed since ``reference``
    (the last success, or the exposure if there is none)."""
    if reference is None:
        return False, None
    gap = (now - reference).total_seconds() / 86400.0
    return (bool(due) and gap >= DELAYED_MIN_GAP_DAYS), round(gap, 4)


# ------------------------------------------------------------------------------ tracker


@dataclass(frozen=True)
class EvidenceRow:
    """One ``phrase_evidence`` row (the projection's primary key is source_event_id + dimension)."""

    source_event_id: str
    dimension: str
    score: float
    weight: float
    context_family: str
    origin: str
    hint_level: int
    is_delayed: bool
    gap_days: Optional[float]
    echo: bool
    occurred_at: datetime
    retracted: bool = False


@dataclass(frozen=True)
class AttemptRecord:
    event_id: str
    occurred_at: datetime
    origin: str
    outcome: str
    hint_level: int = 0
    echo: bool = False
    context_family: str = GENERAL
    due: bool = False
    linguistic_level: Optional[int] = None
    functional_level: Optional[int] = None


@dataclass
class PhraseSnapshot:
    """The V3 columns of ``phrase_state_projection`` (stage excluded: it depends on ``now``)."""

    exposure_count: int = 0
    success_count: int = 0
    independent_success_count: int = 0
    spontaneous_success_count: int = 0
    delayed_success_count: int = 0
    max_delayed_gap_days: float = 0.0
    families: list[str] = field(default_factory=list)
    home_context_family: Optional[str] = None
    dims: dict[str, Optional[float]] = field(default_factory=dict)
    mastery_index: Optional[float] = None
    schedule_step: int = 0
    next_review_at: Optional[datetime] = None
    last_outcome: Optional[str] = None
    last_success_at: Optional[datetime] = None
    unresolved_critical_errors: int = 0
    native_attempts: int = 0


class PhraseTracker:
    """Replays one phrase's history for one learner, in event order."""

    def __init__(self, *, catalog_families: Collection[str] = (GENERAL,), unresolved_critical_errors: int = 0) -> None:
        self.catalog_families = set(catalog_families) or {GENERAL}
        self.unresolved_critical_errors = unresolved_critical_errors
        self.exposure_count = 0
        self.last_exposure_at: Optional[datetime] = None
        self.recognition_best: Optional[float] = None
        self.success_count = 0
        self.independent_success_count = 0
        self.spontaneous_success_count = 0
        self.delayed_success_count = 0
        self.max_delayed_gap_days = 0.0
        self.success_families: set[str] = set()
        self.home_family: Optional[str] = None
        self.last_success_at: Optional[datetime] = None
        self.last_outcome: Optional[str] = None
        self.schedule: Optional[scheduler.ScheduleState] = None
        self.native_attempts = 0
        self._rows: list[tuple[str, float, float]] = []

    # -- inputs ------------------------------------------------------------------------------

    def on_exposure(self, at: datetime) -> None:
        self.exposure_count += 1
        self.last_exposure_at = at

    def on_recognition(self, at: datetime, correct: bool) -> tuple[str, float, float]:
        row = recognition_row(correct)
        self._rows.append(row)
        self.recognition_best = max(self.recognition_best if self.recognition_best is not None else row[1], row[1])
        return row

    def on_legacy(
        self, *, at: datetime, evidence: str, origin: str, hint_level: int, rows: Sequence[tuple[str, float, float]]
    ) -> None:
        """A v1 ``phrase_production_assessed`` that the v1 ladder counted (non-retracted). Legacy
        successes are unscheduled evidence, but a first success still enters the schedule."""
        self._rows.extend(rows)
        if evidence == "recognition":
            self.recognition_best = max(self.recognition_best or 0.0, max((r[1] for r in rows if r[0] == "recognition"), default=0.0))
            return
        outcome = SUCCESS if hint_level == 0 else HINTED
        self._record_success(at, outcome, origin, hint_level, GENERAL, is_delayed=False, gap_days=None)
        self.last_outcome = outcome
        self.schedule = scheduler.advance(self.schedule, outcome, at, due=False)

    def on_attempt(self, rec: AttemptRecord, *, retracted: bool = False) -> list[EvidenceRow]:
        """One ``retrieval_attempted``. Returns the evidence rows to persist (flagged ``retracted`` when
        the event was retracted, in which case the tracker state does not change)."""
        reference = self.last_success_at or self.last_exposure_at
        is_delayed, gap_days = delay_since(reference, rec.occurred_at, rec.due)
        new_family = is_new_family(rec.context_family, self.catalog_families, self.home_family)
        triples = evidence_rows(
            outcome=rec.outcome,
            linguistic_level=rec.linguistic_level,
            functional_level=rec.functional_level,
            hint_level=rec.hint_level,
            is_delayed=is_delayed,
            echo=rec.echo,
            new_family=new_family,
        )
        rows = [
            EvidenceRow(
                rec.event_id, dim, score, weight, rec.context_family, rec.origin, rec.hint_level,
                is_delayed, gap_days, rec.echo, rec.occurred_at, retracted,
            )
            for dim, score, weight in triples
        ]
        if retracted or rec.echo:
            return rows
        self.native_attempts += 1
        self._rows.extend(triples)
        if rec.outcome in (SUCCESS, HINTED):
            self._record_success(rec.occurred_at, rec.outcome, rec.origin, rec.hint_level, rec.context_family, is_delayed, gap_days)
        self.last_outcome = rec.outcome
        self.schedule = scheduler.advance(self.schedule, rec.outcome, rec.occurred_at, due=rec.due)
        return rows

    def _record_success(
        self, at: datetime, outcome: str, origin: str, hint_level: int, family: str, is_delayed: bool, gap_days: Optional[float]
    ) -> None:
        self.success_count += 1
        if outcome == SUCCESS and hint_level == 0:
            self.independent_success_count += 1
        if outcome == SUCCESS and origin == RetrievalOrigin.SPONTANEOUS.value:
            self.spontaneous_success_count += 1
        if is_delayed:
            self.delayed_success_count += 1
            self.max_delayed_gap_days = max(self.max_delayed_gap_days, gap_days or 0.0)
        self.success_families.add(family or GENERAL)
        if self.home_family is None:
            self.home_family = family or GENERAL
        self.last_success_at = at

    # -- outputs -----------------------------------------------------------------------------

    def dims(self) -> dict[str, Optional[float]]:
        return dimension_scores(self._rows)

    def snapshot(self) -> PhraseSnapshot:
        dims = self.dims()
        return PhraseSnapshot(
            exposure_count=self.exposure_count,
            success_count=self.success_count,
            independent_success_count=self.independent_success_count,
            spontaneous_success_count=self.spontaneous_success_count,
            delayed_success_count=self.delayed_success_count,
            max_delayed_gap_days=round(self.max_delayed_gap_days, 4),
            families=sorted(self.success_families),
            home_context_family=self.home_family,
            dims=dims,
            mastery_index=mastery_index(dims),
            schedule_step=self.schedule.step if self.schedule else 0,
            next_review_at=self.schedule.next_review_at if self.schedule else None,
            last_outcome=self.last_outcome,
            last_success_at=self.last_success_at,
            unresolved_critical_errors=self.unresolved_critical_errors,
            native_attempts=self.native_attempts,
        )

    def stage(self, now: datetime) -> PhraseStage:
        return derive_stage(self.snapshot(), recognized=(self.recognition_best or 0.0) >= RECOGNIZED_MIN_SCORE, now=now)


# ------------------------------------------------------------------------------ stage


def stable_gate(s: PhraseSnapshot, now: datetime) -> dict[str, bool]:
    """Every condition of the Stable gate, so a test (or a progress screen) can say which one is missing."""
    non_general = [f for f in s.families if f != GENERAL]
    return {
        "successes": s.success_count >= STABLE_MIN_SUCCESSES,
        "independent": s.independent_success_count >= STABLE_MIN_INDEPENDENT,
        "families": len(non_general) >= STABLE_MIN_FAMILIES,
        "delayed": s.delayed_success_count >= STABLE_MIN_DELAYED and s.max_delayed_gap_days >= STABLE_MIN_DELAYED_GAP_DAYS,
        "no_critical_errors": s.unresolved_critical_errors == 0,
        "latest_not_failure": s.last_outcome != FAILURE,
        "recent_success": s.last_success_at is not None and (now - s.last_success_at).total_seconds() <= STABLE_RECENCY_DAYS * 86400.0,
    }


def _base_stage(s: PhraseSnapshot, recognized: bool, now: datetime) -> PhraseStage:
    if all(stable_gate(s, now).values()):
        return PhraseStage.STABLE
    if s.spontaneous_success_count >= 1:
        return PhraseStage.PRODUCED_SPONTANEOUSLY
    if s.independent_success_count >= 1:
        return PhraseStage.PRODUCED_INDEPENDENTLY
    if s.success_count >= 1:
        return PhraseStage.PRODUCED_WITH_PROMPT
    if recognized:
        return PhraseStage.RECOGNIZED
    if s.exposure_count >= 1:
        return PhraseStage.SEEN
    return PhraseStage.UNSEEN


def one_rung_below(stage: PhraseStage) -> PhraseStage:
    """The miss rule's step down: one rung, never below Seen (a phrase at Seen or lower stays put)."""
    index = STAGE_ORDER.index(stage)
    floor = STAGE_ORDER.index(PhraseStage.SEEN)
    return stage if index <= floor else STAGE_ORDER[index - 1]


def derive_stage(s: PhraseSnapshot, *, recognized: bool, now: datetime) -> PhraseStage:
    """Stage, first match wins (S0 5.5), then the miss rule: if the latest production outcome is a
    failure the stage is one rung below what all the evidence gives (floor Seen); the next success restores it."""
    base = _base_stage(s, recognized, now)
    if s.last_outcome == FAILURE:
        return one_rung_below(base)
    return base


def effective_stage(stage: str, last_success_at: Optional[datetime], now: datetime) -> PhraseStage:
    """Read-time staleness: Stable with no success for 90 days is Produced-independently."""
    current = PhraseStage(stage)
    if current == PhraseStage.STABLE and (
        last_success_at is None or (now - last_success_at).total_seconds() > STABLE_RECENCY_DAYS * 86400.0
    ):
        return PhraseStage.PRODUCED_INDEPENDENTLY
    return current
