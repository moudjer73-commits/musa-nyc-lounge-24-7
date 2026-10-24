# -*- coding: utf-8 -*-
"""
database.py — Dual-Database Engine for Musa's NYC Lounge
Features:
- Inflection-Aware Smart Cloze Generator (Fixes morphological & pronoun-insertion leaks)
- Active Productive Uptake Detector (Swain's Output Hypothesis tracking)
- Trio Persona State ('marcus', 'chloe', 'jake', 'trio') & B2-to-C2 Foil storage
- SQLite WAL Concurrency & Self-Cleaning Silent Telegram Backup
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
    REGISTERS_MAP
)

logger = logging.getLogger("MusaNYCLounge.DB")

# Irregular verb stems for accurate morphological Cloze masking & Active Uptake detection
IRREGULAR_STEM_MAP: Dict[str, List[str]] = {
    "wind": ["wind", "winds", "wound", "winding"],
    "bite": ["bite", "bites", "bit", "bitten", "biting"],
    "take": ["take", "takes", "took", "taken", "taking"],
    "cut": ["cut", "cuts", "cutting"],
    "hit": ["hit", "hits", "hitting"],
    "let": ["let", "lets", "letting"],
    "put": ["put", "puts", "putting"],
    "run": ["run", "runs", "ran", "running"],
    "fall": ["fall", "falls", "fell", "fallen", "falling"],
    "give": ["give", "gives", "gave", "given", "giving"],
    "have": ["have", "has", "had", "having", "'ve", "'s", "'d"],
    "be": ["be", "am", "is", "are", "was", "were", "been", "being", "'m", "'re", "'s"],
    "get": ["get", "gets", "got", "gotten", "getting"],
    "go": ["go", "goes", "went", "gone", "going"],
    "come": ["come", "comes", "came", "coming"],
    "make": ["make", "makes", "made", "making"],
    "hold": ["hold", "holds", "held", "holding"],
    "keep": ["keep", "keeps", "kept", "keeping"],
    "leave": ["leave", "leaves", "left", "leaving"],
    "catch": ["catch", "catches", "caught", "catching"],
    "throw": ["throw", "throws", "threw", "thrown", "throwing"],
    "bring": ["bring", "brings", "brought", "bringing"],
    "think": ["think", "thinks", "thought", "thinking"],
    "buy": ["buy", "buys", "bought", "buying"],
    "break": ["break", "breaks", "broke", "broken", "breaking"],
    "speak": ["speak", "speaks", "spoke", "spoken", "speaking"],
    "drive": ["drive", "drives", "drove", "driven", "driving"],
    "ride": ["ride", "rides", "rode", "ridden", "riding"],
    "write": ["write", "writes", "wrote", "written", "writing"],
    "see": ["see", "sees", "saw", "seen", "seeing"],
    "eat": ["eat", "eats", "ate", "eaten", "eating"],
    "drink": ["drink", "drinks", "drank", "drunk", "drinking"],
    "blow": ["blow", "blows", "blew", "blown", "blowing"],
    "grow": ["grow", "grows", "grew", "grown", "growing"],
    "know": ["know", "knows", "knew", "known", "knowing"],
    "fly": ["fly", "flies", "flew", "flown", "flying"],
    "draw": ["draw", "draws", "drew", "drawn", "drawing"],
    "wear": ["wear", "wears", "wore", "worn", "wearing"],
    "tear": ["tear", "tears", "tore", "torn", "tearing"],
    "stand": ["stand", "stands", "stood", "standing"],
    "sit": ["sit", "sits", "sat", "sitting"],
    "set": ["set", "sets", "setting"],
    "pay": ["pay", "pays", "paid", "paying"],
    "say": ["say", "says", "said", "saying"],
    "sell": ["sell", "sells", "sold", "selling"],
    "tell": ["tell", "tells", "told", "telling"],
    "lose": ["lose", "loses", "lost", "losing"],
    "shoot": ["shoot", "shoots", "shot", "shooting"],
    "lead": ["lead", "leads", "led", "leading"],
    "read": ["read", "reads", "reading"],
    "meet": ["meet", "meets", "met", "meeting"],
    "spend": ["spend", "spends", "spent", "spending"],
    "send": ["send", "send", "sends", "sent", "sending"],
    "build": ["build", "builds", "built", "building"],
    "find": ["find", "finds", "found", "finding"],
    "bind": ["bind", "binds", "bound", "binding"]
}

PLACEHOLDER_STOPWORDS = {
    "someone", "somebody", "something", "somewhere", "one", "ones",
    "your", "my", "his", "her", "their", "our", "one's", "someone's",
    "a", "an", "the", "it", "them", "him", "us", "you", "me"
}

INITIAL_CORPUS_SEED: List[Tuple[str, str, str, str, str, str]] = [
    ("play it by ear", "☕ Casual", "Decide how to handle something as it unfolds instead of planning ahead", "When plans are uncertain or flexible", "let's just / we'll + play it by ear", "No need to lock anything down right now—let's just play it by ear."),
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
    ("jump the gun", "☕ Casual", "Act too soon before having all the facts", "Stopping someone from reacting prematurely", "let's not jump the gun", "Hold on, let's not jump the gun before we actually hear his side of the story."),
    ("hit the nail on the head", "🎙️ Articulate", "Pinpoint the exact truth or root cause of an issue", "Validating a sharp observation", "you hit the nail right on the head", "You hit the nail on the head—that's the exact issue nobody wants to admit."),
    ("let it slide", "☕ Casual", "Ignore a minor mistake or annoyance instead of making a big deal out of it", "Choosing peace over confrontation", "just let it slide this time", "It annoyed me a little, sure, but I figured I'd just let it slide."),
    ("by the skin of my teeth", "🎭 Witty", "Barely manage to do something or escape a close call", "Narrating a narrow escape", "made it by the skin of my teeth", "The subway doors were literally closing—I caught the train by the skin of my teeth."),
    ("have a lot on my plate", "☕ Casual", "Be busy juggling many responsibilities at once", "Politely declining extra work", "I've got a lot on my plate right now", "I'd love to help out, man, but I've got way too much on my plate this week."),
    ("boil down to", "🎙️ Articulate", "Come down to the single most important factor or core issue", "Summarizing a complex argument", "what it really boils down to is [noun]", "At the end of the day, the whole disagreement boils down to trust."),
    ("pan out", "🎙️ Articulate", "Turn out well or develop in a particular way", "Talking about how a plan or project unfolded", "see how things pan out / didn't pan out", "We tried launching it in the spring, but the timing just didn't pan out."),
    ("iron out", "🎙️ Articulate", "Resolve remaining small problems or details", "Finalizing a plan or agreement", "iron out a few kinks / details", "We're almost ready—we just need to iron out a few last-minute details."),
    ("brush up on", "☕ Casual", "Review and refresh a skill you haven't used lately", "Preparing for a trip or presentation", "brush up on my [skill]", "I really need to brush up on my spoken French before the conference."),
    ("run it by", "🎙️ Articulate", "Tell someone about an idea to get their quick feedback or approval", "Checking with a colleague or friend", "let me run this by [person]", "That sounds promising—let me run it by Sarah and see what she thinks."),
    ("fall through", "☕ Casual", "Fail to happen after being planned (deals, trips, plans)", "Explaining why a plan was canceled", "our plans fell through at the last minute", "We were supposed to drive upstate, but the whole trip fell through."),
    ("give someone the benefit of the doubt", "🎙️ Articulate", "Choose to believe someone had good intentions despite doubts", "Being fair when evidence is incomplete", "give [person] the benefit of the doubt", "Maybe his phone really did die—let's give him the benefit of the doubt."),
    ("not my cup of tea", "🎭 Witty", "Polite, understated way to say you really dislike something", "Declining an activity without sounding harsh", "just not really my cup of tea", "Crowded nightclubs aren't really my cup of tea anymore."),
    ("pull strings", "🎙️ Articulate", "Use personal influence or connections behind the scenes", "Explaining how someone got a rare favor", "pull a few strings to make it happen", "He had to pull a few strings just to get us a table on Friday night."),
    ("keep me posted", "☕ Casual", "Keep me updated as things develop", "Wrapping up a conversation when waiting for news", "keep me posted on how it goes", "Good luck with the interview tomorrow—definitely keep me posted!")
]


def _word_regex_token(word: str) -> str:
    w = word.lower().strip()
    if w in PLACEHOLDER_STOPWORDS:
        return r"(?:\w+(?:'\w+)?\s+){0,3}?"
    if w in IRREGULAR_STEM_MAP:
        forms = "|".join(re.escape(f) for f in IRREGULAR_STEM_MAP[w])
        return rf"\b(?:{forms})\b"
    if len(w) > 3:
        stem = re.escape(w[:-1] if w.endswith("e") else w)
        return rf"\b{stem}\w*\b"
    return rf"\b{re.escape(w)}\b"


def build_smart_cloze(chunk: str, example: str, ai_cloze: str = "") -> str:
    """
    Creates an accurate fill-in-the-blank sentence without leaking the target chunk,
    handling irregular verb inflections (wind -> wound) and inserted pronouns
    (cut someone some slack -> cut him some slack).
    """
    if ai_cloze and "____" in ai_cloze and chunk.lower() not in ai_cloze.lower():
        return ai_cloze.strip()

    if not example:
        return "______"

    # 1. Exact case-insensitive match
    exact_re = re.compile(re.escape(chunk), re.IGNORECASE)
    if exact_re.search(example):
        return exact_re.sub("______", example)

    # 2. Inflection & pronoun-bridging regex
    tokens = [t for t in re.split(r"\s+", chunk.strip()) if t]
    if tokens:
        pattern_parts = []
        for tok in tokens:
            clean_tok = re.sub(r"[^a-zA-Z']", "", tok)
            if not clean_tok:
                continue
            pattern_parts.append(_word_regex_token(clean_tok))
        if pattern_parts:
            flex_pattern = r"\s+(?:\w+(?:'\w+)?\s+){0,2}?".join(pattern_parts)
            try:
                flex_re = re.compile(flex_pattern, re.IGNORECASE)
                if flex_re.search(example):
                    return flex_re.sub("______", example, count=1)
            except Exception:
                pass

    # 3. Fallback: mask individual content words from chunk inside example so answer never leaks
    masked = example
    content_words = [
        re.sub(r"[^a-zA-Z']", "", t) for t in tokens
        if len(t) >= 3 and t.lower() not in PLACEHOLDER_STOPWORDS
    ]
    replaced_any = False
    for cw in content_words:
        tok_pat = _word_regex_token(cw)
        try:
            m_re = re.compile(tok_pat, re.IGNORECASE)
            if m_re.search(masked):
                masked = m_re.sub("____", masked, count=1)
                replaced_any = True
        except Exception:
            pass

    if replaced_any:
        return masked
    return "Complete the thought with the target expression: ______"


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
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
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
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
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
                    b2_foil TEXT DEFAULT '',
                    explanation TEXT DEFAULT '',
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
                    category TEXT DEFAULT 'Spoken Flow',
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
                    user_transcript_text TEXT DEFAULT '',
                    host_reply_text TEXT DEFAULT '',
                    active_persona TEXT DEFAULT 'marcus',
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

            # Safe Auto-Migrations for Existing Restored Databases
            u_cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "active_persona" not in u_cols:
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN active_persona TEXT DEFAULT 'trio'")
                except Exception:
                    pass

            lv_cols = [c[1] for c in conn.execute("PRAGMA table_info(lexical_vault)").fetchall()]
            if "b2_foil" not in lv_cols:
                try:
                    conn.execute("ALTER TABLE lexical_vault ADD COLUMN b2_foil TEXT DEFAULT ''")
                except Exception:
                    pass
            if "productive_count" not in lv_cols:
                try:
                    conn.execute("ALTER TABLE lexical_vault ADD COLUMN productive_count INTEGER DEFAULT 0")
                except Exception:
                    pass

            ab_cols = [c[1] for c in conn.execute("PRAGMA table_info(atomic_buffer)").fetchall()]
            for col_name, col_def in [
                ("host_reply_text", "TEXT DEFAULT ''"),
                ("user_transcript_text", "TEXT DEFAULT ''"),
                ("active_persona", "TEXT DEFAULT 'marcus'")
            ]:
                if col_name not in ab_cols:
                    try:
                        conn.execute(f"ALTER TABLE atomic_buffer ADD COLUMN {col_name} {col_def}")
                    except Exception:
                        pass

            # Retroactively repair any leaked "(Phrase: ______)" clozes in existing vault rows
            bad_clozes = conn.execute(
                "SELECT id, chunk, example_sentence, cloze_sentence FROM lexical_vault WHERE cloze_sentence LIKE '%(Phrase:%'"
            ).fetchall()
            for bc in bad_clozes:
                fixed_c = build_smart_cloze(bc["chunk"], bc["example_sentence"])
                conn.execute("UPDATE lexical_vault SET cloze_sentence = ? WHERE id = ?", (fixed_c, bc["id"]))

            if ADMIN_ID > 0:
                conn.execute("""
                    INSERT OR IGNORE INTO users (user_id, full_name, level, english_only, preferred_register, active_persona, is_whitelisted, onboarded, daily_limit)
                    VALUES (?, 'Musa', 'C2', 1, 'articulate', 'trio', 1, 1, 500)
                """, (ADMIN_ID,))
                conn.execute("UPDATE users SET is_whitelisted = 1, daily_limit = 500 WHERE user_id = ?", (ADMIN_ID,))

            conn.execute("""
                UPDATE users
                SET preferred_register = 'articulate'
                WHERE preferred_register NOT IN ("articulate", "casual", "witty", "executive", "diplomatic") OR preferred_register IS NULL
            """)
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

    def _raw_lookup_collocations(self, query: str, user_id: int = 0, limit: int = 8) -> List[Dict[str, Any]]:
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
            if u.get("active_persona") not in ("trio", "duo_mc", "duo_cj", "duo_mj", "marcus", "chloe", "jake"):
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
                    d.get("category", "Spoken Flow"),
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
    def detect_and_record_active_uptake(self, user_id: int, user_utterance: str) -> List[str]:
        """
        Scans the member's spoken or written utterance for active, unprompted production
        of expressions stored in their personal lexical_vault. Increments `productive_count`
        when matched.
        """
        if not user_utterance or len(user_utterance.strip()) < 4:
            return []

        matched_chunks: List[str] = []
        matched_ids: List[int] = []

        with self._lock, self._conn() as conn:
            vault_rows = conn.execute(
                "SELECT id, chunk FROM lexical_vault WHERE user_id = ?", (user_id,)
            ).fetchall()

            for r in vault_rows:
                chunk = (r["chunk"] or "").strip()
                if not chunk:
                    continue
                tokens = [re.sub(r"[^a-zA-Z']", "", t) for t in chunk.split() if t]
                pattern_parts = [_word_regex_token(t) for t in tokens if t]
                if not pattern_parts:
                    continue
                flex_pat = r"\s+(?:\w+(?:'\w+)?\s+){0,2}?".join(pattern_parts)
                try:
                    if re.search(flex_pat, user_utterance, re.IGNORECASE):
                        matched_chunks.append(chunk)
                        matched_ids.append(r["id"])
                except Exception:
                    if chunk.lower() in user_utterance.lower():
                        matched_chunks.append(chunk)
                        matched_ids.append(r["id"])

            if matched_ids:
                placeholders = ",".join(["?"] * len(matched_ids))
                conn.execute(
                    f"UPDATE lexical_vault SET productive_count = productive_count + 1 WHERE id IN ({placeholders})",
                    matched_ids
                )
                conn.commit()

        return matched_chunks[:3]

    def store_atomic_batch(
        self,
        user_id: int,
        token_key: str,
        items: List[Dict[str, Any]],
        audio_clean_text: str = "",
        host_reply_text: str = "",
        user_voice_file_id: str = "",
        user_transcript_text: str = "",
        active_persona: str = "marcus"
    ):
        self.ingest_chunks_to_corpus(items)
        now_ts = int(time.time())
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM atomic_buffer WHERE created_at < ?", (now_ts - 172800,))
            conn.execute("""
                INSERT OR REPLACE INTO atomic_buffer
                (token_key, user_id, payload_json, audio_clean_text, user_transcript_text, host_reply_text, active_persona, user_voice_file_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token_key,
                user_id,
                json.dumps(items, ensure_ascii=False),
                audio_clean_text,
                user_transcript_text,
                host_reply_text,
                active_persona,
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
                (user_id, chunk, register_tag, b2_foil, explanation, trigger_context, co_text_pattern, typical_followup, example_sentence, cloze_sentence, srs_next_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                chunk,
                item.get("register_tag", "🎙️ Articulate"),
                item.get("b2_foil", ""),
                item.get("explanation", ""),
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

    def get_stealth_injection_targets(self, user_id: int, limit: int = 4) -> Tuple[List[Dict[str, Any]], List[int]]:
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

    def grade_srs_card(self, card_id: int, quality: int):
        card = self.get_srs_card_by_id(card_id)
        if not card:
            return
        reps = int(card.get("srs_repetitions", 0))
        ef = float(card.get("srs_efactor", 2.5))
        interval = int(card.get("srs_interval", 1))

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
                SET srs_repetitions = ?, srs_efactor = ?, srs_interval = ?, srs_next_review = ?
                WHERE id = ?
            """, (reps, ef, interval, next_date, card_id))
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

    # =================================================================
    # Self-Cleaning Silent Vault Sync (Deletes previous backup message)
    # =================================================================
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

                if "user_id" in cols and "preferred_register" in cols:
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

    def lookup_collocations(self, query: str, user_id: Optional[int] = None, limit: int = 5) -> List[Dict[str, Any]]:
        q_clean = re.sub(r"[^a-zA-Z0-9'\s-]", "", (query or "")).strip().lower()
        if not q_clean:
            return []
        stopwords = {
            "a", "an", "the", "by", "to", "in", "on", "of", "for", "with", "at", "from",
            "up", "down", "out", "off", "over", "under", "as", "is", "are", "was", "were",
            "be", "been", "it", "this", "that", "my", "your", "our", "their", "his", "her",
            "and", "or", "but", "so", "if", "into", "about", "far", "very", "much", "more"
        }
        raw_hits = self._raw_lookup_collocations(query, user_id=user_id, limit=25)
        full_pat = re.compile(r"\b" + re.escape(q_clean) + r"\b", re.IGNORECASE)
        content_tokens = [t for t in q_clean.split() if len(t) >= 4 and t not in stopwords]

        filtered = []
        seen = set()
        for h in raw_hits:
            chunk_txt = (h.get("chunk") or "").strip()
            key = chunk_txt.lower()
            if not key or key in seen:
                continue
            # 1) تطابق العبارة الكاملة داخل حقل chunk نفسه (وليس في جملة المثال)
            if full_pat.search(chunk_txt):
                seen.add(key)
                filtered.append(h)
            # 2) أو تطابق كلمة دلالية رئيسية (>= 4 أحرف وليست من حروف الجر) ككلمة مستقلة داخل chunk
            elif content_tokens and all(re.search(r"\b" + re.escape(tok) + r"\b", chunk_txt, re.IGNORECASE) for tok in content_tokens):
                seen.add(key)
                filtered.append(h)
            if len(filtered) >= limit:
                break
        return filtered

    def set_user_mode(self, user_id: int, mode: str):
        try:
            with self._conn() as conn:
                conn.execute("UPDATE users SET current_mode = ? WHERE user_id = ?", (mode, user_id))
        except Exception:
            pass

    def set_user_field(self, user_id: int, field: str, value: Any):
        allowed = {"current_mode", "preferred_register", "active_persona", "voice_enabled", "daily_goal", "level", "english_only"}
        if field not in allowed:
            return
        self.get_user(user_id)
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
            conn.commit()
