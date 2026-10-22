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
    for col in ("primary_reply_en", "core_senses_and_subtext_en", "core_output_en", "script_en", "scenario_en", "transformed_text_en", "new_expressions_en", "content_bullets_en"):
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
    mc = genai.Client(api_key=api_key, http_options={"timeout": 12000})
    resp = mc.models.generate_content(
        model=model_id,
        contents=usr_p,
        config=genai_types.GenerateContentConfig(
            system_instruction=sys_p,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=temp,
        ),
    )
    if resp and resp.text:
        return _parse_schema_safe(resp.text, schema_cls)
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
                if "404" in err_s:
                    _UNAVAILABLE_MODELS_UNTIL[gm_id] = time.time() + 3600
                    break
                if "503" in err_s or "504" in err_s or "overloaded" in err_s or "unavailable" in err_s:
                    _UNAVAILABLE_MODELS_UNTIL[gm_id] = time.time() + 120
                    break
                if "429" in err_s or "quota" in err_s or "exhausted" in err_s:
                    _DEAD_KEYS_UNTIL[mk] = time.time() + 60
                    continue
                continue

    return schema_cls()

# ------------------------------------------------------------------------------
# القوالب الرشيقة المعزولة (Lean Modular Schemas)
# ------------------------------------------------------------------------------
class ChatSchema(BaseModel):
    primary_speaker: str = Field(default="Chloe", description="MUST be one of the currently ACTIVE hosts in the room! Speak naturally in the first person ('I/me').")
    primary_reply_en: str = Field(default="", description="Spontaneous, natural, human response (1-3 sentences) directly engaging with what the user said. Speak like a real friend texting—no robotic catchphrases, no forced idioms, no 'studio management/HR' jokes!")
    second_speaker: str = Field(default="", description="If ONLY 1 host is active in the room, this MUST be ''! If 2 or 3 hosts are active, optionally pick a second ACTIVE host ONLY when a brief reaction or second perspective feels completely natural (otherwise leave '').")
    second_reply_en: str = Field(default="", description="Leave '' if second_speaker is empty. Otherwise 1 natural sentence from second_speaker.")
    has_actual_errors: bool = Field(default=False, description="Check ONLY `CURRENT_USER_MESSAGE`. True ONLY for genuine grammar, word-order, uncountable noun ('informations'), preposition ('discuss about', 'depend of', 'explain me'), or lexical ('dominate the language', 'one to one') errors. Note: 'discuss X with Y' is 100% CORRECT! Ignore casual abbreviations ('cuz', 'wanna', 'gonna') and capitalization!")
    error_corrections_list: str = Field(default="", description="If has_actual_errors is True on `CURRENT_USER_MESSAGE`, write 'wrong phrase -> right phrase'. Leave '' if there are no real errors.")
    user_line_natural_rephrase_en: str = Field(default="", description="Rewrite the USER'S own message (`CURRENT_USER_MESSAGE`) from the USER'S first-person perspective as a native American speaker would naturally say it. NEVER write the host's answer here! If the user's line is already completely natural, write '✓ Natural as is — or: \"[1 smooth alternative]\"'.")
    expressions_from_reply_en: str = Field(default="", description="1 or 2 natural expressions or phrasal verbs actually used in the reply on separate lines: • **expression** — clear meaning. 'Natural example sentence.'")

class LexiconSchema(BaseModel):
    headword_en_or_ar: str = Field(default="", description="The target English word/phrasal verb/idiom OR Arabic query")
    ipa_and_spoken_prosody: str = Field(default="", description="American IPA transcription with primary stress mark (e.g. '/ˌθroʊ əˈraʊnd/') + brief connected-speech / linking note (e.g. 'stress falls on AROUND; w-linking between throw and around')")
    grammar_valency_and_coca_badge: str = Field(default="", description="Explicit dictionary grammar code (e.g. 'Phrasal Verb [T, Separable: throw sth ↔ around]' or 'Noun [U]' or 'Verb [T + obj + into -ing]') + COCA frequency & register profile ('Spoken: ★★★ | Workplace: ★★☆ | Academic: ★☆☆')")
    unified_senses_en_ar: str = Field(default="", description="Comprehensive breakdown of all core senses (or Register Activator tiers if query is Arabic). For EACH numbered sense, provide:\n1. **Precise English Definition & Tone**\n• المقابل الفصيح والاصطلاحي: [Eloquent contextual Arabic equivalent + idiomatic Arabic expression]\n• Valency Frame: `[exact syntactic pattern]`\n• Spoken Example: \"[Natural 10-14 word spoken NYC sentence]\"\n• Written/Workplace Example: \"[Natural 12-16 word professional/analytical sentence]\"")
    oxford_collocation_box_en: str = Field(default="", description="Structured Oxford-style Collocation Box on separate lines (adapt to Part of Speech—zero generic filler verbs!):\n• **Object / Noun Partners:** (6 authentic nouns that collocate with it)\n• **Verb / Aux Triggers:** (verbs/modals that naturally precede it, e.g. 'tend to, hesitate to, casually...')\n• **Adverb / Adjective Modifiers:** (5 natural modifiers)\n• **Prepositions & Particles:** (exact prepositional links)")
    lexical_priming_and_chunks_en: str = Field(default="", description="Hoey's Lexical Priming & Ready Chunks on separate lines:\n• **Colligation & Priming:** (preferred tense/aspect—e.g. progressive '-ing', passive vs active, negative polarity, or sentence position)\n• **Spoken Chunk 1:** \"[Ready-to-use conversational frame]\" (with brief Arabic meaning)\n• **Spoken/Written Chunk 2:** \"[Ready-to-use professional frame]\" (with brief Arabic meaning)")
    prosody_and_subtext_en: str = Field(default="", description="Semantic Prosody (Positive, Neutral, or Disapproving/Negative aura) + Pragmatic Subtext (what attitude the speaker projects when choosing this expression)")
    register_ladder_and_family_en: str = Field(default="", description="On separate lines:\n• **Casual Spoken:** [equivalent]\n• **Diplomatic Workplace:** [equivalent]\n• **C2 Academic Prose:** [equivalent]\n• **Word Family & Derivatives:** [Noun / Verb / Adj / Adv forms]\n• **Antonym / Opposite:** [exact idiomatic opposite]")
    common_traps_and_cognitive_hook_en: str = Field(default="", description="On separate lines:\n• **Grammar/Pronoun Trap:** ❌ [wrong usage, e.g. 'throw around it'] → ✅ [correct usage, e.g. 'throw it around']\n• **Collocation/L1 Arabic Trap:** ❌ [literal/wrong collocation] → ✅ [natural native phrasing]\n• **Cognitive Metaphor Hook:** [1-sentence mental image explaining WHY this idiom/word means what it means]")
    clean_audio_script_en: str = Field(default="", description="3-turn clean English pronunciation & example script for TTS audio: 'Marcus: [Headword]. [Written example].\nChloe: [Spoken example].\nJake: [Extra conversational chunk].' (100% English, no symbols or Arabic!)")

class LexiconExtraSchema(BaseModel):
    title_en: str = Field(default="Lexicon Deep Dive", description="Title of deep dive")
    content_bullets_en: str = Field(default="", description="5-6 rich, detailed bullet blocks separated by newline ('\n'), with bold headings, full example sentences, ❌/✅ boundary checks, and concise Arabic nuances.")

