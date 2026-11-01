"""M002 (S0 section 4.2): V3.0 tables and columns.

Additive only: nothing is dropped or rewritten, ``learners`` keeps the existing settings columns
and ``learner_settings`` holds only V3 additions (``auto_voice`` moves out of process memory).
``active_interaction`` is operational state (mutable, TTL), not evidence: outcomes are events.
``feedback_item_projection`` and ``phrase_evidence`` are projections: they carry no AUTOINCREMENT
ids, so a rebuild reproduces them exactly.
"""

from __future__ import annotations

import sqlite3

from migrations import Migration, MigrationContext, VerificationError

VERSION = 2
NAME = "v3_tables_and_columns"

SQL = """
ALTER TABLE sessions ADD COLUMN session_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN scene_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN primary_context_family TEXT NOT NULL DEFAULT 'general';
ALTER TABLE sessions ADD COLUMN pending_clarification_json TEXT;

ALTER TABLE session_state_projection ADD COLUMN cards_in_scene INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session_state_projection ADD COLUMN turns_since_p0 INTEGER NOT NULL DEFAULT 99;

ALTER TABLE phrase_catalog ADD COLUMN catalog_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE phrase_catalog ADD COLUMN lexical_structure_type TEXT;
ALTER TABLE phrase_catalog ADD COLUMN context_families_json TEXT NOT NULL DEFAULT '["general"]';
ALTER TABLE phrase_catalog ADD COLUMN subskill_id TEXT;
ALTER TABLE phrase_catalog ADD COLUMN arabic_gloss TEXT;
ALTER TABLE phrase_catalog ADD COLUMN acceptable_forms_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE phrase_catalog ADD COLUMN meaning_prompt TEXT;
ALTER TABLE phrase_catalog ADD COLUMN situation_prompt TEXT;
ALTER TABLE phrase_catalog ADD COLUMN function_prompt TEXT;

ALTER TABLE phrase_state_projection ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN independent_success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN spontaneous_success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN delayed_success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN max_delayed_gap_days REAL NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN context_families_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE phrase_state_projection ADD COLUMN home_context_family TEXT;
ALTER TABLE phrase_state_projection ADD COLUMN dim_r REAL;
ALTER TABLE phrase_state_projection ADD COLUMN dim_p REAL;
ALTER TABLE phrase_state_projection ADD COLUMN dim_i REAL;
ALTER TABLE phrase_state_projection ADD COLUMN dim_t REAL;
ALTER TABLE phrase_state_projection ADD COLUMN dim_a REAL;
ALTER TABLE phrase_state_projection ADD COLUMN mastery_index REAL;
ALTER TABLE phrase_state_projection ADD COLUMN schedule_step INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrase_state_projection ADD COLUMN last_outcome TEXT;
ALTER TABLE phrase_state_projection ADD COLUMN last_success_at_utc TEXT;
ALTER TABLE phrase_state_projection ADD COLUMN unresolved_critical_errors INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_psp_due ON phrase_state_projection(learner_id, next_review_at_utc)
  WHERE next_review_at_utc IS NOT NULL;

CREATE TABLE phrase_evidence (
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
CREATE INDEX idx_phrase_evidence_lp ON phrase_evidence(learner_id, phrase_id, occurred_at_utc);

CREATE TABLE feedback_item_projection (
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
CREATE INDEX idx_fip_queue ON feedback_item_projection(learner_id, status, priority_score DESC);
CREATE INDEX idx_fip_pattern ON feedback_item_projection(learner_id, pattern_id, created_at_utc);

CREATE TABLE active_interaction (
  interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  learner_id INTEGER NOT NULL UNIQUE REFERENCES learners(learner_id),
  session_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_ref TEXT NOT NULL,
  state TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  scene_revision_at_open INTEGER NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json)),
  created_at_utc TEXT NOT NULL,
  expires_at_utc TEXT NOT NULL
);

CREATE TABLE learner_settings (
  learner_id INTEGER PRIMARY KEY REFERENCES learners(learner_id),
  auto_voice INTEGER NOT NULL DEFAULT 0,
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE audio_cache (
  script_hash TEXT NOT NULL,
  voice_config TEXT NOT NULL,
  telegram_file_id TEXT NOT NULL,
  telegram_file_unique_id TEXT,
  duration_s REAL,
  created_at_utc TEXT NOT NULL,
  PRIMARY KEY (script_hash, voice_config)
);

CREATE TABLE competency_subskills (
  subskill_id TEXT PRIMARY KEY,
  competency_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  catalog_version TEXT NOT NULL
);

CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# S0 4.2: rows in learner_settings are created for every existing learner (auto_voice off) and
# lazily for new ones. The timestamp comes from the injected clock, so it is a bound parameter.
BACKFILL_SQL = (
    "INSERT OR IGNORE INTO learner_settings (learner_id, auto_voice, updated_at_utc) "
    "SELECT learner_id, 0, ? FROM learners"
)

EXPECTED_TABLES = {
    "phrase_evidence", "feedback_item_projection", "active_interaction", "learner_settings",
    "audio_cache", "competency_subskills", "meta",
}
EXPECTED_COLUMNS = {
    "sessions": {"session_revision", "scene_revision", "primary_context_family", "pending_clarification_json"},
    "session_state_projection": {"cards_in_scene", "turns_since_p0"},
    "phrase_catalog": {
        "catalog_version", "lexical_structure_type", "context_families_json", "subskill_id",
        "arabic_gloss", "acceptable_forms_json", "meaning_prompt", "situation_prompt", "function_prompt",
    },
    "phrase_state_projection": {
        "success_count", "independent_success_count", "spontaneous_success_count",
        "delayed_success_count", "max_delayed_gap_days", "context_families_json",
        "home_context_family", "dim_r", "dim_p", "dim_i", "dim_t", "dim_a", "mastery_index",
        "schedule_step", "last_outcome", "last_success_at_utc", "unresolved_critical_errors",
    },
}


def run(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    conn.execute(BACKFILL_SQL, (ctx.now_iso,))


def verify(conn: sqlite3.Connection, ctx: MigrationContext, snapshot: object) -> None:
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing_tables = EXPECTED_TABLES - tables
    if missing_tables:
        raise VerificationError(f"M002 did not create tables: {sorted(missing_tables)}")
    for table, expected in EXPECTED_COLUMNS.items():
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        absent = expected - present
        if absent:
            raise VerificationError(f"M002 did not add columns to {table}: {sorted(absent)}")
    learners = conn.execute("SELECT COUNT(*) FROM learners").fetchone()[0]
    settings = conn.execute("SELECT COUNT(*) FROM learner_settings").fetchone()[0]
    if settings != learners:
        raise VerificationError(f"learner_settings has {settings} rows for {learners} learners")


MIGRATION = Migration(
    version=VERSION,
    name=NAME,
    sql=SQL,
    fk_off=False,
    logic_tag="m002/1",
    run=run,
    verify=verify,
)
