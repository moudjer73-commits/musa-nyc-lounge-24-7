"""SQLite storage. Event-sourced: facts are appended, state is computed.

Tables
------
users, sessions, turns            conversation bookkeeping
expressions                       the learner's Phrase Vault (one row per user x expression)
learner_targets                   expressions currently being "stealth practised" (max 3 active)
events                            exposure | recognized | prompted | spontaneous | opportunity
finding_events                    every diagnosed issue, with the action the policy took
drills                            queued / finished mini-drills per pattern_key
error_patterns (VIEW)             counts per (user, pattern_key), derived from finding_events

Nothing in here stores a "score" or a "level". `mastery.compute_stage` derives the stage of an
expression from its events, and `mastery.next_due` derives its review date.

All timestamps are unix seconds (float) and are always passed in by the caller, so tests are
deterministic and the clock is never read inside this module.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from .checks import strip_tags
from .mastery import DAY, Event, Stage, compute_stage, is_due, next_due
from .registers import FORMALITIES, formality_of, required_formalities
from .models import Expression, Finding, PersonaCard, SceneBible, SessionFacts, Variant
from .state import LearnerState

MAX_ACTIVE_TARGETS = 3
PATTERN_DRILL_COOLDOWN_DAYS = 7

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    created_at      REAL    NOT NULL,
    cefr            TEXT    NOT NULL DEFAULT 'B2' CHECK (cefr IN ('B1','B2','C1','C2')),
    dialect         TEXT,
    include_arabic  INTEGER NOT NULL DEFAULT 1,
    strictness      TEXT    NOT NULL DEFAULT 'normal' CHECK (strictness IN ('light','normal','coach')),
    baseline_words  REAL    NOT NULL DEFAULT 12.0
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    mode         TEXT    NOT NULL,
    persona_json TEXT,
    scene_json   TEXT,
    register     TEXT,
    started_at   REAL    NOT NULL,
    ended_at     REAL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    user_id    INTEGER NOT NULL,
    ts         REAL    NOT NULL,
    speaker    TEXT    NOT NULL CHECK (speaker IN ('learner','persona')),
    text       TEXT    NOT NULL,
    words      INTEGER NOT NULL DEFAULT 0,
    modality   TEXT    NOT NULL DEFAULT 'text',
    meta_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, turn_id);

CREATE TABLE IF NOT EXISTS expressions (
    user_id          INTEGER NOT NULL REFERENCES users(user_id),
    expr_id          TEXT    NOT NULL,
    form             TEXT    NOT NULL,
    meaning          TEXT    NOT NULL DEFAULT '',
    register_tags    TEXT    NOT NULL DEFAULT '[]',
    variants         TEXT    NOT NULL DEFAULT '[]',
    l1_trap          TEXT,
    acceptable_forms TEXT    NOT NULL DEFAULT '[]',
    opportunity_hint TEXT,
    source           TEXT    NOT NULL DEFAULT 'card',
    created_at       REAL    NOT NULL,
    PRIMARY KEY (user_id, expr_id)
);

CREATE TABLE IF NOT EXISTS learner_targets (
    user_id      INTEGER NOT NULL,
    expr_id      TEXT    NOT NULL,
    activated_at REAL    NOT NULL,
    retired_at   REAL,
    FOREIGN KEY (user_id, expr_id) REFERENCES expressions(user_id, expr_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_target
    ON learner_targets(user_id, expr_id) WHERE retired_at IS NULL;

CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    expr_id     TEXT    NOT NULL,
    session_id  INTEGER,
    turn_id     INTEGER,
    ts          REAL    NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN ('exposure','recognized','prompted','spontaneous','opportunity')),
    correct     INTEGER,
    outcome     TEXT,
    naturalness INTEGER CHECK (naturalness IS NULL OR naturalness BETWEEN 1 AND 3),
    register    TEXT,
    hint_level  INTEGER,
    context_id  TEXT,
    mode        TEXT,
    FOREIGN KEY (user_id, expr_id) REFERENCES expressions(user_id, expr_id)
);
CREATE INDEX IF NOT EXISTS idx_events_user_expr ON events(user_id, expr_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_user_type ON events(user_id, type, ts);

CREATE TABLE IF NOT EXISTS finding_events (
    finding_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    session_id    INTEGER,
    turn_id       INTEGER,
    ts            REAL    NOT NULL,
    pattern_key   TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    severity      INTEGER NOT NULL,
    l1_transfer   INTEGER NOT NULL DEFAULT 0,
    l1_pattern    TEXT,
    action        TEXT    NOT NULL CHECK (action IN ('silent','recast','offer_card','queue_drill','clarify','defer')),
    finding_json  TEXT    NOT NULL,
    card_html     TEXT,
    card_shown_at REAL,
    recapped_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_findings_user_pattern ON finding_events(user_id, pattern_key, ts);
CREATE INDEX IF NOT EXISTS idx_findings_session ON finding_events(session_id, ts);

CREATE TABLE IF NOT EXISTS drills (
    drill_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    pattern_key TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    queued_at   REAL    NOT NULL,
    done_at     REAL,
    outcome     TEXT
);

CREATE VIEW IF NOT EXISTS error_patterns AS
SELECT f.user_id,
       f.pattern_key,
       (SELECT l1_pattern FROM finding_events x
         WHERE x.user_id = f.user_id AND x.pattern_key = f.pattern_key
         ORDER BY x.ts DESC LIMIT 1)               AS l1_pattern,
       MAX(f.l1_transfer)                           AS l1_transfer,
       COUNT(*)                                     AS count,
       MIN(f.ts)                                    AS first_seen,
       MAX(f.ts)                                    AS last_seen
FROM finding_events f
GROUP BY f.user_id, f.pattern_key;
"""