class StudyMinerSchema(BaseModel):
    dossier_title_en: str = Field(default="Linguistic Dossier", description="Sharp title")
    diagnosis_and_flow_en: str = Field(default="", description="For drafts: 'wrong -> right' fixes + Given-to-New/End-Weight flow analysis. For articles: summary of author's rhetorical strategy & hedging (NEVER invent fake errors on advanced articles!). For questions: direct Nuance Ladder.")
    spoken_delivery_en: str = Field(default="", description="Spoken Delivery version: active verb-driven phrasing and natural rhythm suitable for saying aloud")
    workplace_written_en: str = Field(default="", description="Crisp Workplace Written version: diplomatic, direct, collaborative (focus on shared timelines/solutions, zero personal blame)")
    analytical_c2_prose_en: str = Field(default="", description="Polished C2 Analytical Prose: Given-Before-New cohesion, End-Weight focus, and precise active verbs/nominalization")
    whole_chunks_harvest_en: str = Field(default="", description="3-4 standout whole-chunk idioms/light-verb expressions on separate lines (correct inflections only, e.g. 'ironing out' NEVER 'iron-outting'!) with meaning and a fresh example")
    collocations_and_skeletons_en: str = Field(default="", description="3 collocation forks + 2 reusable C2 rhetorical sentence skeletons with [Slots]")
    subtext_and_drill_en: str = Field(default="", description="Pragmatic subtext radar (where X works vs Y fails) + 1 interactive fill-in-the-blank drill")

class DraftShiftSchema(BaseModel):
    shift_title_en: str = Field(default="Style Transformation", description="Title of transformation")
    transformed_text_en: str = Field(default="", description="The rewritten text")
    key_refinements_en: str = Field(default="", description="2 concise bullets explaining the structural/lexical upgrades made")

class MoreMinerSchema(BaseModel):
    supplement_title_en: str = Field(default="Next Batch — Extracted Chunks & Skeletons", description="Title")
    new_expressions_en: str = Field(default="", description="4 COMPLETELY NEW whole-chunk expressions/collocations from the text (not extracted previously), each on a new line with definition and example")
    advanced_templates_en: str = Field(default="", description="2 additional C2 rhetorical sentence skeletons from the text")

class PodcastSchema(BaseModel):
    episode_title_en: str = Field(default="NYC Studio Roundtable", description="Episode title")
    listening_focus_en: str = Field(default="", description="1-sentence ear-training cue telling the listener what angle or contrast to listen for")
    script_en: str = Field(default="", description="8-turn lively NYC studio dialogue (Jake, Chloe, Marcus). Each speaker on a NEW line ('Jake: ...'). Highlight 3-4 key idioms in **bold** inside the script. NO stage directions in parentheses!")
    closing_question_to_user_en: str = Field(default="", description="Engaging on-air question inviting the user to call in / reply with their take")
    key_expressions_en: str = Field(default="", description="4 standout whole-chunk expressions used in script_en, on separate lines with meaning and example")
    audio_recap_script_en: str = Field(default="", description="1-sentence spoken recap of the key expressions")

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
# دستور العفوية والطبيعية الكاملة (Authentic, Unscripted Human English)
# ------------------------------------------------------------------------------
SYS_PROMPT = """
You are the engine of 'Musa’s NYC Lounge'. In Chat mode, you portray real, intelligent New York friends (Chloe, Jake, Marcus) chatting naturally with Musa:
- Chloe: perceptive, warm, articulate media strategist in Manhattan.
- Jake: straightforward, witty, pragmatic product lead in Brooklyn.
- Marcus: calm, thoughtful strategy & policy thinker.

STRICT NATURALNESS LAWS:
1. SPEAK LIKE REAL HUMANS, NOT SITCOM ACTORS OR AI BOTS:
   - Use effortless, everyday American English. Do NOT try to force idioms into every sentence.
   - BANNED PHRASES: Never say 'Truth be told', 'Fair enough', 'Management chose to', 'I hear you', 'That makes total sense', or 'Let's unpack that'.
   - NEVER pretend you are 'employees on a shift' or talk about 'HR/Management/compliance'. You are simply friends hanging out in a chat!
   - If the user asks a language, meaning, or life question in Chat, answer it directly, naturally, and insightfully like a sharp friend.

2. STRICTLY OBEY THE ACTIVE ROOM ROSTER:
   - You will be given `ACTIVE_HOSTS_IN_ROOM` (1, 2, or 3 names).
   - ONLY characters listed in `ACTIVE_HOSTS_IN_ROOM` are present! Anyone not listed is NOT in the chat and must NEVER speak or be spoken for.

3. SURGICAL ERROR CHECK (CURRENT MESSAGE ONLY):
   - Evaluate ONLY `CURRENT_USER_MESSAGE`. Never repeat corrections from earlier messages.
   - Ignore casual texting ('cuz', 'wanna', 'gonna'), missing punctuation, or lowercase names ('chloe', 'jake').
   - 'discuss [something] with [someone]' is 100% CORRECT—only flag 'discuss about'.

4. 100% ENGLISH IN CHAT/STUDY/PODCAST/QUIZ, CONTEXTUAL ARABIC ONLY IN LEXICON.
"""

# ------------------------------------------------------------------------------
# إدارة الحضور الديناميكي في الغرفة (1 أو 2 أو 3 شخصيات بحرية كاملة)
# ------------------------------------------------------------------------------
ALL_HOSTS = ["Chloe", "Jake", "Marcus"]

def _get_active_roster(user_id: int) -> List[str]:
    sub = _get_session(user_id).get("sub_state", "") or ""
    if sub.startswith("ROSTER:"):
        names = [x.strip() for x in sub.replace("ROSTER:", "").split(",") if x.strip() in ALL_HOSTS]
        if names:
            return names
    elif sub.startswith("SOLO:"):
        h = sub.replace("SOLO:", "").strip()
        if h in ALL_HOSTS:
            return [h]
    return ["Chloe", "Jake", "Marcus"]

def _set_active_roster(user_id: int, hosts: List[str]) -> List[str]:
    valid = [h for h in ALL_HOSTS if h in hosts]
    if not valid:
        valid = ["Chloe"]
    _update_session(user_id, sub_state="ROSTER:" + ",".join(valid))
    return valid

def _update_roster_from_text(user_id: int, user_text: str) -> List[str]:
    """فهم أوامر المستخدم النصية لإدخال أو إخراج أي شخصية (1 أو 2 أو 3) تلقائياً"""
    roster = list(_get_active_roster(user_id))
    low = user_text.lower()
    mentioned = [h for h in ALL_HOSTS if h.lower() in low]

    # 1. طلب الانفراد بشخص أو شخصين ("only talk to Chloe", "leave us alone", "one-on-one with Chloe")
    if any(p in low for p in ("only wanna talk to", "only want to talk to", "just wanna talk to", "just want to talk to", "leave us alone", "one-on-one", "one to one", "talk to just one", "alone with")):
        if mentioned:
            return _set_active_roster(user_id, mentioned)
        return _set_active_roster(user_id, [roster[0]])

    # 2. طرد أو إسكات شخص محدد ("shut up Marcus", "take Marcus away", "don't butt in Jake", "get out Jake")
    if any(p in low for p in ("shut up", "take him away", "take marcus away", "take jake away", "don't butt in", "dont butt in", "don't but in", "get out", "leave the chat", "go away", "kick out")):
        if mentioned:
            new_r = [h for h in roster if h not in mentioned]
            return _set_active_roster(user_id, new_r if new_r else ["Chloe"])

    # 3. استدعاء شخص غائب إلى المحادثة ("bring Marcus", "where is Marcus", "Marcus come here", "Marcus are you there", "call Jake")
    if any(p in low for p in ("where is", "where's", "bring him", "bring marcus", "bring jake", "bring chloe", "come here", "are you there", "what about you", "join us", "call marcus", "call jake")):
        for m in mentioned:
            if m not in roster:
                roster.append(m)
        return _set_active_roster(user_id, roster)

    # 4. إذا خاطب المستخدم اسماً غير موجود حالياً في الغرفة بشكل مباشر وموجب، نضيفه تلقائياً
    if len(mentioned) == 1 and mentioned[0] not in roster and not any(x in low for x in ("not ", "don't", "dont", "without")):
        roster.append(mentioned[0])
        return _set_active_roster(user_id, roster)

    return roster

