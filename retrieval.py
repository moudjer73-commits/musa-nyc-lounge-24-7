"""3-Tier Hybrid Retrieval & Reconstruction Evaluator + Hint Ladder
(S0 Contracts, Sections 5.4, 5.5, 5.7 & 6).
"""
from __future__ import annotations

import difflib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from clock import to_utc_iso
from interaction import (
    ActiveInteraction,
    InteractionObject,
    InteractionState,
    close_interaction,
    open_interaction,
    transition,
)
from llm_transport import CallResult, DeadlineExceeded, QuotaShed, call_json
from mastery import (
    MasterySnapshot,
    build_evidence_for_retrieval,
    recompute_phrase_state,
)
from scheduler import ScheduleUpdate, compute_next_schedule
from schemas_v3 import (
    CallMetaDict,
    ContextFamily,
    MatchedBy,
    Ordinal,
    PromptKind,
    ReconstructionAttemptedV2,
    RetrievalAttemptedV2,
    RetrievalOrigin,
    RetrievalOutcome,
)
from validation import normalize_text

__all__ = [
    "EvaluationMatch",
    "RetrievalTurnResult",
    "ReconstructionTurnResult",
    "evaluate_phrase_attempt",
    "build_retrieval_hint",
    "start_review_interaction",
    "record_retrieval_attempt",
    "record_reconstruction_attempt",
]

FUZZY_MATCH_THRESHOLD = 0.84


@dataclass(frozen=True)
class EvaluationMatch:
    outcome: RetrievalOutcome
    linguistic_level: Ordinal
    functional_level: Ordinal
    matched_form: str | None
    matched_by: MatchedBy
    echo: bool
    functional_equivalent_credited: bool = False
    call_meta: CallMetaDict | None = None


@dataclass(frozen=True)
class RetrievalTurnResult:
    event_id: str
    evaluation: EvaluationMatch
    mastery: MasterySnapshot
    schedule: ScheduleUpdate
    next_hint_text: str | None
    interaction_closed: bool


@dataclass(frozen=True)
class ReconstructionTurnResult:
    event_id: str
    outcome: Literal["success", "partial", "failure"]
    evaluation: EvaluationMatch
    next_state: InteractionState | None


def _word_tokens(text: str) -> list[str]:
    norm = normalize_text(text)
    if not norm:
        return []
    # Strip punctuation around tokens while keeping internal apostrophes
    cleaned = []
    for raw_tok in norm.split():
        tok = raw_tok.strip(".,!?;:\"'()[]{}-—")
        if tok:
            cleaned.append(tok)
    return cleaned


def _contains_token_subsequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    if n == 0 or len(haystack) < n:
        return False
    target = list(needle)
    for i in range(len(haystack) - n + 1):
        if list(haystack[i : i + n]) == target:
            return True
    return False


def _best_window_similarity(haystack: Sequence[str], needle: Sequence[str]) -> float:
    n = len(needle)
    if n == 0 or not haystack:
        return 0.0
    needle_str = " ".join(needle)
    if len(haystack) <= n:
        return difflib.SequenceMatcher(None, " ".join(haystack), needle_str).ratio()
    best = 0.0
    for win_len in (max(1, n - 1), n, min(len(haystack), n + 1)):
        for i in range(len(haystack) - win_len + 1):
            cand_str = " ".join(haystack[i : i + win_len])
            r = difflib.SequenceMatcher(None, cand_str, needle_str).ratio()
            if r > best:
                best = r
    return best