class TargetCapError(RuntimeError):
    """More than MAX_ACTIVE_TARGETS active targets were requested."""


def _row_to_event(r: sqlite3.Row) -> Event:
    return Event(
        ts=r["ts"],
        type=r["type"],
        correct=None if r["correct"] is None else bool(r["correct"]),
        session_id=r["session_id"],
        register=r["register"],
        naturalness=r["naturalness"],
        hint_level=r["hint_level"],
        outcome=r["outcome"],
    )


def _merge_unique(a: list[str], b: list[str]) -> list[str]:
    return list(dict.fromkeys([*a, *b]))


def _merge_variants(old: list[Variant], new: list[Variant]) -> list[Variant]:
    merged: dict[str, list[str]] = {}
    for v in [*old, *new]:
        merged[v.form] = _merge_unique(merged.get(v.form, []), v.register_tags)
    return [Variant(form=f, register_tags=t) for f, t in merged.items()]


def _row_to_expression(r: sqlite3.Row) -> Expression:
    return Expression(
        id=r["expr_id"],
        form=r["form"],
        meaning=r["meaning"],
        register_tags=json.loads(r["register_tags"]),
        variants=[Variant.model_validate(v) for v in json.loads(r["variants"])],
        l1_trap=r["l1_trap"],
        acceptable_forms=json.loads(r["acceptable_forms"]),
        opportunity_hint=r["opportunity_hint"],
    )


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ users

    def get_or_create_user(self, user_id: int, now: float, **defaults: Any) -> sqlite3.Row:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO users(user_id, created_at) VALUES (?, ?)", (user_id, now))
            if defaults:
                self._update_user(c, user_id, defaults)
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown user {user_id}")
        return row

    _USER_FIELDS = {"cefr", "dialect", "include_arabic", "strictness", "baseline_words"}

    def _update_user(self, c: sqlite3.Connection, user_id: int, fields: dict[str, Any]) -> None:
        bad = set(fields) - self._USER_FIELDS
        if bad:
            raise ValueError(f"cannot update user fields {bad}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE users SET {sets} WHERE user_id = ?", (*fields.values(), user_id))

    def update_user(self, user_id: int, **fields: Any) -> None:
        with self.tx() as c:
            self._update_user(c, user_id, fields)

    def update_baseline_words(self, user_id: int, words: int, alpha: float = 0.1) -> None:
        """Exponential moving average of the learner's message length (ignores one-word replies)."""
        if words < 3:
            return
        with self.tx() as c:
            c.execute(
                "UPDATE users SET baseline_words = (1 - ?) * baseline_words + ? * ? WHERE user_id = ?",
                (alpha, alpha, words, user_id),
            )

    # --------------------------------------------------------------- sessions

    def start_session(
        self, user_id: int, mode: str, now: float,
        persona: PersonaCard | None = None, scene: SceneBible | None = None,
    ) -> int:
        register = scene.register_plan.default if scene else None
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO sessions(user_id, mode, persona_json, scene_json, register, started_at) VALUES (?,?,?,?,?,?)",
                (
                    user_id, mode,
                    persona.model_dump_json() if persona else None,
                    scene.model_dump_json() if scene else None,
                    register, now,
                ),
            )
            return int(cur.lastrowid)

    def end_session(self, session_id: int, now: float) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET ended_at = ? WHERE session_id = ?", (now, session_id))

    def get_session(self, session_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown session {session_id}")
        return row

    def load_persona_scene(self, session_id: int) -> tuple[PersonaCard, SceneBible]:
        row = self.get_session(session_id)
        return (
            PersonaCard.model_validate_json(row["persona_json"]),
            SceneBible.model_validate_json(row["scene_json"]),
        )

    def set_register(self, session_id: int, register: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET register = ? WHERE session_id = ?", (register, session_id))

    # ------------------------------------------------------------------ turns

    def log_turn(
        self, session_id: int, user_id: int, speaker: str, text: str, now: float,
        *, words: int = 0, modality: str = "text", meta: dict[str, Any] | None = None,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO turns(session_id, user_id, ts, speaker, text, words, modality, meta_json) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, user_id, now, speaker, text, words, modality, json.dumps(meta) if meta else None),
            )
            return int(cur.lastrowid)

    def get_turn(self, turn_id: int) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if r is None:
            raise KeyError(f"unknown turn {turn_id}")
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json")) if d.get("meta_json") else {}
        return d

    def recent_turns(self, session_id: int, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_id DESC LIMIT ?", (session_id, limit)
        ).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["meta"] = json.loads(d.pop("meta_json")) if d.get("meta_json") else {}
            out.append(d)
        return out

    def persona_turns(self, session_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return [t for t in self.recent_turns(session_id, limit * 3) if t["speaker"] == "persona"][-limit:]

    def learner_turn_count(self, session_id: int) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND speaker = 'learner'", (session_id,)
        ).fetchone()[0])

    def session_facts(self, session_id: int) -> SessionFacts:
        """What can honestly be said about how a scene went, straight from the turn log."""
        rows = self.conn.execute(
            "SELECT words, modality, text FROM turns WHERE session_id = ? AND speaker = 'learner' ORDER BY turn_id",
            (session_id,),
        ).fetchall()
        words = [int(r["words"]) for r in rows]
        n = len(words)
        facts: dict[str, Any] = {
            "learner_turns": n,
            "questions_asked": sum("?" in r["text"] for r in rows),
            "voice_turns": sum(r["modality"] == "voice" for r in rows),
            "text_turns": sum(r["modality"] != "voice" for r in rows),
            "beats_reached": len(self.used_beat_ids(session_id)),
            "cards_shown": int(self.conn.execute(
                "SELECT COUNT(*) FROM finding_events WHERE session_id = ? AND action = 'offer_card'", (session_id,)
            ).fetchone()[0]),
        }
        scene_json = self.get_session(session_id)["scene_json"]      # a mode session has no scene, hence no beats
        facts["beats_total"] = len(SceneBible.model_validate_json(scene_json).beats) if scene_json else 0
        if n:
            facts["avg_words"] = round(sum(words) / n)
            facts["longest_turn_words"] = max(words)
        if n >= 4:
            half = n // 2
            first, second = sum(words[:half]) / half, sum(words[-half:]) / half
            facts["first_half_avg_words"], facts["second_half_avg_words"] = round(first), round(second)
            ratio = second / first if first else 1.0
            facts["length_trend"] = "longer" if ratio >= 1.3 else "shorter" if ratio <= 0.7 else "steady"
        return SessionFacts(**facts)

    def used_beat_ids(self, session_id: int) -> set[str]:
        used: set[str] = set()
        for t in self.recent_turns(session_id, 200):
            beat = t["meta"].get("beat_id")
            if t["speaker"] == "persona" and beat:
                used.add(beat)
        return used

    # ------------------------------------------------------------ expressions

    def upsert_expression(self, user_id: int, expr: Expression, now: float, source: str = "card") -> None:
        """Insert, or merge evidence into an existing item.

        Each time the same form is shown as natural in a new register, that register is added to its
        range; each variant keeps its own range. Ranges only grow from evidence, never from a blind union.
        """
        existing = self.get_expression(user_id, expr.id)
        tags = _merge_unique(existing.register_tags if existing else [], expr.register_tags)
        variants = _merge_variants(existing.variants if existing else [], expr.variants)
        with self.tx() as c:
            c.execute(
                """INSERT INTO expressions(user_id, expr_id, form, meaning, register_tags, variants, l1_trap,
                                           acceptable_forms, opportunity_hint, source, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, expr_id) DO UPDATE SET
                       meaning = excluded.meaning,
                       register_tags = excluded.register_tags,
                       variants = excluded.variants,
                       acceptable_forms = excluded.acceptable_forms,
                       opportunity_hint = COALESCE(excluded.opportunity_hint, expressions.opportunity_hint),
                       l1_trap = COALESCE(excluded.l1_trap, expressions.l1_trap)""",
                (
                    user_id, expr.id, expr.form, expr.meaning, json.dumps(tags),
                    json.dumps([v.model_dump() for v in variants], ensure_ascii=False),
                    expr.l1_trap, json.dumps(expr.acceptable_forms), expr.opportunity_hint, source, now,
                ),
            )

    def get_expression(self, user_id: int, expr_id: str) -> Expression | None:
        r = self.conn.execute(
            "SELECT * FROM expressions WHERE user_id = ? AND expr_id = ?", (user_id, expr_id)
        ).fetchone()
        return _row_to_expression(r) if r else None

    def list_expressions(self, user_id: int) -> list[Expression]:
        rows = self.conn.execute("SELECT * FROM expressions WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
        return [_row_to_expression(r) for r in rows]

    # ----------------------------------------------------------------- events

    def record_event(
        self, user_id: int, expr_id: str, type_: str, now: float, *,
        correct: bool | None = None, outcome: str | None = None, naturalness: int | None = None,
        register: str | None = None, hint_level: int | None = None, context_id: str | None = None,
        session_id: int | None = None, turn_id: int | None = None, mode: str | None = None,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO events(user_id, expr_id, session_id, turn_id, ts, type, correct, outcome,
                                      naturalness, register, hint_level, context_id, mode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, expr_id, session_id, turn_id, now, type_,
                    None if correct is None else int(correct), outcome, naturalness,
                    register, hint_level, context_id, mode,
                ),
            )
            return int(cur.lastrowid)

    def events_for(self, user_id: int, expr_id: str) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE user_id = ? AND expr_id = ? ORDER BY ts, event_id", (user_id, expr_id)
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def stage_of(self, user_id: int, expr_id: str, now: float) -> Stage:
        expr = self.get_expression(user_id, expr_id)
        need = required_formalities(expr.register_tags if expr else [])
        return compute_stage(self.events_for(user_id, expr_id), now, min_formalities=need)

    def due_at(self, user_id: int, expr_id: str) -> float | None:
        return next_due(self.events_for(user_id, expr_id))

    def due_expressions(self, user_id: int, now: float, limit: int = 10) -> list[Expression]:
        """Vault items whose review date has passed, most overdue first."""
        due: list[tuple[float, Expression]] = []
        for expr in self.list_expressions(user_id):
            evs = self.events_for(user_id, expr.id)
            d = next_due(evs)
            if d is not None and d <= now:
                due.append((d, expr))
        due.sort(key=lambda t: t[0])
        return [e for _, e in due[:limit]]

    # ---------------------------------------------------------------- targets

    def active_targets(self, user_id: int) -> list[Expression]:
        rows = self.conn.execute(
            """SELECT e.* FROM learner_targets t
               JOIN expressions e ON e.user_id = t.user_id AND e.expr_id = t.expr_id
               WHERE t.user_id = ? AND t.retired_at IS NULL ORDER BY t.activated_at""",
            (user_id,),
        ).fetchall()
        return [_row_to_expression(r) for r in rows]

    def activate_target(self, user_id: int, expr_id: str, now: float) -> None:
        """Start stealth-practising an expression. At most three at a time (cognitive load)."""
        with self.tx() as c:
            already = c.execute(
                "SELECT 1 FROM learner_targets WHERE user_id = ? AND expr_id = ? AND retired_at IS NULL",
                (user_id, expr_id),
            ).fetchone()
            if already:
                return
            n = c.execute(
                "SELECT COUNT(*) FROM learner_targets WHERE user_id = ? AND retired_at IS NULL", (user_id,)
            ).fetchone()[0]
            if n >= MAX_ACTIVE_TARGETS:
                raise TargetCapError(f"already {MAX_ACTIVE_TARGETS} active targets")
            c.execute(
                "INSERT INTO learner_targets(user_id, expr_id, activated_at) VALUES (?,?,?)", (user_id, expr_id, now)
            )

    def retire_stable_targets(self, user_id: int, now: float) -> list[str]:
        """Free the slots of expressions that reached STABLE."""
        retired: list[str] = []
        for expr in self.active_targets(user_id):
            if self.stage_of(user_id, expr.id, now) >= Stage.STABLE:
                with self.tx() as c:
                    c.execute(
                        "UPDATE learner_targets SET retired_at = ? WHERE user_id = ? AND expr_id = ? AND retired_at IS NULL",
                        (now, user_id, expr.id),
                    )
                retired.append(expr.id)
        return retired

    # --------------------------------------------------------------- findings

    def log_finding(
        self, user_id: int, finding: Finding, action: str, now: float,
        *, session_id: int | None = None, turn_id: int | None = None,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO finding_events(user_id, session_id, turn_id, ts, pattern_key, category, severity,
                                              l1_transfer, l1_pattern, action, finding_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, session_id, turn_id, now, finding.pattern_key, finding.category, finding.severity,
                    int(finding.l1_transfer), finding.l1_pattern, action, finding.model_dump_json(),
                ),
            )
            return int(cur.lastrowid)

    def set_finding_action(self, finding_event_id: int, action: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE finding_events SET action = ? WHERE finding_event_id = ?", (action, finding_event_id))

    def get_finding(self, finding_event_id: int) -> tuple[Finding, sqlite3.Row]:
        row = self.conn.execute(
            "SELECT * FROM finding_events WHERE finding_event_id = ?", (finding_event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown finding {finding_event_id}")
        return Finding.model_validate_json(row["finding_json"]), row

    def save_card(self, finding_event_id: int, html: str, now: float) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE finding_events SET card_html = ?, card_shown_at = ? WHERE finding_event_id = ?",
                (html, now, finding_event_id),
            )

    def recent_card_text(self, session_id: int, n: int = 2, within_turns: int = 3) -> list[str]:
        """Text of cards the learner saw during the last `within_turns` learner turns.

        Used for echo detection: wording from a card the learner read a moment ago is not
        spontaneous production. Older cards no longer count as echo."""
        ts_rows = self.conn.execute(
            "SELECT ts FROM turns WHERE session_id = ? AND speaker = 'learner' ORDER BY turn_id DESC LIMIT ?",
            (session_id, within_turns),
        ).fetchall()
        cutoff = min((r["ts"] for r in ts_rows), default=0.0)
        rows = self.conn.execute(
            """SELECT card_html FROM finding_events
               WHERE session_id = ? AND card_html IS NOT NULL AND card_shown_at >= ?
               ORDER BY card_shown_at DESC LIMIT ?""",
            (session_id, cutoff, n),
        ).fetchall()
        return [strip_tags(r["card_html"]) for r in rows]

    def pattern_counts(self, user_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT pattern_key, count FROM error_patterns WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {r["pattern_key"]: int(r["count"]) for r in rows}

    def known_patterns(self, user_id: int, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT pattern_key, count FROM error_patterns WHERE user_id = ? ORDER BY count DESC, last_seen DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [{"key": r["pattern_key"], "count": int(r["count"])} for r in rows]

    def recap_items(self, session_id: int, limit: int = 3) -> list[tuple[int, Finding]]:
        """Up to `limit` distinct issues from this scene worth a line in the end-of-scene note."""
        rows = self.conn.execute(
            """SELECT * FROM finding_events
               WHERE session_id = ? AND recapped_at IS NULL AND severity >= 2
                 AND action IN ('defer','offer_card','recast','queue_drill')
               ORDER BY severity DESC, ts DESC""",
            (session_id,),
        ).fetchall()
        seen: set[str] = set()
        out: list[tuple[int, Finding]] = []
        for r in rows:
            if r["pattern_key"] in seen:
                continue
            seen.add(r["pattern_key"])
            out.append((int(r["finding_event_id"]), Finding.model_validate_json(r["finding_json"])))
            if len(out) == limit:
                break
        return out

    def mark_recapped(self, finding_event_ids: Iterable[int], now: float) -> None:
        """Recapping a pattern retires every row of that pattern in the same scene, not just the shown one."""
        with self.tx() as c:
            c.executemany(
                """UPDATE finding_events SET recapped_at = ?
                   WHERE recapped_at IS NULL AND (session_id, pattern_key) IN
                         (SELECT session_id, pattern_key FROM finding_events WHERE finding_event_id = ?)""",
                [(now, i) for i in finding_event_ids],
            )

    # round-trip of spontaneous wins for the debrief
    def session_wins(self, session_id: int) -> list[str]:
        rows = self.conn.execute(
            """SELECT DISTINCT x.form FROM events e
               JOIN expressions x ON x.user_id = e.user_id AND x.expr_id = e.expr_id
               WHERE e.session_id = ? AND e.type = 'spontaneous' AND e.correct = 1""",
            (session_id,),
        ).fetchall()
        return [r["form"] for r in rows]

    # ------------------------------------------------------------------ drills

    def queue_drill(self, user_id: int, pattern_key: str, mode: str, now: float) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO drills(user_id, pattern_key, mode, queued_at) VALUES (?,?,?,?)",
                (user_id, pattern_key, mode, now),
            )
            return int(cur.lastrowid)

    def finish_drill(self, drill_id: int, now: float, outcome: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE drills SET done_at = ?, outcome = ? WHERE drill_id = ?", (now, outcome, drill_id))

    def queued_drills(self, user_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT pattern_key FROM drills WHERE user_id = ? AND done_at IS NULL ORDER BY queued_at", (user_id,)
        ).fetchall()
        return [r["pattern_key"] for r in rows]

    def drilled_recently(self, user_id: int, now: float, days: float = PATTERN_DRILL_COOLDOWN_DAYS) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT pattern_key FROM drills WHERE user_id = ? AND done_at IS NOT NULL AND done_at >= ?",
            (user_id, now - days * DAY),
        ).fetchall()
        return [r["pattern_key"] for r in rows]

    # ----------------------------------------------------------- learner state

    def build_learner_state(
        self, user_id: int, session_id: int, now: float, *, hesitation_ratio: float = 0.0, mode: str = "open_scene",
    ) -> LearnerState:
        """Snapshot for the policy. Call it AFTER logging the current learner turn and BEFORE logging
        this turn's findings, so pattern counts mean "times seen before now"."""
        user = self.get_user(user_id)
        turn_index = self.learner_turn_count(session_id)
        last_card_turn = self.conn.execute(
            "SELECT MAX(turn_id) FROM finding_events WHERE session_id = ? AND action = 'offer_card'", (session_id,)
        ).fetchone()[0]
        if last_card_turn is None:
            turns_since_card = 99
        else:
            turns_since_card = int(self.conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id = ? AND speaker = 'learner' AND turn_id > ?",
                (session_id, last_card_turn),
            ).fetchone()[0])
        cards = int(self.conn.execute(
            "SELECT COUNT(*) FROM finding_events WHERE session_id = ? AND action = 'offer_card'", (session_id,)
        ).fetchone()[0])
        words = [
            int(r["words"]) for r in self.conn.execute(
                "SELECT words FROM turns WHERE session_id = ? AND speaker = 'learner' ORDER BY turn_id DESC LIMIT 5",
                (session_id,),
            ).fetchall()
        ][::-1]
        return LearnerState(
            user_id=user_id,
            cefr=user["cefr"],
            dialect=user["dialect"],
            include_arabic=bool(user["include_arabic"]),
            strictness=user["strictness"],
            mode=mode,
            turn_index=turn_index,
            turns_since_card=turns_since_card,
            cards_this_session=cards,
            baseline_words=float(user["baseline_words"]),
            recent_words=words,
            hesitation_ratio=hesitation_ratio,
            pattern_counts=self.pattern_counts(user_id),
            drilled_recently=self.drilled_recently(user_id, now),
            queued_drills=self.queued_drills(user_id),
        )

    # ----------------------------------------------------------------- metrics

    def metrics(self, user_id: int, now: float, window_days: float = 30) -> dict[str, Any]:
        """Honest progress numbers, all derived from events.

        spontaneous_rate   spontaneous correct uses / opportunities the character created (approximate:
                           opportunities are what the Responder says it created).
        stage_counts       how many vault expressions sit at each rung of the ladder right now.
        formality_diversity distinct formality buckets (informal/neutral/formal) among good spontaneous uses,
                           out of three. Register labels are not counted: casual and warm are one bucket.
        repeat_after_drill findings of drilled patterns in the 14 days after a drill vs the 14 days before
                           (< 1.0 means the pattern is recurring less often).
        """
        since = now - window_days * DAY
        spont = int(self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND type = 'spontaneous' AND correct = 1 AND ts >= ?",
            (user_id, since),
        ).fetchone()[0])
        opps = int(self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND type = 'opportunity' AND ts >= ?", (user_id, since)
        ).fetchone()[0])
        formalities = {
            formality_of(r["register"]) for r in self.conn.execute(
                "SELECT DISTINCT register FROM events WHERE user_id = ? AND type = 'spontaneous' AND correct = 1 "
                "AND register IS NOT NULL AND ts >= ?",
                (user_id, since),
            ).fetchall()
        }
        stages = Counter(self.stage_of(user_id, e.id, now).name for e in self.list_expressions(user_id))

        before = after = 0
        for d in self.conn.execute(
            "SELECT * FROM drills WHERE user_id = ? AND done_at IS NOT NULL AND done_at + ? <= ?",
            (user_id, 14 * DAY, now),
        ).fetchall():
            q = "SELECT COUNT(*) FROM finding_events WHERE user_id = ? AND pattern_key = ? AND ts >= ? AND ts < ?"
            before += int(self.conn.execute(q, (user_id, d["pattern_key"], d["done_at"] - 14 * DAY, d["done_at"])).fetchone()[0])
            after += int(self.conn.execute(q, (user_id, d["pattern_key"], d["done_at"], d["done_at"] + 14 * DAY)).fetchone()[0])

        return {
            "spontaneous_uses": spont,
            "opportunities": opps,
            "spontaneous_rate": (spont / opps) if opps else None,
            "stage_counts": dict(stages),
            "formality_diversity": len(formalities - {None}) / len(FORMALITIES),
            "repeat_after_drill": (after / before) if before else None,
        }
