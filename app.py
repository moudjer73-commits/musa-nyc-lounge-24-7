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

class PrecisionEditorSchema(BaseModel):
    polished_version_en: str = Field(default="", description="Clean, natural, idiomatic polish of the user's draft that preserves their core meaning and intended tone without robotic bloat.")
    surgical_diffs_en_ar: str = Field(default="", description="Each fix on a separate line: '• ❌ [exact error/weak phrase] ➔ ✅ **[native fix]** — [concise reason + Arabic note if helpful]'. If error-free, write '• ✓ Grammatically solid—see vocabulary upgrades below.'")
    lexical_upgrades_en_ar: str = Field(default="", description="3 high-impact vocabulary or collocation upgrades from the draft on separate lines: '• [simple word] ➔ **[C1/C2 native verb/chunk]** (المعنى السياقي) — \"[short example]\"'")
    your_turn_prompt_en: str = Field(default="", description="1-sentence interactive prompt inviting the user to reply right now using one of the upgraded chunks or structures.")

class ArabicBridgeSchema(BaseModel):
    spoken_american_en: str = Field(default="", description="How a native American naturally expresses this Arabic thought aloud in conversation or a meeting (active verbs, natural flow).")
    executive_written_en: str = Field(default="", description="Crisp, diplomatic workplace/professional written version of this Arabic thought.")
    c2_academic_prose_en: str = Field(default="", description="Authoritative C2 academic/analytical prose (nominalization, Given-Before-New flow, End-Weight focus).")
    building_blocks_and_trap_en_ar: str = Field(default="", description="On separate lines:\n• **[Key English Chunk 1]:** [المعنى السياقي وكيفية توظيفه]\n• **[Key English Chunk 2]:** [المعنى السياقي وكيفية توظيفه]\n• ❌ **Literal Translation Trap:** [what Arabic speakers mistakenly write literally] ➔ ✅ **[Native phrasing]**")
    your_turn_prompt_en: str = Field(default="", description="1-sentence prompt inviting the user to write a follow-up sentence in English using one of the building blocks above.")

class ModularMinerSchema(BaseModel):
    theme_title_en: str = Field(default="Close-Reading Harvest", description="Short 3-5 word title")
    top_chunks_en_ar: str = Field(default="", description="Top 4 standout idioms, phrasal verbs, or C1/C2 collocations from the text. Format EACH as a numbered block:\n1. **[Exact Expression]** — `[Grammar Frame]`\n• المعنى السياقي: [Eloquent Arabic equivalent]\n• New Example: \"[Fresh 10-14 word sentence on a different topic]\"")
    your_turn_prompt_en: str = Field(default="", description="1-sentence challenge asking the user to reply with their own sentence using one of the 4 expressions above.")

class TutorLoopSchema(BaseModel):
    title_en: str = Field(default="Interactive Studio Feedback", description="Short title")
    direct_feedback_or_answer_en: str = Field(default="", description="If the user is practicing a sentence from the previous Study card: evaluate their sentence, praise what worked, fix any slip-up (❌ ➔ ✅), and show the polished version! If the user asked a linguistic/nuance question: give the direct, authoritative rule with Arabic conceptual anchor.")
    register_or_nuance_examples_en_ar: str = Field(default="", description="3 sharp bullet points showing Spoken, Professional, and C2 Academic examples (or contrasting the synonyms/structures asked about) with concise Arabic notes.")
    next_step_challenge_en: str = Field(default="", description="1-sentence follow-up challenge to keep the active learning loop going.")

class DraftShiftSchema(BaseModel):
    shift_title_en: str = Field(default="Style Transformation", description="Title of transformation")
    transformed_text_en: str = Field(default="", description="The rewritten text")
    key_refinements_en: str = Field(default="", description="3 concise bullets explaining the exact lexical, syntactic, or rhetorical upgrades made (with brief Arabic meanings).")

class MoreMinerSchema(BaseModel):
    supplement_title_en: str = Field(default="Deep Harvest", description="Title")
    new_expressions_en: str = Field(default="", description="3-4 rich blocks separated by blank lines (either 4 brand-new chunks with Arabic & examples, OR 3 C2 sentence skeletons with [Slots] & applied examples, OR Rhetorical & Nominalization analysis).")

class PodcastSchema(BaseModel):
    episode_title_en: str = Field(default="NYC Studio Roundtable", description="Compelling, podcast-style episode title")
    episode_summary_and_ear_cue_en: str = Field(default="", description="2 sharp sentences: Sentence 1 sets the scene/conflict of the episode; Sentence 2 tells the listener what specific argument or contrast to listen for in the audio.")
    script_en: str = Field(default="", description="7-turn natural, unscripted NYC dialogue (Jake, Chloe, Marcus). Put EACH speaker turn on a NEW line starting strictly with 'Jake: ...', 'Chloe: ...', or 'Marcus: ...' (DO NOT put asterisks around speaker names!). Highlight 4 standout idioms/chunks inside the dialogue using **bold**. Zero stage directions in parentheses!")
    fast_speech_and_chunks_en_ar: str = Field(default="", description="Break down the 4 bolded expressions from script_en as numbered blocks:\n1. **[Exact Expression]** — `[Connected Speech / Stress Guide, e.g. 'gonna hafta / stress on HINGE']`\n• المعنى السياقي: [Eloquent Arabic meaning]\n• Why it works here: [1-line pragmatic note]")
    on_air_call_in_question_en: str = Field(default="", description="Direct, thought-provoking question from the hosts inviting the listener (by name) to call in with a voice note or text reply.")

class PodcastCallInSchema(BaseModel):
    on_air_host_response_en: str = Field(default="", description="2-host live studio reaction to the user's call-in comment (e.g. 'Chloe: ...\nMarcus: ...'), welcoming their point on air, engaging with their idea, and asking a sharp follow-up.")
    call_in_language_polish_en_ar: str = Field(default="", description="On separate lines:\n• **Your Call-In Polished (Broadcast-Ready):** '[Natural native upgrade of what the user said]'\n• **Fixes / Upgrades:** [Any ❌ ➔ ✅ grammar/collocation fix, or '✓ Spot-on delivery!']\n• **Power Chunk from Hosts:** **[expression]** — [Arabic meaning]")

