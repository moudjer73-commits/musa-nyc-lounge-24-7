# -*- coding: utf-8 -*-
"""
🗽 MUSA'S NYC LOUNGE (24/7 Relay Architecture)
Batch 1: Configuration, Linguistic Persona, Pydantic Schemas & SQLite Engine
"""

import os
import sys
import time
import json
import sqlite3
import logging
import tempfile
import subprocess
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# =====================================================================
# 1. إعدادات السجلات والمتغيرات البيئية (Environment & Logging)
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MusaNYCLounge")

# قراءة مرنة لمتغيرات البيئة لضمان التوافق مع GitHub Secrets أو Colab
BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TOKEN", "")
).strip()

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY", "")
).strip()

ADMIN_ID_RAW = (
    os.getenv("ADMIN_ID")
    or os.getenv("CHAT_ID")
    or os.getenv("OWNER_ID")
    or "0"
).strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

DB_PATH = os.getenv("DB_PATH", "musa_nyc_lounge.db")
CORPUS_DB_PATH = os.getenv("CORPUS_DB_PATH", "collocations_corpus.db")
TEMP_DIR = "temp_lounge_media"
os.makedirs(TEMP_DIR, exist_ok=True)

# مدة التشغيل لدورة GitHub Actions (5.3 ساعات = 318 دقيقة قبل الإغلاق الآمن والنسخ الاحتياطي)
RELAY_MAX_RUNTIME_SECONDS = int(os.getenv("RELAY_MAX_RUNTIME_SECONDS", str(318 * 60)))
START_TIME = time.time()

# إعداد نماذج Gemini والأصوات الأمريكية العصبية (Edge-TTS)
PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.5-pro"

US_VOICES = {
    "male_host": "en-US-AndrewMultilingualNeural",   # مذيع نيويوركي دافئ وطبيعي جداً
    "female_host": "en-US-AvaMultilingualNeural",    # مذيعة أمريكية حوارية وواضحة
    "casual_guy": "en-US-GuyNeural",
    "witty_gal": "en-US-AriaNeural"
}

# =====================================================================
# 2. دستور الهوية اللغوية (NYC Lounge Anti-Corporate Manifesto)
# =====================================================================
FORBIDDEN_CORPORATE_CLICHES = [
    "delve", "delve into", "utilize", "foster", "navigate challenges",
    "testament to", "tapestry", "moreover", "furthermore", "in conclusion",
    "it is crucial", "synergy", "leverage", "paradigm", "facilitate",
    "endeavor", "commence", "multifaceted", "underscore", "pivotal"
]

REGISTERS_INFO = {
    "articulate": "🎙️ Articulate / Podcast (Smart, natural, flowing spoken American English)",
    "casual": "☕ Casual / NYC Vernacular (Warm, blunt, ellipsis, phrasal verbs, idioms)",
    "witty": "🎭 Witty / Dry Humor & Subtext (Understatement, sharp irony, clever framing)"
}

NYC_LOUNGE_SYSTEM_CORE = f"""
You are the host and master linguistic coach at "Musa's NYC Lounge" — a sharp, warm, culturally savvy New Yorker.
Your core mission: Transform the user's English from stiff textbook/corporate patterns into effortless, Native-Like Spoken American English (C1/C2 intuition).

STRICT ANTI-CORPORATE BAN:
- NEVER sound like an HR manager, a business email, or an academic textbook.
- NEVER use these banned words: {', '.join(FORBIDDEN_CORPORATE_CLICHES)}.
- Champion spoken American grammar: conversational gambits, phrasal verbs, idiomatic chunks, heads/tails ("That guy over there, he's actually..."), smart hedging ("sort of", "or something along those lines"), and pragmatic subtext.

REGISTER TAGGING SYSTEM:
Always tag your rewrites and lexical upgrades with one of these exact badges:
- [🎙️ Articulate]: Smart conversational / Podcast host vibe.
- [☕ Casual]: Fast everyday friendly NYC vernacular.
- [🎭 Witty]: Ironic, understated, or punchy storytelling vibe.

3-PART LEXICAL FRAME RULE:
Every lexical item you teach must include:
1. Trigger: The exact social/mental moment a native speaker reaches for this expression.
2. Pattern & Co-text: What verbs, prepositions, or polarity (e.g., mostly negative) glue to it.
3. Typical Follow-up: What natural sentence usually follows it to close the thought.
"""

# =====================================================================
# 3. هياكل البيانات المنظمة (Pydantic Schemas for Gemini JSON Output)
# =====================================================================
class LexicalUpgradeItem(BaseModel):
    chunk: str = Field(description="The target American idiom, phrasal verb, or lexical chunk.")
    register_tag: str = Field(description="Must be [🎙️ Articulate], [☕ Casual], or [🎭 Witty].")
    trigger: str = Field(description="When and why an American native uses this chunk.")
    pattern: str = Field(description="Grammar pattern and collocating words.")
    followup: str = Field(description="A natural follow-up sentence a native would say next.")
    example: str = Field(description="A vivid, non-corporate example sentence.")
    cloze: str = Field(description="The example sentence with the core chunk replaced by '______' for SRS review.")
    explanation: str = Field(description="Concise explanation of nuance and subtext.")


class DiagnosticPoint(BaseModel):
    category: str = Field(description="Grammar | Unnatural_L1 | Connected_Speech | Register_Subtext")
    user_said: str = Field(description="The exact phrase from the user.")
    native_fix: str = Field(description="The natural American replacement.")
    why_note: str = Field(description="Sharp explanation of why the original felt stiff/wrong or how to link sounds.")


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(description="Exact transcription if input was audio, else mirrors user text.")
    lounge_reply: str = Field(description="Your natural, engaging, witty conversational reply as the NYC friend/host. Incorporate stealth targets naturally if provided.")
    tts_clean_target: str = Field(description="The single best native rewrite of the user's message, clean for US Audio Mirror generation.")
    rewrite_articulate: str = Field(description="User's thought rewritten in [🎙️ Articulate] podcast style.")
    rewrite_casual: str = Field(description="User's thought rewritten in [☕ Casual] everyday NYC style.")
    rewrite_witty: str = Field(description="User's thought rewritten in [🎭 Witty] ironic/expressive style.")
    acoustic_prosody_xray: str = Field(description="Detailed American Connected Speech breakdown (Flap T, Schwa, Linking, Stress, Intonation contour) with phonetic respelling.")
    diagnostics: List[DiagnosticPoint] = Field(description="1 to 3 surgical linguistic or phonetic fixes.")
    lexical_upgrades: List[LexicalUpgradeItem] = Field(description="Exactly 3 high-value lexical chunks for Atomic Saving.")


class PodcastLine(BaseModel):
    speaker: str = Field(description="Must be 'male_host' (Andrew) or 'female_host' (Ava).")
    text: str = Field(description="Spoken dialogue line with natural American rhythm and fillers.")


class PodcastEpisodeSchema(BaseModel):
    title: str = Field(description="Catchy NYC Lounge podcast episode title.")
    target_chunks_covered: List[str] = Field(description="List of chunks woven into the script.")
    script: List[PodcastLine] = Field(description="10 to 16 engaging back-and-forth dialogue turns.")
    key_takeaways: List[LexicalUpgradeItem] = Field(description="3 key chunks from the episode for Atomic Saving.")