def build_retrieval_hint(catalog_row: Mapping[str, Any], hint_level: int) -> str:
    """Builds progressive hints (level 1 -> frame/initial cue; level 2 -> masked slots + Arabic gloss)."""
    canonical = str(catalog_row.get("canonical_form") or "").strip()
    pattern = str(catalog_row.get("construction_pattern") or "").strip()
    gloss = str(catalog_row.get("arabic_gloss") or "").strip()
    words = canonical.split()

    if int(hint_level) <= 1:
        starter = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "")
        frame_part = f"Pattern: `{pattern}`" if pattern else f"Starts with: `{starter}...`"
        return f"💡 Hint 1 — {frame_part} ({len(words)} words)"

    # Hint level 2: mask middle content words while showing structure + Arabic gloss
    masked_words: list[str] = []
    for idx, w in enumerate(words):
        if idx in (0, len(words) - 1) or len(w) <= 2:
            masked_words.append(w)
        else:
            masked_words.append(w[0] + "_" * (len(w) - 1))
    skeleton = " ".join(masked_words)
    gloss_part = f"\nالمعنى المقصود: {gloss}" if gloss else ""
    return f"💡 Hint 2 — `{skeleton}`{gloss_part}"


def evaluate_phrase_attempt(
    *,
    learner_text: str,
    canonical_form: str,
    acceptable_forms: Sequence[str] = (),
    communicative_intention: str = "",
    hint_level: int = 0,
    recent_bot_texts: Sequence[str] = (),
    judge_fn: Callable[[str, str, str], dict[str, Any]] | None = None,
    allow_llm_judge: bool = True,
) -> EvaluationMatch:
    """3-Tier Hybrid Evaluator: Python Exact -> Python Fuzzy -> LLM Judge."""
    learner_toks = _word_tokens(learner_text)
    if not learner_toks:
        return EvaluationMatch(
            outcome=RetrievalOutcome.FAILURE,
            linguistic_level=0,
            functional_level=0,
            matched_form=None,
            matched_by=MatchedBy.PYTHON_EXACT,
            echo=False,
        )

    all_forms = list(dict.fromkeys([canonical_form, *acceptable_forms]))
    bot_tok_lists = [_word_tokens(bt) for bt in recent_bot_texts if bt]

    # ------------------------------------------------------------------
    # Tier 1: Python Exact contiguous token match
    # ------------------------------------------------------------------
    for form in all_forms:
        form_toks = _word_tokens(form)
        if form_toks and _contains_token_subsequence(learner_toks, form_toks):
            is_echo = any(_contains_token_subsequence(bt, form_toks) for bt in bot_tok_lists)
            outcome = (
                RetrievalOutcome.HINTED_SUCCESS
                if int(hint_level) > 0
                else RetrievalOutcome.SUCCESS
            )
            return EvaluationMatch(
                outcome=outcome,
                linguistic_level=3,
                functional_level=3,
                matched_form=form,
                matched_by=MatchedBy.PYTHON_EXACT,
                echo=is_echo,
            )

    # ------------------------------------------------------------------
    # Tier 2: Python Fuzzy window similarity
    # ------------------------------------------------------------------
    best_form: str | None = None
    best_ratio = 0.0
    for form in all_forms:
        form_toks = _word_tokens(form)
        if not form_toks:
            continue
        r = _best_window_similarity(learner_toks, form_toks)
        if r > best_ratio:
            best_ratio = r
            best_form = form

    if best_ratio >= FUZZY_MATCH_THRESHOLD and best_form is not None:
        form_toks = _word_tokens(best_form)
        is_echo = any(_contains_token_subsequence(bt, form_toks) for bt in bot_tok_lists)
        outcome = (
            RetrievalOutcome.HINTED_SUCCESS
            if int(hint_level) > 0
            else RetrievalOutcome.SUCCESS
        )
        return EvaluationMatch(
            outcome=outcome,
            linguistic_level=2,
            functional_level=3,
            matched_form=best_form,
            matched_by=MatchedBy.PYTHON_FUZZY,
            echo=is_echo,
        )

    # ------------------------------------------------------------------
    # Tier 3: LLM Judge (for functional equivalence or partial credit)
    # ------------------------------------------------------------------
    if judge_fn is not None or allow_llm_judge:
        try:
            call_meta: CallMetaDict | None = None
            if judge_fn is not None:
                raw_judge = judge_fn(learner_text, canonical_form, communicative_intention)
            else:
                sys_prompt = (
                    "You are an American English C1/C2 assessment judge. Return JSON with "
                    "linguistic_level (0..3), functional_level (0..3), and functional_equivalent (bool)."
                )
                usr_prompt = json.dumps(
                    {
                        "learner_text": learner_text,
                        "target_phrase": canonical_form,
                        "communicative_intention": communicative_intention,
                    }
                )
                res: CallResult[dict[str, Any]] = call_json("judge", sys_prompt, usr_prompt, lambda d: d)
                raw_judge = res.value
                call_meta = CallMetaDict(
                    model=res.meta.model,
                    attempts=res.meta.attempts,
                    latency_ms=res.meta.latency_ms,
                    thinking_budget=res.meta.thinking_budget,
                    input_tokens=res.meta.input_tokens,
                    output_tokens=res.meta.output_tokens,
                )

            l_lvl: Ordinal = int(raw_judge.get("linguistic_level", 0))  # type: ignore[assignment]
            f_lvl: Ordinal = int(raw_judge.get("functional_level", 0))  # type: ignore[assignment]
            l_lvl = max(0, min(3, l_lvl))  # type: ignore[assignment]
            f_lvl = max(0, min(3, f_lvl))  # type: ignore[assignment]
            func_eq = bool(raw_judge.get("functional_equivalent", False))

            if l_lvl >= 2 and f_lvl >= 2:
                out = (
                    RetrievalOutcome.HINTED_SUCCESS
                    if int(hint_level) > 0
                    else RetrievalOutcome.SUCCESS
                )
            elif l_lvl >= 1 or f_lvl >= 2:
                out = RetrievalOutcome.PARTIAL
            else:
                out = RetrievalOutcome.FAILURE

            return EvaluationMatch(
                outcome=out,
                linguistic_level=l_lvl,
                functional_level=f_lvl,
                matched_form=canonical_form if out != RetrievalOutcome.FAILURE else None,
                matched_by=MatchedBy.LLM_JUDGE,
                echo=False,
                functional_equivalent_credited=func_eq,
                call_meta=call_meta,
            )
        except (QuotaShed, DeadlineExceeded, Exception):
            pass

    # Deterministic fallback when LLM Judge is shed or disabled
    if best_ratio >= 0.65:
        return EvaluationMatch(
            outcome=RetrievalOutcome.PARTIAL,
            linguistic_level=1,
            functional_level=2,
            matched_form=best_form,
            matched_by=MatchedBy.PYTHON_FUZZY,
            echo=False,
        )

    return EvaluationMatch(
        outcome=RetrievalOutcome.FAILURE,
        linguistic_level=0,
        functional_level=0,
        matched_form=None,
        matched_by=MatchedBy.PYTHON_EXACT,
        echo=False,
    )