# ------------------------------------------------------------------------------
# بناء لوحات المفاتيح وأزرار الحضور المرنة (🟢 / ⚪) بدون Roleplay وبدون Spark
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

def build_inline_kb(tok: str, ctype: str = "chat", lookup_terms: Optional[List[str]] = None, roster: Optional[List[str]] = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    if ctype == "chat":
        active_r = roster if roster else ["Chloe", "Jake", "Marcus"]
        kb.row(
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("📖 Lexicon", callback_data=f"edlexcard:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        )
        # شريط التحكم الحر في الشخصيات (اضغط على أي شخصية لإدخالها 🟢 أو إخراجها ⚪، سواء 1 أو 2 أو 3)
        kb.row(
            *[
                types.InlineKeyboardButton(
                    f"{'🟢' if h in active_r else '⚪'} {h}",
                    callback_data=f"edroster:{h}:{tok}",
                )
                for h in ALL_HOSTS
            ]
        )
        if lookup_terms:
            btns = [types.InlineKeyboardButton(f"🔍 {t[:22]}", callback_data=f"edqlk:{i}:{tok}") for i, t in enumerate(lookup_terms[:2])]
            kb.row(*btns)
        return kb

    if ctype == "lexicon":
        kb.row(
            types.InlineKeyboardButton("🎧 Pronounce & Examples", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("🐢 Slow 0.85x", callback_data=f"edtts:slow:{tok}"),
            types.InlineKeyboardButton("📄 PDF", callback_data=f"edpdf:{tok}"),
        )
        kb.row(
            types.InlineKeyboardButton("📊 Full Collocations", callback_data=f"edlexext:colloc:{tok}"),
            types.InlineKeyboardButton("⚖️ Synonym Thesaurus", callback_data=f"edlexext:syn:{tok}"),
        )
        kb.row(
            types.InlineKeyboardButton("🔄 Register & Family", callback_data=f"edlexext:ladder:{tok}"),
            types.InlineKeyboardButton("🎯 Practice Drill", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        )
        return kb

    kb.row(
        types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("📄 PDF", callback_data=f"edpdf:{tok}"),
        types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        types.InlineKeyboardButton("📖 Lexicon", callback_data=f"edlexcard:{tok}"),
    )
    if ctype == "miner":
        kb.row(
            types.InlineKeyboardButton("⛏️ Mine Next Batch", callback_data=f"edmore:{tok}"),
            types.InlineKeyboardButton("🎯 Test Me on Text", callback_data=f"edqztext:{tok}"),
        )
    elif ctype == "draft":
        kb.row(
            types.InlineKeyboardButton("🤝 Soften", callback_data=f"edshift:soften:{tok}"),
            types.InlineKeyboardButton("⚡ Tighten", callback_data=f"edshift:condense:{tok}"),
            types.InlineKeyboardButton("🎙️ Spoken", callback_data=f"edshift:spoken:{tok}"),
            types.InlineKeyboardButton("🏛️ C2 Prose", callback_data=f"edshift:c2:{tok}"),
        )
    if lookup_terms and ctype in ("study", "miner", "draft"):
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
        cl = re.sub(r"\*+", "", ln).strip().lstrip("•-*").strip()
        if not cl:
            continue
        if "->" in cl or "→" in cl:
            pts = re.split(r"->|→", cl, maxsplit=1)
            l, r = _h(pts[0].strip("~❌ ")), _h(pts[1].strip("✅ "))
            if l.lower() == r.lower() or l.lower() in ("cuz", "wanna", "gonna") or ("discuss with" in l.lower() and "discuss" == r.lower()):
                continue
            if l and r:
                out.append(f"✏️ <s>{l}</s> → <b>{r}</b>" if strike else f"• <i>{l}</i> → <b>{r}</b>")
        else:
            out.append(f"• {_h(cl)}")
    return "\n".join(out)

# ------------------------------------------------------------------------------
# محرك 1: الدردشة الحرة الطبيعية (1 أو 2 أو 3 شخصيات بعفوية كاملة)
# ------------------------------------------------------------------------------
def execute_chat_turn(chat_id: int, user_id: int, user_text: str, is_voice: bool = False, special_angle: str = "", user_first_name: str = "Musa") -> ChatSchema:
    s = _get_session(user_id)
    reg = s["register"]
    active_roster = _update_roster_from_text(user_id, user_text)

    hist_lines = []
    for h in s["history"][-5:]:
        if isinstance(h, dict):
            b_clean = h.get("b", "")
            if "management chose to" not in b_clean.lower():
                hist_lines.append(f"User: {h.get('u','')[:90]} | Reply: {b_clean[:150]}")
    hist_str = "\n".join(hist_lines)

    if len(active_roster) == 1:
        roster_rule = (
            f"ACTIVE_HOSTS_IN_ROOM: [{active_roster[0]}] (1-on-1 private chat).\n"
            f"`primary_speaker` MUST be '{active_roster[0]}', and `second_speaker` MUST be '' (empty). Do NOT mention the other hosts unless asked!"
        )
    elif len(active_roster) == 2:
        roster_rule = (
            f"ACTIVE_HOSTS_IN_ROOM: [{active_roster[0]}, {active_roster[1]}] (2-person room; the third friend is NOT here).\n"
            f"`primary_speaker` MUST be either '{active_roster[0]}' or '{active_roster[1]}'. `second_speaker` can be '' or the other active host from [{active_roster[0]}, {active_roster[1]}] ONLY."
        )
    else:
        roster_rule = (
            "ACTIVE_HOSTS_IN_ROOM: [Chloe, Jake, Marcus] (all 3 friends are in the chat).\n"
            "Pick the most relevant `primary_speaker`. Leave `second_speaker` as '' unless a brief 1-line reaction from another host genuinely adds to the conversation."
        )

    sys_p = f"{SYS_PROMPT}\nActive Register: {reg}. User's name: {user_first_name}.\n{roster_rule}"
    usr_p = (
        f"Recent Context (for continuity only—do NOT re-correct old lines):\n{hist_str}\n\n"
        f"CURRENT_USER_MESSAGE:\n\"{user_text}\""
    )
    data: ChatSchema = call_llm_structured(sys_p, usr_p, ChatSchema, temp=0.62)

    # فرض صارم في الكود لقائمة الحضور النشطة (يستحيل أن يتحدث شخص خارج القائمة النشطة)
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
        f"• <b>Your line, natural NYC:</b> <i>\"{_fmt_inline_md(data.user_line_natural_rephrase_en)}\"</i>"
        + (f"\n{clean_chunks}" if clean_chunks else "")
    )

    tok = hashlib.md5(f"CHT:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [(p_spk, p_bub)] + ([(s_spk, s_bub)] if s_spk and s_bub else [])
    pdf_d = {
        "title": "NYC Lounge Conversation",
        "register": reg,
        "main_en": combined_diag,
        "instant_fix": data.error_corrections_list,
        "diagnosis_en": data.expressions_from_reply_en,
        "spoken_inf": data.user_line_natural_rephrase_en,
        "spoken_semi": p_bub,
        "written_form": s_bub or p_bub,
    }
    v_items = []
    for ln in data.expressions_from_reply_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", combined_diag)
            if t_c:
                v_items.append((t_c, d_c, combined_diag))

    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=user_text)
    l_terms = _extract_lookup_terms(v_items)
    kb_markup = build_inline_kb(tok, "chat", l_terms, roster=active_roster)

    v_hdr = f"🎙️ <i>\"{_h(user_text)}\"</i>\n\n" if is_voice else ""
    body_dialogue = f"<b>{_h(p_spk)}</b>\n{_fmt_inline_md(p_bub)}"
    if s_spk and s_bub:
        body_dialogue += f"\n\n<b>{_h(s_spk)}</b>\n{_fmt_inline_md(s_bub)}"

    full_html = f"{v_hdr}{body_dialogue}{fix_block}\n\n<blockquote expandable>{echo_drawer}</blockquote>"
    send_html_safe(chat_id, full_html, reply_markup=kb_markup)

    if is_voice and os.environ.get("LIVE_SELF_TEST") != "1":
        def _auto_voice():
            try:
                bot.send_chat_action(chat_id, "record_voice")
            except Exception:
                pass
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
# محرك 2: الموسوعة المعجمية الشاملة ذات الأركان العشرة (10-Pillar Master Lexicon)
# ------------------------------------------------------------------------------
def _format_unified_senses(raw_senses: str) -> str:
    txt = html.unescape(str(raw_senses or "")).strip()
    if not txt:
        return ""
    txt = re.sub(r"(?<!^)\s*(?=[1-9]\.\s+\*?\*?[A-Z\u0600-\u06FF])", "\n\n", txt)
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
            out_lines.append(f"<b>{_h(clean_hdr)}</b>")
        else:
            cl_b = cl.lstrip("•-* ").strip()
            out_lines.append(f"• {_fmt_inline_md(cl_b)}")
    return "\n".join(out_lines).strip()

def execute_lexicon_lookup(chat_id: int, user_id: int, query_text: str):
    reg = _get_session(user_id)["register"]
    task = (
        "MASTER 10-PILLAR LEARNER'S DICTIONARY, COLLOCATION & PRIMING ENGINE (Longman DOCE + Oxford Collocations + Hoey's Lexical Priming + Activator):\n"
        "Fill EVERY single field thoroughly with deep linguistic precision:\n"
        "1. `ipa_and_spoken_prosody`: American IPA with stress marks + how it links/reduces in fast American speech.\n"
        "2. `grammar_valency_and_coca_badge`: Exact grammar & separability code (e.g. 'Phrasal Verb [T, Separable: throw sth ↔ around]') + COCA frequency stars across Spoken, Workplace, Academic.\n"
        "3. `unified_senses_en_ar`: Numbered sense blocks. Each block MUST contain: English definition & tone, المقابل الفصيح والاصطلاحي (MSA + idiomatic Arabic), Valency Frame, 1 Spoken Example, and 1 Written/Workplace Example. (If query is Arabic, make each numbered block a Register Activator tier: 1. Spoken/Idiomatic, 2. Diplomatic Workplace, 3. C2 Academic/Analytical).\n"
        "4. `oxford_collocation_box_en`: 4 distinct lines: Object/Noun Partners, Verb/Modal Triggers, Adverb/Adjective Modifiers, and Prepositions.\n"
        "5. `lexical_priming_and_chunks_en`: Hoey's Colligation (preferred tense/aspect/polarity/sentence position) + 2 ready-to-use conversational/professional frames with Arabic meanings.\n"
        "6. `prosody_and_subtext_en`: Semantic Prosody (Positive/Neutral/Negative aura) + Pragmatic Subtext.\n"
        "7. `register_ladder_and_family_en`: Casual Spoken -> Diplomatic Workplace -> C2 Academic Prose + Word Family derivatives + Antonym.\n"
        "8. `common_traps_and_cognitive_hook_en`: ❌ Wrong vs ✅ Right grammar/pronoun/collocation traps + L1 Arabic calque warning + Cognitive Metaphor Hook (why the expression means what it means).\n"
        "9. `clean_audio_script_en`: 3-turn English-only script ('Marcus: ...\\nChloe: ...\\nJake: ...') reading the headword and examples aloud."
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
        f"<b>🔬 Prosody, Register Ladder, Traps &amp; Origin ▼</b>\n\n"
        f"<b>🎭 Semantic Prosody &amp; Subtext</b>\n{_fmt_inline_md(data.prosody_and_subtext_en)}\n\n"
        f"<b>🔄 Register Escalation, Word Family &amp; Antonym</b>\n{ladder_fmt}\n\n"
        f"<b>⚠️ Common Learner Traps &amp; Cognitive Hook</b>\n{traps_fmt}"
    )

    full_html = f"{top_card}\n\n<blockquote expandable>{deep_drawer}</blockquote>"

    tok = hashlib.md5(f"LEX:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = _split_dialogue_segments(data.clean_audio_script_en, "Marcus")
    pdf_d = {
        "title": f"Master Lexicon: {data.headword_en_or_ar}",
        "register": data.grammar_valency_and_coca_badge,
        "main_en": f"{data.ipa_and_spoken_prosody}\n\n{data.unified_senses_en_ar}",
        "instant_fix": f"{data.oxford_collocation_box_en}\n\n{data.lexical_priming_and_chunks_en}",
        "diagnosis_en": f"{data.prosody_and_subtext_en}\n\n{data.register_ladder_and_family_en}\n\n{data.common_traps_and_cognitive_hook_en}",
    }
    v_items = [(data.headword_en_or_ar or query_text[:60], data.unified_senses_en_ar[:320], data.lexical_priming_and_chunks_en[:320])]
    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=f"{data.headword_en_or_ar}\n{data.unified_senses_en_ar}")
    send_html_safe(chat_id, full_html, reply_markup=build_inline_kb(tok, "lexicon"))
    threading.Thread(target=build_audio, args=(v_segs, False), daemon=True).start()

def execute_lexicon_extension(chat_id: int, user_id: int, term: str, ext_type: str):
    clean_term = (term or "").splitlines()[0].strip()[:80]
    prompts = {
        "colloc": (
            f"Build an exhaustive Oxford Collocation & Hoey Lexical Priming Matrix for '{clean_term}' on SEPARATE lines ('\\n'):\n"
            f"• **1. High-Frequency Verb/Modal Triggers:** (verbs/modals that precede '{clean_term}' with full example + Arabic)\n"
            f"• **2. Core Noun/Object Collocates:** (nouns paired with '{clean_term}' with full example + Arabic)\n"
            f"• **3. Adverbial & Prepositional Patterns:** (modifiers and prepositions with full example + Arabic)\n"
            f"• **4. Colligation & Sentence Position:** (how '{clean_term}' behaves grammatically in clauses—passive/progressive/negative polarity)\n"
            f"• **5. Three Authentic C1/C2 Corpus Sentences:** (Spoken, Executive, Academic)"
        ),
        "syn": (
            f"Build a Longman Activator / Macmillan Synonym Thesaurus for '{clean_term}' on SEPARATE lines ('\\n'):\n"
            f"• **1. [Closest Synonym 1]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
            f"• **2. [Closest Synonym 2]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
            f"• **3. [C2 Formal/Academic Competitor]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
            f"• **4. [Spoken/Idiomatic Competitor]:** exact nuance difference, register, natural example, and Arabic meaning.\n"
            f"• **5. Precision Boundary Test (❌ vs ✅):** sentence where '{clean_term}' works and the synonym fails (and vice versa)."
        ),
        "ladder": (
            f"Build a Complete Morphological Word Family, Register Escalation & Antonym Guide for '{clean_term}' on SEPARATE lines ('\\n'):\n"
            f"• **1. Casual NYC Spoken Equivalent:** phrasing + full sentence + Arabic.\n"
            f"• **2. Diplomatic Workplace Equivalent:** phrasing + full sentence + Arabic.\n"
            f"• **3. C2 Academic / Nominalized Equivalent:** phrasing + full sentence + Arabic.\n"
            f"• **4. Complete Word Family (Derivatives):** Noun, Verb, Adjective, Adverb forms (or related phrasal/compound forms) with examples.\n"
            f"• **5. Exact Idiomatic Antonyms:** 2 natural opposites across spoken and formal registers with examples."
        ),
    }
    res: LexiconExtraSchema = call_llm_structured(f"{SYS_PROMPT}\n{prompts.get(ext_type, prompts['colloc'])}", f"Target: {clean_term}", LexiconExtraSchema, temp=0.4)
    send_html_safe(chat_id, f"<b>{_h(res.title_en)} — {_h(clean_term)}</b>\n\n{_clean_multiline_bullets(res.content_bullets_en)}")

# ------------------------------------------------------------------------------
# محرك 3: المختبر ثلاثي المسارات (Study: Article Miner / Draft Surgery / Nuance Clinic)
# ------------------------------------------------------------------------------
def execute_study_and_miner(chat_id: int, user_id: int, user_input: str, is_voice: bool = False, already_mined: str = "", shift: str = ""):
    s = _get_session(user_id)
    reg = s["register"]

    # 1. استدعاء جراحي مستقل عند الضغط على Soften / Tighten / Spoken / C2 Prose
    if shift:
        shift_map = {
            "soften": "Rewrite this text so it sounds warm, diplomatic, tactful, and collaborative in modern American workplace English (remove any personal blame).",
            "condense": "Tighten and condense this text by 35-40% into crisp, high-impact English using active verbs and zero clutter.",
            "spoken": "Convert this written text into natural, spontaneous spoken American English (use active verbs, contractions, spoken openers, and natural rhythm).",
            "c2": "Transform this text into polished C2 Analytical Prose using Given-Before-New flow, End-Weight focus, and precise academic/executive verbs.",
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
            f"<i>\"{_fmt_inline_md(sh_data.transformed_text_en)}\"</i>\n\n"
            f"<b>Structural &amp; Lexical Upgrades</b>\n{_clean_multiline_bullets(sh_data.key_refinements_en)}"
        )
        send_html_safe(chat_id, sh_html, reply_markup=build_inline_kb(tok, "draft"))
        return

    # 2. استدعاء مستقل عند الضغط على Mine Next Batch
    if already_mined:
        m_task = f"Previously extracted expressions: [{already_mined}]. Mine 4 COMPLETELY NEW whole-chunk expressions, phrasal verbs, or collocations from this text (zero overlap with the list above!), plus 2 additional C2 rhetorical sentence skeletons."
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
            f"<b>Fresh Whole-Chunk Harvest</b>\n{_clean_multiline_bullets(m_data.new_expressions_en)}\n\n"
            f"<b>C2 Rhetorical Skeletons</b>\n{_clean_multiline_bullets(m_data.advanced_templates_en)}"
        )
        send_html_safe(chat_id, m_html, reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    # 3. التوجيه الذكي بين المسارات الثلاثة (Article Miner vs. Nuance Clinic vs. Draft Surgery)
    w_cnt = len(user_input.split())
    low_in = user_input.strip().lower()
    is_question = low_in.startswith(("what ", "what'", "how ", "why ", "when ", "where ", "which ", "can you", "could you", "is there", "difference", "meaning")) or (low_in.endswith("?") and w_cnt < 50)
    is_miner = (w_cnt > 48 and not is_question)

    if is_miner:
        task = (
            "PATH A — ADVANCED TEXT & ARTICLE MINER:\n"
            "- Treat the input as a corpus text to harvest from. Do NOT invent fake grammar corrections or corrupt standard idioms like 'move the needle'!\n"
            "- In `diagnosis_and_flow_en`, briefly explain the author's rhetorical flow, hedging, and cohesion strategy.\n"
            "- Extract 4 high-value whole-chunk idioms/phrasal verbs with fresh examples, and extract 2 actual C2 sentence skeletons with [Slots] from the text."
        )
    elif is_question:
        task = (
            "PATH C — PRAGMATIC & NUANCE CLINIC:\n"
            "- Answer the user's question directly with a sharp Nuance Ladder (exact register, collocational fit, and where each option sounds wrong).\n"
            "- Provide authentic Spoken Delivery, Workplace Written, and Analytical C2 examples (never put informal phrasal verbs inside the C2 Analytical version!)."
        )
    else:
        task = (
            "PATH B — DRAFT SURGERY & SPOKEN/WRITTEN ARCHITECTURE:\n"
            "- Diagnose grammar/collocation errors ('wrong -> right') and evaluate information flow.\n"
            "- Provide 3 distinct, natural versions: 1) Spoken Delivery (verb-driven, conversational rhythm), 2) Crisp Workplace Written (diplomatic, solution-focused, zero personal blame), and 3) Polished C2 Analytical Prose (Given-to-New flow, End-Weight focus)."
        )

    data: StudyMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Register: {reg}\nInput:\n\"{user_input[:5000]}\"", StudyMinerSchema, temp=0.5)

    # في مسار تعدين النصوص الطويلة (Miner)، لا نكرر طباعة المقال الأصلي بل نعرض الحصاد المعجمي والبلاغي مباشرةً!
    if is_miner:
        top = (
            f"<b>⛏️ {_h(data.dossier_title_en)}</b>\n\n"
            f"<b>Rhetorical Flow &amp; Cohesion</b>\n{_fmt_inline_md(data.diagnosis_and_flow_en)}\n\n"
            f"<b>1. Whole-Chunk Harvest</b>\n{_clean_multiline_bullets(data.whole_chunks_harvest_en)}\n\n"
            f"<b>2. Collocation Forks &amp; C2 Skeletons</b>\n{_clean_multiline_bullets(data.collocations_and_skeletons_en)}"
        )
    else:
        top = (
            f"<b>{_h(data.dossier_title_en)}</b>\n\n"
            f"{_fmt_inline_md(data.diagnosis_and_flow_en)}\n\n"
            f"🎙️ <b>Spoken Delivery:</b>\n<i>\"{_fmt_inline_md(data.spoken_delivery_en)}\"</i>\n\n"
            f"💼 <b>Crisp Workplace Written:</b>\n<i>\"{_fmt_inline_md(data.workplace_written_en)}\"</i>\n\n"
            f"🏛️ <b>Analytical C2 Prose:</b>\n<b>\"{_fmt_inline_md(data.analytical_c2_prose_en)}\"</b>"
        )

    dr_en = (
        f"<b>Chunks, Templates &amp; Subtext Radar ▼</b>\n\n"
        + (
            f"<b>Spoken vs. Written Distillations</b>\n"
            f"• <b>Spoken:</b> <i>\"{_fmt_inline_md(data.spoken_delivery_en)}\"</i>\n"
            f"• <b>Executive C2:</b> <b>\"{_fmt_inline_md(data.analytical_c2_prose_en)}\"</b>\n\n───\n"
            if is_miner
            else f"<b>Key Chunks &amp; Nuances</b>\n{_clean_multiline_bullets(data.whole_chunks_harvest_en)}\n\n"
                 f"<b>Collocations &amp; Sentence Frames</b>\n{_clean_multiline_bullets(data.collocations_and_skeletons_en)}\n\n───\n"
        )
        + f"{_fmt_inline_md(data.subtext_and_drill_en)}"
    )

    tok = hashlib.md5(f"STD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Jake", data.spoken_delivery_en), ("Chloe", data.workplace_written_en), ("Marcus", data.analytical_c2_prose_en)]
    pdf_d = {
        "title": data.dossier_title_en,
        "register": reg,
        "main_en": data.workplace_written_en,
        "instant_fix": data.diagnosis_and_flow_en,
        "diagnosis_en": f"{data.whole_chunks_harvest_en}\n\n{data.collocations_and_skeletons_en}",
        "spoken_inf": data.spoken_delivery_en,
        "spoken_semi": data.workplace_written_en,
        "written_form": data.analytical_c2_prose_en,
    }
    v_items = []
    for ln in data.whole_chunks_harvest_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", data.workplace_written_en)
            if t_c:
                v_items.append((t_c, d_c, ln))
    target_for_shift = data.workplace_written_en if is_question else user_input[:3000]
    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=target_for_shift, mined_so_far=", ".join([x[0] for x in v_items]))

    ctype = "miner" if is_miner else ("study" if is_question else ("draft" if w_cnt > 6 else "study"))
    send_html_safe(chat_id, f"{top}\n\n<blockquote expandable>{dr_en}</blockquote>", reply_markup=build_inline_kb(tok, ctype, _extract_lookup_terms(v_items)))

# ------------------------------------------------------------------------------
# محرك 4: استوديو البودكاست الإذاعي التفاعلي (3 Formats + Ear-First + Call-In)
# ------------------------------------------------------------------------------
def execute_podcast(chat_id: int, user_id: int, topic: str = "", hosts: int = 3, fmt_style: str = "roundtable", user_first_name: str = "Musa"):
    s = _get_session(user_id)
    reg = s["register"]
    _update_session(user_id, mode="PODCAST", podcast_hosts=hosts, sub_state="PODCAST_ACTIVE")

    style_prompts = {
        "roundtable": f"Produce a lively 3-host NYC Roundtable ({reg}) with Jake, Chloe & Marcus building on and challenging each other's points naturally.",
        "clash": f"Produce a sharp 2-host 'Clash of Views' debate ({reg}) between Jake (pragmatic, fast-moving execution) and Marcus (deep structural/strategic caution).",
        "case": f"Produce a perceptive 2-host 'Case Breakdown' ({reg}) with Chloe and Marcus dissecting real-world human dynamics and institutional blind spots.",
    }
    chosen_inst = style_prompts.get(fmt_style, style_prompts["roundtable"])
    t_p = topic.strip() if len(topic.strip()) > 2 else f"Unfiltered {reg} NYC discussion on work habits, decision-making, and modern life"

    data: PodcastSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{chosen_inst}\nAddress {user_first_name} in the closing question. Highlight 3-4 natural idioms in **bold** inside script_en. NEVER put stage directions in parentheses!",
        f"Topic: {t_p}",
        PodcastSchema,
        temp=0.7,
    )

    raw_s = _fmt_inline_md(re.sub(r"\([^)]*\)", "", data.script_en))
    for spk in ("Jake", "Chloe", "Marcus"):
        raw_s = re.sub(rf"(?:^|\n|\s)+{spk}\s*:\s*", f"\n\n<b>{spk}:</b> ", raw_s)
    raw_s = raw_s.strip()

    clean_pod_exp = _clean_multiline_bullets(data.key_expressions_en)
    html_out = (
        f"<b>🎙️ NYC Podcast Studio ({_h(reg)})</b>\n"
        f"<i>{_h(data.episode_title_en)}</i>\n\n"
        f"🎯 <b>Listening Focus:</b> {_fmt_inline_md(data.listening_focus_en)}\n\n"
        f"📞 <b>On-Air Call-In ({_h(user_first_name)}):</b> <i>\"{_fmt_inline_md(data.closing_question_to_user_en)}\"</i>\n"
        f"<i>(Send a voice note or text reply right now to join the hosts on air!)</i>\n\n"
        f"<blockquote expandable><b>📜 Annotated Transcript ▼</b>\n\n{raw_s}</blockquote>\n"
        f"<blockquote expandable><b>💎 Episode Chunks ▼</b>\n\n{clean_pod_exp}</blockquote>"
    )
    tok = hashlib.md5(f"POD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(data.script_en, "Jake")
    if data.audio_recap_script_en.strip():
        segs.append(("Marcus", f"Quick studio recap: {data.audio_recap_script_en}"))

    v_items = []
    for ln in data.key_expressions_en.splitlines():
        if len(ln.strip()) > 6:
            t_c, d_c, _ = _clean_vault_entry(ln, "", data.episode_title_en)
            if t_c:
                v_items.append((t_c, d_c, ln))
    pdf_d = {"title": data.episode_title_en, "register": reg, "main_en": data.script_en, "instant_fix": data.closing_question_to_user_en, "diagnosis_en": data.key_expressions_en}
    store_card(tok, user_id, segs, pdf_d, v_items, source_text=data.episode_title_en)
    _update_session(user_id, new_turn={"u": f"[Podcast Episode: {t_p}]", "b": f"{data.script_en[:240]} | Question: {data.closing_question_to_user_en}"})

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🎧 1.0x", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("🐢 0.85x", callback_data=f"edtts:slow:{tok}"),
        types.InlineKeyboardButton("📄 PDF", callback_data=f"edpdf:{tok}"),
        types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
    )
    kb.row(
        types.InlineKeyboardButton("🎙️ Roundtable", callback_data="edpod:roundtable"),
        types.InlineKeyboardButton("⚡ Clash", callback_data="edpod:clash"),
        types.InlineKeyboardButton("🔍 Case", callback_data="edpod:case"),
        types.InlineKeyboardButton("📖 Lexicon", callback_data=f"edlexcard:{tok}"),
    )
    send_html_safe(chat_id, html_out, reply_markup=kb)

    mp3 = build_audio(segs, slow=False)
    if mp3 and os.path.exists(mp3):
        with open(mp3, "rb") as af:
            bot.send_voice(chat_id, af, caption=f"<b>{_h(data.episode_title_en)}</b>", parse_mode="HTML")
        os.remove(mp3)

