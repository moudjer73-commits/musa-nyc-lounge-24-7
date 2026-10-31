"""Sprint S1 Durability & Migration Acceptance Tests (S0 Contracts, Section 8)."""
from __future__ import annotations

from schemas_v3 import RepairScope

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clock import FakeClock, from_utc_iso, to_utc_iso
from database import EventSourcedDatabase
from db_v3_migrations import (
    EventSpec,
    V1_EVENT_TYPES,
    V3_EVENT_TYPES,
    _event_log_fingerprint,
    rebuild_all_projections,
    run_migrations,
    upcast,
)
from interaction import (
    Classification,
    ComprehensionStatus,
    Delivery,
    DiagnosticV2,
    Domain,
    InteractionObject,
    InteractionState,
    POSignal,
    PolicyInputs,
    PriorityClass,
    TextRoute,
    build_callback,
    close_interaction,
    get_active_interaction,
    open_interaction,
    parse_callback,
    priority_score,
    resolve_interaction_policy,
    route_text,
    transition,
)
from tools import state_snapshot


def _build_synthetic_v2_db(db_path: Path) -> EventSourcedDatabase:
    """Builds a pre-M001 v2 database populated with all 14 v1 event types."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE learners (
            learner_id INTEGER PRIMARY KEY,
            display_name TEXT,
            preferred_register TEXT NOT NULL DEFAULT 'casual',
            directness_level TEXT NOT NULL DEFAULT 'balanced',
            feedback_preference TEXT NOT NULL DEFAULT 'active',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE sessions (
            session_id TEXT NOT NULL,
            learner_id INTEGER NOT NULL,
            training_mode TEXT NOT NULL DEFAULT 'open_scene',
            scenario_title TEXT,
            counterpart_role TEXT,
            register_profile TEXT NOT NULL DEFAULT 'casual',
            directness_level TEXT NOT NULL DEFAULT 'balanced',
            status TEXT NOT NULL DEFAULT 'active',
            started_at_utc TEXT NOT NULL,
            ended_at_utc TEXT,
            PRIMARY KEY (session_id, learner_id),
            FOREIGN KEY (learner_id) REFERENCES learners(learner_id)
        );
        CREATE TABLE event_log (
            event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            learner_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'session_started', 'session_ended', 'learner_message_received',
                'analyst_diagnosis_recorded', 'policy_decision_recorded',
                'responder_reply_delivered', 'feedback_card_delivered',
                'micro_workout_scheduled', 'micro_workout_completed',
                'phrase_exposure_recorded', 'phrase_production_assessed',
                'diagnostic_retracted', 'phrase_evidence_retracted',
                'review_completed'
            )),
            occurred_at_utc TEXT NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            schema_version TEXT NOT NULL DEFAULT '1.0',
            model_version TEXT,
            rubric_version TEXT DEFAULT 'v1.0',
            correlation_id TEXT NOT NULL,
            causation_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            FOREIGN KEY (session_id, learner_id) REFERENCES sessions(session_id, learner_id)
        );
        CREATE TRIGGER event_log_no_update BEFORE UPDATE ON event_log
        BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
        CREATE TRIGGER event_log_no_delete BEFORE DELETE ON event_log
        BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
        CREATE TABLE phrase_catalog (
            phrase_id TEXT PRIMARY KEY,
            canonical_form TEXT NOT NULL,
            communicative_intention TEXT NOT NULL,
            construction_pattern TEXT,
            register_profiles_json TEXT NOT NULL DEFAULT '["casual","neutral"]',
            applicability TEXT NOT NULL DEFAULT 'general',
            variety_tags_json TEXT NOT NULL DEFAULT '["US"]'
        );
        CREATE TABLE phrase_state_projection (
            learner_id INTEGER NOT NULL,
            phrase_id TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'Unseen',
            exposure_count INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            unaided_valid_uses INTEGER NOT NULL DEFAULT 0,
            distinct_valid_registers_json TEXT NOT NULL DEFAULT '[]',
            first_unaided_at_utc TEXT,
            last_unaided_at_utc TEXT,
            last_seen_at_utc TEXT,
            next_review_at_utc TEXT,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (learner_id, phrase_id)
        );
        CREATE TABLE error_pattern_projection (
            learner_id INTEGER NOT NULL,
            pattern_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 0,
            retracted_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at_utc TEXT NOT NULL,
            last_seen_at_utc TEXT NOT NULL,
            last_example_span TEXT,
            last_alternative TEXT,
            PRIMARY KEY (learner_id, pattern_id)
        );
        CREATE TABLE session_state_projection (
            session_id TEXT PRIMARY KEY,
            learner_id INTEGER NOT NULL,
            learner_turn_count INTEGER NOT NULL DEFAULT 0,
            turns_since_visible_card INTEGER NOT NULL DEFAULT 99,
            active_workout_json TEXT,
            updated_at_utc TEXT NOT NULL
        );
        """
    )
    now = "2026-01-01T12:00:00Z"
    conn.execute(
        "INSERT INTO learners (learner_id, display_name, created_at_utc, updated_at_utc) VALUES (101, 'Musa', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, learner_id, started_at_utc) VALUES ('s_golden', 101, ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO phrase_catalog (phrase_id, canonical_form, communicative_intention) "
        "VALUES ('push_back_softly', 'I see where you are coming from, but', 'polite disagreement')"
    )

    sample_payloads = {
        "session_started": {"training_mode": "open_scene"},
        "learner_message_received": {"text": "I discussed about the budget.", "is_voice": False},
        "analyst_diagnosis_recorded": {
            "diagnostics": [
                {
                    "kind": "grammar",
                    "severity": "medium",
                    "original_span": "discussed about",
                    "alternative": "discussed",
                    "pattern_id": "verb_prep_discuss",
                    "confidence_score": 0.92,
                },
                {"kind": "insufficient_context", "original_span": "x", "alternative": "y"},
            ]
        },
        "policy_decision_recorded": {"intervention": "card", "selected_diagnostic_id": "dg_1"},
        "responder_reply_delivered": {"reply_text": "Fair point—what part of the budget worries you?"},
        "feedback_card_delivered": {"card_id": "c1", "diagnostic_id": "dg_1"},
        "micro_workout_scheduled": {"phrase_id": "push_back_softly"},
        "micro_workout_completed": {"phrase_id": "push_back_softly", "success": True},
        "phrase_exposure_recorded": {"phrase_id": "push_back_softly"},
        "phrase_production_assessed": {
            "phrase_id": "push_back_softly",
            "production_mode": "independent_production",
            "valid": True,
            "register_profile": "casual",
        },
        "diagnostic_retracted": {"diagnostic_id": "dg_none"},
        "phrase_evidence_retracted": {"source_event_id": "ev_none"},
        "review_completed": {"reviewed_count": 1},
        "session_ended": {"reason": "user"},
    }
    for idx, ev_type in enumerate(V1_EVENT_TYPES, start=1):
        conn.execute(
            """
            INSERT INTO event_log (
                event_id, learner_id, session_id, event_type,
                occurred_at_utc, recorded_at_utc, schema_version,
                correlation_id, idempotency_key, payload_json
            ) VALUES (?, 101, 's_golden', ?, ?, ?, '1.0', ?, ?, ?)
            """,
            (
                f"ev_golden_{idx:02d}",
                ev_type,
                now,
                now,
                f"corr_{idx}",
                f"idem_{idx}",
                json.dumps(sample_payloads[ev_type], sort_keys=True),
            ),
        )
    conn.commit()
    conn.close()
    return EventSourcedDatabase(db_path)


