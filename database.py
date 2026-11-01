import argparse
import json
import re
import sqlite3
import sys
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Sequence

from pydantic import BaseModel

import migrations as _migrations
from clock import Clock, SystemClock, iso_now, parse_iso
from config import DB_PATH
from schemas import (
    PAYLOAD_MODELS,
    RUBRIC_VERSION_V1,
    RUBRIC_VERSION_V2,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    ConfidenceBand,
    EventV2,
    InterventionLevel,
    IssueKind,
    LearnerMessageV2,
    ProductionEvidence,
    TrainingMode,
    TurnOrigin,
    upcast,
)

RECENT_FAILURE_WINDOW_DAYS = 14.0

# The v2 schema, kept verbatim as migration baseline version 0. Fresh files get it first and then
# every migration; existing v2 files are already at it. It is idempotent (IF NOT EXISTS) and is
# NOT the current schema: M001 replaces its event_log, M002 adds the V3.0 tables and columns.
BASELINE_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS learners (
    learner_id INTEGER PRIMARY KEY,
    english_variety TEXT NOT NULL DEFAULT 'general_american',
    default_register TEXT NOT NULL DEFAULT 'casual',
    default_directness TEXT NOT NULL DEFAULT 'balanced',
    feedback_preference TEXT NOT NULL DEFAULT 'light' CHECK(feedback_preference IN ('silent', 'light', 'active')),
    arabic_support TEXT NOT NULL DEFAULT 'bilingual' CHECK(arabic_support IN ('english_only', 'on_request', 'bilingual')),
    opted_in_workouts INTEGER NOT NULL DEFAULT 1,
    active_session_id TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    learner_id INTEGER NOT NULL REFERENCES learners(learner_id),
    training_mode TEXT NOT NULL DEFAULT 'open_scene',
    scene_mode TEXT NOT NULL DEFAULT 'real_conversation',
    register_profile TEXT NOT NULL DEFAULT 'casual',
    directness TEXT NOT NULL DEFAULT 'balanced',
    scene_bible_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(scene_bible_json)),
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_session_learner
ON sessions(session_id, learner_id);

CREATE TABLE IF NOT EXISTS phrase_catalog (
    phrase_id TEXT PRIMARY KEY,
    canonical_form TEXT NOT NULL,
    communicative_intention TEXT NOT NULL,
    construction_pattern TEXT NOT NULL,
    register_profiles_json TEXT NOT NULL CHECK(json_valid(register_profiles_json)),
    variety_tags_json TEXT NOT NULL DEFAULT '["general_american"]' CHECK(json_valid(variety_tags_json)),
    applicability TEXT NOT NULL DEFAULT 'core' CHECK(applicability IN ('core', 'situational', 'context_limited')),
    rubric_version TEXT NOT NULL DEFAULT 'v1.0',
    source_provenance TEXT NOT NULL DEFAULT 'curated_reference'
);

CREATE TABLE IF NOT EXISTS event_log (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    learner_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'session_started',
        'session_ended',
        'learner_message_received',
        'analyst_diagnosis_recorded',
        'policy_decision_recorded',
        'responder_reply_delivered',
        'feedback_card_delivered',
        'micro_workout_scheduled',
        'micro_workout_completed',
        'phrase_exposure_recorded',
        'phrase_production_assessed',
        'diagnostic_retracted',
        'phrase_evidence_retracted',
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
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    FOREIGN KEY (session_id, learner_id) REFERENCES sessions(session_id, learner_id)
);

CREATE TRIGGER IF NOT EXISTS event_log_no_update
BEFORE UPDATE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS event_log_no_delete
BEFORE DELETE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event_log is append-only');
END;

CREATE INDEX IF NOT EXISTS idx_event_log_learner_time ON event_log(learner_id, occurred_at_utc, event_seq);
CREATE INDEX IF NOT EXISTS idx_event_log_session_time ON event_log(session_id, occurred_at_utc, event_seq);
CREATE INDEX IF NOT EXISTS idx_event_log_correlation ON event_log(correlation_id);

CREATE TABLE IF NOT EXISTS session_state_projection (
    session_id TEXT PRIMARY KEY,
    learner_id INTEGER NOT NULL,
    turns_since_visible_card INTEGER NOT NULL DEFAULT 4,
    recent_questions_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(recent_questions_json)),
    revealed_targets_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(revealed_targets_json)),
    cued_targets_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(cued_targets_json)),
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (session_id, learner_id) REFERENCES sessions(session_id, learner_id)
);

CREATE TABLE IF NOT EXISTS phrase_state_projection (
    learner_id INTEGER NOT NULL REFERENCES learners(learner_id),
    phrase_id TEXT NOT NULL REFERENCES phrase_catalog(phrase_id),
    current_stage TEXT NOT NULL DEFAULT 'Unseen' CHECK(current_stage IN (
        'Unseen',
        'Seen',
        'Recognized',
        'Produced-with-prompt',
        'Produced-independently',
        'Produced-spontaneously',
        'Stable'
    )),
    exposure_count INTEGER NOT NULL DEFAULT 0,
    recognition_count INTEGER NOT NULL DEFAULT 0,
    cued_count INTEGER NOT NULL DEFAULT 0,
    independent_count INTEGER NOT NULL DEFAULT 0,
    spontaneous_count INTEGER NOT NULL DEFAULT 0,
    distinct_valid_sessions INTEGER NOT NULL DEFAULT 0,
    distinct_valid_registers INTEGER NOT NULL DEFAULT 0,
    span_days REAL NOT NULL DEFAULT 0.0,
    has_novel_context INTEGER NOT NULL DEFAULT 0,
    recent_unretracted_failure INTEGER NOT NULL DEFAULT 0,
    last_evidence_event_id TEXT,
    next_review_at_utc TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (learner_id, phrase_id)
);

CREATE TABLE IF NOT EXISTS error_pattern_projection (
    learner_id INTEGER NOT NULL REFERENCES learners(learner_id),
    normalized_pattern_id TEXT NOT NULL,
    relevant_context_family TEXT NOT NULL DEFAULT 'global',
    issue_kind TEXT NOT NULL,
    distinct_eligible_occurrences INTEGER NOT NULL DEFAULT 0,
    times_feedback_selected INTEGER NOT NULL DEFAULT 0,
    times_feedback_shown INTEGER NOT NULL DEFAULT 0,
    workout_scheduled INTEGER NOT NULL DEFAULT 0,
    last_occurrence_event_id TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (learner_id, normalized_pattern_id, relevant_context_family)
);

