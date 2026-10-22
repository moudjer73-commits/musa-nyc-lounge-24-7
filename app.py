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
# قاعدة البيانات ونظام الأعضاء والحصص اليومية
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

def _english_name(user_obj: Any) -> str:
    """ضمان كتابة اسم المستخدم بالحروف اللاتينية داخل الحوار الإنجليزي (موسى -> Musa)"""
    uid = int(getattr(user_obj, "id", PRIMARY_OWNER_ID)) if user_obj else PRIMARY_OWNER_ID
    if uid == PRIMARY_OWNER_ID:
        return "Musa"
    raw = (getattr(user_obj, "first_name", "") or "").strip()
    ar_map = {
        "موسى": "Musa", "محمد": "Mohammed", "أحمد": "Ahmed", "احمد": "Ahmed",
        "علي": "Ali", "عمر": "Omar", "خالد": "Khaled", "أمين": "Amine",
        "امين": "Amine", "أمينة": "Amina", "امينة": "Amina", "ياسين": "Yacine",
        "كريم": "Karim", "سارة": "Sara", "مريم": "Maryam", "يوسف": "Youssef",
    }
    if raw in ar_map:
        return ar_map[raw]
    if re.search(r"[\u0600-\u06FF]", raw):
        uname = (getattr(user_obj, "username", "") or "").strip()
        if uname and re.match(r"^[A-Za-z]+", uname):
            return re.match(r"^[A-Za-z]+", uname).group(0).capitalize()
        return "my friend"
    return raw if raw else "friend"

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
                register_level TEXT DEFAULT 'Semi-formal',
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
        conn.execute("""
            INSERT OR IGNORE INTO members_v17 (user_id, full_name, username, status, daily_limit, used_today, last_day, total_used)
            VALUES (?, 'Musa (Host)', 'owner', 'owner', -1, 0, ?, 0)
        """, (PRIMARY_OWNER_ID, _today_str()))
        conn.execute("UPDATE members_v17 SET status='owner', daily_limit=-1 WHERE user_id=?", (PRIMARY_OWNER_ID,))
        conn.execute("DELETE FROM vault_v15 WHERE chunk LIKE 'Productivity Paradoxes:%' OR chunk LIKE '%Management chose%'")
        conn.execute("UPDATE session_v15 SET history_json='[]' WHERE history_json LIKE '%Management chose%'")
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

def sync_telegram_identity():
    global _BOT_USERNAME
    try:
        me = bot.get_me()
        _BOT_USERNAME = me.username or ""
    except Exception:
        pass
    try:
        bot.set_my_name("Musa’s NYC Lounge")
        desc = (
            "Musa’s NYC Lounge — Private B2–C2 American English Studio\n\n"
            "• Chat: Live NYC conversation & roleplay with Jake, Chloe & Marcus across Casual, Professional & Formal registers.\n"
            "• Lexicon: Deep EN ⇄ AR lookup with corpus badges, grammar frames & collocations.\n"
            "• Study & Miner: Extract idioms & C2 templates, or apply Soften, Condense, Spoken & C2 edits.\n"
            "• Podcast: Ear-first 2 & 3-host episodes with normal/slow studio audio.\n"
            "• Quiz, Vault & Radar: Active recall, error radar & PDF/Anki exports."
        )
        short_d = "Private B2–C2 American English studio — live NYC chat, deep EN⇄AR lexicon, C2 editing & multi-host podcasts."
        bot.set_my_description(desc)
        bot.set_my_short_description(short_d)
    except Exception:
        pass
    try:
        member_cmds = [
            types.BotCommand("chat", "Join the live NYC group chat"),
            types.BotCommand("lexicon", "Contextual Lexicon & Activator (EN/AR)"),
            types.BotCommand("study", "Mine articles, edit drafts, or compare nuances"),
            types.BotCommand("podcast", "Multi-host studio episodes & call-in"),
            types.BotCommand("quiz", "Collocation & C2 transformation drills"),
            types.BotCommand("vault", "Saved expressions & PDF/Anki export"),
            types.BotCommand("radar", "Your personal mistake radar & fixes"),
            types.BotCommand("guide", "Studio user guide"),
        ]
        bot.set_my_commands(member_cmds, scope=types.BotCommandScopeDefault())
        owner_cmds = member_cmds + [types.BotCommand("admin", "Manage friends, daily quotas & invites")]
        bot.set_my_commands(owner_cmds, scope=types.BotCommandScopeChat(PRIMARY_OWNER_ID))
    except Exception:
        pass

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
    return {"mode": "CHAT", "register": "Semi-formal", "podcast_hosts": 3, "sub_state": "", "history": []}

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
            h_v = h_v[-10:]
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
    term = re.sub(r"\*+", "", (raw_term or "").strip())
    term = re.sub(r"^[•\-\*\d\.\)\s]+", "", term).strip()
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
    clean_d = re.sub(r"\*+", "", (raw_def or "").strip())
    return term[:120], clean_d[:400], (raw_ctx or "").strip()[:450]

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
                    w, r = pts[0].strip("•~*- "), pts[1].strip("•~*- ")
                    if w and r and len(w) > 2:
                        conn.execute("INSERT INTO dna_v15 (user_id, error_pattern, native_fix) VALUES (?, ?, ?)", (user_id, w[:120], r[:120]))
            conn.commit()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# تنسيق النصوص نظيفاً من بقايا النجوم (**) ومحرك الصوت بالتنفس الطبيعي
# ------------------------------------------------------------------------------
STUDIO_VOICES = {
    "Jake": "en-US-AndrewMultilingualNeural",
    "Chloe": "en-US-AvaMultilingualNeural",
    "Marcus": "en-US-BrianMultilingualNeural",
}

def _h(v: Any) -> str:
    raw = html.unescape(str(v if v is not None else "")).strip()
    return html.escape(raw, quote=False)

def _fmt_inline_md(raw_line: str) -> str:
    """تحويل **الكلمة** إلى <b>الكلمة</b> وحذف أي نجوم مفرطة مشوهة"""
    escaped = _h(raw_line)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = escaped.replace("**", "")
    return escaped

def _clean_multiline_bullets(raw: str) -> str:
    txt = html.unescape(str(raw or "")).strip()
    if not txt:
        return ""
    txt = re.sub(r"\s*\|\s*", "\n", txt)
    txt = re.sub(r"(?<!^)\s*(•|\u2022)\s*", "\n• ", txt)
    lines = []
    for ln in txt.splitlines():
        cl = ln.strip().lstrip("•-* ").strip()
        if cl:
            lines.append(f"• {_fmt_inline_md(cl)}")
    return "\n".join(lines)

def _clean_tts(t: str) -> str:
    t = html.unescape(str(t or ""))
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]+>|\*+|[\u0600-\u06FF]+", " ", t)
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

def _get_silence_mp3() -> Optional[str]:
    """توليد وقفة تنفس بشرية (380ms) لفصل أصوات المتحدثين بشكل طبيعي"""
    sil_p = "/tmp/tts_cache_hd/breath_pause_380ms.mp3"
    if os.path.exists(sil_p) and os.path.getsize(sil_p) > 200:
        return sil_p
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.38", "-b:a", "128k", sil_p],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        return sil_p if os.path.exists(sil_p) else None
    except Exception:
        return None