def test_s1_golden_migration_and_append_only_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "golden_v2.db"
    _build_synthetic_v2_db(db_path)

    conn = sqlite3.connect(str(db_path))
    before_count, before_max, before_sha = _event_log_fingerprint(conn, "event_log")
    assert before_count == 14
    conn.close()

    applied = run_migrations(db_path)
    assert applied[:3] == [1, 2, 3]

    # Idempotence: second run applies nothing
    assert run_migrations(db_path) == []

    conn = sqlite3.connect(str(db_path))
    after_count, after_max, after_sha = _event_log_fingerprint(conn, "event_log")
    assert (after_count, after_max, after_sha) == (before_count, before_max, before_sha)

    # UPDATE and DELETE on event_log must still raise
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE event_log SET payload_json='{}' WHERE event_seq=1")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM event_log WHERE event_seq=1")

    # All 3 new V3 event types exist in event_types
    stored_types = {r[0] for r in conn.execute("SELECT event_type FROM event_types").fetchall()}
    for t in V1_EVENT_TYPES + V3_EVENT_TYPES:
        assert t in stored_types

    # Consecutive projection rebuilds are deterministic
    rebuild_all_projections(conn)
    snap1 = conn.execute("SELECT * FROM phrase_evidence ORDER BY source_event_id, dimension").fetchall()
    rebuild_all_projections(conn)
    snap2 = conn.execute("SELECT * FROM phrase_evidence ORDER BY source_event_id, dimension").fetchall()
    assert snap1 == snap2
    assert len(snap1) == 2  # independent_production -> production + independence
    conn.close()


