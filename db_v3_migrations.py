"""Spoken English Lab V3.0 — Migrations M001-M003 & Section 6 DB Extensions."""
from __future__ import annotations

from catalog_v3 import apply_m004_seed_catalog as _apply_m004


import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from clock import Clock, SystemClock, to_utc_iso
from interaction import ACTIVE_INTERACTION_DDL
from schemas_v3 import LearnerMessageV2, TurnOrigin, make_diagnostic_id, upcast

__all__ = [
    "V1_EVENT_TYPES",
    "V3_EVENT_TYPES",
    "AppendResult",
    "EventSpec",
    "Migration",
    "MIGRATIONS",
    "LATEST_VERSION",
    "run_migrations",
    "rebuild_all_projections",
    "V3DatabaseMixin",
    "V3EventSourcedDatabase",
    "upcast",
    "make_diagnostic_id",
]

V1_EVENT_TYPES: tuple[str, ...] = (
    "session_started",
    "session_ended",
    "learner_message_received",
    "analyst_diagnosis_recorded",
    "policy_decision_recorded",
    "responder_reply_delivered",
    "feedback_card_delivered",
    "micro_workout_scheduled",
    "micro_workout_completed",
    "phrase_exposure_recorded",
    "phrase_production_assessed",
    "diagnostic_retracted",
    "phrase_evidence_retracted",
    "review_completed",
)

V3_EVENT_TYPES: tuple[str, ...] = (
    "reconstruction_attempted",
    "retrieval_attempted",
    "analysis_skipped",
)


@dataclass(frozen=True)
class AppendResult:
    inserted: bool
    event_id: str
    event_seq: int
    session_revision: int | None = None


@dataclass(frozen=True)
class EventSpec:
    learner_id: int
    session_id: str
    event_type: str
    payload: Any
    correlation_id: str
    idempotency_key: str
    causation_id: str | None = None
    schema_version: str = "2.0"
    rubric_version: str = "v2.0"
    model_version: str | None = None
    occurred_at_utc: str | None = None


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection, str], None]
    checksum: str
    needs_fk_off: bool = False


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(r[1]) for r in rows}


def _event_log_fingerprint(conn: sqlite3.Connection, table: str = "event_log") -> tuple[int, int, str]:
    h = hashlib.sha256()
    count = 0
    max_seq = 0
    cur = conn.execute(
        f'SELECT event_seq, event_id, payload_json FROM "{table}" ORDER BY event_seq ASC'
    )
    for seq, ev_id, payload_json in cur:
        s_int = int(seq)
        h.update(f"{s_int}||{ev_id}||{payload_json}\n".encode("utf-8"))
        count += 1
        if s_int > max_seq:
            max_seq = s_int
    return count, max_seq, h.hexdigest()


