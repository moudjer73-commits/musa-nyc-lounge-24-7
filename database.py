# -*- coding: utf-8 -*-
"""
database.py — Dual-Database Engine with WAL Concurrency, Morphological Cloze Builder,
Productive Uptake Detector (Swain's Output Hypothesis), and Persona Persistence.
"""
import os
import re
import json
import uuid
import time
import sqlite3
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple

from config import (
    USER_DB_PATH,
    CORPUS_DB_PATH,
    ADMIN_ID,
    REGISTERS_MAP,
    PERSONAS_MAP
)

logger = logging.getLogger("MusaNYCLounge.DB")

INITIAL_CORPUS_SEED: List[Tuple[str, str, str, str, str, str]] = [
    ("play it by ear", "☕ Casual", "Decide how to handle something as it unfolds instead of locking down a plan", "When plans are uncertain or flexible", "let's just / we'll + play it by ear", "No need to lock anything down right now—let's just play it by ear."),
    ("wind up", "🎙️ Articulate", "End up in an unexpected situation or doing something unplanned", "Telling a story about an unintended outcome", "wind up + V-ing / adj", "We went out for a quick coffee and wound up talking for three hours."),
    ("take it with a grain of salt", "🎙️ Articulate", "Treat a claim or story with healthy skepticism", "When someone exaggerates or passes along rumors", "take [something] with a grain of salt", "I'd take his version of the story with a grain of salt; he loves drama."),
    ("call it a day", "☕ Casual", "Stop working or wrap up an activity for the rest of the day", "When you've done enough and want to rest", "let's / I'm gonna + call it a day", "We've covered enough ground—let's call it a day and grab some food."),
    ("off the top of my head", "🎙️ Articulate", "From memory, without checking exact figures or details", "Giving a quick estimate in conversation", "off the top of my head, I'd say...", "Off the top of my head, I think it costs around fifty bucks, give or take."),
    ("cut someone some slack", "☕ Casual", "Give someone a break instead of judging them harshly", "Defending someone who made an honest mistake", "cut [person] some slack", "Hey, cut him some slack—it's literally his first week on the job."),
    ("on the fence", "🎙️ Articulate", "Undecided between two choices", "Discussing a tough decision", "be / sit + on the fence about [something]", "I'm still on the fence about taking that offer, to be honest."),
    ("a tough pill to swallow", "🎙️ Articulate", "A harsh reality or disappointment that you have to accept", "Acknowledging difficult news or blunt feedback", "that's a tough pill to swallow", "Missing the finals by one point was a tough pill to swallow."),
    ("push the envelope", "🎙️ Articulate", "Go beyond usual boundaries or innovate boldly", "Praising bold creative or professional work", "really pushing the envelope on [topic]", "That director always pushes the envelope without making it feel forced."),
    ("bite off more than you can chew", "☕ Casual", "Take on a commitment that's too big to handle", "Feeling overwhelmed by too many tasks", "bit off a bit more than I could chew", "I think I bit off more than I could chew by agreeing to host twenty people."),
    ("read between the lines", "🎙️ Articulate", "Catch the unspoken meaning or real intention behind someone's words", "Decoding polite excuses or subtle hints", "if you read between the lines", "She said everything was fine, but if you read between the lines, she's frustrated."),
    ("that's one way to put it", "🎭 Witty", "Dry, understated reaction when someone describes a disaster too mildly", "Reacting with irony or dry humor", "Well, that's certainly one way to put it", "You call getting stranded at JFK for twelve hours 'a minor delay'? Well, that's one way to put it."),
    ("jump the gun", "☕ Casual", "Act prematurely before having all the facts or before the right time", "Stopping someone from rushing a decision", "let's not jump the gun", "Hold on, let's not jump the gun before we actually hear his side of the story."),
    ("hit the nail on the head", "🎙️ Articulate", "Pinpoint the exact truth or root cause of an issue", "Validating a sharp observation", "you hit the nail right on the head", "You hit the nail on the head—that's the exact issue nobody wants to admit."),
    ("let it slide", "☕ Casual", "Ignore a minor mistake or annoyance instead of making a big deal out of it", "Choosing peace over confrontation", "just let it slide this time", "It annoyed me a little, sure, but I figured I'd just let it slide."),
    ("by the skin of my teeth", "🎭 Witty", "Barely manage to do something or escape a close call", "Narrating a narrow escape", "made it by the skin of my teeth", "The subway doors were literally closing—I caught the train by the skin of my teeth."),
    ("have a lot on my plate", "☕ Casual", "Be busy juggling many responsibilities at once", "Politely declining extra work", "I've got a lot on my plate right now", "I'd love to help out, man, but I've got way too much on my plate this week."),
    ("boil down to", "🎙️ Articulate", "Come down to the single most important factor or core issue", "Summarizing a complex argument", "what it really boils down to is [noun]", "At the end of the day, the whole disagreement boils down to trust."),
    ("pan out", "🎙️ Articulate", "Turn out well or develop in a particular way", "Talking about how a plan or project unfolded", "see how things pan out / didn't pan out", "We tried launching it in the spring, but the timing just didn't pan out."),
    ("iron out", "🎙️ Articulate", "Resolve remaining small problems or details", "Finalizing a plan or agreement", "iron out a few kinks / details", "We're almost ready—we just need to iron out a few last-minute details."),
    ("brush up on", "☕ Casual", "Review and refresh a skill you haven't used lately", "Preparing for a trip or presentation", "brush up on my [skill]", "I really need to brush up on my spoken Spanish before the trip."),
    ("run it by", "🎙️ Articulate", "Tell someone about an idea to get their quick feedback or approval", "Checking with a colleague or friend", "let me run this by [person]", "That sounds promising—let me run it by Chloe and see what she thinks."),
    ("fall through", "☕ Casual", "Fail to happen after being planned (deals, trips, plans)", "Explaining why a plan was canceled", "our plans fell through at the last minute", "We were supposed to drive upstate, but the whole trip fell through."),
    ("give someone the benefit of the doubt", "🎙️ Articulate", "Choose to believe someone had good intentions despite doubts", "Being fair when evidence is incomplete", "give [person] the benefit of the doubt", "Maybe his phone really did die—let's give him the benefit of the doubt.")
]