def test_s1_upcast_all_14_v1_event_types(tmp_path: Path) -> None:
    db_path = tmp_path / "upcast_v2.db"
    _build_synthetic_v2_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM event_log ORDER BY event_seq ASC").fetchall()
    assert len(rows) == 14
    for r in rows:
        up = upcast(r)
        assert isinstance(up["payload"], dict)
        if r["event_type"] == "analyst_diagnosis_recorded":
            diags = up["payload"]["diagnostics"]
            assert len(diags) == 1  # insufficient_context filtered out
            assert diags[0]["domain"] == "linguistic"
            assert diags[0]["classification"] == "error"
            assert diags[0]["diagnostic_id"].startswith("dg_")
            assert up["payload"]["p0"] == {"candidate": False, "line": None}
        elif r["event_type"] == "policy_decision_recorded":
            assert up["payload"]["delivery"] == "end_of_turn"
            assert up["payload"]["priority_class"] == 1
        elif r["event_type"] == "responder_reply_delivered":
            assert up["payload"]["comprehension_status"] == "ok"
        elif r["event_type"] == "learner_message_received":
            assert up["payload"]["origin"] == "scene"
            assert up["payload"]["input_modality"] == "text"
    conn.close()


def test_s1_active_interaction_ttl_routing_and_callbacks(tmp_path: Path) -> None:
    db_path = tmp_path / "inter.db"
    _build_synthetic_v2_db(db_path)
    run_migrations(db_path)

    clock = FakeClock("2026-06-01T10:00:00Z")
    active = open_interaction(
        sqlite3.connect(str(db_path)),
        learner_id=101,
        session_id="s_golden",
        object_type=InteractionObject.UPGRADE,
        object_ref="dg_12345678",
        state=InteractionState.UPGRADE_VIEW,
        payload={"card": "test"},
        scene_revision=2,
        now=clock.now(),
        ttl_s=900,
    )
    assert active.revision == 1

    # Survives fresh connection
    loaded = get_active_interaction(sqlite3.connect(str(db_path)), 101, clock.now())
    assert loaded is not None and loaded.interaction_id == active.interaction_id

    # Learner text in UPGRADE_VIEW closes row and routes as SCENE_TURN
    route, row = route_text(sqlite3.connect(str(db_path)), 101, clock.now())
    assert route == TextRoute.SCENE_TURN and row is None
    assert get_active_interaction(sqlite3.connect(str(db_path)), 101, clock.now()) is None

    # Open RECONSTRUCTION_ACTIVE and test transition + TTL expiry
    rec = open_interaction(
        sqlite3.connect(str(db_path)),
        learner_id=101,
        session_id="s_golden",
        object_type=InteractionObject.RECONSTRUCTION,
        object_ref="dg_12345678",
        state=InteractionState.RECONSTRUCTION_ACTIVE,
        payload={},
        scene_revision=2,
        now=clock.now(),
        ttl_s=900,
    )
    route2, row2 = route_text(sqlite3.connect(str(db_path)), 101, clock.now())
    assert route2 == TextRoute.RECONSTRUCTION_ATTEMPT and row2 is not None

    updated = transition(
        sqlite3.connect(str(db_path)),
        interaction_id=rec.interaction_id,
        expected_revision=1,
        new_state=InteractionState.RECONSTRUCTION_FEEDBACK,
        payload_patch={"attempt": "I discussed the budget."},
        now=clock.now(),
    )
    assert updated is not None and updated.revision == 2

    # Advance FakeClock past TTL (16 minutes) -> lazy expiry returns None
    clock.advance(minutes=16)
    assert get_active_interaction(sqlite3.connect(str(db_path)), 101, clock.now()) is None

    # Callback wire format <= 32 bytes
    cb = build_callback("rc", 123, 4)
    assert cb == "rc:123:4"
    parsed = parse_callback(cb)
    assert parsed is not None and (parsed.op, parsed.obj_id, parsed.rev) == ("rc", 123, 4)
    assert parse_callback("invalid:op:1") is None
    assert parse_callback("rc:" + "9" * 40 + ":1") is None


