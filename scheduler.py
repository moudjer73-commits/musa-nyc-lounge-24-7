"""Deterministic Spaced Repetition Scheduler & Review Queue Selector
(S0 Contracts, Sections 5.7 & 6).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clock import from_utc_iso, to_utc_iso
from schemas_v3 import ContextFamily, PhraseStage, PromptKind, RetrievalOutcome

__all__ = [
    "LADDER_DAYS",
    "MAX_SCHEDULE_STEP",
    "ScheduleUpdate",
    "ReviewQueueItem",
    "compute_next_schedule",
    "choose_prompt_kind",
    "select_due_review_items",
    "select_scene_targets",
]

# Spaced repetition interval ladder in days (steps 0..5)
LADDER_DAYS: tuple[float, ...] = (1.0, 3.0, 7.0, 14.0, 30.0, 60.0)
MAX_SCHEDULE_STEP = len(LADDER_DAYS) - 1


@dataclass(frozen=True)
class ScheduleUpdate:
    step_before: int
    step_after: int
    interval_days: float
    next_review_at: datetime
    next_review_at_utc: str


@dataclass(frozen=True)
class ReviewQueueItem:
    phrase_id: str
    canonical_form: str
    communicative_intention: str
    arabic_gloss: str | None
    stage: PhraseStage
    schedule_step: int
    mastery_index: float | None
    next_review_at_utc: str
    prompt_kind: PromptKind
    prompt_text: str
    context_family: ContextFamily


def compute_next_schedule(
    *,
    step_before: int,
    outcome: RetrievalOutcome,
    hint_level: int,
    now: datetime,
    is_due: bool = True,
) -> ScheduleUpdate:
    """Deterministic spaced-repetition step & due timestamp calculator."""
    clamped_before = max(0, min(int(step_before), MAX_SCHEDULE_STEP))
    out = RetrievalOutcome(outcome)

    if out == RetrievalOutcome.SUCCESS and int(hint_level) == 0:
        step_after = min(clamped_before + 1, MAX_SCHEDULE_STEP) if is_due else clamped_before
        interval_days = LADDER_DAYS[step_after]
    elif out in (RetrievalOutcome.HINTED_SUCCESS, RetrievalOutcome.SUCCESS) and int(hint_level) == 1:
        # Hinted success at level 1 holds the current step and schedules a half-step check (min 1 day)
        step_after = clamped_before
        interval_days = max(1.0, round(LADDER_DAYS[step_after] * 0.5, 2))
    elif out == RetrievalOutcome.PARTIAL or int(hint_level) >= 2:
        step_after = max(0, clamped_before - 1)
        interval_days = 1.0
    else:
        # RetrievalOutcome.FAILURE resets back 2 steps and schedules within 12 hours (0.5 days)
        step_after = max(0, clamped_before - 2)
        interval_days = 0.5

    next_dt = now + timedelta(days=interval_days)
    return ScheduleUpdate(
        step_before=clamped_before,
        step_after=step_after,
        interval_days=interval_days,
        next_review_at=next_dt,
        next_review_at_utc=to_utc_iso(next_dt),
    )


def choose_prompt_kind(stage: PhraseStage | str, attempt_count: int) -> PromptKind:
    """Rotates meaning -> situation -> function prompts as learner mastery advances."""
    st = str(stage)
    if st in (PhraseStage.UNSEEN.value, PhraseStage.SEEN.value, PhraseStage.RECOGNIZED.value):
        return PromptKind.MEANING
    if st == PhraseStage.PRODUCED_WITH_PROMPT.value:
        return PromptKind.SITUATION if (attempt_count % 2 == 0) else PromptKind.MEANING
    order = (PromptKind.SITUATION, PromptKind.FUNCTION, PromptKind.MEANING)
    return order[int(attempt_count) % len(order)]


def _prompt_text_for_kind(cat_row: Mapping[str, Any], kind: PromptKind) -> str:
    intention = str(cat_row.get("communicative_intention") or "express this idea naturally")
    if kind == PromptKind.MEANING and cat_row.get("meaning_prompt"):
        return str(cat_row["meaning_prompt"])
    if kind == PromptKind.SITUATION and cat_row.get("situation_prompt"):
        return str(cat_row["situation_prompt"])
    if kind == PromptKind.FUNCTION and cat_row.get("function_prompt"):
        return str(cat_row["function_prompt"])
    return f"In conversation, how would you {intention}?"


def _resolve_conn(db: Any) -> tuple[sqlite3.Connection, bool]:
    if isinstance(db, sqlite3.Connection):
        db.row_factory = sqlite3.Row
        return db, False
    if hasattr(db, "_connect"):
        conn = db._connect()
        conn.row_factory = sqlite3.Row
        return conn, True
    conn = sqlite3.connect(str(getattr(db, "db_path", db)))
    conn.row_factory = sqlite3.Row
    return conn, True


def select_due_review_items(
    db: Any,
    *,
    learner_id: int,
    now: datetime,
    limit: int = 5,
) -> list[ReviewQueueItem]:
    """Returns due phrases for learner_id ordered by overdue timestamp and lowest mastery_index."""
    now_iso = to_utc_iso(now)
    conn, owned = _resolve_conn(db)
    try:
        rows = conn.execute(
            """
            SELECT
                p.phrase_id,
                p.stage,
                p.attempt_count,
                COALESCE(p.schedule_step, 0) AS schedule_step,
                p.mastery_index,
                p.next_review_at_utc,
                p.home_context_family,
                c.canonical_form,
                c.communicative_intention,
                c.arabic_gloss,
                c.context_families_json,
                c.meaning_prompt,
                c.situation_prompt,
                c.function_prompt
            FROM phrase_state_projection p
            JOIN phrase_catalog c ON c.phrase_id = p.phrase_id
            WHERE p.learner_id = ?
              AND p.next_review_at_utc IS NOT NULL
              AND p.next_review_at_utc <= ?
            ORDER BY
              p.next_review_at_utc ASC,
              COALESCE(p.mastery_index, 0.0) ASC,
              p.phrase_id ASC
            LIMIT ?
            """,
            (learner_id, now_iso, int(limit)),
        ).fetchall()

        out: list[ReviewQueueItem] = []
        for r in rows:
            rd = dict(r)
            st = PhraseStage(str(rd["stage"])) if rd.get("stage") in PhraseStage._value2member_map_ else PhraseStage.SEEN
            p_kind = choose_prompt_kind(st, int(rd.get("attempt_count") or 0))
            p_text = _prompt_text_for_kind(rd, p_kind)

            fams: list[str] = []
            try:
                fams = json.loads(rd.get("context_families_json") or '["general"]')
            except Exception:
                fams = ["general"]
            chosen_fam_str = rd.get("home_context_family") or (fams[0] if fams else "general")
            chosen_fam = (
                ContextFamily(chosen_fam_str)
                if chosen_fam_str in ContextFamily._value2member_map_
                else ContextFamily.GENERAL
            )

            out.append(
                ReviewQueueItem(
                    phrase_id=str(rd["phrase_id"]),
                    canonical_form=str(rd["canonical_form"]),
                    communicative_intention=str(rd["communicative_intention"]),
                    arabic_gloss=rd.get("arabic_gloss"),
                    stage=st,
                    schedule_step=int(rd["schedule_step"] or 0),
                    mastery_index=float(rd["mastery_index"]) if rd.get("mastery_index") is not None else None,
                    next_review_at_utc=str(rd["next_review_at_utc"]),
                    prompt_kind=p_kind,
                    prompt_text=p_text,
                    context_family=chosen_fam,
                )
            )
        return out
    finally:
        if owned:
            conn.close()


def select_scene_targets(
    db: Any,
    *,
    learner_id: int,
    context_family: ContextFamily | str = ContextFamily.GENERAL,
    now: datetime,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Selects up to `limit` target phrases for a scene (due items first, then transfer/unseen candidates)."""
    fam = str(ContextFamily(context_family).value) if context_family else "general"
    now_iso = to_utc_iso(now)
    conn, owned = _resolve_conn(db)
    try:
        rows = conn.execute(
            """
            SELECT
                c.phrase_id,
                c.canonical_form,
                c.communicative_intention,
                c.arabic_gloss,
                c.context_families_json,
                COALESCE(p.stage, 'Unseen') AS stage,
                p.next_review_at_utc,
                p.mastery_index,
                COALESCE(p.context_families_json, '[]') AS used_families_json
            FROM phrase_catalog c
            LEFT JOIN phrase_state_projection p
              ON p.phrase_id = c.phrase_id AND p.learner_id = ?
            """,
            (learner_id,),
        ).fetchall()

        scored: list[tuple[int, float, str, dict[str, Any]]] = []
        for r in rows:
            rd = dict(r)
            try:
                cat_fams = json.loads(rd.get("context_families_json") or '["general"]')
            except Exception:
                cat_fams = ["general"]
            try:
                used_fams = json.loads(rd.get("used_families_json") or "[]")
            except Exception:
                used_fams = []

            is_due = bool(rd.get("next_review_at_utc") and str(rd["next_review_at_utc"]) <= now_iso)
            fam_match = fam in cat_fams or "general" in cat_fams
            needs_transfer = bool(used_fams and fam not in used_fams and fam_match)

            if is_due and fam_match:
                bucket = 0
            elif needs_transfer:
                bucket = 1
            elif fam_match and rd["stage"] != PhraseStage.STABLE.value:
                bucket = 2
            else:
                bucket = 3

            m_idx = float(rd["mastery_index"]) if rd.get("mastery_index") is not None else 0.0
            scored.append((bucket, m_idx, str(rd["phrase_id"]), rd))

        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in scored[: int(limit)]]
    finally:
        if owned:
            conn.close()
