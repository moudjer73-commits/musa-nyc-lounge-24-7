#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, html, base64, shutil, sqlite3, hashlib, tempfile, threading, traceback, asyncio, subprocess, logging, random
from datetime import datetime, timezone

for _m, _p in [
    ("telebot", "pyTelegramBotAPI"), ("genai", "google-genai"), ("groq", "groq"),
    ("pydantic", "pydantic"), ("edge_tts", "edge-tts"), ("weasyprint", "weasyprint"),
    ("pypdf", "pypdf"), ("huggingface_hub", "huggingface_hub"),
]:
    try:
        if _m == "genai":
            from google import genai
        else:
            __import__(_m)
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _p])
        except Exception:
            pass

import telebot
from telebot import types
import edge_tts
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field
from google import genai
from google.genai import types as genai_types
from groq import Groq
from huggingface_hub import HfApi, hf_hub_download

try:
    import weasyprint
    HAS_WEASYPRINT = True
except Exception:
    HAS_WEASYPRINT = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except Exception:
    HAS_PYPDF = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("NYCLoungeStudio")

PRIMARY_OWNER_ID = int("8874707150")
HF_REPO_ID = "moudjer/musa-nyc-lounge-vault"
EMBEDDED_HF_REV_B64 = "==AWVd0YClmbM5UQsNVRJh3SRl0cotkY1pUR4hlRGd3QmNGZfZGa"
EMBEDDED_CFG_B64 = "fSJ0bHVhdi1lZ251b2wtY3luLWFzdW0vcmVqZHVvbSIgOiJPUEVSX0JEX0ZIIiAsIlhVR2NCaW5MTkFsU0VJeEtRSXNoS2J1SkV4WEZGd0NmY2RfZmgiIDoiTkVLT1RfRkgiICwiaHNhbGYtNS4zLWluaW1lZyIgOiJMRURPTV9JTklNRUdfREVJRklSRVYiICwiVUY3ZDF0YVVkNGR4SFlVRnhUNWRuQ05MWUYzYnlkR1dIV0VncWNpYVEyODJpODhWQ2p4NV9rc2csbE1rV2I5eERaSjFSMHEzd0RPMlZpN2RHWUYzYnlkR1dOVWhMMW1tZVJUYUsyYThDc1prOV9rc2ciIDoiU1lFS19RT1JHX0RFSUZJUkVWIiAsIkFNTEowcmMtRzctZERvMkxaWUEtNTQyM29EV0pjV0R6ME5NZTJyVEROZHhJNk5SOGJBLlFBLGd3X3FvX2hqQy02Qlk1bld4cEhTUFNaQ016REtSV2FYR1Z1NE91NEhHcVNMNk5SOGJBLlFBLFEzSzI4V1pESU9wV1ptZjhRaGJQZzVvS21wRUxEcHNZQU1BUHJ6NmF4c0xMNk5SOGJBLlFBLHdtandrWHEwWWZiTXJxTm02LWdqcENUQVV2d0lzZ2ExWmpHc0JKR1RneU1JNk5SOGJBLlFBIiA6IlNZRUtfSU5JTUVHX0RFSUZJUkVWIiAsIjR2UlU5OEZFRWllaXNVZVItZEU2UWtWLUw4RnhKV1A0RkFBOjI3MDYzODI2OTgiIDoiTkVLT1RfR1RfREVJRklSRVYiew=="

DB_PATH = "/tmp/nyc_lounge_studio.db"
os.makedirs("/tmp/tts_cache_hd", exist_ok=True)
os.makedirs("/tmp/pdf_exports", exist_ok=True)

def _bootstrap():
    if EMBEDDED_HF_REV_B64 and not os.environ.get("HF_TOKEN"):
        try:
            os.environ["HF_TOKEN"] = base64.b64decode(EMBEDDED_HF_REV_B64[::-1].encode("utf-8")).decode("utf-8").strip()
        except Exception:
            pass
    if EMBEDDED_CFG_B64:
        try:
            dec = json.loads(base64.b64decode(EMBEDDED_CFG_B64.encode("utf-8")).decode("utf-8")[::-1])
            for k, v in dec.items():
                if v and not os.environ.get(k):
                    os.environ[k] = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
        except Exception:
            pass

    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) < 4096:
        if os.path.exists("/content/musa_nyc_lounge_v8.db") and os.path.getsize("/content/musa_nyc_lounge_v8.db") >= 4096:
            with open("/content/musa_nyc_lounge_v8.db", "rb") as sf, open(DB_PATH, "wb") as df:
                df.write(sf.read())
        else:
            tok = os.environ.get("HF_TOKEN", "").strip()
            if tok:
                try:
                    dl = hf_hub_download(repo_id=HF_REPO_ID, filename="musa_nyc_lounge_v8.db", repo_type="dataset", token=tok)
                    with open(dl, "rb") as sf, open(DB_PATH, "wb") as df:
                        df.write(sf.read())
                except Exception:
                    pass
    try:
        if os.path.exists(DB_PATH):
            os.chmod(DB_PATH, 0o666)
    except Exception:
        pass

_bootstrap()

def _parse_key_list(env_names: List[str]) -> List[str]:
    out = []
    for en in env_names:
        raw = (os.environ.get(en) or "").strip()
        if not raw:
            continue
        if raw.startswith("["):
            try:
                arr = json.loads(raw)
                out.extend([str(x).strip() for x in arr if str(x).strip()])
                continue
            except Exception:
                raw = raw.strip("[]")
        for part in raw.split(","):
            cl = part.strip().strip("'").strip('"').strip()
            if cl and cl not in out:
                out.append(cl)
    return out

_tg_list = _parse_key_list(["VERIFIED_TG_TOKEN", "TELEGRAM_TOKEN"])
TELEGRAM_TOKEN = _tg_list[0] if _tg_list else ""
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True)

def _collect_gemini_keys() -> List[str]:
    return _parse_key_list(["VERIFIED_GEMINI_KEYS", "GEMINI_API_KEY", "GEMINI_KEYS"])

def _collect_groq_keys() -> List[str]:
    return _parse_key_list(["VERIFIED_GROQ_KEYS", "GROQ_API_KEY", "GROQ_KEYS"])

# ------------------------------------------------------------------------------
# قاعدة البيانات ونظام إدارة الأعضاء والحصص اليومية
# ------------------------------------------------------------------------------
_db_lock = threading.Lock()
_last_sync_ts = 0.0
_PROCESSED_MSG_IDS = set()
_BOT_USERNAME = ""

