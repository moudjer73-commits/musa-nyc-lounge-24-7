"""5-Dimension Mastery Engine & Deterministic Stage Transitions
(S0 Contracts, Sections 5.5, 5.6 & 6).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from clock import from_utc_iso, to_utc_iso
from schemas_v3 import (
    ORDINAL_SCORE,
    ContextFamily,
    EvidenceDimension,
    Ordinal,
    PhraseStage,
    RetrievalOrigin,
    RetrievalOutcome,
)

__all__ = [
    "DIMENSION_WEIGHTS",
    "STABLE_MASTERY_THRESHOLD",
    "EvidenceRowSpec",
    "MasterySnapshot",
    "build_evidence_for_retrieval",
    "compute_mastery_snapshot",
    "compute_phrase_stage",
    "recompute_phrase_state",
]

DIMENSION_WEIGHTS: dict[str, float] = {
    "dim_r": 0.15,  # Recognition
    "dim_p": 0.30,  # Production
    "dim_i": 0.25,  # Independence
    "dim_t": 0.15,  # Transfer across context families
    "dim_a": 0.15,  # Pragmatic / functional appropriateness
}

STABLE_MASTERY_THRESHOLD = 75.0
DELAYED_SUCCESS_MIN_GAP_DAYS = 1.0


@dataclass(frozen=True)
class EvidenceRowSpec:
    source_event_id: str
    dimension: EvidenceDimension
    learner_id: int
    phrase_id: str
    score: float
    weight: float
    context_family: str
    origin: str
    hint_level: int
    is_delayed: int
    gap_days: float | None
    echo: int
    occurred_at_utc: str
    retracted: int = 0


@dataclass(frozen=True)
class MasterySnapshot:
    learner_id: int
    phrase_id: str
    stage: PhraseStage
    exposure_count: int
    attempt_count: int
    success_count: int
    independent_success_count: int
    spontaneous_success_count: int
    delayed_success_count: int
    max_delayed_gap_days: float
    context_families: list[str]
    home_context_family: str | None
    dim_r: float | None
    dim_p: float | None
    dim_i: float | None
    dim_t: float | None
    dim_a: float | None
    mastery_index: float | None
    unresolved_critical_errors: int
    last_outcome: str | None
    last_success_at_utc: str | None


def _independence_score(
    outcome: RetrievalOutcome,
    origin: RetrievalOrigin,
    hint_level: int,
) -> float:
    if outcome == RetrievalOutcome.FAILURE:
        return 0.0
    if origin == RetrievalOrigin.SPONTANEOUS and hint_level == 0:
        return 100.0 if outcome == RetrievalOutcome.SUCCESS else 60.0
    if hint_level == 0 and outcome == RetrievalOutcome.SUCCESS:
        return 85.0
    if hint_level == 1 or outcome == RetrievalOutcome.HINTED_SUCCESS:
        return 50.0
    return 25.0


def build_evidence_for_retrieval(
    *,
    source_event_id: str,
    learner_id: int,
    phrase_id: str,
    outcome: RetrievalOutcome,
    origin: RetrievalOrigin,
    context_family: ContextFamily | str,
    hint_level: int,
    linguistic_level: Ordinal | None,
    functional_level: Ordinal | None,
    occurred_at_utc: str,
    prior_last_success_at_utc: str | None = None,
    echo: bool = False,
) -> list[EvidenceRowSpec]:
    """Maps a single retrieval or spontaneous production event into dimensional evidence rows."""
    fam = str(ContextFamily(context_family).value) if context_family else "general"
    orig = str(RetrievalOrigin(origin).value)
    echo_int = 1 if echo else 0

    ling_ord = linguistic_level if linguistic_level is not None else (
        3 if outcome == RetrievalOutcome.SUCCESS else (2 if outcome in (RetrievalOutcome.HINTED_SUCCESS, RetrievalOutcome.PARTIAL) else 0)
    )
    func_ord = functional_level if functional_level is not None else ling_ord

    prod_score = ORDINAL_SCORE[int(ling_ord)]
    prag_score = ORDINAL_SCORE[int(func_ord)]
    indep_score = _independence_score(outcome, RetrievalOrigin(origin), hint_level)

    gap_days: float | None = None
    is_delayed = 0
    if prior_last_success_at_utc and outcome in (RetrievalOutcome.SUCCESS, RetrievalOutcome.HINTED_SUCCESS):
        try:
            curr_dt = from_utc_iso(occurred_at_utc)
            prev_dt = from_utc_iso(prior_last_success_at_utc)
            delta_days = (curr_dt - prev_dt).total_seconds() / 86400.0
            if delta_days >= 0.0:
                gap_days = round(delta_days, 4)
                if delta_days >= DELAYED_SUCCESS_MIN_GAP_DAYS and hint_level == 0:
                    is_delayed = 1
        except Exception:
            pass

    prod_weight = 1.2 if orig == "spontaneous" else (1.0 if hint_level == 0 else 0.75)

    rows = [
        EvidenceRowSpec(
            source_event_id=source_event_id,
            dimension=EvidenceDimension.PRODUCTION,
            learner_id=learner_id,
            phrase_id=phrase_id,
            score=prod_score,
            weight=prod_weight,
            context_family=fam,
            origin=orig,
            hint_level=int(hint_level),
            is_delayed=is_delayed,
            gap_days=gap_days,
            echo=echo_int,
            occurred_at_utc=occurred_at_utc,
        ),
        EvidenceRowSpec(
            source_event_id=source_event_id,
            dimension=EvidenceDimension.INDEPENDENCE,
            learner_id=learner_id,
            phrase_id=phrase_id,
            score=indep_score,
            weight=1.0,
            context_family=fam,
            origin=orig,
            hint_level=int(hint_level),
            is_delayed=is_delayed,
            gap_days=gap_days,
            echo=echo_int,
            occurred_at_utc=occurred_at_utc,
        ),
        EvidenceRowSpec(
            source_event_id=source_event_id,
            dimension=EvidenceDimension.PRAGMATIC,
            learner_id=learner_id,
            phrase_id=phrase_id,
            score=prag_score,
            weight=1.0,
            context_family=fam,
            origin=orig,
            hint_level=int(hint_level),
            is_delayed=is_delayed,
            gap_days=gap_days,
            echo=echo_int,
            occurred_at_utc=occurred_at_utc,
        ),
    ]
    return rows


def _transfer_score(distinct_family_count: int) -> float | None:
    if distinct_family_count <= 0:
        return None
    if distinct_family_count == 1:
        return 40.0
    if distinct_family_count == 2:
        return 75.0
    return 100.0


def compute_phrase_stage(
    *,
    exposure_count: int,
    attempt_count: int,
    dim_r: float | None,
    success_count: int,
    independent_success_count: int,
    spontaneous_success_count: int,
    delayed_success_count: int,
    max_delayed_gap_days: float,
    distinct_context_families: int,
    mastery_index: float | None,
    unresolved_critical_errors: int,
    last_outcome: str | None = None,
) -> PhraseStage:
    """Deterministic stage progression & regression rules (Section 5.6)."""
    if (
        independent_success_count >= 2
        and delayed_success_count >= 1
        and max_delayed_gap_days >= DELAYED_SUCCESS_MIN_GAP_DAYS
        and distinct_context_families >= 2
        and unresolved_critical_errors == 0
        and (mastery_index or 0.0) >= STABLE_MASTERY_THRESHOLD
        and last_outcome != "failure"
    ):
        return PhraseStage.STABLE

    if spontaneous_success_count >= 1 and unresolved_critical_errors == 0 and last_outcome != "failure":
        return PhraseStage.PRODUCED_SPONTANEOUSLY

    if independent_success_count >= 1:
        return PhraseStage.PRODUCED_INDEPENDENTLY

    if success_count >= 1:
        return PhraseStage.PRODUCED_WITH_PROMPT

    if (dim_r is not None and dim_r >= 50.0) or (attempt_count > 0 and success_count == 0 and exposure_count > 0):
        return PhraseStage.RECOGNIZED

    if exposure_count > 0 or attempt_count > 0:
        return PhraseStage.SEEN

    return PhraseStage.UNSEEN


def compute_mastery_snapshot(
    *,
    learner_id: int,
    phrase_id: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    exposure_count: int = 0,
    unresolved_critical_errors: int = 0,
    last_outcome: str | None = None,
) -> MasterySnapshot:
    """Computes all 5 dimensions, mastery_index, and PhraseStage from active evidence rows."""
    active_rows = [
        r
        for r in evidence_rows
        if not int(r.get("retracted", 0) or 0) and not int(r.get("echo", 0) or 0)
    ]

    dim_acc: dict[str, list[float]] = {
        "recognition": [0.0, 0.0],
        "production": [0.0, 0.0],
        "independence": [0.0, 0.0],
        "pragmatic": [0.0, 0.0],
    }

    prod_events: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}

    for idx, r in enumerate(active_rows):
        dim = str(r.get("dimension") or "").lower()
        score = float(r.get("score") or 0.0)
        weight = float(r.get("weight") or 1.0)
        if dim in dim_acc and weight > 0:
            # Slight recency boost for newer evidence rows
            recency_factor = 1.0 + (0.05 * min(idx, 10))
            eff_w = weight * recency_factor
            dim_acc[dim][0] += score * eff_w
            dim_acc[dim][1] += eff_w

        if dim == "production":
            ev_id = str(r.get("source_event_id") or f"ev_{idx}")
            prod_events[ev_id] = dict(r)
            if score >= 60.0:
                fam = str(r.get("context_family") or "general")
                family_counts[fam] = family_counts.get(fam, 0) + 1

    def _avg(key: str) -> float | None:
        num, den = dim_acc[key]
        return round(num / den, 2) if den > 0 else None

    dim_r = _avg("recognition")
    dim_p = _avg("production")
    dim_i = _avg("independence")
    dim_a = _avg("pragmatic")

    # If the learner has produced the phrase, recognition is implicitly at least max(dim_r, dim_p)
    if dim_r is None and dim_p is not None:
        dim_r = dim_p

    distinct_families = sorted(family_counts.keys())
    dim_t = _transfer_score(len(distinct_families))
    home_family = (
        min(family_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if family_counts
        else None
    )

    attempt_count = len(prod_events)
    success_count = 0
    independent_success_count = 0
    spontaneous_success_count = 0
    delayed_success_count = 0
    max_delayed_gap_days = 0.0
    last_success_at_utc: str | None = None

    ordered_prod = sorted(
        prod_events.values(), key=lambda item: str(item.get("occurred_at_utc") or "")
    )
    for pev in ordered_prod:
        sc = float(pev.get("score") or 0.0)
        hl = int(pev.get("hint_level") or 0)
        orig = str(pev.get("origin") or "scene")
        occ = str(pev.get("occurred_at_utc") or "")
        if sc >= 60.0:
            success_count += 1
            if hl == 0:
                independent_success_count += 1
                if orig == "spontaneous":
                    spontaneous_success_count += 1
            if int(pev.get("is_delayed") or 0) == 1 or float(pev.get("gap_days") or 0.0) >= DELAYED_SUCCESS_MIN_GAP_DAYS:
                if hl == 0:
                    delayed_success_count += 1
                    max_delayed_gap_days = max(
                        max_delayed_gap_days, float(pev.get("gap_days") or 0.0)
                    )
            if occ:
                last_success_at_utc = occ

    dim_map = {
        "dim_r": dim_r,
        "dim_p": dim_p,
        "dim_i": dim_i,
        "dim_t": dim_t,
        "dim_a": dim_a,
    }
    weighted_sum = 0.0
    weight_total = 0.0
    for d_key, d_val in dim_map.items():
        if d_val is not None:
            w = DIMENSION_WEIGHTS[d_key]
            weighted_sum += w * d_val
            weight_total += w

    mastery_index = round(weighted_sum / weight_total, 2) if weight_total > 0 else None

    effective_last_outcome = last_outcome
    if effective_last_outcome is None and ordered_prod:
        last_sc = float(ordered_prod[-1].get("score") or 0.0)
        effective_last_outcome = "success" if last_sc >= 60.0 else "failure"

    stage = compute_phrase_stage(
        exposure_count=exposure_count,
        attempt_count=attempt_count,
        dim_r=dim_r,
        success_count=success_count,
        independent_success_count=independent_success_count,
        spontaneous_success_count=spontaneous_success_count,
        delayed_success_count=delayed_success_count,
        max_delayed_gap_days=max_delayed_gap_days,
        distinct_context_families=len(distinct_families),
        mastery_index=mastery_index,
        unresolved_critical_errors=unresolved_critical_errors,
        last_outcome=effective_last_outcome,
    )

    return MasterySnapshot(
        learner_id=learner_id,
        phrase_id=phrase_id,
        stage=stage,
        exposure_count=exposure_count,
        attempt_count=attempt_count,
        success_count=success_count,
        independent_success_count=independent_success_count,
        spontaneous_success_count=spontaneous_success_count,
        delayed_success_count=delayed_success_count,
        max_delayed_gap_days=round(max_delayed_gap_days, 4),
        context_families=distinct_families,
        home_context_family=home_family,
        dim_r=dim_r,
        dim_p=dim_p,
        dim_i=dim_i,
        dim_t=dim_t,
        dim_a=dim_a,
        mastery_index=mastery_index,
        unresolved_critical_errors=unresolved_critical_errors,
        last_outcome=effective_last_outcome,
        last_success_at_utc=last_success_at_utc,
    )


def recompute_phrase_state(
    conn: sqlite3.Connection,
    *,
    learner_id: int,
    phrase_id: str,
    now: datetime,
    schedule_step: int | None = None,
    next_review_at_utc: str | None = None,
    last_outcome: str | None = None,
) -> MasterySnapshot:
    """Recomputes and persists phrase_state_projection for (learner_id, phrase_id)."""
    conn.row_factory = sqlite3.Row
    ev_rows = conn.execute(
        "SELECT * FROM phrase_evidence WHERE learner_id = ? AND phrase_id = ? "
        "ORDER BY occurred_at_utc ASC, rowid ASC",
        (learner_id, phrase_id),
    ).fetchall()

    cur_psp = conn.execute(
        "SELECT * FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = ?",
        (learner_id, phrase_id),
    ).fetchone()
    cur_dict = dict(cur_psp) if cur_psp is not None else {}

    exposure_count = int(cur_dict.get("exposure_count") or 0)
    unresolved_errors = int(cur_dict.get("unresolved_critical_errors") or 0)

    snap = compute_mastery_snapshot(
        learner_id=learner_id,
        phrase_id=phrase_id,
        evidence_rows=[dict(r) for r in ev_rows],
        exposure_count=exposure_count,
        unresolved_critical_errors=unresolved_errors,
        last_outcome=last_outcome or cur_dict.get("last_outcome"),
    )

    now_iso = to_utc_iso(now)
    eff_step = schedule_step if schedule_step is not None else int(cur_dict.get("schedule_step") or 0)
    eff_next_rev = (
        next_review_at_utc
        if next_review_at_utc is not None
        else cur_dict.get("next_review_at_utc")
    )
    first_unaided = cur_dict.get("first_unaided_at_utc") or (
        snap.last_success_at_utc if snap.independent_success_count > 0 else None
    )
    last_unaided = (
        snap.last_success_at_utc
        if snap.independent_success_count > 0
        else cur_dict.get("last_unaided_at_utc")
    )
    last_seen = snap.last_success_at_utc or cur_dict.get("last_seen_at_utc") or now_iso

    psp_cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(phrase_state_projection)").fetchall()
    }
    row_values: dict[str, Any] = {
        "learner_id": learner_id,
        "phrase_id": phrase_id,
        "stage": snap.stage.value,
        "exposure_count": snap.exposure_count,
        "attempt_count": snap.attempt_count,
        "unaided_valid_uses": snap.independent_success_count,
        "distinct_valid_registers_json": cur_dict.get("distinct_valid_registers_json") or "[]",
        "first_unaided_at_utc": first_unaided,
        "last_unaided_at_utc": last_unaided,
        "last_seen_at_utc": last_seen,
        "next_review_at_utc": eff_next_rev,
        "updated_at_utc": now_iso,
        "success_count": snap.success_count,
        "independent_success_count": snap.independent_success_count,
        "spontaneous_success_count": snap.spontaneous_success_count,
        "delayed_success_count": snap.delayed_success_count,
        "max_delayed_gap_days": snap.max_delayed_gap_days,
        "context_families_json": json.dumps(snap.context_families, ensure_ascii=False),
        "home_context_family": snap.home_context_family,
        "dim_r": snap.dim_r,
        "dim_p": snap.dim_p,
        "dim_i": snap.dim_i,
        "dim_t": snap.dim_t,
        "dim_a": snap.dim_a,
        "mastery_index": snap.mastery_index,
        "schedule_step": eff_step,
        "last_outcome": snap.last_outcome,
        "last_success_at_utc": snap.last_success_at_utc,
        "unresolved_critical_errors": snap.unresolved_critical_errors,
    }
    if "acquisition_stage" in psp_cols:
        row_values["acquisition_stage"] = snap.stage.value
    for col_info in conn.execute("PRAGMA table_info(phrase_state_projection)").fetchall():
        cname, ctype, notnull, dflt = str(col_info[1]), str(col_info[2]).upper(), int(col_info[3]), col_info[4]
        if cname not in row_values or row_values[cname] is None:
            if cname in cur_dict and cur_dict[cname] is not None:
                row_values[cname] = cur_dict[cname]
            elif notnull and dflt is None:
                row_values[cname] = now_iso if "UTC" in cname.upper() else (0 if "INT" in ctype or "REAL" in ctype else "")
    filtered = {k: v for k, v in row_values.items() if k in psp_cols}
    cols_sql = ", ".join(filtered.keys())
    placeholders = ", ".join("?" for _ in filtered)
    conn.execute(
        f"INSERT OR REPLACE INTO phrase_state_projection ({cols_sql}) VALUES ({placeholders})",
        tuple(filtered.values()),
    )
    return snap