IRREGULAR_STEMS: Dict[str, str] = {
    "wind": r"(?:wind|winds|wound|winding)",
    "bite": r"(?:bite|bites|bit|bitten|biting)",
    "take": r"(?:take|takes|took|taken|taking)",
    "cut": r"(?:cut|cuts|cutting)",
    "hit": r"(?:hit|hits|hitting)",
    "let": r"(?:let|lets|letting)",
    "fall": r"(?:fall|falls|fell|fallen|falling)",
    "give": r"(?:give|gives|gave|given|giving)",
    "run": r"(?:run|runs|ran|running)",
    "have": r"(?:have|has|had|having|'ve|'s|'d)",
    "be": r"(?:be|am|is|are|was|were|been|being|'m|'re|'s)",
    "keep": r"(?:keep|keeps|kept|keeping)",
    "pull": r"(?:pull|pulls|pulled|pulling)",
    "push": r"(?:push|pushes|pushed|pushing)",
    "boil": r"(?:boil|boils|boiled|boiling)",
    "pan": r"(?:pan|pans|panned|panning)",
    "iron": r"(?:iron|irons|ironed|ironing)",
    "brush": r"(?:brush|brushes|brushed|brushing)",
    "jump": r"(?:jump|jumps|jumped|jumping)",
    "play": r"(?:play|plays|played|playing)",
    "call": r"(?:call|calls|called|calling)",
    "read": r"(?:read|reads|reading)"
}


def build_smart_cloze(chunk: str, example: str, ai_cloze: str = "") -> str:
    """Ensures the target chunk is ALWAYS masked in the cloze sentence even with inflections or pronouns."""
    if ai_cloze and "____" in ai_cloze and chunk.lower() not in ai_cloze.lower():
        return ai_cloze.strip()
    if not example:
        return "______"

    tokens = chunk.strip().split()
    regex_parts = []
    for tok in tokens:
        low = tok.lower().strip("[]()")
        if low in ("someone", "somebody", "person"):
            regex_parts.append(r"(?:\w+(?:\s+\w+)?)")
        elif low in ("something", "it"):
            regex_parts.append(r"(?:\w+(?:\s+\w+){0,2})")
        elif low in ("my", "your", "one's", "his", "her", "their", "our"):
            regex_parts.append(r"(?:my|your|his|her|their|our|one's|the|a)")
        elif low in IRREGULAR_STEMS:
            regex_parts.append(IRREGULAR_STEMS[low])
        elif len(low) >= 4:
            stem = re.escape(low[:-1] if low.endswith("e") else low)
            regex_parts.append(rf"{stem}\w*")
        else:
            regex_parts.append(re.escape(tok))

    flex_pattern = r"\b" + r"(?:\s+(?:a\s+bit\s+|right\s+|really\s+|just\s+)?)".join(regex_parts) + r"\b"
    masked, count = re.subn(flex_pattern, "______", example, flags=re.IGNORECASE)
    if count > 0:
        return masked

    # Fallback: mask any content word >= 4 chars from chunk that appears in example
    content_words = [w for w in tokens if len(w) >= 4 and w.lower() not in ("with", "from", "that", "this", "some", "more", "than")]
    masked_fb = example
    replaced_any = False
    for cw in content_words:
        stem = re.escape(cw[:4].lower())
        masked_fb, c = re.subn(rf"\b{stem}\w*\b", "______", masked_fb, flags=re.IGNORECASE)
        if c > 0:
            replaced_any = True
    return masked_fb if replaced_any else "Complete this thought using your target phrase: ______"