def db_connect() -> sqlite3.Connection:
    try:
        if os.path.exists(DB_PATH):
            os.chmod(DB_PATH, 0o666)
    except Exception:
        pass
    return sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _init_tables():
    with _db_lock, db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members_v17 (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                daily_limit INTEGER DEFAULT 15,
                used_today INTEGER DEFAULT 0,
                last_day TEXT DEFAULT '',
                total_used INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invites_v17 (
                code TEXT PRIMARY KEY,
                daily_limit INTEGER DEFAULT 15,
                created_at REAL DEFAULT 0,
                used_by INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_v15 (
                user_id INTEGER PRIMARY KEY,
                mode TEXT DEFAULT 'CHAT',
                register_level TEXT DEFAULT 'Professional',
                podcast_hosts INTEGER DEFAULT 3,
                sub_state TEXT DEFAULT '',
                history_json TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vault_v15 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 8874707150,
                chunk TEXT DEFAULT '',
                meaning_ar TEXT DEFAULT '',
                context_sentence TEXT DEFAULT '',
                srs_stage INTEGER DEFAULT 1,
                use_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dna_v15 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 8874707150,
                error_pattern TEXT DEFAULT '',
                native_fix TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards_v15 (
                tok TEXT PRIMARY KEY,
                user_id INTEGER,
                voice_json TEXT DEFAULT '[]',
                pdf_json TEXT DEFAULT '{}',
                vault_json TEXT DEFAULT '[]',
                ar_text TEXT DEFAULT '',
                source_text TEXT DEFAULT '',
                mined_so_far TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quiz_v15 (
                q_id TEXT PRIMARY KEY,
                user_id INTEGER,
                quiz_json TEXT DEFAULT '{}'
            )
        """)
        # تسجيل المالك بصلاحية غير محدودة دائماً
        conn.execute("""
            INSERT OR IGNORE INTO members_v17 (user_id, full_name, username, status, daily_limit, used_today, last_day, total_used)
            VALUES (?, 'Musa (Host)', 'owner', 'owner', -1, 0, ?, 0)
        """, (PRIMARY_OWNER_ID, _today_str()))
        conn.execute("UPDATE members_v17 SET status='owner', daily_limit=-1 WHERE user_id=?", (PRIMARY_OWNER_ID,))
        # تنظيف المدخلات القديمة المبتورة في الخزنة (مثل عنوان البودكاست المحفوظ بالخطأ سابقاً)
        conn.execute("DELETE FROM vault_v15 WHERE chunk LIKE 'Productivity Paradoxes:%'")
        conn.commit()

_init_tables()

def sync_db_async(force: bool = False):
    global _last_sync_ts
    if not force and (time.time() - _last_sync_ts < 60):
        return
    _last_sync_ts = time.time()
    hf_tok = os.environ.get("HF_TOKEN", "").strip()
    if not hf_tok or not os.path.exists(DB_PATH):
        return
    def _up():
        try:
            tmp = f"/tmp/db_sync_{int(time.time())}.db"
            with _db_lock:
                shutil.copyfile(DB_PATH, tmp)
            HfApi(token=hf_tok).upload_file(path_or_fileobj=tmp, path_in_repo="musa_nyc_lounge_v8.db", repo_id=HF_REPO_ID, repo_type="dataset")
            os.remove(tmp)
        except Exception:
            pass
    threading.Thread(target=_up, daemon=True).start()

# ------------------------------------------------------------------------------
# ضبط هوية البوت الرسمية وأوامره في تيليجرام (Telegram Profile & Commands API)
# ------------------------------------------------------------------------------
def sync_telegram_identity():
    global _BOT_USERNAME
    try:
        me = bot.get_me()
        _BOT_USERNAME = me.username or ""
    except Exception:
        pass
    try:
        bot.set_my_name("NYC English Lounge")
    except Exception:
        pass
    try:
        bot.set_my_short_description(
            "Private American English studio — live NYC conversation, C2 editing, contextual lexicon & multi-host podcasts."
        )
    except Exception:
        pass
    try:
        bot.set_my_description(
            "Welcome to the NYC English Lounge.\n\n"
            "An immersive private studio for mastering natural spoken American English, executive communication, and C2 academic prose.\n\n"
            "• Live Roundtable Conversation (Jake, Chloe & Marcus)\n"
            "• Deep Contextual Lexicon (EN ⇄ AR & Collocations)\n"
            "• Expression Miner, C2 Draft Surgery & Studio Podcasts"
        )
    except Exception:
        pass
    try:
        member_cmds = [
            types.BotCommand("chat", "Join the NYC Lounge table"),
            types.BotCommand("lexicon", "Contextual dictionary & collocations (EN/AR)"),
            types.BotCommand("study", "Analyze text, mine idioms, or polish a draft"),
            types.BotCommand("podcast", "Listen to a multi-host studio episode"),
            types.BotCommand("quiz", "Collocation & C2 rewrite drills"),
            types.BotCommand("vault", "Saved expressions & PDF/Anki export"),
            types.BotCommand("radar", "Your personal mistake patterns & fixes"),
            types.BotCommand("guide", "Studio user guide"),
        ]
        bot.set_my_commands(member_cmds, scope=types.BotCommandScopeDefault())
        owner_cmds = member_cmds + [types.BotCommand("admin", "Manage friends, daily quotas & invites")]
        bot.set_my_commands(owner_cmds, scope=types.BotCommandScopeChat(PRIMARY_OWNER_ID))
    except Exception:
        pass

# ------------------------------------------------------------------------------
# إدارة الأعضاء والحصص اليومية (Access Control & Quotas)
# ------------------------------------------------------------------------------
def get_or_register_member(user: Any) -> Dict[str, Any]:
    uid = int(getattr(user, "id", PRIMARY_OWNER_ID))
    fname = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip() or f"Member {uid}"
    uname = getattr(user, "username", "") or ""
    today = _today_str()
    with _db_lock, db_connect() as conn:
        row = conn.execute(
            "SELECT user_id, full_name, username, status, daily_limit, used_today, last_day, total_used FROM members_v17 WHERE user_id=?",
            (uid,),
        ).fetchone()
        if not row:
            st = "owner" if uid == PRIMARY_OWNER_ID else "pending"
            lim = -1 if uid == PRIMARY_OWNER_ID else 15
            conn.execute(
                "INSERT INTO members_v17 (user_id, full_name, username, status, daily_limit, used_today, last_day, total_used) VALUES (?, ?, ?, ?, ?, 0, ?, 0)",
                (uid, fname, uname, st, lim, today),
            )
            conn.commit()
            return {"user_id": uid, "name": fname, "username": uname, "status": st, "limit": lim, "used": 0, "total": 0}
        used_now = int(row[5] or 0)
        if row[6] != today:
            used_now = 0
            conn.execute("UPDATE members_v17 SET used_today=0, last_day=?, full_name=?, username=? WHERE user_id=?", (today, fname, uname, uid))
            conn.commit()
        return {
            "user_id": row[0],
            "name": fname or row[1],
            "username": uname or row[2],
            "status": "owner" if uid == PRIMARY_OWNER_ID else (row[3] or "pending"),
            "limit": -1 if uid == PRIMARY_OWNER_ID else int(row[4] if row[4] is not None else 15),
            "used": used_now,
            "total": int(row[7] or 0),
        }

def check_and_consume_quota(user: Any) -> Tuple[bool, str]:
    m = get_or_register_member(user)
    uid = m["user_id"]
    if uid == PRIMARY_OWNER_ID or m["status"] == "owner":
        return True, ""
    if m["status"] != "approved":
        return False, "not_approved"
    if m["limit"] == 0 or (m["limit"] > 0 and m["used"] >= m["limit"]):
        return False, f"limit_reached:{m['limit']}"
    with _db_lock, db_connect() as conn:
        conn.execute(
            "UPDATE members_v17 SET used_today = used_today + 1, total_used = total_used + 1, last_day = ? WHERE user_id=?",
            (_today_str(), uid),
        )
        conn.commit()
    return True, ""

def set_member_quota(target_uid: int, status: Optional[str] = None, daily_limit: Optional[int] = None, reset_today: bool = False):
    with _db_lock, db_connect() as conn:
        row = conn.execute("SELECT user_id FROM members_v17 WHERE user_id=?", (target_uid,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO members_v17 (user_id, full_name, status, daily_limit, used_today, last_day) VALUES (?, ?, ?, ?, 0, ?)",
                (target_uid, f"Friend {target_uid}", status or "approved", daily_limit if daily_limit is not None else 15, _today_str()),
            )
        else:
            if status is not None:
                conn.execute("UPDATE members_v17 SET status=? WHERE user_id=?", (status, target_uid))
            if daily_limit is not None:
                conn.execute("UPDATE members_v17 SET daily_limit=? WHERE user_id=?", (daily_limit, target_uid))
            if reset_today:
                conn.execute("UPDATE members_v17 SET used_today=0, last_day=? WHERE user_id=?", (_today_str(), target_uid))
        conn.commit()
    sync_db_async(force=True)

def create_invite_code(limit_val: int) -> str:
    code = "inv_" + hashlib.md5(f"{time.time()}:{random.random()}".encode()).hexdigest()[:10]
    with _db_lock, db_connect() as conn:
        conn.execute("INSERT INTO invites_v17 (code, daily_limit, created_at, used_by) VALUES (?, ?, ?, 0)", (code, limit_val, time.time()))
        conn.commit()
    return code

def redeem_invite_code(code: str, user: Any) -> Optional[int]:
    with _db_lock, db_connect() as conn:
        row = conn.execute("SELECT daily_limit, used_by FROM invites_v17 WHERE code=?", (code,)).fetchone()
        if not row or (row[1] and row[1] != int(user.id)):
            return None
        lim = int(row[0] or 15)
        conn.execute("UPDATE invites_v17 SET used_by=? WHERE code=?", (int(user.id), code))
        conn.commit()
    get_or_register_member(user)
    set_member_quota(int(user.id), status="approved", daily_limit=lim, reset_today=True)
    return lim

# ------------------------------------------------------------------------------
# الجلسات والذاكرة والخزنة
# ------------------------------------------------------------------------------
def _get_session(user_id: int) -> Dict[str, Any]:
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT mode, register_level, podcast_hosts, sub_state, history_json FROM session_v15 WHERE user_id=?", (user_id,)).fetchone()
            if row:
                raw_h = json.loads(row[4] or "[]")
                clean_h = [x for x in raw_h if isinstance(x, dict)] if isinstance(raw_h, list) else []
                return {"mode": row[0] or "CHAT", "register": row[1] or "Professional", "podcast_hosts": int(row[2] or 3), "sub_state": row[3] or "", "history": clean_h}
    except Exception:
        pass
    return {"mode": "CHAT", "register": "Professional", "podcast_hosts": 3, "sub_state": "", "history": []}

def _update_session(user_id: int, mode: Optional[str] = None, register: Optional[str] = None, podcast_hosts: Optional[int] = None, sub_state: Optional[str] = None, new_turn: Optional[Dict[str, str]] = None, clear_hist: bool = False):
    try:
        s = _get_session(user_id)
        m_v = mode if mode else s["mode"]
        r_v = register if register else s["register"]
        p_v = podcast_hosts if podcast_hosts is not None else s["podcast_hosts"]
        sub_v = sub_state if sub_state is not None else s["sub_state"]
        h_v = [] if clear_hist else s["history"]
        if isinstance(new_turn, dict):
            h_v.append(new_turn)
            h_v = h_v[-8:]
        with _db_lock, db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_v15 (user_id, mode, register_level, podcast_hosts, sub_state, history_json) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, m_v, r_v, p_v, sub_v, json.dumps(h_v, ensure_ascii=False)),
            )
            conn.commit()
    except Exception:
        pass

def store_card(tok: str, user_id: int, voice_segs: Any, pdf_dict: Any, vault_items: Any, ar_text: str = "", source_text: str = "", mined_so_far: str = ""):
    try:
        with _db_lock, db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cards_v15 (tok, user_id, voice_json, pdf_json, vault_json, ar_text, source_text, mined_so_far) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tok, user_id, json.dumps(voice_segs), json.dumps(pdf_dict), json.dumps(vault_items), ar_text or "", source_text or "", mined_so_far or ""),
            )
            conn.commit()
    except Exception:
        pass

def load_card(tok: str) -> Optional[Dict[str, Any]]:
    try:
        with db_connect() as conn:
            r = conn.execute("SELECT user_id, voice_json, pdf_json, vault_json, ar_text, source_text, mined_so_far FROM cards_v15 WHERE tok=?", (tok,)).fetchone()
            if r:
                return {"user_id": r[0], "voice_segs": json.loads(r[1]), "pdf_dict": json.loads(r[2]), "vault_items": json.loads(r[3]), "ar_text": r[4], "source_text": r[5], "mined_so_far": r[6]}
    except Exception:
        pass
    return None

def _clean_vault_entry(raw_term: str, raw_def: str, raw_ctx: str) -> Tuple[str, str, str]:
    term = re.sub(r"^[•\-\*\d\.\)\s]+", "", (raw_term or "").strip())
    if "—" in term:
        pts = term.split("—", 1)
        term, raw_def = pts[0].strip(), (pts[1].strip() + " " + (raw_def or "")).strip()
    elif " - " in term:
        pts = term.split(" - ", 1)
        term, raw_def = pts[0].strip(), (pts[1].strip() + " " + (raw_def or "")).strip()
    elif ":" in term and len(term.split(":", 1)[0].split()) <= 6:
        pts = term.split(":", 1)
        term, raw_def = pts[0].strip(), (pts[1].strip() + " " + (raw_def or "")).strip()
    term = term.strip(" '\"\u201c\u201d*")
    return term[:120], (raw_def or "").strip()[:400], (raw_ctx or "").strip()[:450]

def _save_vault_item(user_id: int, chunk: str, meaning_note: str, ctx: str) -> bool:
    try:
        term, m_note, c_sent = _clean_vault_entry(chunk, meaning_note, ctx)
        if len(term) < 2 or len(term.split()) > 12:
            return False
        with _db_lock, db_connect() as conn:
            ex = conn.execute("SELECT id FROM vault_v15 WHERE user_id=? AND lower(chunk)=lower(?)", (user_id, term)).fetchone()
            if ex:
                conn.execute("UPDATE vault_v15 SET srs_stage = MIN(3, srs_stage + 1), use_count = use_count + 1 WHERE id=?", (ex[0],))
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO vault_v15 (user_id, chunk, meaning_ar, context_sentence, srs_stage, use_count) VALUES (?, ?, ?, ?, 1, 1)",
                (user_id, term, m_note, c_sent),
            )
            conn.commit()
        return True
    except Exception:
        return False

def _get_recent_vault_terms(user_id: int, limit: int = 4) -> List[str]:
    try:
        with db_connect() as conn:
            rows = conn.execute("SELECT chunk FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
            return [r[0] for r in rows if r[0]]
    except Exception:
        return []

def _log_dna(user_id: int, corr_lines: str):
    try:
        with _db_lock, db_connect() as conn:
            for ln in re.split(r"[\n|]+", corr_lines or "")[:5]:
                if "->" in ln or "→" in ln:
                    pts = re.split(r"->|→", re.sub(r"<[^>]+>", "", ln), maxsplit=1)
                    w, r = pts[0].strip("•~*- "), pts[1].strip()
                    if w and r and len(w) > 2:
                        conn.execute("INSERT INTO dna_v15 (user_id, error_pattern, native_fix) VALUES (?, ?, ?)", (user_id, w[:120], r[:120]))
            conn.commit()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# محرك الذكاء الاصطناعي الموثق والسريع
# ------------------------------------------------------------------------------
_PINNED_GROQ: Optional[Tuple[str, str]] = None
_PINNED_GEMINI: Optional[Tuple[str, str]] = None

def _discover_groq_models(client: Groq) -> List[str]:
    cands = []
    try:
        for m in getattr(client.models.list(), "data", []):
            mid = getattr(m, "id", "") or ""
            low = mid.lower()
            if not any(x in low for x in ("whisper", "guard", "tts", "playai", "orpheus", "embed", "vision", "allam", "deepseek")):
                cands.append(mid)
    except Exception:
        pass
    cands.sort(key=lambda x: 1 if "llama-3.3-70b" in x.lower() or "llama-4" in x.lower() else (2 if "70b" in x.lower() else 4))
    return cands[:5] if cands else ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

def _discover_gemini_models(client: genai.Client) -> List[str]:
    cands = []
    try:
        for m in client.models.list():
            sn = (getattr(m, "name", "") or "").replace("models/", "")
            if "gemini" in sn.lower() and not any(x in sn.lower() for x in ("embedding", "imagen", "veo", "aqa", "tts", "audio", "learnlm")):
                cands.append(sn)
    except Exception:
        pass
    cands.sort(key=lambda x: 1 if "2.5-flash" in x else (2 if "2.0-flash" in x else 4))
    return cands[:5] if cands else ["gemini-2.5-flash", "gemini-2.0-flash"]

def _is_populated(obj: Any) -> bool:
    d = obj.model_dump() if hasattr(obj, "model_dump") else {}
    for col in ("dialogue_en", "core_definition_en", "core_output_en", "script_en", "scenario_en", "transformed_text_en", "new_expressions_en"):
        if col in d:
            return len(str(d.get(col) or "").strip()) > 5
    return True

def _parse_schema_safe(raw_txt: str, schema_cls: Any) -> Any:
    t = re.sub(r"<think>.*?</think>", "", (raw_txt or "").strip(), flags=re.DOTALL).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    res = None
    try:
        res = schema_cls.model_validate_json(t)
    except Exception:
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if m:
            d = json.loads(m.group(0))
            clean_d = {}
            for k, v in d.items():
                if isinstance(v, list):
                    clean_d[k] = "\n".join([str(i) for i in v])
                elif isinstance(v, dict):
                    clean_d[k] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, bool):
                    clean_d[k] = v
                elif v is not None:
                    clean_d[k] = str(v)
            res = schema_cls(**clean_d)
    if res is None or not _is_populated(res):
        raise ValueError("Unpopulated schema")
    return res

def call_llm_structured(sys_p: str, usr_p: str, schema_cls: Any, temp: float = 0.6) -> Any:
    global _PINNED_GROQ, _PINNED_GEMINI
    f_names = list(schema_cls.model_fields.keys())
    f_desc = "\n".join([f'- "{k}": {v.description}' for k, v in schema_cls.model_fields.items()])
    j_inst = (
        f"\n\nReturn strictly a valid flat JSON object with ALL of these exact keys filled out completely:\n{f_desc}\n"
        f"Required keys: {json.dumps(f_names)}. Every string value must be a single non-empty string."
    )

    if _PINNED_GROQ:
        gk, gm = _PINNED_GROQ
        try:
            cc = Groq(api_key=gk, timeout=16.0).chat.completions.create(
                model=gm, messages=[{"role": "system", "content": sys_p + j_inst}, {"role": "user", "content": usr_p}],
                response_format={"type": "json_object"}, temperature=temp,
            )
            return _parse_schema_safe(cc.choices[0].message.content, schema_cls)
        except Exception:
            _PINNED_GROQ = None

    if _PINNED_GEMINI:
        mk, gm_id = _PINNED_GEMINI
        try:
            mc = genai.Client(api_key=mk)
            resp = mc.models.generate_content(
                model=gm_id, contents=usr_p,
                config=genai_types.GenerateContentConfig(
                    system_instruction=sys_p + j_inst,
                    response_mime_type="application/json",
                    temperature=temp,
                ),
            )
            if resp and resp.text:
                return _parse_schema_safe(resp.text, schema_cls)
        except Exception:
            _PINNED_GEMINI = None

    for gk in _collect_groq_keys():
        try:
            gc = Groq(api_key=gk, timeout=16.0)
            for gm in _discover_groq_models(gc):
                try:
                    cc = gc.chat.completions.create(
                        model=gm, messages=[{"role": "system", "content": sys_p + j_inst}, {"role": "user", "content": usr_p}],
                        response_format={"type": "json_object"}, temperature=temp,
                    )
                    res = _parse_schema_safe(cc.choices[0].message.content, schema_cls)
                    _PINNED_GROQ = (gk, gm)
                    return res
                except Exception:
                    continue
        except Exception:
            continue

    for mk in _collect_gemini_keys():
        try:
            mc = genai.Client(api_key=mk)
            for gm_id in _discover_gemini_models(mc):
                try:
                    resp = mc.models.generate_content(
                        model=gm_id, contents=usr_p,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=sys_p + j_inst,
                            response_mime_type="application/json",
                            temperature=temp,
                        ),
                    )
                    if resp and resp.text:
                        res = _parse_schema_safe(resp.text, schema_cls)
                        _PINNED_GEMINI = (mk, gm_id)
                        return res
                except Exception:
                    continue
        except Exception:
            continue

    return schema_cls()

# ------------------------------------------------------------------------------
# الصوتيات والـ PDF وتنسيق النصوص
# ------------------------------------------------------------------------------
STUDIO_VOICES = {
    "Jake": "en-US-AndrewMultilingualNeural",
    "Chloe": "en-US-AvaMultilingualNeural",
    "Marcus": "en-US-BrianMultilingualNeural",
}

def _h(v: Any) -> str:
    raw = html.unescape(str(v if v is not None else "")).strip()
    return html.escape(raw, quote=False)

def _clean_multiline_bullets(raw: str) -> str:
    txt = html.unescape(str(raw or "")).strip()
    if not txt:
        return ""
    txt = re.sub(r"\s*\|\s*", "\n", txt)
    txt = re.sub(r"(?<!^)\s*(•|\u2022)\s*", "\n• ", txt)
    lines = [f"• {_h(ln.strip().lstrip('•-* '))}" for ln in txt.splitlines() if ln.strip()]
    return "\n".join(lines)

def _clean_tts(t: str) -> str:
    t = html.unescape(str(t or ""))
    # إزالة أي توجيهات مسرحية بين قوسين مثل (interrupting) أو (laughing) حتى لا يقرأها الصوت
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]+>|[\u0600-\u06FF]+", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7F’'\",.!?;:\-\s]+", " ", t)).strip()

def _split_dialogue_segments(raw_dialogue: str, default_speaker: str = "Jake") -> List[Tuple[str, str]]:
    segs = []
    norm = re.sub(r"(?:^|\n|\s)+(Jake|Chloe|Marcus)\s*:\s*", r"\n\1: ", str(raw_dialogue or "")).strip()
    for ln in norm.splitlines():
        m = re.match(r"^\s*(Jake|Chloe|Marcus)\s*:\s*(.+)$", ln.strip())
        if m:
            segs.append((m.group(1), m.group(2).strip()))
        elif ln.strip():
            segs.append((default_speaker, ln.strip()))
    return segs if segs else [(default_speaker, str(raw_dialogue or ""))]

def build_audio(segments: List[Tuple[str, str]], slow: bool = False) -> Optional[str]:
    rate = "-15%" if slow else "+0%"
    valid = [(s, _clean_tts(re.sub(r"^\s*(Jake|Chloe|Marcus)\s*:\s*", "", t))) for s, t in segments if len(_clean_tts(t)) > 2]
    if not valid:
        return None
    h_k = hashlib.md5(f"V17::{rate}::{'||'.join([f'{s}:{t}' for s,t in valid])}".encode()).hexdigest()
    cache_p = f"/tmp/tts_cache_hd/{h_k}.mp3"
    if os.path.exists(cache_p) and os.path.getsize(cache_p) > 800:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.close()
        shutil.copyfile(cache_p, tmp.name)
        return tmp.name
    parts, list_f, out_p = [], tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name, tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    try:
        for spk, txt in valid:
            v_name = STUDIO_VOICES.get(spk, "en-US-AndrewMultilingualNeural")
            p_raw = tempfile.NamedTemporaryFile(delete=False, suffix="_s.mp3").name
            async def _run():
                await edge_tts.Communicate(text=txt, voice=v_name, rate=rate).save(p_raw)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()
            if os.path.exists(p_raw) and os.path.getsize(p_raw) > 500:
                parts.append(p_raw)
        if not parts:
            return None
        with open(list_f, "w") as lf:
            for pf in parts:
                lf.write(f"file '{pf}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_f, "-ar", "48000", "-ac", "1", "-b:a", "128k", out_p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=35)
        for pf in parts + [list_f]:
            try:
                os.remove(pf)
            except Exception:
                pass
        if os.path.exists(out_p) and os.path.getsize(out_p) > 800:
            shutil.copyfile(out_p, cache_p)
            return out_p
    except Exception:
        pass
    return None

def transcribe_voice(v_bytes: bytes) -> str:
    raw_p = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg").name
    mp3_p = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    with open(raw_p, "wb") as f:
        f.write(v_bytes)
    try:
        subprocess.run(["ffmpeg", "-y", "-i", raw_p, "-ar", "16000", "-ac", "1", "-b:a", "48k", mp3_p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    except Exception:
        mp3_p = raw_p
    target = mp3_p if os.path.exists(mp3_p) and os.path.getsize(mp3_p) > 100 else raw_p
    for gk in _collect_groq_keys():
        try:
            with open(target, "rb") as af:
                tr = Groq(api_key=gk, timeout=15.0).audio.transcriptions.create(file=("v.mp3", af.read()), model="whisper-large-v3-turbo")
            if tr and getattr(tr, "text", "").strip():
                return tr.text.strip()
        except Exception:
            continue
    return ""

def generate_pdf_sheet(pdf_data: Dict[str, Any]) -> Optional[str]:
    if not HAS_WEASYPRINT:
        return None
    ar_block = f'<div class="sec"><div class="ar">{_h(pdf_data.get("ar_anchor", "")).replace(chr(10), "<br>")}</div></div>' if pdf_data.get("ar_anchor") else ""
    html_doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 15mm; }}
    body {{ font-family: 'DejaVu Sans', sans-serif; color: #0f172a; line-height: 1.55; font-size: 10pt; }}
    .hdr {{ background: #0f172a; color: #fff; padding: 14px; border-radius: 6px; margin-bottom: 12px; }}
    .sec {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; }}
    .ar {{ direction: rtl; text-align: right; background: #fffbeb; padding: 8px; border-right: 3px solid #d97706; margin-top: 6px; }}
    </style></head><body>
    <div class="hdr"><h2>NYC English Lounge — {_h(pdf_data.get('title', 'Study Dossier'))} ({_h(pdf_data.get('register', 'Professional'))})</h2></div>
    <div class="sec"><b>Core Content / Dialogue:</b><br>{_h(pdf_data.get('main_en', '')).replace(chr(10), '<br>')}<br><br><b>Refinements &amp; Notes:</b><br>{_h(pdf_data.get('instant_fix', '')).replace(chr(10), '<br>')}</div>
    <div class="sec"><b>Across Registers &amp; Expressions:</b><br>• Casual: {_h(pdf_data.get('spoken_inf', ''))}<br>• Professional: {_h(pdf_data.get('spoken_semi', ''))}<br>• Formal (C2): {_h(pdf_data.get('written_form', ''))}<br><br>{_h(pdf_data.get('diagnosis_en', '')).replace(chr(10), '<br>')}</div>
    {ar_block}
    </body></html>"""
    out_p = f"/tmp/pdf_exports/NYC_Lounge_{int(time.time())}.pdf"
    try:
        weasyprint.HTML(string=html_doc).write_pdf(out_p)
        return out_p if os.path.exists(out_p) else None
    except Exception:
        return None

# ------------------------------------------------------------------------------
# القوالب الرشيقة المعزولة (Lean Modular Schemas — 100% English + Deep Lexicon)
# ------------------------------------------------------------------------------
class ChatSchema(BaseModel):
    primary_speaker: str = Field(default="Chloe", description="'Jake', 'Chloe', or 'Marcus'. Must match whoever the user addressed!")
    dialogue_en: str = Field(default="", description="Vivid NYC dialogue reply (2-3 sentences from primary speaker, optionally followed on a new line by a sharp 1-sentence interjection from a second host like 'Jake: ...' or 'Marcus: ...'). NEVER include stage directions in parentheses like (interrupting)!")
    has_actual_errors: bool = Field(default=False, description="True if the USER's message has any grammar, uncountable noun ('informations/feedbacks'), verb-preposition ('discuss about', 'depend of', 'explain me'), or collocation ('take a fast decision', 'do a mistake') error.")
    error_corrections_list: str = Field(default="", description="Put each error from the USER's message on a separate line: 'wrong phrase -> right phrase'. Leave empty if user's English is error-free.")
    better_alternatives_list: str = Field(default="", description="1-2 native idiomatic upgrades for the USER's phrasing ('user simple phrase -> native upgrade').")
    recast_casual_en: str = Field(default="", description="How a native New Yorker naturally says the USER's point in Casual conversation")
    recast_professional_en: str = Field(default="", description="How an articulate professional expresses the USER's point tactfully at work")
    recast_formal_c2_en: str = Field(default="", description="C2 Executive/Academic phrasing (using nominalization, cleft structure, or hedging—NO 'Dear Name' letter headers!)")
    usage_and_subtext_note_en: str = Field(default="", description="1 concise note on pragmatic subtext, register fit, or spoken discourse flow (NO fake phonetic IPA rules!)")
    key_expressions_en: str = Field(default="", description="2 standout whole-chunk expressions from dialogue_en on separate lines: • **chunk 1** — meaning. 'Example.'\n• **chunk 2** — meaning. 'Example.'")
    active_challenge_chunk: str = Field(default="", description="1 specific target idiom or collocation (2-5 words) for the user to try using in their next reply")

class LexiconSchema(BaseModel):
    headword_or_query: str = Field(default="", description="The target English expression/word OR Arabic query")
    corpus_distribution_badge: str = Field(default="Spoken: High | Workplace: Very High | Academic: Moderate", description="Frequency across Spoken, Workplace, and Academic registers")
    syntactic_and_prosody_frame: str = Field(default="", description="Mandatory grammar pattern (e.g. 'hold off on + [noun/-ing]') + Semantic Prosody (Positive, Neutral, or Negative/Negative-Polarity only)")
    core_definition_en: str = Field(default="", description="Precise English definition, pragmatic subtext, and register boundary (where it works vs where it sounds wrong)")
    contextual_arabic_equivalents: str = Field(default="", description="2-3 eloquent, natural Arabic equivalents (الفصحى السياقية الرصينة) tailored by context—NO robotic literal word-for-word translations! If query was Arabic, list the categorized English equivalents (Everyday Idiomatic, Workplace Diplomatic, Academic C2) with Arabic nuances.")
    collocation_forks_en: str = Field(default="", description="Collocation forks on separate lines: • Verbs + term: ...\n• Adjectives/Nouns + term: ...\n• Opposite/Antonym: ...")
    authentic_examples_en: str = Field(default="", description="2 natural full-sentence examples (1 spoken/workplace, 1 executive/academic)")

class LexiconExtraSchema(BaseModel):
    title_en: str = Field(default="Lexicon Deep Dive", description="Title ('Word Family & Derivatives' or 'Extended Contextual Examples')")
    content_bullets_en: str = Field(default="", description="4 rich bullet points with forms/examples and concise Arabic contextual meanings")

class StudyMinerSchema(BaseModel):
    dossier_title_en: str = Field(default="Linguistic Dossier", description="Clean title")
    editorial_diagnosis_en: str = Field(default="", description="Direct answer, tone diagnosis, or 'wrong -> right' fixes. Never corrupt standard idioms like 'move the needle'!")
    core_output_en: str = Field(default="", description="Polished version of user's draft OR primary answer sentence")
    casual_version_en: str = Field(default="", description="Casual verb-driven spoken version")
    professional_version_en: str = Field(default="", description="Diplomatic workplace version (focus on shared timelines/solutions, never personal blame)")
    formal_c2_version_en: str = Field(default="", description="C2 Academic/Executive nominalized prose")
    chunks_and_idioms_en: str = Field(default="", description="3 whole-chunk expressions/phrasal verbs on separate lines (correct inflections only, e.g. 'ironing out' NEVER 'iron-outting'!) with meaning and fresh example")
    collocations_and_templates_en: str = Field(default="", description="3 collocation forks + 2 advanced C2 reusable sentence frames with [Slots]")
    subtext_and_drill_en: str = Field(default="", description="Pragmatic subtext radar (where X works vs Y fails) + 1 fill-in-the-blank drill")

class DraftShiftSchema(BaseModel):
    shift_title_en: str = Field(default="Style Transformation", description="Title of transformation")
    transformed_text_en: str = Field(default="", description="The rewritten text")
    key_refinements_en: str = Field(default="", description="2 concise bullets explaining the structural/lexical upgrades made")

class MoreMinerSchema(BaseModel):
    supplement_title_en: str = Field(default="Additional Extracted Expressions", description="Title")
    new_expressions_en: str = Field(default="", description="4 NEW whole-chunk expressions/collocations from the text (not extracted previously), each on a new line with definition and example")
    advanced_templates_en: str = Field(default="", description="2 advanced rhetorical sentence templates from the text")

class PodcastSchema(BaseModel):
    episode_title_en: str = Field(default="NYC Studio Roundtable", description="Episode title")
    script_en: str = Field(default="", description="6-turn lively script (Jake, Chloe, Marcus). Each speaker on a NEW line ('Jake: ...'). NO parentheses stage directions like (interrupting) or (laughs)!")
    closing_question_to_user_en: str = Field(default="", description="Direct engaging question inviting the listener's take")
    key_expressions_en: str = Field(default="", description="3 standout expressions actually used by the hosts in script_en, on separate lines with meaning and example")
    audio_recap_script_en: str = Field(default="", description="1-sentence spoken recap of the 3 expressions")

class QuizSchema(BaseModel):
    category_en: str = Field(default="C1/C2 Precision & Collocations", description="Category")
    scenario_en: str = Field(default="", description="Sentence with a blank OR C2 key-word transformation prompt")
    option_a: str = Field(default="", description="Option A")
    option_b: str = Field(default="", description="Option B")
    option_c: str = Field(default="", description="Option C")
    option_d: str = Field(default="", description="Option D")
    correct_letter: str = Field(default="B", description="'A', 'B', 'C', or 'D'")
    explanation_en: str = Field(default="", description="Clear English explanation of why the correct option wins and why the others fail")
    target_expression_en: str = Field(default="", description="The winning whole-chunk expression")

# ------------------------------------------------------------------------------
# الدستور اللغوي الصارم (SYS_PROMPT)
# ------------------------------------------------------------------------------
SYS_PROMPT = """
You are the linguistic engine of 'NYC English Lounge', an elite American English studio. Obey these laws strictly:
1. 100% ENGLISH IMMERSION IN CHAT, STUDY, PODCAST & QUIZ:
   - Write all explanations, definitions, and style notes in sharp, natural English.
   - Arabic is used EXCLUSIVELY inside `LexiconSchema` (`contextual_arabic_equivalents`), where you provide eloquent contextual Arabic equivalents (الفصحى السياقية الرصينة) and NEVER robotic literal word-for-word translations.
2. DISTINCT NYC HOST PERSONALITIES & BANTER:
   - Jake (31, Brooklyn tech product lead): pragmatic, direct, dry wit, fast-paced spoken idioms.
   - Chloe (29, Manhattan media strategist): perceptive, reads subtext and human dynamics, tactful yet candid.
   - Marcus (36, policy & strategy advisor): analytical, big-picture thinker, loves playing Devil's Advocate.
   - NEVER call the user Jake, Chloe, or Marcus! Address the user by their actual name.
   - NEVER write stage directions in parentheses like `(interrupting)` or `(laughs)` inside dialogue or podcast scripts.
3. SURGICAL ERROR TRIAGE (USER'S WORDS ONLY):
   - Evaluate ONLY what the user wrote. Catch every real grammar, uncountable noun (`informations`, `feedbacks`), verb-preposition trap (`discuss about`, `depend of`, `explain me`, `married with`), and collocation error (`take a fast decision`, `do a mistake`) in `error_corrections_list`.
   - Ignore missing commas in casual texting. Never corrupt standard idioms (keep `move the needle`, never `move the needle forward`; inflect phrasal verbs on the verb: `ironing out`, NEVER `iron-outting`).
4. AUTHENTIC REGISTER ISOLATION:
   - Casual: natural verb-driven everyday spoken NYC English.
   - Professional: diplomatic, solution-oriented workplace English (never blame colleagues rudely; reframe around timelines and shared goals; avoid archaic 'Kindly provide').
   - Formal (C2): high-level academic and executive prose using nominalization, cleft structures ('What impedes progress is...'), and hedging—never start with 'Dear [Name]'.
"""

# ------------------------------------------------------------------------------
# بناء لوحات المفاتيح والأزرار التفاعلية
# ------------------------------------------------------------------------------
def build_reply_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    s = _get_session(user_id)
    m, r = s["mode"], s["register"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=5)
    kb.row(
        types.KeyboardButton("Casual •" if r == "Casual" else "Casual"),
        types.KeyboardButton("Professional •" if r == "Professional" else "Professional"),
        types.KeyboardButton("Formal •" if r == "Formal" else "Formal"),
    )
    kb.row(
        types.KeyboardButton("Chat •" if m == "CHAT" else "Chat"),
        types.KeyboardButton("Lexicon •" if m == "LEXICON" else "Lexicon"),
        types.KeyboardButton("Study •" if m == "STUDY" else "Study"),
        types.KeyboardButton("Podcast •" if m == "PODCAST" else "Podcast"),
        types.KeyboardButton("Quiz •" if m == "QUIZ" else "Quiz"),
    )
    return kb

def _extract_lookup_terms(vault_items: List[Tuple[str, str, str]]) -> List[str]:
    terms = []
    for it in (vault_items or [])[:2]:
        if it and it[0]:
            t, _, _ = _clean_vault_entry(it[0], "", "")
            if 2 <= len(t) <= 32 and t not in terms:
                terms.append(t)
    return terms

def build_inline_kb(tok: str, ctype: str = "chat", lookup_terms: Optional[List[str]] = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Listen", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("PDF", callback_data=f"edpdf:{tok}"),
        types.InlineKeyboardButton("Save", callback_data=f"edsave:{tok}"),
        types.InlineKeyboardButton("Lexicon", callback_data=f"edlexcard:{tok}"),
    )
    if ctype == "chat":
        kb.row(
            types.InlineKeyboardButton("Other Host's Take", callback_data=f"edchat:take:{tok}"),
            types.InlineKeyboardButton("Devil's Advocate", callback_data=f"edchat:devil:{tok}"),
            types.InlineKeyboardButton("Scenario", callback_data="edscen:menu"),
        )
    elif ctype == "lexicon":
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("Word Family", callback_data=f"edlexext:family:{tok}"),
            types.InlineKeyboardButton("More Examples", callback_data=f"edlexext:examples:{tok}"),
            types.InlineKeyboardButton("Save", callback_data=f"edsave:{tok}"),
        )
    elif ctype == "miner":
        kb.row(
            types.InlineKeyboardButton("Extract More", callback_data=f"edmore:{tok}"),
            types.InlineKeyboardButton("Elevate (C2)", callback_data=f"edshift:c2:{tok}"),
            types.InlineKeyboardButton("Make Spoken", callback_data=f"edshift:spoken:{tok}"),
        )
    elif ctype == "draft":
        kb.row(
            types.InlineKeyboardButton("Soften", callback_data=f"edshift:soften:{tok}"),
            types.InlineKeyboardButton("Condense", callback_data=f"edshift:condense:{tok}"),
            types.InlineKeyboardButton("Make Spoken", callback_data=f"edshift:spoken:{tok}"),
            types.InlineKeyboardButton("Elevate (C2)", callback_data=f"edshift:c2:{tok}"),
        )
    if lookup_terms and ctype in ("chat", "study", "miner"):
        btns = [types.InlineKeyboardButton(f"🔍 {t[:22]}", callback_data=f"edqlk:{i}:{tok}") for i, t in enumerate(lookup_terms[:2])]
        if btns:
            kb.row(*btns)
    return kb

def send_html_safe(chat_id: int, html_text: str, reply_markup: Any = None):
    try:
        if len(html_text) <= 4080:
            return bot.send_message(chat_id, html_text, parse_mode="HTML", reply_markup=reply_markup)
        return bot.send_message(chat_id, html_text[:4050], parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        clean_plain = html.unescape(re.sub(r"<[^>]+>", "", html_text))
        return bot.send_message(chat_id, clean_plain[:4000], reply_markup=reply_markup)

def _fmt_fixes(raw: str, strike: bool = True) -> str:
    out = []
    for ln in re.split(r"[\n|]+", raw or ""):
        cl = ln.strip().lstrip("•-*").strip()
        if not cl:
            continue
        if "->" in cl or "→" in cl:
            pts = re.split(r"->|→", cl, maxsplit=1)
            l, r = _h(pts[0].strip("~❌ ")), _h(pts[1].strip("✅ "))
            if l and r:
                out.append(f"• <s>{l}</s> → <b>{r}</b>" if strike else f"• <i>{l}</i> → <b>{r}</b>")
        else:
            out.append(f"• {_h(cl)}")
    return "\n".join(out)

# ------------------------------------------------------------------------------
# محرك 1: المحادثة الحية والاندماج مع الشخصيات (Chat & Scenarios)
# ------------------------------------------------------------------------------
def execute_chat_turn(chat_id: int, user_id: int, user_text: str, is_voice: bool = False, special_angle: str = "", user_first_name: str = "Musa") -> ChatSchema:
    s = _get_session(user_id)
    reg = s["register"]
    hist_str = "\n".join([f"{user_first_name}: {h.get('u','')}\nLounge: {h.get('b','')}" for h in s["history"][-5:] if isinstance(h, dict)])
    vault_terms = _get_recent_vault_terms(user_id, 3)
    vault_hint = f"Naturally weave one of these saved expressions into the host reply if it fits: {', '.join(vault_terms)}." if vault_terms else ""

    angle_inst = ""
    if special_angle == "take":
        angle_inst = "Have a DIFFERENT host jump in with a contrasting NYC perspective on what the user just said!"
    elif special_angle == "devil":
        angle_inst = "Have Marcus or Jake play Devil's Advocate—respectfully challenge the user's premise with a sharp counter-argument!"

    sys_p = f"{SYS_PROMPT}\nActive Register: {reg}. User's name is {user_first_name}. {vault_hint} {angle_inst}"
    usr_p = f"Recent Conversation:\n{hist_str}\n\n{user_first_name} ({'voice note' if is_voice else 'message'}): \"{user_text}\""
    data: ChatSchema = call_llm_structured(sys_p, usr_p, ChatSchema, temp=0.65)

    _update_session(user_id, new_turn={"u": user_text[:220], "b": data.dialogue_en[:260]})
    if data.has_actual_errors and data.error_corrections_list:
        _log_dna(user_id, data.error_corrections_list)

    # التحقق مما إذا استخدم المستخدم عبارة التحدي أو إحدى عبارات خزنته
    mastery_banner = ""
    for vt in vault_terms:
        if len(vt) > 3 and vt.lower() in user_text.lower():
            mastery_banner = f"🎯 <b>Spot-on active usage of:</b> <i>{_h(vt)}</i>\n\n"
            break

    raw_diag = _h(re.sub(r"\([^)]*\)", "", data.dialogue_en))
    for spk in ("Jake", "Chloe", "Marcus"):
        raw_diag = re.sub(rf"(?:^|\n|\s)+{spk}\s*:\s*", f"\n\n<b>{spk}</b>\n", raw_diag)
    raw_diag = raw_diag.strip()
    if not any(f"<b>{sp}</b>" in raw_diag for sp in ("Jake", "Chloe", "Marcus")):
        raw_diag = f"<b>{_h(data.primary_speaker)}</b>\n{raw_diag}"

    blocks = []
    if data.has_actual_errors and data.error_corrections_list.strip():
        blocks.append(f"<b>Corrections</b>\n{_fmt_fixes(data.error_corrections_list, strike=True)}")
    else:
        blocks.append("<b>Phrasing:</b> ✓ Natural and grammatically sound.")
    if data.better_alternatives_list.strip():
        blocks.append(f"<b>Native Upgrades</b>\n{_fmt_fixes(data.better_alternatives_list, strike=False)}")

    clean_ke = _clean_multiline_bullets(data.key_expressions_en)
    ch_chunk = _h(data.active_challenge_chunk.strip(" '\"\u201c\u201d•*"))
    challenge_line = f"\n\n🎯 <b>Try in your next reply:</b> <code>{ch_chunk}</code>" if ch_chunk and "target" not in ch_chunk.lower() else ""

    drawer_en = (
        f"<b>Registers, Subtext &amp; Key Expressions ▼</b>\n\n"
        f"• <b>Casual:</b> <i>\"{_h(data.recast_casual_en)}\"</i>\n"
        f"• <b>Professional:</b> <i>\"{_h(data.recast_professional_en)}\"</i>\n"
        f"• <b>Formal (C2):</b> <b>\"{_h(data.recast_formal_c2_en)}\"</b>\n\n"
        f"<b>Pragmatic &amp; Usage Note</b>\n{_h(data.usage_and_subtext_note_en)}\n\n───\n"
        f"<b>Key Expressions</b>\n{clean_ke}{challenge_line}"
    )

    v_hdr = f"<b>Voice Note:</b> <i>\"{_h(user_text)}\"</i>\n\n" if is_voice else ""
    full_html = f"{mastery_banner}{v_hdr}{raw_diag}\n\n" + "\n\n".join(blocks) + f"\n\n<blockquote expandable>{drawer_en}</blockquote>"

    tok = hashlib.md5(f"CHT:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = _split_dialogue_segments(data.dialogue_en, data.primary_speaker)
    pdf_d = {
        "title": "NYC Lounge Conversation",
        "register": reg,
        "main_en": data.dialogue_en,
        "instant_fix": data.error_corrections_list,
        "diagnosis_en": f"{data.usage_and_subtext_note_en}\n\n{data.key_expressions_en}",
        "spoken_inf": data.recast_casual_en,
        "spoken_semi": data.recast_professional_en,
        "written_form": data.recast_formal_c2_en,
    }
    v_items = []
    for ln in data.key_expressions_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", data.dialogue_en)
            if t_c:
                v_items.append((t_c, d_c, data.dialogue_en))
    if not v_items and ch_chunk:
        v_items.append((ch_chunk, data.usage_and_subtext_note_en, data.recast_professional_en))

    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=user_text)
    l_terms = _extract_lookup_terms(v_items)
    send_html_safe(chat_id, full_html, reply_markup=build_inline_kb(tok, "chat", l_terms))
    threading.Thread(target=build_audio, args=(v_segs, False), daemon=True).start()
    return data

# ------------------------------------------------------------------------------
# محرك 2: المعجم السياقي والتلازمي العميق (Lexicon — 4-Path Engine)
# ------------------------------------------------------------------------------
def execute_lexicon_lookup(chat_id: int, user_id: int, query_text: str):
    reg = _get_session(user_id)["register"]
    task = (
        "Perform a deep contextual & collocational Lexicon lookup.\n"
        "- If input is English: provide Corpus Distribution, mandatory Syntactic & Semantic Prosody frame, sharp English definition & subtext, 2-3 eloquent contextual Arabic equivalents (الفصحى السياقية الرصينة), Collocation Forks (Verbs/Adjectives/Antonym), and 2 authentic examples.\n"
        "- If input is Arabic (word, idiom, or concept): provide the spectrum of authentic American English equivalents across Casual/Idiomatic, Workplace/Diplomatic, and C2 Academic registers, warn against literal translation traps, and show collocations & examples.\n"
        "- If input compares X vs Y: contrast their exact collocational and register boundaries."
    )
    data: LexiconSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Lookup Query: \"{query_text.strip()}\"", LexiconSchema, temp=0.45)

    html_out = (
        f"<b>Lexicon — {_h(data.headword_or_query or query_text)}</b>\n"
        f"<code>[{_h(data.corpus_distribution_badge)}]</code>\n\n"
        f"<b>Syntactic Frame &amp; Polarity</b>\n<code>{_h(data.syntactic_and_prosody_frame)}</code>\n\n"
        f"<b>Core Sense &amp; Subtext</b>\n{_h(data.core_definition_en)}\n\n"
        f"<b>Contextual Arabic &amp; Spectrum (المقابلات السياقية الرصينة)</b>\n{_clean_multiline_bullets(data.contextual_arabic_equivalents)}\n\n"
        f"<b>Collocation Forks</b>\n{_clean_multiline_bullets(data.collocation_forks_en)}\n\n"
        f"<b>Authentic Examples</b>\n{_clean_multiline_bullets(data.authentic_examples_en)}"
    )
    tok = hashlib.md5(f"LEX:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Marcus", f"{data.headword_or_query}. {data.authentic_examples_en}")]
    pdf_d = {
        "title": f"Lexicon: {data.headword_or_query}",
        "register": reg,
        "main_en": f"{data.syntactic_and_prosody_frame}\n\n{data.core_definition_en}",
        "instant_fix": data.collocation_forks_en,
        "diagnosis_en": data.authentic_examples_en,
        "ar_anchor": data.contextual_arabic_equivalents,
    }
    v_items = [(data.headword_or_query or query_text[:60], f"{data.core_definition_en} | {data.contextual_arabic_equivalents}", data.authentic_examples_en)]
    store_card(tok, user_id, v_segs, pdf_d, v_items, ar_text=data.contextual_arabic_equivalents, source_text=data.headword_or_query or query_text)
    send_html_safe(chat_id, html_out, reply_markup=build_inline_kb(tok, "lexicon"))

def execute_lexicon_extension(chat_id: int, user_id: int, term: str, ext_type: str):
    prompt = (
        f"Provide the complete Word Family & Derivatives (Noun, Verb, Adjective, Adverb, Opposite) for '{term}' with usage examples and concise Arabic meanings."
        if ext_type == "family"
        else f"Provide 4 diverse, high-level sentences using '{term}' across Casual, Workplace, Executive, and Academic contexts with brief Arabic nuances."
    )
    res: LexiconExtraSchema = call_llm_structured(f"{SYS_PROMPT}\n{prompt}", f"Target: {term}", LexiconExtraSchema, temp=0.5)
    send_html_safe(chat_id, f"<b>{_h(res.title_en)} — {_h(term)}</b>\n\n{_clean_multiline_bullets(res.content_bullets_en)}")

# ------------------------------------------------------------------------------
# محرك 3: الدراسة واستخراج التراكيب وجراحة المسودات (Study & Miner)
# ------------------------------------------------------------------------------
def execute_study_and_miner(chat_id: int, user_id: int, user_input: str, is_voice: bool = False, already_mined: str = "", shift: str = ""):
    s = _get_session(user_id)
    reg = s["register"]

    # استدعاء مصغّر مستقل وسريع عند الضغط على Soften / Condense / Make Spoken / Elevate C2
    if shift:
        shift_map = {
            "soften": "Rewrite this text so it sounds warm, diplomatic, tactful, and collaborative in American workplace English (eliminate blame).",
            "condense": "Condense this text by 35-40% into crisp, high-impact executive English.",
            "spoken": "Convert this written text into natural, verb-driven spoken American English suitable for saying aloud in a meeting.",
            "c2": "Elevate this text into authoritative C2 Academic/Executive prose using nominalization, cleft framing, and precise collocations.",
        }
        sh_data: DraftShiftSchema = call_llm_structured(
            f"{SYS_PROMPT}\n{shift_map.get(shift, shift_map['spoken'])}",
            f"Text:\n\"{user_input[:3000]}\"",
            DraftShiftSchema,
            temp=0.5,
        )
        tok = hashlib.md5(f"SHF:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Chloe", sh_data.transformed_text_en)]
        pdf_d = {"title": sh_data.shift_title_en, "register": reg, "main_en": sh_data.transformed_text_en, "instant_fix": sh_data.key_refinements_en}
        store_card(tok, user_id, v_segs, pdf_d, [(sh_data.transformed_text_en[:90], sh_data.key_refinements_en, user_input[:200])], source_text=user_input)
        sh_html = (
            f"<b>{_h(sh_data.shift_title_en)}</b>\n\n"
            f"<i>\"{_h(sh_data.transformed_text_en)}\"</i>\n\n"
            f"<b>Key Upgrades</b>\n{_clean_multiline_bullets(sh_data.key_refinements_en)}"
        )
        send_html_safe(chat_id, sh_html, reply_markup=build_inline_kb(tok, "draft"))
        return

    # استدعاء مصغّر مستقل عند الضغط على Extract More لمنع تكرار البطاقة الأولى
    if already_mined:
        m_task = f"Previously extracted expressions: [{already_mined}]. Extract 4 COMPLETELY NEW idioms, phrasal verbs, or collocations from this text that were NOT listed above, plus 2 advanced sentence templates."
        m_data: MoreMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{m_task}", f"Text:\n\"{user_input[:4500]}\"", MoreMinerSchema, temp=0.55)
        tok = hashlib.md5(f"MOR:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_items = []
        for ln in m_data.new_expressions_en.splitlines():
            if len(ln.strip()) > 6:
                t_c, d_c, _ = _clean_vault_entry(ln, "", user_input[:200])
                if t_c:
                    v_items.append((t_c, d_c, ln))
        new_mined = f"{already_mined}, {', '.join([x[0] for x in v_items])}".strip(", ")
        store_card(tok, user_id, [("Marcus", m_data.new_expressions_en)], {"title": m_data.supplement_title_en, "main_en": m_data.new_expressions_en, "diagnosis_en": m_data.advanced_templates_en}, v_items, source_text=user_input, mined_so_far=new_mined)
        m_html = (
            f"<b>{_h(m_data.supplement_title_en)}</b>\n\n"
            f"<b>New Expressions &amp; Chunks</b>\n{_clean_multiline_bullets(m_data.new_expressions_en)}\n\n"
            f"<b>Rhetorical Sentence Templates</b>\n{_clean_multiline_bullets(m_data.advanced_templates_en)}"
        )
        send_html_safe(chat_id, m_html, reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    w_cnt = len(user_input.split())
    low_in = user_input.strip().lower()
    is_question = low_in.startswith(("what ", "what'", "how ", "why ", "when ", "where ", "which ", "can you", "could you", "is there", "difference", "meaning")) or (low_in.endswith("?") and w_cnt < 50)
    is_miner = (w_cnt > 50 and not is_question)

    if is_miner:
        task = "LINGUISTIC MINING: Extract 3 standout whole-chunk idioms/phrasal verbs, 3 collocation forks + 2 rhetorical C2 sentence templates directly from the text, and provide verb-driven Casual vs nominalized C2 Formal versions."
    elif is_question:
        task = "Answer the user's question or 'How do I say...' query with exact synonym & register boundaries (never use informal phrasal verbs inside the Formal C2 version!)."
    else:
        task = "Edit and upgrade the user's draft. Make the Professional version genuinely diplomatic and collaborative (reframe around shared goals/timelines, never personal blame)."

    data: StudyMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Register: {reg}\nInput:\n\"{user_input[:5000]}\"", StudyMinerSchema, temp=0.5)

    top = (
        f"<b>{_h(data.dossier_title_en)}</b> • <i>{_h(reg)}</i>\n\n"
        f"{_h(data.editorial_diagnosis_en)}\n\n"
        f"<b>Core Phrasing</b>\n<i>\"{_h(data.core_output_en)}\"</i>\n\n"
        f"<b>1. Idiomatic Chunks &amp; Nuances</b>\n{_clean_multiline_bullets(data.chunks_and_idioms_en)}\n\n"
        f"<b>2. Collocations &amp; C2 Sentence Frames</b>\n{_clean_multiline_bullets(data.collocations_and_templates_en)}"
    )
    dr_en = (
        f"<b>Register Matrix &amp; Subtext Radar ▼</b>\n\n"
        f"• <b>Casual (Verb-Driven):</b> <i>\"{_h(data.casual_version_en)}\"</i>\n"
        f"• <b>Professional (Diplomatic):</b> <i>\"{_h(data.professional_version_en)}\"</i>\n"
        f"• <b>Formal (C2 Nominalized):</b> <b>\"{_h(data.formal_c2_version_en)}\"</b>\n\n───\n"
        f"{_h(data.subtext_and_drill_en)}"
    )

    tok = hashlib.md5(f"STD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Jake", data.core_output_en), ("Chloe", data.professional_version_en), ("Marcus", data.formal_c2_version_en)]
    pdf_d = {"title": data.dossier_title_en, "register": reg, "main_en": data.core_output_en, "instant_fix": data.editorial_diagnosis_en, "diagnosis_en": f"{data.chunks_and_idioms_en}\n{data.collocations_and_templates_en}", "spoken_inf": data.casual_version_en, "spoken_semi": data.professional_version_en, "written_form": data.formal_c2_version_en}
    v_items = []
    for ln in data.chunks_and_idioms_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", data.core_output_en)
            if t_c:
                v_items.append((t_c, d_c, ln))
    target_for_shift = data.core_output_en if is_question else user_input[:3000]
    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=target_for_shift, mined_so_far=", ".join([x[0] for x in v_items]))

    ctype = "miner" if is_miner else ("study" if is_question else ("draft" if w_cnt > 8 else "study"))
    send_html_safe(chat_id, f"{top}\n\n<blockquote expandable>{dr_en}</blockquote>", reply_markup=build_inline_kb(tok, ctype, _extract_lookup_terms(v_items)))