def start_review_interaction(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    phrase_id: str,
    prompt_kind: PromptKind,
    context_family: ContextFamily,
    scene_revision: int,
    now: datetime,
    ttl_s: int = 900,
) -> ActiveInteraction:
    """Opens a REVIEW_ACTIVE interaction for a due review item."""
    return open_interaction(
        db,
        learner_id=learner_id,
        session_id=session_id,
        object_type=InteractionObject.REVIEW_ITEM,
        object_ref=phrase_id,
        state=InteractionState.REVIEW_ACTIVE,
        payload={
            "phrase_id": phrase_id,
            "prompt_kind": str(prompt_kind.value),
            "context_family": str(context_family.value),
            "hint_level": 0,
        },
        scene_revision=scene_revision,
        now=now,
        ttl_s=ttl_s,
    )


def record_retrieval_attempt(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    phrase_id: str,
    learner_text: str,
    origin: RetrievalOrigin = RetrievalOrigin.REVIEW,
    prompt_kind: PromptKind = PromptKind.MEANING,
    context_family: ContextFamily = ContextFamily.GENERAL,
    hint_level: Literal[0, 1, 2] = 0,
    due: bool = True,
    interaction_id: int | None = None,
    expected_interaction_revision: int | None = None,
    correlation_id: str = "corr_ret",
    idempotency_key: str = "idem_ret",
    recent_bot_texts: Sequence[str] = (),
    judge_fn: Callable[[str, str, str], dict[str, Any]] | None = None,
    allow_llm_judge: bool = False,
    now: datetime,
) -> RetrievalTurnResult:
    """Evaluates a retrieval attempt, appends RetrievalAttemptedV2, updates evidence & schedule."""
    now_iso = to_utc_iso(now)
    with db._connect() as conn:
        cat_row = conn.execute(
            "SELECT * FROM phrase_catalog WHERE phrase_id = ?", (phrase_id,)
        ).fetchone()
        if cat_row is None:
            raise LookupError(f"Unknown phrase_id: {phrase_id!r}")
        cat_dict = dict(cat_row)

        psp_row = conn.execute(
            "SELECT * FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = ?",
            (learner_id, phrase_id),
        ).fetchone()
        psp_dict = dict(psp_row) if psp_row is not None else {}

    try:
        acc_forms = json.loads(cat_dict.get("acceptable_forms_json") or "[]")
    except Exception:
        acc_forms = []

    step_before = int(psp_dict.get("schedule_step") or 0)
    prior_last_success = psp_dict.get("last_success_at_utc")

    eval_match = evaluate_phrase_attempt(
        learner_text=learner_text,
        canonical_form=str(cat_dict["canonical_form"]),
        acceptable_forms=acc_forms,
        communicative_intention=str(cat_dict.get("communicative_intention") or ""),
        hint_level=hint_level,
        recent_bot_texts=recent_bot_texts,
        judge_fn=judge_fn,
        allow_llm_judge=allow_llm_judge,
    )

    event_payload = RetrievalAttemptedV2(
        phrase_id=phrase_id,
        origin=RetrievalOrigin(origin),
        prompt_kind=PromptKind(prompt_kind),
        context_family=ContextFamily(context_family),
        outcome=eval_match.outcome,
        hint_level=hint_level,
        linguistic_level=eval_match.linguistic_level,
        functional_level=eval_match.functional_level,
        matched_form=eval_match.matched_form,
        matched_by=eval_match.matched_by,
        echo=eval_match.echo,
        due=due,
        schedule_step_before=step_before,
        interaction_id=interaction_id,
        call_meta=eval_match.call_meta,
    )

    app_res = db.append_event_ex(
        learner_id=learner_id,
        session_id=session_id,
        event_type="retrieval_attempted",
        payload=event_payload,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        occurred_at_utc=now_iso,
        rebuild=False,
    )

    ev_specs = build_evidence_for_retrieval(
        source_event_id=app_res.event_id,
        learner_id=learner_id,
        phrase_id=phrase_id,
        outcome=eval_match.outcome,
        origin=RetrievalOrigin(origin),
        context_family=ContextFamily(context_family),
        hint_level=hint_level,
        linguistic_level=eval_match.linguistic_level,
        functional_level=eval_match.functional_level,
        occurred_at_utc=now_iso,
        prior_last_success_at_utc=prior_last_success,
        echo=eval_match.echo,
    )

    sched = compute_next_schedule(
        step_before=step_before,
        outcome=eval_match.outcome,
        hint_level=hint_level,
        now=now,
        is_due=due,
    )

    with db._connect() as conn:
        for es in ev_specs:
            conn.execute(
                """
                INSERT OR REPLACE INTO phrase_evidence (
                    source_event_id, dimension, learner_id, phrase_id,
                    score, weight, context_family, origin, hint_level,
                    is_delayed, gap_days, echo, occurred_at_utc, retracted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    es.source_event_id,
                    es.dimension.value,
                    es.learner_id,
                    es.phrase_id,
                    es.score,
                    es.weight,
                    es.context_family,
                    es.origin,
                    es.hint_level,
                    es.is_delayed,
                    es.gap_days,
                    es.echo,
                    es.occurred_at_utc,
                ),
            )
        mastery_snap = recompute_phrase_state(
            conn,
            learner_id=learner_id,
            phrase_id=phrase_id,
            now=now,
            schedule_step=sched.step_after,
            next_review_at_utc=sched.next_review_at_utc,
            last_outcome=eval_match.outcome.value,
        )
        conn.commit()

    next_hint: str | None = None
    closed = False
    if interaction_id is not None:
        if eval_match.outcome in (RetrievalOutcome.SUCCESS, RetrievalOutcome.HINTED_SUCCESS) or hint_level >= 2:
            close_interaction(db, interaction_id=interaction_id)
            closed = True
        else:
            next_hl = min(2, int(hint_level) + 1)
            next_hint = build_retrieval_hint(cat_dict, next_hl)
            if expected_interaction_revision is not None:
                transition(
                    db,
                    interaction_id=interaction_id,
                    expected_revision=expected_interaction_revision,
                    new_state=InteractionState.REVIEW_ACTIVE,
                    payload_patch={"hint_level": next_hl},
                    now=now,
                )

    return RetrievalTurnResult(
        event_id=app_res.event_id,
        evaluation=eval_match,
        mastery=mastery_snap,
        schedule=sched,
        next_hint_text=next_hint,
        interaction_closed=closed,
    )


def record_reconstruction_attempt(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    interaction_id: int,
    expected_revision: int,
    diagnostic_id: str,
    target_alternative: str,
    attempt_text: str,
    correlation_id: str = "corr_rc",
    idempotency_key: str = "idem_rc",
    judge_fn: Callable[[str, str, str], dict[str, Any]] | None = None,
    allow_llm_judge: bool = False,
    now: datetime,
) -> ReconstructionTurnResult:
    """Evaluates a learner's reconstruction attempt and transitions active_interaction to RECONSTRUCTION_FEEDBACK."""
    now_iso = to_utc_iso(now)
    eval_match = evaluate_phrase_attempt(
        learner_text=attempt_text,
        canonical_form=target_alternative,
        acceptable_forms=[target_alternative],
        communicative_intention="reconstruct the upgraded phrasing",
        hint_level=0,
        judge_fn=judge_fn,
        allow_llm_judge=allow_llm_judge,
    )

    if eval_match.outcome in (RetrievalOutcome.SUCCESS, RetrievalOutcome.HINTED_SUCCESS):
        rc_outcome: Literal["success", "partial", "failure"] = "success"
    elif eval_match.outcome == RetrievalOutcome.PARTIAL:
        rc_outcome = "partial"
    else:
        rc_outcome = "failure"

    payload = ReconstructionAttemptedV2(
        interaction_id=interaction_id,
        diagnostic_id=diagnostic_id,
        attempt_text=attempt_text,
        linguistic_level=eval_match.linguistic_level,
        functional_level=eval_match.functional_level,
        outcome=rc_outcome,
        functional_equivalent_credited=eval_match.functional_equivalent_credited,
        matched_by=eval_match.matched_by,
    )
    app_res = db.append_event_ex(
        learner_id=learner_id,
        session_id=session_id,
        event_type="reconstruction_attempted",
        payload=payload,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        occurred_at_utc=now_iso,
        rebuild=False,
    )

    updated = transition(
        db,
        interaction_id=interaction_id,
        expected_revision=expected_revision,
        new_state=InteractionState.RECONSTRUCTION_FEEDBACK,
        payload_patch={
            "last_attempt": attempt_text,
            "outcome": rc_outcome,
        },
        now=now,
    )

    return ReconstructionTurnResult(
        event_id=app_res.event_id,
        outcome=rc_outcome,
        evaluation=eval_match,
        next_state=updated.state if updated is not None else None,
    )