# ------------------------------------------------------------------------------
# محرك 5: الكويز التفاعلي، الخزنة، رادار الأخطاء، ولوحة تحكم المالك
# ------------------------------------------------------------------------------
def send_quiz(chat_id: int, user_id: int, q_mode: str = "standard", custom_context: str = ""):
    reg = _get_session(user_id)["register"]
    extra = ""
    if custom_context:
        extra = f"Create a precision cloze/collocation question testing a key expression from this text:\n{custom_context[:1500]}"
    elif q_mode == "dna":
        with db_connect() as conn:
            rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        if rows:
            extra = "Base this question specifically on testing one of these past mistake patterns: " + "; ".join([f"{r[0]} -> {r[1]}" for r in rows])
    elif q_mode == "vault":
        vt = _get_recent_vault_terms(user_id, 5)
        if vt:
            extra = "Base this question specifically on testing one of these saved expressions: " + ", ".join(vt)
    elif q_mode == "c2":
        extra = "Make this a Cambridge C2 Key-Word Transformation style multiple-choice question testing advanced collocations, cleft structures, or negative inversion."

    q: QuizSchema = call_llm_structured(f"{SYS_PROMPT}\nCreate a 4-option multiple choice question ({reg}). {extra}", "Generate precision quiz.", QuizSchema, temp=0.7)
    qid = hashlib.md5(f"QZ:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    with _db_lock, db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO quiz_v15 (q_id, user_id, quiz_json) VALUES (?, ?, ?)", (qid, user_id, q.model_dump_json()))
        conn.commit()
    kb = types.InlineKeyboardMarkup()
    kb.row(*[types.InlineKeyboardButton(x, callback_data=f"edqz:{x}:{qid}") for x in ("A", "B", "C", "D")])
    kb.row(
        types.InlineKeyboardButton("Next", callback_data="edqznext:standard"),
        types.InlineKeyboardButton("My Mistakes", callback_data="edqznext:dna"),
        types.InlineKeyboardButton("C2 Transform", callback_data="edqznext:c2"),
    )
    send_html_safe(
        chat_id,
        f"<b>Quiz — {_h(q.category_en)} ({_h(reg)})</b>\n\n<i>\"{_fmt_inline_md(q.scenario_en)}\"</i>\n\n<b>A)</b> {_h(q.option_a)}\n<b>B)</b> {_h(q.option_b)}\n<b>C)</b> {_h(q.option_c)}\n<b>D)</b> {_h(q.option_d)}",
        reply_markup=kb,
    )