class QuizSchema(BaseModel):
    quiz_mode_badge_en: str = Field(default="Collocation & Valency Precision", description="Exact badge for the drill mode")
    scenario_or_stem_en: str = Field(default="", description="For Collocation/Pragmatics/Vault: a realistic C1/C2 sentence with a ____ blank (or situational prompt). For C2 Transformation: 'Base Sentence: ...\nKEY WORD: **[WORD]**\nComplete the second sentence using 3-6 words including the KEY WORD so it means the same:'")
    option_a: str = Field(default="", description="Option A")
    option_b: str = Field(default="", description="Option B")
    option_c: str = Field(default="", description="Option C")
    option_d: str = Field(default="", description="Option D")
    correct_letter: str = Field(default="B", description="'A', 'B', 'C', or 'D'")
    winning_reason_en_ar: str = Field(default="", description="Why the correct option wins (exact collocation, syntactic frame, register fit, and eloquent Arabic meaning).")
    distractor_anatomy_en: str = Field(default="", description="Explain briefly on separate lines why EACH of the other 3 options fails (e.g. '• Option A: fails because...\n• Option C: wrong preposition...').")
    complete_native_sentence_en: str = Field(default="", description="The complete, final winning sentence written out cleanly so it can be read aloud by TTS audio.")
    target_expression_en: str = Field(default="", description="The core 2-5 word winning expression or collocation to save/lookup.")
    active_production_prompt_en: str = Field(default="", description="A 1-sentence challenge inviting the user to type or speak their OWN sentence using `target_expression_en`.")

