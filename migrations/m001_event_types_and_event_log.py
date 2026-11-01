"""M001 (S0 section 4.1): ``event_types`` registry and ``event_log`` rebuild.

The v2 ``event_log`` has a CHECK listing the 14 live event types, so V3.0's three new types cannot
be inserted. SQLite cannot drop a CHECK, so the table is rebuilt: ``event_types`` replaces the CHECK
(the registry is data, not schema), rows are copied with their ``event_seq`` untouched, and the
append-only triggers and indexes are recreated.

The runner sets ``foreign_keys=OFF`` before BEGIN. Verification runs inside the transaction, before
COMMIT: row count, SHA-256 over ``event_seq || event_id || payload_json`` in ``event_seq`` order and
``max(event_seq)`` are equal before and after, the AUTOINCREMENT counter did not move backwards,
and an UPDATE and a DELETE on ``event_log`` both raise.

``idx_event_log_correlation`` existed in v2 and is recreated here. The S0 text lists only three
indexes; dropping the fourth silently would be a regression for correlation lookups.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from migrations import Migration, MigrationContext, VerificationError

VERSION = 1
NAME = "event_types_and_event_log_rebuild"

# Frozen copy of the registry as of this migration: later event types arrive in later migrations,
# and editing this list would (correctly) trip the checksum guard.
SQL = """
CREATE TABLE event_types (
  event_type TEXT PRIMARY KEY,
  introduced_in INTEGER NOT NULL
);
INSERT INTO event_types(event_type, introduced_in) VALUES
 ('session_started',0),('session_ended',0),('learner_message_received',0),
 ('analyst_diagnosis_recorded',0),('policy_decision_recorded',0),
 ('responder_reply_delivered',0),('feedback_card_delivered',0),
 ('micro_workout_scheduled',0),('micro_workout_completed',0),
 ('phrase_exposure_recorded',0),('phrase_production_assessed',0),
 ('diagnostic_retracted',0),('phrase_evidence_retracted',0),
 ('review_completed',0),
 ('reconstruction_attempted',1),('retrieval_attempted',1),
 ('analysis_skipped',1);

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
INSERT INTO event_log_new (event_seq, event_id, learner_id, session_id, event_type,
  occurred_at_utc, recorded_at_utc, schema_version, model_version, rubric_version,
  correlation_id, causation_id, idempotency_key, payload_json)
SELECT event_seq, event_id, learner_id, session_id, event_type,
  occurred_at_utc, recorded_at_utc, schema_version, model_version, rubric_version,
  correlation_id, causation_id, idempotency_key, payload_json
FROM event_log ORDER BY event_seq;
DROP TABLE event_log;
ALTER TABLE event_log_new RENAME TO event_log;

CREATE TRIGGER event_log_no_update BEFORE UPDATE ON event_log
BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
CREATE TRIGGER event_log_no_delete BEFORE DELETE ON event_log
BEGIN SELECT RAISE(ABORT, 'event_log is append-only'); END;
CREATE INDEX idx_event_log_learner_time ON event_log(learner_id, occurred_at_utc, event_seq);
CREATE INDEX idx_event_log_session_time ON event_log(session_id, occurred_at_utc, event_seq);
CREATE INDEX idx_event_log_type_learner ON event_log(event_type, learner_id, event_seq);
CREATE INDEX idx_event_log_correlation ON event_log(correlation_id);
"""

EXPECTED_COLUMNS = [
    "event_seq", "event_id", "learner_id", "session_id", "event_type", "occurred_at_utc",
    "recorded_at_utc", "schema_version", "model_version", "rubric_version", "correlation_id",
    "causation_id", "idempotency_key", "payload_json",
]
EXPECTED_INDEXES = {
    "idx_event_log_learner_time", "idx_event_log_session_time",
    "idx_event_log_type_learner", "idx_event_log_correlation",
}


def log_fingerprint(conn: sqlite3.Connection) -> dict[str, Any]:
    """Row count, max(event_seq) and SHA-256 over ``event_seq || event_id || payload_json``."""
    digest = hashlib.sha256()
    count = 0
    max_seq = 0
    for row in conn.execute("SELECT event_seq, event_id, payload_json FROM event_log ORDER BY event_seq"):
        digest.update(f"{row[0]}{row[1]}{row[2]}".encode("utf-8"))
        count += 1
        max_seq = int(row[0])
    return {"count": count, "max_seq": max_seq, "sha256": digest.hexdigest()}


def _sequence(conn: sqlite3.Connection) -> Any:
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'event_log'").fetchone()
    return None if row is None else int(row[0])


def before(conn: sqlite3.Connection, ctx: MigrationContext) -> dict[str, Any]:
    snapshot = log_fingerprint(conn)
    snapshot["sequence"] = _sequence(conn)
    return snapshot


def _expect_append_only(conn: sqlite3.Connection, statement: str, label: str) -> None:
    try:
        conn.execute(statement)
    except sqlite3.IntegrityError as exc:
        if "append-only" not in str(exc):
            raise VerificationError(f"{label} on event_log raised the wrong error: {exc}") from exc
        return
    raise VerificationError(f"{label} on event_log did not raise: the append-only trigger is missing")


def _probe_triggers(conn: sqlite3.Connection) -> None:
    conn.execute("SAVEPOINT m001_probe")
    try:
        conn.execute(
            "INSERT INTO event_log (event_id, learner_id, session_id, event_type, occurred_at_utc, "
            "recorded_at_utc, correlation_id, idempotency_key, payload_json) "
            "VALUES ('ev_m001probe', 0, 'm001_probe', 'session_started', 'x', 'x', 'x', 'm001_probe', '{}')"
        )
        _expect_append_only(conn, "UPDATE event_log SET correlation_id = 'y' WHERE event_id = 'ev_m001probe'", "UPDATE")
        _expect_append_only(conn, "DELETE FROM event_log WHERE event_id = 'ev_m001probe'", "DELETE")
    finally:
        conn.execute("ROLLBACK TO m001_probe")
        conn.execute("RELEASE m001_probe")


def verify(conn: sqlite3.Connection, ctx: MigrationContext, snapshot: dict[str, Any]) -> None:
    after = log_fingerprint(conn)
    for key in ("count", "max_seq", "sha256"):
        if after[key] != snapshot[key]:
            raise VerificationError(f"event_log {key} changed: {snapshot[key]!r} -> {after[key]!r}")
    new_sequence = _sequence(conn)
    old_sequence = snapshot["sequence"]
    if old_sequence is not None and (new_sequence is None or new_sequence < old_sequence):
        raise VerificationError(f"AUTOINCREMENT counter moved backwards: {old_sequence!r} -> {new_sequence!r}")
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(event_log)")]
    if columns != EXPECTED_COLUMNS:
        raise VerificationError(f"event_log columns differ from the contract: {columns}")
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(event_log)")}
    missing = EXPECTED_INDEXES - indexes
    if missing:
        raise VerificationError(f"event_log is missing indexes: {sorted(missing)}")
    registered = {r["event_type"] for r in conn.execute("SELECT event_type FROM event_types")}
    used = {r["event_type"] for r in conn.execute("SELECT DISTINCT event_type FROM event_log")}
    if not used <= registered:
        raise VerificationError(f"event_log has unregistered event types: {sorted(used - registered)}")
    _probe_triggers(conn)


MIGRATION = Migration(
    version=VERSION,
    name=NAME,
    sql=SQL,
    fk_off=True,
    logic_tag="m001/1",
    before=before,
    verify=verify,
)
