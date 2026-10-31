"""Sprint S4 End-to-End Acceptance Tests: UI Callbacks, Reconstruction, Review, Audio Cache & Debrief."""
from __future__ import annotations

from pathlib import Path

import pytest

from clock import FakeClock
from database import EventSourcedDatabase
from interaction import InteractionState, TextRoute, build_callback, get_active_interaction
from orchestrator import AnalysisOutcome, TurnHandle, TurnOrchestrator
from schemas_v3 import FeedbackStatus, PhraseStage
from ui_v3 import (
    build_scene_debrief,
    build_v3_progress_report,
    handle_v3_callback,
    handle_v3_text_turn,
    resolve_or_cache_audio,
)
from validation import validate_analysis


def _make_orch(db: EventSourcedDatabase) -> TurnOrchestrator:
    try:
        orch = TurnOrchestrator(db)
    except TypeError:
        orch = object.__new__(TurnOrchestrator)
    orch.db = db
    return orch


def test_s4_upgrade_tap_to_reconstruction_and_auto_close_on_scene_text(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-31T19:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s4_ug_rc.db")
    db.migrate()
    session = db.get_or_create_active_session(1001)
    session_id = session["session_id"]

    res1, rev1 = db.accept_scene_turn(
        learner_id=1001,
        session_id=session_id,
        text="I discussed about the schedule with Sarah.",
        idempotency_key="s4_1001_1:learner",
        origin="scene",
        correlation_id="c_s4_1",
    )
    with db._connect() as conn:
        conn.execute(
            "UPDATE session_state_projection SET learner_turn_count = 4, turns_since_visible_card = 10 "
            "WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()

    val = validate_analysis(
        {
            "diagnostics": [
                {
                    "kind": "grammar",
                    "severity": "high",
                    "original_span": "discussed about",
                    "alternative": "discussed",
                    "alternative_status": "required",
                    "explanation_en": "Use 'discuss' without 'about'.",
                    "explanation_ar": "استخدم الفعل discuss مباشرة دون about.",
                    "pattern_id": "verb_prep_discuss",
                    "domain": "linguistic",
                    "classification": "error",
                    "repair_scope": "phrase_span",
                    "communicative_impact": 3,
                    "learning_value": 3,
                    "linguistic_confidence": 0.95,
                }
            ]
        },
        learner_text="I discussed about the schedule with Sarah.",
    )
    orch = _make_orch(db)
    handle = TurnHandle(
        turn_event_id=res1.event_id,
        reply_event_seq=42,
        turn_session_revision=rev1,
        correlation_id="c_s4_1",
        session_id=session_id,
        idempotency_base="s4_1001_1",
    )
    post = orch.apply_analysis_outcome(
        learner_id=1001,
        handle=handle,
        outcome=AnalysisOutcome(status="ok", validated=val),
        now=clock.now(),
    )
    assert post.attach_upgrade_button is True
    assert post.upgrade_callback == "ug:42:1"

    # 1. Learner taps `ug:42:1` -> opens UPGRADE_VIEW and marks feedback_item_projection delivered
    ug_resp = orch.handle_v3_callback(
        learner_id=1001,
        session_id=session_id,
        callback_data=post.upgrade_callback,
        now=clock.now(),
    )
    assert ug_resp.handled is True
    assert "discussed" in ug_resp.text
    assert ug_resp.interaction is not None
    assert ug_resp.interaction.state == InteractionState.UPGRADE_VIEW

    # 2. Learner taps `rc:<id>:1` -> opens RECONSTRUCTION_ACTIVE
    rc_cb = next(b.callback_data for b in ug_resp.buttons if b.callback_data.startswith("rc:"))
    rc_resp = orch.handle_v3_callback(
        learner_id=1001,
        session_id=session_id,
        callback_data=rc_cb,
        now=clock.now(),
    )
    assert rc_resp.handled is True
    assert rc_resp.interaction is not None
    assert rc_resp.interaction.state == InteractionState.RECONSTRUCTION_ACTIVE

    # 3. Learner types reconstruction sentence -> routed to RECONSTRUCTION_ATTEMPT -> RECONSTRUCTION_FEEDBACK
    turn_rc = orch.handle_v3_text_turn(
        learner_id=1001,
        session_id=session_id,
        text="I discussed the schedule with Sarah.",
        now=clock.now(),
    )
    assert turn_rc.handled is True
    assert turn_rc.route == TextRoute.RECONSTRUCTION_ATTEMPT
    assert "Spot on" in turn_rc.text
    assert turn_rc.interaction is not None
    assert turn_rc.interaction.state == InteractionState.RECONSTRUCTION_FEEDBACK

    # 4. Learner sends their next normal scene message while in RECONSTRUCTION_FEEDBACK
    # -> closes active_interaction automatically and routes as SCENE_TURN!
    turn_scene = orch.handle_v3_text_turn(
        learner_id=1001,
        session_id=session_id,
        text="Anyway, what do you think about Friday?",
        now=clock.now(),
    )
    assert turn_scene.handled is False
    assert turn_scene.route == TextRoute.SCENE_TURN
    assert get_active_interaction(db, 1001, clock.now()) is None


def test_s4_spaced_review_callbacks_hint_progression_and_completion(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-31T10:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s4_review.db")
    db.migrate()
    session = db.get_or_create_active_session(1002)
    session_id = session["session_id"]
    orch = _make_orch(db)

    # Seed a due item in phrase_state_projection using schema-aware recompute_phrase_state
    from mastery import recompute_phrase_state
    with db._connect() as conn:
        recompute_phrase_state(
            conn,
            learner_id=1002,
            phrase_id="surface_hidden_tradeoff",
            now=clock.now(),
            schedule_step=0,
            next_review_at_utc="2026-10-31T09:00:00Z",
        )
        conn.commit()

    # 1. Tap `rvn:0:1` -> opens REVIEW_ACTIVE for 'surface_hidden_tradeoff'
    rv_start = orch.handle_v3_callback(
        learner_id=1002,
        session_id=session_id,
        callback_data=build_callback("rvn", 0, 1),
        now=clock.now(),
    )
    assert rv_start.handled is True
    assert rv_start.interaction is not None
    assert rv_start.interaction.state == InteractionState.REVIEW_ACTIVE

    # 2. Tap `rvh` -> Hint 1
    hint1_cb = next(b.callback_data for b in rv_start.buttons if b.callback_data.startswith("rvh:"))
    rv_h1 = orch.handle_v3_callback(
        learner_id=1002,
        session_id=session_id,
        callback_data=hint1_cb,
        now=clock.now(),
    )
    assert "Hint 1" in rv_h1.text
    assert rv_h1.interaction is not None and rv_h1.interaction.payload["hint_level"] == 1

    # 3. Learner types the target phrase -> Hinted Success -> closes REVIEW_ACTIVE
    rv_attempt = orch.handle_v3_text_turn(
        learner_id=1002,
        session_id=session_id,
        text="The flip side of that is a much longer commute.",
        now=clock.now(),
    )
    assert rv_attempt.handled is True
    assert rv_attempt.route == TextRoute.REVIEW_ATTEMPT
    assert "Retrieved!" in rv_attempt.text
    assert get_active_interaction(db, 1002, clock.now()) is None


def test_s4_audio_cache_hit_miss_invalidation_and_auto_voice(tmp_path: Path) -> None:
    db = EventSourcedDatabase(tmp_path / "s4_audio.db")
    db.migrate()

    # Auto-voice toggle persistence
    assert db.get_auto_voice(1003) is False
    db.set_auto_voice(1003, True)
    assert db.get_auto_voice(1003) is True

    synth_calls = 0

    def fake_synth(text: str, voice: str) -> tuple[bytes, str | None]:
        nonlocal synth_calls
        synth_calls += 1
        return b"FAKE_MP3_BYTES", f"tg_file_id_{synth_calls}"

    # 1. Cache miss -> synthesizes and stores tg_file_id_1
    r1 = resolve_or_cache_audio(
        db,
        script_text="Fair point—let's revisit the timeline tomorrow.",
        synthesize_fn=fake_synth,
    )
    assert r1.cache_hit is False
    assert r1.file_id == "tg_file_id_1"
    assert synth_calls == 1

    # 2. Cache hit -> returns tg_file_id_1 without calling fake_synth
    r2 = resolve_or_cache_audio(
        db,
        script_text="Fair point—let's revisit the timeline tomorrow.",
        synthesize_fn=fake_synth,
        send_by_file_id_fn=lambda fid: True,
    )
    assert r2.cache_hit is True
    assert r2.file_id == "tg_file_id_1"
    assert synth_calls == 1

    # 3. Telegram rejects stale file_id -> invalidates cache and re-synthesizes tg_file_id_2
    r3 = resolve_or_cache_audio(
        db,
        script_text="Fair point—let's revisit the timeline tomorrow.",
        synthesize_fn=fake_synth,
        send_by_file_id_fn=lambda fid: False,
    )
    assert r3.cache_hit is False
    assert r3.file_id == "tg_file_id_2"
    assert synth_calls == 2


def test_s4_scene_debrief_pattern_deduplication_and_progress_report(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-31T20:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s4_debrief.db")
    db.migrate()
    session = db.get_or_create_active_session(1004)
    session_id = session["session_id"]

    with db._connect() as conn:
        # Insert two pending deferred items with the SAME pattern_id -> lower score must be MERGED
        for diag_id, seq, score, span in (
            ("dg_top00001", 10, 82.0, "discussed about the plan"),
            ("dg_dup00002", 14, 64.0, "discussed about the cost"),
        ):
            conn.execute(
                """
                INSERT INTO feedback_item_projection (
                    diagnostic_id, learner_id, session_id, turn_event_seq,
                    pattern_id, context_family, domain, classification,
                    repair_scope, kind, severity, original_span, alternative,
                    explanation_en, explanation_ar, priority_class, priority_score,
                    delivery, status, created_at_utc
                ) VALUES (?, 1004, ?, ?, 'verb_prep_discuss', 'workplace', 'linguistic', 'error',
                          'phrase_span', 'grammar', 'medium', ?, 'discussed', 'Drop about', 'احذف about',
                          1, ?, 'deferred', 'pending', '2026-10-31T19:50:00Z')
                """,
                (diag_id, session_id, seq, span, score),
            )
        conn.commit()

    orch = _make_orch(db)
    cards = orch.build_scene_debrief(learner_id=1004, session_id=session_id, now=clock.now())
    assert len(cards) == 1
    assert cards[0]["diagnostic_id"] == "dg_top00001"

    with db._connect() as conn:
        dup_row = conn.execute(
            "SELECT status, merged_into FROM feedback_item_projection WHERE diagnostic_id = 'dg_dup00002'"
        ).fetchone()
        assert dup_row["status"] == FeedbackStatus.MERGED.value
        assert dup_row["merged_into"] == "dg_top00001"

    report = orch.build_v3_progress_report(learner_id=1004, now=clock.now())
    assert "5D Mastery Report" in report["text"]
    assert PhraseStage.STABLE.value in report["stage_counts"]