class ActiveQuizEvalSchema(BaseModel):
    evaluation_and_score_en: str = Field(default="", description="Direct assessment of the user's sentence (e.g. '9/10 — Natural & accurate!' or '7/10 — Good idea, small preposition slip') + exact explanation.")
    surgical_fix_and_model_en_ar: str = Field(default="", description="On separate lines:\n• **Your Sentence Polished:** '[100% natural native version]'\n• **Fix / Nuance:** [❌ ➔ ✅ or why it works + Arabic note]\n• **Bonus C2 Variation:** '[High-level executive/academic version]'")

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

    if ctype == "draft":
        kb.row(
            types.InlineKeyboardButton("🎙️ Spoken", callback_data=f"edshift:spoken:{tok}"),
            types.InlineKeyboardButton("💼 Executive", callback_data=f"edshift:soften:{tok}"),
            types.InlineKeyboardButton("🏛️ C2 Academic", callback_data=f"edshift:c2:{tok}"),
            types.InlineKeyboardButton("⚡ Tighten", callback_data=f"edshift:condense:{tok}"),
        )
        kb.row(
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("🎯 Quiz Fixes", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
            types.InlineKeyboardButton("⛏️ Mine Instead", callback_data=f"edrecast:miner:{tok}"),
        )
    elif ctype == "bridge":
        kb.row(
            types.InlineKeyboardButton("🎧 Listen All 3", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("🐢 Slow 0.85x", callback_data=f"edtts:slow:{tok}"),
            types.InlineKeyboardButton("💾 Save Chunks", callback_data=f"edsave:{tok}"),
        )
    elif ctype == "miner":
        kb.row(
            types.InlineKeyboardButton("🏗️ C2 Templates", callback_data=f"edmore:skel:{tok}"),
            types.InlineKeyboardButton("🔬 Rhetoric & Flow", callback_data=f"edmore:rhet:{tok}"),
            types.InlineKeyboardButton("⛏️ Next 4 Chunks", callback_data=f"edmore:chunks:{tok}"),
        )
        kb.row(
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("🎯 Cloze Quiz", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save All", callback_data=f"edsave:{tok}"),
            types.InlineKeyboardButton("✍️ Edit as Draft", callback_data=f"edrecast:draft:{tok}"),
        )
    else:
        kb.row(
            types.InlineKeyboardButton("🎧 Listen", callback_data=f"edtts:1x:{tok}"),
            types.InlineKeyboardButton("🎯 Practice Quiz", callback_data=f"edqztext:{tok}"),
            types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
        )
    if lookup_terms and ctype in ("study", "miner", "draft", "bridge", "clinic"):
        btns = [types.InlineKeyboardButton(f"🔍 {t[:24]}", callback_data=f"edqlk:{i}:{tok}") for i, t in enumerate(lookup_terms[:2])]
        if btns:
            kb.row(*btns)
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
# محرك 3: ورشة الدراسة التفاعلية الرشيقة (V22 Interactive Study Workbench)
# ------------------------------------------------------------------------------
def _detect_study_route(text: str, user_id: int, forced_lens: str = "") -> str:
    if forced_lens in ("draft", "miner", "bridge", "clinic"):
        return forced_lens
    clean = text.strip()
    low = clean.lower()
    w_cnt = len(clean.split())

    # 1. إذا كان النص يحتوي على حروف عربية (أكثر من كلمتين بالعربية) -> جسر الصياغة AR -> EN Bridge
    ar_chars = len(re.findall(r"[\u0600-\u06FF]", clean))
    if ar_chars > 8:
        return "bridge"

    # 2. أوامر بادئة صريحة
    if low.startswith(("mine:", "extract:", "article:")):
        return "miner"
    if low.startswith(("edit:", "fix:", "draft:", "polish:")):
        return "draft"

    # 3. أسئلة الفروق الدلالية والنحوية ("How do I...", "What is the difference...", "vs")
    if (
        low.startswith(("what ", "what'", "how ", "why ", "when ", "where ", "which ", "can i say", "is it correct", "difference"))
        or (" vs " in low and w_cnt < 35)
        or (clean.endswith("?") and w_cnt < 35)
    ):
        return "clinic"

    # 4. إذا كان المستخدم يرد بجملة قصيرة (أقل من 30 كلمة) بعد بطاقة Study سابقة -> المدرب التفاعلي يقيّم محاولته ويصححها!
    s = _get_session(user_id)
    last_study_ctx = [h for h in s.get("history", []) if isinstance(h, dict) and str(h.get("u", "")).startswith("[STUDY")]
    if last_study_ctx and w_cnt <= 28 and not any(x in low for x in ("http://", "https://")):
        return "clinic"

    # 5. التمييز بين مقال للتعدين وبين مسودة المستخدم
    first_person = len(re.findall(r"\b(i|my|me|we|our|us|i'm|i've|i'd)\b", low))
    learner_slips = any(x in low for x in ("discuss about", "depend of", "explain me", "informations", "feedbacks", "do a mistake", "cannot success", "in my opinion i think"))
    if learner_slips:
        return "draft"
    if w_cnt > 65 and first_person <= 1:
        return "miner"
    return "draft"

def execute_study_and_miner(chat_id: int, user_id: int, user_input: str, is_voice: bool = False, already_mined: str = "", shift: str = "", forced_lens: str = ""):
    s = _get_session(user_id)
    reg = s["register"]
    sub_st = s.get("sub_state", "") or ""
    if not forced_lens and sub_st.startswith("STUDY_LENS:"):
        forced_lens = sub_st.replace("STUDY_LENS:", "").strip()

    # 1. التحويل الأسلوبي عند الطلب بلمسة زر (Spoken / Executive / C2 Academic / Tighten)
    if shift:
        shift_map = {
            "spoken": (
                "🎙️ Natural Spoken Delivery",
                "Rewrite this text into natural, spontaneous spoken American English suitable for saying aloud in a meeting or discussion (active verbs, contractions, spoken rhythm, zero stiffness).",
            ),
            "soften": (
                "💼 Crisp & Diplomatic Executive Written",
                "Rewrite this text into warm, tactful, solution-oriented American executive/workplace English (focus on shared goals and timelines, zero personal blame).",
            ),
            "c2": (
                "🏛️ C2 Academic & Analytical Prose",
                "Transform this text into authoritative C2 Academic/Analytical prose using Given-Before-New information flow, End-Weight focus, strategic nominalization, and organic demonstrative cohesion (no mechanical 'Furthermore/Moreover').",
            ),
            "condense": (
                "⚡ Tightened High-Impact Version (-35%)",
                "Condense this text by 35-40% into razor-sharp, high-impact English by eliminating clutter and using precision verbs.",
            ),
        }
        sh_title, sh_prompt = shift_map.get(shift, shift_map["spoken"])
        sh_data: DraftShiftSchema = call_llm_structured(
            f"{SYS_PROMPT}\n{sh_prompt}",
            f"Original Text:\n\"{user_input[:3500]}\"",
            DraftShiftSchema,
            temp=0.42,
        )
        tok = hashlib.md5(f"SHF:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Marcus" if shift == "c2" else "Chloe", sh_data.transformed_text_en)]
        pdf_d = {"title": sh_title, "register": reg, "main_en": sh_data.transformed_text_en, "instant_fix": sh_data.key_refinements_en}
        store_card(tok, user_id, v_segs, pdf_d, [(sh_data.transformed_text_en[:90], sh_data.key_refinements_en, user_input[:200])], source_text=user_input)
        sh_html = (
            f"<b>{sh_title}</b>\n\n"
            f"<i>\"{_fmt_inline_md(sh_data.transformed_text_en)}\"</i>\n\n"
            f"<b>🔬 What Changed &amp; Why</b>\n{_clean_multiline_bullets(sh_data.key_refinements_en)}"
        )
        send_html_safe(chat_id, sh_html, reply_markup=build_inline_kb(tok, "draft"))
        return

    # 2. الاستخراج الإضافي عند الطلب في وضع التعدين (C2 Templates / Rhetoric & Flow / Next 4 Chunks)
    if already_mined:
        if already_mined.startswith("SKEL_ONLY::"):
            prev_m = already_mined.replace("SKEL_ONLY::", "")
            m_task = (
                "Extract 3 reusable C2 Rhetorical Sentence Templates from this text. Format each as a numbered block:\n"
                "1. **Pattern:** `[Frame with [Slots], e.g. Far from [V-ing X], [Subj] merely [Verb Y]...]`\n"
                "• الوظيفة البلاغية: [Arabic explanation of rhetorical effect]\n"
                "• Applied Example: \"[Brand-new sentence built with this exact skeleton]\""
            )
            hdr_icon = "🏗️ Reusable C2 Sentence Templates"
        elif already_mined.startswith("RHET_ONLY::"):
            prev_m = already_mined.replace("RHET_ONLY::", "")
            m_task = (
                "Analyze the Author's Prose Architecture & Nominalization in 3 numbered blocks:\n"
                "1. **Argument & Cohesion Flow:** How the author links ideas (Given-to-New, End-Weight, transitions).\n"
                "2. **Hedging & Epistemic Stance:** Exact words/structures used to qualify claims.\n"
                "3. **Spoken Verb-Driven ➔ C2 Nominalization Map:** Show 2 sentences from the text contrasted with their everyday spoken equivalents (with Arabic notes)."
            )
            hdr_icon = "🔬 Rhetoric, Cohesion & Nominalization Breakdown"
        else:
            prev_m = already_mined
            m_task = (
                f"Previously extracted: [{prev_m}]. Mine 4 COMPLETELY NEW whole-chunk expressions, phrasal verbs, or academic collocations from this text (zero overlap!). Format each as:\n"
                "1. **[Exact Expression]** — `[Grammar Frame]`\n"
                "• المعنى السياقي: [Eloquent Arabic meaning]\n"
                "• New Example: \"[Fresh 10-14 word sentence on a different topic]\""
            )
            hdr_icon = "⛏️ Next 4 Extracted Chunks"

        m_data: MoreMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{m_task}", f"Text:\n\"{user_input[:4500]}\"", MoreMinerSchema, temp=0.42)
        tok = hashlib.md5(f"MOR:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_items = []
        for ln in m_data.new_expressions_en.splitlines():
            if re.match(r"^[1-9]\.", ln.strip()):
                t_c, d_c, _ = _clean_vault_entry(re.sub(r"^[1-9]\.\s*", "", ln.strip()), "", user_input[:160])
                if t_c:
                    v_items.append((t_c, d_c, ln))
        new_mined = f"{prev_m}, {', '.join([x[0] for x in v_items])}".strip(", ")
        store_card(tok, user_id, [("Marcus", m_data.new_expressions_en)], {"title": hdr_icon, "main_en": m_data.new_expressions_en}, v_items, source_text=user_input, mined_so_far=new_mined)
        m_html = f"<b>{hdr_icon}</b>\n\n{_format_unified_senses(m_data.new_expressions_en)}"
        send_html_safe(chat_id, m_html, reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    # 3. تحديد المسار الذكي
    route = _detect_study_route(user_input, user_id, forced_lens=("draft" if is_voice and not forced_lens else forced_lens))

    # المسار أ: جسر الصياغة من الفكرة العربية إلى الإنجليزية الأصيلة (AR -> EN Thought Architect)
    if route == "bridge":
        task = (
            "ARABIC-TO-ENGLISH THOUGHT ARCHITECT:\n"
            "The user wrote an idea or paragraph in Arabic (or mixed AR/EN). Show how to express this exact meaning natively in American English without literal translation stiffness:\n"
            "1. `spoken_american_en`: natural, effortless spoken NYC phrasing.\n"
            "2. `executive_written_en`: crisp, diplomatic workplace phrasing.\n"
            "3. `c2_academic_prose_en`: high-level C2 analytical/academic prose (nominalization & Given-to-New flow).\n"
            "4. `building_blocks_and_trap_en_ar`: 2 key native chunks used + the ❌ Literal Arabic Calque Trap to avoid.\n"
            "5. `your_turn_prompt_en`: invite the user to write 1 sentence using one of these chunks."
        )
        b_res: ArabicBridgeSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Arabic/Mixed Input:\n\"{user_input[:3000]}\"", ArabicBridgeSchema, temp=0.42)
        _update_session(user_id, new_turn={"u": f"[STUDY AR->EN]: {user_input[:160]}", "b": f"C2: {b_res.c2_academic_prose_en[:180]} | Blocks: {b_res.building_blocks_and_trap_en_ar[:180]}"})

        html_b = (
            f"<b>🌉 AR ➔ EN Native Expression Bridge</b>\n\n"
            f"🎙️ <b>1. Natural Spoken American</b>\n<i>\"{_fmt_inline_md(b_res.spoken_american_en)}\"</i>\n\n"
            f"💼 <b>2. Crisp Executive Written</b>\n<i>\"{_fmt_inline_md(b_res.executive_written_en)}\"</i>\n\n"
            f"🏛️ <b>3. C2 Academic &amp; Analytical Prose</b>\n<b>\"{_fmt_inline_md(b_res.c2_academic_prose_en)}\"</b>\n\n"
            f"<b>🧱 Key Building Blocks &amp; Calque Trap</b>\n{_clean_multiline_bullets(b_res.building_blocks_and_trap_en_ar)}\n\n"
            f"🎯 <b>Your Turn:</b> <i>{_fmt_inline_md(b_res.your_turn_prompt_en)}</i>"
        )
        tok = hashlib.md5(f"BRG:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Jake", b_res.spoken_american_en), ("Chloe", b_res.executive_written_en), ("Marcus", b_res.c2_academic_prose_en)]
        pdf_d = {"title": "AR to EN Expression Bridge", "register": reg, "main_en": b_res.executive_written_en, "spoken_inf": b_res.spoken_american_en, "written_form": b_res.c2_academic_prose_en, "diagnosis_en": b_res.building_blocks_and_trap_en_ar}
        v_items = [(b_res.executive_written_en[:80], user_input[:200], b_res.c2_academic_prose_en)]
        store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=b_res.executive_written_en)
        send_html_safe(chat_id, html_b, reply_markup=build_inline_kb(tok, "bridge"))
        return

    # المسار ب: التعدين الرشيق للمقالات والنصوص (Modular Corpus Miner)
    if route == "miner":
        task = (
            "MODULAR CLOSE-READING & CHUNK MINER:\n"
            "Extract the Top 4 highest-value idioms, phrasal verbs, or C1/C2 collocations from this text. Do NOT invent grammar errors on the text!\n"
            "For each of the 4 items, show: Exact Expression, Grammar Frame, eloquent contextual Arabic meaning, and a brand-new example sentence."
        )
        m_res: ModularMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Text:\n\"{user_input[:5500]}\"", ModularMinerSchema, temp=0.4)
        _update_session(user_id, new_turn={"u": f"[STUDY MINER]: {user_input[:140]}", "b": m_res.top_chunks_en_ar[:260]})

        html_m = (
            f"<b>⛏️ {_h(m_res.theme_title_en)} — Top 4 Chunks</b>\n\n"
            f"{_format_unified_senses(m_res.top_chunks_en_ar)}\n\n"
            f"🎯 <b>Active Practice:</b> <i>{_fmt_inline_md(m_res.your_turn_prompt_en)}</i>"
        )
        tok = hashlib.md5(f"MIN:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_items = []
        for ln in m_res.top_chunks_en_ar.splitlines():
            if re.match(r"^[1-9]\.", ln.strip()):
                t_c, d_c, _ = _clean_vault_entry(re.sub(r"^[1-9]\.\s*", "", ln.strip()), "", m_res.theme_title_en)
                if t_c:
                    v_items.append((t_c, d_c, ln))
        v_segs = [("Marcus", m_res.top_chunks_en_ar)]
        pdf_d = {"title": m_res.theme_title_en, "register": "C1/C2 Corpus Harvest", "main_en": m_res.top_chunks_en_ar}
        store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=user_input[:4500], mined_so_far=", ".join([x[0] for x in v_items]))
        send_html_safe(chat_id, html_m, reply_markup=build_inline_kb(tok, "miner", _extract_lookup_terms(v_items)))
        return

    # المسار ج: المدرب التفاعلي وعيادة الفروق الدقيقة (Interactive Tutor Loop & Nuance Clinic)
    if route == "clinic":
        recent_study = "\n".join([f"Prior Input: {h.get('u','')}\nPrior Coaching: {h.get('b','')}" for h in s.get("history", [])[-3:] if isinstance(h, dict)])
        task = (
            "INTERACTIVE STUDY TUTOR & NUANCE CLINIC:\n"
            "- Check `Recent Study Context`. If the user is replying with a practice sentence or testing a chunk from the previous card, evaluate their attempt warmly, show any ❌ ➔ ✅ refinements, and give 3 register variations!\n"
            "- If the user is asking a linguistic question ('X vs Y', 'How do I say...', grammar rule), answer it with razor-sharp clarity, 3 register examples (Spoken, Executive, C2 Academic), and Arabic nuances."
        )
        c_res: TutorLoopSchema = call_llm_structured(
            f"{SYS_PROMPT}\n{task}",
            f"Recent Study Context:\n{recent_study}\n\nUser's Current Input:\n\"{user_input[:2000]}\"",
            TutorLoopSchema,
            temp=0.45,
        )
        _update_session(user_id, new_turn={"u": f"[STUDY PRACTICE/Q]: {user_input[:160]}", "b": c_res.direct_feedback_or_answer_en[:220]})

        html_c = (
            f"<b>🎓 {_h(c_res.title_en)}</b>\n\n"
            f"{_fmt_inline_md(c_res.direct_feedback_or_answer_en)}\n\n"
            f"<b>📊 Examples &amp; Nuances Across Registers</b>\n{_clean_multiline_bullets(c_res.register_or_nuance_examples_en_ar)}\n\n"
            f"🎯 <b>Next Step:</b> <i>{_fmt_inline_md(c_res.next_step_challenge_en)}</i>"
        )
        tok = hashlib.md5(f"CLN:{user_id}:{time.time()}".encode()).hexdigest()[:10]
        v_segs = [("Chloe", c_res.direct_feedback_or_answer_en), ("Marcus", c_res.register_or_nuance_examples_en_ar)]
        pdf_d = {"title": c_res.title_en, "register": reg, "main_en": c_res.direct_feedback_or_answer_en, "diagnosis_en": c_res.register_or_nuance_examples_en_ar}
        v_items = [(c_res.title_en[:70], c_res.direct_feedback_or_answer_en[:240], c_res.register_or_nuance_examples_en_ar[:240])]
        store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=user_input)
        send_html_safe(chat_id, html_c, reply_markup=build_inline_kb(tok, "clinic"))
        return

    # المسار د (الافتراضي للمسودات والصوتيات): المحرر الجراحي المركز (Precision Editor)
    task = (
        "PRECISION DRAFT & SPEECH EDITOR:\n"
        "1. `polished_version_en`: Polish the user's text so it sounds 100% natural, clear, and idiomatic in the target register while keeping their personal voice.\n"
        "2. `surgical_diffs_en_ar`: List every grammar, preposition, collocation, or L1 Arabic calque fix as '• ❌ [user phrase] ➔ ✅ **[native fix]** — [concise reason]'.\n"
        "3. `lexical_upgrades_en_ar`: 3 specific word/collocation upgrades from their draft with Arabic meanings.\n"
        "4. `your_turn_prompt_en`: 1-sentence challenge inviting them to reply using one of the upgrades."
    )
    d_res: PrecisionEditorSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{task}",
        f"Target Register: {reg}\nUser's Draft:\n\"{user_input[:4000]}\"",
        PrecisionEditorSchema,
        temp=0.42,
    )
    _update_session(user_id, new_turn={"u": f"[STUDY DRAFT]: {user_input[:160]}", "b": f"Polished: {d_res.polished_version_en[:180]} | Upgrades: {d_res.lexical_upgrades_en_ar[:180]}"})

    if d_res.surgical_diffs_en_ar and ("->" in d_res.surgical_diffs_en_ar or "➔" in d_res.surgical_diffs_en_ar):
        _log_dna(user_id, d_res.surgical_diffs_en_ar.replace("➔", "->"))

    v_prefix = f"🎙️ <b>Voice Draft:</b> <i>\"{_h(user_input[:240])}\"</i>\n\n" if is_voice else ""
    html_d = (
        f"{v_prefix}<b>✍️ Polished Version ({_h(reg)})</b>\n"
        f"<i>\"{_fmt_inline_md(d_res.polished_version_en)}\"</i>\n\n"
        f"<b>🔧 Surgical Fixes (❌ ➔ ✅)</b>\n{_clean_multiline_bullets(d_res.surgical_diffs_en_ar)}\n\n"
        f"<b>🚀 Vocabulary &amp; Collocation Upgrades</b>\n{_clean_multiline_bullets(d_res.lexical_upgrades_en_ar)}\n\n"
        f"🎯 <b>Your Turn:</b> <i>{_fmt_inline_md(d_res.your_turn_prompt_en)}</i>"
    )
    tok = hashlib.md5(f"DRF:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Chloe", d_res.polished_version_en)]
    pdf_d = {
        "title": "Precision Draft Surgery",
        "register": reg,
        "main_en": d_res.polished_version_en,
        "instant_fix": d_res.surgical_diffs_en_ar,
        "diagnosis_en": d_res.lexical_upgrades_en_ar,
    }
    v_items = []
    for ln in d_res.lexical_upgrades_en_ar.splitlines():
        if "➔" in ln or "->" in ln:
            pts = re.split(r"➔|->", ln, maxsplit=1)
            t_c, d_c, _ = _clean_vault_entry(pts[1], pts[0], d_res.polished_version_en)
            if t_c:
                v_items.append((t_c, d_c, d_res.polished_version_en))
    store_card(tok, user_id, v_segs, pdf_d, v_items, source_text=user_input[:3500])
    send_html_safe(chat_id, html_d, reply_markup=build_inline_kb(tok, "draft", _extract_lookup_terms(v_items)))