# ------------------------------------------------------------------------------
# محرك 4: استوديو البودكاست وتدريب الأذن (Ear-First Podcast & Roundtable)
# ------------------------------------------------------------------------------
def execute_podcast(chat_id: int, user_id: int, topic: str = "", hosts: int = 3, user_first_name: str = "Musa"):
    s = _get_session(user_id)
    reg = s["register"]
    _update_session(user_id, mode="PODCAST", podcast_hosts=hosts, sub_state="PODCAST_ACTIVE")
    t_p = topic.strip() if len(topic.strip()) > 2 else f"Realistic {reg} NYC roundtable on work, decision-making, and human habits"
    data: PodcastSchema = call_llm_structured(
        f"{SYS_PROMPT}\nProduce a {hosts}-host NYC podcast ({reg}) with natural turn-taking. Address {user_first_name} in the closing question. NEVER put stage directions in parentheses!",
        f"Topic: {t_p}",
        PodcastSchema,
        temp=0.7,
    )

    raw_s = _h(re.sub(r"\([^)]*\)", "", data.script_en))
    for spk in ("Jake", "Chloe", "Marcus"):
        raw_s = re.sub(rf"(?:^|\n|\s)+{spk}\s*:\s*", f"\n\n<b>{spk}:</b> ", raw_s)
    raw_s = raw_s.strip()

    clean_pod_exp = _clean_multiline_bullets(data.key_expressions_en)
    html_out = (
        f"<b>NYC Podcast ({hosts} Hosts • {_h(reg)})</b>\n"
        f"<i>{_h(data.episode_title_en)}</i>\n\n"
        f"🎧 <i>Listen to the audio first to train your ear, then open the transcript below.</i>\n\n"
        f"<b>Your Turn:</b> <i>\"{_h(data.closing_question_to_user_en)}\"</i>\n"
        f"<i>(Reply by voice or text to join the hosts at the table!)</i>\n\n"
        f"<blockquote expandable><b>Full Episode Transcript ▼</b>\n\n{raw_s}</blockquote>\n"
        f"<blockquote expandable><b>Standout Expressions ▼</b>\n\n{clean_pod_exp}</blockquote>"
    )
    tok = hashlib.md5(f"POD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(data.script_en, "Jake")
    if data.audio_recap_script_en.strip():
        segs.append(("Marcus", f"Key expressions from this episode: {data.audio_recap_script_en}"))

    v_items = []
    for ln in data.key_expressions_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", data.episode_title_en)
            if t_c:
                v_items.append((t_c, d_c, ln))
    pdf_d = {"title": data.episode_title_en, "register": reg, "main_en": data.script_en, "instant_fix": data.closing_question_to_user_en, "diagnosis_en": data.key_expressions_en}
    store_card(tok, user_id, segs, pdf_d, v_items, source_text=data.episode_title_en)
    _update_session(user_id, new_turn={"u": f"[Podcast Topic: {t_p}]", "b": f"{data.script_en[:240]} | Question: {data.closing_question_to_user_en}"})

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Normal 1.0x", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("Slow 0.85x", callback_data=f"edtts:slow:{tok}"),
        types.InlineKeyboardButton("PDF", callback_data=f"edpdf:{tok}"),
        types.InlineKeyboardButton("Save", callback_data=f"edsave:{tok}"),
    )
    kb.row(
        types.InlineKeyboardButton("New 2-Host", callback_data="edpod:2"),
        types.InlineKeyboardButton("New 3-Host", callback_data="edpod:3"),
        types.InlineKeyboardButton("Lexicon", callback_data=f"edlexcard:{tok}"),
    )
    send_html_safe(chat_id, html_out, reply_markup=kb)

    mp3 = build_audio(segs, slow=False)
    if mp3 and os.path.exists(mp3):
        with open(mp3, "rb") as af:
            bot.send_voice(chat_id, af, caption=f"<b>{_h(data.episode_title_en)}</b>", parse_mode="HTML")
        os.remove(mp3)

