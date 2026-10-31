"""V3.0 Interactive Telegram UI Controller, Callback Dispatcher, Audio Cache & Debrief
(S0 Contracts, Sections 5.4, 6, 7 & 8).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from clock import SystemClock, to_utc_iso
from interaction import (
    ActiveInteraction,
    InteractionObject,
    InteractionState,
    TextRoute,
    build_callback,
    close_interaction,
    get_active_interaction,
    open_interaction,
    parse_callback,
    route_text,
    transition,
)
from retrieval import (
    build_retrieval_hint,
    record_reconstruction_attempt,
    record_retrieval_attempt,
    start_review_interaction,
)
from scheduler import select_due_review_items
from schemas_v3 import (
    ContextFamily,
    FeedbackCardDeliveredV2,
    FeedbackStatus,
    PhraseStage,
    PromptKind,
    RetrievalOrigin,
    RetrievalOutcome,
)

__all__ = [
    "UIButton",
    "UIResponse",
    "AudioLookupResult",
    "format_upgrade_card",
    "handle_v3_callback",
    "handle_v3_text_turn",
    "resolve_or_cache_audio",
    "build_scene_debrief",
    "build_v3_progress_report",
]


@dataclass(frozen=True)
class UIButton:
    text: str
    callback_data: str


@dataclass(frozen=True)
class UIResponse:
    handled: bool
    text: str
    buttons: list[UIButton] = field(default_factory=list)
    route: TextRoute = TextRoute.SCENE_TURN
    interaction: ActiveInteraction | None = None


@dataclass(frozen=True)
class AudioLookupResult:
    cache_hit: bool
    script_hash: str
    voice_config: str
    file_id: str | None
    audio_bytes: bytes | None = None


def _script_hash(text: str) -> str:
    norm = " ".join((text or "").strip().split()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def format_upgrade_card(item: dict[str, Any]) -> str:
    """Formats a concise bilingual V3 upgrade/error card without judgmental tone on upgrades."""
    cls = str(item.get("classification") or "error")
    orig = str(item.get("original_span") or "")
    alt = str(item.get("alternative") or "")
    exp_en = str(item.get("explanation_en") or "").strip()
    exp_ar = str(item.get("explanation_ar") or "").strip()

    header = "🔧 *Key Repair*" if cls == "error" else "✨ *Natural Upgrade*"
    lines = [
        header,
        f"• *You said:* \"{orig}\"",
        f"• *Try saying:* \"{alt}\"",
    ]
    if exp_en:
        lines.append(f"💡 {exp_en}")
    if exp_ar:
        lines.append(f"🌍 {exp_ar}")
    return "\n".join(lines)


def _fetch_feedback_item_for_ug(
    conn: sqlite3.Connection,
    *,
    learner_id: int,
    turn_event_seq: int,
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM feedback_item_projection
        WHERE learner_id = ? AND turn_event_seq = ? AND status IN ('pending', 'delivered')
        ORDER BY priority_class ASC, priority_score DESC
        LIMIT 1
        """,
        (learner_id, turn_event_seq),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT * FROM feedback_item_projection
            WHERE learner_id = ? AND status = 'pending'
            ORDER BY priority_class ASC, priority_score DESC, created_at_utc DESC
            LIMIT 1
            """,
            (learner_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def handle_v3_callback(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    callback_data: str,
    now: datetime | None = None,
) -> UIResponse:
    """Dispatches compact V3 `<op>:<obj_id>:<rev>` callbacks (Section 7)."""
    now_dt = now or SystemClock().now()
    now_iso = to_utc_iso(now_dt)
    parsed = parse_callback(callback_data)
    if parsed is None:
        return UIResponse(handled=False, text="")

    op, obj_id, rev = parsed.op, parsed.obj_id, parsed.rev

    # ------------------------------------------------------------------
    # 1. `ug:<reply_event_seq>:<session_rev>` -> Open UPGRADE_VIEW
    # ------------------------------------------------------------------
    if op == "ug":
        with db._connect() as conn:
            item = _fetch_feedback_item_for_ug(conn, learner_id=learner_id, turn_event_seq=obj_id)
            if item is None:
                return UIResponse(
                    handled=True,
                    text="This coaching note is no longer active.",
                )
            diag_id = str(item["diagnostic_id"])
            conn.execute(
                "UPDATE feedback_item_projection SET status = ?, delivered_at_utc = ? WHERE diagnostic_id = ?",
                (FeedbackStatus.DELIVERED.value, now_iso, diag_id),
            )
            conn.commit()

        card_payload = FeedbackCardDeliveredV2(
            diagnostic_id=diag_id,
            card_id=f"card_{diag_id}",
            trigger="tap",
        )
        db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload=card_payload,
            correlation_id=f"ug_{diag_id}",
            idempotency_key=f"ug_tap_{learner_id}_{diag_id}_{rev}",
            occurred_at_utc=now_iso,
            rebuild=False,
        )

        active = open_interaction(
            db,
            learner_id=learner_id,
            session_id=session_id,
            object_type=InteractionObject.UPGRADE,
            object_ref=diag_id,
            state=InteractionState.UPGRADE_VIEW,
            payload={
                "diagnostic_id": diag_id,
                "original_span": item["original_span"],
                "alternative": item["alternative"],
                "explanation_en": item.get("explanation_en") or "",
                "explanation_ar": item.get("explanation_ar") or "",
            },
            scene_revision=rev,
            now=now_dt,
            ttl_s=900,
        )
        buttons = [
            UIButton("✍️ Try it", build_callback("rc", active.interaction_id, active.revision)),
            UIButton("🔙 Back to scene", build_callback("bk", active.interaction_id, active.revision)),
        ]
        return UIResponse(
            handled=True,
            text=format_upgrade_card(item),
            buttons=buttons,
            interaction=active,
        )

    # ------------------------------------------------------------------
    # 2. `rc:<interaction_id>:<rev>` -> Open RECONSTRUCTION_ACTIVE (15 min TTL)
    # ------------------------------------------------------------------
    if op == "rc":
        current = get_active_interaction(db, learner_id, now_dt)
        if current is None or current.interaction_id != obj_id:
            return UIResponse(
                handled=True,
                text="⏱ This upgrade card expired. Send your next message to continue the scene!",
            )
        diag_id = str(current.payload.get("diagnostic_id") or current.object_ref)
        alt_text = str(current.payload.get("alternative") or "")
        orig_span = str(current.payload.get("original_span") or "")

        rec_active = open_interaction(
            db,
            learner_id=learner_id,
            session_id=session_id,
            object_type=InteractionObject.RECONSTRUCTION,
            object_ref=diag_id,
            state=InteractionState.RECONSTRUCTION_ACTIVE,
            payload={
                "diagnostic_id": diag_id,
                "original_span": orig_span,
                "alternative": alt_text,
            },
            scene_revision=current.scene_revision_at_open,
            now=now_dt,
            ttl_s=900,
        )
        prompt_lines = [
            "✍️ *Quick Reconstruction*",
            f"Rewrite your thought naturally using: *{alt_text}*",
            "(Type your sentence below, or tap Skip to return to the scene.)",
        ]
        buttons = [
            UIButton("⏭ Skip", build_callback("rs", rec_active.interaction_id, rec_active.revision)),
        ]
        return UIResponse(
            handled=True,
            text="\n".join(prompt_lines),
            buttons=buttons,
            interaction=rec_active,
        )

    # ------------------------------------------------------------------
    # 3. `rs` (skip reconstruction) / `bk` (back to scene) -> Close interaction
    # ------------------------------------------------------------------
    if op in ("rs", "bk"):
        close_interaction(db, interaction_id=obj_id)
        return UIResponse(
            handled=True,
            text="👍 Back to the scene—keep the conversation going!",
            buttons=[],
            interaction=None,
        )

    # ------------------------------------------------------------------
    # 4. `rvn:<idx>:<rev>` -> Start or advance to next due review item
    # ------------------------------------------------------------------
    if op == "rvn":
        due_items = select_due_review_items(db, learner_id=learner_id, now=now_dt, limit=1)
        if not due_items:
            close_interaction(db, interaction_id=obj_id)
            return UIResponse(
                handled=True,
                text="🎉 You're all caught up on spaced-repetition reviews!",
                buttons=[],
                interaction=None,
            )
        top = due_items[0]
        rev_active = start_review_interaction(
            db,
            learner_id=learner_id,
            session_id=session_id,
            phrase_id=top.phrase_id,
            prompt_kind=top.prompt_kind,
            context_family=top.context_family,
            scene_revision=rev,
            now=now_dt,
            ttl_s=900,
        )
        text = (
            f"🧠 *Spaced Retrieval ({top.context_family.value})*\n"
            f"{top.prompt_text}\n\n"
            f"_Reply with the natural American English phrase:_ "
        )
        buttons = [
            UIButton("💡 Hint", build_callback("rvh", rev_active.interaction_id, rev_active.revision)),
            UIButton("⏭ Skip", build_callback("rvs", rev_active.interaction_id, rev_active.revision)),
        ]
        return UIResponse(
            handled=True,
            text=text,
            buttons=buttons,
            interaction=rev_active,
        )

    # ------------------------------------------------------------------
    # 5. `rvh:<interaction_id>:<rev>` -> Progressive hint ladder (1 -> 2)
    # ------------------------------------------------------------------
    if op == "rvh":
        current = get_active_interaction(db, learner_id, now_dt)
        if current is None or current.interaction_id != obj_id or current.state != InteractionState.REVIEW_ACTIVE:
            return UIResponse(handled=True, text="⏱ This review item has expired.")
        cur_hint = int(current.payload.get("hint_level") or 0)
        next_hint_lvl = min(2, cur_hint + 1)
        phrase_id = str(current.payload.get("phrase_id") or current.object_ref)

        with db._connect() as conn:
            cat_row = conn.execute(
                "SELECT * FROM phrase_catalog WHERE phrase_id = ?", (phrase_id,)
            ).fetchone()
            cat_dict = dict(cat_row) if cat_row is not None else {"canonical_form": phrase_id}

        updated = transition(
            db,
            interaction_id=current.interaction_id,
            expected_revision=current.revision,
            new_state=InteractionState.REVIEW_ACTIVE,
            payload_patch={"hint_level": next_hint_lvl},
            now=now_dt,
            ttl_s=900,
        )
        active_ref = updated or current
        hint_text = build_retrieval_hint(cat_dict, next_hint_lvl)
        buttons: list[UIButton] = []
        if next_hint_lvl < 2:
            buttons.append(
                UIButton("💡 Hint 2", build_callback("rvh", active_ref.interaction_id, active_ref.revision))
            )
        buttons.append(
            UIButton("⏭ Skip", build_callback("rvs", active_ref.interaction_id, active_ref.revision))
        )
        return UIResponse(
            handled=True,
            text=hint_text,
            buttons=buttons,
            interaction=active_ref,
        )

    # ------------------------------------------------------------------
    # 6. `rvs:<interaction_id>:<rev>` -> Skip current review item
    # ------------------------------------------------------------------
    if op == "rvs":
        close_interaction(db, interaction_id=obj_id)
        return UIResponse(
            handled=True,
            text="⏭ Skipped review item.",
            buttons=[UIButton("➡️ Next Review", build_callback("rvn", 0, rev))],
            interaction=None,
        )

    # ------------------------------------------------------------------
    # 7. `ls:<event_seq>:<rev>` -> Listen button callback acknowledgement
    # ------------------------------------------------------------------
    if op == "ls":
        return UIResponse(
            handled=True,
            text=f"🎧 Audio requested for turn #{obj_id}.",
            buttons=[],
        )

    return UIResponse(handled=False, text="")


def handle_v3_text_turn(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    text: str,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> UIResponse:
    """Routes free-text learner messages per Section 5.4 (`route_text`)."""
    now_dt = now or SystemClock().now()
    route, active = route_text(db, learner_id, now_dt)

    if route == TextRoute.SCENE_TURN or active is None:
        return UIResponse(handled=False, text="", route=TextRoute.SCENE_TURN, interaction=None)

    idem = idempotency_key or f"v3_txt_{learner_id}_{active.interaction_id}_{active.revision}"

    if route == TextRoute.RECONSTRUCTION_ATTEMPT:
        diag_id = str(active.payload.get("diagnostic_id") or active.object_ref)
        target_alt = str(active.payload.get("alternative") or "")
        rc_res = record_reconstruction_attempt(
            db,
            learner_id=learner_id,
            session_id=session_id,
            interaction_id=active.interaction_id,
            expected_revision=active.revision,
            diagnostic_id=diag_id,
            target_alternative=target_alt,
            attempt_text=text,
            idempotency_key=idem,
            now=now_dt,
        )
        updated_active = get_active_interaction(db, learner_id, now_dt)
        rev_num = updated_active.revision if updated_active is not None else active.revision + 1
        if rc_res.outcome == "success":
            msg = f"✅ *Spot on!* You nailed the natural phrasing: \"{target_alt}\"\n_(Send your next message to continue the scene.)_"
            btns = [UIButton("🔙 Back to scene", build_callback("bk", active.interaction_id, rev_num))]
        else:
            msg = (
                f"🤏 *Close!* Target phrasing: \"{target_alt}\"\n"
                "Tap *Try again* to give it another shot, or just send your next scene message."
            )
            btns = [
                UIButton("🔄 Try again", build_callback("rc", active.interaction_id, rev_num)),
                UIButton("🔙 Back to scene", build_callback("bk", active.interaction_id, rev_num)),
            ]
        return UIResponse(
            handled=True,
            text=msg,
            buttons=btns,
            route=route,
            interaction=updated_active,
        )

    if route == TextRoute.REVIEW_ATTEMPT:
        phrase_id = str(active.payload.get("phrase_id") or active.object_ref)
        p_kind_str = str(active.payload.get("prompt_kind") or "meaning")
        c_fam_str = str(active.payload.get("context_family") or "general")
        hint_lvl = int(active.payload.get("hint_level") or 0)

        ret_res = record_retrieval_attempt(
            db,
            learner_id=learner_id,
            session_id=session_id,
            phrase_id=phrase_id,
            learner_text=text,
            origin=RetrievalOrigin.REVIEW,
            prompt_kind=PromptKind(p_kind_str) if p_kind_str in PromptKind._value2member_map_ else PromptKind.MEANING,
            context_family=ContextFamily(c_fam_str) if c_fam_str in ContextFamily._value2member_map_ else ContextFamily.GENERAL,
            hint_level=min(2, max(0, hint_lvl)),  # type: ignore[arg-type]
            due=True,
            interaction_id=active.interaction_id,
            expected_interaction_revision=active.revision,
            idempotency_key=idem,
            now=now_dt,
        )
        if ret_res.interaction_closed:
            if ret_res.evaluation.outcome in (RetrievalOutcome.SUCCESS, RetrievalOutcome.HINTED_SUCCESS):
                msg = (
                    f"✅ *Retrieved!* (`{ret_res.mastery.stage.value}` — "
                    f"next review in {int(ret_res.schedule.interval_days)}d)"
                )
            else:
                with db._connect() as conn:
                    c_row = conn.execute(
                        "SELECT canonical_form FROM phrase_catalog WHERE phrase_id = ?",
                        (phrase_id,),
                    ).fetchone()
                    canon = str(c_row[0]) if c_row else phrase_id
                msg = f"📌 Target phrase: *{canon}*\nWe'll revisit this soon to lock it in."
            return UIResponse(
                handled=True,
                text=msg,
                buttons=[UIButton("➡️ Next Review", build_callback("rvn", 0, 1))],
                route=route,
                interaction=None,
            )

        updated_active = get_active_interaction(db, learner_id, now_dt)
        rev_num = updated_active.revision if updated_active is not None else active.revision + 1
        btns = [
            UIButton("⏭ Skip", build_callback("rvs", active.interaction_id, rev_num)),
        ]
        if int((updated_active.payload if updated_active else {}).get("hint_level", 1)) < 2:
            btns.insert(
                0,
                UIButton("💡 Next Hint", build_callback("rvh", active.interaction_id, rev_num)),
            )
        return UIResponse(
            handled=True,
            text=ret_res.next_hint_text or "Try again with the hint above!",
            buttons=btns,
            route=route,
            interaction=updated_active,
        )

    return UIResponse(handled=False, text="", route=route, interaction=active)


def resolve_or_cache_audio(
    db: Any,
    *,
    script_text: str,
    voice_config: str = "en-US-AndrewMultilingualNeural",
    synthesize_fn: Callable[[str, str], tuple[bytes, str | None]] | None = None,
    send_by_file_id_fn: Callable[[str], bool] | None = None,
) -> AudioLookupResult:
    """Checks `audio_cache` first; invalidates stale `telegram_file_id` on rejection and re-synthesizes."""
    s_hash = _script_hash(script_text)
    cached_id = db.get_audio_file_id(s_hash, voice_config)

    if cached_id is not None:
        if send_by_file_id_fn is None or send_by_file_id_fn(cached_id):
            return AudioLookupResult(
                cache_hit=True,
                script_hash=s_hash,
                voice_config=voice_config,
                file_id=cached_id,
                audio_bytes=None,
            )
        # Telegram rejected the cached file_id -> drop it and fall through to synthesis
        db.drop_audio_file_id(s_hash, voice_config)

    if synthesize_fn is None:
        return AudioLookupResult(
            cache_hit=False,
            script_hash=s_hash,
            voice_config=voice_config,
            file_id=None,
            audio_bytes=None,
        )

    audio_bytes, new_file_id = synthesize_fn(script_text, voice_config)
    if new_file_id:
        db.put_audio_file_id(s_hash, voice_config, new_file_id)
    return AudioLookupResult(
        cache_hit=False,
        script_hash=s_hash,
        voice_config=voice_config,
        file_id=new_file_id,
        audio_bytes=audio_bytes,
    )


def build_scene_debrief(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    now: datetime | None = None,
    max_cards: int = 3,
) -> list[dict[str, Any]]:
    """Deduplicates deferred findings by `pattern_id` (`MERGED`) and delivers top debrief cards."""
    now_dt = now or SystemClock().now()
    now_iso = to_utc_iso(now_dt)

    with db._connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM feedback_item_projection
            WHERE learner_id = ? AND session_id = ? AND status = 'pending' AND delivery = 'deferred'
            ORDER BY priority_class ASC, priority_score DESC, turn_event_seq ASC
            """,
            (learner_id, session_id),
        ).fetchall()

        kept_by_pattern: dict[str, dict[str, Any]] = {}
        merged_pairs: list[tuple[str, str]] = []

        for r in rows:
            item = dict(r)
            pat = str(item["pattern_id"])
            if pat not in kept_by_pattern:
                kept_by_pattern[pat] = item
            else:
                winner_id = str(kept_by_pattern[pat]["diagnostic_id"])
                merged_pairs.append((winner_id, str(item["diagnostic_id"])))

        for winner_id, dup_id in merged_pairs:
            conn.execute(
                "UPDATE feedback_item_projection SET status = ?, merged_into = ? WHERE diagnostic_id = ?",
                (FeedbackStatus.MERGED.value, winner_id, dup_id),
            )

        selected = list(kept_by_pattern.values())[: int(max_cards)]
        for item in selected:
            conn.execute(
                "UPDATE feedback_item_projection SET status = ?, delivered_at_utc = ? WHERE diagnostic_id = ?",
                (FeedbackStatus.DELIVERED.value, now_iso, str(item["diagnostic_id"])),
            )
        conn.commit()

    for item in selected:
        diag_id = str(item["diagnostic_id"])
        db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload=FeedbackCardDeliveredV2(
                diagnostic_id=diag_id,
                card_id=f"debrief_{diag_id}",
                trigger="debrief",
            ),
            correlation_id=f"debrief_{session_id}",
            idempotency_key=f"debrief_{session_id}_{diag_id}",
            occurred_at_utc=now_iso,
            rebuild=False,
        )

    return selected