def _apply_m001(conn: sqlite3.Connection, now_iso: str) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_types (
            event_type TEXT PRIMARY KEY,
            introduced_in INTEGER NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT OR IGNORE INTO event_types (event_type, introduced_in) VALUES (?, 0)",
        [(t,) for t in V1_EVENT_TYPES],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO event_types (event_type, introduced_in) VALUES (?, 1)",
        [(t,) for t in V3_EVENT_TYPES],
    )
    if _table_exists(conn, "event_log"):
        conn.execute(
            "INSERT OR IGNORE INTO event_types (event_type, introduced_in) "
            "SELECT DISTINCT event_type, 0 FROM event_log"
        )

    before_count, before_max_seq, before_hash = _event_log_fingerprint(conn, "event_log")

    conn.execute("DROP TABLE IF EXISTS event_log_new")
    conn.execute(
        """
        CREATE TABLE event_log_new (
            event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            learner_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL REFERENCES event_types(event_type),
            occurred_at_utc TEXT NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            schema_version TEXT NOT NULL DEFAULT '1.0',
            model_version TEXT,
            rubric_version TEXT DEFAULT 'v1.0',
            correlation_id TEXT NOT NULL,
            causation_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            FOREIGN KEY (session_id, learner_id) REFERENCES sessions(session_id, learner_id)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO event_log_new (
            event_seq, event_id, learner_id, session_id, event_type,
            occurred_at_utc, recorded_at_utc, schema_version, model_version,
            rubric_version, correlation_id, causation_id, idempotency_key, payload_json
        )
        SELECT
            event_seq, event_id, learner_id, session_id, event_type,
            occurred_at_utc, recorded_at_utc, schema_version, model_version,
            rubric_version, correlation_id, causation_id, idempotency_key, payload_json
        FROM event_log ORDER BY event_seq ASC;
        """
    )
    conn.execute("DROP TABLE event_log")
    conn.execute("ALTER TABLE event_log_new RENAME TO event_log")

    conn.execute(
        """
        CREATE TRIGGER event_log_no_update BEFORE UPDATE ON event_log
        BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER event_log_no_delete BEFORE DELETE ON event_log
        BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_log_learner_time "
        "ON event_log(learner_id, occurred_at_utc, event_seq);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_log_session_time "
        "ON event_log(session_id, occurred_at_utc, event_seq);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_log_type_learner "
        "ON event_log(event_type, learner_id, event_seq);"
    )

    after_count, after_max_seq, after_hash = _event_log_fingerprint(conn, "event_log")
    if after_count != before_count:
        raise RuntimeError(f"M001 row count mismatch: {before_count} != {after_count}")
    if after_max_seq != before_max_seq:
        raise RuntimeError(f"M001 max(event_seq) mismatch: {before_max_seq} != {after_max_seq}")
    if after_hash != before_hash:
        raise RuntimeError("M001 SHA-256 digest mismatch after rebuild")

    # Verify append-only triggers raise on UPDATE and DELETE
    conn.execute("SAVEPOINT sp_verify_triggers")
    try:
        if after_count == 0:
            l_cols = _table_columns(conn, "learners")
            if "updated_at_utc" in l_cols and "created_at_utc" in l_cols:
                conn.execute(
                    "INSERT INTO learners (learner_id, created_at_utc, updated_at_utc) VALUES (-999, ?, ?) "
                    "ON CONFLICT(learner_id) DO NOTHING",
                    (now_iso, now_iso),
                )
            elif "created_at_utc" in l_cols:
                conn.execute(
                    "INSERT INTO learners (learner_id, created_at_utc) VALUES (-999, ?) "
                    "ON CONFLICT(learner_id) DO NOTHING",
                    (now_iso,),
                )
            else:
                conn.execute(
                    "INSERT INTO learners (learner_id) VALUES (-999) "
                    "ON CONFLICT(learner_id) DO NOTHING"
                )
            conn.execute(
                "INSERT INTO sessions (session_id, learner_id, started_at_utc) VALUES ('__probe__', -999, ?) "
                "ON CONFLICT(session_id, learner_id) DO NOTHING",
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO event_log (
                    event_id, learner_id, session_id, event_type, occurred_at_utc,
                    recorded_at_utc, schema_version, correlation_id, idempotency_key, payload_json
                ) VALUES ('ev_probe', -999, '__probe__', 'session_started', ?, ?, '1.0', 'c', 'k_probe', '{}')
                """,
                (now_iso, now_iso),
            )
        for sql_Check in (
            "UPDATE event_log SET payload_json = '{}' WHERE rowid = (SELECT MIN(rowid) FROM event_log)",
            "DELETE FROM event_log WHERE rowid = (SELECT MIN(rowid) FROM event_log)",
        ):
            raised = False
            try:
                conn.execute(sql_Check)
            except sqlite3.DatabaseError:
                raised = True
            if not raised:
                raise RuntimeError(f"Trigger failed to block: {sql_Check}")
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT sp_verify_triggers")
        conn.execute("RELEASE SAVEPOINT sp_verify_triggers")