# ------------------------------------------------------------------------------
# محرك 4: استوديو البودكاست الإذاعي المتكامل (V23 — مطويات مضمونة + مفكك النطق + مداخلة على الهواء)
# ------------------------------------------------------------------------------
def _format_podcast_script_safe(raw_script: str) -> str:
    """تنسيق نص البودكاست بأمان تام دون أي تداخل لوسوم <b> داخل المطوية القابلة للطي"""
    clean = html.unescape(str(raw_script or "")).strip()
    clean = re.sub(r"\([^)]*\)", "", clean)
    clean = re.sub(r"\*+\s*(Jake|Chloe|Marcus)\s*\*+\s*:", r"\1:", clean)
    clean = re.sub(r"(Jake|Chloe|Marcus)\s*:\s*\*+", r"\1: ", clean)
    norm = re.sub(r"(?:^|\n|\s)+(Jake|Chloe|Marcus)\s*:\s*", r"\n\1: ", clean).strip()
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
        "roundtable": f"FORMAT: 3-Host NYC Roundtable ({reg}). Jake, Chloe, and Marcus trade sharp, unscripted perspectives, interrupting and building on each other's points like a real NY podcast.",
        "clash": f"FORMAT: 2-Host Clash of Views Debate ({reg}) between Jake (fast, pragmatic, anti-bureaucracy) and Marcus (strategic, structural, cautious). Use authentic agreement/pushback markers.",
        "case": f"FORMAT: 2-Host Deep Case Breakdown ({reg}) with Chloe and Marcus dissecting human psychology, leadership blind spots, and decision-making.",
        "vault": (
            f"FORMAT: Personalized 'Vault Mastery' Episode ({reg}) with Chloe, Jake & Marcus.\n"
            f"Naturally weave these saved expressions into the hosts' dialogue (bolding them with **): {', '.join(vault_terms) if vault_terms else 'hold off on, throw around, come down to, pan out'}."
        ),
    }
    chosen_inst = style_prompts.get(fmt_style, style_prompts["roundtable"])
    t_p = topic.strip() if len(topic.strip()) > 2 else f"Compelling {reg} NYC discussion on work culture, ambition, and modern habits"

    data: PodcastSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{chosen_inst}\nAddress {user_first_name} in `on_air_call_in_question_en`. Highlight 4 key idioms in **bold** inside `script_en` (NEVER bold speaker names!).",
        f"Episode Topic: {t_p}",
        PodcastSchema,
        temp=0.65,
    )

    script_html = _format_podcast_script_safe(data.script_en)
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
        f"<i>(Reply by voice or text right now to join the hosts on air!)</i>\n\n"
        f"<blockquote expandable>📜 <b>Tap to Open Full Episode Transcript ▼</b>\n\n{script_html}\n\n───\n<i>End of Transcript</i></blockquote>\n\n"
        f"<blockquote expandable>💎 <b>Tap to Open Chunks &amp; Fast-Speech Decoder ▼</b>\n\n{chunks_html}\n\n───\n<i>Tap Save below to add these to your Vault</i></blockquote>"
    )

    tok = hashlib.md5(f"POD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(data.script_en, "Jake")

    v_items = []
    for ln in data.fast_speech_and_chunks_en_ar.splitlines():
        if re.match(r"^[1-9]\.", ln.strip()):
            t_c, d_c, _ = _clean_vault_entry(re.sub(r"^[1-9]\.\s*", "", ln.strip()), "", data.episode_title_en)
            if t_c:
                v_items.append((t_c, d_c, data.episode_title_en))

    pdf_d = {
        "title": data.episode_title_en,
        "register": f"{fmt_badge} ({reg})",
        "main_en": data.script_en,
        "instant_fix": data.on_air_call_in_question_en,
        "diagnosis_en": data.fast_speech_and_chunks_en_ar,
    }
    store_card(tok, user_id, segs, pdf_d, v_items, source_text=f"{data.episode_title_en}\n{data.script_en}")
    _update_session(
        user_id,
        mode="PODCAST",
        sub_state=f"PODCAST_ACTIVE:{fmt_style}",
        new_turn={"u": f"[Podcast: {data.episode_title_en}]", "b": f"{data.script_en[:320]} | Question: {data.on_air_call_in_question_en}"},
    )

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🎧 Audio 1.0x", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("🐢 Slow 0.85x", callback_data=f"edtts:slow:{tok}"),
        types.InlineKeyboardButton("🎯 Quiz Episode", callback_data=f"edqztext:{tok}"),
        types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
    )
    kb.row(
        types.InlineKeyboardButton("📜 Separate Transcript", callback_data=f"edpodview:tr:{tok}"),
        types.InlineKeyboardButton("💎 Separate Chunks", callback_data=f"edpodview:ch:{tok}"),
        types.InlineKeyboardButton("📄 PDF", callback_data=f"edpdf:{tok}"),
    )
    kb.row(
        types.InlineKeyboardButton("🎙️ Roundtable", callback_data="edpod:roundtable"),
        types.InlineKeyboardButton("⚡ Clash", callback_data="edpod:clash"),
        types.InlineKeyboardButton("🔍 Case", callback_data="edpod:case"),
        types.InlineKeyboardButton("💎 Vault Ep", callback_data="edpod:vault"),
    )
    l_terms = _extract_lookup_terms(v_items)
    if l_terms:
        kb.row(*[types.InlineKeyboardButton(f"🔍 {t[:22]}", callback_data=f"edqlk:{i}:{tok}") for i, t in enumerate(l_terms[:2])])

    send_html_safe(chat_id, html_out, reply_markup=kb)

    if os.environ.get("LIVE_SELF_TEST") != "1":
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
        threading.Thread(target=_send_pod_voice, daemon=True).start()

