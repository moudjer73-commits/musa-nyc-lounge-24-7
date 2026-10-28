import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from config import DB_PATH
from schemas import ConfidenceBand, InterventionLevel, IssueKind, ProductionEvidence, TrainingMode

RECENT_FAILURE_WINDOW_DAYS = 14.0

SQLITE_EVENT_SOURCED_DDL = """
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


class EventSourcedDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(SQLITE_EVENT_SOURCED_DDL)
            conn.commit()
        for pid, form, intent, pat, regs, app in DEFAULT_CURATED_PHRASES:
            self.register_phrase(pid, form, intent, pat, regs, app)

    def upsert_learner(
        self,
        learner_id: int,
        feedback_preference: str = "light",
        opted_in_workouts: bool = True,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO learners (learner_id, feedback_preference, opted_in_workouts, created_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(learner_id) DO UPDATE SET
                    feedback_preference = excluded.feedback_preference,
                    opted_in_workouts = excluded.opted_in_workouts
                """,
                (learner_id, feedback_preference, 1 if opted_in_workouts else 0, _now_iso_utc()),
            )
            conn.commit()

    def get_learner_settings(self, learner_id: int) -> dict[str, Any]:
        self.upsert_learner(learner_id)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM learners WHERE learner_id = ?", (learner_id,)).fetchone()
            return dict(row)

    def update_learner_preference(self, learner_id: int, *, feedback_preference: Optional[str] = None, default_register: Optional[str] = None) -> None:
        self.upsert_learner(learner_id)
        with self._conn() as conn:
            if feedback_preference in ("silent", "light", "active"):
                conn.execute("UPDATE learners SET feedback_preference = ? WHERE learner_id = ?", (feedback_preference, learner_id))
            if default_register:
                conn.execute("UPDATE learners SET default_register = ? WHERE learner_id = ?", (default_register, learner_id))
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
        started = started_at_utc or _now_iso_utc()
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
        """
        Selects at most 1 non-Stable core expression that fits the active register
        for organic stealth elicitation.
        """
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
                (card_id, learner_id, session_id, correlation_id, reply_text, json.dumps(diagnostic_dict, ensure_ascii=False), _now_iso_utc()),
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
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO phrase_catalog (
                    phrase_id, canonical_form, communicative_intention,
                    construction_pattern, register_profiles_json, applicability
                ) VALUES (?, ?, ?, ?, ?, ?)
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
        """
        Dynamically registers a natural spoken expression discovered during the learner's
        own spontaneous conversation so the phrase catalog grows organically without static bias.
        """
        import re as _re
        clean_slug = _re.sub(r"[^a-z0-9]+", "_", canonical_form.strip().lower()).strip("_")[:48]
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
    ) -> tuple[bool, str]:
        occ = occurred_at_utc or _now_iso_utc()
        rec = _now_iso_utc()
        ev_id = event_id or f"ev_{uuid.uuid4().hex[:12]}"

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT event_id FROM event_log WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return (False, str(existing["event_id"]))

            conn.execute(
                """
                INSERT INTO event_log (
                    event_id, learner_id, session_id, event_type,
                    occurred_at_utc, recorded_at_utc, correlation_id,
                    causation_id, idempotency_key, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id,
                    learner_id,
                    session_id,
                    event_type,
                    occ,
                    rec,
                    correlation_id,
                    causation_id,
                    idempotency_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._rebuild_projections_in_tx(conn, learner_id=learner_id)
            conn.commit()
            return (True, ev_id)

    def rebuild_all_projections(self) -> None:
        with self._conn() as conn:
            self._rebuild_projections_in_tx(conn, learner_id=None)
            conn.commit()

    def _rebuild_projections_in_tx(self, conn: sqlite3.Connection, learner_id: Optional[int] = None) -> None:
        now_str = _now_iso_utc()
        if learner_id is None:
            conn.execute("DELETE FROM session_state_projection")
            conn.execute("DELETE FROM phrase_state_projection")
            conn.execute("DELETE FROM error_pattern_projection")
            sessions_rows = conn.execute("SELECT session_id, learner_id, started_at_utc FROM sessions").fetchall()
            events = conn.execute(
                "SELECT * FROM event_log ORDER BY occurred_at_utc ASC, event_seq ASC"
            ).fetchall()
        else:
            conn.execute("DELETE FROM session_state_projection WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM phrase_state_projection WHERE learner_id = ?", (learner_id,))
            conn.execute("DELETE FROM error_pattern_projection WHERE learner_id = ?", (learner_id,))
            sessions_rows = conn.execute(
                "SELECT session_id, learner_id, started_at_utc FROM sessions WHERE learner_id = ?",
                (learner_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM event_log WHERE learner_id = ? ORDER BY occurred_at_utc ASC, event_seq ASC",
                (learner_id,),
            ).fetchall()

        session_state: dict[str, dict[str, Any]] = {}
        session_meta: dict[str, dict[str, Any]] = {}
        for s_row in conn.execute("SELECT * FROM sessions").fetchall():
            sid = str(s_row["session_id"])
            session_meta[sid] = dict(s_row)

        for s in sessions_rows:
            sid = str(s["session_id"])
            session_state[sid] = {
                "session_id": sid,
                "learner_id": int(s["learner_id"]),
                "turns_since_visible_card": 4,
                "recent_questions": [],
                "revealed_targets": [],
                "cued_targets": [],
                "updated_at_utc": str(s["started_at_utc"]),
            }

        retracted_diagnostic_keys: set[str] = set()
        retracted_phrase_event_ids: set[str] = set()

        for ev in events:
            etype = str(ev["event_type"])
            payload = json.loads(ev["payload_json"])
            if etype == "diagnostic_retracted":
                target_ev_id = payload.get("target_event_id", "")
                diag_id = payload.get("diagnostic_id", "")
                retracted_diagnostic_keys.add(f"{target_ev_id}:{diag_id}")
            elif etype == "phrase_evidence_retracted":
                target_ev_id = payload.get("target_event_id", "")
                retracted_phrase_event_ids.add(str(target_ev_id))

        error_proj: dict[tuple[int, str, str], dict[str, Any]] = {}
        seen_occurrence_ids: set[str] = set()
        phrase_events_map: dict[tuple[int, str], list[dict[str, Any]]] = {}

        for ev in events:
            ev_id = str(ev["event_id"])
            lid = int(ev["learner_id"])
            sid = str(ev["session_id"])
            etype = str(ev["event_type"])
            occ_ts = str(ev["occurred_at_utc"])
            corr_id = str(ev["correlation_id"])
            payload = json.loads(ev["payload_json"])

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
                diagnostics_list = payload.get("diagnostics", [])
                seen_in_this_turn: set[tuple[str, str]] = set()
                for d_raw in diagnostics_list:
                    diag_id = str(d_raw.get("diagnostic_id", ""))
                    if f"{ev_id}:{diag_id}" in retracted_diagnostic_keys:
                        continue
                    kind_str = str(d_raw.get("kind", ""))
                    if kind_str in (
                        IssueKind.OPTIONAL_UPGRADE.value,
                        IssueKind.ACCEPTABLE_VARIATION.value,
                        IssueKind.INSUFFICIENT_CONTEXT.value,
                    ):
                        continue
                    if not d_raw.get("preserves_intended_meaning") or not d_raw.get("context_fit_verified"):
                        continue
                    if d_raw.get("confidence_band") == ConfidenceBand.LOW.value or float(d_raw.get("confidence_score", 0.0)) < 0.65:
                        continue

                    pat_id = str(d_raw["normalized_pattern_id"])
                    ctx_fam = (
                        str(d_raw.get("relevant_context_family", "global"))
                        if kind_str in (IssueKind.PRAGMATICS.value, IssueKind.REGISTER.value)
                        else "global"
                    )
                    if (pat_id, ctx_fam) in seen_in_this_turn:
                        continue
                    seen_in_this_turn.add((pat_id, ctx_fam))

                    occurrence_key = f"{corr_id}:{pat_id}:{ctx_fam}"
                    if occurrence_key in seen_occurrence_ids:
                        continue
                    seen_occurrence_ids.add(occurrence_key)

                    pkey = (lid, pat_id, ctx_fam)
                    entry = error_proj.setdefault(
                        pkey,
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
                if payload.get("intervention") == InterventionLevel.BRIEF_CARD.value:
                    pat_id = payload.get("selected_pattern_id")
                    ctx_fam = payload.get("selected_context_family", "global")
                    if pat_id and (lid, pat_id, ctx_fam) in error_proj:
                        error_proj[(lid, pat_id, ctx_fam)]["times_feedback_selected"] += 1
                        error_proj[(lid, pat_id, ctx_fam)]["updated_at_utc"] = occ_ts

            elif etype == "responder_reply_delivered":
                if s_proj:
                    s_proj["turns_since_visible_card"] += 1
                    asked_opt_q = bool(payload.get("asked_optional_question", False))
                    if not payload.get("was_clarification_question", False):
                        s_proj["recent_questions"].append(asked_opt_q)
                        s_proj["recent_questions"] = s_proj["recent_questions"][-10:]

            elif etype == "feedback_card_delivered":
                if s_proj:
                    s_proj["turns_since_visible_card"] = 0
                pat_id = payload.get("normalized_pattern_id")
                ctx_fam = payload.get("relevant_context_family", "global")
                if pat_id and (lid, pat_id, ctx_fam) in error_proj:
                    error_proj[(lid, pat_id, ctx_fam)]["times_feedback_shown"] += 1
                    error_proj[(lid, pat_id, ctx_fam)]["updated_at_utc"] = occ_ts

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
                if ev_id in retracted_phrase_event_ids:
                    continue
                pid = str(payload["target_id"])
                s_info = session_meta.get(sid, {})
                reg_used = str(payload.get("register_profile") or s_info.get("register_profile") or "casual")
                t_mode = str(payload.get("training_mode") or s_info.get("training_mode") or "open_scene")
                phrase_events_map.setdefault((lid, pid), []).append(
                    {
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
                )

        for sid, s_proj in session_state.items():
            conn.execute(
                """
                INSERT INTO session_state_projection (
                    session_id, learner_id, turns_since_visible_card,
                    recent_questions_json, revealed_targets_json, cued_targets_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    s_proj["learner_id"],
                    s_proj["turns_since_visible_card"],
                    json.dumps(s_proj["recent_questions"]),
                    json.dumps(s_proj["revealed_targets"]),
                    json.dumps(s_proj["cued_targets"]),
                    s_proj["updated_at_utc"],
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

        catalog_rows = {
            str(r["phrase_id"]): str(r["applicability"])
            for r in conn.execute("SELECT phrase_id, applicability FROM phrase_catalog").fetchall()
        }

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
                    now_str,
                ),
            )