# ------------------------------------------------------------------------------
# محرك 5: الكويز التفاعلي، الخزنة، ورادار الأخطاء (Quiz, Vault & Radar)
# ------------------------------------------------------------------------------
def send_quiz(chat_id: int, user_id: int, q_mode: str = "standard"):
    reg = _get_session(user_id)["register"]
    extra = ""
    if q_mode == "dna":
        with db_connect() as conn:
            rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        if rows:
            extra = "Base this question specifically on testing one of these past mistake patterns: " + "; ".join([f"{r[0]} -> {r[1]}" for r in rows])
    elif q_mode == "vault":
        vt = _get_recent_vault_terms(user_id, 5)
        if vt:
            extra = "Base this question specifically on testing one of these saved expressions: " + ", ".join(vt)
    elif q_mode == "c2":
        extra = "Make this a Cambridge C2 Key-Word Transformation style multiple-choice question testing advanced collocations or inversion."

    q: QuizSchema = call_llm_structured(f"{SYS_PROMPT}\nCreate a 4-option multiple choice question ({reg}). {extra}", "Generate precision quiz.", QuizSchema, temp=0.7)
    qid = hashlib.md5(f"QZ:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    with _db_lock, db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO quiz_v15 (q_id, user_id, quiz_json) VALUES (?, ?, ?)", (qid, user_id, q.model_dump_json()))
        conn.commit()
    kb = types.InlineKeyboardMarkup()
    kb.row(*[types.InlineKeyboardButton(x, callback_data=f"edqz:{x}:{qid}") for x in ("A", "B", "C", "D")])
    kb.row(
        types.InlineKeyboardButton("Next", callback_data="edqznext:standard"),
        types.InlineKeyboardButton("Past Mistakes", callback_data="edqznext:dna"),
        types.InlineKeyboardButton("C2 Challenge", callback_data="edqznext:c2"),
    )
    send_html_safe(
        chat_id,
        f"<b>Quiz — {_h(q.category_en)} ({_h(reg)})</b>\n\n<i>\"{_h(q.scenario_en)}\"</i>\n\n<b>A)</b> {_h(q.option_a)}\n<b>B)</b> {_h(q.option_b)}\n<b>C)</b> {_h(q.option_c)}\n<b>D)</b> {_h(q.option_d)}",
        reply_markup=kb,
    )