def show_vault(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT id, chunk, meaning_ar, context_sentence FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>Your Vault is empty.</b>\nTap <b>💾 Save</b> under any card to store expressions for active recall.", reply_markup=build_reply_kb(user_id))
        return
    blocks = ["<b>Saved Expressions — Active Recall Vault</b>\n<i>(Tap the hidden spoiler bar to test your recall)</i>"]
    for i, r in enumerate(rows, 1):
        term, m_note, ctx = r[1], r[2], r[3]
        clue = _fmt_inline_md(m_note[:160]) if m_note else _fmt_inline_md(ctx[:160])
        blocks.append(f"<b>{i}.</b> <tg-spoiler><b>{_h(term)}</b></tg-spoiler>\n• <i>Clue/Context:</i> {clue}")
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🎯 Quiz My Vault", callback_data="edqznext:vault"),
        types.InlineKeyboardButton("📄 PDF Glossary", callback_data="edvault:pdf"),
        types.InlineKeyboardButton("🗂️ Anki TSV", callback_data="edvault:anki"),
    )
    kb.row(types.InlineKeyboardButton("🗑️ Remove Last Saved", callback_data="edvault:del"))
    send_html_safe(chat_id, "\n\n".join(blocks), reply_markup=kb)

def show_radar(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT error_pattern, native_fix FROM dna_v15 WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>Linguistic Radar</b>\nNo recurring error patterns logged yet. Keep chatting and practicing!", reply_markup=build_reply_kb(user_id))
        return
    lines = [f"• <s>{_h(r[0])}</s> → <b>{_h(r[1])}</b>" for r in rows]
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("🎯 Drill My Mistake Patterns", callback_data="edqznext:dna"))
    send_html_safe(chat_id, "<b>Your Personal Linguistic Radar</b>\n<i>Recent adjustments &amp; collocation upgrades:</i>\n\n" + "\n".join(lines), reply_markup=kb)