class LoungeDatabase:
    def __init__(self, user_db_path: str = USER_DB_PATH, corpus_db_path: str = CORPUS_DB_PATH):
        self.user_db_path = user_db_path
        self.corpus_db_path = corpus_db_path
        self._lock = threading.RLock()
        self.fts5_supported = False
        self.init_schema()
        self.init_corpus_fts5()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.user_db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _corpus_conn(self):
        conn = sqlite3.connect(self.corpus_db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    full_name TEXT DEFAULT '',
                    level TEXT DEFAULT 'C1',
                    english_only INTEGER DEFAULT 1,
                    preferred_register TEXT DEFAULT 'articulate',
                    active_persona TEXT DEFAULT 'trio',
                    current_mode TEXT DEFAULT 'spontaneous_chat',
                    session_meta TEXT DEFAULT '{}',
                    is_whitelisted INTEGER DEFAULT 0,
                    onboarded INTEGER DEFAULT 1,
                    daily_count INTEGER DEFAULT 0,
                    daily_limit INTEGER DEFAULT 120,
                    last_date TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lexical_vault (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    chunk TEXT,
                    register_tag TEXT DEFAULT '🎙️ Articulate',
                    explanation TEXT DEFAULT '',
                    flat_b2_foil TEXT DEFAULT '',
                    trigger_context TEXT DEFAULT '',
                    co_text_pattern TEXT DEFAULT '',
                    typical_followup TEXT DEFAULT '',
                    example_sentence TEXT DEFAULT '',
                    cloze_sentence TEXT DEFAULT '',
                    stealth_target INTEGER DEFAULT 4,
                    stealth_done INTEGER DEFAULT 0,
                    productive_count INTEGER DEFAULT 0,
                    srs_repetitions INTEGER DEFAULT 0,
                    srs_efactor REAL DEFAULT 2.5,
                    srs_interval INTEGER DEFAULT 1,
                    srs_next_review TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, chunk)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS error_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    category TEXT DEFAULT '🎭 Natural Flow',
                    user_said TEXT DEFAULT '',
                    native_fix TEXT DEFAULT '',
                    why_note TEXT DEFAULT '',
                    frequency INTEGER DEFAULT 1,
                    mastered INTEGER DEFAULT 0,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, native_fix)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS atomic_buffer (
                    token_key TEXT PRIMARY KEY,
                    user_id INTEGER,
                    payload_json TEXT,
                    audio_clean_text TEXT DEFAULT '',
                    mirror_target_text TEXT DEFAULT '',
                    host_reply_text TEXT DEFAULT '',
                    host_persona TEXT DEFAULT 'marcus',
                    user_voice_file_id TEXT DEFAULT '',
                    created_at INTEGER
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS invite_codes (
                    code TEXT PRIMARY KEY,
                    daily_limit INTEGER DEFAULT 80,
                    used_by INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    val TEXT DEFAULT ''
                )
            """)

            # Safe schema migrations for existing user DBs
            u_cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "active_persona" not in u_cols:
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN active_persona TEXT DEFAULT 'trio'")
                except Exception:
                    pass

            v_cols = [c[1] for c in conn.execute("PRAGMA table_info(lexical_vault)").fetchall()]
            if "productive_count" not in v_cols:
                try:
                    conn.execute("ALTER TABLE lexical_vault ADD COLUMN productive_count INTEGER DEFAULT 0")
                except Exception:
                    pass
            if "flat_b2_foil" not in v_cols:
                try:
                    conn.execute("ALTER TABLE lexical_vault ADD COLUMN flat_b2_foil TEXT DEFAULT ''")
                except Exception:
                    pass

            ab_cols = [c[1] for c in conn.execute("PRAGMA table_info(atomic_buffer)").fetchall()]
            for col_name, col_def in [
                ("host_reply_text", "TEXT DEFAULT ''"),
                ("mirror_target_text", "TEXT DEFAULT ''"),
                ("host_persona", "TEXT DEFAULT 'marcus'")
            ]:
                if col_name not in ab_cols:
                    try:
                        conn.execute(f"ALTER TABLE atomic_buffer ADD COLUMN {col_name} {col_def}")
                    except Exception:
                        pass

            # Repair any leaked cloze sentences in existing vault
            rows = conn.execute("SELECT id, chunk, example_sentence, cloze_sentence FROM lexical_vault").fetchall()
            for r in rows:
                c_sent = r["cloze_sentence"] or ""
                if "(Phrase: ______)" in c_sent or r["chunk"].lower() in c_sent.lower():
                    fixed_c = build_smart_cloze(r["chunk"], r["example_sentence"])
                    conn.execute("UPDATE lexical_vault SET cloze_sentence = ? WHERE id = ?", (fixed_c, r["id"]))

            if ADMIN_ID > 0:
                conn.execute("""
                    INSERT OR IGNORE INTO users (user_id, full_name, level, english_only, preferred_register, active_persona, is_whitelisted, onboarded, daily_limit)
                    VALUES (?, 'Musa', 'C2', 1, 'articulate', 'trio', 1, 1, 500)
                """, (ADMIN_ID,))
                conn.execute("UPDATE users SET is_whitelisted = 1, daily_limit = 500 WHERE user_id = ?", (ADMIN_ID,))
            conn.commit()

    def get_sys_state(self, key: str, default: str = "") -> str:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT val FROM system_state WHERE key = ?", (key,)).fetchone()
            return row["val"] if row else default

    def set_sys_state(self, key: str, val: str):
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO system_state (key, val) VALUES (?, ?)", (key, str(val)))
            conn.commit()

    def init_corpus_fts5(self):
        with self._lock, self._corpus_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS corpus_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk TEXT UNIQUE,
                    register_tag TEXT,
                    explanation TEXT,
                    trigger_context TEXT,
                    co_text_pattern TEXT,
                    example_sentence TEXT
                )
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS corpus_fts USING fts5(
                        chunk,
                        register_tag,
                        explanation,
                        trigger_context,
                        co_text_pattern,
                        example_sentence,
                        content='corpus_entries',
                        content_rowid='id'
                    )
                """)
                self.fts5_supported = True
            except Exception:
                self.fts5_supported = False

            for row in INITIAL_CORPUS_SEED:
                conn.execute("""
                    INSERT OR IGNORE INTO corpus_entries
                    (chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, row)
            if self.fts5_supported:
                try:
                    conn.execute("INSERT INTO corpus_fts(corpus_fts) VALUES('rebuild')")
                except Exception:
                    pass
            conn.commit()

    def ingest_chunks_to_corpus(self, items: List[Dict[str, Any]]):
        if not items:
            return
        with self._lock, self._corpus_conn() as conn:
            added = False
            for it in items:
                chunk = (it.get("chunk") or "").strip()
                if not chunk or len(chunk) < 3:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO corpus_entries
                    (chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    chunk,
                    it.get("register_tag", "🎙️ Articulate"),
                    it.get("explanation", ""),
                    it.get("trigger", it.get("trigger_context", "")),
                    it.get("pattern", it.get("co_text_pattern", "")),
                    it.get("example", it.get("example_sentence", ""))
                ))
                if cur.rowcount > 0:
                    added = True
            if added and self.fts5_supported:
                try:
                    conn.execute("INSERT INTO corpus_fts(corpus_fts) VALUES('rebuild')")
                except Exception:
                    pass
            conn.commit()

    def lookup_collocations(self, query: str, user_id: int = 0, limit: int = 8) -> List[Dict[str, Any]]:
        clean_q = re.sub(r"[^a-zA-Z0-9\s'-]", "", query).strip()
        if not clean_q:
            return []

        results: List[Dict[str, Any]] = []
        seen_chunks = set()

        if user_id:
            with self._lock, self._conn() as uconn:
                u_rows = uconn.execute("""
                    SELECT chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence
                    FROM lexical_vault
                    WHERE user_id = ? AND (chunk LIKE ? OR explanation LIKE ? OR example_sentence LIKE ?)
                    LIMIT ?
                """, (user_id, f"%{clean_q}%", f"%{clean_q}%", f"%{clean_q}%", limit)).fetchall()
                for r in u_rows:
                    d = dict(r)
                    d["source"] = "⭐ Your Vault"
                    results.append(d)
                    seen_chunks.add(d["chunk"].lower())

        with self._lock, self._corpus_conn() as cconn:
            c_rows = []
            if self.fts5_supported:
                fts_tokens = " OR ".join([f'"{w}"*' for w in clean_q.split() if len(w) >= 2])
                if fts_tokens:
                    try:
                        c_rows = cconn.execute("""
                            SELECT chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence
                            FROM corpus_fts
                            WHERE corpus_fts MATCH ?
                            LIMIT ?
                        """, (fts_tokens, limit)).fetchall()
                    except Exception:
                        c_rows = []
            if not c_rows:
                c_rows = cconn.execute("""
                    SELECT chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence
                    WHERE chunk LIKE ? OR explanation LIKE ? OR example_sentence LIKE ?
                    FROM corpus_entries
                    LIMIT ?
                """, (f"%{clean_q}%", f"%{clean_q}%", f"%{clean_q}%", limit)).fetchall() if False else cconn.execute("""
                    SELECT chunk, register_tag, explanation, trigger_context, co_text_pattern, example_sentence
                    FROM corpus_entries
                    WHERE chunk LIKE ? OR explanation LIKE ? OR example_sentence LIKE ?
                    LIMIT ?
                """, (f"%{clean_q}%", f"%{clean_q}%", f"%{clean_q}%", limit)).fetchall()

            for r in c_rows:
                d = dict(r)
                if d["chunk"].lower() not in seen_chunks:
                    d["source"] = "🗽 Club Phrasebook"
                    results.append(d)
                    seen_chunks.add(d["chunk"].lower())

        return results[:limit]

    def get_rag_collocations_for_context(self, user_text: str, limit: int = 4) -> List[str]:
        stopwords = {
            "the", "and", "for", "that", "this", "with", "you", "not", "have", "are", "from",
            "was", "but", "what", "all", "when", "can", "said", "there", "use", "each", "which",
            "she", "how", "their", "will", "other", "about", "out", "many", "then", "them", "these"
        }
        words = [
            w.lower() for w in re.findall(r"[a-zA-Z]{4,}", user_text or "")
            if w.lower() not in stopwords
        ]
        if not words:
            return []
        query_str = " ".join(words[:4])
        hits = self.lookup_collocations(query_str, user_id=0, limit=limit)
        return [
            f"{h['chunk']} ({h['register_tag']}) [Pattern: {h['co_text_pattern']}] — e.g., '{h['example_sentence']}'"
            for h in hits
        ]

    def get_user(self, user_id: int, username: str = "", full_name: str = "") -> Dict[str, Any]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                is_wl = 1 if (user_id == ADMIN_ID or ADMIN_ID == 0) else 0
                d_lim = 500 if user_id == ADMIN_ID else 80
                conn.execute("""
                    INSERT INTO users (user_id, username, full_name, level, english_only, preferred_register, active_persona, current_mode, is_whitelisted, daily_limit, last_date)
                    VALUES (?, ?, ?, 'C1', 1, 'articulate', 'trio', 'spontaneous_chat', ?, ?, ?)
                """, (user_id, username, full_name, is_wl, d_lim, today))
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            else:
                r_dict = dict(row)
                if r_dict.get("last_date", "") != today:
                    conn.execute("UPDATE users SET daily_count = 0, last_date = ? WHERE user_id = ?", (today, user_id))
                    conn.commit()
                if username or full_name:
                    conn.execute(
                        "UPDATE users SET username = COALESCE(NULLIF(?, ''), username), full_name = COALESCE(NULLIF(?, ''), full_name) WHERE user_id = ?",
                        (username, full_name, user_id)
                    )
                    conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

            u = dict(row)
            if u.get("preferred_register") not in REGISTERS_MAP:
                u["preferred_register"] = "articulate"
            if u.get("active_persona") not in PERSONAS_MAP:
                u["active_persona"] = "trio"
            return u

    def update_user(self, user_id: int, **kwargs):
        if not kwargs:
            return
        allowed = {
            "level", "english_only", "preferred_register", "active_persona",
            "current_mode", "session_meta", "is_whitelisted", "onboarded", "daily_limit"
        }
        filtered = {k: v for k, v in kwargs.items() if k in allowed}
        if "level" in filtered and filtered["level"] in ("C1", "C2"):
            filtered["english_only"] = 1
        if not filtered:
            return
        cols = ", ".join([f"{k} = ?" for k in filtered.keys()])
        vals = list(filtered.values()) + [user_id]
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
            conn.commit()

    def get_session_meta(self, user_id: int) -> Dict[str, Any]:
        u = self.get_user(user_id)
        try:
            return json.loads(u.get("session_meta") or "{}")
        except Exception:
            return {}

    def update_session_meta(self, user_id: int, **kwargs):
        meta = self.get_session_meta(user_id)
        meta.update(kwargs)
        self.update_user(user_id, session_meta=json.dumps(meta, ensure_ascii=False))

    def check_and_increment_quota(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        if user_id == ADMIN_ID:
            with self._lock, self._conn() as conn:
                conn.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?", (user_id,))
                conn.commit()
            return True
        if u.get("daily_count", 0) >= u.get("daily_limit", 80):
            return False
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
        return True

    def set_whitelist(self, user_id: int, status: int = 1, daily_limit: int = 80):
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO users (user_id, is_whitelisted, daily_limit)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_whitelisted = excluded.is_whitelisted, daily_limit = excluded.daily_limit
            """, (user_id, status, daily_limit))
            conn.commit()

    def list_whitelisted(self) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM users WHERE is_whitelisted = 1 ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def create_invite_code(self, daily_limit: int = 80) -> str:
        code = "NYC-" + uuid.uuid4().hex[:6].upper()
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO invite_codes (code, daily_limit) VALUES (?, ?)", (code, daily_limit))
            conn.commit()
        return code

    def redeem_invite_code(self, user_id: int, code_str: str) -> Optional[int]:
        c = code_str.strip().upper()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM invite_codes WHERE code = ? AND used_by = 0", (c,)).fetchone()
            if not row:
                return None
            limit_val = row["daily_limit"]
            conn.execute("UPDATE invite_codes SET used_by = ? WHERE code = ?", (user_id, c))
            conn.execute("""
                INSERT INTO users (user_id, is_whitelisted, daily_limit)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_whitelisted = 1, daily_limit = excluded.daily_limit
            """, (user_id, limit_val))
            conn.commit()
            return limit_val

    def append_history(self, user_id: int, role: str, content: str):
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content[:1500]))
            conn.execute("""
                DELETE FROM chat_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT 18
                )
            """, (user_id, user_id))
            conn.commit()

    def get_history(self, user_id: int, limit: int = 10) -> List[Dict[str, str]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def reset_history(self, user_id: int):
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            conn.commit()

    def record_diagnostics(self, user_id: int, diagnostics: List[Dict[str, Any]]):
        if not diagnostics:
            return
        with self._lock, self._conn() as conn:
            for d in diagnostics:
                native_fix = (d.get("native_fix") or "").strip()
                user_said = (d.get("user_said") or "").strip()
                if not native_fix or not user_said:
                    continue
                conn.execute("""
                    INSERT INTO error_profile (user_id, category, user_said, native_fix, why_note, frequency, mastered)
                    VALUES (?, ?, ?, ?, ?, 1, 0)
                    ON CONFLICT(user_id, native_fix) DO UPDATE SET
                        frequency = frequency + 1,
                        user_said = excluded.user_said,
                        why_note = excluded.why_note,
                        mastered = 0,
                        last_seen = CURRENT_TIMESTAMP
                """, (
                    user_id,
                    d.get("category", "🎭 Natural Flow"),
                    user_said,
                    native_fix,
                    d.get("why_note", "")
                ))
            conn.commit()

    def get_weaknesses(self, user_id: int, limit: int = 6) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM error_profile
                WHERE user_id = ? AND mastered = 0
                ORDER BY frequency DESC, last_seen DESC
                LIMIT ?
            """, (user_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def mark_weakness_mastered(self, err_id: int):
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE error_profile SET mastered = 1 WHERE id = ?", (err_id,))
            conn.commit()

    # =================================================================
    # Active Productive Uptake Detector (Swain's Output Hypothesis)
    # =================================================================
    def detect_and_record_productive_uptake(self, user_id: int, user_utterance: str) -> List[str]:
        """Checks if the user actively produced any expression from their Vault in their own speech/text."""
        if not user_utterance or len(user_utterance) < 4:
            return []
        matched_chunks: List[str] = []
        with self._lock, self._conn() as conn:
            vault_rows = conn.execute(
                "SELECT id, chunk FROM lexical_vault WHERE user_id = ?", (user_id,)
            ).fetchall()
            for r in vault_rows:
                chunk = r["chunk"]
                # Check if build_smart_cloze masks it (meaning the chunk or its inflected form was used!)
                masked = build_smart_cloze(chunk, user_utterance)
                if "______" in masked and masked != "Complete this thought using your target phrase: ______":
                    matched_chunks.append(chunk)
                    conn.execute(
                        "UPDATE lexical_vault SET productive_count = productive_count + 1 WHERE id = ?",
                        (r["id"],)
                    )
            conn.commit()
        return matched_chunks[:3]

    def store_atomic_batch(
        self,
        user_id: int,
        token_key: str,
        items: List[Dict[str, Any]],
        audio_clean_text: str = "",
        mirror_target_text: str = "",
        host_reply_text: str = "",
        host_persona: str = "marcus",
        user_voice_file_id: str = ""
    ):
        self.ingest_chunks_to_corpus(items)
        now_ts = int(time.time())
        clean_host = re.sub(r"<[^>]+>", "", host_reply_text or "")
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM atomic_buffer WHERE created_at < ?", (now_ts - 172800,))
            conn.execute("""
                INSERT OR REPLACE INTO atomic_buffer
                (token_key, user_id, payload_json, audio_clean_text, mirror_target_text, host_reply_text, host_persona, user_voice_file_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token_key,
                user_id,
                json.dumps(items, ensure_ascii=False),
                audio_clean_text,
                mirror_target_text or audio_clean_text,
                clean_host,
                host_persona,
                user_voice_file_id,
                now_ts
            ))
            conn.commit()

    def get_atomic_meta(self, token_key: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM atomic_buffer WHERE token_key = ?", (token_key,)).fetchone()
            return dict(row) if row else None

    def add_vault_frame_direct(self, user_id: int, item: Dict[str, Any]) -> str:
        chunk = (item.get("chunk") or "").strip()
        if not chunk:
            return ""
        example = (item.get("example") or item.get("example_sentence") or "").strip()
        ai_cloze = (item.get("cloze_sentence") or "").strip()
        cloze = build_smart_cloze(chunk, example, ai_cloze=ai_cloze)
        today = datetime.utcnow().strftime("%Y-%m-%d")

        with self._lock, self._conn() as conn:
            cur = conn.execute("""
                INSERT OR IGNORE INTO lexical_vault
                (user_id, chunk, register_tag, explanation, flat_b2_foil, trigger_context, co_text_pattern, typical_followup, example_sentence, cloze_sentence, srs_next_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                chunk,
                item.get("register_tag", "🎙️ Articulate"),
                item.get("explanation", ""),
                item.get("flat_b2_foil", ""),
                item.get("trigger", item.get("trigger_context", "")),
                item.get("pattern", item.get("co_text_pattern", "")),
                item.get("followup", item.get("typical_followup", "")),
                example,
                cloze,
                today
            ))
            conn.commit()
            return chunk if cur.rowcount > 0 else f"{chunk} (Already in Vault)"

    def save_atomic_item_to_vault(self, user_id: int, token_key: str, item_idx: int) -> Optional[str]:
        meta = self.get_atomic_meta(token_key)
        if not meta:
            return None
        try:
            items = json.loads(meta["payload_json"])
            idx = item_idx - 1
            if idx < 0 or idx >= len(items):
                return None
            return self.add_vault_frame_direct(user_id, items[idx])
        except Exception as e:
            logger.error(f"Atomic save error: {e}")
            return None

    def get_stealth_injection_targets(self, user_id: int, limit: int = 2) -> Tuple[List[Dict[str, Any]], List[int]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("""
                SELECT id, chunk, register_tag, trigger_context
                FROM lexical_vault
                WHERE user_id = ? AND stealth_done < stealth_target
                ORDER BY stealth_done ASC, id DESC
                LIMIT ?
            """, (user_id, limit)).fetchall()
            items = [dict(r) for r in rows]
            ids = [r["id"] for r in items]
            return items, ids

    def increment_stealth_done(self, vault_ids: List[int]):
        if not vault_ids:
            return
        placeholders = ",".join(["?"] * len(vault_ids))
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE lexical_vault SET stealth_done = stealth_done + 1 WHERE id IN ({placeholders})", vault_ids)
            conn.commit()

    def get_due_srs_card(self, user_id: int) -> Optional[Dict[str, Any]]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM lexical_vault
                WHERE user_id = ? AND (srs_next_review <= ? OR srs_next_review = '')
                ORDER BY srs_next_review ASC, id ASC
                LIMIT 1
            """, (user_id, today)).fetchone()
            return dict(row) if row else None

    def get_srs_card_by_id(self, card_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM lexical_vault WHERE id = ?", (card_id,)).fetchone()
            return dict(row) if row else None

    def grade_srs_card(self, card_id: int, quality: int, increment_productive: bool = False):
        card = self.get_srs_card_by_id(card_id)
        if not card:
            return
        reps = int(card.get("srs_repetitions", 0))
        ef = float(card.get("srs_efactor", 2.5))
        interval = int(card.get("srs_interval", 1))
        prod = int(card.get("productive_count", 0)) + (1 if increment_productive else 0)

        if quality < 3:
            reps = 0
            interval = 1
        else:
            if reps == 0:
                interval = 1
            elif reps == 1:
                interval = 3
            else:
                interval = max(1, int(round(interval * ef)))
            reps += 1

        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        ef = max(1.3, ef)
        next_date = (datetime.utcnow() + timedelta(days=interval)).strftime("%Y-%m-%d")

        with self._lock, self._conn() as conn:
            conn.execute("""
                UPDATE lexical_vault
                SET srs_repetitions = ?, srs_efactor = ?, srs_interval = ?, srs_next_review = ?, productive_count = ?
                WHERE id = ?
            """, (reps, ef, interval, next_date, prod, card_id))
            conn.commit()

    def get_user_vault(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lexical_vault WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            total_vault = conn.execute("SELECT COUNT(*) FROM lexical_vault WHERE user_id = ?", (user_id,)).fetchone()[0]
            due_srs = conn.execute(
                "SELECT COUNT(*) FROM lexical_vault WHERE user_id = ? AND (srs_next_review <= ? OR srs_next_review = '')",
                (user_id, today)
            ).fetchone()[0]
            active_errors = conn.execute("SELECT COUNT(*) FROM error_profile WHERE user_id = ? AND mastered = 0", (user_id,)).fetchone()[0]
            mastered_errors = conn.execute("SELECT COUNT(*) FROM error_profile WHERE user_id = ? AND mastered = 1", (user_id,)).fetchone()[0]
            stealth_mastered = conn.execute("SELECT COUNT(*) FROM lexical_vault WHERE user_id = ? AND stealth_done >= stealth_target", (user_id,)).fetchone()[0]
            productive_mastered = conn.execute("SELECT COUNT(*) FROM lexical_vault WHERE user_id = ? AND productive_count > 0", (user_id,)).fetchone()[0]
            return {
                "total_vault": total_vault,
                "due_srs": due_srs,
                "active_errors": active_errors,
                "mastered_errors": mastered_errors,
                "stealth_mastered": stealth_mastered,
                "productive_mastered": productive_mastered
            }

    def backup_to_telegram(self, bot: Any, caption_note: str = "Vault Sync") -> bool:
        if not ADMIN_ID or not bot:
            return False
        try:
            old_msg_id = self.get_sys_state("last_backup_msg_id", "")
            snap_path = f"{self.user_db_path}.backup"
            with self._lock, self._conn() as src_conn:
                dst_conn = sqlite3.connect(snap_path)
                src_conn.backup(dst_conn)
                dst_conn.close()

            with open(snap_path, "rb") as f:
                sent = bot.send_document(
                    ADMIN_ID,
                    f,
                    visible_file_name=os.path.basename(self.user_db_path),
                    caption=f"📓 <i>{caption_note}</i>",
                    parse_mode="HTML",
                    disable_notification=True
                )
            try:
                bot.pin_chat_message(ADMIN_ID, sent.message_id, disable_notification=True)
            except Exception:
                pass

            if old_msg_id and old_msg_id.isdigit() and int(old_msg_id) != sent.message_id:
                try:
                    bot.delete_message(ADMIN_ID, int(old_msg_id))
                except Exception:
                    pass

            self.set_sys_state("last_backup_msg_id", str(sent.message_id))
            if os.path.exists(snap_path):
                os.remove(snap_path)
            return True
        except Exception as e:
            logger.error(f"Telegram backup error: {e}")
            return False

    def restore_from_telegram_pin(self, bot: Any) -> bool:
        if not ADMIN_ID or not bot:
            return False
        try:
            chat = bot.get_chat(ADMIN_ID)
            pinned = getattr(chat, "pinned_message", None)
            expected_name = os.path.basename(self.user_db_path)
            if pinned and pinned.document and pinned.document.file_name == expected_name:
                file_info = bot.get_file(pinned.document.file_id)
                downloaded = bot.download_file(file_info.file_path)
                temp_check = f"{self.user_db_path}.restore_tmp"
                with open(temp_check, "wb") as f:
                    f.write(downloaded)

                chk_conn = sqlite3.connect(temp_check)
                cols = [c[1] for c in chk_conn.execute("PRAGMA table_info(users)").fetchall()]
                chk_conn.close()

                if "user_id" in cols:
                    with self._lock:
                        os.replace(temp_check, self.user_db_path)
                    self.init_schema()
                    self.set_sys_state("last_backup_msg_id", str(pinned.message_id))
                    return True
                else:
                    os.remove(temp_check)
        except Exception as e:
            logger.warning(f"Could not auto-restore from pinned message: {e}")
        return False