def show_vault(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT id, chunk, meaning_ar, context_sentence FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>Your Vault is empty.</b>\nTap <b>Save</b> under any card to store expressions for active recall.", reply_markup=build_reply_kb(user_id))
        return
    blocks = ["<b>Saved Expressions — Active Recall Vault</b>\n<i>(Tap the hidden spoiler bar to test your recall)</i>"]
    for i, r in enumerate(rows, 1):
        term, m_note, ctx = r[1], r[2], r[3]
        clue = _h(m_note[:160]) if m_note else _h(ctx[:160])
        blocks.append(f"<b>{i}.</b> <tg-spoiler><b>{_h(term)}</b></tg-spoiler>\n• <i>Clue/Context:</i> {clue}")
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Quiz My Vault", callback_data="edqznext:vault"),
        types.InlineKeyboardButton("Export PDF", callback_data="edvault:pdf"),
        types.InlineKeyboardButton("Anki TSV", callback_data="edvault:anki"),
    )
    kb.row(types.InlineKeyboardButton("Remove Last Saved", callback_data="edvault:del"))
    send_html_safe(chat_id, "\n\n".join(blocks), reply_markup=kb)

def show_radar(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>Linguistic Radar</b>\nNo recurring error patterns logged yet. Keep chatting and practicing!", reply_markup=build_reply_kb(user_id))
        return
    lines = [f"• <s>{_h(r[0])}</s> → <b>{_h(r[1])}</b>" for r in rows]
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("Drill My Mistake Patterns", callback_data="edqznext:dna"))
    send_html_safe(chat_id, "<b>Your Personal Linguistic Radar</b>\n<i>Recent adjustments &amp; collocation upgrades:</i>\n\n" + "\n".join(lines), reply_markup=kb)