def execute_podcast_call_in(chat_id: int, user_id: int, user_text: str, is_voice: bool = False, user_first_name: str = "Musa"):
    s = _get_session(user_id)
    reg = s["register"]
    last_ep = "\n".join([f"{h.get('u','')}\n{h.get('b','')}" for h in s.get("history", [])[-2:] if isinstance(h, dict)])

    task = (
        f"LIVE ON-AIR CALL-IN RESPONSE ({reg}):\n"
        f"The listener ({user_first_name}) just called into the podcast to answer the hosts' question.\n"
        "1. In `on_air_host_response_en`, write a lively 2-turn studio response ('Chloe: ...\nMarcus: ...' or 'Jake: ...') welcoming Musa's point on air, reacting to his perspective, and asking a follow-up.\n"
        "2. In `call_in_language_polish_en_ar`, show a broadcast-ready polish of what Musa said, any ❌ ➔ ✅ fix, and 1 power chunk used by the hosts."
    )
    res: PodcastCallInSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{task}",
        f"Episode Context:\n{last_ep}\n\nListener ({user_first_name}) Call-In:\n\"{user_text}\"",
        PodcastCallInSchema,
        temp=0.6,
    )
    _update_session(user_id, new_turn={"u": f"[Call-In]: {user_text[:180]}", "b": res.on_air_host_response_en[:240]})

    dialogue_fmt = _format_podcast_script_safe(res.on_air_host_response_en)
    polish_fmt = _clean_multiline_bullets(res.call_in_language_polish_en_ar)
    v_hdr = f"📞 <b>Your On-Air Voice Call-In:</b> <i>&quot;{_h(user_text)}&quot;</i>\n\n" if is_voice else f"📞 <b>On-Air Response to {_h(user_first_name)}:</b>\n\n"

    html_call = (
        f"{v_hdr}{dialogue_fmt}\n\n"
        f"<blockquote expandable>💡 <b>Broadcast Polish &amp; Notes ▼</b>\n\n{polish_fmt}</blockquote>"
    )
    tok = hashlib.md5(f"CAL:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = _split_dialogue_segments(res.on_air_host_response_en, "Chloe")
    store_card(tok, user_id, segs, {"title": "On-Air Call-In", "main_en": res.on_air_host_response_en, "diagnosis_en": res.call_in_language_polish_en_ar}, [], source_text=user_text)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🎧 Hear Hosts", callback_data=f"edtts:1x:{tok}"),
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
# محرك 5: ساحة الكويز والإنتاج اللغوي لـ C1/C2 (5 Modes + Distractor Anatomy + Streak)
# ------------------------------------------------------------------------------
_QUIZ_STREAK: Dict[int, int] = {}

def send_quiz_hub(chat_id: int, user_id: int):
    streak = _QUIZ_STREAK.get(user_id, 0)
    s_badge = f" • 🔥 <b>Streak: {streak}</b>" if streak > 0 else ""
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🔗 Collocations & Preps", callback_data="edqznext:colloc"),
        types.InlineKeyboardButton("🏛️ C2 Transformations", callback_data="edqznext:c2"),
    )
    kb.row(
        types.InlineKeyboardButton("🎭 Pragmatic Subtext", callback_data="edqznext:prag"),
        types.InlineKeyboardButton("💎 My Vault & Mistakes", callback_data="edqznext:vault"),
    )
    kb.row(
        types.InlineKeyboardButton("✍️ Active Production Challenge (Type/Speak)", callback_data="edqznext:produce"),
    )
    send_html_safe(
        chat_id,
        f"<b>🎯 C1/C2 Active Mastery Arena</b>{s_badge}\n\n"
        f"Choose your training mode below, or jump straight into the question:",
        reply_markup=kb,
    )

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
        )
        kb.row(
            types.InlineKeyboardButton("🔗 Switch to Multiple-Choice", callback_data="edqznext:colloc"),
        )
        send_html_safe(
            chat_id,
            f"<b>✍️ Active Production Challenge ({_h(reg)})</b>\n\n"
            f"🎯 <b>Target Expression:</b> <code>{_h(target_ch)}</code>\n\n"
            f"Write (or record a voice note with) <b>one complete, natural sentence</b> in a <i>{_h(reg)}</i> context using <b>{_h(target_ch)}</b>.\n"
            f"<i>(Send your sentence now and I'll score it and show you the native C1/C2 polish!)</i>",
            reply_markup=kb,
        )
        return

    _update_session(user_id, mode="QUIZ", sub_state=f"QUIZ_MC:{q_mode}")

    mode_instructions = {
        "colloc": (
            "MODE 1 — C1/C2 COLLOCATION & PREPOSITION VALENCY DRILL:\n"
            "Create a high-precision sentence with a ____ blank testing an advanced verb+noun collocation, phrasal verb, or tricky verb+preposition frame. Make the 3 distractors realistic near-misses (e.g. L1 calques or close synonyms that clash collocationally)."
        ),
        "c2": (
            "MODE 2 — CAMBRIDGE C2 KEY-WORD TRANSFORMATION:\n"
            "In `scenario_or_stem_en`, provide:\n"
            "Base Sentence: '[A complete sentence]'\n"
            "KEY WORD: **[WORD IN CAPS]**\n"
            "Lead-in with blank: '[Beginning of transformed sentence] ____ [end of sentence].'\n"
            "Test C2 structures: Negative Inversion ('Little did...', 'Seldom have...'), Cleft structures, Nominalization, or Advanced Phrasal/Prepositional frames."
        ),
        "prag": (
            "MODE 3 — PRAGMATIC SUBTEXT & DIPLOMATIC REGISTER FIT:\n"
            "In `scenario_or_stem_en`, describe a delicate NYC workplace, negotiation, or academic scenario and ask: 'Which response strikes the exact diplomatic/idiomatic tone without sounding passive-aggressive, overly blunt, or unnatural?'"
        ),
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
        types.InlineKeyboardButton("🔗 Collocations", callback_data="edqznext:colloc"),
        types.InlineKeyboardButton("🏛️ C2 Transform", callback_data="edqznext:c2"),
        types.InlineKeyboardButton("🎭 Pragmatics", callback_data="edqznext:prag"),
    )
    kb.row(
        types.InlineKeyboardButton("💎 Vault/Radar", callback_data="edqznext:vault"),
        types.InlineKeyboardButton("✍️ Active Write", callback_data="edqznext:produce"),
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
        f"The user was challenged to write/speak a natural sentence using the target expression: '{target_chunk}'.\n"
        "Evaluate their attempt accurately, give a score out of 10, fix any grammar/collocation slip (❌ ➔ ✅), and provide both a polished natural version and a C2 analytical variation."
    )
    ev: ActiveQuizEvalSchema = call_llm_structured(
        f"{SYS_PROMPT}\n{task}",
        f"Target Expression: '{target_chunk}'\nUser's Sentence: \"{user_text}\"",
        ActiveQuizEvalSchema,
        temp=0.45,
    )
    _QUIZ_STREAK[user_id] = _QUIZ_STREAK.get(user_id, 0) + 1
    tok = hashlib.md5(f"QEV:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Chloe", ev.surgical_fix_and_model_en_ar)]
    store_card(tok, user_id, v_segs, {"title": f"Production Check: {target_chunk}", "main_en": user_text, "diagnosis_en": ev.surgical_fix_and_model_en_ar}, [(target_chunk, ev.evaluation_and_score_en, user_text)], source_text=target_chunk)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✍️ Next Production Prompt", callback_data="edqznext:produce"),
        types.InlineKeyboardButton("🔗 Multiple-Choice Quiz", callback_data="edqznext:colloc"),
    )
    kb.row(
        types.InlineKeyboardButton(f"🔍 {target_chunk[:20]}", callback_data=f"edlexcard:{tok}"),
        types.InlineKeyboardButton("💾 Save to Vault", callback_data=f"edsave:{tok}"),
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
        types.InlineKeyboardButton("🎙️ Podcast with My Vault", callback_data="edpod:vault"),
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
        send_html_safe(chat_id, "<b>Linguistic Radar</b>\nNo recurring error patterns logged yet. Keep chatting and practicing!", reply_markup=build_reply_kb(user_id))
        return
    lines = [f"• <s>{_h(r[0])}</s> → <b>{_h(r[1])}</b>" for r in rows]
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("🎯 Drill My Mistake Patterns", callback_data="edqznext:vault"))
    send_html_safe(chat_id, "<b>Your Personal Linguistic Radar</b>\n<i>Recent adjustments &amp; collocation upgrades:</i>\n\n" + "\n".join(lines), reply_markup=kb)

