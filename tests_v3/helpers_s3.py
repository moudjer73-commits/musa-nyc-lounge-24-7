"""Builders shared by the S3 tests: a tiny catalog seed, retrieval_attempted events, a day-based timeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from catalog import CatalogSeed, Competency, Expression, Subskill
from clock import parse_iso, to_iso
from database import EventSourcedDatabase
from schemas import (
    ContextFamily,
    MatchedBy,
    Ordinal,
    PromptKind,
    RetrievalAttemptedV2,
    RetrievalOrigin,
    RetrievalOutcome,
)

LEARNER = 1001
D0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def at(days: float) -> str:
    """ISO timestamp ``days`` after the timeline's origin (2026-10-01T09:00Z)."""
    return to_iso(D0 + timedelta(days=days))


def when(days: float) -> datetime:
    return D0 + timedelta(days=days)


def expression(
    phrase_id: str,
    canonical_form: str = "come to think of it",
    *,
    families: tuple[str, ...] = ("workplace", "friendships", "general"),
    registers: tuple[str, ...] = ("casual", "diplomatic"),
    intention: str = "add_an_afterthought",
    **over: Any,
) -> Expression:
    base: dict[str, Any] = dict(
        phrase_id=phrase_id,
        canonical_form=canonical_form,
        communicative_intention=intention,
        construction_pattern="discourse_marker",
        lexical_structure_type="fixed_chunk",
        subskill_id="P1.1",
        register_profiles=list(registers),
        context_families=[ContextFamily(f) for f in families],
        arabic_gloss="بالمناسبة، وأنا أفكر في الأمر",
        acceptable_forms=[],
        meaning_prompt="You just remembered something that changes your answer. How do you start?",
        situation_prompt="Mid-meeting, a detail comes back to you. How do you bring it in?",
        function_prompt="Say one sentence that adds an afterthought.",
    )
    base.update(over)
    return Expression(**base)


def tiny_seed(*expressions: Expression, version: str = "1.0.0") -> CatalogSeed:
    return CatalogSeed(
        catalog_version=version,
        competencies=[Competency(competency_id="P1", name="Opening and managing talk")],
        subskills=[
            Subskill(subskill_id="P1.1", competency_id="P1", name="Afterthoughts", description="Adding a late thought."),
        ],
        expressions=list(expressions) or [expression("come_to_think_of_it")],
    )


def attempt_payload(
    phrase_id: str,
    *,
    outcome: str = "success",
    origin: str = "review",
    hint_level: int = 0,
    family: str = "general",
    due: bool = False,
    linguistic: Optional[int] = 3,
    functional: Optional[int] = 2,
    echo: bool = False,
    prompt_kind: str = "meaning",
    step_before: int = 0,
) -> RetrievalAttemptedV2:
    return RetrievalAttemptedV2(
        phrase_id=phrase_id,
        origin=RetrievalOrigin(origin),
        prompt_kind=PromptKind(prompt_kind),
        context_family=ContextFamily(family),
        outcome=RetrievalOutcome(outcome),
        hint_level=hint_level,  # type: ignore[arg-type]
        linguistic_level=None if linguistic is None else Ordinal(linguistic),
        functional_level=None if functional is None else Ordinal(functional),
        matched_form=None,
        matched_by=MatchedBy.PYTHON_EXACT,
        echo=echo,
        due=due,
        schedule_step_before=step_before,
    )


def attempt(
    db: EventSourcedDatabase,
    phrase_id: str,
    day: float,
    key: str,
    *,
    session: str = "s1",
    learner: int = LEARNER,
    event_id: Optional[str] = None,
    **kw: Any,
):
    """Append one ``retrieval_attempted`` event ``day`` days into the timeline."""
    return db.append_event_ex(
        learner_id=learner,
        session_id=session,
        event_type="retrieval_attempted",
        payload=attempt_payload(phrase_id, **kw),
        correlation_id=f"corr_{key}",
        idempotency_key=key,
        occurred_at_utc=at(day),
        event_id=event_id,
    )


def exposure(db: EventSourcedDatabase, phrase_id: str, day: float, key: str, *, session: str = "s1", learner: int = LEARNER):
    return db.append_event_ex(
        learner_id=learner,
        session_id=session,
        event_type="phrase_exposure_recorded",
        payload={"phrase_id": phrase_id, "revealed_to_learner": True, "directly_cued": False},
        correlation_id=f"corr_{key}",
        idempotency_key=key,
        occurred_at_utc=at(day),
    )


def phrase_row(db: EventSourcedDatabase, phrase_id: str, learner: int = LEARNER) -> dict[str, Any]:
    with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = ?", (learner, phrase_id)
        ).fetchone()
    assert row is not None, f"no phrase_state_projection row for {phrase_id}"
    return dict(row)


def evidence(db: EventSourcedDatabase, phrase_id: str, learner: int = LEARNER) -> list[dict[str, Any]]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT * FROM phrase_evidence WHERE learner_id = ? AND phrase_id = ? ORDER BY occurred_at_utc, source_event_id, dimension",
            (learner, phrase_id),
        ).fetchall()
    return [dict(r) for r in rows]


def next_review(db: EventSourcedDatabase, phrase_id: str, learner: int = LEARNER) -> Optional[datetime]:
    value = phrase_row(db, phrase_id, learner)["next_review_at_utc"]
    return parse_iso(value) if value else None