def send_guide(chat_id: int, user_id: int):
    g = (
        "<b>NYC English Lounge — Studio Guide</b>\n\n"
        "• <b>Register Bar (Top):</b> Switch anytime between <b>Casual</b> (everyday NYC spoken), <b>Professional</b> (diplomatic workplace), and <b>Formal</b> (C2 executive &amp; academic prose).\n\n"
        "• <b>Studio Modes (Bottom):</b>\n"
        "1. <b>Chat:</b> Live conversation with Jake, Chloe &amp; Marcus. Includes instant corrections, register recasts, roleplay scenarios, and one-tap Lexicon buttons.\n"
        "2. <b>Lexicon:</b> Deep bidirectional lookup (EN ⇄ AR). Send an English idiom, an Arabic phrase/concept, or <i>X vs Y</i> for corpus distribution, syntactic frames, and collocation forks.\n"
        "3. <b>Study:</b> Send any question, draft paragraph, PDF, or long article to extract idioms, C2 templates, or apply <i>Soften / Condense / Make Spoken / Elevate C2</i>.\n"
        "4. <b>Podcast:</b> Ear-first 2-Host &amp; 3-Host episodes. Reply to any episode to discuss the topic with the hosts.\n"
        "5. <b>Quiz, /vault &amp; /radar:</b> Active recall spoilers, personal error radar, and PDF/Anki exports."
    )
    send_html_safe(chat_id, g, reply_markup=build_reply_kb(user_id))

# ------------------------------------------------------------------------------
# لوحة تحكم المالك لإدارة الأصدقاء والحصص اليومية (/admin)
# ------------------------------------------------------------------------------
def send_admin_panel(chat_id: int):
    if int(chat_id) != PRIMARY_OWNER_ID:
        return
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT user_id, full_name, username, status, daily_limit, used_today, total_used FROM members_v17 WHERE user_id != ? ORDER BY total_used DESC",
            (PRIMARY_OWNER_ID,),
        ).fetchall()

    lines = ["<b>🔐 Studio Access &amp; Quota Control</b>"]
    kb = types.InlineKeyboardMarkup()
    if not rows:
        lines.append("\n<i>No friends registered yet. Create a one-click invite link below or forward a message from a friend here.</i>")
    else:
        lines.append("\n<b>Members &amp; Today's Usage:</b>")
        for r in rows[:15]:
            uid, name, uname, st, lim, used, tot = r
            lim_str = "∞" if lim < 0 else str(lim)
            badge = "🟢" if st == "approved" else ("🟡" if st == "pending" else "🔴")
            u_tag = f" (@{_h(uname)})" if uname else ""
            lines.append(f"{badge} <b>{_h(name)}</b>{u_tag} — <code>{used}/{lim_str}</code> today (Total: {tot})")
            kb.row(types.InlineKeyboardButton(f"⚙️ {name[:18]} ({used}/{lim_str})", callback_data=f"adm:view:{uid}"))

    kb.row(
        types.InlineKeyboardButton("🔗 Invite Link (15/day)", callback_data="adm:inv:15"),
        types.InlineKeyboardButton("🔗 Invite Link (30/day)", callback_data="adm:inv:30"),
    )
    send_html_safe(chat_id, "\n".join(lines), reply_markup=kb)

def send_member_control_card(chat_id: int, target_uid: int):
    if int(chat_id) != PRIMARY_OWNER_ID:
        return
    with db_connect() as conn:
        r = conn.execute(
            "SELECT user_id, full_name, username, status, daily_limit, used_today, total_used FROM members_v17 WHERE user_id=?",
            (target_uid,),
        ).fetchone()
    if not r:
        return
    uid, name, uname, st, lim, used, tot = r
    lim_str = "Unlimited" if lim < 0 else f"{lim} msgs/day"
    u_tag = f"@{_h(uname)}" if uname else "No username"
    txt = (
        f"<b>👤 Member Control — {_h(name)}</b>\n"
        f"• <b>ID:</b> <code>{uid}</code> ({u_tag})\n"
        f"• <b>Status:</b> <b>{st.upper()}</b>\n"
        f"• <b>Daily Quota:</b> <b>{lim_str}</b>\n"
        f"• <b>Used Today:</b> <code>{used}</code> (Total: <code>{tot}</code>)"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("5/day", callback_data=f"adm:set:{uid}:5"),
        types.InlineKeyboardButton("15/day", callback_data=f"adm:set:{uid}:15"),
        types.InlineKeyboardButton("30/day", callback_data=f"adm:set:{uid}:30"),
        types.InlineKeyboardButton("50/day", callback_data=f"adm:set:{uid}:50"),
        types.InlineKeyboardButton("∞", callback_data=f"adm:set:{uid}:-1"),
    )
    kb.row(
        types.InlineKeyboardButton("Reset Today (0)", callback_data=f"adm:rst:{uid}"),
        types.InlineKeyboardButton("Pause Access" if st == "approved" else "Approve Access", callback_data=f"adm:tog:{uid}"),
    )
    kb.row(types.InlineKeyboardButton("← Back to Members List", callback_data="adm:list"))
    send_html_safe(chat_id, txt, reply_markup=kb)