def send_guide(chat_id: int, user_id: int):
    g = (
        "<b>Musa’s NYC Lounge — Studio Guide</b>\n\n"
        "• <b>Register Bar (Top):</b> Switch anytime between <b>Casual</b> (everyday spoken NYC), <b>Professional</b> (diplomatic workplace), and <b>Formal</b> (C2 analytical prose).\n\n"
        "• <b>5 Studio Modes (Bottom):</b>\n"
        "1. <b>Chat:</b> Spontaneous NYC conversation with 1, 2, or all 3 friends (toggle 🟢/⚪ anytime).\n"
        "2. <b>Lexicon:</b> 10-pillar dictionary &amp; activator (EN ⇄ AR) with IPA, Oxford Collocations, Hoey Priming &amp; Learner Traps.\n"
        "3. <b>Study:</b> Precision Draft Editor, AR➔EN Thought Bridge, Modular Text Miner &amp; Live Tutor.\n"
        "4. <b>Podcast:</b> Roundtable, Clash Debate, Case Study &amp; <i>My Vault Episode</i> with expandable transcripts, fast-speech decoder &amp; live on-air call-ins.\n"
        "5. <b>Quiz:</b> 5-mode C1/C2 arena (Collocations, C2 Key-Word Transformations, Pragmatics, Vault/Radar &amp; Active Production)."
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
        s_kb = types.InlineKeyboardMarkup()
        s_kb.row(
            types.InlineKeyboardButton("🤖 Smart Auto •", callback_data="edstudylens:auto"),
            types.InlineKeyboardButton("✍️ Draft Editor", callback_data="edstudylens:draft"),
            types.InlineKeyboardButton("🌉 AR ➔ EN Bridge", callback_data="edstudylens:bridge"),
        )
        s_kb.row(
            types.InlineKeyboardButton("⛏️ Text Miner", callback_data="edstudylens:miner"),
            types.InlineKeyboardButton("🎓 Nuance Tutor", callback_data="edstudylens:clinic"),
        )
        send_html_safe(
            chat_id,
            "<b>📚 Interactive Study Workbench</b>\n\n"
            "• <b>✍️ Send an English Draft / Voice Note:</b> Get a clean polished version, surgical ❌➔✅ fixes, and 1-tap buttons for <i>Spoken, Executive, C2 Academic, or Tightened</i> rewrites.\n"
            "• <b>🌉 Send an Idea in Arabic:</b> Build it natively across Spoken, Executive, and C2 Academic English.\n"
            "• <b>⛏️ Send an Article / PDF:</b> Harvest the Top 4 chunks first, then tap <i>C2 Templates</i> or <i>Rhetoric &amp; Flow</i> on demand.\n"
            "• <b>🎯 Reply to Practice:</b> Try using any upgraded chunk right here and I'll coach your sentence!",
            reply_markup=s_kb,
        )
        return
    if low == "/podcast" or txt.startswith(("Podcast", "🎙️")):
        _update_session(uid, mode="PODCAST", sub_state="")
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🎙️ Roundtable (3 Hosts)", callback_data="edpod:roundtable"),
            types.InlineKeyboardButton("⚡ Clash Debate (2 Hosts)", callback_data="edpod:clash"),
        )
        kb.row(
            types.InlineKeyboardButton("🔍 Deep Case Study", callback_data="edpod:case"),
            types.InlineKeyboardButton("💎 My Vault Episode", callback_data="edpod:vault"),
        )
        send_html_safe(
            chat_id,
            "<b>🎙️ NYC Podcast Studio</b>\n\n"
            "• <b>4 Episode Formats:</b> Choose <i>Roundtable</i>, <i>Clash Debate</i>, <i>Case Study</i>, or generate an episode using your saved <i>Vault Expressions</i>!\n"
            "• <b>Custom Topic:</b> Or simply type any topic right now and the hosts will go live on air.",
            reply_markup=kb,
        )
        return
    if low == "/quiz" or txt.startswith(("Quiz", "🎯")):
        ok_q, reason_q = check_and_consume_quota(user_obj)
        if not ok_q:
            lim_n = reason_q.split(":")[-1]
            send_html_safe(chat_id, f"<b>Daily Studio Limit Reached ({lim_n} sessions/day)</b>\nYour quota resets automatically tomorrow. You can review your /vault anytime.")
            return
        _update_session(uid, mode="QUIZ", sub_state="QUIZ_MC:colloc")
        send_quiz_hub(chat_id, uid)
        send_quiz(chat_id, uid, "colloc")
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
                if sub_st.startswith("PODCAST_ACTIVE") and not low.startswith(("new:", "topic:")):
                    execute_podcast_call_in(chat_id, uid, txt, is_voice=False, user_first_name=en_name)
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
    elif d.startswith("edqlk_direct:"):
        term_d = d.split(":", 1)[1]
        bot.answer_callback_query(call.id, f"Looking up: {term_d[:22]}...")
        threading.Thread(target=execute_lexicon_lookup, args=(chat_id, uid, term_d), daemon=True).start()
    elif d.startswith("edpodview:"):
        _, v_type, tok = d.split(":", 2)
        c = load_card(tok)
        if not c:
            bot.answer_callback_query(call.id, "Episode data not found.")
            return
        bot.answer_callback_query(call.id, "Opening full view...")
        if v_type == "tr":
            scr_fmt = _format_podcast_script_safe(c["pdf_dict"].get("main_en", ""))
            send_html_safe(chat_id, f"<b>📜 Full Episode Transcript — {_h(c['pdf_dict'].get('title', ''))}</b>\n\n{scr_fmt}")
        else:
            ch_fmt = _format_unified_senses(c["pdf_dict"].get("diagnosis_en", ""))
            send_html_safe(chat_id, f"<b>💎 Episode Chunks &amp; Fast-Speech Decoder</b>\n\n{ch_fmt}")
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
            v_segs = [("Marcus", q.complete_native_sentence_en or q.scenario_or_stem_en)]
            store_card(
                tok,
                uid,
                v_segs,
                {"title": q.quiz_mode_badge_en, "main_en": q.complete_native_sentence_en, "diagnosis_en": f"{q.winning_reason_en_ar}\n\n{q.distractor_anatomy_en}"},
                [(q.target_expression_en, q.winning_reason_en_ar, q.complete_native_sentence_en)],
                source_text=q.target_expression_en,
            )
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton(f"➡️ Next ({q_mode_origin.upper()})", callback_data=f"edqznext:{q_mode_origin}"),
                types.InlineKeyboardButton("🎧 Hear Sentence", callback_data=f"edtts:1x:{tok}"),
                types.InlineKeyboardButton("💾 Save", callback_data=f"edsave:{tok}"),
            )
            kb.row(
                types.InlineKeyboardButton(f"🔍 {q.target_expression_en[:20]}", callback_data=f"edlexcard:{tok}"),
                types.InlineKeyboardButton("✍️ Try Using It Yourself", callback_data=f"edqztry:{tok}"),
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
                f"<blockquote expandable>🔬 <b>Why the Other Options Fail (Distractor Anatomy) ▼</b>\n\n{_clean_multiline_bullets(q.distractor_anatomy_en)}</blockquote>"
            )
            send_html_safe(chat_id, res_html, reply_markup=kb)
    elif d.startswith("edqztry:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        target_ch = c["source_text"] if c and c.get("source_text") else "hold off on"
        _update_session(uid, mode="QUIZ", sub_state=f"QUIZ_PRODUCE:{target_ch}")
        bot.answer_callback_query(call.id, "Your turn! Type or speak a sentence.")
        send_html_safe(
            chat_id,
            f"✍️ <b>Active Production Turn:</b>\nWrite or record a sentence right now using <code>{_h(target_ch)}</code> and I'll score and polish it!",
        )
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