def build_v3_progress_report(
    db: Any,
    *,
    learner_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Builds the 5D Mastery, Stage Distribution & Due Review Report for the learner."""
    now_dt = now or SystemClock().now()
    due_items = select_due_review_items(db, learner_id=learner_id, now=now_dt, limit=20)

    with db._connect() as conn:
        conn.row_factory = sqlite3.Row
        psp_rows = conn.execute(
            "SELECT * FROM phrase_state_projection WHERE learner_id = ?",
            (learner_id,),
        ).fetchall()

    stage_counts: dict[str, int] = {st.value: 0 for st in PhraseStage}
    dims_accum: dict[str, list[float]] = {
        "dim_r": [],
        "dim_p": [],
        "dim_i": [],
        "dim_t": [],
        "dim_a": [],
        "mastery_index": [],
    }

    for r in psp_rows:
        rd = dict(r)
        st_val = str(rd.get("stage") or rd.get("acquisition_stage") or PhraseStage.UNSEEN.value)
        stage_counts[st_val] = stage_counts.get(st_val, 0) + 1
        for k in dims_accum:
            if rd.get(k) is not None:
                dims_accum[k].append(float(rd[k]))

    avg_dims = {
        k: round(sum(vals) / len(vals), 1) if vals else 0.0
        for k, vals in dims_accum.items()
    }

    summary_lines = [
        "📊 *Spoken English Lab V3.0 — 5D Mastery Report*",
        f"• *Overall Mastery Index:* `{avg_dims['mastery_index']}%`",
        f"• *Dimensions:* R `{avg_dims['dim_r']}` | P `{avg_dims['dim_p']}` | I `{avg_dims['dim_i']}` | T `{avg_dims['dim_t']}` | A `{avg_dims['dim_a']}`",
        f"• *Stable Phrases:* `{stage_counts.get(PhraseStage.STABLE.value, 0)}` | *Due for Review:* `{len(due_items)}`",
    ]
    return {
        "text": "\n".join(summary_lines),
        "stage_counts": stage_counts,
        "averages": avg_dims,
        "due_review_count": len(due_items),
    }