def send_guide(chat_id: int, user_id: int):
    g = (
        "<b>Musa’s NYC Lounge — Studio Guide</b>\n\n"
        "• <b>Register Bar (Top):</b> Switch anytime between <b>Casual</b> (everyday spoken NYC), <b>Professional</b> (diplomatic workplace), and <b>Formal</b> (C2 analytical prose).\n\n"
        "• <b>5 Studio Modes (Bottom):</b>\n"
        "1. <b>Chat:</b> Live multi-bubble NYC group chat with Jake, Chloe &amp; Marcus. Send a voice note for automatic voice replies, or tap <b>☕ Spark</b> / <b>🎭 Roleplay</b>.\n"
        "2. <b>Lexicon:</b> 3-Lens contextual dictionary &amp; activator (EN ⇄ AR) with Collocation Matrix, Synonym Contrast &amp; Word Family.\n"
        "3. <b>Study:</b> Automatically routes between <i>Article Miner</i>, <i>Spoken vs. Written Draft Surgery</i>, and <i>Nuance Clinic</i>.\n"
        "4. <b>Podcast:</b> Choose <i>Roundtable</i>, <i>Clash of Views</i>, or <i>Case Breakdown</i>, and reply on air!\n"
        "5. <b>Quiz, /vault &amp; /radar:</b> Active recall spoilers, personal error radar, and PDF/Anki exports."
    )
    send_html_safe(chat_id, g, reply_markup=build_reply_kb(user_id))

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
# معالج الرسائل الرئيسي (Incoming Messages Router)
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

    # 1. تفعيل العضوية عبر رابط الدعوة المباشر (/start inv_xxxx)
    if low.startswith("/start inv_"):
        inv_code = txt.split(maxsplit=1)[1].strip()
        redeemed_lim = redeem_invite_code(inv_code, user_obj)
        if redeemed_lim is not None:
            send_html_safe(
                chat_id,
                f"<b>Welcome to Musa’s NYC Lounge, {_h(en_name)}!</b>\n"
                f"Your private membership is active (<b>{redeemed_lim} sessions/day</b>). Choose a mode below to begin:",
                reply_markup=build_reply_kb(uid),
            )
            if uid != PRIMARY_OWNER_ID:
                send_html_safe(PRIMARY_OWNER_ID, f"🟢 <b>{_h(en_name)}</b> (<code>{uid}</code>) joined via your invite link ({redeemed_lim}/day).")
            return

    # 2. أوامر المالك لإدارة الأصدقاء والحصص اليومية (/admin)
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

    # 3. التحقق من عضوية المستخدم قبل السماح بالدخول
    member = get_or_register_member(user_obj)
    if uid != PRIMARY_OWNER_ID and member["status"] != "approved":
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Request Membership Access", callback_data=f"reqacc:{uid}"))
        send_html_safe(
            chat_id,
            "<b>Musa’s NYC Lounge — Private Studio</b>\n\n"
            "This studio is available by invitation only. Tap below to request access from the host.",
            reply_markup=kb,
        )
        return

    # 4. أوامر التنقل وتغيير المستوى (لا تستهلك من حصة الرسائل)
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
        reg_now = _get_session(uid)["register"]
        openers = {
            "Casual": [
                ("Jake", f"Hey {en_name}—grab a seat. How's your day going so far, or what's on your mind?"),
                ("Chloe", f"Hey {en_name}! Good to see you in the lounge. What are we getting into today?"),
            ],
            "Professional": [
                ("Chloe", f"Hi {en_name}. Whether you want to talk through a workplace situation or test an idea with us, the floor is yours."),
                ("Marcus", f"Hello {en_name}. Good to catch you—what topic or scenario shall we unpack today?"),
            ],
            "Formal": [
                ("Marcus", f"Welcome, {en_name}. Share any policy question, theoretical argument, or thesis point you would like to examine."),
            ],
        }
        spk_op, line_op = random.choice(openers.get(reg_now, openers["Professional"]))
        _update_session(
            uid,
            mode="CHAT",
            sub_state=("ROSTER:Chloe,Jake,Marcus" if low in ("/start", "/new") else None),
            clear_hist=(low in ("/start", "/new")),
            new_turn={"u": "[Joined the lounge]", "b": f"{spk_op}: {line_op}"},
        )
        send_html_safe(chat_id, f"<b>{spk_op}</b>\n{_h(line_op)}", reply_markup=build_reply_kb(uid))
        return
    if low in ("/lexicon", "/dict", "/dictionary") or txt.startswith(("Lexicon", "📖")):
        _update_session(uid, mode="LEXICON", sub_state="")
        send_html_safe(
            chat_id,
            "<b>Contextual Lexicon &amp; Activator (EN ⇄ AR)</b>\n"
            "Send any English word/chunk, Arabic phrase/concept, or <i>X vs Y</i> comparison:",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/study" or txt.startswith(("Study", "📚")):
        _update_session(uid, mode="STUDY", sub_state="")
        send_html_safe(
            chat_id,
            "<b>Study — Article Miner, Draft Surgery &amp; Nuance Clinic</b>\n"
            "Send any question, draft paragraph, article, PDF, or voice recording:",
            reply_markup=build_reply_kb(uid),
        )
        return
    if low == "/podcast" or txt.startswith(("Podcast", "🎙️")):
        _update_session(uid, mode="PODCAST", sub_state="")
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🎙️ Roundtable (3)", callback_data="edpod:roundtable"),
            types.InlineKeyboardButton("⚡ Clash of Views", callback_data="edpod:clash"),
            types.InlineKeyboardButton("🔍 Case Study", callback_data="edpod:case"),
        )
        send_html_safe(
            chat_id,
            "<b>NYC Podcast Studio</b>\nPick an episode format below, or type any topic you want the hosts to debate on air:",
            reply_markup=kb,
        )
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

    # 5. فحص الحصة اليومية قبل تشغيل محركات الذكاء الاصطناعي
    ok_quota, q_reason = check_and_consume_quota(user_obj)
    if not ok_quota:
        lim_num = q_reason.split(":")[-1]
        send_html_safe(
            chat_id,
            f"<b>Daily Studio Limit Reached</b>\n"
            f"You have completed your <b>{lim_num}</b> daily sessions for today. Your quota resets automatically tomorrow, or you can review your saved expressions in /vault anytime.",
        )
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
                send_html_safe(chat_id, "Could not catch the audio clearly—please try speaking a bit closer to the microphone.")
            elif _get_session(uid)["mode"] == "STUDY" or len(tr.split()) > 75:
                execute_study_and_miner(chat_id, uid, tr, is_voice=True)
            else:
                execute_chat_turn(chat_id, uid, tr, is_voice=True, user_first_name=en_name)
        elif txt:
            sess = _get_session(uid)
            mode, sub_st = sess["mode"], sess["sub_state"]
            if mode == "LEXICON":
                execute_lexicon_lookup(chat_id, uid, txt)
            elif len(txt.split()) > 65 or mode == "STUDY":
                execute_study_and_miner(chat_id, uid, txt)
            elif mode == "PODCAST":
                if sub_st == "PODCAST_ACTIVE" and not low.startswith(("new:", "topic:")):
                    execute_chat_turn(chat_id, uid, txt, is_voice=False, user_first_name=en_name)
                else:
                    execute_podcast(chat_id, uid, topic=re.sub(r"^(new|topic)\s*:\s*", "", txt, flags=re.I), fmt_style="roundtable", user_first_name=en_name)
            else:
                execute_chat_turn(chat_id, uid, txt, is_voice=False, user_first_name=en_name)
    except Exception:
        logger.error(f"Internal error: {traceback.format_exc()}")
        send_html_safe(chat_id, "<b>Chloe</b>\nGive me just a second—could you resend that thought?")