# =====================================================================
# 4. محرك قاعدة البيانات الذكي (SQLite WAL + Telegram Relay Persistence)
# =====================================================================
class LoungeDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_schema(self):
        with self._lock, self._conn() as conn:
            # 1. جدول المستخدمين وحالة الجلسة
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    full_name TEXT DEFAULT '',
                    level TEXT DEFAULT 'C1',
                    english_only INTEGER DEFAULT 1,
                    preferred_register TEXT DEFAULT 'articulate',
                    current_mode TEXT DEFAULT 'spontaneous_chat',
                    session_meta TEXT DEFAULT '{}',
                    is_whitelisted INTEGER DEFAULT 0,
                    onboarded INTEGER DEFAULT 0,
                    daily_count INTEGER DEFAULT 0,
                    daily_limit INTEGER DEFAULT 120,
                    last_date TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. المفكرة الذرية + التكرار المتباعد (SM-2) + طابور التشبع الخفي
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lexical_vault (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    chunk TEXT NOT NULL,
                    register_tag TEXT DEFAULT '[🎙️ Articulate]',
                    trigger_context TEXT DEFAULT '',
                    co_text_pattern TEXT DEFAULT '',
                    typical_followup TEXT DEFAULT '',
                    example_sentence TEXT DEFAULT '',
                    cloze_sentence TEXT DEFAULT '',
                    explanation TEXT DEFAULT '',
                    easiness_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 1,
                    repetitions INTEGER DEFAULT 0,
                    next_review_date TEXT DEFAULT '',
                    stealth_target INTEGER DEFAULT 4,
                    stealth_done INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. سجل نقاط الضعف الذكي (Error Profile)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS error_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    category TEXT,
                    user_said TEXT,
                    native_fix TEXT,
                    why_note TEXT,
                    frequency INTEGER DEFAULT 1,
                    mastered INTEGER DEFAULT 0,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. ذاكرة الحوار المستمرة
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. ذاكرة الأزرار الذرية المؤقتة (Atomic Save Buffer)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS atomic_buffer (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    token_key TEXT,
                    item_idx INTEGER,
                    payload_json TEXT,
                    audio_clean_text TEXT DEFAULT '',
                    user_voice_file_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            if ADMIN_ID > 0:
                conn.execute("""
                    INSERT OR IGNORE INTO users (user_id, full_name, level, english_only, is_whitelisted, onboarded)
                    VALUES (?, 'Musa (Founder)', 'C2', 1, 1, 1)
                """, (ADMIN_ID,))
            conn.commit()

    # --- إدارة المستخدمين والصلاحيات ---
    def get_user(self, user_id: int, username: str = "", full_name: str = "") -> Dict[str, Any]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                is_wl = 1 if (user_id == ADMIN_ID or ADMIN_ID == 0) else 0
                conn.execute("""
                    INSERT INTO users (user_id, username, full_name, is_whitelisted, last_date)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username or "", full_name or "", is_wl, today))
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            elif row["last_date"] != today:
                conn.execute("UPDATE users SET daily_count = 0, last_date = ? WHERE user_id = ?", (today, user_id))
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row)

    def update_user(self, user_id: int, **kwargs):
        if not kwargs:
            return
        if "level" in kwargs and kwargs["level"] in ("C1", "C2"):
            kwargs["english_only"] = 1
        cols = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        vals = list(kwargs.values()) + [user_id]
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
            conn.commit()

    def set_whitelist(self, target_id: int, status: int = 1):
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO users (user_id, is_whitelisted) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_whitelisted = excluded.is_whitelisted
            """, (target_id, status))
            conn.commit()

    def list_whitelisted(self) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT user_id, username, full_name, level, daily_count FROM users WHERE is_whitelisted = 1").fetchall()
            return [dict(r) for r in rows]

    def check_and_increment_quota(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        if user_id == ADMIN_ID:
            return True
        if u["daily_count"] >= u["daily_limit"]:
            return False
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
        return True

    # --- طابور التشبع السياقي الخفي (Stealth Input Flood) ---
    def fetch_stealth_targets(self, user_id: int, limit: int = 3) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("""
                SELECT id, chunk, register_tag, co_text_pattern 
                FROM lexical_vault 
                WHERE user_id = ? AND stealth_done < stealth_target
                ORDER BY RANDOM() LIMIT ?
            """, (user_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def increment_stealth_done(self, ids: List[int]):
        if not ids:
            return
        q = ",".join(["?"] * len(ids))
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE lexical_vault SET stealth_done = stealth_done + 1 WHERE id IN ({q})", ids)
            conn.commit()

    # --- الحفظ الذري والمفكرة والتكرار المتباعد (Atomic Save & SRS) ---
    def store_atomic_batch(
        self, user_id: int, token_key: str, items: List[Dict[str, Any]],
        audio_clean_text: str = "", user_voice_file_id: str = ""
    ):
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM atomic_buffer WHERE created_at < datetime('now', '-1 day')")
            for idx, item in enumerate(items, start=1):
                conn.execute("""
                    INSERT INTO atomic_buffer (user_id, token_key, item_idx, payload_json, audio_clean_text, user_voice_file_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, token_key, idx, json.dumps(item, ensure_ascii=False), audio_clean_text, user_voice_file_id))
            conn.commit()

    def get_atomic_meta(self, token_key: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT audio_clean_text, user_voice_file_id, payload_json FROM atomic_buffer WHERE token_key = ? LIMIT 1",
                (token_key,)
            ).fetchone()
            return dict(row) if row else None

    def save_atomic_item_to_vault(self, user_id: int, token_key: str, item_idx: int) -> Optional[str]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            row = conn.execute("""
                SELECT payload_json FROM atomic_buffer 
                WHERE user_id = ? AND token_key = ? AND item_idx = ?
            """, (user_id, token_key, item_idx)).fetchone()
            if not row:
                return None
            item = json.loads(row["payload_json"])
            chunk_str = item.get("chunk", "").strip()
            # منع التكرار لنفس المستخدم
            exists = conn.execute(
                "SELECT id FROM lexical_vault WHERE user_id = ? AND LOWER(chunk) = LOWER(?)",
                (user_id, chunk_str)
            ).fetchone()
            if exists:
                return f"{chunk_str} (موجود مسبقاً)"

            conn.execute("""
                INSERT INTO lexical_vault (
                    user_id, chunk, register_tag, trigger_context, co_text_pattern,
                    typical_followup, example_sentence, cloze_sentence, explanation, next_review_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, chunk_str, item.get("register_tag", "[🎙️ Articulate]"),
                item.get("trigger", ""), item.get("pattern", ""),
                item.get("followup", ""), item.get("example", ""),
                item.get("cloze", ""), item.get("explanation", ""), today
            ))
            conn.commit()
            return chunk_str

    def get_due_srs_card(self, user_id: int) -> Optional[Dict[str, Any]]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM lexical_vault 
                WHERE user_id = ? AND next_review_date <= ?
                ORDER BY next_review_date ASC, id ASC LIMIT 1
            """, (user_id, today)).fetchone()
            return dict(row) if row else None

    def get_srs_card_by_id(self, card_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM lexical_vault WHERE id = ?", (card_id,)).fetchone()
            return dict(row) if row else None

    def grade_srs_card(self, card_id: int, grade: int):
        with self._lock, self._conn() as conn:
            card = conn.execute("SELECT * FROM lexical_vault WHERE id = ?", (card_id,)).fetchone()
            if not card:
                return
            ef = card["easiness_factor"]
            reps = card["repetitions"]
            interval = card["interval_days"]

            if grade >= 3:
                if reps == 0:
                    interval = 1
                elif reps == 1:
                    interval = 4
                else:
                    interval = max(1, round(interval * ef))
                reps += 1
            else:
                reps = 0
                interval = 1

            ef = max(1.3, ef + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
            next_rev = (datetime.utcnow() + timedelta(days=interval)).strftime("%Y-%m-%d")
            conn.execute("""
                UPDATE lexical_vault 
                SET easiness_factor = ?, interval_days = ?, repetitions = ?, next_review_date = ?
                WHERE id = ?
            """, (ef, interval, reps, next_rev, card_id))
            conn.commit()

    def get_user_vault(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lexical_vault WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- سجل الأخطاء والذاكرة ---
    def record_diagnostics(self, user_id: int, diagnostics: List[Dict[str, Any]]):
        with self._lock, self._conn() as conn:
            for d in diagnostics:
                fix = d.get("native_fix", "").strip()
                if not fix:
                    continue
                existing = conn.execute(
                    "SELECT id FROM error_profile WHERE user_id = ? AND LOWER(native_fix) = LOWER(?) AND mastered = 0",
                    (user_id, fix)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE error_profile SET frequency = frequency + 1, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                        (existing["id"],)
                    )
                else:
                    conn.execute("""
                        INSERT INTO error_profile (user_id, category, user_said, native_fix, why_note)
                        VALUES (?, ?, ?, ?, ?)
                    """, (user_id, d.get("category", "General"), d.get("user_said", ""), fix, d.get("why_note", "")))
            conn.commit()

    def get_weaknesses(self, user_id: int, limit: int = 6) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM error_profile 
                WHERE user_id = ? AND mastered = 0 
                ORDER BY frequency DESC, last_seen DESC LIMIT ?
            """, (user_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def mark_weakness_mastered(self, err_id: int):
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE error_profile SET mastered = 1 WHERE id = ?", (err_id,))
            conn.commit()

    def append_history(self, user_id: int, role: str, content: str, max_len: int = 14):
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content[:1500]))
            conn.execute("""
                DELETE FROM chat_history WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
            """, (user_id, user_id, max_len))
            conn.commit()

    def fetch_history(self, user_id: int) -> List[Dict[str, str]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id ASC", (user_id,)).fetchall()
            return [dict(r) for r in rows]

    def reset_history(self, user_id: int):
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            conn.commit()

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            total_vault = conn.execute("SELECT COUNT(*) FROM lexical_vault WHERE user_id = ?", (user_id,)).fetchone()[0]
            due_srs = conn.execute("SELECT COUNT(*) FROM lexical_vault WHERE user_id = ? AND next_review_date <= ?", (user_id, today)).fetchone()[0]
            active_err = conn.execute("SELECT COUNT(*) FROM error_profile WHERE user_id = ? AND mastered = 0", (user_id,)).fetchone()[0]
            mastered_err = conn.execute("SELECT COUNT(*) FROM error_profile WHERE user_id = ? AND mastered = 1", (user_id,)).fetchone()[0]
            return {
                "total_vault": total_vault,
                "due_srs": due_srs,
                "active_errors": active_err,
                "mastered_errors": mastered_err
            }

    # --- البحث المعجمي الفوري في قواعد المتلازمات (Fast SQLite Lookup) ---
    def lookup_collocations(self, query_word: str, limit: int = 8) -> List[str]:
        results = []
        q_clean = query_word.strip()
        if not q_clean:
            return results
        # 1. البحث في قاعدة المتلازمات الخارجية إن وجدت
        if os.path.exists(CORPUS_DB_PATH):
            try:
                with sqlite3.connect(CORPUS_DB_PATH) as c_conn:
                    tables = [r[0] for r in c_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    for tbl in tables[:2]:
                        cols = [c[1] for c in c_conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
                        text_cols = [c for c in cols if "id" not in c.lower()]
                        if text_cols:
                            where_clause = " OR ".join([f"{col} LIKE ?" for col in text_cols])
                            params = [f"%{q_clean}%"] * len(text_cols) + [limit]
                            rows = c_conn.execute(f"SELECT * FROM {tbl} WHERE {where_clause} LIMIT ?", params).fetchall()
                            for r in rows:
                                results.append(" | ".join([str(val) for val in r if val is not None]))
                            if results:
                                break
            except Exception as e:
                logger.warning(f"Corpus DB lookup warning: {e}")

        # 2. البحث في المفكرة الذرية المحلية
        with self._lock, self._conn() as conn:
            local_rows = conn.execute(
                "SELECT chunk, register_tag, co_text_pattern, example_sentence FROM lexical_vault WHERE chunk LIKE ? OR example_sentence LIKE ? LIMIT 5",
                (f"%{q_clean}%", f"%{q_clean}%")
            ).fetchall()
            for r in local_rows:
                results.append(f"⭐ {r['chunk']} {r['register_tag']} — {r['example_sentence']}")
        return results[:limit]

    # --- النسخ الاحتياطي والاستعادة التلقائية عبر الرسالة المثبتة في تيليجرام (Relay Sync) ---
    def backup_to_telegram(self, bot: telebot.TeleBot, caption_note: str = "🔄 Auto-Relay Backup"):
        if not ADMIN_ID:
            return
        try:
            with self._lock, self._conn() as conn:
                conn.execute("PRAGMA wal_checkpoint(FULL);")
            with open(self.db_path, "rb") as f:
                sent_msg = bot.send_document(
                    ADMIN_ID,
                    f,
                    visible_file_name="musa_nyc_lounge.db",
                    caption=f"🗽 <b>Musa's NYC Lounge DB Backup</b>\n{caption_note}\n🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
                    parse_mode="HTML",
                    disable_notification=True
                )
            try:
                bot.pin_chat_message(ADMIN_ID, sent_msg.message_id, disable_notification=True)
            except Exception:
                pass
            logger.info("✅ Database successfully backed up and pinned to Admin Telegram.")
        except Exception as e:
            logger.error(f"Telegram backup error: {e}")

    def restore_from_telegram_pin(self, bot: telebot.TeleBot) -> bool:
        if not ADMIN_ID:
            return False
        try:
            chat = bot.get_chat(ADMIN_ID)
            pinned = getattr(chat, "pinned_message", None)
            if pinned and pinned.document and pinned.document.file_name.endswith(".db"):
                file_info = bot.get_file(pinned.document.file_id)
                downloaded = bot.download_file(file_info.file_path)
                with self._lock:
                    with open(self.db_path, "wb") as f:
                        f.write(downloaded)
                self.init_schema()
                logger.info("✅ Database automatically restored from Admin pinned message!")
                return True
        except Exception as e:
            logger.warning(f"Could not auto-restore from pinned message: {e}")
        return False

# =====================================================================
# 5. المحرك اللغوي والصوتي (Gemini 2.5 Native Multimodal Engine)
# =====================================================================
import asyncio
import csv
import html
import uuid
import edge_tts
from weasyprint import HTML as WeasyHTML


class NYCLoungeAI:
    def __init__(self, api_key: str, db: LoungeDatabase):
        self.api_key = api_key
        self.db = db
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    def _call_structured(self, prompt_parts: list, schema_cls, sys_instruction: str):
        """يستدعي النموذج الأساسي مع التبديل التلقائي للنموذج الاحتياطي عند الحاجة"""
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY غير مضبوط في متغيرات البيئة.")

        for model_name in (PRIMARY_MODEL, FALLBACK_MODEL):
            try:
                resp = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt_parts,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction,
                        response_mime_type="application/json",
                        response_schema=schema_cls,
                        temperature=0.75
                    )
                )
                if resp and resp.text:
                    return schema_cls.model_validate_json(resp.text)
            except Exception as e:
                logger.warning(f"Model {model_name} structured call warning: {e}")
        raise RuntimeError("تعذر الحصول على استجابة منظمة من Gemini API.")

    def _call_free_text(self, prompt_parts: list, sys_instruction: str) -> str:
        if not self.client:
            return "⚠️ GEMINI_API_KEY is missing."
        for model_name in (PRIMARY_MODEL, FALLBACK_MODEL):
            try:
                resp = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt_parts,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction,
                        temperature=0.7
                    )
                )
                if resp and resp.text:
                    return resp.text
            except Exception as e:
                logger.warning(f"Model {model_name} text call warning: {e}")
        return "⚠️ حدث خطأ مؤقت في الاتصال بمحرك الذكاء الاصطناعي."

    def process_lounge_turn(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> Tuple[LoungeTurnResponse, List[int]]:
        """
        يعالج رسالة المستخدم (نصية أو صوتية خام) وفق الوضع النشط،
        مع حقن القوالب الخفية (Stealth Flood) والمتلازمات المعجمية (RAG).
        """
        user_id = user["user_id"]
        level = user.get("level", "C1")
        eng_only = bool(user.get("english_only", 1))
        pref_reg = user.get("preferred_register", "articulate")
        mode = user.get("current_mode", "spontaneous_chat")

        # 1. جلب أهداف الدمج الخفي (Stealth Input Flood)
        stealth_targets = self.db.fetch_stealth_targets(user_id, limit=2)
        stealth_ids = [t["id"] for t in stealth_targets]
        stealth_str = ", ".join([f"'{t['chunk']}' ({t['register_tag']})" for t in stealth_targets])

        # 2. جلب نقاط الضعف المتكررة للحذر منها
        weaknesses = self.db.get_weaknesses(user_id, limit=3)
        weak_str = "; ".join([f"Avoid '{w['user_said']}' -> Use '{w['native_fix']}'" for w in weaknesses])

        # 3. بحث RAG سريع في قاعدة المتلازمات إذا كان النص متوفراً
        rag_hits = []
        if user_text:
            words = [w.strip(".,!?\"'") for w in user_text.split() if len(w) > 4]
            for w in words[:2]:
                rag_hits.extend(self.db.lookup_collocations(w, limit=2))
        rag_context = "\n".join(rag_hits[:4]) if rag_hits else "None"

        # 4. جلب سياق الحوار الأخير
        history = self.db.fetch_history(user_id)
        hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history[-8:]])

        # 5. تخصيص توجيه الوضع النشط (Active Mode Directive)
        mode_directives = {
            "spontaneous_chat": "Engage as a sharp, witty NYC friend in unscripted conversation. React to their point genuinely, push the topic forward, and keep the vibe effortless.",
            "storytelling_gym": "Act as an NYC Narrative & Stand-up Storytelling Coach. Help them use Historical Present ('So I walk in...'), dramatic pauses, bait-and-switch setups, and punchy delivery.",
            "register_lab": "Focus intensely on Register Shifting. Show how their exact thought shifts across [🎙️ Articulate], [☕ Casual], and [🎭 Witty] registers, highlighting subtext.",
            "subtext_decoder": "Decode the pragmatic subtext, irony, or dry humor behind the input, and teach how an American reads between the lines or replies with equal wit.",
            "situation_gambit": "The user is describing a social situation or feeling. Give them the exact Native American Conversational Gambits for that exact moment.",
            "weakness_drill": "Test and coach the user specifically on their recurring error profile and forced lexical output.",
            "shadowing_drill": "Analyze the user's vocal prosody, Flap T, Schwa reduction, thought chunking, and intonation contour with extreme precision."
        }

        lang_rule = (
            "STRICT ENGLISH-ONLY IMMERSION: Explain everything 100% in natural English. NEVER use Arabic."
            if eng_only or level in ("C1", "C2")
            else "You may use brief Arabic hints only inside diagnostic notes if helpful for B1/B2 clarity."
        )

        sys_instruction = f"""
{NYC_LOUNGE_SYSTEM_CORE}

USER PROFILE:
- Proficiency Level: {level}
- Preferred Vibe: {REGISTERS_INFO.get(pref_reg, REGISTERS_INFO['articulate'])}
- Language Rule: {lang_rule}
- Current Lounge Mode: {mode} -> {mode_directives.get(mode, mode_directives['spontaneous_chat'])}

STEALTH INPUT FLOOD (INCIDENTAL ACQUISITION):
If possible, naturally weave 1 or 2 of these saved chunks into your `lounge_reply` (italicize them subtly with <i>...</i> when used):
[{stealth_str if stealth_str else 'No active stealth targets'}]

USER RECURRING WEAKNESSES TO MONITOR:
[{weak_str if weak_str else 'No recorded weaknesses yet'}]

CORPUS COLLOCATION HINTS:
{rag_context}

RECENT CONVERSATION CONTEXT:
{hist_str if hist_str else 'Start of new session.'}
"""

        contents = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                "Listen carefully to the user's raw voice note. "
                "1) Transcribe what they said accurately. "
                "2) Evaluate their American pronunciation, rhythm, word stress, Flap-T, vowel reductions (Schwa), and linking in `acoustic_prosody_xray` (include phonetic respelling like 'Did-ja eat yet?'). "
                "3) Respond naturally in `lounge_reply` and fill all 3 register rewrites and 3 lexical upgrades."
            )
        else:
            contents.append(
                f"User input: \"{user_text}\"\n"
                "Analyze their phrasing, unnatural L1 interference, and register. "
                "In `acoustic_prosody_xray`, show how an American native would vocally link and stress the best rewrite (with phonetic respelling)."
            )

        result: LoungeTurnResponse = self._call_structured(contents, LoungeTurnResponse, sys_instruction)
        return result, stealth_ids

    def generate_podcast_episode(self, user: Dict[str, Any], custom_topic: str = "") -> PodcastEpisodeSchema:
        """يؤلف حلقة بودكاست نيويوركية بين Andrew و Ava تدمج وتناقش قوالب مفكرة المستخدم وأخطاءه"""
        user_id = user["user_id"]
        vault_items = self.db.get_user_vault(user_id, limit=6)
        weaknesses = self.db.get_weaknesses(user_id, limit=3)

        target_chunks = [v["chunk"] for v in vault_items[:4]]
        weak_fixes = [w["native_fix"] for w in weaknesses[:2]]
        all_targets = target_chunks + weak_fixes

        topic_prompt = custom_topic.strip() if custom_topic.strip() else "An ironic observation about everyday modern life, human habits, or a quirky NYC story"

        sys_instruction = f"""
{NYC_LOUNGE_SYSTEM_CORE}
You are producing an authentic dual-host mini-podcast episode at "Musa's NYC Lounge".
Hosts:
- `male_host` (Andrew): Witty, observant, relaxed storyteller.
- `female_host` (Ava): Sharp, articulate, loves unpacking why certain expressions hit differently.

RULES FOR THE SCRIPT:
1. Topic: {topic_prompt}
2. Target Expressions to weave into the conversation: {', '.join(all_targets) if all_targets else 'Use 4 high-level natural American idioms/phrasal verbs'}.
3. Mid-dialogue Meta-Moments: Twice during the conversation, have one host briefly pause the story ("Wait, I love how you phrased that...") to unpack the exact pragmatic vibe/subtext of a target expression before resuming the banter naturally.
4. Keep lines punchy and conversational (12 to 16 turns total). Zero corporate speak!
"""
        prompt = ["Generate the complete Dual-Host NYC Lounge Podcast episode now."]
        return self._call_structured(prompt, PodcastEpisodeSchema, sys_instruction)

    def mine_text_and_subtext(self, user: Dict[str, Any], raw_text: str) -> LoungeTurnResponse:
        """مُشرّح النصوص الأصلية: يستخرج أثمن القوالب المعجمية والنبرة الخفية مع إتاحة حفظها الذري"""
        sys_instruction = f"""
{NYC_LOUNGE_SYSTEM_CORE}
You are in 'Native Text & Subtext Miner' mode.
The user sent an English passage, quote, or dialogue.
1. In `lounge_reply`: Give a sharp breakdown of the passage's register, pragmatic tone, subtext/irony, and why it works (or where it feels stiff).
2. In `rewrite_articulate`, `rewrite_casual`, `rewrite_witty`: Show how the core message of this text sounds across the 3 registers.
3. In `lexical_upgrades`: Extract the 3 most valuable lexical chunks/idioms/structures from the text (or upgrades for it) using the 3-Part Lexical Frame so the user can save them atomically.
"""
        return self._call_structured([f"Analyze and mine this text:\n\n{raw_text}"], LoungeTurnResponse, sys_instruction)

    def build_active_drill(self, user: Dict[str, Any], drill_type: str) -> Dict[str, str]:
        """
        يولد تمريناً نشطاً:
        - forced_output: تحدي دمج 3 قوالب في رسالة صوتية أو نصية واحدة.
        - shadowing: جملة غنية بالكلام المتصل الأمريكي لمحاكاتها صوتياً.
        - micro_dictation: جملة سريعة مدمجة لاختبار أذن المستخدم في فك شفرة الكلام المتصل.
        - diagnostic_test: اختبار تحديد مستوى تكيفي سريع.
        """
        user_id = user["user_id"]
        vault = self.db.get_user_vault(user_id, limit=5)
        weaknesses = self.db.get_weaknesses(user_id, limit=4)
        chunks_list = [v["chunk"] for v in vault[:3]] + [w["native_fix"] for w in weaknesses[:2]]
        chunks_str = ", ".join(chunks_list[:3]) if chunks_list else "cut somebody some slack, play it by ear, wind up"

        if drill_type == "forced_output":
            prompt = f"""
Create a punchy 'Forced Output Challenge' for a {user['level']} speaker at Musa's NYC Lounge.
Target expressions they MUST use in a single voice note or message: [{chunks_str}].
Give them a vivid, realistic everyday scenario (2 sentences max) and challenge them to react to it using all 3 expressions naturally.
Return ONLY the challenge text formatted cleanly with emojis.
"""
            text = self._call_free_text([prompt], NYC_LOUNGE_SYSTEM_CORE)
            return {"text": text, "tts_target": ""}

        elif drill_type == "shadowing":
            prompt = f"""
Create ONE natural, rhythmically rich Spoken American English sentence (20-28 words) inspired by NYC everyday storytelling (incorporating '{chunks_str}' if natural).
Format your response in two parts separated by '|||':
Part 1: The exact clean English sentence ONLY (to be synthesized into audio).
Part 2: The Acoustic Shadowing Guide showing Flap-T, Schwa reductions, word linking, stressed words in CAPS, and intonation arrows (↘ / ↗).
"""
            raw = self._call_free_text([prompt], NYC_LOUNGE_SYSTEM_CORE)
            parts = raw.split("|||")
            clean_sent = parts[0].strip().strip('"')
            guide = parts[1].strip() if len(parts) > 1 else "Listen closely to the rhythm and record your voice mimicking the exact speed and linking!"
            return {"text": f"🎙️ <b>Shadowing Target:</b>\n<code>{html.escape(clean_sent)}</code>\n\n🔬 <b>Acoustic Guide:</b>\n{html.escape(guide)}", "tts_target": clean_sent}

        elif drill_type == "micro_dictation":
            prompt = """
Write ONE fast, natural everyday American sentence full of connected speech reductions (like 'Did you end up going?' -> 'Jeet', 'gonna', 'wound up', 'oughta', 'coulda').
Return ONLY the clean English sentence, nothing else (10 to 16 words).
"""
            clean_sent = self._call_free_text([prompt], NYC_LOUNGE_SYSTEM_CORE).strip().strip('"')
            return {
                "text": "👂 <b>Micro-Dictation (Bottom-Up Listening Decoding)</b>\n\nاستمع جيداً للمقطع الصوتي المرفق بالسرعة الأمريكية الطبيعية، واكتب الجملة الكاملة التي سمعتها ليكشف لك البوت الاختزالات الصوتية الخفية فيها:",
                "tts_target": clean_sent
            }

        else:  # diagnostic_test
            prompt = """
Create a brief, engaging 2-part 'NYC Lounge Adaptive Diagnostic Challenge' to calibrate a user's spoken/written English level (B1 to C2):
1. A tricky everyday social situation: ask how they'd respond naturally.
2. A quick storytelling prompt: ask them to send a 20-second voice note or short paragraph about a minor frustration or funny moment this week.
Keep it warm, sharp, and concise.
"""
            text = self._call_free_text([prompt], NYC_LOUNGE_SYSTEM_CORE)
            return {"text": text, "tts_target": ""}


# =====================================================================
# 6. محرك الصوتيات، المرآة الصوتية FFmpeg، البودكاست، والتصدير
# =====================================================================
class LoungeMediaEngine:
    @staticmethod
    def _run_async(coro):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    @classmethod
    def synthesize_us_speech(cls, text: str, voice_key: str = "male_host", rate: str = "+0%") -> Optional[str]:
        """يولد ملف صوتي أمريكي طبيعي عبر Edge-TTS ويحوله إلى OGG Opus متوافق مع بصمة تيليجرام الصوتية"""
        voice_name = US_VOICES.get(voice_key, US_VOICES["male_host"])
        uid = uuid.uuid4().hex[:8]
        raw_mp3 = os.path.join(TEMP_DIR, f"tts_{uid}.mp3")
        out_ogg = os.path.join(TEMP_DIR, f"voice_{uid}.ogg")

        async def _gen():
            comm = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
            await comm.save(raw_mp3)

        try:
            cls._run_async(_gen())
            subprocess.run(
                ["ffmpeg", "-y", "-i", raw_mp3, "-c:a", "libopus", "-b:a", "48k", "-vbr", "on", out_ogg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            if os.path.exists(raw_mp3):
                os.remove(raw_mp3)
            return out_ogg
        except Exception as e:
            logger.error(f"Edge-TTS synthesis error: {e}")
            return raw_mp3 if os.path.exists(raw_mp3) else None

    @classmethod
    def build_acoustic_mirror(cls, user_ogg_path: str, native_clean_text: str) -> Optional[str]:
        """
        المرآة الصوتية المدمجة عبر FFmpeg:
        [صوت المستخدم الأصلي] + [نغمة فاصلة 0.35 ثانية] + [النطق الأمريكي بالسرعة الطبيعية] + [النطق الأمريكي بسرعة -15% للتركيز على الكلام المتصل]
        """
        uid = uuid.uuid4().hex[:8]
        native_norm_mp3 = os.path.join(TEMP_DIR, f"mirror_norm_{uid}.mp3")
        native_slow_mp3 = os.path.join(TEMP_DIR, f"mirror_slow_{uid}.mp3")
        beep_wav = os.path.join(TEMP_DIR, f"beep_{uid}.wav")
        final_mirror_ogg = os.path.join(TEMP_DIR, f"acoustic_mirror_{uid}.ogg")

        async def _gen_both():
            c1 = edge_tts.Communicate(text=native_clean_text, voice=US_VOICES["male_host"], rate="+0%")
            c2 = edge_tts.Communicate(text=native_clean_text, voice=US_VOICES["male_host"], rate="-16%")
            await c1.save(native_norm_mp3)
            await c2.save(native_slow_mp3)

        try:
            cls._run_async(_gen_both())
            # توليد نغمة فاصلة ناعمة (Sine beep 660Hz لمدة 0.25 ثانية متبوعة بصمت قصير)
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=0.22",
                "-af", "afade=t=in:st=0:d=0.05,afade=t=out:st=0.15:d=0.07,apad=pad_dur=0.35",
                "-ar", "24000", "-ac", "1", beep_wav
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            # دمج المقاطع الأربعة: صوت المستخدم -> نغمة -> أمريكي طبيعي -> نغمة -> أمريكي بطيء للتدقيق
            filter_complex = (
                "[0:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[u];"
                "[1:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[b1];"
                "[2:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[n1];"
                "[1:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[b2];"
                "[3:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[n2];"
                "[u][b1][n1][b2][n2]concat=n=5:v=0:a=1[out]"
            )
            subprocess.run([
                "ffmpeg", "-y",
                "-i", user_ogg_path,
                "-i", beep_wav,
                "-i", native_norm_mp3,
                "-i", native_slow_mp3,
                "-filter_complex", filter_complex,
                "-map", "[out]", "-c:a", "libopus", "-b:a", "48k", final_mirror_ogg
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            for f in (native_norm_mp3, native_slow_mp3, beep_wav):
                if os.path.exists(f):
                    os.remove(f)
            return final_mirror_ogg
        except Exception as e:
            logger.error(f"Acoustic Mirror build error: {e}")
            return cls.synthesize_us_speech(native_clean_text)

    @classmethod
    def produce_dual_host_podcast(cls, ep: PodcastEpisodeSchema) -> Optional[str]:
        """يدمج حوار Andrew و Ava عبر FFmpeg في ملف بودكاست MP3 واحد متدفق"""
        uid = uuid.uuid4().hex[:8]
        segment_files = []
        concat_list_path = os.path.join(TEMP_DIR, f"pod_list_{uid}.txt")
        final_mp3 = os.path.join(TEMP_DIR, f"NYC_Lounge_Podcast_{uid}.mp3")

        try:
            for idx, turn in enumerate(ep.script):
                seg_path = os.path.join(TEMP_DIR, f"pod_seg_{uid}_{idx}.mp3")
                voice_name = US_VOICES.get(turn.speaker, US_VOICES["male_host"])

                async def _save_seg(t=turn.text, v=voice_name, p=seg_path):
                    comm = edge_tts.Communicate(text=t, voice=v, rate="+2%")
                    await comm.save(p)

                cls._run_async(_save_seg())
                if os.path.exists(seg_path):
                    segment_files.append(seg_path)

            if not segment_files:
                return None

            with open(concat_list_path, "w", encoding="utf-8") as lf:
                for seg in segment_files:
                    lf.write(f"file '{os.path.abspath(seg)}'\n")

            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list_path, "-c:a", "libmp3lame", "-q:a", "3", final_mp3
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            return final_mp3 if os.path.exists(final_mp3) else None
        except Exception as e:
            logger.error(f"Podcast production error: {e}")
            return None
        finally:
            for seg in segment_files:
                if os.path.exists(seg):
                    os.remove(seg)
            if os.path.exists(concat_list_path):
                os.remove(concat_list_path)

    @staticmethod
    def export_vault_pdf(user: Dict[str, Any], vault_items: List[Dict[str, Any]], stats: Dict[str, Any]) -> Optional[str]:
        """يولد تقريراً مرئياً أنيقاً بصيغة PDF عبر WeasyPrint يضم الإطارات المعجمية الثلاثية"""
        uid = uuid.uuid4().hex[:6]
        pdf_path = os.path.join(TEMP_DIR, f"Musa_NYC_Lounge_Vault_{uid}.pdf")

        cards_html = ""
        for idx, item in enumerate(vault_items, start=1):
            cards_html += f"""
            <div class="card">
                <div class="header">
                    <span class="chunk">{idx}. {html.escape(item['chunk'])}</span>
                    <span class="badge">{html.escape(item['register_tag'])}</span>
                </div>
                <p class="explain">{html.escape(item['explanation'])}</p>
                <div class="frame-grid">
                    <div><b>🎯 Trigger:</b> {html.escape(item['trigger_context'])}</div>
                    <div><b>🔗 Pattern & Co-text:</b> {html.escape(item['co_text_pattern'])}</div>
                    <div><b>💬 Typical Follow-up:</b> {html.escape(item['typical_followup'])}</div>
                </div>
                <div class="example"><b>Example:</b> "{html.escape(item['example_sentence'])}"</div>
            </div>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{ size: A4; margin: 18mm; }}
                body {{ font-family: 'DejaVu Sans', sans-serif; color: #1e293b; background: #ffffff; line-height: 1.5; }}
                .banner {{ background: #0f172a; color: #f8fafc; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
                .banner h1 {{ margin: 0 0 6px 0; font-size: 22px; color: #38bdf8; }}
                .banner p {{ margin: 0; font-size: 12px; color: #cbd5e1; }}
                .card {{ border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px; margin-bottom: 14px; page-break-inside: avoid; background: #f8fafc; }}
                .header {{ display: flex; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 8px; }}
                .chunk {{ font-size: 16px; font-weight: bold; color: #0f172a; }}
                .badge {{ font-size: 11px; color: #0284c7; font-weight: bold; float: right; }}
                .explain {{ font-size: 12.5px; color: #334155; margin: 6px 0; }}
                .frame-grid {{ font-size: 11.5px; background: #ffffff; padding: 8px; border-radius: 6px; border: 1px solid #e2e8f0; margin-bottom: 8px; }}
                .frame-grid div {{ margin-bottom: 4px; }}
                .example {{ font-size: 12px; font-style: italic; color: #0369a1; }}
            </style>
        </head>
        <body>
            <div class="banner">
                <h1>🗽 Musa's NYC Lounge — Lexical Frames Vault</h1>
                <p>Member: {html.escape(user.get('full_name') or 'NYC Lounge Member')} | Level: {html.escape(user.get('level', 'C1'))} | Total Saved Chunks: {stats.get('total_vault', 0)} | Exported: {datetime.utcnow().strftime('%Y-%m-%d')}</p>
            </div>
            {cards_html if cards_html else '<p>No saved chunks in your vault yet.</p>'}
        </body>
        </html>
        """
        try:
            WeasyHTML(string=html_content).write_pdf(pdf_path)
            return pdf_path if os.path.exists(pdf_path) else None
        except Exception as e:
            logger.error(f"WeasyPrint PDF error: {e}")
            return None

    @staticmethod
    def export_vault_anki_csv(vault_items: List[Dict[str, Any]]) -> Optional[str]:
        """يصدر المفكرة إلى ملف CSV جاهز للاستيراد الفوري في Anki بنظام ملء الفراغ والإطار الثلاثي"""
        uid = uuid.uuid4().hex[:6]
        csv_path = os.path.join(TEMP_DIR, f"Musa_NYC_Anki_Deck_{uid}.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                for item in vault_items:
                    cloze_q = item.get("cloze_sentence") or item.get("example_sentence", "").replace(item["chunk"], "______")
                    front = (
                        f"<b>Complete the NYC Chunk {html.escape(item['register_tag'])}:</b><br><br>"
                        f"\"{html.escape(cloze_q)}\"<br><br>"
                        f"<small>🎯 Trigger: {html.escape(item['trigger_context'])}</small>"
                    )
                    back = (
                        f"<b>{html.escape(item['chunk'])}</b> {html.escape(item['register_tag'])}<hr>"
                        f"<b>Meaning:</b> {html.escape(item['explanation'])}<br>"
                        f"<b>🔗 Pattern:</b> {html.escape(item['co_text_pattern'])}<br>"
                        f"<b>💬 Typical Follow-up:</b> {html.escape(item['typical_followup'])}<br><br>"
                        f"<i>Full Example: \"{html.escape(item['example_sentence'])}\"</i>"
                    )
                    writer.writerow([front, back, "Musa_NYC_Lounge"])
            return csv_path if os.path.exists(csv_path) else None
        except Exception as e:
            logger.error(f"Anki CSV error: {e}")
            return None

    @staticmethod
    def cleanup_file(filepath: Optional[str]):
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass


# =====================================================================
# 7. منسق الرسائل التفاعلية والاقتباس القابل للطي (Expandable Blockquote UI)
# =====================================================================
def format_lounge_turn_html(res: LoungeTurnResponse, is_voice: bool = False) -> str:
    """
    يفصل انسيابية الحوار العفوي في الأعلى عن المجهر اللغوي والصوتي المطوي في الأسفل
    باستخدام <blockquote expandable> الخاص بتيليجرام.
    """
    top_section = ""
    if is_voice and res.transcription:
        top_section += f"🎙️ <i>\"{html.escape(res.transcription)}\"</i>\n\n"

    # الرد الطبيعي العفوي في الأعلى
    top_section += f"🗽 {res.lounge_reply}\n\n"

    # بناء القسم المطوي (المجهر اللغوي والصوتي + محول السجلات + الإطارات الثلاثية المرقمة للحفظ الذري)
    xray_lines = [
        "🔬 <b>NYC Linguistic & Acoustic X-Ray (اضغط للفتح)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎭 <b>Register Shifter (كيف يقولها الأمريكي بـ 3 نبرات):</b>",
        f"• <b>[🎙️ Articulate]:</b> <code>{html.escape(res.rewrite_articulate)}</code>",
        f"• <b>[☕ Casual]:</b> <code>{html.escape(res.rewrite_casual)}</code>",
        f"• <b>[🎭 Witty]:</b> <code>{html.escape(res.rewrite_witty)}</code>",
        "",
        f"🎧 <b>Connected Speech & US Prosody:</b>\n{html.escape(res.acoustic_prosody_xray)}"
    ]

    if res.diagnostics:
        xray_lines.append("\n🩺 <b>Surgical Diagnostics:</b>")
        for d in res.diagnostics[:3]:
            xray_lines.append(
                f"• ❌ <i>{html.escape(d.user_said)}</i> ➔ ✅ <b>{html.escape(d.native_fix)}</b>\n"
                f"  └ <code>[{html.escape(d.category)}]</code> {html.escape(d.why_note)}"
            )

    if res.lexical_upgrades:
        xray_lines.append("\n💎 <b>3-Part Lexical Frames (اضغط الزر المرقم بالأسفل للحفظ الذري):</b>")
        for idx, up in enumerate(res.lexical_upgrades[:3], start=1):
            xray_lines.append(
                f"\n<b>({idx}) {html.escape(up.chunk)}</b> {html.escape(up.register_tag)}\n"
                f"  • 🎯 <b>Trigger:</b> {html.escape(up.trigger)}\n"
                f"  • 🔗 <b>Pattern:</b> {html.escape(up.pattern)}\n"
                f"  • 💬 <b>Follow-up:</b> <i>\"{html.escape(up.followup)}\"</i>\n"
                f"  • 📝 <b>Example:</b> <i>\"{html.escape(up.example)}\"</i>"
            )

    expandable_body = "\n".join(xray_lines)
    return f"{top_section}<blockquote expandable>{expandable_body}</blockquote>"

# =====================================================================
# 8. مصنع لوحات الأزرار التفاعلية (Inline Keyboards Factory)
# =====================================================================
class LoungeKeyboards:
    @staticmethod
    def main_menu(user: Dict[str, Any], is_admin: bool = False) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        mode = user.get("current_mode", "spontaneous_chat")
        reg = user.get("preferred_register", "articulate")

        def _m(target, label):
            return f"✅ {label}" if mode == target else label

        kb.add(
            InlineKeyboardButton(_m("spontaneous_chat", "🗣️ دردشة نيويورك العفوية"), callback_data="mode:spontaneous_chat"),
            InlineKeyboardButton(_m("storytelling_gym", "🎭 نادي السرد والمفارقات"), callback_data="mode:storytelling_gym")
        )
        kb.add(
            InlineKeyboardButton(_m("register_lab", "🎛️ مختبر السجلات اللغوية"), callback_data="mode:register_lab"),
            InlineKeyboardButton(_m("subtext_decoder", "🕵️‍♂️ كاشف المعنى المبطن"), callback_data="mode:subtext_decoder")
        )
        kb.add(
            InlineKeyboardButton(_m("situation_gambit", "⚡ مُسعف المواقف اللحظية"), callback_data="mode:situation_gambit"),
            InlineKeyboardButton("🎙️ استوديو البودكاست", callback_data="menu:podcast")
        )
        kb.add(
            InlineKeyboardButton("🔬 مُشرّح النصوص والبحث", callback_data="menu:miner_lookup"),
            InlineKeyboardButton("🎯 التدريب النشط والصوتيات", callback_data="menu:drills")
        )
        kb.add(
            InlineKeyboardButton("🧠 المراجعة الذكية (SRS)", callback_data="srs:next"),
            InlineKeyboardButton("📓 مفكرتي والتصدير", callback_data="menu:vault")
        )
        kb.add(
            InlineKeyboardButton(f"🎚️ النبرة: {reg.capitalize()}", callback_data="action:cycle_register"),
            InlineKeyboardButton("⚙️ الإعدادات وإحصائياتي", callback_data="menu:settings")
        )
        kb.add(
            InlineKeyboardButton("🔄 تصفير ذاكرة الحوار", callback_data="action:clear_chat")
        )
        if is_admin:
            kb.add(InlineKeyboardButton("🛡️ لوحة المشرف والنسخ الاحتياطي", callback_data="menu:admin"))
        return kb

    @staticmethod
    def turn_action_bar(token_key: str, num_upgrades: int = 3, has_user_voice: bool = False) -> InlineKeyboardMarkup:
        """شريط الأزرار الذرية أسفل كل رد من البوت (نطق/مرآة صوتية + حفظ ذري مرقم + القائمة)"""
        kb = InlineKeyboardMarkup(row_width=3)
        audio_btns = [
            InlineKeyboardButton("🔊 نطق أمريكي", callback_data=f"tts:{token_key}")
        ]
        if has_user_voice:
            audio_btns.append(InlineKeyboardButton("🪞 المرآة الصوتية", callback_data=f"mirror:{token_key}"))
        kb.row(*audio_btns)

        save_btns = [
            InlineKeyboardButton(f"⭐ حفظ ({i})", callback_data=f"save:{token_key}:{i}")
            for i in range(1, min(num_upgrades, 3) + 1)
        ]
        if save_btns:
            kb.row(*save_btns)

        kb.row(
            InlineKeyboardButton("🎚️ تبديل النبرة", callback_data="action:cycle_register"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def podcast_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🎧 توليد حلقة بودكاست من مفكرتي وأخطائي", callback_data="pod:generate_auto"),
            InlineKeyboardButton("✍️ توليد حلقة حول موضوع أكتبه الآن", callback_data="mode:podcast_custom_topic"),
            InlineKeyboardButton("🎙️ وضع «أنت الضيف» (حوار إذاعي مباشر)", callback_data="mode:podcast_guest"),
            InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def miner_lookup_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("⚡ البحث الفوري في قاعدة المتلازمات (SQLite)", callback_data="mode:fast_lookup"),
            InlineKeyboardButton("🔬 مُشرّح النصوص الأصلية (Chunk & Subtext Miner)", callback_data="mode:chunk_miner"),
            InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def drills_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🔥 تحدي التوظيف الإجباري (Forced Output)", callback_data="drill:forced_output"),
            InlineKeyboardButton("🎙️ تدريب المحاكاة الصوتية (US Shadowing)", callback_data="drill:shadowing"),
            InlineKeyboardButton("👂 فك شفرة السمع السريع (Micro-Dictation)", callback_data="drill:micro_dictation"),
            InlineKeyboardButton("🩺 علاج نقاط ضعفي المتكررة", callback_data="drill:weakness_list"),
            InlineKeyboardButton("🧭 اختبار تحديد المستوى التكيّفي", callback_data="drill:diagnostic_test"),
            InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def srs_reveal_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("👁️ إظهار الإجابة والإطار الثلاثي", callback_data=f"srs:reveal:{card_id}"),
            InlineKeyboardButton("🔊 استماع", callback_data=f"srs:tts:{card_id}")
        )
        kb.row(InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:home"))
        return kb

    @staticmethod
    def srs_grade_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(
            InlineKeyboardButton("🔴 صعب (غداً)", callback_data=f"srs:grade:{card_id}:1"),
            InlineKeyboardButton("🟡 جيد", callback_data=f"srs:grade:{card_id}:3"),
            InlineKeyboardButton("🟢 سهل", callback_data=f"srs:grade:{card_id}:5")
        )
        kb.row(
            InlineKeyboardButton("🔊 نطق المثال", callback_data=f"srs:tts:{card_id}"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def vault_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📄 تصدير تقرير PDF", callback_data="export:pdf"),
            InlineKeyboardButton("🗂️ تصدير بطاقات Anki CSV", callback_data="export:anki")
        )
        kb.add(
            InlineKeyboardButton("🧠 بدء جلسة مراجعة SRS", callback_data="srs:next"),
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def settings_menu(user: Dict[str, Any]) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=4)
        cur_lvl = user.get("level", "C1")
        levels = ["B1", "B2", "C1", "C2"]
        kb.row(*[
            InlineKeyboardButton(f"{'✅ ' if cur_lvl == lv else ''}{lv}", callback_data=f"set:level:{lv}")
            for lv in levels
        ])
        eng_only = bool(user.get("english_only", 1))
        eng_label = "🔒 الانغماس الإنجليزي الكامل: مُفعّل" if eng_only else "🔓 شرح عربي مساند: مسموح"
        kb.row(InlineKeyboardButton(eng_label, callback_data="set:toggle_eng"))
        kb.row(InlineKeyboardButton("🧭 بدء اختبار تشخيص المستوى", callback_data="drill:diagnostic_test"))
        kb.row(InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu:home"))
        return kb

    @staticmethod
    def admin_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("💾 نسخ احتياطي فوري وتثبيت", callback_data="admin:backup_now"),
            InlineKeyboardButton("👥 قائمة الأصدقاء المسموحين", callback_data="admin:list_users")
        )
        kb.add(
            InlineKeyboardButton("➕ إضافة صديق بالـ ID", callback_data="mode:admin_add_user"),
            InlineKeyboardButton("➖ إزالة صديق بالـ ID", callback_data="mode:admin_del_user")
        )
        kb.add(InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu:home"))
        return kb


# =====================================================================
# 9. تهيئة الكائنات الرئيسية ومعالجات التيليجرام (Bot & Handlers)
# =====================================================================
db = LoungeDatabase(DB_PATH)
ai_engine = NYCLoungeAI(GEMINI_API_KEY, db)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4) if BOT_TOKEN else None


def is_authorized(user_id: int, username: str = "", full_name: str = "") -> bool:
    u = db.get_user(user_id, username, full_name)
    if user_id == ADMIN_ID or ADMIN_ID == 0:
        return True
    return bool(u.get("is_whitelisted", 0))


def build_welcome_banner(user: Dict[str, Any]) -> str:
    stats = db.get_user_stats(user["user_id"])
    mode_names = {
        "spontaneous_chat": "🗣️ دردشة نيويورك العفوية",
        "storytelling_gym": "🎭 نادي السرد والمفارقات",
        "register_lab": "🎛️ مختبر السجلات اللغوية",
        "subtext_decoder": "🕵️‍♂️ كاشف المعنى المبطن والتهكم",
        "situation_gambit": "⚡ مُسعف المواقف اللحظية",
        "fast_lookup": "⚡ البحث المعجمي الفوري (SQLite)",
        "chunk_miner": "🔬 مُشرّح النصوص الأصلية",
        "podcast_guest": "🎙️ استوديو البودكاست (أنت الضيف)",
        "podcast_custom_topic": "✍️ تأليف بودكاست مخصص"
    }
    cur_mode = mode_names.get(user.get("current_mode", "spontaneous_chat"), user.get("current_mode"))
    reg_badge = REGISTERS_INFO.get(user.get("preferred_register", "articulate"), "🎙️ Articulate")

    return (
        f"🗽 <b>Musa's NYC Lounge — 24/7 Spoken American Mastery</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Member:</b> {html.escape(user.get('full_name') or 'Friend')} | 🎓 <b>Level:</b> <code>{user.get('level', 'C1')}</code>\n"
        f"🎯 <b>Active Mode:</b> {cur_mode}\n"
        f"🎚️ <b>Register Vibe:</b> {reg_badge}\n"
        f"📊 <b>Vault:</b> <code>{stats['total_vault']}</code> chunks | 🧠 <b>Due SRS:</b> <code>{stats['due_srs']}</code> | 🩺 <b>Weaknesses:</b> <code>{stats['active_errors']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>أرسل رسالة صوتية أو نصية مباشرة للتفاعل، أو اختر أداة من الأزرار بالأسفل:</i>"
    )


if bot:
    @bot.message_handler(commands=["start", "menu", "lounge"])
    def cmd_start(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.reply_to(
                message,
                f"🔒 <b>Musa's NYC Lounge is a Private Club.</b>\n"
                f"أرسل معرّفك التالي لصديقك (Musa) لتفعيل حسابك:\n<code>{uid}</code>"
            )
            return

        user = db.get_user(uid, uname, fname)
        bot.send_message(
            message.chat.id,
            build_welcome_banner(user),
            reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
        )

    @bot.message_handler(content_types=["document"])
    def handle_document_restore(message):
        """يتيح للمشرف استعادة قاعدة البيانات فوراً بمجرد إرسال ملف .db للبوت"""
        uid = message.from_user.id
        if uid != ADMIN_ID:
            return
        doc = message.document
        if doc and doc.file_name and doc.file_name.endswith(".db"):
            status_msg = bot.reply_to(message, "⏳ <b>جاري استعادة قاعدة البيانات من الملف المرفق...</b>")
            try:
                file_info = bot.get_file(doc.file_id)
                downloaded = bot.download_file(file_info.file_path)
                with open(DB_PATH, "wb") as f:
                    f.write(downloaded)
                db.init_schema()
                bot.edit_message_text(
                    "✅ <b>تمت استعادة قاعدة البيانات وتحديث الجداول بنجاح!</b>",
                    message.chat.id, status_msg.message_id
                )
                db.backup_to_telegram(bot, caption_note="📌 Manual DB Restore Pin")
            except Exception as e:
                bot.edit_message_text(f"❌ فشل استعادة الملف: {e}", message.chat.id, status_msg.message_id)

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        uid = call.from_user.id
        uname = call.from_user.username or ""
        fname = call.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.answer_callback_query(call.id, "🔒 غير مصرح لك باستخدام البوت.", show_alert=True)
            return

        user = db.get_user(uid, uname, fname)
        data = call.data or ""

        try:
            # --- 1. التنقل بين القوائم ---
            if data == "menu:home":
                bot.edit_message_text(
                    build_welcome_banner(user),
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:podcast":
                bot.edit_message_text(
                    "🎙️ <b>Musa's NYC Lounge — Dual-Host Podcast Studio</b>\n\n"
                    "هنا يقوم المذيعان الأمريكيان (Andrew & Ava) بنقاش حيوي شيق يدمجان فيه قوالب مفكرتك وأخطاءك السابقة مع التوقف لشرح سياقها الصوتي والبلاغي:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.podcast_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:miner_lookup":
                bot.edit_message_text(
                    "🔬 <b>مُشرّح النصوص والبحث المعجمي الفوري</b>\n\n"
                    "• <b>البحث الفوري:</b> اكتب كلمة واحدة لاستخراج متلازماتها فوراً من قاعدة البيانات.\n"
                    "• <b>مُشرّح النصوص:</b> أرسل أي فقرة أو حوار إنجليزي يعجبك لاستخراج أثمن القوالب المعجمية والنبرة الخفية منه مع أزرار حفظ ذرية:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.miner_lookup_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:drills":
                bot.edit_message_text(
                    "🎯 <b>نادي التدريب النشط والصوتيات الأمريكية</b>\n\n"
                    "اختر نوع التحدي الذي تريد خوضه الآن:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.drills_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:vault":
                vault_items = db.get_user_vault(uid, limit=10)
                stats = db.get_user_stats(uid)
                preview = "\n".join([
                    f"{i}. <b>{html.escape(v['chunk'])}</b> {html.escape(v['register_tag'])}"
                    for i, v in enumerate(vault_items, 1)
                ]) if vault_items else "<i>مفكرتك فارغة حالياً. اضغط على أزرار [⭐ حفظ] أسفل الردود لإضافة القوالب!</i>"

                bot.edit_message_text(
                    f"📓 <b>مفكرتي الذرية والتصدير (Lexical Frames Vault)</b>\n"
                    f"إجمالي القوالب المحفوظة: <code>{stats['total_vault']}</code> | المستحق للمراجعة اليوم: <code>{stats['due_srs']}</code>\n\n"
                    f"<b>أحدث القوالب المضافة:</b>\n{preview}",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.vault_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:settings":
                stats = db.get_user_stats(uid)
                bot.edit_message_text(
                    f"⚙️ <b>الإعدادات ولوحة القياس (Telemetry)</b>\n\n"
                    f"• المستوى الحالي: <code>{user['level']}</code>\n"
                    f"• القوالب المتقنة في المفكرة: <code>{stats['total_vault']}</code>\n"
                    f"• نقاط الضعف قيد العلاج: <code>{stats['active_errors']}</code> | تم التغلب على: <code>{stats['mastered_errors']}</code>\n"
                    f"• استهلاك اليوم: <code>{user['daily_count']} / {user['daily_limit']}</code>\n\n"
                    f"<i>ملاحظة: اختيار المستوى C1 أو C2 يفعّل وضع الانغماس الإنجليزي الكامل تلقائياً:</i>",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:admin" and uid == ADMIN_ID:
                bot.edit_message_text(
                    "🛡️ <b>لوحة تحكم المشرف (Founder Admin Panel)</b>\n\n"
                    "يمكنك من هنا إدارة قائمة الأصدقاء، أو سحب نسخة احتياطية فورية وتثبيتها، أو إرسال أي ملف <code>.db</code> للمحادثة لاستعادته مباشرة:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.admin_menu()
                )
                bot.answer_callback_query(call.id)

            # --- 2. تغيير الأوضاع والنبرة ---
            elif data.startswith("mode:"):
                new_mode = data.split(":", 1)[1]
                db.update_user(uid, current_mode=new_mode)
                user = db.get_user(uid)

                mode_prompts = {
                    "spontaneous_chat": "🗣️ <b>وضع دردشة نيويورك العفوية مفعّل!</b>\nتحدث أو اكتب بحرية في أي موضوع؛ سيرد عليك البوت بعفوية في الأعلى ويضع المجهر اللغوي والصوتي مطوياً بالأسفل.",
                    "storytelling_gym": "🎭 <b>نادي السرد القصصي والمفارقات مفعّل!</b>\nاحكِ موقفاً حدث معك (صوتاً أو نصاً)؛ سيعلمك البوت كيف ترويه بالمضارع الدرامي (Historical Present) مع ضبط الإيقاع والـ Punchline.",
                    "register_lab": "🎛️ <b>مختبر السجلات اللغوية مفعّل!</b>\nأرسل أي جملة أو فكرة، ليحولها البوت فوراً عبر السجلات الثلاثة: [🎙️ Articulate] و [☕ Casual] و [🎭 Witty].",
                    "subtext_decoder": "🕵️‍♂️ <b>كاشف المعنى المبطن والتهكم مفعّل!</b>\nأرسل أي عبارة سمعتها أو موقفاً تريد الرد عليه بذكاء وسخرية هادئة (Understatement) ليشرح لك البوت ما بين السطور.",
                    "situation_gambit": "⚡ <b>مُسعف المواقف اللحظية مفعّل!</b>\nصف الموقف الاجتماعي أو الشعور الذي تريد إيصاله (حتى لو بالدارجة أو العربية)، ليعطيك البوت المفاتيح الحوارية الأمريكية (Gambits) الجاهزة لهذا الموقف.",
                    "fast_lookup": "⚡ <b>البحث المعجمي الفوري مفعّل!</b>\nأرسل الآن كلمة إنجليزية واحدة أو كلمتين للبحث السريع في قاعدة بيانات المتلازمات ومفكرتك.",
                    "chunk_miner": "🔬 <b>مُشرّح النصوص الأصلية مفعّل!</b>\nأرسل أي فقرة أو نص إنجليزي أعجبك ليستخرج البوت منه أثمن القوالب المعجمية مع أزرار حفظ ذرية.",
                    "podcast_custom_topic": "✍️ <b>تأليف بودكاست مخصص:</b>\nأرسل الآن عنوان الموضوع أو الفكرة التي تريد من (Andrew & Ava) مناقشتها في الحلقة مع دمج كلمات مفكرتك.",
                    "podcast_guest": "🎙️ <b>وضع «أنت الضيف» في استوديو البودكاست!</b>\nأرسل رسالة صوتية أو نصية لبدء الحوار الإذاعي المباشر مع المذيع النيويوركي.",
                    "admin_add_user": "➕ أرسل الآن الـ <b>Telegram ID</b> الخاص بصديقك لإضافته إلى القائمة البيضاء:",
                    "admin_del_user": "➖ أرسل الآن الـ <b>Telegram ID</b> لإزالته من القائمة البيضاء:"
                }
                bot.answer_callback_query(call.id, "✅ تم تفعيل الوضع!")
                bot.send_message(
                    call.message.chat.id,
                    mode_prompts.get(new_mode, f"✅ Mode set to: {new_mode}"),
                    reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                )

            elif data == "action:cycle_register":
                order = ["articulate", "casual", "witty"]
                cur = user.get("preferred_register", "articulate")
                nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "articulate"
                db.update_user(uid, preferred_register=nxt)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"🎚️ تم ضبط النبرة على: {nxt.upper()}")
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                except Exception:
                    pass

            elif data == "action:clear_chat":
                db.reset_history(uid)
                bot.answer_callback_query(call.id, "🔄 تم تصفير ذاكرة الجلسة الحالية بنجاح!", show_alert=True)

            # --- 3. الحفظ الذري والنطق الأمريكي والمرآة الصوتية ---
            elif data.startswith("save:"):
                _, token_key, idx_str = data.split(":")
                saved_chunk = db.save_atomic_item_to_vault(uid, token_key, int(idx_str))
                if saved_chunk:
                    bot.answer_callback_query(call.id, f"⭐ تم حفظ: {saved_chunk}", show_alert=False)
                else:
                    bot.answer_callback_query(call.id, "⚠️ انتهت صلاحية هذا الزر أو تم حفظه.", show_alert=True)

            elif data.startswith("tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("audio_clean_text"):
                    bot.answer_callback_query(call.id, "⚠️ النص الصوتي غير متوفر.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🎧 جاري توليد النطق الأمريكي الطبيعي...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_us_speech(meta["audio_clean_text"])
                if ogg_path:
                    with open(ogg_path, "rb") as vf:
                        bot.send_voice(
                            call.message.chat.id, vf,
                            caption=f"🇺🇸 <code>{html.escape(meta['audio_clean_text'])}</code>"
                        )
                    LoungeMediaEngine.cleanup_file(ogg_path)

            elif data.startswith("mirror:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("user_voice_file_id"):
                    bot.answer_callback_query(call.id, "⚠️ المرآة الصوتية متاحة للرسائل الصوتية فقط.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🪞 جاري دمج صوتك مع النطق الأمريكي عبر FFmpeg...")
                bot.send_chat_action(call.message.chat.id, "record_voice")

                user_ogg = os.path.join(TEMP_DIR, f"u_{token_key}.ogg")
                try:
                    f_info = bot.get_file(meta["user_voice_file_id"])
                    raw_voice = bot.download_file(f_info.file_path)
                    with open(user_ogg, "wb") as f:
                        f.write(raw_voice)
                    mirror_ogg = LoungeMediaEngine.build_acoustic_mirror(user_ogg, meta["audio_clean_text"])
                    if mirror_ogg:
                        with open(mirror_ogg, "rb") as mf:
                            bot.send_voice(
                                call.message.chat.id, mf,
                                caption=(
                                    "🪞 <b>Acoustic Mirror (Back-to-Back):</b>\n"
                                    "1️⃣ صوتك الأصلي ➔ 🔔 ➔ 2️⃣ النطق الأمريكي الطبيعي ➔ 🔔 ➔ 3️⃣ النطق الأمريكي البطيء (-16%)\n\n"
                                    f"🎯 <code>{html.escape(meta['audio_clean_text'])}</code>"
                                )
                            )
                        LoungeMediaEngine.cleanup_file(mirror_ogg)
                finally:
                    LoungeMediaEngine.cleanup_file(user_ogg)

            # --- 4. توليد البودكاست الحواري ---
            elif data == "pod:generate_auto":
                bot.answer_callback_query(call.id, "🎙️ جاري تأليف وتسجيل حلقة البودكاست...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "🎙️ <b>استوديو البودكاست يعمل الآن...</b>\n"
                    "1️⃣ جاري كتابة السيناريو الحواري بين <b>Andrew & Ava</b> ودمج قوالب مفكرتك...\n"
                    "2️⃣ سيتم دمج المقاطع الصوتية عبر <code>FFmpeg</code> وإرسال الحلقة خلال ثوانٍ."
                )
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ep = ai_engine.generate_podcast_episode(user)
                mp3_path = LoungeMediaEngine.produce_dual_host_podcast(ep)

                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in ep.key_takeaways]
                db.store_atomic_batch(uid, token_key, upgrades_dicts, audio_clean_text=ep.title)

                script_html = "\n\n".join([
                    f"<b>{'🧔‍♂️ Andrew' if line.speaker == 'male_host' else '👩🏼‍💼 Ava'}:</b> {html.escape(line.text)}"
                    for line in ep.script
                ])
                takeaways_html = "\n".join([
                    f"<b>({i}) {html.escape(u.chunk)}</b> {html.escape(u.register_tag)} — {html.escape(u.explanation)}"
                    for i, u in enumerate(ep.key_takeaways, 1)
                ])

                full_caption = (
                    f"🎙️ <b>{html.escape(ep.title)}</b>\n"
                    f"🎯 <b>Target Chunks:</b> <code>{html.escape(', '.join(ep.target_chunks_covered))}</code>\n\n"
                    f"<blockquote expandable>📜 <b>النص الكامل للحلقة والمفاتيح المعجمية (اضغط للفتح):</b>\n\n"
                    f"{script_html}\n\n"
                    f"💎 <b>Key Takeaways:</b>\n{takeaways_html}</blockquote>"
                )

                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            call.message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge (Andrew & Ava)",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    if len(full_caption) > 1024:
                        bot.send_message(
                            call.message.chat.id, full_caption,
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        call.message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                    )
                bot.delete_message(call.message.chat.id, wait_msg.message_id)

            # --- 5. التدريبات النشطة (Active Drills) ---
            elif data.startswith("drill:"):
                drill_type = data.split(":", 1)[1]
                if drill_type == "weakness_list":
                    weaknesses = db.get_weaknesses(uid, limit=6)
                    if not weaknesses:
                        bot.answer_callback_query(call.id, "🎉 سجل نقاط ضعفك نظيف تماماً!", show_alert=True)
                        return
                    lines = ["🩺 <b>سجل نقاط ضعفك المتكررة (Smart Error Profile):</b>\n"]
                    kb = InlineKeyboardMarkup(row_width=2)
                    for w in weaknesses:
                        lines.append(
                            f"• ❌ <i>{html.escape(w['user_said'])}</i> ➔ ✅ <b>{html.escape(w['native_fix'])}</b> "
                            f"(تكرر {w['frequency']}x)\n  └ {html.escape(w['why_note'])}"
                        )
                        kb.add(InlineKeyboardButton(f"✅ أتقنت: {w['native_fix'][:18]}", callback_data=f"master:{w['id']}"))
                    kb.add(InlineKeyboardButton("🔥 تحدي توظيف لعلاجها الآن", callback_data="drill:forced_output"))
                    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="menu:drills"))
                    bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=kb)
                    bot.answer_callback_query(call.id)
                    return

                bot.answer_callback_query(call.id, "🎯 جاري تجهيز التمرين...")
                mode_map = {
                    "forced_output": "weakness_drill",
                    "shadowing": "shadowing_drill",
                    "micro_dictation": "shadowing_drill",
                    "diagnostic_test": "spontaneous_chat"
                }
                db.update_user(uid, current_mode=mode_map.get(drill_type, "spontaneous_chat"))
                drill_data = ai_engine.build_active_drill(user, drill_type)
                bot.send_message(call.message.chat.id, drill_data["text"])

                if drill_data.get("tts_target"):
                    bot.send_chat_action(call.message.chat.id, "record_voice")
                    ogg_p = LoungeMediaEngine.synthesize_us_speech(drill_data["tts_target"])
                    if ogg_p:
                        with open(ogg_p, "rb") as vf:
                            bot.send_voice(call.message.chat.id, vf, caption="🎧 استمع جيداً ثم أرسل إجابتك أو تسجيلك الصوتي:")
                        LoungeMediaEngine.cleanup_file(ogg_p)

            elif data.startswith("master:"):
                err_id = int(data.split(":", 1)[1])
                db.mark_weakness_mastered(err_id)
                bot.answer_callback_query(call.id, "🎉 ممتاز! تمت إزالتها من نقاط الضعف النشطة.")

            # --- 6. نظام التكرار المتباعد الداخلي (In-Bot SRS) ---
            elif data == "srs:next":
                card = db.get_due_srs_card(uid)
                if not card:
                    bot.answer_callback_query(call.id, "🎉 راجعت كل البطاقات المستحقة لليوم!", show_alert=True)
                    return
                cloze = card.get("cloze_sentence") or card.get("example_sentence", "").replace(card["chunk"], "______")
                msg_text = (
                    f"🧠 <b>Spaced Repetition Review (SM-2)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎚️ <b>Register:</b> {html.escape(card['register_tag'])}\n"
                    f"🎯 <b>Trigger:</b> {html.escape(card['trigger_context'])}\n\n"
                    f"📝 <b>Complete the missing NYC chunk:</b>\n"
                    f"<i>\"{html.escape(cloze)}\"</i>"
                )
                bot.send_message(call.message.chat.id, msg_text, reply_markup=LoungeKeyboards.srs_reveal_kb(card["id"]))
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:reveal:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if not card:
                    bot.answer_callback_query(call.id, "⚠️ البطاقة غير موجودة.")
                    return
                ans_text = (
                    f"🧠 <b>SRS Answer Revealed:</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>{html.escape(card['chunk'])}</b> {html.escape(card['register_tag'])}\n"
                    f"• <b>Meaning:</b> {html.escape(card['explanation'])}\n"
                    f"• 🎯 <b>Trigger:</b> {html.escape(card['trigger_context'])}\n"
                    f"• 🔗 <b>Pattern:</b> {html.escape(card['co_text_pattern'])}\n"
                    f"• 💬 <b>Follow-up:</b> <i>\"{html.escape(card['typical_followup'])}\"</i>\n\n"
                    f"✅ <b>Full Example:</b>\n<i>\"{html.escape(card['example_sentence'])}\"</i>\n\n"
                    f"👇 <b>كيف كان تذكرك لهذا القالب؟</b>"
                )
                bot.edit_message_text(ans_text, call.message.chat.id, call.message.message_id, reply_markup=LoungeKeyboards.srs_grade_kb(card_id))
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:grade:"):
                _, _, card_id_str, grade_str = data.split(":")
                db.grade_srs_card(int(card_id_str), int(grade_str))
                bot.answer_callback_query(call.id, "✅ تم تحديث موعد المراجعة القادم!")
                # عرض البطاقة التالية تلقائياً
                nxt_card = db.get_due_srs_card(uid)
                if nxt_card:
                    cloze = nxt_card.get("cloze_sentence") or nxt_card.get("example_sentence", "").replace(nxt_card["chunk"], "______")
                    bot.edit_message_text(
                        f"🧠 <b>Next SRS Card:</b>\n"
                        f"🎚️ <b>Register:</b> {html.escape(nxt_card['register_tag'])}\n"
                        f"🎯 <b>Trigger:</b> {html.escape(nxt_card['trigger_context'])}\n\n"
                        f"<i>\"{html.escape(cloze)}\"</i>",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.srs_reveal_kb(nxt_card["id"])
                    )
                else:
                    bot.edit_message_text(
                        "🎉 <b>أحسنت! أنهيت جميع مراجعاتك المستحقة لليوم.</b>",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )

            elif data.startswith("srs:tts:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if card:
                    bot.answer_callback_query(call.id, "🔊 جاري توليد النطق...")
                    ogg_p = LoungeMediaEngine.synthesize_us_speech(card.get("example_sentence") or card["chunk"])
                    if ogg_p:
                        with open(ogg_p, "rb") as vf:
                            bot.send_voice(call.message.chat.id, vf, caption=f"🇺🇸 <b>{html.escape(card['chunk'])}</b>")
                        LoungeMediaEngine.cleanup_file(ogg_p)

            # --- 7. تصدير المفكرة (PDF & Anki CSV) ---
            elif data == "export:pdf":
                vault_items = db.get_user_vault(uid, limit=200)
                if not vault_items:
                    bot.answer_callback_query(call.id, "⚠️ مفكرتك فارغة حالياً.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "📄 جاري تصميم تقرير PDF عبر WeasyPrint...")
                stats = db.get_user_stats(uid)
                pdf_p = LoungeMediaEngine.export_vault_pdf(user, vault_items, stats)
                if pdf_p:
                    with open(pdf_p, "rb") as pf:
                        bot.send_document(
                            call.message.chat.id, pf,
                            caption="📄 <b>تقرير مفكرتك المعجمية (3-Part Lexical Frames PDF)</b>"
                        )
                    LoungeMediaEngine.cleanup_file(pdf_p)

            elif data == "export:anki":
                vault_items = db.get_user_vault(uid, limit=500)
                if not vault_items:
                    bot.answer_callback_query(call.id, "⚠️ مفكرتك فارغة حالياً.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🗂️ جاري تجهيز ملف Anki CSV...")
                csv_p = LoungeMediaEngine.export_vault_anki_csv(vault_items)
                if csv_p:
                    with open(csv_p, "rb") as cf:
                        bot.send_document(
                            call.message.chat.id, cf,
                            caption="🗂️ <b>ملف بطاقات Anki جاهز للاستيراد (Delimiter: Semicolon ;)</b>"
                        )
                    LoungeMediaEngine.cleanup_file(csv_p)

            # --- 8. الإعدادات ولوحة المشرف ---
            elif data.startswith("set:level:"):
                new_lv = data.split(":")[2]
                db.update_user(uid, level=new_lv)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"✅ تم تحديث المستوى إلى {new_lv}")
                bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )

            elif data == "set:toggle_eng":
                cur_eng = bool(user.get("english_only", 1))
                if user.get("level") in ("C1", "C2") and cur_eng:
                    bot.answer_callback_query(call.id, "🔒 الانغماس الإنجليزي إلزامي لمستويات C1/C2!", show_alert=True)
                    return
                db.update_user(uid, english_only=(0 if cur_eng else 1))
                user = db.get_user(uid)
                bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )
                bot.answer_callback_query(call.id, "✅ تم تحديث إعداد اللغة.")

            elif data == "admin:backup_now" and uid == ADMIN_ID:
                bot.answer_callback_query(call.id, "💾 جاري إرسال وتثبيت النسخة الاحتياطية...")
                db.backup_to_telegram(bot, caption_note="🛡️ Manual Admin Backup")

            elif data == "admin:list_users" and uid == ADMIN_ID:
                wl = db.list_whitelisted()
                txt = "👥 <b>الأصدقاء المسموح لهم في القائمة البيضاء:</b>\n\n" + "\n".join([
                    f"• <code>{u['user_id']}</code> — {html.escape(u['full_name'] or u['username'] or 'Member')} ({u['level']}) [اليوم: {u['daily_count']}]"
                    for u in wl
                ])
                bot.send_message(call.message.chat.id, txt, reply_markup=LoungeKeyboards.admin_menu())
                bot.answer_callback_query(call.id)

        except Exception as e:
            logger.error(f"Callback error ({data}): {e}")
            bot.answer_callback_query(call.id, "⚠️ حدث خطأ أثناء تنفيذ الأمر.")

    # =====================================================================
    # 10. معالج الرسائل الصوتية الخام (Raw Voice Handler -> Gemini 2.5)
    # =====================================================================
    @bot.message_handler(content_types=["voice", "audio"])
    def handle_voice_input(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.reply_to(message, f"🔒 Private Lounge. Your ID: <code>{uid}</code>")
            return

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "⚠️ وصلت للحد اليومي المسموح به من الرسائل.")
            return

        user = db.get_user(uid, uname, fname)
        status_msg = bot.reply_to(
            message,
            "🎧 <b>جاري الاستماع لبصمتك الصوتية وتحليل الكلام المتصل (Flap-T / Linking / Prosody)...</b>"
        )
        bot.send_chat_action(message.chat.id, "typing")

        try:
            voice_obj = message.voice or message.audio
            file_id = voice_obj.file_id
            file_info = bot.get_file(file_id)
            audio_bytes = bot.download_file(file_info.file_path)

            res, stealth_ids = ai_engine.process_lounge_turn(
                user=user,
                user_text="",
                audio_bytes=audio_bytes,
                audio_mime="audio/ogg"
            )

            # تحديث سجل الحوار، الأخطاء، وطابور التشبع الخفي
            db.append_history(uid, "user", f"[Voice]: {res.transcription}")
            db.append_history(uid, "assistant", res.lounge_reply)
            db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
            if stealth_ids:
                db.increment_stealth_done(stealth_ids)

            # تخزين الأزرار الذرية + معرف ملف صوت المستخدم لبناء المرآة الصوتية عند الطلب
            token_key = uuid.uuid4().hex[:10]
            upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
            db.store_atomic_batch(
                user_id=uid,
                token_key=token_key,
                items=upgrades_dicts,
                audio_clean_text=res.tts_clean_target or res.rewrite_articulate,
                user_voice_file_id=file_id
            )

            formatted_html = format_lounge_turn_html(res, is_voice=True)
            bot.edit_message_text(
                formatted_html,
                message.chat.id,
                status_msg.message_id,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=True
                )
            )
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            bot.edit_message_text(
                f"⚠️ حدث خطأ أثناء تحليل الرسالة الصوتية: <code>{html.escape(str(e)[:180])}</code>",
                message.chat.id, status_msg.message_id
            )

    # =====================================================================
    # 11. معالج الرسائل النصية الشامل والأوضاع الخاصة (Text Handler)
    # =====================================================================
    @bot.message_handler(content_types=["text"])
    def handle_text_input(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"
        text = (message.text or "").strip()

        if not text or text.startswith("/"):
            return

        if not is_authorized(uid, uname, fname):
            bot.reply_to(message, f"🔒 Private Lounge. Your ID: <code>{uid}</code>")
            return

        user = db.get_user(uid, uname, fname)
        mode = user.get("current_mode", "spontaneous_chat")

        # --- أ. أوامر المشرف السريعة (إضافة / حذف صديق بالـ ID) ---
        if uid == ADMIN_ID and mode in ("admin_add_user", "admin_del_user"):
            digits = "".join([c for c in text if c.isdigit()])
            if not digits:
                bot.reply_to(message, "⚠️ يرجى إرسال أرقام الـ ID فقط.")
                return
            target_id = int(digits)
            status_val = 1 if mode == "admin_add_user" else 0
            db.set_whitelist(target_id, status=status_val)
            db.update_user(uid, current_mode="spontaneous_chat")
            action_str = "إضافة" if status_val == 1 else "إزالة"
            bot.reply_to(
                message,
                f"✅ تمت {action_str} المستخدم <code>{target_id}</code> بنجاح، وعدتَ الآن لوضع الدردشة العفوية.",
                reply_markup=LoungeKeyboards.main_menu(db.get_user(uid), is_admin=True)
            )
            return

        # --- ب. البحث الفوري في قاعدة بيانات المتلازمات (Fast SQLite Lookup) ---
        if mode == "fast_lookup" and len(text.split()) <= 3:
            hits = db.lookup_collocations(text, limit=10)
            if hits:
                out = f"⚡ <b>نتائج البحث الفوري عن <code>{html.escape(text)}</code>:</b>\n\n" + "\n".join(
                    [f"• {html.escape(h)}" for h in hits]
                )
                bot.reply_to(message, out)
                return
            # إذا لم يجد في القاعدة المحلية يكمل تلقائياً للمحلل الذكي بالأسفل

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "⚠️ وصلت للحد اليومي المسموح به.")
            return

        # --- ج. توليد حلقة بودكاست بموضوع مخصص ---
        if mode == "podcast_custom_topic":
            wait_msg = bot.reply_to(
                message,
                f"🎙️ <b>جاري تأليف وتسجيل حلقة بودكاست حول:</b> <i>{html.escape(text)}</i>..."
            )
            try:
                db.update_user(uid, current_mode="spontaneous_chat")
                ep = ai_engine.generate_podcast_episode(user, custom_topic=text)
                mp3_path = LoungeMediaEngine.produce_dual_host_podcast(ep)

                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in ep.key_takeaways]
                db.store_atomic_batch(uid, token_key, upgrades_dicts, audio_clean_text=ep.title)

                script_html = "\n\n".join([
                    f"<b>{'🧔‍♂️ Andrew' if line.speaker == 'male_host' else '👩🏼‍💼 Ava'}:</b> {html.escape(line.text)}"
                    for line in ep.script
                ])
                full_caption = (
                    f"🎙️ <b>{html.escape(ep.title)}</b>\n\n"
                    f"<blockquote expandable>📜 <b>النص الكامل للحلقة (اضغط للفتح):</b>\n\n{script_html}</blockquote>"
                )
                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                    )
                bot.delete_message(message.chat.id, wait_msg.message_id)
            except Exception as e:
                bot.edit_message_text(f"⚠️ خطأ أثناء إنتاج البودكاست: {e}", message.chat.id, wait_msg.message_id)
            return

        # --- د. مُشرّح النصوص الأصلية أو الدردشة العفوية والمختبرات ---
        status_msg = bot.reply_to(message, "🗽 <b>جاري التحليل وصياغة البدائل الطبيعية...</b>")
        bot.send_chat_action(message.chat.id, "typing")

        try:
            if mode == "chunk_miner":
                res = ai_engine.mine_text_and_subtext(user, text)
                stealth_ids = []
            else:
                res, stealth_ids = ai_engine.process_lounge_turn(user=user, user_text=text)

            db.append_history(uid, "user", text)
            db.append_history(uid, "assistant", res.lounge_reply)
            db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
            if stealth_ids:
                db.increment_stealth_done(stealth_ids)

            token_key = uuid.uuid4().hex[:10]
            upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
            db.store_atomic_batch(
                user_id=uid,
                token_key=token_key,
                items=upgrades_dicts,
                audio_clean_text=res.tts_clean_target or res.rewrite_articulate
            )

            formatted_html = format_lounge_turn_html(res, is_voice=False)
            bot.edit_message_text(
                formatted_html,
                message.chat.id,
                status_msg.message_id,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=False
                )
            )
        except Exception as e:
            logger.error(f"Text turn error: {e}")
            bot.edit_message_text(
                f"⚠️ حدث خطأ أثناء المعالجة: <code>{html.escape(str(e)[:180])}</code>",
                message.chat.id, status_msg.message_id
            )


# =====================================================================
# 12. منظم دورة التناوب (5.3-Hour GitHub Relay Lifecycle Manager)
# =====================================================================
def relay_lifecycle_watchdog():
    """
    يراقب وقت تشغيل الحاوية على GitHub Actions:
    - يقوم بنسخ احتياطي دوري كل ساعتين.
    - عند الوصول إلى 5.3 ساعات (RELAY_MAX_RUNTIME_SECONDS)، يرسل نسخة احتياطية ختامية
      ويثبتها في محادثة المشرف ثم يغلق البوت بسلام لتستلم الدورة التالية.
    """
    last_periodic_backup = time.time()
    while True:
        time.sleep(25)
        elapsed = time.time() - START_TIME

        # نسخ احتياطي دوري كل ساعتين للأمان
        if time.time() - last_periodic_backup >= 7200 and bot:
            db.backup_to_telegram(bot, caption_note="⏱️ Periodic 2-Hour Checkpoint")
            last_periodic_backup = time.time()

        # الإغلاق الآمن عند انتهاء دورة الـ 5.3 ساعات
        if elapsed >= RELAY_MAX_RUNTIME_SECONDS:
            logger.info("⏳ 5.3-Hour Relay cycle reached. Performing final DB backup and graceful exit...")
            if bot:
                db.backup_to_telegram(bot, caption_note="🏁 End-of-Cycle Relay Handover")
                try:
                    bot.stop_polling()
                except Exception:
                    pass
            os._exit(0)


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is empty! Make sure secrets are passed to `env:` in your GitHub Workflow.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        logger.error("❌ GEMINI_API_KEY is empty! Make sure secrets are passed to `env:` in your GitHub Workflow.")
        sys.exit(1)

    logger.info("🗽 Starting Musa's NYC Lounge (24/7 Relay Architecture)...")

    # 1. استعادة قاعدة البيانات تلقائياً من آخر رسالة مثبتة في حساب المشرف
    restored = db.restore_from_telegram_pin(bot)
    if not restored:
        logger.info("ℹ️ Starting with fresh or local SQLite database.")

    # 2. تشغيل المراقب الزمني لدورة الـ 5.3 ساعات في الخلفية
    watchdog_thread = threading.Thread(target=relay_lifecycle_watchdog, daemon=True)
    watchdog_thread.start()

    # 3. بدء استقبال الرسائل بلا انقطاع
    logger.info("✅ Bot is live and polling!")
    bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=True)


if __name__ == "__main__":
    main()