# ------------------------------------------------------------------------------
# معالج الرسائل الرئيسي
# ------------------------------------------------------------------------------
def handle_incoming_message(message: types.Message):
    chat_id = message.chat.id
    msg_id = getattr(message, "message_id", None)
    if msg_id is not None:
        dedup_k = (chat_id, msg_id)
        if dedup_k in _PROCESSED_MSG_IDS:
            return
        _PROCESSED_MSG_IDS.add(dedup_k)

    user_obj = getattr(message, "from_user", None)
    uid = int(user_obj.id) if user_obj else PRIMARY_OWNER_ID
    first_name = (getattr(user_obj, "first_name", "") or "Musa").strip()
    txt = (message.text or message.caption or "").strip()
    low = txt.lower()

    # التحقق من روابط الدعوة السريعة (/start inv_xxxx)
    if low.startswith("/start inv_"):
        inv_code = txt.split(maxsplit=1)[1].strip()
        redeemed_lim = redeem_invite_code(inv_code, user_obj)
        if redeemed_lim is not None:
            send_html_safe(
                chat_id,
                f"<b>Welcome to NYC English Lounge, {_h(first_name)}!</b>\n"
                f"Your private membership is now active (<b>{redeemed_lim} sessions/day</b>). Choose a mode below to begin:",
                reply_markup=build_reply_kb(uid),
            )
            if uid != PRIMARY_OWNER_ID:
                send_html_safe(PRIMARY_OWNER_ID, f"🟢 <b>{_h(first_name)}</b> (<code>{uid}</code>) joined via your invite link ({redeemed_lim}/day).")
            return

    # إذا قام المالك بتحويل رسالة من صديق أو كتابة /allow ID LIMIT
    if uid == PRIMARY_OWNER_ID:
        if getattr(message, "forward_from", None) and getattr(message.forward_from, "id", None):
            f_user = message.forward_from
            get_or_register_member(f_user)
            set_member_quota(int(f_user.id), status="approved", daily_limit=15)
            send_member_control_card(chat_id, int(f_user.id))
            return
        if low in ("/admin", "admin"):
            send_admin_panel(chat_id)
            return
        if low.startswith("/allow "):
            pts = txt.split()
            if len(pts) >= 2 and pts[1].isdigit():
                t_id = int(pts[1])
                lim_v = int(pts[2]) if len(pts) >= 3 and pts[2].lstrip("-").isdigit() else 15
                set_member_quota(t_id, status="approved", daily_limit=lim_v)
                send_member_control_card(chat_id, t_id)
                return

    # فحص صلاحية العضو قبل السماح بأي تفاعل
    member = get_or_register_member(user_obj)
    if uid != PRIMARY_OWNER_ID and member["status"] != "approved":
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Request Membership Access", callback_data=f"reqacc:{uid}"))
        send_html_safe(
            chat_id,
            "<b>NYC English Lounge — Private Studio</b>\n\n"
            "This linguistic coaching studio is available by invitation only. Tap below to request access from the host.",
            reply_markup=kb,
        )
        return

    # أوامر التنقل السريع (لا تستهلك من حصة الرسائل اليومية)
    if low in ("/guide", "guide", "user guide"):
        send_guide(chat_id, uid)
        return
    if low in ("/vault",) or txt.startswith(("Vault", "💎")):
        show_vault(chat_id, uid)
        return
    if low in ("/radar", "radar"):
        show_radar(chat_id, uid)
        return
    if low in ("/casual", "/informal", "/professional", "/semiformal", "/formal") or txt.startswith(("Casual", "Professional", "Formal")):
        r_target = "Casual" if "casual" in low else ("Formal" if ("formal" in low and "semi" not in low) else "Professional")
        _update_session(uid, register=r_target)
        send_html_safe(chat_id, f"<b>Register set to: {r_target}</b>", reply_markup=build_reply_kb(uid))
        return
    if low in ("/start", "/help", "/new", "new topic") or low in ("/chat", "/lounge") or txt.startswith(("Chat", "💬")):
        _update_session(uid, mode="CHAT", sub_state="", clear_hist=(low in ("/start", "/new")))
        reg_now = _get_session(uid)["register"]
        openers = {
            "Casual": [
                f"<b>Jake</b>\nHey {_h(first_name)}—grab a seat. How's your day going so far, or what's on your mind?",
                f"<b>Chloe</b>\nHey {_h(first_name)}! Good to see you in the lounge. What are we getting into today?",
            ],
            "Professional": [
                f"<b>Chloe</b>\nHi {_h(first_name)}. Whether you want to talk through a workplace situation or test an idea with us, the floor is yours.",
                f"<b>Marcus</b>\nHello {_h(first_name)}. Good to catch you—what topic or scenario shall we unpack today?",
            ],
            "Formal": [
                f"<b>Marcus</b>\nWelcome, {_h(first_name)}. Share any policy question, theoretical argument, or thesis point you would like to examine.",
            ],
        }
        send_html_safe(chat_id, random.choice(openers.get(reg_now, openers["Professional"])), reply_markup=build_reply_kb(uid))
        return
    if low in ("/lexicon", "/dict", "/dictionary") or txt.startswith(("Lexicon", "📖")):
        _update_session(uid, mode="LEXICON", sub_state="")
        send_html_safe(
            chat_id,
            "<b>Contextual Lexicon &amp; Collocation Engine (EN ⇄ AR)</b>\n"
            "Send any English word/idiom, Arabic phrase/concept, or <i>X vs Y</i> comparison:",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/study" or txt.startswith(("Study", "📚")):
        _update_session(uid, mode="STUDY", sub_state="")
        send_html_safe(
            chat_id,
            "<b>Study, Expression Miner &amp; C2 Draft Surgery</b>\n"
            "Send any question, draft paragraph, article, PDF, or voice recording:",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/podcast" or txt.startswith(("Podcast", "🎙️")):
        _update_session(uid, mode="PODCAST", sub_state="")
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("2 Hosts (Duo)", callback_data="edpod:2"), types.InlineKeyboardButton("3 Hosts (Roundtable)", callback_data="edpod:3"))
        send_html_safe(chat_id, "<b>Podcast Studio</b>\nTap below for an episode, or type any topic you want the hosts to debate:", reply_markup=kb)
        return
    if low == "/quiz" or txt.startswith(("Quiz", "🎯")):
        ok_q, reason_q = check_and_consume_quota(user_obj)
        if not ok_q:
            lim_n = reason_q.split(":")[-1]
            send_html_safe(chat_id, f"<b>Daily Studio Limit Reached ({lim_n} sessions/day)</b>\nYour quota resets automatically tomorrow. You can review your /vault anytime.")
            return
        _update_session(uid, mode="QUIZ", sub_state="")
        send_quiz(chat_id, uid)
        return

    # التحقق من الحصة اليومية قبل استدعاء الذكاء الاصطناعي
    ok_quota, q_reason = check_and_consume_quota(user_obj)
    if not ok_quota:
        lim_num = q_reason.split(":")[-1]
        send_html_safe(
            chat_id,
            f"<b>Daily Studio Limit Reached</b>\n"
            f"You have completed your <b>{lim_num}</b> daily sessions for today. Your quota resets automatically tomorrow, or you can review your saved expressions in /vault anytime.",
        )
        return

    ack_msg = None
    try:
        ack_msg = bot.send_message(chat_id, "✍️ ...")
    except Exception:
        pass

    try:
        if message.content_type == "document":
            fb = bot.download_file(bot.get_file(message.document.file_id).file_path)
            if (message.document.file_name or "").lower().endswith(".pdf") and HAS_PYPDF:
                tp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
                open(tp, "wb").write(fb)
                dt = "\n".join([p.extract_text() or "" for p in PdfReader(tp).pages[:12]])
            else:
                dt = fb.decode("utf-8", errors="ignore")
            execute_study_and_miner(chat_id, uid, dt)
        elif message.content_type in ("voice", "audio"):
            fid = message.voice.file_id if message.content_type == "voice" else message.audio.file_id
            tr = transcribe_voice(bot.download_file(bot.get_file(fid).file_path))
            if not tr:
                send_html_safe(chat_id, "Could not catch the audio clearly—please try speaking a bit closer to the microphone.")
            elif _get_session(uid)["mode"] == "STUDY" or len(tr.split()) > 75:
                execute_study_and_miner(chat_id, uid, tr, is_voice=True)
            else:
                execute_chat_turn(chat_id, uid, tr, is_voice=True, user_first_name=first_name)
        elif txt:
            sess = _get_session(uid)
            mode, sub_st = sess["mode"], sess["sub_state"]
            if mode == "LEXICON":
                execute_lexicon_lookup(chat_id, uid, txt)
            elif len(txt.split()) > 65 or mode == "STUDY":
                execute_study_and_miner(chat_id, uid, txt)
            elif mode == "PODCAST":
                # إذا كان المستخدم يرد على سؤال البودكاست، يدخل في حوار طاولة مستديرة مع المذيعين!
                if sub_st == "PODCAST_ACTIVE" and not low.startswith(("new:", "topic:")):
                    execute_chat_turn(chat_id, uid, txt, is_voice=False, user_first_name=first_name)
                else:
                    execute_podcast(chat_id, uid, topic=re.sub(r"^(new|topic)\s*:\s*", "", txt, flags=re.I), user_first_name=first_name)
            else:
                execute_chat_turn(chat_id, uid, txt, is_voice=False, user_first_name=first_name)
    except Exception:
        logger.error(f"Internal error: {traceback.format_exc()}")
        send_html_safe(chat_id, "<b>Chloe</b>\nGive me just a second—could you resend that thought?")
    finally:
        if ack_msg:
            try:
                bot.delete_message(chat_id, ack_msg.message_id)
            except Exception:
                pass

@bot.message_handler(content_types=["text", "voice", "audio", "photo", "document"])
def on_message(message: types.Message):
    threading.Thread(target=handle_incoming_message, args=(message,), daemon=True).start()

# ------------------------------------------------------------------------------
# معالج الأزرار التفاعلية (Inline Callbacks)
# ------------------------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: types.CallbackQuery):
    user_obj = getattr(call, "from_user", None)
    uid = int(user_obj.id) if user_obj else PRIMARY_OWNER_ID
    first_name = (getattr(user_obj, "first_name", "") or "Musa").strip()
    chat_id = call.message.chat.id
    d = (call.data or "").strip()
    fb_txt = (call.message.text or call.message.caption or "").strip()

    # 1. طلب عضوية من صديق جديد
    if d.startswith("reqacc:"):
        m = get_or_register_member(user_obj)
        bot.answer_callback_query(call.id, "Request sent to the host!")
        bot.edit_message_text(
            "<b>Request Sent</b>\nYour membership request has been forwarded to the host. You will be notified here as soon as it is approved.",
            chat_id,
            call.message.message_id,
            parse_mode="HTML",
        )
        u_tag = f" (@{_h(m['username'])})" if m["username"] else ""
        adm_kb = types.InlineKeyboardMarkup()
        adm_kb.row(
            types.InlineKeyboardButton("Approve (10/d)", callback_data=f"adm:set:{uid}:10"),
            types.InlineKeyboardButton("Approve (25/d)", callback_data=f"adm:set:{uid}:25"),
            types.InlineKeyboardButton("Approve (50/d)", callback_data=f"adm:set:{uid}:50"),
        )
        adm_kb.row(
            types.InlineKeyboardButton("Unlimited (∞)", callback_data=f"adm:set:{uid}:-1"),
            types.InlineKeyboardButton("Decline", callback_data=f"adm:dec:{uid}"),
        )
        send_html_safe(
            PRIMARY_OWNER_ID,
            f"🔔 <b>New Studio Access Request</b>\n• <b>Name:</b> {_h(m['name'])}{u_tag}\n• <b>ID:</b> <code>{uid}</code>\n\nSelect their daily message limit to approve:",
            reply_markup=adm_kb,
        )
        return

    # 2. أزرار لوحة تحكم المالك (Admin Callbacks)
    if d.startswith("adm:"):
        if uid != PRIMARY_OWNER_ID:
            bot.answer_callback_query(call.id, "Host access only.")
            return
        pts = d.split(":")
        act = pts[1]
        if act == "list":
            bot.answer_callback_query(call.id)
            send_admin_panel(chat_id)
        elif act == "view":
            bot.answer_callback_query(call.id)
            send_member_control_card(chat_id, int(pts[2]))
        elif act == "set":
            t_uid, lim_v = int(pts[2]), int(pts[3])
            set_member_quota(t_uid, status="approved", daily_limit=lim_v)
            lim_label = "Unlimited" if lim_v < 0 else f"{lim_v}/day"
            bot.answer_callback_query(call.id, f"Approved: {lim_label}")
            send_member_control_card(chat_id, t_uid)
            try:
                send_html_safe(
                    t_uid,
                    f"<b>Welcome to NYC English Lounge!</b>\nYour membership is active (Daily limit: <b>{lim_label}</b>). Tap any mode below to begin:",
                    reply_markup=build_reply_kb(t_uid),
                )
            except Exception:
                pass
        elif act == "rst":
            t_uid = int(pts[2])
            set_member_quota(t_uid, reset_today=True)
            bot.answer_callback_query(call.id, "Today's counter reset to 0!")
            send_member_control_card(chat_id, t_uid)
        elif act == "tog":
            t_uid = int(pts[2])
            with db_connect() as conn:
                r = conn.execute("SELECT status FROM members_v17 WHERE user_id=?", (t_uid,)).fetchone()
            new_st = "paused" if (r and r[0] == "approved") else "approved"
            set_member_quota(t_uid, status=new_st)
            bot.answer_callback_query(call.id, f"Status: {new_st}")
            send_member_control_card(chat_id, t_uid)
        elif act == "dec":
            t_uid = int(pts[2])
            set_member_quota(t_uid, status="paused", daily_limit=0)
            bot.answer_callback_query(call.id, "Declined.")
        elif act == "inv":
            lim_v = int(pts[2])
            code = create_invite_code(lim_v)
            b_user = _BOT_USERNAME or bot.get_me().username
            link = f"https://t.me/{b_user}?start={code}"
            bot.answer_callback_query(call.id, "Invite link created!")
            send_html_safe(
                chat_id,
                f"🔗 <b>One-Click Friend Invite Link ({lim_v} msgs/day)</b>\n\n"
                f"Forward this link to your friend. As soon as they tap it, their account will activate automatically with <b>{lim_v} sessions/day</b>:\n\n"
                f"{link}",
            )
        return

    # التحقق من صلاحية العضو للأزرار العامة
    m_chk = get_or_register_member(user_obj)
    if uid != PRIMARY_OWNER_ID and m_chk["status"] != "approved":
        bot.answer_callback_query(call.id, "Private studio.")
        return

    if d.startswith("edtts:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        segs = c["voice_segs"] if c and c.get("voice_segs") else [("Jake", fb_txt[:800])]
        bot.answer_callback_query(call.id, "Preparing studio audio...")
        def _job():
            p = build_audio(segs, slow=("slow" in d))
            if p and os.path.exists(p):
                with open(p, "rb") as af:
                    bot.send_voice(chat_id, af, caption="<b>Studio Audio</b>", parse_mode="HTML")
                os.remove(p)
        threading.Thread(target=_job, daemon=True).start()
    elif d.startswith("edpdf:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        pdf_d = c["pdf_dict"] if c and c.get("pdf_dict") else {"title": "Study Dossier", "register": "Professional", "main_en": fb_txt[:1500]}
        bot.answer_callback_query(call.id, "Generating PDF dossier...")
        def _pjob():
            p = generate_pdf_sheet(pdf_d)
            if p and os.path.exists(p):
                with open(p, "rb") as pf:
                    bot.send_document(chat_id, pf, caption="<b>NYC Lounge Dossier (PDF)</b>", parse_mode="HTML")
                os.remove(p)
        threading.Thread(target=_pjob, daemon=True).start()
    elif d.startswith("edsave:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        items = c["vault_items"] if c and c.get("vault_items") else [(fb_txt.splitlines()[0][:80], "", fb_txt[:200])]
        saved_cnt = 0
        for it in items:
            if len(it) >= 3 and _save_vault_item(uid, it[0], it[1], it[2]):
                saved_cnt += 1
        sync_db_async(force=True)
        bot.answer_callback_query(call.id, f"Saved to your Vault! ({saved_cnt} new)" if saved_cnt else "Updated in your Vault!")
    elif d.startswith("edlexcard:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        target_q = terms[0] if terms else (c["source_text"][:90] if c and c.get("source_text") else fb_txt.splitlines()[0][:80])
        bot.answer_callback_query(call.id, f"Opening Lexicon: {target_q[:24]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, target_q), daemon=True).start()
    elif d.startswith("edqlk:"):
        _, idx_s, tok = d.split(":", 2)
        c = load_card(tok)
        terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        idx = int(idx_s) if idx_s.isdigit() else 0
        target_q = terms[idx] if idx < len(terms) else (terms[0] if terms else fb_txt[:60])
        bot.answer_callback_query(call.id, f"Looking up: {target_q[:24]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, target_q), daemon=True).start()
    elif d.startswith("edlexext:"):
        _, ext_type, tok = d.split(":", 2)
        c = load_card(tok)
        term = c["source_text"] if c and c.get("source_text") else fb_txt.splitlines()[0]
        bot.answer_callback_query(call.id, "Loading...")
        threading.Thread(target=execute_lexicon_extension, args=(chat_id, uid, term, ext_type), daemon=True).start()
    elif d.startswith("edchat:"):
        _, angle, tok = d.split(":", 2)
        c = load_card(tok)
        usr_orig = c["source_text"] if c and c.get("source_text") else "What do the rest of you think about this?"
        bot.answer_callback_query(call.id, "Bringing in another host...")
        threading.Thread(target=execute_chat_turn, args=(chat_id, uid, usr_orig, False, angle, first_name), daemon=True).start()
    elif d.startswith("edscen:"):
        sub = d.split(":", 1)[1]
        if sub == "menu":
            bot.answer_callback_query(call.id)
            kb = types.InlineKeyboardMarkup()
            kb.row(types.InlineKeyboardButton("Tough Negotiation", callback_data="edscen:neg"), types.InlineKeyboardButton("Thesis / Policy Defense", callback_data="edscen:def"))
            kb.row(types.InlineKeyboardButton("Project Crisis Room", callback_data="edscen:cri"), types.InlineKeyboardButton("Hot-Seat Debate", callback_data="edscen:deb"))
            send_html_safe(chat_id, "<b>High-Stakes Roleplay Arena</b>\nPick a live scenario to step into with the hosts:", reply_markup=kb)
        else:
            scen_prompts = {
                "neg": "<b>Scenario: Tough Deadline Negotiation</b>\n\n<b>Marcus</b>\nMusa, the board wants the new governance report rolled out by Friday morning, no exceptions. Tell me honestly—why are you asking to push the date back?",
                "def": "<b>Scenario: Executive &amp; Academic Defense</b>\n\n<b>Marcus</b>\nLet's put your thesis to the test, Musa. Many critics argue that municipal digital platforms are mere window dressing unless institutional culture changes first. How do you defend your model against that critique?",
                "cri": "<b>Scenario: Project Crisis Room</b>\n\n<b>Jake</b>\nAlright Musa, we've got a situation—our lead developer just called in sick two hours before the client rollout, and key features are still unverified. What's your call?",
                "deb": "<b>Scenario: The Hot-Seat Debate</b>\n\n<b>Chloe</b>\nHere's a controversial take, Musa: remote and hybrid work is quietly killing mentorship for younger professionals. Do you buy that, or is it just outdated management nostalgia?",
            }
            bot.answer_callback_query(call.id, "Scenario active!")
            msg_s = scen_prompts.get(sub, scen_prompts["neg"])
            _update_session(uid, mode="CHAT", new_turn={"u": "[Started Roleplay Scenario]", "b": re.sub(r"<[^>]+>", "", msg_s)})
            send_html_safe(chat_id, msg_s, reply_markup=build_reply_kb(uid))
    elif d.startswith("edmore:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Mining fresh expressions...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, c["mined_so_far"] if c else "previous batch", ""), daemon=True).start()
    elif d.startswith("edshift:"):
        _, sh, tok = d.split(":", 2)
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Transforming style...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, "", sh), daemon=True).start()
    elif d.startswith("edpod:"):
        h_n = int(d.split(":", 1)[1])
        bot.answer_callback_query(call.id, f"Producing {h_n}-Host episode...")
        threading.Thread(target=execute_podcast, args=(chat_id, uid, "", h_n, first_name), daemon=True).start()
    elif d.startswith("edqz:"):
        _, ch, qid = d.split(":", 2)
        with db_connect() as conn:
            r = conn.execute("SELECT quiz_json FROM quiz_v15 WHERE q_id=?", (qid,)).fetchone()
        if r:
            q = QuizSchema.model_validate_json(r[0])
            corr = q.correct_letter.strip().upper()[:1]
            bot.answer_callback_query(call.id, "Spot on!" if ch == corr else f"Best choice: {corr}")
            tok = hashlib.md5(f"QZR:{uid}:{time.time()}".encode()).hexdigest()[:10]
            store_card(tok, uid, [("Marcus", q.explanation_en)], {"title": q.category_en, "main_en": q.scenario_en, "diagnosis_en": q.explanation_en}, [(q.target_expression_en, q.explanation_en, q.scenario_en)], source_text=q.target_expression_en)
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton(f"🔍 {q.target_expression_en[:20]}", callback_data=f"edlexcard:{tok}"),
                types.InlineKeyboardButton("Save Expression", callback_data=f"edsave:{tok}"),
                types.InlineKeyboardButton("Next Question", callback_data="edqznext:standard"),
            )
            send_html_safe(
                chat_id,
                f"<b>{'✓ Correct (' + corr + ')' if ch == corr else 'Selected (' + ch + ') → Best Choice: (' + corr + ')'}</b>\n\n"
                f"<b>Why It Works</b>\n{_h(q.explanation_en)}",
                reply_markup=kb,
            )
    elif d.startswith("edqznext:"):
        qm = d.split(":", 1)[1]
        bot.answer_callback_query(call.id, "Loading question...")
        threading.Thread(target=send_quiz, args=(chat_id, uid, qm), daemon=True).start()
    elif d.startswith("edvault:"):
        act = d.split(":", 1)[1]
        with db_connect() as conn:
            rows = conn.execute("SELECT id, chunk, meaning_ar, context_sentence FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT 40", (uid,)).fetchall()
        if act == "del" and rows:
            with _db_lock, db_connect() as conn:
                conn.execute("DELETE FROM vault_v15 WHERE id=?", (rows[0][0],))
                conn.commit()
            bot.answer_callback_query(call.id, "Last item removed.")
            show_vault(chat_id, uid)
        elif act == "anki" and rows:
            bot.answer_callback_query(call.id, "Exporting Anki TSV...")
            tsv_p = f"/tmp/pdf_exports/NYC_Vault_Anki_{uid}.tsv"
            with open(tsv_p, "w", encoding="utf-8") as tf:
                for r in rows:
                    front = (r[2] or r[3] or "").replace("\t", " ").replace("\n", " ")
                    back = f"<b>{r[1]}</b><br><i>{r[3]}</i>".replace("\t", " ").replace("\n", " ")
                    tf.write(f"{front}\t{back}\n")
            with open(tsv_p, "rb") as tf:
                bot.send_document(chat_id, tf, caption="<b>Anki Flashcards Export (.tsv)</b>", parse_mode="HTML")
        elif act == "pdf" and rows:
            bot.answer_callback_query(call.id, "Generating Glossary PDF...")
            body_lines = "\n\n".join([f"• {r[1]} — {r[2]}\n  Example: {r[3]}" for r in rows])
            p = generate_pdf_sheet({"title": "Personal Expression Vault", "register": "C1/C2 Glossary", "main_en": body_lines})
            if p and os.path.exists(p):
                with open(p, "rb") as pf:
                    bot.send_document(chat_id, pf, caption="<b>Personal Vault Glossary (PDF)</b>", parse_mode="HTML")

if os.environ.get("LIVE_SELF_TEST") == "1":
    try:
        bot.remove_webhook()
    except Exception:
        pass
    sync_telegram_identity()
    oid = PRIMARY_OWNER_ID
    _update_session(oid, mode="CHAT", register="Professional", clear_hist=True)
    res_c = execute_chat_turn(
        oid,
        oid,
        "Hey Marcus can you explain me why people always do the same mistake when everything depend of one person?",
        is_voice=False,
        user_first_name="Musa",
    )
    if not res_c.dialogue_en.strip() or not res_c.recast_formal_c2_en.strip():
        raise RuntimeError("Self-test failed: Empty ChatSchema fields")
    print("   🎯 Verified Telegram Identity Sync & Chat Response:", res_c.dialogue_en[:85])
    print("   🎯 Verified Strikethrough Fixes:", res_c.error_corrections_list.replace("\n", " | "))
    print("✅ STUDIO_VERIFIED_OK", flush=True)
    os._exit(0)

if __name__ == "__main__":
    print("🚀 NYC English Lounge Polling Active...", flush=True)
    try:
        bot.remove_webhook()
    except Exception:
        pass
    sync_telegram_identity()
    try:
        send_html_safe(
            PRIMARY_OWNER_ID,
            "<b>NYC English Lounge</b>\n"
            "Your private studio is live. Tap any mode below, or use /admin anytime to invite friends and set their daily message limits.",
            reply_markup=build_reply_kb(PRIMARY_OWNER_ID),
        )
    except Exception:
        pass

    _START = time.time()
    def _timer():
        while time.time() - _START < 5.3 * 3600:
            time.sleep(25)
        sync_db_async(force=True)
        time.sleep(3)
        os._exit(0)
    threading.Thread(target=_timer, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(timeout=25, long_polling_timeout=20, skip_pending=False)
        except Exception:
            time.sleep(3)