def test_s1_cas_and_scene_turn_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "cas.db"
    db = _build_synthetic_v2_db(db_path)
    db.migrate()

    res1, rev1 = db.accept_scene_turn(
        learner_id=101,
        session_id="s_golden",
        text="Hello Alex",
        idempotency_key="tg_msg_101_1:learner",
        origin="scene",
        correlation_id="c1",
    )
    assert res1.inserted is True and rev1 == 1

    # Duplicate idempotency_key does not bump revision again
    res_dup, rev_dup = db.accept_scene_turn(
        learner_id=101,
        session_id="s_golden",
        text="Hello Alex",
        idempotency_key="tg_msg_101_1:learner",
        origin="scene",
        correlation_id="c1",
    )
    assert res_dup.inserted is False and rev_dup == 1

    # CAS with stale revision (0) fails and writes nothing
    ok_stale = db.append_events_if_revision(
        "s_golden",
        0,
        [
            EventSpec(
                learner_id=101,
                session_id="s_golden",
                event_type="analysis_skipped",
                payload={"turn_event_id": res1.event_id, "reason": "trivial"},
                correlation_id="c1",
                idempotency_key="tg_msg_101_1:analysis",
            )
        ],
    )
    assert ok_stale is False

    # CAS with current revision (1) succeeds
    ok_curr = db.append_events_if_revision(
        "s_golden",
        1,
        [
            EventSpec(
                learner_id=101,
                session_id="s_golden",
                event_type="analysis_skipped",
                payload={"turn_event_id": res1.event_id, "reason": "trivial"},
                correlation_id="c1",
                idempotency_key="tg_msg_101_1:analysis",
            )
        ],
    )
    assert ok_curr is True


def test_s1_state_snapshot_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "snap.db"
    db = _build_synthetic_v2_db(db_path)
    db.migrate()

    # Missing passphrase exits 1
    monkeypatch.delenv("SNAPSHOT_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        state_snapshot.cmd_restore(db_path)
    assert exc_info.value.code == 1

    # Empty release with ALLOW_EMPTY_DB=0 returns 1
    monkeypatch.setenv("SNAPSHOT_PASSPHRASE", "test-secret-passphrase-123")
    monkeypatch.setenv("ALLOW_EMPTY_DB", "0")
    fake_404 = MagicMock(status_code=404)
    with patch("tools.state_snapshot._request_with_retry", return_value=fake_404):
        assert state_snapshot.cmd_restore(db_path) == 1