def build_audio(segments: List[Tuple[str, str]], slow: bool = False) -> Optional[str]:
    rate = "-15%" if slow else "+0%"
    valid = [(s, _clean_tts(re.sub(r"^\s*(Jake|Chloe|Marcus)\s*:\s*", "", t))) for s, t in segments if len(_clean_tts(t)) > 2]
    if not valid:
        return None
    h_k = hashlib.md5(f"V18_BREATH::{rate}::{'||'.join([f'{s}:{t}' for s,t in valid])}".encode()).hexdigest()
    cache_p = f"/tmp/tts_cache_hd/{h_k}.mp3"
    if os.path.exists(cache_p) and os.path.getsize(cache_p) > 800:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.close()
        shutil.copyfile(cache_p, tmp.name)
        return tmp.name

    parts, list_f, out_p = [], tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name, tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    sil_file = _get_silence_mp3()
    try:
        for idx, (spk, txt) in enumerate(valid):
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
                if sil_file and idx < len(valid) - 1:
                    parts.append(sil_file)
        if not parts:
            return None
        with open(list_f, "w") as lf:
            for pf in parts:
                lf.write(f"file '{pf}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_f, "-ar", "48000", "-ac", "1", "-b:a", "128k", out_p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        for pf in parts:
            if pf != sil_file:
                try:
                    os.remove(pf)
                except Exception:
                    pass
        try:
            os.remove(list_f)
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
    <div class="hdr"><h2>Musa’s NYC Lounge — {_h(pdf_data.get('title', 'Study Dossier'))} ({_h(pdf_data.get('register', 'Professional'))})</h2></div>
    <div class="sec"><b>Core Content:</b><br>{_h(pdf_data.get('main_en', '')).replace(chr(10), '<br>')}<br><br><b>Refinements &amp; Notes:</b><br>{_h(pdf_data.get('instant_fix', '')).replace(chr(10), '<br>')}</div>
    <div class="sec"><b>Spoken &amp; Written Variants:</b><br>• Spoken/Casual: {_h(pdf_data.get('spoken_inf', ''))}<br>• Workplace/Diplomatic: {_h(pdf_data.get('spoken_semi', ''))}<br>• Analytical/Written (C2): {_h(pdf_data.get('written_form', ''))}<br><br>{_h(pdf_data.get('diagnosis_en', '')).replace(chr(10), '<br>')}</div>
    {ar_block}
    </body></html>"""
    out_p = f"/tmp/pdf_exports/NYC_Lounge_{int(time.time())}.pdf"
    try:
        weasyprint.HTML(string=html_doc).write_pdf(out_p)
        return out_p if os.path.exists(out_p) else None
    except Exception:
        return None


# ------------------------------------------------------------------------------
# محرك الاتصال السريع بالنماذج اللغوية (LLM Structured Caller)
# ------------------------------------------------------------------------------
_PINNED_GROQ: Optional[Tuple[str, str]] = None
# ------------------------------------------------------------------------------
# محرك Gemini النصي الموثق (Pure Verified Text Models — Zero Image Models)
# ------------------------------------------------------------------------------
_PINNED_GEMINI: Optional[Tuple[str, str]] = None
_DEAD_KEYS_UNTIL: Dict[str, float] = {}
_UNAVAILABLE_MODELS_UNTIL: Dict[str, float] = {}
_LIVE_MODELS_CACHE: List[str] = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite", "gemini-3.1-flash-live-preview"]

def _get_live_gemini_models(client: genai.Client) -> List[str]:
    global _LIVE_MODELS_CACHE
    now = time.time()
    avail = [m for m in _LIVE_MODELS_CACHE if _UNAVAILABLE_MODELS_UNTIL.get(m, 0) < now]
    if avail:
        return avail
    found = []
    try:
        for m in client.models.list():
            sn = (getattr(m, "name", "") or "").replace("models/", "")
            low = sn.lower()
            bad_w = ("image", "imagen", "veo", "embedding", "aqa", "tts", "audio", "learnlm", "robotics", "computer-use", "gemma")
            if "gemini" in low and not any(b in low for b in bad_w):
                found.append(sn)
    except Exception:
        pass
    if found:
        _LIVE_MODELS_CACHE = found[:8]
    return _LIVE_MODELS_CACHE

def _is_populated(obj: Any) -> bool:
    d = obj.model_dump() if hasattr(obj, "model_dump") else {}
    for col in (
        "primary_reply_en", "unified_senses_en_ar", "polished_version_en",
        "spoken_american_en", "top_chunks_en_ar", "direct_feedback_or_answer_en",
        "script_en", "on_air_host_response_en", "scenario_or_stem_en", "evaluation_and_score_en",
        "transformed_text_en", "new_expressions_en", "content_bullets_en",
    ):
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

def _try_single_gemini_call(api_key: str, model_id: str, sys_p: str, usr_p: str, schema_cls: Any, temp: float) -> Any:
    mc = genai.Client(api_key=api_key, http_options={"timeout": 22000})
    cfg_kwargs: Dict[str, Any] = {
        "system_instruction": sys_p,
        "response_mime_type": "application/json",
        "response_schema": schema_cls,
        "temperature": temp,
    }
    if "flash" in model_id.lower() and "thinking" not in model_id.lower():
        try:
            cfg_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
    try:
        resp = mc.models.generate_content(
            model=model_id,
            contents=usr_p,
            config=genai_types.GenerateContentConfig(**cfg_kwargs),
        )
        if resp and resp.text:
            return _parse_schema_safe(resp.text, schema_cls)
    except Exception as e1:
        if "thinking" in str(e1).lower() or "invalid" in str(e1).lower():
            cfg_kwargs.pop("thinking_config", None)
            resp2 = mc.models.generate_content(
                model=model_id,
                contents=usr_p,
                config=genai_types.GenerateContentConfig(**cfg_kwargs),
            )
            if resp2 and resp2.text:
                return _parse_schema_safe(resp2.text, schema_cls)
        raise e1
    raise ValueError("Empty Gemini response")

def call_llm_structured(sys_p: str, usr_p: str, schema_cls: Any, temp: float = 0.55) -> Any:
    global _PINNED_GEMINI
    f_names = list(schema_cls.model_fields.keys())
    f_desc = "\n".join([f'- "{k}": {v.description}' for k, v in schema_cls.model_fields.items()])
    full_sys = (
        f"{sys_p}\n\nReturn strictly a valid JSON object matching the schema with ALL of these exact keys:\n{f_desc}\n"
        f"Required keys: {json.dumps(f_names)}. Every string field must be a single cohesive string."
    )

    now = time.time()
    if _PINNED_GEMINI:
        pk, pm = _PINNED_GEMINI
        if _DEAD_KEYS_UNTIL.get(pk, 0) < now and _UNAVAILABLE_MODELS_UNTIL.get(pm, 0) < now:
            try:
                return _try_single_gemini_call(pk, pm, full_sys, usr_p, schema_cls, temp)
            except Exception:
                _PINNED_GEMINI = None

    keys = _collect_gemini_keys()
    active_keys = [k for k in keys if _DEAD_KEYS_UNTIL.get(k, 0) < now]
    if not active_keys:
        _DEAD_KEYS_UNTIL.clear()
        active_keys = keys

    models_list = [m for m in _LIVE_MODELS_CACHE if _UNAVAILABLE_MODELS_UNTIL.get(m, 0) < now]
    if not models_list:
        _UNAVAILABLE_MODELS_UNTIL.clear()
        models_list = _LIVE_MODELS_CACHE

    for gm_id in models_list:
        for mk in active_keys:
            try:
                res = _try_single_gemini_call(mk, gm_id, full_sys, usr_p, schema_cls, temp)
                _PINNED_GEMINI = (mk, gm_id)
                return res
            except Exception as e:
                err_s = str(e).lower()
                if any(x in err_s for x in ("404", "not found", "503", "504", "timeout", "timed out", "overloaded", "unavailable", "deadline")):
                    _UNAVAILABLE_MODELS_UNTIL[gm_id] = time.time() + 180
                    break
                if any(x in err_s for x in ("429", "quota", "exhausted", "resource_exhausted")):
                    _DEAD_KEYS_UNTIL[mk] = time.time() + 60
                    continue
                break

    return schema_cls()

# ------------------------------------------------------------------------------
# القوالب الرشيقة المعزولة (Lean Modular Schemas)
# ------------------------------------------------------------------------------
# ==============================================================================
# [V24 MASTER ENGINE] — UNIFIED SCHEMAS, ZERO-REDUNDANCY UI & PURE AUDIO/VAULT
# ==============================================================================

# ترقية منظف الصوت ليضمن عدم نطق أي حرف عربي أو رمز غريب بواسطة المذيعين الإنجليز
def _clean_tts(raw_text: str) -> str:
    t = html.unescape(str(raw_text or ""))
    t = re.sub(r"<[^>]+>", " ", t)
    # إزالة أي حروف عربية أو رموز تصحيح من النص المرسل لمحرك الصوت الإنجليزي
    t = re.sub(r"[\u0600-\u06FF]+", " ", t)
    t = re.sub(r"[❌✅➔→•▪▸🎉🔥💎🎙️🎧🐢📄💾🔍🌳⚖️🧬🔄⛏️🏗️🔬✍️🌉🎓📞🎬📜🏆]", " ", t)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"\*+|_+|`+|~+", "", t)
    t = re.sub(r"\b(?:Jake|Chloe|Marcus)\s*:\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _split_dialogue_segments(raw_script: str, default_speaker: str = "Chloe") -> List[Tuple[str, str]]:
    clean = html.unescape(str(raw_script or "")).strip()
    clean = re.sub(r"\*+\s*(Jake|Chloe|Marcus)\s*\*+\s*:", r"\1:", clean)
    clean = re.sub(r"(?:^|\n|\s)+(Jake|Chloe|Marcus)\s*:\s*", r"\n\1: ", clean).strip()
    segs: List[Tuple[str, str]] = []
    for ln in clean.splitlines():
        s_ln = ln.strip().lstrip("•-*🎙️ ").strip()
        if not s_ln:
            continue
        m = re.match(r"^(Jake|Chloe|Marcus)\s*:\s*(.+)$", s_ln)
        if m:
            spk, spoken = m.group(1), _clean_tts(m.group(2))
            if len(spoken) > 2:
                segs.append((spk, spoken))
        else:
            spoken = _clean_tts(s_ln)
            if len(spoken) > 2:
                segs.append((default_speaker, spoken))
    return segs if segs else [(default_speaker, _clean_tts(raw_script)[:600])]

def _extract_clean_vault_items(raw_block: str, fallback_context: str = "") -> List[Tuple[str, str, str]]:
    """فلتر صارم للخزنة: يستخرج فقط العبارات والمصطلحات الإنجليزية النظيفة (2-6 كلمات) مع شرحها ومثالها"""
    items: List[Tuple[str, str, str]] = []
    if not raw_block:
        return items
    lines = [ln.strip() for ln in str(raw_block).splitlines() if ln.strip()]
    for idx, ln in enumerate(lines):
        # التقاط العبارات الموضوعة بين **...** أو بعد ترقيم 1. / 2. أو بعد سهم الترقية ➔
        cand_term = ""
        if "➔" in ln or "->" in ln:
            rhs = re.split(r"➔|->", ln, maxsplit=1)[1]
            m_b = re.search(r"\*\*(.+?)\*\*", rhs)
            cand_term = m_b.group(1).strip() if m_b else rhs.split("—")[0].split("(")[0].strip()
        else:
            m_b = re.search(r"\*\*(.+?)\*\*", ln)
            if m_b:
                cand_term = m_b.group(1).strip()
            elif re.match(r"^[1-9]\.\s+", ln):
                cand_term = re.sub(r"^[1-9]\.\s+", "", ln).split("—")[0].split("-")[0].split("[")[0].strip()

        cand_term = re.sub(r"^[1-9]\.\s*", "", cand_term).strip(" •-*'\"`():")
        # استبعاد العناوين الإدارية أو الجمل الطويلة أو الكلمات العربية
        words = cand_term.split()
        if not (1 <= len(words) <= 7) or len(cand_term) > 48:
            continue
        if re.search(r"[\u0600-\u06FF]", cand_term):
            continue
        if any(bad in cand_term.lower() for bad in ("pattern", "spoken", "written", "fix", "upgrade", "call-in", "sentence", "option", "synonym", "collocation", "literal")):
            continue

        # جمع المعنى والمثال من نفس السطر أو السطرين التاليين
        detail_lines = [ln]
        if idx + 1 < len(lines) and not re.match(r"^[1-9]\.", lines[idx + 1]):
            detail_lines.append(lines[idx + 1])
        if idx + 2 < len(lines) and not re.match(r"^[1-9]\.", lines[idx + 2]):
            detail_lines.append(lines[idx + 2])
        joined_detail = " | ".join([re.sub(r"\*+", "", x).strip() for x in detail_lines])
        t_clean, m_clean, c_clean = _clean_vault_entry(cand_term, joined_detail[:240], fallback_context[:220])
        if t_clean and all(t_clean.lower() != existing[0].lower() for existing in items):
            items.append((t_clean, m_clean, c_clean))
    return items[:5]

# ------------------------------------------------------------------------------
# القوالب اللغوية الموحدة (Pydantic Schemas)
# ------------------------------------------------------------------------------
class ChatSchema(BaseModel):
    primary_speaker: str = Field(default="Chloe", description="MUST be one of the currently ACTIVE hosts in `ACTIVE_HOSTS_IN_ROOM`. Speak naturally in first person ('I/me').")
    primary_reply_en: str = Field(default="", description="Spontaneous, natural human response (1-3 sentences) directly engaging with what the user said. Speak like a real NYC friend—no sitcom catchphrases, no 'Truth be told / Fair enough', no 'management/HR' jokes!")
    second_speaker: str = Field(default="", description="MUST be '' if only 1 host is active! If 2 or 3 hosts are active, leave '' on most turns unless a brief 1-sentence chime-in from another ACTIVE host genuinely adds value.")
    second_reply_en: str = Field(default="", description="Leave '' if second_speaker is empty.")
    has_actual_errors: bool = Field(default=False, description="Check ONLY `CURRENT_USER_MESSAGE`. True ONLY for genuine grammar, word-order, uncountable noun ('informations'), preposition ('discuss about', 'depend of', 'explain me'), or collocation ('dominate the language', 'one to one') errors. Note: 'discuss X with Y' is 100% CORRECT! Ignore casual texting ('cuz', 'wanna', 'gonna') and capitalization!")
    error_corrections_list: str = Field(default="", description="If has_actual_errors is True on `CURRENT_USER_MESSAGE`, write 'wrong phrase -> right phrase'. Leave '' if error-free.")
    user_line_natural_rephrase_en: str = Field(default="", description="How a native American naturally says `CURRENT_USER_MESSAGE` from the USER'S perspective. If already natural, write '✓ Natural as is — or: \"[1 smooth alternative]\"'.")
    expressions_from_reply_en: str = Field(default="", description="1 or 2 natural expressions actually used in the reply on separate lines: • **expression** — concise meaning (المعنى بالعربية). 'Natural example sentence.'")

class LexiconSchema(BaseModel):
    headword_en_or_ar: str = Field(default="", description="The target English word/phrasal verb/idiom OR Arabic query")
    ipa_and_spoken_prosody: str = Field(default="", description="American IPA with stress marks (e.g. '/ˌθroʊ əˈraʊnd/') + brief connected-speech note")
    grammar_valency_and_coca_badge: str = Field(default="", description="Explicit grammar code (e.g. 'Phrasal Verb [T, Separable: throw sth ↔ around]' or 'Noun [U]') + COCA register frequency ('Spoken: ★★★ | Workplace: ★★☆ | Academic: ★☆☆')")
    unified_senses_en_ar: str = Field(default="", description="Numbered sense blocks (or Register Activator tiers if query is Arabic). For EACH sense:\n1. **Precise English Definition & Tone**\n• المقابل الفصيح والاصطلاحي: [Eloquent contextual Arabic + idiomatic Arabic]\n• Valency Frame: `[exact syntactic pattern]`\n• Spoken Example: \"[Natural 10-14 word spoken sentence]\"\n• Written Example: \"[Natural 12-16 word executive/academic sentence]\"")
    oxford_collocation_box_en: str = Field(default="", description="Oxford-style Collocation Box on separate lines:\n• **Object / Noun Partners:** ...\n• **Verb / Modal Triggers:** ...\n• **Adverb / Adjective Modifiers:** ...\n• **Prepositions & Particles:** ...")
    lexical_priming_and_chunks_en: str = Field(default="", description="Hoey's Lexical Priming & Ready Chunks on separate lines:\n• **Colligation & Priming:** (preferred tense/aspect/polarity/position)\n• **Spoken Frame:** \"[Ready conversational chunk]\" (المعنى)\n• **Executive Frame:** \"[Ready written chunk]\" (المعنى)")
    prosody_and_subtext_en: str = Field(default="", description="Semantic Prosody (Positive/Neutral/Negative aura) + Pragmatic Subtext")
    register_ladder_and_family_en: str = Field(default="", description="On separate lines:\n• **Casual Spoken:** [equivalent]\n• **Diplomatic Workplace:** [equivalent]\n• **C2 Academic Prose:** [equivalent]\n• **Word Family:** [Noun/Verb/Adj/Adv forms]\n• **Antonym:** [exact opposite]")
    common_traps_and_cognitive_hook_en: str = Field(default="", description="On separate lines:\n• **Grammar/Pronoun Trap:** ❌ [wrong] ➔ ✅ [right]\n• **Collocation/L1 Arabic Trap:** ❌ [wrong] ➔ ✅ [right]\n• **Cognitive Metaphor Hook:** [1-sentence mental image explaining why this expression means what it means]")
    clean_audio_script_en: str = Field(default="", description="3-turn pure English script ('Marcus: ...\nChloe: ...\nJake: ...') pronouncing the headword and reading the examples aloud (NO Arabic or symbols!).")

class LexiconExtraSchema(BaseModel):
    title_en: str = Field(default="Synonym Contrast & Nuance Boundaries", description="Title")
    content_bullets_en: str = Field(default="", description="4-5 detailed blocks on separate lines ('\n') contrasting the term with its closest synonyms, including register, natural examples, Arabic nuances, and a ❌ vs ✅ boundary test.")

class PrecisionEditorSchema(BaseModel):
    polished_version_en: str = Field(default="", description="Natural polish of the user's text strictly matching the ACTIVE REGISTER (Casual = everyday spoken idiom; Professional = tactful workplace/executive phrasing; Formal = elevated C1/C2 formal prose). CRITICAL: Keep the user's exact meaning and pronoun perspective (1st person stays 1st person—never turn 'I feel tired' into an impersonal medical statement!).")
    surgical_diffs_en_ar: str = Field(default="", description="ONLY real grammar, preposition, or collocation errors formatted as '• ❌ [wrong] ➔ ✅ **[fix]** — [reason]'. If the user's sentence is grammatically correct (like 'I feel so tired'), MUST write strictly: '• ✓ Grammatically solid—see register-specific upgrades below.' (NEVER mark a grammatically correct sentence with ❌!).")
    lexical_upgrades_en_ar: str = Field(default="", description="3 vocabulary/collocation upgrades tailored strictly to the ACTIVE REGISTER on separate lines:\n• [user's word] ➔ **[Register-matched upgrade]** ([Eloquent Modern Standard Arabic meaning ONLY—never street slang and never write 'مناسبة لكذا' instead of the meaning!]) — \"[Natural example sentence in this exact register]\"")
    your_turn_prompt_en: str = Field(default="", description="1-sentence prompt inviting the user to write a new sentence using one of the 3 upgrades above.")

class ArabicBridgeSchema(BaseModel):
    spoken_american_en: str = Field(default="", description="How a native American naturally says this Arabic thought aloud in conversation (active verbs, contractions).")
    executive_written_en: str = Field(default="", description="Crisp, diplomatic workplace written version.")
    c2_academic_prose_en: str = Field(default="", description="Authoritative C2 academic/analytical prose keeping the intended meaning without artificial purple prose.")
    building_blocks_and_trap_en_ar: str = Field(default="", description="On separate lines:\n• **[English Chunk 1]:** [المعنى الفصيح الرصين]\n• **[English Chunk 2]:** [المعنى الفصيح الرصين]\n• ❌ **Literal Arabic Calque Trap:** [wrong literal translation] ➔ ✅ **[Native phrasing]**")
    your_turn_prompt_en: str = Field(default="", description="1-sentence prompt inviting the user to write a sentence in English using one of the building blocks.")

class ModularMinerSchema(BaseModel):
    theme_title_en: str = Field(default="Close-Reading Harvest", description="Short 3-5 word title")
    top_chunks_en_ar: str = Field(default="", description="Top 4 standout idioms, phrasal verbs, or C1/C2 collocations from the text. Format EACH as a numbered block:\n1. **[Exact Expression]** — `[Grammar Frame]`\n• المعنى السياقي: [Eloquent MSA equivalent]\n• New Example: \"[Fresh 10-14 word sentence on a different topic]\"")
    your_turn_prompt_en: str = Field(default="", description="1-sentence challenge asking the user to reply with their own sentence using one of the 4 expressions.")

class TutorLoopSchema(BaseModel):
    title_en: str = Field(default="Nuance & Practice Clinic", description="Short specific title")
    direct_feedback_or_answer_en: str = Field(default="", description="Direct, grounded explanation or evaluation of the user's practice attempt. NEVER say 'in elite circles'! Never mark a grammatically valid sentence with ❌ unless there is a real error. Use pure Modern Standard Arabic for any Arabic anchor (no typos or street slang!).")
    register_or_nuance_examples_en_ar: str = Field(default="", description="3 sharp bullets showing how to express the exact idea across:\n• **Casual Spoken:** \"[example]\" — (**[target chunk]**: [المعنى بالعربية الفصحى])\n• **Executive Workplace:** \"[example]\" — (**[target chunk]**: [المعنى بالعربية الفصحى])\n• **Formal / C2 Prose:** \"[example keeping the same speaker perspective]\" — (**[target chunk]**: [المعنى بالعربية الفصحى])")
    next_step_challenge_en: str = Field(default="", description="1-sentence follow-up challenge.")

class DraftShiftSchema(BaseModel):
    shift_title_en: str = Field(default="Style Transformation", description="Title")
    transformed_text_en: str = Field(default="", description="The rewritten text")
    key_refinements_en: str = Field(default="", description="3 concise bullets explaining the lexical, syntactic, or rhetorical upgrades made (with brief Arabic meanings).")

class MoreMinerSchema(BaseModel):
    supplement_title_en: str = Field(default="Deep Harvest", description="Title")
    new_expressions_en: str = Field(default="", description="3-4 rich numbered blocks separated by blank lines.")

class PodcastSchema(BaseModel):
    episode_title_en: str = Field(default="NYC Studio Roundtable", description="Compelling podcast episode title")
    episode_summary_and_ear_cue_en: str = Field(default="", description="2 sharp sentences setting up the core tension of the episode and what contrast to listen for.")
    script_en: str = Field(default="", description="6-7 rich, natural NYC podcast turns. CRITICAL SPOKEN RULES:\n1) Each turn MUST be 2-3 natural spoken sentences.\n2) ALWAYS use natural spoken contractions ('it's', 'can't', 'don't', 'we've', 'there's', 'you're'—NEVER robotic 'it is not', 'you cannot'!).\n3) Match vocabulary to the active register.\n4) Ban cliché agreement openers ('Spot on', 'Exactly', 'True, but'). Have hosts challenge each other with concrete examples!\n5) Highlight 4 high-value idioms/phrasal verbs in **bold** inside the dialogue.")
    fast_speech_and_chunks_en_ar: str = Field(default="", description="Break down the 4 bolded high-value idioms/phrasal verbs from script_en. Format EACH as a numbered block:\n1. **[Exact Expression]** — `[Real American Connected-Speech Guide: explain actual Flap-T, consonant linking, dropped 'g', or schwa /ə/ reduction—NEVER write hyphenated spelling like 'draw-the-line'!]`\n• المعنى السياقي: [Eloquent contextual Arabic meaning]\n• Pragmatic Subtext: [Why the speaker chose this exact chunk here]")
    on_air_call_in_question_en: str = Field(default="", description="Direct question inviting Musa to share his own perspective from his real life/work (NEVER assume Musa lives in New York!).")

class PodcastCallInSchema(BaseModel):
    on_air_host_response_en: str = Field(default="", description="2-host live studio reaction to Musa's call-in ('Chloe: ...\nMarcus: ...'), engaging deeply with his point using natural contractions and spoken rhythm.")
    call_in_language_polish_en_ar: str = Field(default="", description="On separate lines:\n• **Your Call-In Polished:** '[Natural native upgrade of what Musa said]'\n• **Fixes / Upgrades:** [Any ❌ ➔ ✅ fix, or '✓ Spot-on delivery!']\n• **Host Power Chunk:** **[expression]** — [Arabic meaning]")

class QuizSchema(BaseModel):
    quiz_mode_badge_en: str = Field(default="Collocation & Valency Precision", description="Exact badge for the drill mode")
    scenario_or_stem_en: str = Field(default="", description="For Collocation/Pragmatics/Vault: a realistic C1/C2 sentence with a ____ blank (or situational prompt). For C2 Transformation: 'Base Sentence: ...\nKEY WORD: **[WORD]**\nComplete the second sentence using 3-6 words including the KEY WORD so it means the same:'")
    option_a: str = Field(default="", description="Option A")
    option_b: str = Field(default="", description="Option B")
    option_c: str = Field(default="", description="Option C")
    option_d: str = Field(default="", description="Option D")
    correct_letter: str = Field(default="B", description="'A', 'B', 'C', or 'D'")
    winning_reason_en_ar: str = Field(default="", description="Why the correct option wins (exact collocation, syntactic frame, register fit, and eloquent Arabic meaning).")
    distractor_anatomy_en: str = Field(default="", description="Explain briefly on separate lines why EACH of the other 3 options fails.")
    complete_native_sentence_en: str = Field(default="", description="The complete, final winning sentence written out cleanly so it can be read aloud by TTS audio.")
    target_expression_en: str = Field(default="", description="The core 2-5 word winning expression or collocation to save/lookup.")

class ActiveQuizEvalSchema(BaseModel):
    evaluation_and_score_en: str = Field(default="", description="Direct assessment of the user's sentence (e.g. '9/10 — Natural & accurate!') + concise explanation.")
    surgical_fix_and_model_en_ar: str = Field(default="", description="On separate lines:\n• **Your Sentence Polished:** '[100% natural native version]'\n• **Fix / Nuance:** [❌ ➔ ✅ or why it works + Arabic note]\n• **Bonus C2 Variation:** '[High-level executive/academic version]'")

# ------------------------------------------------------------------------------
# الدستور اللغوي العام (System Prompt)
# ------------------------------------------------------------------------------
SYS_PROMPT = """
You are the linguistic engine of 'Musa’s NYC Lounge', an elite American English studio.
In Chat and Podcast modes, you portray three sharp New York friends:
- Chloe: perceptive, warm, articulate media strategist in Manhattan.
- Jake: straightforward, witty, pragmatic product lead in Brooklyn.
- Marcus: calm, thoughtful strategy & policy thinker.

STRICT LAWS:
1. NATURAL HUMAN ENGLISH & ZERO CLICHÉS:
   - Never use 'Truth be told', 'Fair enough', 'Management chose to', 'Spot on', 'I hear you', 'That makes total sense', or 'Let's unpack that'.
   - Never pretend you are employees on a shift or mention HR/management.
   - In Spoken dialogue (Chat & Podcast), always use natural spoken contractions ('it's', 'can't', 'don't', 'we've', 'there's').
2. OBEY THE ACTIVE ROOM ROSTER:
   - In Chat mode, ONLY characters listed in `ACTIVE_HOSTS_IN_ROOM` may speak.
3. SURGICAL ERROR CHECK (CURRENT MESSAGE ONLY):
   - Evaluate ONLY `CURRENT_USER_MESSAGE`. Ignore casual texting ('cuz', 'wanna', 'gonna') and capitalization.
   - 'discuss [something] with [someone]' is 100% CORRECT—only flag 'discuss about'.
"""

# ------------------------------------------------------------------------------
# إدارة الحضور الديناميكي في الشات (1 أو 2 أو 3 شخصيات)
# ------------------------------------------------------------------------------
ALL_HOSTS = ["Chloe", "Jake", "Marcus"]

def _get_active_roster(user_id: int) -> List[str]:
    sub = _get_session(user_id).get("sub_state", "") or ""
    if sub.startswith("ROSTER:"):
        names = [x.strip() for x in sub.replace("ROSTER:", "").split(",") if x.strip() in ALL_HOSTS]
        if names:
            return names
    return ["Chloe", "Jake", "Marcus"]

def _set_active_roster(user_id: int, hosts: List[str]) -> List[str]:
    valid = [h for h in ALL_HOSTS if h in hosts]
    if not valid:
        valid = ["Chloe"]
    _update_session(user_id, sub_state="ROSTER:" + ",".join(valid))
    return valid

def _update_roster_from_text(user_id: int, user_text: str) -> List[str]:
    roster = list(_get_active_roster(user_id))
    low = user_text.lower()
    mentioned = [h for h in ALL_HOSTS if h.lower() in low]

    if any(p in low for p in ("only wanna talk to", "only want to talk to", "just wanna talk to", "just want to talk to", "leave us alone", "one-on-one", "one to one", "talk to just one", "alone with")):
        return _set_active_roster(user_id, mentioned if mentioned else [roster[0]])

    if any(p in low for p in ("shut up", "take him away", "take marcus away", "take jake away", "don't butt in", "dont butt in", "don't but in", "get out", "leave the chat", "go away", "kick out")):
        if mentioned:
            new_r = [h for h in roster if h not in mentioned]
            return _set_active_roster(user_id, new_r if new_r else ["Chloe"])

    if any(p in low for p in ("where is", "where's", "bring him", "bring marcus", "bring jake", "bring chloe", "come here", "are you there", "what about you", "join us", "call marcus", "call jake")):
        for m in mentioned:
            if m not in roster:
                roster.append(m)
        return _set_active_roster(user_id, roster)

    if len(mentioned) == 1 and mentioned[0] not in roster and not any(x in low for x in ("not ", "don't", "dont", "without")):
        roster.append(mentioned[0])
        return _set_active_roster(user_id, roster)

    return roster

# ------------------------------------------------------------------------------
# لوحات المفاتيح الرشيقة والخالية من أي تكرار (Zero-Redundancy Keyboards)
# ------------------------------------------------------------------------------
def build_reply_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    s = _get_session(user_id)
    m, r = s["mode"], s["register"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=5)
    kb.row(
        types.KeyboardButton("Informal •" if r == "Informal" else "Informal"),
        types.KeyboardButton("Semi-formal •" if r == "Semi-formal" else "Semi-formal"),
        types.KeyboardButton("Formal •" if r == "Formal" else "Formal"),
        types.KeyboardButton("💎 Vault"),
        types.KeyboardButton("📡 Radar"),
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
            t = it[0].strip()
            if 2 <= len(t) <= 32 and t not in terms:
                terms.append(t)
    return terms

def build_inline_kb(tok: str, ctype: str = "chat", lookup_terms: Optional[List[str]] = None, roster: Optional[List[str]] = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    l_terms = (lookup_terms or [])[:2]

    if ctype == "chat":
        active_r = roster if roster else ["Chloe", "Jake", "Marcus"]
        # الصف الأول: اختيار حر لمن في الغرفة (1 أو 2 أو 3)
        kb.row(*[types.InlineKeyboardButton(f"{'🟢' if h in active_r else '⚪'} {h}", callback_data=f"edroster:{h}:{tok}") for h in ALL_HOSTS])
        # الصف الثاني: استماع + حفظ + فحص مباشر للعبارات المذكورة بدون أي أزرار مكررة
        row2 = [
            types.InlineKeyboardButton("🎧 Voice", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        ]
        for i, t in enumerate(l_terms[:2]):
            row2.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
        kb.row(*row2)
        return kb

    if ctype == "lexicon":
        # البطاقة تحتوي بالفعل على المتلازمات وسُلّم المستويات والمشتقات؛ لذا نضع 4 أزرار غير مكررة فقط!
        kb.row(
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("⚖️ Synonym Contrast", callback_data=f"edlexext:syn:{tok}"),
            types.InlineKeyboardButton("🎯 Drill Word", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        )
        return kb

    if ctype == "draft":
        kb.row(
            types.InlineKeyboardButton("🎙️ Spoken", callback_data=f"edshift:spoken:{tok}"),
            types.InlineKeyboardButton("💼 Executive", callback_data=f"edshift:soften:{tok}"),
            types.InlineKeyboardButton("🏛️ C2 Prose", callback_data=f"edshift:c2:{tok}"),
            types.InlineKeyboardButton("⚡ Tighten", callback_data=f"edshift:condense:{tok}"),
        )
        row2 = [
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("💾 Save Upgrades", callback_data=f"edsave:{tok}"),
        ]
        for i, t in enumerate(l_terms[:2]):
            row2.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
        kb.row(*row2)
        return kb

    if ctype == "bridge":
        row1 = [
            types.InlineKeyboardButton("🎧 Listen All 3", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("💾 Save Blocks", callback_data=f"edsave:{tok}"),
        ]
        for i, t in enumerate(l_terms[:2]):
            row1.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
        kb.row(*row1)
        return kb

    if ctype == "miner":
        kb.row(
            types.InlineKeyboardButton("🏗️ C2 Templates", callback_data=f"edmore:skel:{tok}"),
            types.InlineKeyboardButton("🔬 Rhetoric & Flow", callback_data=f"edmore:rhet:{tok}"),
            types.InlineKeyboardButton("⛏️ Next 4 Chunks", callback_data=f"edmore:chunks:{tok}"),
        )
        row2 = [
            types.InlineKeyboardButton("🎯 Quiz Text", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save All", callback_data=f"edsave:{tok}"),
        ]
        for i, t in enumerate(l_terms[:2]):
            row2.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
        kb.row(*row2)
        return kb

    # clinic
    row_c = [
        types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("🎯 Quick Drill", callback_data=f"edqztext:{tok}"),
        types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
    ]
    for i, t in enumerate(l_terms[:1]):
        row_c.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
    kb.row(*row_c)
    return kb

def send_html_safe(chat_id: int, html_text: str, reply_markup: Any = None):
    try:
        clean_html = re.sub(r"<b>\s*<b>", "<b>", html_text)
        clean_html = re.sub(r"</b>\s*</b>", "</b>", clean_html)
        if len(clean_html) <= 4080:
            return bot.send_message(chat_id, clean_html, parse_mode="HTML", reply_markup=reply_markup)
        cut_idx = clean_html.rfind("<blockquote expandable>")
        if cut_idx > 400:
            bot.send_message(chat_id, clean_html[:cut_idx].strip(), parse_mode="HTML")
            return bot.send_message(chat_id, clean_html[cut_idx:].strip()[:4050], parse_mode="HTML", reply_markup=reply_markup)
        return bot.send_message(chat_id, clean_html[:4050], parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        clean_plain = html.unescape(re.sub(r"<[^>]+>", "", html_text))
        return bot.send_message(chat_id, clean_plain[:4000], reply_markup=reply_markup)

def _fmt_fixes(raw: str, strike: bool = True) -> str:
    out = []
    for ln in re.split(r"[\n|]+", raw or ""):
        cl = re.sub(r"\*+", "", ln).strip().lstrip("•-*").strip()
        if not cl:
            continue
        if "->" in cl or "→" in cl or "➔" in cl:
            pts = re.split(r"->|→|➔", cl, maxsplit=1)
            l, r = _h(pts[0].strip("~❌ ")), _h(pts[1].strip("✅ "))
            if l.lower() == r.lower() or l.lower() in ("cuz", "wanna", "gonna") or ("discuss with" in l.lower() and "discuss" == r.lower()):
                continue
            if l and r:
                out.append(f"✏️ <s>{l}</s> → <b>{r}</b>" if strike else f"• <i>{l}</i> → <b>{r}</b>")
        else:
            out.append(f"• {_h(cl)}")
    return "\n".join(out)

def _format_unified_senses(raw_senses: str) -> str:
    txt = html.unescape(str(raw_senses or "")).strip()
    if not txt:
        return ""
    txt = re.sub(r"(?<!^)\s*(?=[1-9]\.\s+\*?\*?[A-Za-z\u0600-\u06FF\"\'\[])", "\n\n", txt)
    txt = re.sub(r"\s*(•|\u2022)\s*", "\n• ", txt)
    out_lines = []
    for ln in txt.splitlines():
        cl = ln.strip()
        if not cl:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        if re.match(r"^[1-9]\.", cl):
            clean_hdr = re.sub(r"\*+", "", cl)
            out_lines.append(f"<b>{_fmt_inline_md(clean_hdr)}</b>")
        else:
            cl_b = cl.lstrip("•-* ").strip()
            out_lines.append(f"• {_fmt_inline_md(cl_b)}")
    return "\n".join(out_lines).strip()

# ------------------------------------------------------------------------------
# محرك 1: الدردشة الحرة الطبيعية (Chat — 1, 2, or 3 Friends)
# ------------------------------------------------------------------------------
def execute_chat_turn(chat_id: int, user_id: int, user_text: str, is_voice: bool = False, special_angle: str = "", user_first_name: str = "Musa") -> ChatSchema:
    s = _get_session(user_id)
    reg = s["register"]
    active_roster = _update_roster_from_text(user_id, user_text)

    hist_lines = []
    for h in s["history"][-5:]:
        if isinstance(h, dict):
            b_clean = h.get("b", "")
            if not str(h.get("u", "")).startswith("[STUDY"):
                hist_lines.append(f"User: {h.get('u','')[:90]} | Reply: {b_clean[:150]}")
    hist_str = "\n".join(hist_lines)

    if len(active_roster) == 1:
        roster_rule = f"ACTIVE_HOSTS_IN_ROOM: [{active_roster[0]}] (1-on-1 private chat). `primary_speaker` MUST be '{active_roster[0]}', and `second_speaker` MUST be ''!"
    elif len(active_roster) == 2:
        roster_rule = f"ACTIVE_HOSTS_IN_ROOM: [{active_roster[0]}, {active_roster[1]}] (2-person room). Only '{active_roster[0]}' or '{active_roster[1]}' may speak."
    else:
        roster_rule = "ACTIVE_HOSTS_IN_ROOM: [Chloe, Jake, Marcus]. Pick the most relevant `primary_speaker`. Leave `second_speaker` as '' unless a brief 1-line reaction from another host genuinely adds value."

    sys_p = f"{SYS_PROMPT}\nActive Register: {reg}. User's name: {user_first_name}.\n{roster_rule}"
    usr_p = f"Recent Context (do NOT re-correct old lines):\n{hist_str}\n\nCURRENT_USER_MESSAGE:\n\"{user_text}\""
    data: ChatSchema = call_llm_structured(sys_p, usr_p, ChatSchema, temp=0.62)

    p_spk = data.primary_speaker if data.primary_speaker in active_roster else active_roster[0]
    if len(active_roster) == 1:
        s_spk, s_bub = "", ""
    else:
        cand_s = data.second_speaker.strip()
        if cand_s in active_roster and cand_s != p_spk and data.second_reply_en.strip():
            s_spk = cand_s
            s_bub = re.sub(r"^\s*(Jake|Chloe|Marcus)\s*:\s*", "", re.sub(r"\([^)]*\)", "", data.second_reply_en)).strip()
        else:
            s_spk, s_bub = "", ""

    p_bub = re.sub(r"^\s*(Jake|Chloe|Marcus)\s*:\s*", "", re.sub(r"\([^)]*\)", "", data.primary_reply_en or "")).strip()
    p_bub = re.sub(r"^(Truth be told|Fair enough),\s*", "", p_bub, flags=re.I)
    if p_bub:
        p_bub = p_bub[0].upper() + p_bub[1:]

    combined_diag = f"{p_spk}: {p_bub}" + (f"\n{s_spk}: {s_bub}" if s_spk and s_bub else "")
    _update_session(user_id, new_turn={"u": user_text[:200], "b": combined_diag[:260]})

    fix_block = ""
    if data.has_actual_errors and data.error_corrections_list.strip():
        fmt_f = _fmt_fixes(data.error_corrections_list, strike=True)
        if fmt_f:
            _log_dna(user_id, data.error_corrections_list)
            fix_block = f"\n\n{fmt_f}"

    clean_chunks = _clean_multiline_bullets(data.expressions_from_reply_en)
    echo_drawer = (
        f"<b>💡 Native Echo ▼</b>\n"
        f"• <b>Your line, natural NYC:</b> <i>&quot;{_fmt_inline_md(data.user_line_natural_rephrase_en)}&quot;</i>"
        + (f"\n{clean_chunks}" if clean_chunks else "")
    )

    tok = hashlib.md5(f"CHT:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [(p_spk, _clean_tts(p_bub))] + ([(s_spk, _clean_tts(s_bub))] if s_spk and s_bub else [])
    v_items = _extract_clean_vault_items(data.expressions_from_reply_en, combined_diag)

    store_card(tok, user_id, v_segs, {"title": "NYC Lounge Chat", "register": reg, "main_en": combined_diag}, v_items, source_text=user_text)
    kb_markup = build_inline_kb(tok, "chat", _extract_lookup_terms(v_items), roster=active_roster)

    v_hdr = f"🎙️ <i>&quot;{_h(user_text)}&quot;</i>\n\n" if is_voice else ""
    body_dialogue = f"<b>{_h(p_spk)}</b>\n{_fmt_inline_md(p_bub)}"
    if s_spk and s_bub:
        body_dialogue += f"\n\n<b>{_h(s_spk)}</b>\n{_fmt_inline_md(s_bub)}"

    full_html = f"{v_hdr}{body_dialogue}{fix_block}\n\n<blockquote expandable>{echo_drawer}</blockquote>"
    send_html_safe(chat_id, full_html, reply_markup=kb_markup)

    if is_voice and os.environ.get("LIVE_SELF_TEST") != "1":
        def _auto_voice():
            mp3 = build_audio(v_segs, slow=False)
            if mp3 and os.path.exists(mp3):
                with open(mp3, "rb") as af:
                    bot.send_voice(chat_id, af)
                os.remove(mp3)
        threading.Thread(target=_auto_voice, daemon=True).start()
    else:
        threading.Thread(target=build_audio, args=(v_segs, False), daemon=True).start()

    return data

# ------------------------------------------------------------------------------
# محرك 2: الموسوعة المعجمية الشاملة (10-Pillar Master Lexicon)
# ------------------------------------------------------------------------------
def execute_lexicon_lookup(chat_id: int, user_id: int, query_text: str):
    reg = _get_session(user_id)["register"]
    task = (
        "MASTER 10-PILLAR LEARNER'S DICTIONARY, COLLOCATION & PRIMING ENGINE:\n"
        "Fill every field thoroughly:\n"
        "1. `ipa_and_spoken_prosody`: American IPA with stress marks + connected-speech linking.\n"
        "2. `grammar_valency_and_coca_badge`: Exact grammar & separability code + COCA register stars.\n"
        "3. `unified_senses_en_ar`: Numbered sense blocks (Definition, المقابل الفصيح والاصطلاحي, Valency Frame, Spoken Example, Written Example). If query is Arabic, provide 3 Register Activator blocks (1. Spoken NYC, 2. Workplace, 3. C2 Academic).\n"
        "4. `oxford_collocation_box_en`: Object/Noun Partners, Verb/Modal Triggers, Adverb/Adjective Modifiers, Prepositions.\n"
        "5. `lexical_priming_and_chunks_en`: Hoey's Colligation + 2 ready frames with Arabic.\n"
        "6. `prosody_and_subtext_en` + `register_ladder_and_family_en` + `common_traps_and_cognitive_hook_en`.\n"
        "7. `clean_audio_script_en`: 3-turn pure English script reading the headword and examples aloud."
    )
    data: LexiconSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Lookup Query: \"{query_text.strip()}\"", LexiconSchema, temp=0.38)

    senses_fmt = _format_unified_senses(data.unified_senses_en_ar)
    colloc_fmt = _clean_multiline_bullets(data.oxford_collocation_box_en)
    priming_fmt = _clean_multiline_bullets(data.lexical_priming_and_chunks_en)
    ladder_fmt = _clean_multiline_bullets(data.register_ladder_and_family_en)
    traps_fmt = _clean_multiline_bullets(data.common_traps_and_cognitive_hook_en)

    top_card = (
        f"<b>📖 {_h(data.headword_en_or_ar or query_text)}</b>  <code>{_h(data.ipa_and_spoken_prosody)}</code>\n"
        f"<code>[{_h(data.grammar_valency_and_coca_badge)}]</code>\n\n"
        f"{senses_fmt}\n\n"
        f"<b>📊 Oxford Collocation Box</b>\n{colloc_fmt}\n\n"
        f"<b>🧠 Lexical Priming &amp; Ready Frames</b>\n{priming_fmt}"
    )
    deep_drawer = (
        f"<b>🔬 Prosody, Register Ladder, Family &amp; Traps ▼</b>\n"
        f"<b>🎭 Semantic Prosody &amp; Subtext:</b> {_fmt_inline_md(data.prosody_and_subtext_en)}\n\n"
        f"<b>🔄 Register Ladder, Family &amp; Antonym</b>\n{ladder_fmt}\n\n"
        f"<b>⚠️ Learner Traps &amp; Cognitive Hook</b>\n{traps_fmt}"
    )
    full_html = f"{top_card}\n\n<blockquote expandable>{deep_drawer}</blockquote>"

    tok = hashlib.md5(f"LEX:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = _split_dialogue_segments(data.clean_audio_script_en, "Marcus")
    head_clean = (data.headword_en_or_ar or query_text).strip()[:45]
    v_items = [(head_clean, data.unified_senses_en_ar[:240], data.lexical_priming_and_chunks_en[:240])]
    store_card(tok, user_id, v_segs, {"title": f"Lexicon: {head_clean}", "register": reg, "main_en": data.unified_senses_en_ar}, v_items, source_text=head_clean)
    send_html_safe(chat_id, full_html, reply_markup=build_inline_kb(tok, "lexicon"))
    threading.Thread(target=build_audio, args=(v_segs, False), daemon=True).start()

def execute_lexicon_extension(chat_id: int, user_id: int, term: str, ext_type: str):
    clean_term = (term or "").splitlines()[0].strip()[:60]
    prompt = (
        f"Build a Longman Activator / Macmillan Synonym Thesaurus for '{clean_term}' on SEPARATE lines ('\\n'):\n"
        f"• **1. [Closest Synonym 1]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
        f"• **2. [Closest Synonym 2]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
        f"• **3. [C2 Formal/Academic Competitor]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
        f"• **4. Precision Boundary Test (❌ vs ✅):** sentence where '{clean_term}' works and the synonym fails (and vice versa)."
    )
    res: LexiconExtraSchema = call_llm_structured(f"{SYS_PROMPT}\n{prompt}", f"Target: {clean_term}", LexiconExtraSchema, temp=0.4)
    send_html_safe(chat_id, f"<b>⚖️ {_h(res.title_en)} — {_h(clean_term)}</b>\n\n{_clean_multiline_bullets(res.content_bullets_en)}")

# ------------------------------------------------------------------------------
# محرك 3: ورشة الدراسة التفاعلية الرشيقة (V24 Interactive Study Workbench)
# ------------------------------------------------------------------------------
def _detect_study_route(text: str, user_id: int) -> str:
    clean = text.strip()
    low = clean.lower()
    w_cnt = len(clean.split())

    # 1. نص عربي -> جسر الصياغة AR -> EN Bridge
    if len(re.findall(r"[\u0600-\u06FF]", clean)) > 8:
        return "bridge"

    # 2. أوامر بادئة صريحة
    if low.startswith(("mine:", "extract:", "article:")):
        return "miner"
    if low.startswith(("edit:", "fix:", "draft:", "polish:")):
        return "draft"

    # 3. سؤال لغوي صريح ("what's the difference", "how do I say", "X vs Y", علامة استفهام)
    if (
        low.startswith(("what ", "what'", "how ", "why ", "when ", "where ", "which ", "can i say", "is it correct", "difference"))
        or (" vs " in low and w_cnt < 35)
        or (clean.endswith("?") and w_cnt < 35)
    ):
        return "clinic"

    # 4. التحقق الذكي: لا نعتبر الجملة القصيرة "تدريباً على البطاقة السابقة" إلا إذا:
    #    - لم تكن تكراراً لنفس جملة المستخدم السابقة (عند تغيير المستوى مثلاً!)
    #    - وكانت تحتوي فعلاً على إحدى العبارات المقترحة في البطاقة السابقة!
    s = _get_session(user_id)
    study_turns = [h for h in s.get("history", [])[-2:] if isinstance(h, dict) and str(h.get("u", "")).startswith("[STUDY")]
    if study_turns and w_cnt <= 25:
        last_u = re.sub(r"^\[STUDY[^\]]*\]\s*:\s*", "", str(study_turns[-1].get("u", ""))).strip().lower()
        last_b = str(study_turns[-1].get("b", "")).lower()
        if low != last_u:
            # فحص هل استخدم الطالب إحدى العبارات المقترحة في البطاقة السابقة
            suggested_terms = [m.group(1).lower() for m in re.finditer(r"\*\*(.+?)\*\*", last_b) if len(m.group(1)) > 3]
            if any(t in low for t in suggested_terms):
                return "clinic"

    # 5. التمييز بين مقال طويل للتعدين وبين مسودة المستخدم
    first_person = len(re.findall(r"\b(i|my|me|we|our|us|i'm|i've|i'd)\b", low))
    if w_cnt > 65 and first_person <= 1:
        return "miner"
    return "draft"

def execute_study_and_miner(chat_id: int, user_id: int, user_input: str, is_voice: bool = False, already_mined: str = "", shift: str = ""):
    s = _get_session(user_id)
    reg = s["register"]

    if shift:
        shift_map = {
            "spoken": ("🎙️ Natural Spoken Delivery", "Rewrite this text into natural, spontaneous spoken American English (active verbs, contractions, spoken rhythm). Keep the speaker's exact perspective."),
            "soften": ("💼 Crisp & Diplomatic Executive Written", "Rewrite this text into warm, tactful, solution-oriented American workplace/executive English. Keep the speaker's exact perspective."),
            "c2": ("🏛️ C2 Formal & Analytical Prose", "Transform this text into elevated, natural C2 formal/analytical prose keeping the speaker's exact meaning and pronoun perspective (never turn a personal statement into a random medical/textbook claim!)."),
            "condense": ("⚡ Tightened High-Impact Version (-35%)", "Condense this text by 35-40% into razor-sharp English using precision verbs."),
        }
        sh_title, sh_prompt = shift_map.get(shift, shift_map["spoken"])
        sh_data: DraftShiftSchema = call_llm_structured(f"{SYS_PROMPT}\n{sh_prompt}", f"Original Text:\n\"{user_input[:3500]}\"", DraftShiftSchema, temp=0.42)
        tok = hashlib.md5(f"SHF:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Marcus" if shift == "c2" else "Chloe", _clean_tts(sh_data.transformed_text_en))]
        v_items = _extract_clean_vault_items(sh_data.key_refinements_en, sh_data.transformed_text_en)
        store_card(tok, user_id, v_segs, {"title": sh_title, "register": reg, "main_en": sh_data.transformed_text_en}, v_items, source_text=user_input)
        sh_html = (
            f"<b>{sh_title}</b>\n\n"
            f"<i>&quot;{_fmt_inline_md(sh_data.transformed_text_en)}&quot;</i>\n\n"
            f"<b>🔬 What Changed &amp; Why</b>\n{_clean_multiline_bullets(sh_data.key_refinements_en)}"
        )
        send_html_safe(chat_id, sh_html, reply_markup=build_inline_kb(tok, "draft", _extract_lookup_terms(v_items)))
        return

    if already_mined:
        if already_mined.startswith("SKEL_ONLY::"):
            prev_m = already_mined.replace("SKEL_ONLY::", "")
            m_task = (
                "Extract 3 reusable C2 Rhetorical Sentence Templates from this text as numbered blocks:\n"
                "1. **Pattern:** `[Frame with [Slots]]`\n• الوظيفة البلاغية: [Eloquent Arabic explanation]\n• Applied Example: \"[New sentence built with this skeleton]\""
            )
            hdr_icon = "🏗️ Reusable C2 Sentence Templates"
        elif already_mined.startswith("RHET_ONLY::"):
            prev_m = already_mined.replace("RHET_ONLY::", "")
            m_task = (
                "Analyze the Author's Prose Architecture in 3 numbered blocks:\n"
                "1. **Argument & Cohesion Flow:** (Given-to-New, End-Weight, transitions).\n"
                "2. **Hedging & Stance:** (how claims are qualified).\n"
                "3. **Verb-Driven ➔ C2 Nominalization Map:** (2 sentences contrasted with spoken equivalents + Arabic)."
            )
            hdr_icon = "🔬 Rhetoric, Cohesion & Nominalization Breakdown"
        else:
            prev_m = already_mined
            m_task = (
                f"Previously extracted: [{prev_m}]. Mine 4 COMPLETELY NEW whole-chunk expressions or collocations from this text as numbered blocks:\n"
                "1. **[Exact Expression]** — `[Grammar Frame]`\n• المعنى السياقي: [Eloquent MSA meaning]\n• New Example: \"[Fresh 10-14 word sentence]\""
            )
            hdr_icon = "⛏️ Next 4 Extracted Chunks"

        m_data: MoreMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{m_task}", f"Text:\n\"{user_input[:4500]}\"", MoreMinerSchema, temp=0.42)
        tok = hashlib.md5(f"MOR:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_items = _extract_clean_vault_items(m_data.new_expressions_en, user_input[:200])
        new_mined = f"{prev_m}, {', '.join([x[0] for x in v_items])}".strip(", ")
        store_card(tok, user_id, [("Marcus", _clean_tts(m_data.new_expressions_en))], {"title": hdr_icon, "main_en": m_data.new_expressions_en}, v_items, source_text=user_input, mined_so_far=new_mined)
        send_html_safe(chat_id, f"<b>{hdr_icon}</b>\n\n{_format_unified_senses(m_data.new_expressions_en)}", reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    route = "draft" if is_voice else _detect_study_route(user_input, user_id)

    if route == "bridge":
        task = (
            "ARABIC-TO-ENGLISH THOUGHT ARCHITECT:\n"
            "Express this Arabic thought natively while keeping the exact speaker perspective across: 1) `spoken_american_en`, 2) `executive_written_en`, 3) `c2_academic_prose_en`, 4) `building_blocks_and_trap_en_ar` (2 **bolded chunks** with pure MSA meaning + ❌ Literal Calque Trap), 5) `your_turn_prompt_en`."
        )
        b_res: ArabicBridgeSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Arabic Input:\n\"{user_input[:3000]}\"", ArabicBridgeSchema, temp=0.42)
        _update_session(user_id, new_turn={"u": f"[STUDY AR->EN]: {user_input[:160]}", "b": f"{b_res.c2_academic_prose_en[:140]} | {b_res.building_blocks_and_trap_en_ar[:140]}"})

        html_b = (
            f"<b>🌉 AR ➔ EN Native Expression Bridge</b>\n\n"
            f"🎙️ <b>1. Natural Spoken American</b>\n<i>&quot;{_fmt_inline_md(b_res.spoken_american_en)}&quot;</i>\n\n"
            f"💼 <b>2. Crisp Executive Written</b>\n<i>&quot;{_fmt_inline_md(b_res.executive_written_en)}&quot;</i>\n\n"
            f"🏛️ <b>3. C2 Formal &amp; Analytical Prose</b>\n<b>&quot;{_fmt_inline_md(b_res.c2_academic_prose_en)}&quot;</b>\n\n"
            f"<b>🧱 Key Building Blocks &amp; Calque Trap</b>\n{_clean_multiline_bullets(b_res.building_blocks_and_trap_en_ar)}\n\n"
            f"🎯 <b>Your Turn:</b> <i>{_fmt_inline_md(b_res.your_turn_prompt_en)}</i>"
        )
        tok = hashlib.md5(f"BRG:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Jake", _clean_tts(b_res.spoken_american_en)), ("Chloe", _clean_tts(b_res.executive_written_en)), ("Marcus", _clean_tts(b_res.c2_academic_prose_en))]
        v_items = _extract_clean_vault_items(b_res.building_blocks_and_trap_en_ar, b_res.executive_written_en)
        store_card(tok, user_id, v_segs, {"title": "AR to EN Bridge", "main_en": b_res.executive_written_en}, v_items, source_text=b_res.executive_written_en)
        send_html_safe(chat_id, html_b, reply_markup=build_inline_kb(tok, "bridge", _extract_lookup_terms(v_items)))
        return

    if route == "miner":
        task = "MODULAR CORPUS MINER: Extract the Top 4 highest-value idioms, phrasal verbs, or C1/C2 collocations from this text as numbered blocks (Expression, Frame, pure MSA meaning, New Example)."
        m_res: ModularMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Text:\n\"{user_input[:5500]}\"", ModularMinerSchema, temp=0.4)
        _update_session(user_id, new_turn={"u": f"[STUDY MINER]: {user_input[:140]}", "b": m_res.top_chunks_en_ar[:260]})

        html_m = (
            f"<b>⛏️ {_h(m_res.theme_title_en)} — Top 4 Chunks</b>\n\n"
            f"{_format_unified_senses(m_res.top_chunks_en_ar)}\n\n"
            f"🎯 <b>Active Practice:</b> <i>{_fmt_inline_md(m_res.your_turn_prompt_en)}</i>"
        )
        tok = hashlib.md5(f"MIN:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_items = _extract_clean_vault_items(m_res.top_chunks_en_ar, m_res.theme_title_en)
        v_segs = [("Marcus", _clean_tts(m_res.top_chunks_en_ar))]
        store_card(tok, user_id, v_segs, {"title": m_res.theme_title_en, "main_en": m_res.top_chunks_en_ar}, v_items, source_text=user_input[:4500], mined_so_far=", ".join([x[0] for x in v_items]))
        send_html_safe(chat_id, html_m, reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    if route == "clinic":
        recent_study = "\n".join([f"Prior: {h.get('u','')}\nCoaching: {h.get('b','')}" for h in s.get("history", [])[-2:] if isinstance(h, dict)])
        task = (
            "INTERACTIVE TUTOR & NUANCE CLINIC:\n"
            "- If the user is testing a chunk from the previous card, evaluate their sentence warmly and show any ❌ ➔ ✅ fix.\n"
            "- If asking a linguistic question, give the direct rule with 3 register examples (Casual Spoken, Executive Workplace, Formal C2 Prose) keeping the same speaker meaning, with pure MSA meanings (no street slang!)."
        )
        c_res: TutorLoopSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Recent Study Context:\n{recent_study}\n\nUser Input:\n\"{user_input[:2000]}\"", TutorLoopSchema, temp=0.45)
        _update_session(user_id, new_turn={"u": f"[STUDY PRACTICE/Q]: {user_input[:160]}", "b": c_res.direct_feedback_or_answer_en[:220]})

        html_c = (
            f"<b>🎓 {_h(c_res.title_en)}</b>\n\n"
            f"{_fmt_inline_md(c_res.direct_feedback_or_answer_en)}\n\n"
            f"<b>📊 Examples &amp; Nuances Across Registers</b>\n{_clean_multiline_bullets(c_res.register_or_nuance_examples_en_ar)}\n\n"
            f"🎯 <b>Next Step:</b> <i>{_fmt_inline_md(c_res.next_step_challenge_en)}</i>"
        )
        tok = hashlib.md5(f"CLN:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Chloe", _clean_tts(c_res.direct_feedback_or_answer_en)), ("Marcus", _clean_tts(c_res.register_or_nuance_examples_en_ar))]
        v_items = _extract_clean_vault_items(c_res.register_or_nuance_examples_en_ar, c_res.direct_feedback_or_answer_en)
        store_card(tok, user_id, v_segs, {"title": c_res.title_en, "main_en": c_res.direct_feedback_or_answer_en}, v_items, source_text=user_input)
        send_html_safe(chat_id, html_c, reply_markup=build_inline_kb(tok, "clinic", _extract_lookup_terms(v_items)))
        return

    # المسار الأساسي للمسودات والجمل (Precision Editor — يتكيف بصرامة مع Casual أو Professional أو Formal!)
    reg_rules = {
        "Informal": (
            "ACTIVE REGISTER: INFORMAL (Natural Everyday Spoken & Idiomatic English).\n"
            "- `polished_version_en` and all 3 `lexical_upgrades_en_ar` MUST be authentic everyday idioms, phrasal verbs, and relaxed conversational phrasing (e.g. for 'I feel so tired': 'wiped out', 'running on fumes', 'dead on my feet')."
        ),
        "Semi-formal": (
            "ACTIVE REGISTER: SEMI-FORMAL (Polished, Articulate Standard English — Cambridge C1/C2 Neutral-Polite Register).\n"
            "- `polished_version_en` and all 3 `lexical_upgrades_en_ar` MUST be clear, well-spoken, polite standard English suitable for educated conversation, serious discussions, or general correspondence across ANY life topic (e.g. for 'I feel so tired': 'completely drained', 'running low on energy', 'feeling the strain of a long week').\n"
            "- CRITICAL: Do NOT force corporate/office buzzwords (like 'bandwidth' or 'Slack') unless the user's text is actually about office work! And do NOT use street slang!"
        ),
        "Formal": (
            "ACTIVE REGISTER: FORMAL (Elevated C2 Academic, Analytical & Diplomatic Prose).\n"
            "- `polished_version_en` and all 3 `lexical_upgrades_en_ar` MUST be high-level C1/C2 formal vocabulary and collocations (e.g. for 'I feel so tired': 'thoroughly fatigued', 'considerably depleted', 'experiencing pronounced exhaustion').\n"
            "- CRITICAL: Keep the user's exact 1st-person perspective and meaning! Never turn a personal statement into an impersonal medical textbook claim!"
        ),
    }
    task = (
        f"PRECISION DRAFT & SPEECH EDITOR — {reg_rules.get(reg, reg_rules['Semi-formal'])}\n"
        "1. `surgical_diffs_en_ar`: ONLY flag genuine grammar, preposition, or collocation mistakes. If the sentence is grammatically valid (like 'I feel so tired'), write strictly: '• ✓ Grammatically solid—see register-specific upgrades below.'\n"
        "2. `lexical_upgrades_en_ar`: 3 upgrades strictly matching the ACTIVE REGISTER formatted as '• [user word] ➔ **[Register-matched expression]** ([Pure Modern Standard Arabic meaning—الفصحى السياقية الرصينة, zero slang!]) — \"[Natural example sentence]\"'.\n"
        "3. `your_turn_prompt_en`: 1-sentence prompt to practice one of the 3 upgrades."
    )
    d_res: PrecisionEditorSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Active Register: {reg}\nUser's Draft:\n\"{user_input[:4000]}\"", PrecisionEditorSchema, temp=0.4)
    _update_session(user_id, new_turn={"u": f"[STUDY DRAFT]: {user_input[:160]}", "b": f"{d_res.polished_version_en[:140]} | {d_res.lexical_upgrades_en_ar[:180]}"})

    if d_res.surgical_diffs_en_ar and ("->" in d_res.surgical_diffs_en_ar or "➔" in d_res.surgical_diffs_en_ar):
        _log_dna(user_id, d_res.surgical_diffs_en_ar.replace("➔", "->"))

    v_prefix = f"🎙️ <b>Voice Draft:</b> <i>&quot;{_h(user_input[:240])}&quot;</i>\n\n" if is_voice else ""
    html_d = (
        f"{v_prefix}<b>✍️ Polished Version ({_h(reg)})</b>\n"
        f"<i>&quot;{_fmt_inline_md(d_res.polished_version_en)}&quot;</i>\n\n"
        f"<b>🔧 Surgical Fixes (❌ ➔ ✅)</b>\n{_clean_multiline_bullets(d_res.surgical_diffs_en_ar)}\n\n"
        f"<b>🚀 {_h(reg)} Vocabulary &amp; Collocation Upgrades</b>\n{_clean_multiline_bullets(d_res.lexical_upgrades_en_ar)}\n\n"
        f"🎯 <b>Your Turn:</b> <i>{_fmt_inline_md(d_res.your_turn_prompt_en)}</i>"
    )
    tok = hashlib.md5(f"DRF:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Marcus" if reg == "Formal" else "Chloe", _clean_tts(d_res.polished_version_en))]
    v_items = _extract_clean_vault_items(d_res.lexical_upgrades_en_ar, d_res.polished_version_en)
    store_card(tok, user_id, v_segs, {"title": f"Precision Editor ({reg})", "register": reg, "main_en": d_res.polished_version_en}, v_items, source_text=user_input[:3500])
    send_html_safe(chat_id, html_d, reply_markup=build_inline_kb(tok, "draft", _extract_lookup_terms(v_items)))

# ------------------------------------------------------------------------------
# محرك 4: استوديو البودكاست الإذاعي الرشيق (V24 — بدون أزرار مكررة + كشف ذكي للمواضيع الجديدة)
# ------------------------------------------------------------------------------
def _enforce_spoken_contractions(text: str) -> str:
    if not text:
        return ""
    repls = [
        (r"\bIt is not\b", "It's not"), (r"\bit is not\b", "it's not"),
        (r"\bIt is\b", "It's"), (r"\bit is\b", "it's"),
        (r"\bThere is\b", "There's"), (r"\bthere is\b", "there's"),
        (r"\bThat is\b", "That's"), (r"\bthat is\b", "that's"),
        (r"\bWhat is\b", "What's"), (r"\bwhat is\b", "what's"),
        (r"\bI am\b", "I'm"),
        (r"\bYou are\b", "You're"), (r"\byou are\b", "you're"),
        (r"\bWe are\b", "We're"), (r"\bwe are\b", "we're"),
        (r"\bThey are\b", "They're"), (r"\bthey are\b", "they're"),
        (r"\bWe have\b", "We've"), (r"\bwe have\b", "we've"),
        (r"\bYou have\b", "You've"), (r"\byou have\b", "you've"),
        (r"\bcannot\b", "can't"), (r"\bCannot\b", "Can't"),
        (r"\bdo not\b", "don't"), (r"\bDo not\b", "Don't"),
        (r"\bdoes not\b", "doesn't"), (r"\bDoes not\b", "Doesn't"),
        (r"\bdid not\b", "didn't"), (r"\bDid not\b", "Didn't"),
        (r"\bwill not\b", "won't"), (r"\bWill not\b", "Won't"),
        (r"\bwould not\b", "wouldn't"), (r"\bcould not\b", "couldn't"), (r"\bshould not\b", "shouldn't"),
    ]
    out = text
    for pat, sub in repls:
        out = re.sub(pat, sub, out)
    return out

def _format_podcast_script_safe(raw_script: str, highlight_terms: Optional[List[str]] = None) -> str:
    clean = html.unescape(str(raw_script or "")).strip()
    clean = _enforce_spoken_contractions(clean)
    clean = re.sub(r"\([^)]*\)", "", clean)
    clean = re.sub(r"\*+\s*(Jake|Chloe|Marcus)\s*\*+\s*:", r"\1:", clean)
    clean = re.sub(r"(Jake|Chloe|Marcus)\s*:\s*\*+", r"\1: ", clean)
    norm = re.sub(r"(?:^|\n|\s)+(Jake|Chloe|Marcus)\s*:\s*", r"\n\1: ", clean).strip()

    if highlight_terms:
        for term in highlight_terms:
            t_clean = re.sub(r"\*+", "", term).strip()
            if len(t_clean) > 3 and f"**{t_clean}**" not in norm.lower():
                norm = re.sub(rf"(?<!\*)\b({re.escape(t_clean)})\b(?!\*)", r"**\1**", norm, count=1, flags=re.I)

    out_turns = []
    for ln in norm.splitlines():
        ln_s = ln.strip()
        if not ln_s:
            continue
        m = re.match(r"^(Jake|Chloe|Marcus)\s*:\s*(.+)$", ln_s)
        if m:
            spk, body = m.group(1), m.group(2).strip()
            out_turns.append(f"🎙️ <b>{spk}:</b> {_fmt_inline_md(body)}")
        else:
            out_turns.append(_fmt_inline_md(ln_s))
    return "\n\n".join(out_turns)

def execute_podcast(chat_id: int, user_id: int, topic: str = "", hosts: int = 3, fmt_style: str = "roundtable", user_first_name: str = "Musa"):
    s = _get_session(user_id)
    reg = s["register"]
    _update_session(user_id, mode="PODCAST", podcast_hosts=hosts, sub_state=f"PODCAST_ACTIVE:{fmt_style}")

    vault_terms = _get_recent_vault_terms(user_id, 5)
    style_prompts = {
        "roundtable": f"FORMAT: 3-Host NYC Roundtable ({reg}). Jake, Chloe, and Marcus have a realistic, unscripted debate (2-3 sentences per turn) with genuine friction, personal anecdotes, and natural spoken contractions.",
        "clash": f"FORMAT: 2-Host Clash Debate ({reg}) between Jake (pragmatic execution) and Marcus (deep structural strategy). Have them push back directly on each other's assumptions.",
        "case": f"FORMAT: 2-Host Deep Case Breakdown ({reg}) with Chloe and Marcus dissecting real-world leadership, psychology, and organizational habits.",
        "vault": (
            f"FORMAT: Personalized 'Vault Mastery' Episode ({reg}) with Chloe, Jake & Marcus.\n"
            f"Naturally weave these saved expressions into the hosts' dialogue (bolding them with **): {', '.join(vault_terms) if vault_terms else 'hold off on, throw around, come down to, pan out'}."
        ),
    }
    chosen_inst = style_prompts.get(fmt_style, style_prompts["roundtable"])
    t_p = topic.strip() if len(topic.strip()) > 2 else f"Unfiltered {reg} NYC discussion on work culture, ambition, and modern habits"

    data: PodcastSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{chosen_inst}\n"
        f"CRITICAL: Use natural spoken contractions ('it's', 'can't', 'we've', 'there's'). Each turn must be 2-3 sentences. "
        f"In `fast_speech_and_chunks_en_ar`, explain REAL American phonetic linking/reduction (Flap-T, dropped 'g', schwa /ə/), NEVER hyphenated spelling like 'draw-the-line'! "
        f"In `on_air_call_in_question_en`, address {user_first_name} directly without assuming he lives in NYC.",
        f"Episode Topic: {t_p}",
        PodcastSchema,
        temp=0.62,
    )

    spoken_script = _enforce_spoken_contractions(data.script_en)
    v_items = _extract_clean_vault_items(data.fast_speech_and_chunks_en_ar, data.episode_title_en)
    l_terms = _extract_lookup_terms(v_items)
    all_chunk_names = [x[0] for x in v_items if x and x[0]]

    script_html = _format_podcast_script_safe(spoken_script, highlight_terms=all_chunk_names)
    chunks_html = _format_unified_senses(data.fast_speech_and_chunks_en_ar)

    badge_map = {
        "roundtable": "🎙️ 3-Host Roundtable",
        "clash": "⚡ Clash Debate (Jake vs. Marcus)",
        "case": "🔍 Deep Case Study",
        "vault": "💎 Your Vault in Action",
    }
    fmt_badge = badge_map.get(fmt_style, "🎙️ NYC Studio")

    html_out = (
        f"<b>{fmt_badge} • {_h(reg)}</b>\n"
        f"<b>🎬 {_h(data.episode_title_en)}</b>\n\n"
        f"🎧 <b>Listening Focus:</b> {_fmt_inline_md(data.episode_summary_and_ear_cue_en)}\n\n"
        f"📞 <b>Live Call-In ({_h(user_first_name)}):</b>\n"
        f"<i>&quot;{_fmt_inline_md(data.on_air_call_in_question_en)}&quot;</i>\n"
        f"<i>(Reply by voice or text to join the hosts on air, or send <code>topic: ...</code> for a custom episode)</i>\n\n"
        f"<blockquote expandable>📜 <b>Episode Transcript (Tap to Expand/Collapse) ▼</b>\n{script_html}</blockquote>\n\n"
        f"<blockquote expandable>💎 <b>Key Chunks &amp; Connected-Speech Decoder ▼</b>\n{chunks_html}</blockquote>"
    )

    tok = hashlib.md5(f"POD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(spoken_script, "Jake")
    store_card(tok, user_id, segs, {"title": data.episode_title_en, "register": reg, "main_en": spoken_script}, v_items, source_text=f"{data.episode_title_en}\n{spoken_script}")
    _update_session(
        user_id,
        mode="PODCAST",
        sub_state=f"PODCAST_ACTIVE:{fmt_style}",
        new_turn={"u": f"[Podcast: {data.episode_title_en}]", "b": f"{spoken_script[:320]} | Question: {data.on_air_call_in_question_en}"},
    )

    # صفّان نظيفان فقط بدون تكرار أزرار الصوت 1.0x أو النص المنفصل
    kb = types.InlineKeyboardMarkup()
    row1 = [
        types.InlineKeyboardButton("🐢 Slow 0.85x", callback_data=f"edtts:slow:{tok}"),
        types.InlineKeyboardButton("🎯 Quiz Episode", callback_data=f"edqztext:{tok}"),
        types.InlineKeyboardButton("💾 Save Chunks", callback_data=f"edsave:{tok}"),
    ]
    for i, t in enumerate(l_terms[:1]):
        row1.append(types.InlineKeyboardButton(f"🔍 {t[:18]}", callback_data=f"edqlk:{i}:{tok}"))
    kb.row(*row1)
    kb.row(
        types.InlineKeyboardButton("🎙️ Roundtable", callback_data="edpod:roundtable"),
        types.InlineKeyboardButton("⚡ Clash", callback_data="edpod:clash"),
        types.InlineKeyboardButton("🔍 Case", callback_data="edpod:case"),
        types.InlineKeyboardButton("💎 Vault Ep", callback_data="edpod:vault"),
    )

    send_html_safe(chat_id, html_out, reply_markup=kb)

    def _send_pod_voice():
        try:
            bot.send_chat_action(chat_id, "record_voice")
        except Exception:
            pass
        mp3 = build_audio(segs, slow=False)
        if mp3 and os.path.exists(mp3):
            with open(mp3, "rb") as af:
                bot.send_voice(chat_id, af, caption=f"🎧 <b>{_h(data.episode_title_en)}</b>", parse_mode="HTML")
            os.remove(mp3)

    if os.environ.get("LIVE_SELF_TEST") != "1":
        threading.Thread(target=_send_pod_voice, daemon=True).start()

def execute_podcast_call_in(chat_id: int, user_id: int, user_text: str, is_voice: bool = False, user_first_name: str = "Musa"):
    s = _get_session(user_id)
    reg = s["register"]
    last_ep = "\n".join([f"{h.get('u','')}\n{h.get('b','')}" for h in s.get("history", [])[-2:] if isinstance(h, dict)])

    task = (
        f"LIVE ON-AIR CALL-IN RESPONSE ({reg}):\n"
        f"The listener ({user_first_name}) just called into the podcast.\n"
        "1. In `on_air_host_response_en`, write a lively 2-turn studio response ('Chloe: ...\nMarcus: ...' or 'Jake: ...') welcoming Musa's point on air using natural spoken contractions, reacting to his perspective, and asking a follow-up.\n"
        "2. In `call_in_language_polish_en_ar`, show a broadcast-ready polish of what Musa said, any ❌ ➔ ✅ fix, and 1 **bolded power chunk** used by the hosts with Arabic meaning."
    )
    res: PodcastCallInSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Episode Context:\n{last_ep}\n\nListener ({user_first_name}) Call-In:\n\"{user_text}\"", PodcastCallInSchema, temp=0.6)
    spoken_resp = _enforce_spoken_contractions(res.on_air_host_response_en)
    _update_session(user_id, new_turn={"u": f"[Call-In]: {user_text[:180]}", "b": spoken_resp[:240]})

    dialogue_fmt = _format_podcast_script_safe(spoken_resp)
    polish_fmt = _clean_multiline_bullets(res.call_in_language_polish_en_ar)
    v_hdr = f"📞 <b>Your On-Air Voice Call-In:</b> <i>&quot;{_h(user_text)}&quot;</i>\n\n" if is_voice else f"📞 <b>On-Air Response to {_h(user_first_name)}:</b>\n\n"

    html_call = f"{v_hdr}{dialogue_fmt}\n\n<blockquote expandable>💡 <b>Broadcast Polish &amp; Notes ▼</b>\n{polish_fmt}</blockquote>"
    tok = hashlib.md5(f"CAL:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(spoken_resp, "Chloe")
    v_items = _extract_clean_vault_items(res.call_in_language_polish_en_ar, spoken_resp)
    store_card(tok, user_id, segs, {"title": "On-Air Call-In", "main_en": spoken_resp}, v_items, source_text=user_text)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💾 Save Chunk", callback_data=f"edsave:{tok}"),
        types.InlineKeyboardButton("🎙️ New Roundtable", callback_data="edpod:roundtable"),
        types.InlineKeyboardButton("⚡ New Clash", callback_data="edpod:clash"),
    )
    send_html_safe(chat_id, html_call, reply_markup=kb)

    if os.environ.get("LIVE_SELF_TEST") != "1":
        def _auto_call_audio():
            mp3 = build_audio(segs, slow=False)
            if mp3 and os.path.exists(mp3):
                with open(mp3, "rb") as af:
                    bot.send_voice(chat_id, af)
                os.remove(mp3)
        threading.Thread(target=_auto_call_audio, daemon=True).start()

# ------------------------------------------------------------------------------
# محرك 5: ساحة الكويز والإنتاج اللغوي لـ C1/C2 (بطاقة واحدة موحدة بدون رسائل مكررة)
# ------------------------------------------------------------------------------
_QUIZ_STREAK: Dict[int, int] = {}

def send_quiz(chat_id: int, user_id: int, q_mode: str = "colloc", custom_context: str = ""):
    reg = _get_session(user_id)["register"]
    if q_mode == "standard":
        q_mode = "colloc"

    if q_mode == "produce":
        vt = _get_recent_vault_terms(user_id, 6)
        target_ch = random.choice(vt) if vt else random.choice(["hold off on", "come down to", "stem from", "hinge on", "at odds with", "fall short of"])
        _update_session(user_id, mode="QUIZ", sub_state=f"QUIZ_PRODUCE:{target_ch}")
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(f"🔍 Lookup '{target_ch[:18]}'", callback_data=f"edqlk_direct:{target_ch}"),
            types.InlineKeyboardButton("🎲 Different Expression", callback_data="edqznext:produce"),
            types.InlineKeyboardButton("🔗 Multiple-Choice", callback_data="edqznext:colloc"),
        )
        send_html_safe(
            chat_id,
            f"<b>✍️ Active Production Challenge ({_h(reg)})</b>\n\n"
            f"🎯 <b>Target Expression:</b> <code>{_h(target_ch)}</code>\n\n"
            f"Write (or record a voice note with) <b>one complete, natural sentence</b> in a <i>{_h(reg)}</i> context using <b>{_h(target_ch)}</b>:",
            reply_markup=kb,
        )
        return

    _update_session(user_id, mode="QUIZ", sub_state=f"QUIZ_MC:{q_mode}")

    mode_instructions = {
        "colloc": "MODE 1 — C1/C2 COLLOCATION & PREPOSITION VALENCY DRILL: Create a sentence with a ____ blank testing an advanced verb+noun collocation, phrasal verb, or verb+preposition frame. Make the 3 distractors realistic near-misses.",
        "c2": "MODE 2 — CAMBRIDGE C2 KEY-WORD TRANSFORMATION: In `scenario_or_stem_en`, provide Base Sentence, KEY WORD in caps, and Lead-in with ____ blank testing Negative Inversion, Cleft structures, or Nominalization.",
        "prag": "MODE 3 — PRAGMATIC SUBTEXT & DIPLOMATIC REGISTER FIT: Describe a delicate workplace or academic scenario and ask which response strikes the exact diplomatic/idiomatic tone.",
    }

    if custom_context:
        extra = f"CONTEXT-BASED DRILL: Create a sharp C1/C2 question testing a standout expression or collocation directly from this text:\n{custom_context[:1600]}"
    elif q_mode in ("dna", "vault"):
        with db_connect() as conn:
            dna_rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 4", (user_id,)).fetchall()
        vt = _get_recent_vault_terms(user_id, 5)
        items_str = "; ".join([f"{r[0]} -> {r[1]}" for r in dna_rows]) + " | Saved chunks: " + ", ".join(vt)
        extra = f"PERSONALIZED VAULT & MISTAKE RADAR DRILL: Test one of these exact user items: {items_str}"
    else:
        extra = mode_instructions.get(q_mode, mode_instructions["colloc"])

    q: QuizSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{extra}\nFill all fields including `winning_reason_en_ar` (with Arabic meaning) and `distractor_anatomy_en` (why the other 3 options fail).",
        f"Register: {reg}. Generate quiz.",
        QuizSchema,
        temp=0.65,
    )
    qid = hashlib.md5(f"QZ:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    with _db_lock, db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO quiz_v15 (q_id, user_id, quiz_json) VALUES (?, ?, ?)", (qid, user_id, q.model_dump_json()))
        conn.commit()

    streak = _QUIZ_STREAK.get(user_id, 0)
    s_tag = f" • 🔥 {streak}" if streak > 0 else ""

    kb = types.InlineKeyboardMarkup()
    kb.row(*[types.InlineKeyboardButton(f"{x}", callback_data=f"edqz:{x}:{q_mode}:{qid}") for x in ("A", "B", "C", "D")])
    kb.row(
        types.InlineKeyboardButton("🔗 Colloc •" if q_mode == "colloc" else "🔗 Colloc", callback_data="edqznext:colloc"),
        types.InlineKeyboardButton("🏛️ C2 •" if q_mode == "c2" else "🏛️ C2", callback_data="edqznext:c2"),
        types.InlineKeyboardButton("🎭 Pragmatic •" if q_mode == "prag" else "🎭 Pragmatic", callback_data="edqznext:prag"),
        types.InlineKeyboardButton("💎 Vault •" if q_mode == "vault" else "💎 Vault", callback_data="edqznext:vault"),
        types.InlineKeyboardButton("✍️ Write", callback_data="edqznext:produce"),
    )

    stem_fmt = _fmt_inline_md(q.scenario_or_stem_en).replace("\n", "\n\n")
    send_html_safe(
        chat_id,
        f"<b>🎯 {_h(q.quiz_mode_badge_en)} ({_h(reg)}){s_tag}</b>\n\n"
        f"{stem_fmt}\n\n"
        f"<b>A)</b> {_h(q.option_a)}\n"
        f"<b>B)</b> {_h(q.option_b)}\n"
        f"<b>C)</b> {_h(q.option_c)}\n"
        f"<b>D)</b> {_h(q.option_d)}",
        reply_markup=kb,
    )

def evaluate_active_quiz_production(chat_id: int, user_id: int, user_text: str, target_chunk: str, is_voice: bool = False):
    reg = _get_session(user_id)["register"]
    task = (
        f"ACTIVE PRODUCTION EVALUATION ({reg}):\n"
        f"The user was challenged to write/speak a natural sentence using: '{target_chunk}'.\n"
        "Give a score out of 10, fix any slip (❌ ➔ ✅), and provide both a polished natural version and a C2 analytical variation."
    )
    ev: ActiveQuizEvalSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Target: '{target_chunk}'\nUser's Sentence: \"{user_text}\"", ActiveQuizEvalSchema, temp=0.45)
    _QUIZ_STREAK[user_id] = _QUIZ_STREAK.get(user_id, 0) + 1
    tok = hashlib.md5(f"QEV:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Chloe", _clean_tts(ev.surgical_fix_and_model_en_ar))]
    t_clean, m_clean, c_clean = _clean_vault_entry(target_chunk, ev.evaluation_and_score_en, user_text)
    store_card(tok, user_id, v_segs, {"title": f"Production: {t_clean}", "main_en": user_text}, [(t_clean, m_clean, c_clean)], source_text=t_clean)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✍️ Next Write Prompt", callback_data="edqznext:produce"),
        types.InlineKeyboardButton("🔗 Multiple-Choice", callback_data="edqznext:colloc"),
        types.InlineKeyboardButton(f"🔍 {t_clean[:18]}", callback_data=f"edqlk_direct:{t_clean}"),
        types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
    )
    v_hdr = f"🎙️ <b>Your Spoken Sentence:</b> <i>&quot;{_h(user_text)}&quot;</i>\n\n" if is_voice else ""
    send_html_safe(
        chat_id,
        f"{v_hdr}<b>🏆 {_fmt_inline_md(ev.evaluation_and_score_en)}</b> (🔥 Streak: {_QUIZ_STREAK[user_id]})\n\n"
        f"{_clean_multiline_bullets(ev.surgical_fix_and_model_en_ar)}",
        reply_markup=kb,
    )

def show_vault(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT id, chunk, meaning_ar, context_sentence FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>💎 Your Vault is empty.</b>\nTap <b>💾 Save</b> under any card to store expressions for active recall.", reply_markup=build_reply_kb(user_id))
        return
    blocks = ["<b>💎 Saved Expressions — Active Recall Vault</b>\n<i>(Tap any spoiler bar to test your memory)</i>"]
    for i, r in enumerate(rows, 1):
        term, m_note, ctx = r[1], r[2], r[3]
        clue = _fmt_inline_md(m_note[:160]) if m_note else _fmt_inline_md(ctx[:160])
        blocks.append(f"<b>{i}.</b> <tg-spoiler><b>{_h(term)}</b></tg-spoiler>\n• <i>Clue:</i> {clue}")
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🎯 Quiz My Vault", callback_data="edqznext:vault"),
        types.InlineKeyboardButton("🎙️ Podcast with Vault", callback_data="edpod:vault"),
        types.InlineKeyboardButton("✍️ Write with Vault", callback_data="edqznext:produce"),
    )
    kb.row(
        types.InlineKeyboardButton("📄 PDF Glossary", callback_data="edvault:pdf"),
        types.InlineKeyboardButton("🗂️ Anki TSV", callback_data="edvault:anki"),
        types.InlineKeyboardButton("🗑️ Undo Last", callback_data="edvault:del"),
    )
    send_html_safe(chat_id, "\n\n".join(blocks), reply_markup=kb)

def show_radar(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>📡 Linguistic Radar</b>\nNo recurring error patterns logged yet. Keep chatting and practicing!", reply_markup=build_reply_kb(user_id))
        return
    lines = [f"• <s>{_h(r[0])}</s> → <b>{_h(r[1])}</b>" for r in rows]
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("🎯 Drill My Mistake Patterns", callback_data="edqznext:vault"))
    send_html_safe(chat_id, "<b>📡 Your Personal Linguistic Radar</b>\n<i>Recent adjustments &amp; collocation upgrades:</i>\n\n" + "\n".join(lines), reply_markup=kb)

def send_guide(chat_id: int, user_id: int):
    g = (
        "<b>Musa’s NYC Lounge — V24 Studio Guide</b>\n\n"
        "• <b>Top Bar:</b> Switch register (<b>Informal / Semi-formal / Formal</b>) or open your <b>💎 Vault</b> and <b>📡 Radar</b> in 1 tap.\n"
        "• <b>Bottom Bar (5 Core Modes):</b>\n"
        "1. <b>Chat:</b> Spontaneous NYC conversation with 1, 2, or all 3 hosts (toggle 🟢/⚪ anytime).\n"
        "2. <b>Lexicon:</b> 10-pillar dictionary &amp; activator (EN ⇄ AR) with IPA, Oxford Collocations, Hoey Priming &amp; Learner Traps.\n"
        "3. <b>Study:</b> Auto-detects English Drafts, Arabic-to-English thoughts, Articles/PDFs, or Nuance questions.\n"
        "4. <b>Podcast:</b> 4 formats with expandable transcripts, phonetic decoder &amp; live on-air call-ins.\n"
        "5. <b>Quiz:</b> 5-mode C1/C2 arena (Collocations, C2 Transformations, Pragmatics, Vault/Radar &amp; Active Writing)."
    )
    send_html_safe(chat_id, g, reply_markup=build_reply_kb(user_id))

# ------------------------------------------------------------------------------
# لوحة تحكم المالك للأعضاء والحصص اليومية (Admin Panel)
# ------------------------------------------------------------------------------
def send_admin_panel(chat_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id, name, username, status, daily_limit, used_today FROM members_v17 ORDER BY joined_at DESC LIMIT 20").fetchall()
    lines = ["<b>👑 Studio Host Admin Panel</b>\n"]
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🔗 Invite (15/day)", callback_data="adm:inv:15"),
        types.InlineKeyboardButton("🔗 Invite (30/day)", callback_data="adm:inv:30"),
        types.InlineKeyboardButton("🔗 Invite (∞)", callback_data="adm:inv:-1"),
    )
    for r in rows:
        u_id, nm, un, st, lim, used = r
        lim_s = "∞" if lim < 0 else str(lim)
        lines.append(f"• <b>{_h(nm)}</b> (<code>{u_id}</code>) — {st} [{used}/{lim_s}]")
        if u_id != PRIMARY_OWNER_ID:
            kb.row(types.InlineKeyboardButton(f"⚙️ {nm[:18]} ({used}/{lim_s})", callback_data=f"adm:view:{u_id}"))
    send_html_safe(chat_id, "\n".join(lines), reply_markup=kb)

def send_member_control_card(chat_id: int, target_uid: int):
    with db_connect() as conn:
        r = conn.execute("SELECT user_id, name, username, status, daily_limit, used_today FROM members_v17 WHERE user_id=?", (target_uid,)).fetchone()
    if not r:
        send_html_safe(chat_id, "Member not found.")
        return
    u_id, nm, un, st, lim, used = r
    lim_s = "Unlimited" if lim < 0 else f"{lim}/day"
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("10/d", callback_data=f"adm:set:{u_id}:10"),
        types.InlineKeyboardButton("25/d", callback_data=f"adm:set:{u_id}:25"),
        types.InlineKeyboardButton("50/d", callback_data=f"adm:set:{u_id}:50"),
        types.InlineKeyboardButton("∞", callback_data=f"adm:set:{u_id}:-1"),
    )
    kb.row(
        types.InlineKeyboardButton("🔄 Reset Today", callback_data=f"adm:rst:{u_id}"),
        types.InlineKeyboardButton("⏯️ Pause/Resume", callback_data=f"adm:tog:{u_id}"),
        types.InlineKeyboardButton("🔙 All Members", callback_data="adm:list"),
    )
    send_html_safe(
        chat_id,
        f"<b>⚙️ Member Settings</b>\n• <b>Name:</b> {_h(nm)}\n• <b>ID:</b> <code>{u_id}</code>\n• <b>Status:</b> {st}\n• <b>Quota:</b> {used} / {lim_s}",
        reply_markup=kb,
    )

# ------------------------------------------------------------------------------
# الموجه الرئيسي للرسائل (Incoming Message Router)
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
    en_name = _english_name(user_obj)
    txt = (message.text or message.caption or "").strip()
    low = txt.lower()

    if low.startswith("/start inv_"):
        inv_code = txt.split(maxsplit=1)[1].strip()
        redeemed_lim = redeem_invite_code(inv_code, user_obj)
        if redeemed_lim is not None:
            send_html_safe(
                chat_id,
                f"<b>Welcome to Musa’s NYC Lounge, {_h(en_name)}!</b>\nYour membership is active (<b>{redeemed_lim} sessions/day</b>).",
                reply_markup=build_reply_kb(uid),
            )
            return

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

    member = get_or_register_member(user_obj)
    if uid != PRIMARY_OWNER_ID and member["status"] != "approved":
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Request Membership Access", callback_data=f"reqacc:{uid}"))
        send_html_safe(chat_id, "<b>Musa’s NYC Lounge — Private Studio</b>\nTap below to request access from the host.", reply_markup=kb)
        return

    # أوامر التنقل العلوية والسفلية
    if low in ("/guide", "guide"):
        send_guide(chat_id, uid)
        return
    if low in ("/vault", "vault") or txt.startswith(("Vault", "💎")):
        show_vault(chat_id, uid)
        return
    if low in ("/radar", "radar") or txt.startswith(("Radar", "📡")):
        show_radar(chat_id, uid)
        return
    if low in ("/informal", "/casual", "/semiformal", "/semi-formal", "/professional", "/formal") or txt.startswith(("Informal", "Casual", "Semi-formal", "Professional", "Formal")):
        if "informal" in low or "casual" in low:
            r_target = "Informal"
        elif "semi" in low or "professional" in low:
            r_target = "Semi-formal"
        else:
            r_target = "Formal"
        cur_s = _get_session(uid)
        new_sub = "" if cur_s["mode"] == "PODCAST" else None
        _update_session(uid, register=r_target, sub_state=new_sub)
        reg_desc = {
            "Informal": "Spoken idioms, phrasal verbs & relaxed everyday English",
            "Semi-formal": "Polished, articulate standard English for general & educated contexts",
            "Formal": "Elevated C2 academic, analytical & diplomatic prose",
        }[r_target]
        send_html_safe(chat_id, f"<b>Register set to: {r_target}</b>\n<i>({reg_desc})</i>", reply_markup=build_reply_kb(uid))
        return
    if low in ("/start", "/help", "/new", "/chat", "/lounge") or txt.startswith(("Chat", "💬")):
        reg_now = _get_session(uid)["register"]
        openers = {
            "Informal": [
                ("Jake", f"Hey {en_name}—grab a seat. How's your day going so far, or what's on your mind?"),
                ("Chloe", f"Hey {en_name}! Good to see you in the lounge. What are we getting into today?"),
            ],
            "Semi-formal": [
                ("Chloe", f"Hi {en_name}. Whether you want to talk through a workplace situation or test an idea, the floor is yours."),
                ("Marcus", f"Hello {en_name}. What topic or scenario shall we unpack today?"),
            ],
            "Formal": [
                ("Marcus", f"Welcome, {en_name}. Share any policy question, theoretical argument, or thesis point you would like to examine."),
            ],
        }
        spk_op, line_op = random.choice(openers.get(reg_now, openers["Semi-formal"]))
        _update_session(
            uid,
            mode="CHAT",
            sub_state=("ROSTER:Chloe,Jake,Marcus" if low in ("/start", "/new") else None),
            clear_hist=(low in ("/start", "/new")),
            new_turn={"u": "[Joined the lounge]", "b": f"{spk_op}: {line_op}"},
        )
        send_html_safe(chat_id, f"<b>{spk_op}</b>\n{_h(line_op)}", reply_markup=build_reply_kb(uid))
        return
    if low in ("/lexicon", "/dict") or txt.startswith(("Lexicon", "📖")):
        _update_session(uid, mode="LEXICON", sub_state="")
        send_html_safe(
            chat_id,
            "<b>📖 10-Pillar Lexicon &amp; Activator (EN ⇄ AR)</b>\nSend any English word/idiom, Arabic concept, or <i>X vs Y</i> comparison:",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/study" or txt.startswith(("Study", "📚")):
        _update_session(uid, mode="STUDY", sub_state="")
        send_html_safe(
            chat_id,
            "<b>📚 Interactive Study Workbench</b>\n"
            "• <b>English Draft / Voice Note</b> ➔ Polished version + ❌➔✅ fixes + vocabulary upgrades.\n"
            "• <b>Arabic Thought / Paragraph</b> ➔ Spoken, Executive &amp; C2 Academic English bridge.\n"
            "• <b>Article / PDF</b> ➔ Top 4 chunks + on-demand C2 templates &amp; rhetoric.\n"
            "• <b>Question or Practice Sentence</b> ➔ Instant coaching &amp; register examples.",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/podcast" or txt.startswith(("Podcast", "🎙️")):
        _update_session(uid, mode="PODCAST", sub_state="")
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🎙️ Roundtable (3)", callback_data="edpod:roundtable"),
            types.InlineKeyboardButton("⚡ Clash Debate (2)", callback_data="edpod:clash"),
        )
        kb.row(
            types.InlineKeyboardButton("🔍 Deep Case Study", callback_data="edpod:case"),
            types.InlineKeyboardButton("💎 My Vault Episode", callback_data="edpod:vault"),
        )
        send_html_safe(
            chat_id,
            "<b>🎙️ NYC Podcast Studio</b>\nPick an episode format below, or type any topic right now and the hosts will go live on air:",
            reply_markup=kb,
        )
        return
    if low == "/quiz" or txt.startswith(("Quiz", "🎯")):
        ok_q, reason_q = check_and_consume_quota(user_obj)
        if not ok_q:
            lim_n = reason_q.split(":")[-1]
            send_html_safe(chat_id, f"<b>Daily Studio Limit Reached ({lim_n} sessions/day)</b>")
            return
        _update_session(uid, mode="QUIZ", sub_state="QUIZ_MC:colloc")
        # إرسال بطاقة السؤال الموحدة مباشرة بدون رسالة ثانية مكررة!
        send_quiz(chat_id, uid, "colloc")
        return

    ok_quota, q_reason = check_and_consume_quota(user_obj)
    if not ok_quota:
        lim_num = q_reason.split(":")[-1]
        send_html_safe(chat_id, f"<b>Daily Studio Limit Reached ({lim_num}/day)</b>\nYour quota resets tomorrow.")
        return

    try:
        bot.send_chat_action(chat_id, "typing")
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
                send_html_safe(chat_id, "Could not catch the audio clearly—please try speaking closer to the microphone.")
            elif _get_session(uid)["mode"] == "QUIZ" and _get_session(uid).get("sub_state", "").startswith("QUIZ_PRODUCE:"):
                t_ch = _get_session(uid)["sub_state"].split(":", 1)[1]
                evaluate_active_quiz_production(chat_id, uid, tr, t_ch, is_voice=True)
            elif _get_session(uid)["mode"] == "PODCAST" and _get_session(uid).get("sub_state", "").startswith("PODCAST_ACTIVE"):
                execute_podcast_call_in(chat_id, uid, tr, is_voice=True, user_first_name=en_name)
            elif _get_session(uid)["mode"] == "STUDY" or len(tr.split()) > 75:
                execute_study_and_miner(chat_id, uid, tr, is_voice=True)
            else:
                execute_chat_turn(chat_id, uid, tr, is_voice=True, user_first_name=en_name)
        elif txt:
            sess = _get_session(uid)
            mode, sub_st = sess["mode"], sess["sub_state"]
            if mode == "LEXICON":
                execute_lexicon_lookup(chat_id, uid, txt)
            elif mode == "STUDY":
                execute_study_and_miner(chat_id, uid, txt)
            elif mode == "QUIZ" and sub_st.startswith("QUIZ_PRODUCE:"):
                t_ch = sub_st.split(":", 1)[1]
                evaluate_active_quiz_production(chat_id, uid, txt, t_ch, is_voice=False)
            elif mode == "PODCAST":
                is_new_topic_req = low.startswith(("new:", "topic:", "episode:", "talk about", "discuss ", "debate "))
                if sub_st.startswith("PODCAST_ACTIVE") and not is_new_topic_req:
                    execute_podcast_call_in(chat_id, uid, txt, is_voice=False, user_first_name=en_name)
                else:
                    clean_t = re.sub(r"^(new|topic|episode|talk about)\s*:?\s*", "", txt, flags=re.I)
                    execute_podcast(chat_id, uid, topic=clean_t, fmt_style="roundtable", user_first_name=en_name)
            else:
                execute_chat_turn(chat_id, uid, txt, is_voice=False, user_first_name=en_name)
    except Exception:
        logger.error(f"Internal error: {traceback.format_exc()}")
        send_html_safe(chat_id, "<b>Chloe</b>\nGive me just a second—could you resend that thought?")

@bot.message_handler(content_types=["text", "voice", "audio", "photo", "document"])
def on_message(message: types.Message):
    threading.Thread(target=handle_incoming_message, args=(message,), daemon=True).start()

# ------------------------------------------------------------------------------
# الموجه الرئيسي للأزرار التفاعلية (Inline Callback Router — Zero Dead/Duplicate Branches)
# ------------------------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: types.CallbackQuery):
    user_obj = getattr(call, "from_user", None)
    uid = int(user_obj.id) if user_obj else PRIMARY_OWNER_ID
    en_name = _english_name(user_obj)
    chat_id = call.message.chat.id
    d = (call.data or "").strip()
    fb_txt = (call.message.text or call.message.caption or "").strip()

    if d.startswith("reqacc:"):
        m = get_or_register_member(user_obj)
        bot.answer_callback_query(call.id, "Request sent to the host!")
        adm_kb = types.InlineKeyboardMarkup()
        adm_kb.row(
            types.InlineKeyboardButton("Approve (15/d)", callback_data=f"adm:set:{uid}:15"),
            types.InlineKeyboardButton("Approve (30/d)", callback_data=f"adm:set:{uid}:30"),
            types.InlineKeyboardButton("Unlimited (∞)", callback_data=f"adm:set:{uid}:-1"),
        )
        send_html_safe(PRIMARY_OWNER_ID, f"🔔 <b>New Studio Access Request:</b> {_h(m['name'])} (<code>{uid}</code>)", reply_markup=adm_kb)
        return

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
            bot.answer_callback_query(call.id, "Approved!")
            send_member_control_card(chat_id, t_uid)
        elif act == "rst":
            t_uid = int(pts[2])
            set_member_quota(t_uid, reset_today=True)
            bot.answer_callback_query(call.id, "Counter reset!")
            send_member_control_card(chat_id, t_uid)
        elif act == "tog":
            t_uid = int(pts[2])
            with db_connect() as conn:
                r = conn.execute("SELECT status FROM members_v17 WHERE user_id=?", (t_uid,)).fetchone()
            new_st = "paused" if (r and r[0] == "approved") else "approved"
            set_member_quota(t_uid, status=new_st)
            bot.answer_callback_query(call.id, f"Status: {new_st}")
            send_member_control_card(chat_id, t_uid)
        elif act == "inv":
            lim_v = int(pts[2])
            code = create_invite_code(lim_v)
            b_user = _BOT_USERNAME or bot.get_me().username
            send_html_safe(chat_id, f"🔗 <b>Friend Invite Link ({lim_v}/day):</b>\nhttps://t.me/{b_user}?start={code}")
        return

    m_chk = get_or_register_member(user_obj)
    if uid != PRIMARY_OWNER_ID and m_chk["status"] != "approved":
        bot.answer_callback_query(call.id, "Private studio.")
        return

    if d.startswith("edtts:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        segs = c["voice_segs"] if c and c.get("voice_segs") else [("Jake", _clean_tts(fb_txt[:700]))]
        bot.answer_callback_query(call.id, "Preparing studio audio...")
        def _job():
            p = build_audio(segs, slow=("slow" in d))
            if p and os.path.exists(p):
                with open(p, "rb") as af:
                    bot.send_voice(chat_id, af)
                os.remove(p)
        threading.Thread(target=_job, daemon=True).start()
    elif d.startswith("edsave:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        items = c["vault_items"] if c and c.get("vault_items") else _extract_clean_vault_items(fb_txt, fb_txt[:180])
        saved_cnt = 0
        for it in items:
            if len(it) >= 3 and _save_vault_item(uid, it[0], it[1], it[2]):
                saved_cnt += 1
        sync_db_async(force=True)
        bot.answer_callback_query(call.id, f"Saved {saved_cnt} expression(s) to Vault!" if saved_cnt else "Saved/Updated in your Vault!")
    elif d.startswith("edqlk:"):
        _, idx_s, tok = d.split(":", 2)
        c = load_card(tok)
        terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        idx = int(idx_s) if idx_s.isdigit() else 0
        target_q = terms[idx] if idx < len(terms) else (terms[0] if terms else fb_txt.splitlines()[0][:40])
        bot.answer_callback_query(call.id, f"Lexicon: {target_q[:22]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, target_q), daemon=True).start()
    elif d.startswith("edqlk_direct:"):
        term_d = d.split(":", 1)[1]
        bot.answer_callback_query(call.id, f"Lexicon: {term_d[:22]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, term_d), daemon=True).start()
    elif d.startswith("edlexcard:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        target_q = terms[0] if terms else (c["source_text"].splitlines()[0][:45] if c and c.get("source_text") else fb_txt.splitlines()[0][:40])
        bot.answer_callback_query(call.id, f"Lexicon: {target_q[:22]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, target_q), daemon=True).start()
    elif d.startswith("edlexext:"):
        _, ext_type, tok = d.split(":", 2)
        c = load_card(tok)
        term = c["source_text"].splitlines()[0] if c and c.get("source_text") else fb_txt.splitlines()[0]
        bot.answer_callback_query(call.id, "Contrasting synonyms...")
        threading.Thread(target=execute_lexicon_extension, args=(chat_id, uid, term, ext_type), daemon=True).start()
    elif d.startswith("edroster:"):
        _, h_toggle, tok = d.split(":", 2)
        cur_r = list(_get_active_roster(uid))
        if h_toggle in cur_r:
            if len(cur_r) == 1:
                bot.answer_callback_query(call.id, f"At least one friend ({h_toggle}) must stay in the chat!")
                return
            cur_r.remove(h_toggle)
            action_note = f"{h_toggle} stepped out. Active: {', '.join(cur_r)}"
        else:
            cur_r.append(h_toggle)
            action_note = f"{h_toggle} joined! Active: {', '.join(cur_r)}"
        updated_r = _set_active_roster(uid, cur_r)
        bot.answer_callback_query(call.id, action_note)
        c = load_card(tok)
        l_terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=build_inline_kb(tok, "chat", l_terms, roster=updated_r))
        except Exception:
            pass
    elif d.startswith("edshift:"):
        _, sh, tok = d.split(":", 2)
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Transforming style...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, "", sh), daemon=True).start()
    elif d.startswith("edmore:"):
        pts = d.split(":")
        sub_m = pts[1] if len(pts) == 3 else "chunks"
        tok = pts[-1]
        c = load_card(tok)
        prev_str = c["mined_so_far"] if c and c.get("mined_so_far") else "previous batch"
        if sub_m == "skel":
            prev_str = f"SKEL_ONLY::{prev_str}"
        elif sub_m == "rhet":
            prev_str = f"RHET_ONLY::{prev_str}"
        bot.answer_callback_query(call.id, "Mining deep patterns...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, prev_str, ""), daemon=True).start()
    elif d.startswith("edqztext:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Building targeted drill...")
        threading.Thread(target=send_quiz, args=(chat_id, uid, "colloc", c["source_text"] if c else fb_txt), daemon=True).start()
    elif d.startswith("edpod:"):
        fmt_s = d.split(":", 1)[1]
        h_n = 3 if fmt_s in ("roundtable", "vault", "3") else 2
        f_name = fmt_s if fmt_s in ("roundtable", "clash", "case", "vault") else "roundtable"
        bot.answer_callback_query(call.id, f"Producing {f_name.capitalize()} episode...")
        threading.Thread(target=execute_podcast, args=(chat_id, uid, "", h_n, f_name, en_name), daemon=True).start()
    elif d.startswith("edqz:"):
        pts = d.split(":")
        ch = pts[1]
        q_mode_origin = pts[2] if len(pts) == 4 else "colloc"
        qid = pts[-1]
        with db_connect() as conn:
            r = conn.execute("SELECT quiz_json FROM quiz_v15 WHERE q_id=?", (qid,)).fetchone()
        if r:
            q = QuizSchema.model_validate_json(r[0])
            corr = q.correct_letter.strip().upper()[:1]
            is_right = (ch == corr)
            if is_right:
                _QUIZ_STREAK[uid] = _QUIZ_STREAK.get(uid, 0) + 1
            else:
                _QUIZ_STREAK[uid] = 0
                opt_map = {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d}
                _log_dna(uid, f"{opt_map.get(ch, ch)} -> {opt_map.get(corr, q.target_expression_en)}")

            bot.answer_callback_query(call.id, f"✓ Spot on! (Streak: {_QUIZ_STREAK[uid]})" if is_right else f"Best choice: {corr}")
            tok = hashlib.md5(f"QZR:{uid}:{time.time()}".encode()).hexdigest()[:10]
            v_segs = [("Marcus", _clean_tts(q.complete_native_sentence_en or q.scenario_or_stem_en))]
            t_clean, m_clean, c_clean = _clean_vault_entry(q.target_expression_en, q.winning_reason_en_ar, q.complete_native_sentence_en)
            store_card(tok, uid, v_segs, {"title": q.quiz_mode_badge_en, "main_en": q.complete_native_sentence_en}, [(t_clean, m_clean, c_clean)], source_text=t_clean)

            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton(f"➡️ Next ({q_mode_origin.upper()})", callback_data=f"edqznext:{q_mode_origin}"),
                types.InlineKeyboardButton("🎧 Hear", callback_data=f"edtts:1x:{tok}"),
                types.InlineKeyboardButton(f"🔍 {t_clean[:16]}", callback_data=f"edlexcard:{tok}"),
                types.InlineKeyboardButton("✍️ Use It", callback_data=f"edqztry:{tok}"),
                types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
            )
            verdict_hdr = (
                f"<b>✅ Correct ({corr})! • 🔥 Streak: {_QUIZ_STREAK[uid]}</b>"
                if is_right
                else f"<b>❌ You picked ({ch}) → ✅ Best Choice: ({corr})</b>"
            )
            res_html = (
                f"{verdict_hdr}\n\n"
                f"🗣️ <b>Complete Sentence:</b>\n<i>&quot;{_fmt_inline_md(q.complete_native_sentence_en)}&quot;</i>\n\n"
                f"<b>🎯 Why ({corr}) Wins:</b>\n{_fmt_inline_md(q.winning_reason_en_ar)}\n\n"
                f"<blockquote expandable>🔬 <b>Why the Other Options Fail ▼</b>\n{_clean_multiline_bullets(q.distractor_anatomy_en)}</blockquote>"
            )
            send_html_safe(chat_id, res_html, reply_markup=kb)
    elif d.startswith("edqztry:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        target_ch = c["source_text"] if c and c.get("source_text") else "hold off on"
        _update_session(uid, mode="QUIZ", sub_state=f"QUIZ_PRODUCE:{target_ch}")
        bot.answer_callback_query(call.id, "Your turn! Type or speak a sentence.")
        send_html_safe(chat_id, f"✍️ <b>Active Production Turn:</b>\nWrite or record a sentence right now using <code>{_h(target_ch)}</code> and I'll score and polish it!")
    elif d.startswith("edqznext:"):
        qm = d.split(":", 1)[1]
        bot.answer_callback_query(call.id, f"Loading {qm.upper()} drill...")
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
    # اختبار فلاتر الصوت والخزنة وتوليد رد سريع للتأكد من سلامة كافة الوحدات
    assert "المعنى" not in _clean_tts("hold off on — المعنى السياقي: تأجيل ❌ ➔ ✅")
    v_test = _extract_clean_vault_items("• simple word ➔ **iron out** (يذلل العقبات) — \"We need to iron out the details.\"", "context")
    assert v_test and v_test[0][0] == "iron out"
    oid = PRIMARY_OWNER_ID
    res_c = execute_chat_turn(oid, oid, "Hey Chloe, quick check on the new clean layout!", is_voice=False, user_first_name="Musa")
    if not res_c.primary_reply_en.strip():
        raise RuntimeError("Self-test failed: Empty primary_reply_en")
    print("✅ STUDIO_V18_VERIFIED_OK", flush=True)
    os._exit(0)

if __name__ == "__main__":
    print("🚀 Musa's NYC Lounge V24 Master Edition Polling Active...", flush=True)
    try:
        bot.remove_webhook()
    except Exception:
        pass
    sync_telegram_identity()
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
            bot.infinity_polling(timeout=25, long_polling_timeout=20, skip_pending=True)
        except Exception:
            time.sleep(3)