CREATE TABLE IF NOT EXISTS pending_cards_cache (
    card_id TEXT PRIMARY KEY,
    learner_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    diagnostic_json TEXT NOT NULL CHECK(json_valid(diagnostic_json)),
    created_at_utc TEXT NOT NULL
);
"""

SQLITE_EVENT_SOURCED_DDL = BASELINE_DDL  # v2 name, kept for callers that import it


def _parse_iso_utc(ts_str: str) -> datetime:
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


DEFAULT_CURATED_PHRASES = [
    ("run_something_by_someone", "run [something] by [someone]", "seek_feedback", "run + NP + by + person", ["casual", "diplomatic"], "core"),
    ("see_where_coming_from", "I see where you're coming from", "acknowledge_perspective", "I see where you're coming from, + clause", ["warm", "diplomatic"], "core"),
    ("plate_is_full", "my plate's pretty full", "decline_workload", "my plate is/plate's + adj + full", ["casual", "diplomatic"], "core"),
    ("talk_something_over", "talk [something] over", "discuss_collaboratively", "talk + NP + over", ["casual", "warm", "diplomatic"], "core"),
    ("cuts_both_ways", "it cuts both ways", "weigh_tradeoffs", "NP/it + cuts both ways", ["analytical", "executive"], "situational"),
    ("not_entirely_sold_on", "I'm not entirely sold on [that]", "express_skepticism", "not entirely/quite sold on + NP", ["casual", "diplomatic", "executive"], "core"),
    ("make_a_big_deal", "make a big deal out of [it]", "reassure_or_downplay", "make a big deal out of + NP", ["casual", "warm"], "core"),
    ("push_back_on", "push back on [something]", "challenge_proposal", "push back on + NP", ["diplomatic", "executive"], "core"),
]


@dataclass(frozen=True)
class AppendResult:
    inserted: bool
    event_id: str
    event_seq: int


@dataclass(frozen=True)
class EventSpec:
    """One event to append inside ``append_events_if_revision`` (all-or-nothing).

    ``learner_id`` defaults to the session's learner. ``event_id`` may be pre-assigned
    (``ev_<12 hex>``) so the caller can reference it as ``causation_id`` of a later spec.
    """

    event_type: str
    payload: Any  # BaseModel or dict
    idempotency_key: str
    correlation_id: str
    causation_id: Optional[str] = None
    learner_id: Optional[int] = None
    event_id: Optional[str] = None
    occurred_at_utc: Optional[str] = None
    schema_version: str = SCHEMA_VERSION_V2
    rubric_version: str = RUBRIC_VERSION_V2
    model_version: Optional[str] = None
    rebuild: bool = True


class EventSourcedDatabase:
    def __init__(self, db_path: str = DB_PATH, *, clock: Optional[Clock] = None, auto_migrate: bool = True):
        self.db_path = db_path
        self._clock: Clock = clock or SystemClock()
        self._init_db(auto_migrate)

    @property
    def clock(self) -> Clock:
        return self._clock

    def _now_iso(self) -> str:
        return iso_now(self._clock)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A short-lived read-only connection that is always closed (``query_only`` makes a stray
        write fail loudly instead of sneaking in outside a transaction)."""
        conn = self._conn()
        conn.execute("PRAGMA query_only = ON;")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One ``BEGIN IMMEDIATE`` transaction: commit on success, roll back on any exception.
        ``IMMEDIATE`` takes the write lock up front, so a read-then-write inside it cannot race."""
        conn = sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def _init_db(self, auto_migrate: bool = True) -> None:
        with closing(self._conn()) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
        if auto_migrate:
            self.migrate()
        else:
            with closing(self._conn()) as conn:
                conn.executescript(BASELINE_DDL)
        for pid, form, intent, pat, regs, app in DEFAULT_CURATED_PHRASES:
            self._ensure_phrase(pid, form, intent, pat, regs, app)

    def migrate(self) -> list[int]:
        """Apply pending migrations (idempotent). Returns the versions applied by this call.
        Also available as ``python -m database migrate --db-path <path>``."""
        return _migrations.migrate(self.db_path, baseline_ddl=BASELINE_DDL, clock=self._clock)

    def upsert_learner(
        self,
        learner_id: int,
        feedback_preference: Optional[str] = None,
        opted_in_workouts: Optional[bool] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO learners (learner_id, feedback_preference, opted_in_workouts, created_at_utc)
                VALUES (?, COALESCE(?, 'light'), COALESCE(?, 1), ?)
                """,
                (
                    learner_id,
                    feedback_preference,
                    (1 if opted_in_workouts else 0) if opted_in_workouts is not None else None,
                    self._now_iso(),
                ),
            )
            if feedback_preference in ("silent", "light", "active"):
                conn.execute(
                    "UPDATE learners SET feedback_preference = ? WHERE learner_id = ?",
                    (feedback_preference, learner_id),
                )
            if opted_in_workouts is not None:
                conn.execute(
                    "UPDATE learners SET opted_in_workouts = ? WHERE learner_id = ?",
                    (1 if opted_in_workouts else 0, learner_id),
                )
            conn.commit()

    def get_learner_settings(self, learner_id: int) -> dict[str, Any]:
        self.upsert_learner(learner_id)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM learners WHERE learner_id = ?", (learner_id,)).fetchone()
            return dict(row)

    def update_learner_preference(
        self,
        learner_id: int,
        *,
        feedback_preference: Optional[str] = None,
        default_register: Optional[str] = None,
        default_directness: Optional[str] = None,
        arabic_support: Optional[str] = None,
    ) -> None:
        self.upsert_learner(learner_id)
        with self._conn() as conn:
            if feedback_preference in ("silent", "light", "active"):
                conn.execute(
                    "UPDATE learners SET feedback_preference = ? WHERE learner_id = ?",
                    (feedback_preference, learner_id),
                )
            if default_register in ("casual", "warm", "witty", "diplomatic", "executive", "analytical"):
                conn.execute(
                    "UPDATE learners SET default_register = ? WHERE learner_id = ?",
                    (default_register, learner_id),
                )
            if default_directness in ("soft", "balanced", "direct"):
                conn.execute(
                    "UPDATE learners SET default_directness = ? WHERE learner_id = ?",
                    (default_directness, learner_id),
                )
            if arabic_support in ("bilingual", "on_request", "english_only"):
                conn.execute(
                    "UPDATE learners SET arabic_support = ? WHERE learner_id = ?",
                    (arabic_support, learner_id),
                )
            conn.commit()

    def create_session(
        self,
        session_id: str,
        learner_id: int,
        training_mode: str = "open_scene",
        scene_mode: str = "real_conversation",
        register_profile: str = "casual",
        directness: str = "balanced",
        scene_bible: Optional[dict[str, Any]] = None,
        started_at_utc: Optional[str] = None,
    ) -> None:
        started = started_at_utc or self._now_iso()
        bible_str = json.dumps(scene_bible or {"scene_id": session_id, "scene_mode": scene_mode}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (
                    session_id, learner_id, training_mode, scene_mode,
                    register_profile, directness, scene_bible_json, started_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, learner_id, training_mode, scene_mode, register_profile, directness, bible_str, started),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO session_state_projection (
                    session_id, learner_id, turns_since_visible_card, recent_questions_json,
                    revealed_targets_json, cued_targets_json, updated_at_utc
                ) VALUES (?, ?, 4, '[]', '[]', '[]', ?)
                """,
                (session_id, learner_id, started),
            )
            conn.execute(
                "UPDATE learners SET active_session_id = ? WHERE learner_id = ?",
                (session_id, learner_id),
            )
            conn.commit()

    def get_or_create_active_session(self, learner_id: int) -> dict[str, Any]:
        self.upsert_learner(learner_id)
        with self._conn() as conn:
            l_row = conn.execute("SELECT * FROM learners WHERE learner_id = ?", (learner_id,)).fetchone()
            sid = l_row["active_session_id"] if l_row else None
            if sid:
                s_row = conn.execute("SELECT * FROM sessions WHERE session_id = ? AND learner_id = ?", (sid, learner_id)).fetchone()
                if s_row:
                    return dict(s_row)
        new_sid = f"sess_{uuid.uuid4().hex[:10]}"
        self.create_session(new_sid, learner_id)
        with self._conn() as conn:
            return dict(conn.execute("SELECT * FROM sessions WHERE session_id = ?", (new_sid,)).fetchone())

    def update_session_settings(
        self,
        session_id: str,
        learner_id: int,
        *,
        training_mode: Optional[str] = None,
        scene_mode: Optional[str] = None,
        register_profile: Optional[str] = None,
        directness: Optional[str] = None,
        scene_bible: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._conn() as conn:
            if training_mode:
                conn.execute("UPDATE sessions SET training_mode = ? WHERE session_id = ? AND learner_id = ?", (training_mode, session_id, learner_id))
            if scene_mode:
                conn.execute("UPDATE sessions SET scene_mode = ? WHERE session_id = ? AND learner_id = ?", (scene_mode, session_id, learner_id))
            if register_profile:
                conn.execute("UPDATE sessions SET register_profile = ? WHERE session_id = ? AND learner_id = ?", (register_profile, session_id, learner_id))
            if directness:
                conn.execute("UPDATE sessions SET directness = ? WHERE session_id = ? AND learner_id = ?", (directness, session_id, learner_id))
            if scene_bible is not None:
                conn.execute(
                    "UPDATE sessions SET scene_bible_json = ? WHERE session_id = ? AND learner_id = ?",
                    (json.dumps(scene_bible, ensure_ascii=False), session_id, learner_id),
                )
            conn.commit()

    def get_session_runtime_state(self, session_id: str, learner_id: int) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM session_state_projection WHERE session_id = ? AND learner_id = ?",
                (session_id, learner_id),
            ).fetchone()
            if not row:
                return {
                    "turns_since_visible_card": 4,
                    "recent_questions": [],
                    "revealed_targets": set(),
                    "cued_targets": set(),
                }
            return {
                "turns_since_visible_card": int(row["turns_since_visible_card"]),
                "recent_questions": list(json.loads(row["recent_questions_json"])),
                "revealed_targets": set(json.loads(row["revealed_targets_json"])),
                "cued_targets": set(json.loads(row["cued_targets_json"])),
            }

    def get_authoritative_occurrence_counts(self, learner_id: int) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT normalized_pattern_id, relevant_context_family, distinct_eligible_occurrences FROM error_pattern_projection WHERE learner_id = ?",
                (learner_id,),
            ).fetchall()
        return {
            f"{learner_id}:{r['normalized_pattern_id']}:{r['relevant_context_family']}": int(r["distinct_eligible_occurrences"])
            for r in rows
        }

    def select_stealth_target_for_session(self, learner_id: int, register_profile: str) -> Optional[dict[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT c.phrase_id, c.canonical_form, c.communicative_intention, c.register_profiles_json,
                        COALESCE(p.current_stage, 'Unseen') AS stage
                FROM phrase_catalog c
                LEFT JOIN phrase_state_projection p
                  ON c.phrase_id = p.phrase_id AND p.learner_id = ?
                WHERE c.applicability = 'core'
                ORDER BY CASE COALESCE(p.current_stage, 'Unseen')
                    WHEN 'Seen' THEN 1
                    WHEN 'Recognized' THEN 2
                    WHEN 'Produced-with-prompt' THEN 3
                    WHEN 'Produced-independently' THEN 4
                    WHEN 'Unseen' THEN 5
                    ELSE 6
                END ASC
                """,
                (learner_id,),
            ).fetchall()
        for r in rows:
            if r["stage"] == "Stable":
                continue
            regs = json.loads(r["register_profiles_json"])
            if register_profile in regs:
                return {
                    "phrase_id": str(r["phrase_id"]),
                    "canonical_form": str(r["canonical_form"]),
                    "communicative_intention": str(r["communicative_intention"]),
                    "mode": "organic",
                }
        return None

    def get_recent_dialogue_history(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT event_type, payload_json FROM event_log
                WHERE session_id = ? AND event_type IN ('learner_message_received', 'responder_reply_delivered')
                ORDER BY occurred_at_utc DESC, event_seq DESC LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        history: list[dict[str, str]] = []
        for r in reversed(rows):
            p = json.loads(r["payload_json"])
            if r["event_type"] == "learner_message_received":
                history.append({"role": "learner", "text": str(p.get("text", ""))})
            else:
                history.append({"role": "partner", "text": str(p.get("reply_text", ""))})
        return history

    def save_pending_card(
        self,
        card_id: str,
        learner_id: int,
        session_id: str,
        correlation_id: str,
        reply_text: str,
        diagnostic_dict: dict[str, Any],
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_cards_cache (
                    card_id, learner_id, session_id, correlation_id, reply_text, diagnostic_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (card_id, learner_id, session_id, correlation_id, reply_text, json.dumps(diagnostic_dict, ensure_ascii=False), self._now_iso()),
            )
            conn.commit()

    def get_pending_card(self, card_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pending_cards_cache WHERE card_id = ?", (card_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["diagnostic"] = json.loads(d["diagnostic_json"])
            return d

    def register_phrase(
        self,
        phrase_id: str,
        canonical_form: str,
        communicative_intention: str,
        construction_pattern: str,
        register_profiles: list[str],
        applicability: str = "core",
    ) -> None:
        """Insert or update a phrase's v2 columns. The V3.0 catalog columns (catalog_version,
        arabic_gloss, prompts, ...) are never touched: v2 used INSERT OR REPLACE, which would now
        wipe them on every call."""
        with closing(self._conn()) as conn:
            conn.execute(
                """
                INSERT INTO phrase_catalog (
                    phrase_id, canonical_form, communicative_intention,
                    construction_pattern, register_profiles_json, applicability
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(phrase_id) DO UPDATE SET
                    canonical_form = excluded.canonical_form,
                    communicative_intention = excluded.communicative_intention,
                    construction_pattern = excluded.construction_pattern,
                    register_profiles_json = excluded.register_profiles_json,
                    applicability = excluded.applicability
                """,
                (
                    phrase_id,
                    canonical_form,
                    communicative_intention,
                    construction_pattern,
                    json.dumps(register_profiles),
                    applicability,
                ),
            )
            conn.commit()

    def _ensure_phrase(
        self,
        phrase_id: str,
        canonical_form: str,
        communicative_intention: str,
        construction_pattern: str,
        register_profiles: list[str],
        applicability: str = "core",
    ) -> None:
        """Insert a built-in phrase only if it is missing, so a start-up never overwrites a
        phrase that a later catalog version has refined."""
        with closing(self._conn()) as conn:
            conn.execute(
                """
                INSERT INTO phrase_catalog (
                    phrase_id, canonical_form, communicative_intention,
                    construction_pattern, register_profiles_json, applicability
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(phrase_id) DO NOTHING
                """,
                (
                    phrase_id,
                    canonical_form,
                    communicative_intention,
                    construction_pattern,
                    json.dumps(register_profiles),
                    applicability,
                ),
            )
            conn.commit()

    def register_organic_phrase_from_diagnostic(
        self,
        *,
        normalized_pattern_id: str,
        canonical_form: str,
        communicative_intention: str,
        register_profile: str,
    ) -> str:
        clean_slug = re.sub(r"[^a-z0-9]+", "_", canonical_form.strip().lower()).strip("_")[:48]
        phrase_id = f"organic_{clean_slug}" if clean_slug else f"organic_{uuid.uuid4().hex[:8]}"
        self.register_phrase(
            phrase_id=phrase_id,
            canonical_form=canonical_form.strip(),
            communicative_intention=communicative_intention or normalized_pattern_id,
            construction_pattern=normalized_pattern_id,
            register_profiles=[register_profile, "casual", "diplomatic"],
            applicability="core",
        )
        return phrase_id

    # ------------------------------------------------------------------ event log (v1 and V2 writers)

    @staticmethod
    def _encode_payload(event_type: str, payload: Any, schema_version: str) -> str:
        """JSON text for ``payload_json``. A schema 2.x row of a type that has a V2 model is
        validated and normalised through that model (Pydantic, not CHECK constraints)."""
        major = str(schema_version).split(".", 1)[0]
        if major not in ("1", "2"):
            raise ValueError(f"unsupported schema_version {schema_version!r}")
        if isinstance(payload, BaseModel):
            data: Any = payload.model_dump(mode="json")
        elif isinstance(payload, dict):
            data = payload
        else:
            raise TypeError(f"payload must be a pydantic model or a dict, got {type(payload).__name__}")
        model = PAYLOAD_MODELS.get(event_type)
        if major == "2" and model is not None:
            data = model.model_validate(data).model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        learner_id: int,
        session_id: str,
        event_type: str,
        payload: Any,
        correlation_id: str,
        idempotency_key: str,
        causation_id: Optional[str],
        schema_version: str,
        rubric_version: Optional[str],
        model_version: Optional[str],
        occurred_at_utc: Optional[str],
        event_id: Optional[str],
    ) -> AppendResult:
        existing = conn.execute(
            "SELECT event_id, event_seq FROM event_log WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return AppendResult(False, str(existing["event_id"]), int(existing["event_seq"]))
        body = self._encode_payload(event_type, payload, schema_version)
        recorded = self._now_iso()
        ev_id = event_id or f"ev_{uuid.uuid4().hex[:12]}"
        cursor = conn.execute(
            """
            INSERT INTO event_log (
                event_id, learner_id, session_id, event_type,
                occurred_at_utc, recorded_at_utc, schema_version, model_version, rubric_version,
                correlation_id, causation_id, idempotency_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev_id,
                learner_id,
                session_id,
                event_type,
                occurred_at_utc or recorded,
                recorded,
                schema_version,
                model_version,
                rubric_version,
                correlation_id,
                causation_id,
                idempotency_key,
                body,
            ),
        )
        return AppendResult(True, ev_id, int(cursor.lastrowid))

    def append_event_ex(
        self,
        *,
        learner_id: int,
        session_id: str,
        event_type: str,
        payload: Any,
        correlation_id: str,
        idempotency_key: str,
        causation_id: Optional[str] = None,
        schema_version: str = SCHEMA_VERSION_V2,
        rubric_version: str = RUBRIC_VERSION_V2,
        model_version: Optional[str] = None,
        occurred_at_utc: Optional[str] = None,
        event_id: Optional[str] = None,
        rebuild: bool = True,
    ) -> AppendResult:
        """Append one event. Idempotent on ``idempotency_key`` (a repeat returns the stored event
        with ``inserted=False`` and changes nothing). Writes ``schema_version='2.0'`` by default."""
        with self.transaction() as conn:
            result = self._insert_event(
                conn,
                learner_id=learner_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                causation_id=causation_id,
                schema_version=schema_version,
                rubric_version=rubric_version,
                model_version=model_version,
                occurred_at_utc=occurred_at_utc,
                event_id=event_id,
            )
            if result.inserted and rebuild:
                rebuild_projections_in_tx(conn, learner_id=learner_id)
        return result

    def append_event(
        self,
        *,
        learner_id: int,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        causation_id: Optional[str] = None,
        occurred_at_utc: Optional[str] = None,
        event_id: Optional[str] = None,
        rebuild: bool = True,
    ) -> tuple[bool, str]:
        """v2 entry point, unchanged contract: ``(inserted, event_id)``. It wraps
        ``append_event_ex`` and stamps ``schema_version='1.0'`` / ``rubric_version='v1.0'``
        because every caller still builds v1-shaped payloads; readers upcast them. Callers move to
        ``append_event_ex`` (schema 2.0) when they start emitting V2 payloads."""
        result = self.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            causation_id=causation_id,
            schema_version=SCHEMA_VERSION_V1,
            rubric_version=RUBRIC_VERSION_V1,
            occurred_at_utc=occurred_at_utc,
            event_id=event_id,
            rebuild=rebuild,
        )
        return (result.inserted, result.event_id)

    # ------------------------------------------------------------------ revisions (A13, A14)

    def get_session_revision(self, session_id: str) -> int:
        """``session_revision``: bumped only by scene turns (``accept_scene_turn``)."""
        with self.read() as conn:
            row = conn.execute("SELECT session_revision FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(f"unknown session {session_id!r}")
        return int(row["session_revision"])

    def get_scene_revision(self, session_id: str) -> int:
        with self.read() as conn:
            row = conn.execute("SELECT scene_revision FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(f"unknown session {session_id!r}")
        return int(row["scene_revision"])

    def bump_scene_revision(self, session_id: str) -> int:
        """Increment and return ``scene_revision`` (the caller decides what counts as a new scene)."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET scene_revision = scene_revision + 1 WHERE session_id = ?", (session_id,)
            )
            if cursor.rowcount == 0:
                raise LookupError(f"unknown session {session_id!r}")
            return int(conn.execute("SELECT scene_revision FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0])

    def accept_scene_turn(
        self,
        *,
        learner_id: int,
        session_id: str,
        text: str,
        idempotency_key: str,
        origin: TurnOrigin = TurnOrigin.SCENE,
        correlation_id: str,
        input_modality: str = "text",
        interaction_id: Optional[int] = None,
        telegram_message_id: Optional[int] = None,
        occurred_at_utc: Optional[str] = None,
        rebuild: bool = True,
    ) -> tuple[AppendResult, int]:
        """One transaction: append ``learner_message_received`` (V2) and bump ``session_revision``.
        Returns the result and the new revision. A repeated ``idempotency_key`` changes nothing and
        returns the revision that the first call produced."""
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT event_id, event_seq, schema_version, payload_json FROM event_log WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                stored = json.loads(existing["payload_json"])
                revision = stored.get("session_revision")
                if revision is None:
                    row = conn.execute(
                        "SELECT session_revision FROM sessions WHERE session_id = ?", (session_id,)
                    ).fetchone()
                    revision = int(row["session_revision"]) if row else 0
                return AppendResult(False, str(existing["event_id"]), int(existing["event_seq"])), int(revision)
            row = conn.execute(
                "SELECT session_revision FROM sessions WHERE session_id = ? AND learner_id = ?",
                (session_id, learner_id),
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown session {session_id!r} for learner {learner_id}")
            new_revision = int(row["session_revision"]) + 1
            conn.execute(
                "UPDATE sessions SET session_revision = ? WHERE session_id = ? AND learner_id = ?",
                (new_revision, session_id, learner_id),
            )
            message = LearnerMessageV2(
                text=text,
                input_modality=input_modality,
                origin=TurnOrigin(origin),
                session_revision=new_revision,
                interaction_id=interaction_id,
                telegram_message_id=telegram_message_id,
            )
            result = self._insert_event(
                conn,
                learner_id=learner_id,
                session_id=session_id,
                event_type="learner_message_received",
                payload=message,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                causation_id=None,
                schema_version=SCHEMA_VERSION_V2,
                rubric_version=RUBRIC_VERSION_V2,
                model_version=None,
                occurred_at_utc=occurred_at_utc,
                event_id=None,
            )
            if rebuild:
                rebuild_projections_in_tx(conn, learner_id=learner_id)
        return result, new_revision

    def append_events_if_revision_ex(
        self, session_id: str, expected_revision: int, events: Sequence[EventSpec]
    ) -> Optional[list[AppendResult]]:
        """Compare-and-set: append every spec in one transaction only if ``session_revision`` still
        equals ``expected_revision``. Returns ``None`` (nothing written) when it has moved on,
        otherwise one ``AppendResult`` per spec. A replay is safe: specs whose idempotency key
        already exists are skipped (``inserted=False``)."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT learner_id, session_revision FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown session {session_id!r}")
            if int(row["session_revision"]) != int(expected_revision):
                return None
            results: list[AppendResult] = []
            to_rebuild: set[int] = set()
            for spec in events:
                learner_id = int(spec.learner_id if spec.learner_id is not None else row["learner_id"])
                result = self._insert_event(
                    conn,
                    learner_id=learner_id,
                    session_id=session_id,
                    event_type=spec.event_type,
                    payload=spec.payload,
                    correlation_id=spec.correlation_id,
                    idempotency_key=spec.idempotency_key,
                    causation_id=spec.causation_id,
                    schema_version=spec.schema_version,
                    rubric_version=spec.rubric_version,
                    model_version=spec.model_version,
                    occurred_at_utc=spec.occurred_at_utc,
                    event_id=spec.event_id,
                )
                results.append(result)
                if result.inserted and spec.rebuild:
                    to_rebuild.add(learner_id)
            for learner_id in sorted(to_rebuild):
                rebuild_projections_in_tx(conn, learner_id=learner_id)
            return results

    def append_events_if_revision(self, session_id: str, expected_revision: int, events: Sequence[EventSpec]) -> bool:
        """``False`` = the revision moved on and nothing was written (all-or-nothing)."""
        return self.append_events_if_revision_ex(session_id, expected_revision, events) is not None

    # ------------------------------------------------------------------ pending clarification (A14)

    def set_pending_clarification(self, session_id: str, text: Optional[str]) -> None:
        value = json.dumps(text, ensure_ascii=False) if text and text.strip() else None
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET pending_clarification_json = ? WHERE session_id = ?", (value, session_id)
            )
            if cursor.rowcount == 0:
                raise LookupError(f"unknown session {session_id!r}")

    def take_pending_clarification(self, session_id: str) -> Optional[str]:
        """Read and clear in one transaction: a stored P0 clarification is injected exactly once."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT pending_clarification_json FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown session {session_id!r}")
            raw = row["pending_clarification_json"]
            if raw is None:
                return None
            conn.execute("UPDATE sessions SET pending_clarification_json = NULL WHERE session_id = ?", (session_id,))
        return str(json.loads(raw))

    # ------------------------------------------------------------------ learner settings, audio cache

    def get_auto_voice(self, learner_id: int) -> bool:
        with self.read() as conn:
            row = conn.execute("SELECT auto_voice FROM learner_settings WHERE learner_id = ?", (learner_id,)).fetchone()
        return bool(row["auto_voice"]) if row else False

    def set_auto_voice(self, learner_id: int, on: bool) -> None:
        self.upsert_learner(learner_id)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO learner_settings (learner_id, auto_voice, updated_at_utc) VALUES (?, ?, ?)
                ON CONFLICT(learner_id) DO UPDATE SET
                    auto_voice = excluded.auto_voice, updated_at_utc = excluded.updated_at_utc
                """,
                (learner_id, 1 if on else 0, self._now_iso()),
            )

    def get_audio_file_id(self, script_hash: str, voice_config: str) -> Optional[str]:
        with self.read() as conn:
            row = conn.execute(
                "SELECT telegram_file_id FROM audio_cache WHERE script_hash = ? AND voice_config = ?",
                (script_hash, voice_config),
            ).fetchone()
        return str(row["telegram_file_id"]) if row else None

    def put_audio_file_id(
        self,
        script_hash: str,
        voice_config: str,
        file_id: str,
        file_unique_id: Optional[str],
        duration_s: Optional[float],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audio_cache (
                    script_hash, voice_config, telegram_file_id, telegram_file_unique_id, duration_s, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(script_hash, voice_config) DO UPDATE SET
                    telegram_file_id = excluded.telegram_file_id,
                    telegram_file_unique_id = excluded.telegram_file_unique_id,
                    duration_s = excluded.duration_s,
                    created_at_utc = excluded.created_at_utc
                """,
                (script_hash, voice_config, file_id, file_unique_id, duration_s, self._now_iso()),
            )

    def drop_audio_file_id(self, script_hash: str, voice_config: str) -> None:
        """Forget a cached file id (used when Telegram answers 'wrong file identifier')."""
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM audio_cache WHERE script_hash = ? AND voice_config = ?", (script_hash, voice_config)
            )

    # ------------------------------------------------------------------ projections

    def rebuild_all_projections(self) -> None:
        with self.transaction() as conn:
            rebuild_projections_in_tx(conn, learner_id=None)

    def _rebuild_projections_in_tx(self, conn: sqlite3.Connection, learner_id: Optional[int] = None) -> None:
        rebuild_projections_in_tx(conn, learner_id=learner_id)


# ==========================================================================================
# Projection rebuild (v1-visible columns reproduced exactly; V3.0 projections added).
#
# Every projection is a pure function of ``event_log`` read through ``upcast``. Nothing here
# reads the wall clock, so two consecutive rebuilds produce byte-identical tables (S0 4.3).
# Changes from v2, all needed for that property:
#   * phrase_state_projection.updated_at_utc is the time of the phrase's last event (v2 stamped
#     the wall-clock time of the rebuild).
# Not rebuilt here: the mastery aggregates of phrase_state_projection (success_count, dim_*,
# schedule_step, ...) and the retrieval/reconstruction events; they belong to mastery.py and
# scheduler.py (S3) and keep their column defaults until then.
# ==========================================================================================

_PROJECTION_TABLES = (
    "session_state_projection",
    "phrase_state_projection",
    "error_pattern_projection",
    "feedback_item_projection",
    "phrase_evidence",
)

_NON_FINDING_KINDS = frozenset(
    {
        IssueKind.OPTIONAL_UPGRADE.value,
        IssueKind.ACCEPTABLE_VARIATION.value,
        IssueKind.INSUFFICIENT_CONTEXT.value,
    }
)
_IN_SCENE_CARD_TRIGGERS = frozenset({"tap", "end_of_turn"})
_QUEUE_TRIGGERS = frozenset({"review", "debrief"})
_BACKFILL_SKIP_REASONS = frozenset({"shed", "deadline", "error"})

# S0 5.5, legacy phrase_production_assessed -> evidence rows: (dimension, score, weight)
_LEGACY_EVIDENCE_ROWS = {
    ProductionEvidence.RECOGNITION.value: (("recognition", 100.0, 1.0),),
    ProductionEvidence.CUED_PRODUCTION.value: (("production", 75.0, 0.8),),
    ProductionEvidence.INDEPENDENT_PRODUCTION.value: (("production", 75.0, 1.0), ("independence", 75.0, 1.0)),
    ProductionEvidence.SPONTANEOUS_PRODUCTION.value: (("production", 75.0, 1.0), ("independence", 75.0, 1.0)),
}


def _context_family_for_pattern(kind: str, d_raw: dict[str, Any]) -> str:
    """The family the error projection files a finding under (v2 rule, unchanged)."""
    if kind in (IssueKind.PRAGMATICS.value, IssueKind.REGISTER.value):
        return str(d_raw.get("relevant_context_family", "global"))
    return "global"


def _feedback_family(raw_family: Any) -> str:
    """v1 used 'global' for 'not family-specific'; V3.0 calls that 'general'."""
    family = str(raw_family or "general")
    return "general" if family == "global" else family


def _asks_question(reply: dict[str, Any]) -> bool:
    return "?" in str(reply.get("reply_text") or "") or "?" in str(reply.get("second_bubble_text") or "")


def rebuild_projections_in_tx(conn: sqlite3.Connection, learner_id: Optional[int] = None) -> None:
    """Rebuild every projection (or one learner's) from ``event_log`` inside the caller's transaction."""
    where, params = ("", ()) if learner_id is None else ("WHERE learner_id = ?", (learner_id,))
    for table in _PROJECTION_TABLES:
        conn.execute(f"DELETE FROM {table} {where}", params)

    session_rows = conn.execute(
        f"SELECT session_id, learner_id, started_at_utc FROM sessions {where}", params
    ).fetchall()
    event_rows = conn.execute(
        f"SELECT * FROM event_log {where} ORDER BY occurred_at_utc ASC, event_seq ASC", params
    ).fetchall()
    session_meta = {str(r["session_id"]): dict(r) for r in conn.execute("SELECT * FROM sessions").fetchall()}
    catalog_rows = {
        str(r["phrase_id"]): str(r["applicability"])
        for r in conn.execute("SELECT phrase_id, applicability FROM phrase_catalog").fetchall()
    }

    events: list[EventV2] = [upcast(r, validate=False) for r in event_rows]

    # ---- indexes built before the main pass (they do not depend on event order)
    seq_by_event_id: dict[str, int] = {}
    retracted_diagnostics: set[tuple[str, str]] = set()
    retracted_phrase_events: set[str] = set()
    skipped_turns: dict[str, int] = {}
    diag_by_id: dict[str, dict[str, Any]] = {}
    alias_by_corr: dict[tuple[str, str], str] = {}
    alias_by_event: dict[tuple[str, str], str] = {}
    diags_by_corr: dict[str, list[str]] = {}
    for ev in events:
        seq_by_event_id[ev.event_id] = ev.event_seq
        payload = ev.payload
        if ev.event_type == "diagnostic_retracted":
            retracted_diagnostics.add((str(payload.get("target_event_id", "")), str(payload.get("diagnostic_id", ""))))
        elif ev.event_type == "phrase_evidence_retracted":
            retracted_phrase_events.add(str(payload.get("target_event_id", "")))
        elif ev.event_type == "analysis_skipped":
            if payload.get("reason") in _BACKFILL_SKIP_REASONS:
                skipped_turns.setdefault(str(payload.get("turn_event_id", "")), ev.event_seq)
        elif ev.event_type == "analyst_diagnosis_recorded":
            for d_raw in payload.get("diagnostics") or []:
                dg_id = str(d_raw.get("diagnostic_id", ""))
                diag_by_id[dg_id] = {"event": ev, "raw": d_raw}
                diags_by_corr.setdefault(ev.correlation_id, []).append(dg_id)
                legacy_id = ev.legacy_diagnostic_ids.get(dg_id)
                if legacy_id is not None:
                    alias_by_corr.setdefault((ev.correlation_id, legacy_id), dg_id)
                    alias_by_event.setdefault((ev.event_id, legacy_id), dg_id)

    def diagnostic_retracted(analysis_event: EventV2, dg_id: str) -> bool:
        if (analysis_event.event_id, dg_id) in retracted_diagnostics:
            return True
        legacy_id = analysis_event.legacy_diagnostic_ids.get(dg_id)
        return legacy_id is not None and (analysis_event.event_id, legacy_id) in retracted_diagnostics

    def resolve_diagnostic(ev: EventV2, reference: str) -> Optional[str]:
        """A diagnostic id as written on ``ev`` (a dg_ id, or a v1 id on a v1 row) -> dg_ id."""
        if reference in diag_by_id:
            return reference
        return alias_by_corr.get((ev.correlation_id, reference))

    def pattern_key_of(dg_id: str) -> Optional[tuple[str, str]]:
        info = diag_by_id.get(dg_id)
        if info is None:
            return None
        raw = info["raw"]
        pattern = raw.get("normalized_pattern_id")
        if not pattern:
            return None
        return (str(pattern), _context_family_for_pattern(str(raw.get("kind", "")), raw))

    # ---- state
    session_state: dict[str, dict[str, Any]] = {}
    for s in session_rows:
        sid = str(s["session_id"])
        session_state[sid] = {
            "session_id": sid,
            "learner_id": int(s["learner_id"]),
            "turns_since_visible_card": 4,
            "recent_questions": [],
            "revealed_targets": [],
            "cued_targets": [],
            "cards_in_scene": 0,
            "turns_since_p0": 99,
            "updated_at_utc": str(s["started_at_utc"]),
        }
    error_proj: dict[tuple[int, str, str], dict[str, Any]] = {}
    seen_occurrence_ids: set[str] = set()
    phrase_events_map: dict[tuple[int, str], list[dict[str, Any]]] = {}
    evidence_rows: list[tuple[Any, ...]] = []
    feedback_items: dict[str, dict[str, Any]] = {}

    for ev in events:
        ev_id = ev.event_id
        lid = ev.learner_id
        sid = ev.session_id
        etype = ev.event_type
        occ_ts = ev.occurred_at_utc
        corr_id = ev.correlation_id
        payload = ev.payload

        s_proj = session_state.get(sid)
        if s_proj:
            s_proj["updated_at_utc"] = occ_ts

        if etype == "phrase_exposure_recorded":
            pid = str(payload["phrase_id"])
            was_revealed = bool(payload.get("revealed_to_learner", True))
            was_cued = bool(payload.get("directly_cued", False))
            if s_proj:
                if was_revealed and pid not in s_proj["revealed_targets"]:
                    s_proj["revealed_targets"].append(pid)
                if was_cued and pid not in s_proj["cued_targets"]:
                    s_proj["cued_targets"].append(pid)
            phrase_events_map.setdefault((lid, pid), []).append(
                {
                    "event_id": ev_id,
                    "session_id": sid,
                    "occurred_at_utc": occ_ts,
                    "kind": "exposure",
                    "evidence": "none",
                    "valid": True,
                }
            )

        elif etype == "analyst_diagnosis_recorded":
            backfilled = skipped_turns.get(str(payload.get("turn_event_id", "")), ev.event_seq + 1) < ev.event_seq
            turn_seq = seq_by_event_id.get(str(payload.get("turn_event_id", "")), ev.event_seq)
            seen_in_this_turn: set[tuple[str, str]] = set()
            for d_raw in payload.get("diagnostics") or []:
                dg_id = str(d_raw.get("diagnostic_id", ""))
                kind_str = str(d_raw.get("kind", ""))

                # feedback_item_projection: every surviving finding becomes a queue item
                if dg_id and dg_id not in feedback_items:
                    feedback_items[dg_id] = {
                        "diagnostic_id": dg_id,
                        "learner_id": lid,
                        "session_id": sid,
                        "turn_event_seq": turn_seq,
                        "pattern_id": str(d_raw.get("normalized_pattern_id", "")),
                        "context_family": _feedback_family(d_raw.get("relevant_context_family")),
                        "domain": str(d_raw.get("domain", "linguistic")),
                        "classification": str(d_raw.get("classification", "error")),
                        "repair_scope": str(d_raw.get("repair_scope", "phrase_span")),
                        "kind": kind_str,
                        "severity": str(d_raw.get("severity", "medium")),
                        "original_span": str(d_raw.get("original_span", "")),
                        "alternative": str(d_raw.get("alternative") or ""),
                        "explanation_en": d_raw.get("explanation_en"),
                        "explanation_ar": d_raw.get("explanation_ar"),
                        "priority_class": 4,
                        "priority_score": 0.0,
                        "delivery": "deferred" if backfilled else "suppressed",
                        "status": "retracted" if diagnostic_retracted(ev, dg_id) else "pending",
                        "merged_into": None,
                        "created_at_utc": occ_ts,
                        "delivered_at_utc": None,
                        "backfilled": backfilled,
                        "event_id": ev_id,
                    }

                # error_pattern_projection (v2 rules)
                if diagnostic_retracted(ev, dg_id):
                    continue
                if kind_str in _NON_FINDING_KINDS or str(d_raw.get("classification", "error")) != "error":
                    continue
                if not d_raw.get("preserves_intended_meaning") or not d_raw.get("context_fit_verified"):
                    continue
                if d_raw.get("confidence_band") == ConfidenceBand.LOW.value or float(d_raw.get("confidence_score", 0.0)) < 0.65:
                    continue
                pat_id = d_raw.get("normalized_pattern_id")
                if not pat_id:
                    continue
                pat_id = str(pat_id)
                ctx_fam = _context_family_for_pattern(kind_str, d_raw)
                if (pat_id, ctx_fam) in seen_in_this_turn:
                    continue
                seen_in_this_turn.add((pat_id, ctx_fam))

                occurrence_key = f"{corr_id}:{pat_id}:{ctx_fam}"
                if occurrence_key in seen_occurrence_ids:
                    continue
                seen_occurrence_ids.add(occurrence_key)

                entry = error_proj.setdefault(
                    (lid, pat_id, ctx_fam),
                    {
                        "issue_kind": kind_str,
                        "distinct_eligible_occurrences": 0,
                        "times_feedback_selected": 0,
                        "times_feedback_shown": 0,
                        "workout_scheduled": 0,
                        "last_occurrence_event_id": ev_id,
                        "updated_at_utc": occ_ts,
                    },
                )
                entry["distinct_eligible_occurrences"] += 1
                entry["last_occurrence_event_id"] = ev_id
                entry["updated_at_utc"] = occ_ts

        elif etype == "policy_decision_recorded":
            selected_key: Optional[tuple[str, str]] = None
            selected_delivered = False
            if ev.is_legacy:
                if ev.legacy.get("intervention") == InterventionLevel.BRIEF_CARD.value:
                    pat_id = ev.legacy.get("selected_pattern_id")
                    if pat_id:
                        selected_key = (str(pat_id), str(ev.legacy.get("selected_context_family", "global")))
                        selected_delivered = True
            else:
                selected_ref = payload.get("selected_diagnostic_id")
                resolved = resolve_diagnostic(ev, str(selected_ref)) if selected_ref else None
                if resolved is not None:
                    for decision in payload.get("decisions") or []:
                        if str(decision.get("diagnostic_id")) == resolved and decision.get("delivery") in (
                            "end_of_turn",
                            "immediate_followup",
                        ):
                            selected_key = pattern_key_of(resolved)
                            selected_delivered = selected_key is not None
            if selected_delivered and selected_key is not None and (lid, *selected_key) in error_proj:
                error_proj[(lid, *selected_key)]["times_feedback_selected"] += 1
                error_proj[(lid, *selected_key)]["updated_at_utc"] = occ_ts
            for decision in payload.get("decisions") or []:
                resolved = resolve_diagnostic(ev, str(decision.get("diagnostic_id", "")))
                item = feedback_items.get(resolved) if resolved else None
                if item is None:
                    continue
                item["priority_class"] = int(decision.get("priority_class", 4))
                item["priority_score"] = float(decision.get("priority_score", 0.0))
                item["delivery"] = "deferred" if item["backfilled"] else str(decision.get("delivery", "deferred"))
                if s_proj and int(decision.get("priority_class", 4)) == 0 and decision.get("delivery") == "immediate_followup":
                    s_proj["turns_since_p0"] = 0

        elif etype == "responder_reply_delivered":
            if s_proj:
                s_proj["turns_since_visible_card"] += 1
                s_proj["turns_since_p0"] = min(99, s_proj["turns_since_p0"] + 1)
                if ev.is_legacy:
                    asked_opt_q = bool(ev.legacy.get("asked_optional_question", False))
                    was_clarification = bool(ev.legacy.get("was_clarification_question", False))
                else:
                    was_clarification = payload.get("comprehension_status", "ok") != "ok"
                    asked_opt_q = _asks_question(payload) and not was_clarification
                if not was_clarification:
                    s_proj["recent_questions"].append(asked_opt_q)
                    s_proj["recent_questions"] = s_proj["recent_questions"][-10:]

        elif etype == "feedback_card_delivered":
            trigger = str(payload.get("trigger", "tap"))
            if ev.is_legacy:
                pat_id = ev.legacy.get("normalized_pattern_id")
                key: Optional[tuple[str, str]] = (
                    (str(pat_id), str(ev.legacy.get("relevant_context_family", "global"))) if pat_id else None
                )
                reference = str(ev.legacy.get("diagnostic_id", "") or "")
                resolved = resolve_diagnostic(ev, reference) if reference else None
                if resolved is None and pat_id:
                    for candidate in diags_by_corr.get(corr_id, []):
                        if (diag_by_id[candidate]["raw"].get("normalized_pattern_id")) == pat_id:
                            resolved = candidate
                            break
            else:
                resolved = resolve_diagnostic(ev, str(payload.get("diagnostic_id", "")))
                key = pattern_key_of(resolved) if resolved else None
            if s_proj and trigger in _IN_SCENE_CARD_TRIGGERS:
                s_proj["turns_since_visible_card"] = 0
                s_proj["cards_in_scene"] += 1
            if key is not None and (lid, *key) in error_proj:
                error_proj[(lid, *key)]["times_feedback_shown"] += 1
                error_proj[(lid, *key)]["updated_at_utc"] = occ_ts
            item = feedback_items.get(resolved) if resolved else None
            if item is not None and item["status"] in ("pending", "merged"):
                item["status"] = "delivered"
                item["merged_into"] = None
                item["delivered_at_utc"] = occ_ts
                if trigger in _QUEUE_TRIGGERS:
                    # S0 5.8: delivering the queue's representative merges the earlier items of the same pattern
                    delivered_created = parse_iso(item["created_at_utc"])
                    for other in feedback_items.values():
                        if (
                            other is not item
                            and other["learner_id"] == item["learner_id"]
                            and other["pattern_id"] == item["pattern_id"]
                            and other["status"] == "pending"
                            and parse_iso(other["created_at_utc"]) <= delivered_created
                        ):
                            other["status"] = "merged"
                            other["merged_into"] = item["diagnostic_id"]

        elif etype == "diagnostic_retracted":
            target = str(payload.get("target_event_id", ""))
            reference = str(payload.get("diagnostic_id", ""))
            resolved = alias_by_event.get((target, reference)) or (reference if reference in feedback_items else None)
            item = feedback_items.get(resolved) if resolved else None
            if item is not None and item["event_id"] == target:
                item["status"] = "retracted"

        elif etype == "micro_workout_scheduled":
            pat_id = payload.get("normalized_pattern_id")
            ctx_fam = payload.get("relevant_context_family", "global")
            if pat_id and (lid, pat_id, ctx_fam) in error_proj:
                error_proj[(lid, pat_id, ctx_fam)]["workout_scheduled"] = 1
                error_proj[(lid, pat_id, ctx_fam)]["updated_at_utc"] = occ_ts

        elif etype == "micro_workout_completed":
            pat_id = payload.get("normalized_pattern_id")
            ctx_fam = payload.get("relevant_context_family", "global")
            if pat_id and (lid, pat_id, ctx_fam) in error_proj:
                error_proj[(lid, pat_id, ctx_fam)]["workout_scheduled"] = 0
                error_proj[(lid, pat_id, ctx_fam)]["updated_at_utc"] = occ_ts

        elif etype == "phrase_production_assessed":
            pid = str(payload["target_id"])
            retracted = ev_id in retracted_phrase_events
            s_info = session_meta.get(sid, {})
            reg_used = str(payload.get("register_profile") or s_info.get("register_profile") or "casual")
            t_mode = str(payload.get("training_mode") or s_info.get("training_mode") or "open_scene")
            item = {
                "event_id": ev_id,
                "session_id": sid,
                "occurred_at_utc": occ_ts,
                "kind": "assessment",
                "evidence": str(payload.get("evidence", "none")),
                "was_revealed_in_task": bool(payload.get("was_revealed_in_task", False)),
                "was_directly_cued": bool(payload.get("was_directly_cued", False)),
                "fits_communicative_context": bool(payload.get("fits_communicative_context", False)),
                "fits_register": bool(payload.get("fits_register", False)),
                "phrase_specific_rubric_passed": bool(payload.get("phrase_specific_rubric_passed", False)),
                "is_novel_context": bool(payload.get("is_novel_context", False)),
                "register_profile": reg_used,
                "training_mode": t_mode,
                "failed_attempt": bool(payload.get("failed_attempt", False)),
            }
            if not retracted:
                phrase_events_map.setdefault((lid, pid), []).append(item)
            # S0 5.5: evidence rows exist exactly where the v1 ladder counted the attempt as valid
            valid_rubric = (
                item["fits_communicative_context"] and item["fits_register"] and item["phrase_specific_rubric_passed"]
            )
            counted = valid_rubric and not item["failed_attempt"]
            ev_type = item["evidence"]
            if ev_type == ProductionEvidence.CUED_PRODUCTION.value:
                counted = counted and not item["was_revealed_in_task"]
            elif ev_type in (
                ProductionEvidence.INDEPENDENT_PRODUCTION.value,
                ProductionEvidence.SPONTANEOUS_PRODUCTION.value,
            ):
                counted = counted and not item["was_revealed_in_task"] and not item["was_directly_cued"]
            if counted and ev_type in _LEGACY_EVIDENCE_ROWS and pid in catalog_rows:
                origin = "spontaneous" if ev_type == ProductionEvidence.SPONTANEOUS_PRODUCTION.value else "drill"
                hint_level = 1 if ev_type == ProductionEvidence.CUED_PRODUCTION.value else 0
                for dimension, score, weight in _LEGACY_EVIDENCE_ROWS[ev_type]:
                    evidence_rows.append(
                        (ev_id, dimension, lid, pid, score, weight, "general", origin, hint_level, 0, None, 0, occ_ts, 1 if retracted else 0)
                    )

    # ---- write: sessions, error patterns, feedback items, evidence
    for sid, s_proj in session_state.items():
        conn.execute(
            """
            INSERT INTO session_state_projection (
                session_id, learner_id, turns_since_visible_card,
                recent_questions_json, revealed_targets_json, cued_targets_json, updated_at_utc,
                cards_in_scene, turns_since_p0
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                s_proj["learner_id"],
                s_proj["turns_since_visible_card"],
                json.dumps(s_proj["recent_questions"]),
                json.dumps(s_proj["revealed_targets"]),
                json.dumps(s_proj["cued_targets"]),
                s_proj["updated_at_utc"],
                s_proj["cards_in_scene"],
                s_proj["turns_since_p0"],
            ),
        )

    for (lid, pat_id, ctx_fam), ep in error_proj.items():
        conn.execute(
            """
            INSERT INTO error_pattern_projection (
                learner_id, normalized_pattern_id, relevant_context_family,
                issue_kind, distinct_eligible_occurrences, times_feedback_selected,
                times_feedback_shown, workout_scheduled, last_occurrence_event_id, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid,
                pat_id,
                ctx_fam,
                ep["issue_kind"],
                ep["distinct_eligible_occurrences"],
                ep["times_feedback_selected"],
                ep["times_feedback_shown"],
                ep["workout_scheduled"],
                ep["last_occurrence_event_id"],
                ep["updated_at_utc"],
            ),
        )

    for item in feedback_items.values():
        conn.execute(
            """
            INSERT INTO feedback_item_projection (
                diagnostic_id, learner_id, session_id, turn_event_seq, pattern_id, context_family,
                domain, classification, repair_scope, kind, severity, original_span, alternative,
                explanation_en, explanation_ar, priority_class, priority_score, delivery, status,
                merged_into, created_at_utc, delivered_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["diagnostic_id"], item["learner_id"], item["session_id"], item["turn_event_seq"],
                item["pattern_id"], item["context_family"], item["domain"], item["classification"],
                item["repair_scope"], item["kind"], item["severity"], item["original_span"], item["alternative"],
                item["explanation_en"], item["explanation_ar"], item["priority_class"], item["priority_score"],
                item["delivery"], item["status"], item["merged_into"], item["created_at_utc"], item["delivered_at_utc"],
            ),
        )

    for row in evidence_rows:
        conn.execute(
            """
            INSERT INTO phrase_evidence (
                source_event_id, dimension, learner_id, phrase_id, score, weight, context_family,
                origin, hint_level, is_delayed, gap_days, echo, occurred_at_utc, retracted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )

    # ---- phrase_state_projection: the v2 stage ladder, unchanged
    for (lid, pid), ev_list in phrase_events_map.items():
        if pid not in catalog_rows:
            continue
        applicability = catalog_rows[pid]
        exposure_count = 0
        recognition_count = 0
        cued_count = 0
        independent_count = 0
        spontaneous_count = 0
        qualifying_trials: list[dict[str, Any]] = []
        has_novel_context = False
        last_ev_id = ev_list[-1]["event_id"]
        latest_ts = _parse_iso_utc(ev_list[-1]["occurred_at_utc"])

        recent_failure_active = False
        successes_since_last_recent_failure = 0

        for item in ev_list:
            if item["kind"] == "exposure":
                exposure_count += 1
                continue

            item_ts = _parse_iso_utc(item["occurred_at_utc"])
            age_days = (latest_ts - item_ts).total_seconds() / 86400.0

            if item.get("failed_attempt"):
                if age_days <= RECENT_FAILURE_WINDOW_DAYS:
                    recent_failure_active = True
                    successes_since_last_recent_failure = 0
                continue

            ev_type = item["evidence"]
            valid_rubric = (
                item["fits_communicative_context"]
                and item["fits_register"]
                and item["phrase_specific_rubric_passed"]
            )

            if ev_type == ProductionEvidence.RECOGNITION.value and valid_rubric:
                recognition_count += 1
            elif ev_type == ProductionEvidence.CUED_PRODUCTION.value and valid_rubric and not item["was_revealed_in_task"]:
                cued_count += 1
            elif (
                ev_type in (ProductionEvidence.INDEPENDENT_PRODUCTION.value, ProductionEvidence.SPONTANEOUS_PRODUCTION.value)
                and valid_rubric
                and not item["was_revealed_in_task"]
                and not item["was_directly_cued"]
            ):
                if ev_type == ProductionEvidence.SPONTANEOUS_PRODUCTION.value and item["training_mode"] == TrainingMode.OPEN_SCENE.value:
                    spontaneous_count += 1
                    is_spont_open = True
                else:
                    independent_count += 1
                    is_spont_open = False

                if item.get("is_novel_context"):
                    has_novel_context = True

                qualifying_trials.append(
                    {
                        "session_id": item["session_id"],
                        "register_profile": item["register_profile"],
                        "ts": item_ts,
                        "is_spont_open": is_spont_open,
                    }
                )
                if recent_failure_active:
                    successes_since_last_recent_failure += 1
                    if successes_since_last_recent_failure >= 2:
                        recent_failure_active = False

        distinct_sessions = len({t["session_id"] for t in qualifying_trials})
        distinct_registers = len({t["register_profile"] for t in qualifying_trials})
        span_days = (
            (qualifying_trials[-1]["ts"] - qualifying_trials[0]["ts"]).total_seconds() / 86400.0
            if len(qualifying_trials) >= 2
            else 0.0
        )
        spont_open_count = sum(1 for t in qualifying_trials if t["is_spont_open"])

        is_stable = (
            applicability != "context_limited"
            and not recent_failure_active
            and len(qualifying_trials) >= 3
            and spont_open_count >= 1
            and distinct_sessions >= 3
            and distinct_registers >= 2
            and span_days >= 7.0
            and has_novel_context
        )

        if is_stable:
            stage = "Stable"
        elif spontaneous_count >= 1:
            stage = "Produced-spontaneously"
        elif independent_count >= 1:
            stage = "Produced-independently"
        elif cued_count >= 1:
            stage = "Produced-with-prompt"
        elif recognition_count >= 1:
            stage = "Recognized"
        elif exposure_count >= 1:
            stage = "Seen"
        else:
            stage = "Unseen"

        conn.execute(
            """
            INSERT INTO phrase_state_projection (
                learner_id, phrase_id, current_stage,
                exposure_count, recognition_count, cued_count,
                independent_count, spontaneous_count,
                distinct_valid_sessions, distinct_valid_registers,
                span_days, has_novel_context, recent_unretracted_failure,
                last_evidence_event_id, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid,
                pid,
                stage,
                exposure_count,
                recognition_count,
                cued_count,
                independent_count,
                spontaneous_count,
                distinct_sessions,
                distinct_registers,
                round(span_days, 4),
                1 if has_novel_context else 0,
                1 if recent_failure_active else 0,
                last_ev_id,
                ev_list[-1]["occurred_at_utc"],
            ),
        )


# ==========================================================================================
# Command line: python -m database migrate|status --db-path <path>
# Exit codes: 0 ok, 1 error (checksum mismatch, verification failure, database ahead of code,
# unreadable file), 10 = `status --check` found pending migrations.
# ==========================================================================================


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m database", description="Spoken English Lab database tools")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate_cmd = commands.add_parser("migrate", help="apply pending migrations (idempotent)")
    migrate_cmd.add_argument("--db-path", default=DB_PATH)
    status_cmd = commands.add_parser("status", help="print the migration state as JSON (read-only)")
    status_cmd.add_argument("--db-path", default=DB_PATH)
    status_cmd.add_argument("--check", action="store_true", help="exit 10 when migrations are pending")
    args = parser.parse_args(argv)

    try:
        if args.command == "migrate":
            applied = _migrations.migrate(args.db_path, baseline_ddl=BASELINE_DDL)
            report = _migrations.status(args.db_path)
            report["applied_now"] = applied
            print(json.dumps(report, sort_keys=True))
            return 0
        report = _migrations.status(args.db_path)
        print(json.dumps(report, sort_keys=True))
        if args.check and report["pending"]:
            return 10
        return 0
    except Exception as exc:  # the workflow only needs a non-zero exit code and the reason on stderr
        print(f"{args.command} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Run the CLI from the importable module, so `from database import ...` inside a migration is the
    # same module object as the one that is running (``python -m database`` starts as ``__main__``).
    from database import main as _main

    raise SystemExit(_main())