_M002_ALTERS: tuple[tuple[str, str, str], ...] = (
    ("sessions", "session_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "scene_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "primary_context_family", "TEXT NOT NULL DEFAULT 'general'"),
    ("sessions", "pending_clarification_json", "TEXT"),
    ("session_state_projection", "learner_turn_count", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state_projection", "turns_since_visible_card", "INTEGER NOT NULL DEFAULT 99"),
    ("session_state_projection", "cards_in_scene", "INTEGER NOT NULL DEFAULT 0"),
    ("session_state_projection", "turns_since_p0", "INTEGER NOT NULL DEFAULT 99"),
    ("phrase_catalog", "catalog_version", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("phrase_catalog", "lexical_structure_type", "TEXT"),
    ("phrase_catalog", "context_families_json", "TEXT NOT NULL DEFAULT '[\"general\"]'"),
    ("phrase_catalog", "subskill_id", "TEXT"),
    ("phrase_catalog", "arabic_gloss", "TEXT"),
    ("phrase_catalog", "acceptable_forms_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("phrase_catalog", "meaning_prompt", "TEXT"),
    ("phrase_catalog", "situation_prompt", "TEXT"),
    ("phrase_catalog", "function_prompt", "TEXT"),
    ("phrase_state_projection", "stage", "TEXT NOT NULL DEFAULT 'Unseen'"),
    ("phrase_state_projection", "attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "success_count", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "independent_success_count", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "spontaneous_success_count", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "delayed_success_count", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "max_delayed_gap_days", "REAL NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "context_families_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("phrase_state_projection", "home_context_family", "TEXT"),
    ("phrase_state_projection", "dim_r", "REAL"),
    ("phrase_state_projection", "dim_p", "REAL"),
    ("phrase_state_projection", "dim_i", "REAL"),
    ("phrase_state_projection", "dim_t", "REAL"),
    ("phrase_state_projection", "dim_a", "REAL"),
    ("phrase_state_projection", "mastery_index", "REAL"),
    ("phrase_state_projection", "schedule_step", "INTEGER NOT NULL DEFAULT 0"),
    ("phrase_state_projection", "last_outcome", "TEXT"),
    ("phrase_state_projection", "last_success_at_utc", "TEXT"),
    ("phrase_state_projection", "unresolved_critical_errors", "INTEGER NOT NULL DEFAULT 0"),
)


def _apply_m002(conn: sqlite3.Connection, now_iso: str) -> None:
    for table, col, col_def in _M002_ALTERS:
        cols = _table_columns(conn, table)
        if col not in cols:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {col_def}')

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_psp_due
        ON phrase_state_projection(learner_id, next_review_at_utc)
        WHERE next_review_at_utc IS NOT NULL;
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS phrase_evidence (
            source_event_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            learner_id INTEGER NOT NULL REFERENCES learners(learner_id),
            phrase_id TEXT NOT NULL REFERENCES phrase_catalog(phrase_id),
            score REAL NOT NULL CHECK(score >= 0 AND score <= 100),
            weight REAL NOT NULL CHECK(weight > 0),
            context_family TEXT NOT NULL DEFAULT 'general',
            origin TEXT NOT NULL,
            hint_level INTEGER NOT NULL DEFAULT 0,
            is_delayed INTEGER NOT NULL DEFAULT 0,
            gap_days REAL,
            echo INTEGER NOT NULL DEFAULT 0,
            occurred_at_utc TEXT NOT NULL,
            retracted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (source_event_id, dimension)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_phrase_evidence_lp "
        "ON phrase_evidence(learner_id, phrase_id, occurred_at_utc);"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_item_projection (
            diagnostic_id TEXT PRIMARY KEY,
            learner_id INTEGER NOT NULL REFERENCES learners(learner_id),
            session_id TEXT NOT NULL,
            turn_event_seq INTEGER NOT NULL,
            pattern_id TEXT NOT NULL,
            context_family TEXT NOT NULL DEFAULT 'general',
            domain TEXT NOT NULL,
            classification TEXT NOT NULL,
            repair_scope TEXT NOT NULL,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            original_span TEXT NOT NULL,
            alternative TEXT NOT NULL,
            explanation_en TEXT,
            explanation_ar TEXT,
            priority_class INTEGER NOT NULL,
            priority_score REAL NOT NULL,
            delivery TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            merged_into TEXT,
            created_at_utc TEXT NOT NULL,
            delivered_at_utc TEXT
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fip_queue "
        "ON feedback_item_projection(learner_id, status, priority_score DESC);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fip_pattern "
        "ON feedback_item_projection(learner_id, pattern_id, created_at_utc);"
    )
    conn.executescript(ACTIVE_INTERACTION_DDL)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learner_settings (
            learner_id INTEGER PRIMARY KEY REFERENCES learners(learner_id),
            auto_voice INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO learner_settings (learner_id, auto_voice, updated_at_utc) "
        "SELECT learner_id, 0, ? FROM learners;",
        (now_iso,),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_cache (
            script_hash TEXT NOT NULL,
            voice_config TEXT NOT NULL,
            telegram_file_id TEXT NOT NULL,
            telegram_file_unique_id TEXT,
            duration_s REAL,
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY (script_hash, voice_config)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS competency_subskills (
            subskill_id TEXT PRIMARY KEY,
            competency_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            catalog_version TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def rebuild_all_projections(conn: sqlite3.Connection, db_instance: Any = None) -> None:
    """Rebuilds v1 and V3 projections through upcast() deterministically (Section 4.3 & 5.5)."""
    conn.row_factory = sqlite3.Row
    for tbl in (
        "phrase_evidence",
        "feedback_item_projection",
        "phrase_state_projection",
        "error_pattern_projection",
        "session_state_projection",
    ):
        if _table_exists(conn, tbl):
            conn.execute(f'DELETE FROM "{tbl}"')

    # If the host EventSourcedDatabase has its v1 projection replay logic, run it first so
    # all v1-visible columns on phrase_state_projection, error_pattern_projection, and
    # session_state_projection are 100% preserved, then populate V3 projections via upcast().
    if db_instance is not None and hasattr(db_instance, "_replay_v1_projections_on_conn"):
        db_instance._replay_v1_projections_on_conn(conn)

    rows = conn.execute("SELECT * FROM event_log ORDER BY event_seq ASC").fetchall()
    known_phrases = {
        str(r[0]) for r in conn.execute("SELECT phrase_id FROM phrase_catalog").fetchall()
    }

    for raw in rows:
        ev = upcast(raw)
        ev_type = str(ev["event_type"])
        ev_id = str(ev["event_id"])
        ev_seq = int(ev["event_seq"])
        learner_id = int(ev["learner_id"])
        session_id = str(ev["session_id"])
        occurred = str(ev["occurred_at_utc"])
        payload = ev["payload"]

        if ev_type == "analyst_diagnosis_recorded":
            for d in payload.get("diagnostics", []):
                if not isinstance(d, dict):
                    continue
                diag_id = str(d.get("diagnostic_id") or make_diagnostic_id(ev_id, 0))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feedback_item_projection (
                        diagnostic_id, learner_id, session_id, turn_event_seq,
                        pattern_id, context_family, domain, classification,
                        repair_scope, kind, severity, original_span, alternative,
                        explanation_en, explanation_ar, priority_class, priority_score,
                        delivery, status, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, 'general', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 50.0, 'deferred', 'pending', ?)
                    """,
                    (
                        diag_id,
                        learner_id,
                        session_id,
                        ev_seq,
                        str(d.get("pattern_id") or "general"),
                        str(d.get("domain") or "linguistic"),
                        str(d.get("classification") or "error"),
                        str(d.get("repair_scope") or "phrase_span"),
                        str(d.get("kind") or "grammar"),
                        str(d.get("severity") or "medium"),
                        str(d.get("original_span") or ""),
                        str(d.get("alternative") or ""),
                        str(d.get("explanation_en") or ""),
                        d.get("explanation_ar"),
                        occurred,
                    ),
                )

        elif ev_type == "feedback_card_delivered":
            diag_id = payload.get("diagnostic_id")
            if diag_id:
                conn.execute(
                    "UPDATE feedback_item_projection SET status='delivered', delivery='end_of_turn', delivered_at_utc=? "
                    "WHERE diagnostic_id=?",
                    (occurred, str(diag_id)),
                )

        elif ev_type == "diagnostic_retracted":
            diag_id = payload.get("diagnostic_id")
            if diag_id:
                conn.execute(
                    "UPDATE feedback_item_projection SET status='retracted' WHERE diagnostic_id=?",
                    (str(diag_id),),
                )

        elif ev_type == "phrase_production_assessed":
            phrase_id = str(payload.get("phrase_id") or "")
            if phrase_id and phrase_id in known_phrases:
                mode = str(
                    payload.get("production_mode")
                    or payload.get("mode")
                    or payload.get("evidence_type")
                    or "independent_production"
                ).lower()
                is_echo = 1 if payload.get("echo") or payload.get("is_echo") else 0
                if not is_echo:
                    if "recog" in mode:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO phrase_evidence (
                                source_event_id, dimension, learner_id, phrase_id,
                                score, weight, context_family, origin, hint_level,
                                is_delayed, gap_days, echo, occurred_at_utc, retracted
                            ) VALUES (?, 'recognition', ?, ?, 100.0, 1.0, 'general', 'scene', 0, 0, NULL, 0, ?, 0)
                            """,
                            (ev_id, learner_id, phrase_id, occurred),
                        )
                    elif "cued" in mode or "prompt" in mode:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO phrase_evidence (
                                source_event_id, dimension, learner_id, phrase_id,
                                score, weight, context_family, origin, hint_level,
                                is_delayed, gap_days, echo, occurred_at_utc, retracted
                            ) VALUES (?, 'production', ?, ?, 75.0, 0.8, 'general', 'scene', 1, 0, NULL, 0, ?, 0)
                            """,
                            (ev_id, learner_id, phrase_id, occurred),
                        )
                    else:
                        origin = "spontaneous" if "spontaneous" in mode else "scene"
                        for dim in ("production", "independence"):
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO phrase_evidence (
                                    source_event_id, dimension, learner_id, phrase_id,
                                    score, weight, context_family, origin, hint_level,
                                    is_delayed, gap_days, echo, occurred_at_utc, retracted
                                ) VALUES (?, ?, ?, ?, 75.0, 1.0, 'general', ?, 0, 0, NULL, 0, ?, 0)
                                """,
                                (ev_id, dim, learner_id, phrase_id, origin, occurred),
                            )

        elif ev_type == "retrieval_attempted":
            phrase_id = str(payload.get("phrase_id") or "")
            if phrase_id and phrase_id in known_phrases:
                from mastery import build_evidence_for_retrieval, recompute_phrase_state
                from clock import from_utc_iso
                from schemas_v3 import ContextFamily, RetrievalOrigin, RetrievalOutcome
                prior_row = conn.execute(
                    "SELECT last_success_at_utc, schedule_step FROM phrase_state_projection WHERE learner_id=? AND phrase_id=?",
                    (learner_id, phrase_id),
                ).fetchone()
                prior_succ = str(prior_row[0]) if prior_row and prior_row[0] else None
                ev_specs = build_evidence_for_retrieval(
                    source_event_id=ev_id,
                    learner_id=learner_id,
                    phrase_id=phrase_id,
                    outcome=RetrievalOutcome(str(payload.get("outcome") or "success")),
                    origin=RetrievalOrigin(str(payload.get("origin") or "review")),
                    context_family=str(payload.get("context_family") or "general"),
                    hint_level=int(payload.get("hint_level") or 0),
                    linguistic_level=payload.get("linguistic_level"),
                    functional_level=payload.get("functional_level"),
                    occurred_at_utc=occurred,
                    prior_last_success_at_utc=prior_succ,
                    echo=bool(payload.get("echo", False)),
                )
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
                recompute_phrase_state(
                    conn,
                    learner_id=learner_id,
                    phrase_id=phrase_id,
                    now=from_utc_iso(occurred),
                    last_outcome=str(payload.get("outcome") or "success"),
                )

        elif ev_type == "phrase_evidence_retracted":
            src_id = (
                payload.get("source_event_id")
                or payload.get("retracted_event_id")
                or payload.get("target_event_id")
            )
            if src_id:
                conn.execute(
                    "UPDATE phrase_evidence SET retracted = 1 WHERE source_event_id = ?",
                    (str(src_id),),
                )

    if _table_exists(conn, "meta"):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('projection_version', '2')")


def _apply_m003(conn: sqlite3.Connection, now_iso: str) -> None:
    rebuild_all_projections(conn)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="m001_event_types_and_event_log_rebuild",
        apply=_apply_m001,
        checksum=hashlib.sha256(b"S0_M001_v3.0").hexdigest(),
        needs_fk_off=True,
    ),
    Migration(
        version=2,
        name="m002_v3_tables_and_columns",
        apply=_apply_m002,
        checksum=hashlib.sha256(b"S0_M002_v3.0").hexdigest(),
        needs_fk_off=False,
    ),
    Migration(
        version=3,
        name="m003_projection_rebuild",
        apply=_apply_m003,
        checksum=hashlib.sha256(b"S0_M003_v3.0").hexdigest(),
        needs_fk_off=False,
    ),
    Migration(
        version=4,
        name="m004_seed_catalog_v3",
        apply=_apply_m004,
        checksum=hashlib.sha256(b"S0_M004_v3.0").hexdigest(),
        needs_fk_off=False,
    ),
)

LATEST_VERSION = MIGRATIONS[-1].version


def run_migrations(db_or_conn: Any, clock: Clock | None = None) -> list[int]:
    """Runs pending migrations atomically (Section 4.0)."""
    clk = clock or SystemClock()
    now_iso = to_utc_iso(clk.now())

    owned = False
    db_inst = None
    if isinstance(db_or_conn, sqlite3.Connection):
        conn = db_or_conn
    elif isinstance(db_or_conn, (str, Path)):
        conn = sqlite3.connect(str(db_or_conn), isolation_level=None)
        owned = True
    elif hasattr(db_or_conn, "_connect"):
        db_inst = db_or_conn
        conn = db_or_conn._connect()
        conn.isolation_level = None
        owned = True
    else:
        raise TypeError("Expected sqlite3.Connection, path, or EventSourcedDatabase")

    prev_isolation = conn.isolation_level
    if conn.in_transaction:
        conn.commit()
    conn.isolation_level = None

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at_utc TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
            """
        )
        applied_rows = conn.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
        applied_map = {int(r[0]): (str(r[1]), str(r[2])) for r in applied_rows}

        for m in MIGRATIONS:
            if m.version in applied_map:
                _, stored_sum = applied_map[m.version]
                if stored_sum != m.checksum:
                    raise RuntimeError(
                        f"Checksum mismatch on applied migration v{m.version} ({m.name})"
                    )

        newly_applied: list[int] = []
        for m in MIGRATIONS:
            if m.version in applied_map:
                continue
            if m.needs_fk_off:
                conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if m.version == 3 and db_inst is not None:
                        rebuild_all_projections(conn, db_instance=db_inst)
                    else:
                        m.apply(conn, now_iso)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at_utc, checksum) "
                        "VALUES (?, ?, ?, ?)",
                        (m.version, m.name, now_iso, m.checksum),
                    )
                    conn.commit()
                except BaseException:
                    if conn.in_transaction:
                        conn.rollback()
                    raise
            finally:
                if m.needs_fk_off:
                    conn.execute("PRAGMA foreign_keys = ON")
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise RuntimeError(f"PRAGMA foreign_key_check failed after v{m.version}: {fk_errors}")
            newly_applied.append(m.version)

        return newly_applied
    finally:
        if owned:
            conn.close()
        else:
            conn.isolation_level = prev_isolation


class V3DatabaseMixin:
    """Section 6 V3.0 extensions for EventSourcedDatabase."""

    def _connect(self) -> sqlite3.Connection:
        for attr in ("db_path", "_db_path", "path", "_path"):
            p = getattr(self, attr, None)
            if p is not None:
                conn = sqlite3.connect(str(p), isolation_level=None)
                conn.row_factory = sqlite3.Row
                return conn
        for attr in ("conn", "_conn", "connection", "_connection"):
            c = getattr(self, attr, None)
            if isinstance(c, sqlite3.Connection):
                c.row_factory = sqlite3.Row
                return c
        raise RuntimeError("Cannot resolve database path or connection on EventSourcedDatabase")

    def _get_clock(self) -> Clock:
        clk = getattr(self, "clock", None) or getattr(self, "_clock", None)
        return clk if clk is not None else SystemClock()

    def _replay_v1_projections_on_conn(self, conn: sqlite3.Connection) -> None:
        if hasattr(self, "rebuild_projections_from_event_log"):
            # Delegate to existing v1 rebuild logic on the open connection if supported
            pass

    def migrate(self) -> list[int]:
        applied = run_migrations(self, clock=self._get_clock())
        if applied and hasattr(self, "rebuild_projections_from_event_log"):
            self.rebuild_projections_from_event_log()
            with self._connect() as conn:
                rebuild_all_projections(conn)
                conn.commit()
        return applied

    def append_event_ex(
        self,
        *,
        learner_id: int,
        session_id: str,
        event_type: str,
        payload: Any,
        correlation_id: str,
        idempotency_key: str,
        causation_id: str | None = None,
        schema_version: str = "2.0",
        rubric_version: str = "v2.0",
        model_version: str | None = None,
        occurred_at_utc: str | None = None,
        rebuild: bool = True,
    ) -> AppendResult:
        now_iso = occurred_at_utc or to_utc_iso(self._get_clock().now())
        if hasattr(payload, "model_dump"):
            payload_dict = payload.model_dump(mode="json")
        else:
            payload_dict = dict(payload)
        payload_json = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        with self._connect() as conn:
            row = conn.execute(
                "SELECT event_id, event_seq FROM event_log WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                return AppendResult(inserted=False, event_id=str(row["event_id"]), event_seq=int(row["event_seq"]))

            if _table_exists(conn, "event_types"):
                conn.execute(
                    "INSERT OR IGNORE INTO event_types (event_type, introduced_in) VALUES (?, 1)",
                    (event_type,),
                )

            ev_id = f"ev_{uuid.uuid4().hex[:12]}"
            cur = conn.execute(
                """
                INSERT INTO event_log (
                    event_id, learner_id, session_id, event_type,
                    occurred_at_utc, recorded_at_utc, schema_version,
                    model_version, rubric_version, correlation_id,
                    causation_id, idempotency_key, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id,
                    learner_id,
                    session_id,
                    event_type,
                    now_iso,
                    now_iso,
                    schema_version,
                    model_version,
                    rubric_version,
                    correlation_id,
                    causation_id,
                    idempotency_key,
                    payload_json,
                ),
            )
            seq = int(cur.lastrowid)
            if rebuild and hasattr(self, "_apply_event_to_projections"):
                try:
                    self._apply_event_to_projections(
                        conn,
                        event_id=ev_id,
                        learner_id=learner_id,
                        session_id=session_id,
                        event_type=event_type,
                        occurred_at_utc=now_iso,
                        payload=payload_dict,
                    )
                except Exception:
                    pass
            conn.commit()
            return AppendResult(inserted=True, event_id=ev_id, event_seq=seq)

    def accept_scene_turn(
        self,
        *,
        learner_id: int,
        session_id: str,
        text: str,
        idempotency_key: str,
        origin: TurnOrigin = TurnOrigin.SCENE,
        correlation_id: str,
        input_modality: Literal["text", "voice"] = "text",
        telegram_message_id: int | None = None,
    ) -> tuple[AppendResult, int]:
        now_iso = to_utc_iso(self._get_clock().now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT event_id, event_seq, payload_json FROM event_log WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    p = json.loads(existing["payload_json"])
                    rev = int(p.get("session_revision") or self._get_rev_on_conn(conn, session_id))
                    conn.commit()
                    return (
                        AppendResult(
                            inserted=False,
                            event_id=str(existing["event_id"]),
                            event_seq=int(existing["event_seq"]),
                            session_revision=rev,
                        ),
                        rev,
                    )

                conn.execute(
                    "UPDATE sessions SET session_revision = COALESCE(session_revision, 0) + 1 "
                    "WHERE session_id = ? AND learner_id = ?",
                    (session_id, learner_id),
                )
                rev = self._get_rev_on_conn(conn, session_id)
                msg_model = LearnerMessageV2(
                    text=text,
                    input_modality=input_modality,
                    origin=TurnOrigin(origin),
                    session_revision=rev,
                    telegram_message_id=telegram_message_id,
                )
                payload_dict = msg_model.model_dump(mode="json")
                payload_json = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                ev_id = f"ev_{uuid.uuid4().hex[:12]}"
                cur = conn.execute(
                    """
                    INSERT INTO event_log (
                        event_id, learner_id, session_id, event_type,
                        occurred_at_utc, recorded_at_utc, schema_version,
                        rubric_version, correlation_id, idempotency_key, payload_json
                    ) VALUES (?, ?, ?, 'learner_message_received', ?, ?, '2.0', 'v2.0', ?, ?, ?)
                    """,
                    (ev_id, learner_id, session_id, now_iso, now_iso, correlation_id, idempotency_key, payload_json),
                )
                seq = int(cur.lastrowid)
                if hasattr(self, "_apply_event_to_projections"):
                    try:
                        self._apply_event_to_projections(
                            conn,
                            event_id=ev_id,
                            learner_id=learner_id,
                            session_id=session_id,
                            event_type="learner_message_received",
                            occurred_at_utc=now_iso,
                            payload=payload_dict,
                        )
                    except Exception:
                        pass
                conn.commit()
                res = AppendResult(inserted=True, event_id=ev_id, event_seq=seq, session_revision=rev)
                return res, rev
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def _get_rev_on_conn(self, conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute(
            "SELECT session_revision FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def get_session_revision(self, session_id: str) -> int:
        with self._connect() as conn:
            return self._get_rev_on_conn(conn, session_id)

    def append_events_if_revision(
        self,
        session_id: str,
        expected_revision: int,
        events: Sequence[EventSpec],
    ) -> bool:
        now_iso = to_utc_iso(self._get_clock().now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current_rev = self._get_rev_on_conn(conn, session_id)
                if current_rev != int(expected_revision):
                    conn.rollback()
                    return False
                for spec in events:
                    existing = conn.execute(
                        "SELECT 1 FROM event_log WHERE idempotency_key = ?",
                        (spec.idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        continue
                    if hasattr(spec.payload, "model_dump"):
                        p_dict = spec.payload.model_dump(mode="json")
                    else:
                        p_dict = dict(spec.payload)
                    p_json = json.dumps(p_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    ev_id = f"ev_{uuid.uuid4().hex[:12]}"
                    occ = spec.occurred_at_utc or now_iso
                    conn.execute(
                        """
                        INSERT INTO event_log (
                            event_id, learner_id, session_id, event_type,
                            occurred_at_utc, recorded_at_utc, schema_version,
                            model_version, rubric_version, correlation_id,
                            causation_id, idempotency_key, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ev_id,
                            spec.learner_id,
                            spec.session_id,
                            spec.event_type,
                            occ,
                            now_iso,
                            spec.schema_version,
                            spec.model_version,
                            spec.rubric_version,
                            spec.correlation_id,
                            spec.causation_id,
                            spec.idempotency_key,
                            p_json,
                        ),
                    )
                conn.commit()
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def set_pending_clarification(self, session_id: str, text: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET pending_clarification_json = ? WHERE session_id = ?",
                (text, session_id),
            )
            conn.commit()

    def take_pending_clarification(self, session_id: str) -> str | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT pending_clarification_json FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                val = str(row[0]) if row is not None and row[0] is not None else None
                if val is not None:
                    conn.execute(
                        "UPDATE sessions SET pending_clarification_json = NULL WHERE session_id = ?",
                        (session_id,),
                    )
                conn.commit()
                return val
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def get_auto_voice(self, learner_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT auto_voice FROM learner_settings WHERE learner_id = ?",
                (learner_id,),
            ).fetchone()
            return bool(row[0]) if row is not None else False

    def set_auto_voice(self, learner_id: int, on: bool) -> None:
        now_iso = to_utc_iso(self._get_clock().now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO learner_settings (learner_id, auto_voice, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(learner_id) DO UPDATE SET
                    auto_voice = excluded.auto_voice,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (learner_id, 1 if on else 0, now_iso),
            )
            conn.commit()

    def get_audio_file_id(self, script_hash: str, voice_config: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_file_id FROM audio_cache WHERE script_hash = ? AND voice_config = ?",
                (script_hash, voice_config),
            ).fetchone()
            return str(row[0]) if row is not None else None

    def put_audio_file_id(
        self,
        script_hash: str,
        voice_config: str,
        file_id: str,
        file_unique_id: str | None = None,
        duration_s: float | None = None,
    ) -> None:
        now_iso = to_utc_iso(self._get_clock().now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audio_cache (
                    script_hash, voice_config, telegram_file_id,
                    telegram_file_unique_id, duration_s, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(script_hash, voice_config) DO UPDATE SET
                    telegram_file_id = excluded.telegram_file_id,
                    telegram_file_unique_id = excluded.telegram_file_unique_id,
                    duration_s = excluded.duration_s,
                    created_at_utc = excluded.created_at_utc
                """,
                (script_hash, voice_config, file_id, file_unique_id, duration_s, now_iso),
            )
            conn.commit()

    def drop_audio_file_id(self, script_hash: str, voice_config: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM audio_cache WHERE script_hash = ? AND voice_config = ?",
                (script_hash, voice_config),
            )
            conn.commit()


class V3EventSourcedDatabase(V3DatabaseMixin):
    """Standalone wrapper for CLI and tests."""

    def __init__(self, db_path: str | Path, clock: Clock | None = None) -> None:
        from database import EventSourcedDatabase
        self._inner = EventSourcedDatabase(db_path)
        self.db_path = Path(db_path)
        self.clock = clock or SystemClock()

    def _connect(self) -> sqlite3.Connection:
        return self._inner._connect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m database")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mig = sub.add_parser("migrate")
    mig.add_argument("--db-path", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        from database import EventSourcedDatabase
        db = EventSourcedDatabase(args.db_path)
        applied = db.migrate()
        print(json.dumps({"db_path": args.db_path, "applied": applied}))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