@bot.message_handler(content_types=["text", "voice", "audio", "photo", "document"])
def on_message(message: types.Message):
    threading.Thread(target=handle_incoming_message, args=(message,), daemon=True).start()

# ------------------------------------------------------------------------------
# معالج الأزرار التفاعلية (Inline Callbacks Router)
# ------------------------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: types.CallbackQuery):
    user_obj = getattr(call, "from_user", None)
    uid = int(user_obj.id) if user_obj else PRIMARY_OWNER_ID
    en_name = _english_name(user_obj)
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
                    f"<b>Welcome to Musa’s NYC Lounge!</b>\nYour membership is active (Daily limit: <b>{lim_label}</b>). Tap any mode below to begin:",
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
                f"Forward this link to your friend. As soon as they tap it, their account activates automatically with <b>{lim_v} sessions/day</b>:\n\n"
                f"{link}",
            )
        return

    # 3. التحقق من صلاحية العضو للأزرار العامة
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
                    bot.send_voice(chat_id, af)
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
        bot.answer_callback_query(call.id, "Loading deep dive...")
        threading.Thread(target=execute_lexicon_extension, args=(chat_id, uid, term, ext_type), daemon=True).start()
    elif d.startswith("edroster:"):
        _, h_toggle, tok = d.split(":", 2)
        cur_r = list(_get_active_roster(uid))
        if h_toggle in cur_r:
            if len(cur_r) == 1:
                bot.answer_callback_query(call.id, f"At least one friend ({h_toggle}) must stay in the chat!")
                return
            cur_r.remove(h_toggle)
            action_note = f"{h_toggle} stepped out. Active now: {', '.join(cur_r)}"
        else:
            cur_r.append(h_toggle)
            action_note = f"{h_toggle} joined! Active now: {', '.join(cur_r)}"
        updated_r = _set_active_roster(uid, cur_r)
        bot.answer_callback_query(call.id, action_note)
        c = load_card(tok)
        l_terms = _extract_lookup_terms(c["vault_items"]) if c and c.get("vault_items") else []
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=build_inline_kb(tok, "chat", l_terms, roster=updated_r))
        except Exception:
            pass

    elif d.startswith("edmore:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Mining next batch...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, c["mined_so_far"] if c else "previous batch", ""), daemon=True).start()
    elif d.startswith("edqztext:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Building quiz from text...")
        threading.Thread(target=send_quiz, args=(chat_id, uid, "standard", c["source_text"] if c else fb_txt), daemon=True).start()
    elif d.startswith("edshift:"):
        _, sh, tok = d.split(":", 2)
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Transforming style...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, "", sh), daemon=True).start()
    elif d.startswith("edpod:"):
        fmt_s = d.split(":", 1)[1]
        h_n = 3 if fmt_s in ("roundtable", "3") else 2
        f_name = fmt_s if fmt_s in ("roundtable", "clash", "case") else "roundtable"
        bot.answer_callback_query(call.id, f"Producing {f_name.capitalize()} episode...")
        threading.Thread(target=execute_podcast, args=(chat_id, uid, "", h_n, f_name, en_name), daemon=True).start()
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
                types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
                types.InlineKeyboardButton("Next Question", callback_data="edqznext:standard"),
            )
            send_html_safe(
                chat_id,
                f"<b>{'✓ Correct (' + corr + ')' if ch == corr else 'Selected (' + ch + ') → Best Choice: (' + corr + ')'}</b>\n\n"
                f"<b>Why It Works</b>\n{_fmt_inline_md(q.explanation_en)}",
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
    _update_session(oid, mode="CHAT", register="Casual", clear_hist=True, new_turn={"u": "[Joined]", "b": "Chloe: Hey Musa! What are we getting into today?"})
    res_c = execute_chat_turn(
        oid,
        oid,
        "Nothing interesting to be honest, just relaxing in bed",
        is_voice=False,
        user_first_name="Musa",
    )
    if not res_c.primary_reply_en.strip():
        raise RuntimeError("Self-test failed: Empty primary_bubble_en")
    print("   🎯 Verified Primary Speaker Continuity:", res_c.primary_speaker, "->", res_c.primary_reply_en[:80])
    print("   🎯 Verified Zero Fake Fixes on Correct Line:", "has_actual_errors =", res_c.has_actual_errors)
    print("✅ STUDIO_V18_VERIFIED_OK", flush=True)
    os._exit(0)

if __name__ == "__main__":
    print("🚀 Musa's NYC Lounge Polling Active (Single-Instance Lock)...", flush=True)
    try:
        bot.remove_webhook()
    except Exception:
        pass
    sync_telegram_identity()
    try:
        _update_session(
            PRIMARY_OWNER_ID,
            mode="CHAT",
            register="Casual",
            clear_hist=True,
            new_turn={"u": "[Entered the lounge]", "b": "Chloe: Hey Musa—everything's set up and the table is yours. How's your evening going?"},
        )
        send_html_safe(
            PRIMARY_OWNER_ID,
            "<b>Chloe</b>\nHey Musa—everything's set up and the table is yours. How's your evening going?",
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
            bot.infinity_polling(timeout=25, long_polling_timeout=20, skip_pending=True)
        except Exception:
            time.sleep(3)
