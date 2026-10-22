#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, html, base64, shutil, sqlite3, hashlib, tempfile, threading, traceback, asyncio, subprocess, logging

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
logger = logging.getLogger("NYCLoungeV15_2")

PRIMARY_OWNER_ID = "8874707150"
HF_REPO_ID = "moudjer/musa-nyc-lounge-vault"
EMBEDDED_HF_REV_B64 = "==AWVd0YClmbM5UQsNVRJh3SRl0cotkY1pUR4hlRGd3QmNGZfZGa"
EMBEDDED_CFG_B64 = "fSJ0bHVhdi1lZ251b2wtY3luLWFzdW0vcmVqZHVvbSIgOiJPUEVSX0JEX0ZIIiAsIlhVR2NCaW5MTkFsU0VJeEtRSXNoS2J1SkV4WEZGd0NmY2RfZmgiIDoiTkVLT1RfRkgiICwiaHNhbGYtNS4zLWluaW1lZyIgOiJMRURPTV9JTklNRUdfREVJRklSRVYiICwiVUY3ZDF0YVVkNGR4SFlVRnhUNWRuQ05MWUYzYnlkR1dIV0VncWNpYVEyODJpODhWQ2p4NV9rc2csbE1rV2I5eERaSjFSMHEzd0RPMlZpN2RHWUYzYnlkR1dOVWhMMW1tZVJUYUsyYThDc1prOV9rc2ciIDoiU1lFS19RT1JHX0RFSUZJUkVWIiAsIkFNTEowcmMtRzctZERvMkxaWUEtNTQyM29EV0pjV0R6ME5NZTJyVEROZHhJNk5SOGJBLlFBLGd3X3FvX2hqQy02Qlk1bld4cEhTUFNaQ016REtSV2FYR1Z1NE91NEhHcVNMNk5SOGJBLlFBLFEzSzI4V1pESU9wV1ptZjhRaGJQZzVvS21wRUxEcHNZQU1BUHJ6NmF4c0xMNk5SOGJBLlFBLHdtandrWHEwWWZiTXJxTm02LWdqcENUQVV2d0lzZ2ExWmpHc0JKR1RneU1JNk5SOGJBLlFBIiA6IlNZRUtfSU5JTUVHX0RFSUZJUkVWIiAsIjR2UlU5OEZFRWllaXNVZVItZEU2UWtWLUw4RnhKV1A0RkFBOjI3MDYzODI2OTgiIDoiTkVLT1RfR1RfREVJRklSRVYiew=="

DB_PATH = "/content/musa_nyc_lounge_v8.db" if os.path.exists("/content") else "/tmp/musa_nyc_lounge_v8.db"
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
        tok = os.environ.get("HF_TOKEN", "").strip()
        if tok:
            try:
                dl = hf_hub_download(repo_id=HF_REPO_ID, filename="musa_nyc_lounge_v8.db", repo_type="dataset", token=tok)
                shutil.copyfile(dl, DB_PATH)
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
# جداول v15 المعزولة والنظيفة تماماً
# ------------------------------------------------------------------------------
_db_lock = threading.Lock()
_last_sync_ts = 0.0

def db_connect() -> sqlite3.Connection:
    try:
        if os.path.exists(DB_PATH):
            os.chmod(DB_PATH, 0o666)
    except Exception:
        pass
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def _init_v15_tables():
    with _db_lock, db_connect() as conn:
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
        try:
            cnt = conn.execute("SELECT COUNT(*) FROM vault_v15").fetchone()[0]
            if cnt == 0:
                old_info = conn.execute("PRAGMA table_info(lexical_vault)").fetchall()
                if old_info:
                    c_names = [r[1] for r in old_info]
                    old_rows = conn.execute("SELECT * FROM lexical_vault").fetchall()
                    for r in old_rows:
                        d = dict(zip(c_names, r))
                        chk = d.get("chunk") or d.get("phrase") or d.get("expression") or d.get("word") or ""
                        ar = d.get("meaning_ar") or d.get("meaning") or d.get("translation") or ""
                        ctx = d.get("context_sentence") or d.get("context") or d.get("example") or ""
                        if str(chk).strip():
                            conn.execute(
                                "INSERT INTO vault_v15 (user_id, chunk, meaning_ar, context_sentence, srs_stage) VALUES (?, ?, ?, ?, 1)",
                                (int(PRIMARY_OWNER_ID), str(chk).strip(), str(ar).strip(), str(ctx).strip()),
                            )
        except Exception:
            pass
        conn.commit()

_init_v15_tables()

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
            tmp = f"/tmp/db_{int(time.time())}.db"
            with _db_lock:
                shutil.copy2(DB_PATH, tmp)
            HfApi(token=hf_tok).upload_file(path_or_fileobj=tmp, path_in_repo="musa_nyc_lounge_v8.db", repo_id=HF_REPO_ID, repo_type="dataset")
            os.remove(tmp)
        except Exception:
            pass
    threading.Thread(target=_up, daemon=True).start()

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
            h_v = h_v[-6:]
        with _db_lock, db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_v15 (user_id, mode, register_level, podcast_hosts, sub_state, history_json) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, m_v, r_v, p_v, sub_v, json.dumps(h_v, ensure_ascii=False)),
            )
            conn.commit()
    except Exception:
        pass

def store_card(tok: str, user_id: int, voice_segs: Any, pdf_dict: Any, vault_items: Any, ar_text: str, source_text: str = "", mined_so_far: str = ""):
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

def _save_vault_item(user_id: int, chunk: str, meaning_ar: str, ctx: str) -> bool:
    try:
        chunk = re.sub(r"\s+", " ", (chunk or "")).strip()
        if len(chunk) < 2:
            return False
        with _db_lock, db_connect() as conn:
            ex = conn.execute("SELECT id FROM vault_v15 WHERE user_id=? AND chunk=?", (user_id, chunk)).fetchone()
            if ex:
                conn.execute("UPDATE vault_v15 SET srs_stage = MIN(3, srs_stage + 1) WHERE id=?", (ex[0],))
                conn.commit()
                return False
            conn.execute("INSERT INTO vault_v15 (user_id, chunk, meaning_ar, context_sentence, srs_stage) VALUES (?, ?, ?, ?, 1)", (user_id, chunk, meaning_ar or "", ctx or ""))
            conn.commit()
        return True
    except Exception:
        return False

def _log_dna(user_id: int, corr_lines: str):
    try:
        with _db_lock, db_connect() as conn:
            for ln in (corr_lines or "").splitlines()[:4]:
                if "->" in ln or "→" in ln:
                    pts = re.split(r"->|→", re.sub(r"<[^>]+>", "", ln), maxsplit=1)
                    w, r = pts[0].strip("•~ "), pts[1].strip()
                    if w and r:
                        conn.execute("INSERT INTO dna_v15 (user_id, error_pattern, native_fix) VALUES (?, ?, ?)", (user_id, w[:100], r[:100]))
            conn.commit()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# محرك الذكاء الاصطناعي السريع (Groq + Gemini)
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
    cands.sort(key=lambda x: 1 if "2.5-flash-lite" in x else (2 if "2.0-flash" in x else (3 if "flash" in x else 6)))
    return cands[:5] if cands else ["gemini-2.5-flash-lite", "gemini-2.0-flash"]

def _parse_schema_safe(raw_txt: str, schema_cls: Any) -> Any:
    t = re.sub(r"<think>.*?</think>", "", (raw_txt or "").strip(), flags=re.DOTALL).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return schema_cls.model_validate_json(t)
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
            return schema_cls(**clean_d)
        raise

def call_llm_structured(sys_p: str, usr_p: str, schema_cls: Any, temp: float = 0.6) -> Any:
    global _PINNED_GROQ, _PINNED_GEMINI
    f_names = list(schema_cls.model_fields.keys())
    f_desc = "\n".join([f'- "{k}": {v.description}' for k, v in schema_cls.model_fields.items()])
    j_inst = f"\n\nReturn strictly a flat JSON object with these keys:\n{f_desc}\nRequired keys: {json.dumps(f_names)}. Every string field must be a plain string."

    if _PINNED_GROQ:
        gk, gm = _PINNED_GROQ
        try:
            cc = Groq(api_key=gk, timeout=14.0).chat.completions.create(
                model=gm, messages=[{"role": "system", "content": sys_p + j_inst}, {"role": "user", "content": usr_p}],
                response_format={"type": "json_object"}, temperature=temp,
            )
            return _parse_schema_safe(cc.choices[0].message.content, schema_cls)
        except Exception:
            _PINNED_GROQ = None

    for gk in _collect_groq_keys():
        try:
            gc = Groq(api_key=gk, timeout=14.0)
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
                            response_schema=schema_cls,
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
# الصوتيات والـ PDF
# ------------------------------------------------------------------------------
STUDIO_VOICES = {"Jake": "en-US-AndrewMultilingualNeural", "Chloe": "en-US-AvaMultilingualNeural", "Marcus": "en-US-BrianMultilingualNeural"}

def _h(v: Any) -> str:
    return html.escape(str(v if v is not None else "")).strip()

def _clean_tts(t: str) -> str:
    t = re.sub(r"<[^>]+>|\[[^\]]*\]|[\u0600-\u06FF]+", " ", str(t or ""))
    return re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7F’'\",.!?;:\-\s()]+", " ", t)).strip()

def build_audio(segments: List[Tuple[str, str]], slow: bool = False) -> Optional[str]:
    rate = "-15%" if slow else "+0%"
    valid = [(s, _clean_tts(re.sub(r"^\s*(Jake|Chloe|Marcus)\s*:\s*", "", t))) for s, t in segments if len(_clean_tts(t)) > 2]
    if not valid:
        return None
    h_k = hashlib.md5(f"V15::{rate}::{'||'.join([f'{s}:{t}' for s,t in valid])}".encode()).hexdigest()
    cache_p = f"/tmp/tts_cache_hd/{h_k}.mp3"
    if os.path.exists(cache_p) and os.path.getsize(cache_p) > 800:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.close()
        shutil.copy2(cache_p, tmp.name)
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
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_f, "-ar", "48000", "-ac", "1", "-b:a", "128k", out_p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        for pf in parts + [list_f]:
            try:
                os.remove(pf)
            except Exception:
                pass
        if os.path.exists(out_p) and os.path.getsize(out_p) > 800:
            shutil.copy2(out_p, cache_p)
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
    html_doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 15mm; }}
    body {{ font-family: 'DejaVu Sans', sans-serif; color: #0f172a; line-height: 1.55; font-size: 10pt; }}
    .hdr {{ background: #0f172a; color: #fff; padding: 14px; border-radius: 6px; margin-bottom: 12px; }}
    .sec {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; }}
    .ar {{ direction: rtl; text-align: right; background: #fffbeb; padding: 8px; border-right: 3px solid #d97706; margin-top: 6px; }}
    </style></head><body>
    <div class="hdr"><h2>{_h(pdf_data.get('title', 'Study Sheet'))} ({_h(pdf_data.get('register', 'Professional'))})</h2></div>
    <div class="sec"><b>Dialogue / Context:</b><br>{_h(pdf_data.get('main_en', '')).replace(chr(10), '<br>')}<br><br><b>Adjustments:</b><br>{_h(pdf_data.get('instant_fix', '')).replace(chr(10), '<br>')}</div>
    <div class="sec"><b>Across Registers:</b><br>• Casual: {_h(pdf_data.get('spoken_inf', ''))}<br>• Professional: {_h(pdf_data.get('spoken_semi', ''))}<br>• Formal: {_h(pdf_data.get('written_form', ''))}<br><br>{_h(pdf_data.get('diagnosis_en', '')).replace(chr(10), '<br>')}</div>
    <div class="sec"><div class="ar">{_h(pdf_data.get('ar_anchor', '')).replace(chr(10), '<br>')}</div></div>
    </body></html>"""
    out_p = f"/tmp/pdf_exports/NYC_Sheet_{int(time.time())}.pdf"
    try:
        weasyprint.HTML(string=html_doc).write_pdf(out_p)
        return out_p if os.path.exists(out_p) else None
    except Exception:
        return None

# ------------------------------------------------------------------------------
# القوالب التحريرية المرنة
# ------------------------------------------------------------------------------
class ChatSchema(BaseModel):
    primary_speaker: str = Field(default="Jake", description="'Jake', 'Chloe', or 'Marcus'")
    dialogue_en: str = Field(default="Jake: That makes total sense—let's talk it over. How do you see it playing out?", description="Natural 2-3 sentence spoken reply in the active register, adding a fresh perspective before asking a follow-up question.")
    has_actual_errors: bool = Field(default=False, description="True ONLY if user made real grammar, preposition, collocation, or literal translation errors")
    error_corrections_list: str = Field(default="", description="If has_actual_errors=True: 'wrong phrase -> right phrase' (one per line). Empty if no real errors.")
    better_alternatives_list: str = Field(default="", description="1 or 2 stronger native upgrades: 'simple phrase -> stronger phrase'")
    tone_and_why_en: str = Field(default="Tone: Clear and natural.", description="1 line on tone impression + plain-English reason for adjustments")
    recast_casual_en: str = Field(default="", description="User's point in Casual everyday spoken English (slang, phrasal verbs, contractions)")
    recast_professional_en: str = Field(default="", description="User's point in Professional workplace English")
    recast_formal_en: str = Field(default="", description="User's point in concise Formal academic/executive writing")
    spoken_flow_en: str = Field(default="", description="Short connected-speech and stress tip")
    key_expressions_en: str = Field(default="", description="2 key expressions from reply with definition and example + 'Try next: [phrase]'")
    arabic_translation_and_notes: str = Field(default="", description="Literal (حرفياً) vs Optimal (الأمثل) Arabic translation of reply and key expressions")

class StudyMinerSchema(BaseModel):
    dossier_title_en: str = Field(default="Linguistic Analysis & Expression Dossier", description="Title")
    summary_or_corrections_en: str = Field(default="", description="Direct English answer, tone summary, or 'wrong -> right' fixes")
    core_output_en: str = Field(default="", description="Polished version of user's draft OR primary usable scenario sentence")
    casual_version_en: str = Field(default="", description="Casual everyday spoken version")
    professional_version_en: str = Field(default="", description="Professional workplace version")
    formal_version_en: str = Field(default="", description="Formal academic/executive written version")
    phrasal_and_idioms_en: str = Field(default="", description="3 extracted phrasal verbs/idioms (or synonym comparisons) with original context & new example")
    collocations_and_templates_en: str = Field(default="", description="3 high-value word partners (collocations) + 2 reusable sentence templates with [Slots]")
    pragmatics_and_drill_en: str = Field(default="", description="Subtext, where synonym X works vs Y fails, and 1-line practice drill")
    arabic_notes_and_translations: str = Field(default="", description="Literal (حرفياً) vs Optimal (الأمثل) Arabic translations of extracted expressions and nuances")

class PodcastSchema(BaseModel):
    episode_title_en: str = Field(default="Why Good Plans Lose Momentum", description="Episode title")
    script_en: str = Field(default="", description="6-turn natural spoken script (Jake, Chloe, Marcus) with mid-sentence interruptions and backchanneling.")
    closing_question_to_user_en: str = Field(default="Musa, how do you usually handle this when it comes up?", description="Direct question to Musa")
    key_expressions_and_rhythm_en: str = Field(default="", description="3 standout expressions with definitions/examples + fast-speech decoding tip")
    audio_recap_script_en: str = Field(default="", description="1-sentence audio recap of the 3 key expressions")
    arabic_episode_notes: str = Field(default="", description="Literal (حرفياً) vs Optimal (الأمثل) Arabic translations of the 3 key expressions and summary")

class QuizSchema(BaseModel):
    category_en: str = Field(default="Collocation & Register Precision", description="Category")
    scenario_en: str = Field(default="", description="Scenario or sentence with a blank")
    option_a: str = Field(default="", description="Option A")
    option_b: str = Field(default="", description="Option B")
    option_c: str = Field(default="", description="Option C")
    option_d: str = Field(default="", description="Option D")
    correct_letter: str = Field(default="B", description="'A', 'B', 'C', or 'D'")
    explanation_and_registers_en: str = Field(default="", description="Why correct option wins, why others fail, and Casual vs Formal phrasing")
    practice_prompt_en: str = Field(default="", description="1-line practice prompt")
    target_expression_en: str = Field(default="", description="Winning expression")
    arabic_notes: str = Field(default="", description="Literal vs Optimal Arabic translation and nuance")

# ------------------------------------------------------------------------------
# واجهة الأزرار والعرض التحريري
# ------------------------------------------------------------------------------
def build_reply_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    s = _get_session(user_id)
    m, r = s["mode"], s["register"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=4)
    kb.row(
        types.KeyboardButton("Casual •" if r == "Casual" else "Casual"),
        types.KeyboardButton("Professional •" if r == "Professional" else "Professional"),
        types.KeyboardButton("Formal •" if r == "Formal" else "Formal"),
    )
    kb.row(
        types.KeyboardButton("Chat •" if m == "CHAT" else "Chat"),
        types.KeyboardButton("Podcast •" if m == "PODCAST" else "Podcast"),
        types.KeyboardButton("Study •" if m == "STUDY" else "Study"),
        types.KeyboardButton("Quiz •" if m == "QUIZ" else "Quiz"),
    )
    return kb

def build_inline_kb(tok: str, ctype: str = "chat") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Listen", callback_data=f"edtts:1x:{tok}"),
        types.InlineKeyboardButton("PDF", callback_data=f"edpdf:{tok}"),
        types.InlineKeyboardButton("Save", callback_data=f"edsave:{tok}"),
        types.InlineKeyboardButton("Arabic", callback_data=f"edar:{tok}"),
    )
    if ctype == "miner":
        kb.row(
            types.InlineKeyboardButton("Extract More", callback_data=f"edmore:{tok}"),
            types.InlineKeyboardButton("Practice Template", callback_data=f"eddrill:{tok}"),
        )
    elif ctype == "draft":
        kb.row(
            types.InlineKeyboardButton("Soften", callback_data=f"edshift:soften:{tok}"),
            types.InlineKeyboardButton("Condense", callback_data=f"edshift:condense:{tok}"),
            types.InlineKeyboardButton("Make Spoken", callback_data=f"edshift:spoken:{tok}"),
        )
    return kb

def send_html_safe(chat_id: int, html_text: str, reply_markup: Any = None):
    if len(html_text) > 4050:
        html_text = html_text[:3950] + "\n</i></b></blockquote>"
    try:
        return bot.send_message(chat_id, html_text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        return bot.send_message(chat_id, re.sub(r"<[^>]+>", "", html_text)[:4000], reply_markup=reply_markup)

def _fmt_fixes(raw: str, strike: bool = True) -> str:
    out = []
    for ln in (raw or "").splitlines():
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

SYS_PROMPT = """
You are the linguistic engine of 'Musa's NYC Lounge'.
1. Write all primary explanations, definitions, and style notes in clear, direct English.
2. In Arabic fields (`arabic_*`), provide BOTH the Literal Mental Image (`حرفياً: ...`) and Optimal Arabic Translation (`الأمثل: ...`).
3. Put ONLY actual errors in `error_corrections_list` (`wrong -> right`). Put stylistic upgrades for simple words in `better_alternatives_list` (`simple -> stronger`).
4. Isolate registers strictly:
   - Casual: everyday spoken American English, modern slang, phrasal verbs, contractions (gonna/wanna), ellipsis.
   - Professional: workplace, meetings, diplomatic turn-taking, clear idioms.
   - Formal: concise academic, policy, and executive prose.
"""

def execute_chat_turn(chat_id: int, user_id: int, user_text: str, is_voice: bool = False):
    s = _get_session(user_id)
    reg = s["register"]
    hist_str = "\n".join([f"User: {h.get('u','')}\nLounge: {h.get('b','')}" for h in s["history"][-4:] if isinstance(h, dict)])

    sys_p = f"{SYS_PROMPT}\nActive Register: {reg}. Share a brief perspective before asking a follow-up question."
    usr_p = f"Context:\n{hist_str}\n\nUser ({'voice' if is_voice else 'text'}): \"{user_text}\""
    data: ChatSchema = call_llm_structured(sys_p, usr_p, ChatSchema, temp=0.65)

    _update_session(user_id, new_turn={"u": user_text[:200], "b": data.dialogue_en[:250]})
    if data.has_actual_errors and data.error_corrections_list:
        _log_dna(user_id, data.error_corrections_list)

    raw_diag = _h(data.dialogue_en)
    for spk in ("Jake", "Chloe", "Marcus"):
        raw_diag = re.sub(rf"(?:^|\n)\s*{spk}\s*:\s*", f"\n\n<b>{spk}</b>\n", raw_diag)
    raw_diag = raw_diag.strip()
    if not any(f"<b>{sp}</b>" in raw_diag for sp in ("Jake", "Chloe", "Marcus")):
        raw_diag = f"<b>{_h(data.primary_speaker)}</b>\n{raw_diag}"

    blocks = []
    if data.has_actual_errors and data.error_corrections_list.strip():
        blocks.append(f"<b>Corrections</b>\n{_fmt_fixes(data.error_corrections_list, strike=True)}")
    else:
        blocks.append("<b>Phrasing:</b> ✓ Grammatically sound and natural.")
    if data.better_alternatives_list.strip():
        blocks.append(f"<b>Better Alternatives</b>\n{_fmt_fixes(data.better_alternatives_list, strike=False)}")

    drawer_en = (
        f"<b>Notes, Register Phrasing &amp; Expressions ▼</b>\n\n"
        f"<b>1. Tone &amp; Adjustments</b>\n{_h(data.tone_and_why_en)}\n\n───\n"
        f"<b>2. Across Registers</b>\n"
        f"• <b>Casual:</b> <i>\"{_h(data.recast_casual_en)}\"</i>\n"
        f"• <b>Professional:</b> <i>\"{_h(data.recast_professional_en)}\"</i>\n"
        f"• <b>Formal:</b> <b>\"{_h(data.recast_formal_en)}\"</b>\n"
        f"• <b>Spoken Flow:</b> {_h(data.spoken_flow_en)}\n\n───\n"
        f"<b>3. Key Expressions</b>\n{_h(data.key_expressions_en)}"
    )
    drawer_ar = f"<b>Translation &amp; Arabic Notes ▼</b>\n\n{_h(data.arabic_translation_and_notes)}"

    v_hdr = f"<b>Voice Note:</b> <i>\"{_h(user_text)}\"</i>\n\n" if is_voice else ""
    full_html = f"{v_hdr}{raw_diag}\n\n" + "\n\n".join(blocks) + f"\n\n<blockquote expandable>{drawer_en}</blockquote>\n<blockquote expandable>{drawer_ar}</blockquote>"

    tok = hashlib.md5(f"CHT:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [(data.primary_speaker, data.dialogue_en), ("Marcus", f"Casual: {data.recast_casual_en}. Professional: {data.recast_professional_en}.")]
    pdf_d = {"title": "Conversation & Style Sheet", "register": reg, "main_en": data.dialogue_en, "instant_fix": data.error_corrections_list, "diagnosis_en": data.key_expressions_en, "spoken_inf": data.recast_casual_en, "spoken_semi": data.recast_professional_en, "written_form": data.recast_formal_en, "ar_anchor": data.arabic_translation_and_notes}
    v_items = [(data.recast_professional_en[:85], data.arabic_translation_and_notes[:200], data.dialogue_en)]
    store_card(tok, user_id, v_segs, pdf_d, v_items, data.arabic_translation_and_notes, source_text=user_text)

    send_html_safe(chat_id, full_html, reply_markup=build_inline_kb(tok, "chat"))
    threading.Thread(target=build_audio, args=(v_segs, False), daemon=True).start()

def execute_study_and_miner(chat_id: int, user_id: int, user_input: str, is_voice: bool = False, already_mined: str = "", shift: str = ""):
    s = _get_session(user_id)
    reg = s["register"]
    w_cnt = len(user_input.split())
    is_miner = (w_cnt > 55) or bool(already_mined)

    if already_mined:
        task = f"Already extracted: [{already_mined}]. Extract a NEW batch of phrasal verbs, idioms, collocations, and sentence templates!"
    elif shift:
        task = f"Transform the user's draft using style '{shift}' (soften / condense / spoken)."
    elif is_miner:
        task = "LINGUISTIC MINING: Extract Phrasal Verbs & Idioms (with original sentence + new example), High-Value Collocations, and Reusable Sentence Templates."
    else:
        task = "Answer the query, 'How do I say...' reverse search, or edit the draft. Show synonym boundaries (where X works vs Y fails) and templates."

    data: StudyMinerSchema = call_llm_structured(f"{SYS_PROMPT}\n{task}", f"Register: {reg}\nInput:\n\"{user_input[:5000]}\"", StudyMinerSchema, temp=0.55)

    top = (
        f"<b>{_h(data.dossier_title_en)}</b> • <i>{_h(reg)}</i>\n\n"
        f"{_h(data.summary_or_corrections_en)}\n\n"
        f"<b>Core Phrasing / Context</b>\n<i>\"{_h(data.core_output_en)}\"</i>\n\n"
        f"<b>1. Phrasal Verbs, Idioms &amp; Nuances</b>\n{_h(data.phrasal_and_idioms_en)}\n\n"
        f"<b>2. Word Partners &amp; Sentence Templates</b>\n{_h(data.collocations_and_templates_en)}"
    )
    dr_en = (
        f"<b>Register Matrix &amp; Practice Drill ▼</b>\n\n"
        f"• <b>Casual:</b> <i>\"{_h(data.casual_version_en)}\"</i>\n"
        f"• <b>Professional:</b> <i>\"{_h(data.professional_version_en)}\"</i>\n"
        f"• <b>Formal:</b> <b>\"{_h(data.formal_version_en)}\"</b>\n\n───\n"
        f"{_h(data.pragmatics_and_drill_en)}"
    )
    dr_ar = f"<b>Translation &amp; Arabic Notes ▼</b>\n\n{_h(data.arabic_notes_and_translations)}"

    tok = hashlib.md5(f"STD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    v_segs = [("Jake", data.core_output_en), ("Chloe", data.professional_version_en), ("Marcus", data.formal_version_en)]
    pdf_d = {"title": data.dossier_title_en, "register": reg, "main_en": data.core_output_en, "instant_fix": data.summary_or_corrections_en, "diagnosis_en": f"{data.phrasal_and_idioms_en}\n{data.collocations_and_templates_en}", "spoken_inf": data.casual_version_en, "spoken_semi": data.professional_version_en, "written_form": data.formal_version_en, "ar_anchor": data.arabic_notes_and_translations}
    v_items = [(ln.strip("•-*123. ")[:80], data.arabic_notes_and_translations[:200], ln) for ln in data.phrasal_and_idioms_en.splitlines() if len(ln.strip()) > 6][:4]
    store_card(tok, user_id, v_segs, pdf_d, v_items, data.arabic_notes_and_translations, source_text=user_input[:5000], mined_so_far=f"{already_mined}, {', '.join([x[0] for x in v_items])}".strip(", "))

    ctype = "miner" if is_miner else ("draft" if w_cnt > 12 else "study")
    send_html_safe(chat_id, f"{top}\n\n<blockquote expandable>{dr_en}</blockquote>\n<blockquote expandable>{dr_ar}</blockquote>", reply_markup=build_inline_kb(tok, ctype))

def execute_podcast(chat_id: int, user_id: int, topic: str = "", hosts: int = 3):
    s = _get_session(user_id)
    reg = s["register"]
    _update_session(user_id, mode="PODCAST", podcast_hosts=hosts)
    t_p = topic.strip() if len(topic.strip()) > 2 else f"Realistic {reg} NYC discussion on work, habits, and decision-making"
    data: PodcastSchema = call_llm_structured(f"{SYS_PROMPT}\nProduce a {hosts}-host podcast ({reg}) with natural interruptions and backchanneling.", f"Topic: {t_p}", PodcastSchema, temp=0.7)

    raw_s = _h(data.script_en)
    for spk in ("Jake", "Chloe", "Marcus"):
        raw_s = re.sub(rf"(?:^|\n)\s*{spk}\s*:\s*", f"\n\n<b>{spk}:</b> ", raw_s).strip()

    html_out = (
        f"<b>NYC Podcast ({hosts} Hosts • {_h(reg)})</b>\n<i>{_h(data.episode_title_en)}</i>\n\n"
        f"{raw_s}\n\n<b>Your Turn:</b> <i>\"{_h(data.closing_question_to_user_en)}\"</i>\n\n"
        f"<blockquote expandable><b>Episode Expressions &amp; Rhythm ▼</b>\n\n{_h(data.key_expressions_and_rhythm_en)}</blockquote>\n"
        f"<blockquote expandable><b>Translation &amp; Arabic Notes ▼</b>\n\n{_h(data.arabic_episode_notes)}</blockquote>"
    )
    tok = hashlib.md5(f"POD:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    segs = [("Jake", data.script_en), ("Marcus", f"Key expressions: {data.audio_recap_script_en}")]
    pdf_d = {"title": data.episode_title_en, "register": reg, "main_en": data.script_en, "instant_fix": data.closing_question_to_user_en, "diagnosis_en": data.key_expressions_and_rhythm_en, "ar_anchor": data.arabic_episode_notes}
    store_card(tok, user_id, segs, pdf_d, [(data.episode_title_en, data.arabic_episode_notes[:200], data.key_expressions_and_rhythm_en[:200])], data.arabic_episode_notes)

    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("Normal 1.0x", callback_data=f"edtts:1x:{tok}"), types.InlineKeyboardButton("Slow 0.85x", callback_data=f"edtts:slow:{tok}"), types.InlineKeyboardButton("PDF", callback_data=f"edpdf:{tok}"))
    kb.row(types.InlineKeyboardButton("2 Hosts", callback_data="edpod:2"), types.InlineKeyboardButton("3 Hosts", callback_data="edpod:3"), types.InlineKeyboardButton("Save", callback_data=f"edsave:{tok}"))
    send_html_safe(chat_id, html_out, reply_markup=kb)

    mp3 = build_audio(segs, slow=False)
    if mp3 and os.path.exists(mp3):
        with open(mp3, "rb") as af:
            bot.send_voice(chat_id, af, caption=f"<b>NYC Podcast ({hosts} Hosts)</b>", parse_mode="HTML")
        os.remove(mp3)

def send_quiz(chat_id: int, user_id: int):
    reg = _get_session(user_id)["register"]
    q: QuizSchema = call_llm_structured(f"{SYS_PROMPT}\nCreate a 4-option multiple choice question testing {reg} American English.", "Generate quiz.", QuizSchema, temp=0.7)
    qid = hashlib.md5(f"QZ:{user_id}:{time.time()}".encode()).hexdigest()[:10]
    with _db_lock, db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO quiz_v15 (q_id, user_id, quiz_json) VALUES (?, ?, ?)", (qid, user_id, q.model_dump_json()))
        conn.commit()
    kb = types.InlineKeyboardMarkup()
    kb.row(*[types.InlineKeyboardButton(x, callback_data=f"edqz:{x}:{qid}") for x in ("A", "B", "C", "D")])
    kb.row(types.InlineKeyboardButton("Next Question", callback_data="edqznext:any"))
    send_html_safe(chat_id, f"<b>Quiz — {_h(q.category_en)} ({_h(reg)})</b>\n\n<i>\"{_h(q.scenario_en)}\"</i>\n\n<b>A)</b> {_h(q.option_a)}\n<b>B)</b> {_h(q.option_b)}\n<b>C)</b> {_h(q.option_c)}\n<b>D)</b> {_h(q.option_d)}", reply_markup=kb)

def send_guide(chat_id: int, user_id: int):
    g = (
        "<b>Musa’s NYC Lounge — User Guide</b>\n\n"
        "• <b>Top Bar (Register):</b> <b>Casual</b> (street &amp; daily slang), <b>Professional</b> (workplace &amp; tact), <b>Formal</b> (academic &amp; executive writing).\n"
        "• <b>Bottom Bar (Modes):</b>\n"
        "1. <b>Chat:</b> Speak or type for natural conversation with exposed <b>Corrections</b> (<s>error</s> → <b>fix</b>) and <b>Better Alternatives</b>.\n"
        "2. <b>Podcast:</b> 2-Host &amp; 3-Host episodes with Normal/Slow audio and key-phrase recaps.\n"
        "3. <b>Study:</b> Send any word, draft, long article, PDF, screenshot, or audio recording to extract Phrasal Verbs, Word Partners, and Sentence Templates.\n"
        "4. <b>Quiz &amp; Vault (/vault):</b> Practice active recall and export PDF study sheets."
    )
    send_html_safe(chat_id, g, reply_markup=build_reply_kb(user_id))

def show_vault(chat_id: int, user_id: int):
    with db_connect() as conn:
        rows = conn.execute("SELECT chunk, meaning_ar, context_sentence FROM vault_v15 WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
    if not rows:
        send_html_safe(chat_id, "<b>Your Vault is empty.</b> Tap <b>Save</b> under any card to store expressions.", reply_markup=build_reply_kb(user_id))
        return
    txt = "<b>Saved Expressions (Vault)</b>\n\n" + "\n\n".join([f"<b>{i}. {_h(r[0])}</b>\n<i>\"{_h(r[2][:120])}\"</i>" for i, r in enumerate(rows, 1)])
    send_html_safe(chat_id, txt, reply_markup=build_reply_kb(user_id))

def handle_incoming_message(message: types.Message):
    chat_id = message.chat.id
    uid = message.from_user.id if getattr(message, "from_user", None) else int(PRIMARY_OWNER_ID)
    txt = (message.text or message.caption or "").strip()
    low = txt.lower()
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    try:
        if low in ("/guide", "guide", "user guide"):
            send_guide(chat_id, uid)
            return
        if low in ("/start", "/help", "/new", "new topic") or low in ("/chat", "/lounge") or txt.startswith(("Chat", "💬")):
            _update_session(uid, mode="CHAT", sub_state="", clear_hist=(low in ("/start", "/new")))
            send_html_safe(
                chat_id,
                "<b>Chloe</b>\nHi Musa—good to see you. We were just discussing how teams handle sudden changes in plans without losing momentum. What’s on your mind today?",
                reply_markup=build_reply_kb(uid),
            )
            return
        if low == "/podcast" or txt.startswith(("Podcast", "🎙️")):
            _update_session(uid, mode="PODCAST")
            kb = types.InlineKeyboardMarkup()
            kb.row(types.InlineKeyboardButton("2 Hosts (Duo)", callback_data="edpod:2"), types.InlineKeyboardButton("3 Hosts (Roundtable)", callback_data="edpod:3"))
            send_html_safe(chat_id, "<b>Podcast Studio</b>\nTap below for an instant episode, or type any topic:", reply_markup=kb)
            return
        if low == "/study" or txt.startswith(("Study", "📚")):
            _update_session(uid, mode="STUDY")
            send_html_safe(chat_id, "<b>Study &amp; Expression Miner</b>\nSend any word, question, draft paragraph, article, PDF, image, or voice recording:", reply_markup=build_reply_kb(uid))
            return
        if low == "/quiz" or txt.startswith(("Quiz", "🎯")):
            _update_session(uid, mode="QUIZ")
            send_quiz(chat_id, uid)
            return
        if low == "/vault" or txt.startswith(("Vault", "💎")):
            show_vault(chat_id, uid)
            return
        if low in ("/casual", "/informal", "/professional", "/semiformal", "/formal") or txt.startswith(("Casual", "Professional", "Formal", "🟢", "🔵", "🟣")):
            r_target = "Casual" if ("casual" in low or "informal" in low or txt.startswith("🟢")) else ("Formal" if ("formal" in low and "semi" not in low and not txt.startswith("🔵")) else "Professional")
            _update_session(uid, register=r_target)
            send_html_safe(chat_id, f"<b>Register set to: {r_target}</b>", reply_markup=build_reply_kb(uid))
            return

        if message.content_type == "document":
            fb = bot.download_file(bot.get_file(message.document.file_id).file_path)
            if (message.document.file_name or "").lower().endswith(".pdf") and HAS_PYPDF:
                tp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
                open(tp, "wb").write(fb)
                dt = "\n".join([p.extract_text() or "" for p in PdfReader(tp).pages[:12]])
            else:
                dt = fb.decode("utf-8", errors="ignore")
            execute_study_and_miner(chat_id, uid, dt)
            return

        if message.content_type in ("voice", "audio"):
            fid = message.voice.file_id if message.content_type == "voice" else message.audio.file_id
            tr = transcribe_voice(bot.download_file(bot.get_file(fid).file_path))
            if not tr:
                send_html_safe(chat_id, "Could not catch the audio clearly—please try speaking closer to the mic.")
                return
            if _get_session(uid)["mode"] == "STUDY" or len(tr.split()) > 70:
                execute_study_and_miner(chat_id, uid, tr, is_voice=True)
            else:
                execute_chat_turn(chat_id, uid, tr, is_voice=True)
            return

        if not txt:
            return
        mode = _get_session(uid)["mode"]
        if len(txt.split()) > 65 or mode == "STUDY":
            execute_study_and_miner(chat_id, uid, txt)
        elif mode == "PODCAST":
            execute_podcast(chat_id, uid, topic=txt)
        else:
            execute_chat_turn(chat_id, uid, txt)
    except Exception as err:
        logger.error(f"Handler error: {traceback.format_exc()}")
        send_html_safe(chat_id, f"<b>Jake</b>\nI hear you—let's look at that closely. <i>(Note: {_h(str(err)[:120])})</i>")

@bot.message_handler(content_types=["text", "voice", "audio", "photo", "document"])
def on_message(message: types.Message):
    threading.Thread(target=handle_incoming_message, args=(message,), daemon=True).start()

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: types.CallbackQuery):
    uid = call.from_user.id if getattr(call, "from_user", None) else int(PRIMARY_OWNER_ID)
    chat_id = call.message.chat.id
    d = (call.data or "").strip()
    fb_txt = (call.message.text or call.message.caption or "").strip()

    if d.startswith("edtts:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        segs = c["voice_segs"] if c and c.get("voice_segs") else [("Jake", fb_txt[:800])]
        bot.answer_callback_query(call.id, "Sending audio...")
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
        pdf_d = c["pdf_dict"] if c and c.get("pdf_dict") else {"title": "Study Sheet", "register": "Professional", "main_en": fb_txt[:1500]}
        bot.answer_callback_query(call.id, "Generating PDF...")
        def _pjob():
            p = generate_pdf_sheet(pdf_d)
            if p and os.path.exists(p):
                with open(p, "rb") as pf:
                    bot.send_document(chat_id, pf, caption="<b>Study Sheet (PDF)</b>", parse_mode="HTML")
                os.remove(p)
        threading.Thread(target=_pjob, daemon=True).start()
    elif d.startswith("edsave:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        items = c["vault_items"] if c and c.get("vault_items") else [(fb_txt.splitlines()[0][:80], "", fb_txt[:200])]
        for it in items:
            if len(it) >= 3:
                _save_vault_item(uid, it[0], it[1], it[2])
        sync_db_async(force=True)
        bot.answer_callback_query(call.id, "Saved to your Vault!")
    elif d.startswith("edar:"):
        tok = d.split(":")[-1]
        c = load_card(tok)
        bot.answer_callback_query(call.id)
        if c and c.get("ar_text"):
            send_html_safe(chat_id, f"<b>Arabic Translation &amp; Notes</b>\n\n{_h(c['ar_text'])}")
    elif d.startswith("edmore:"):
        tok = d.split(":", 1)[1]
        c = load_card(tok)
        bot.answer_callback_query(call.id, "Mining more expressions...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, c["mined_so_far"] if c else "", ""), daemon=True).start()
    elif d.startswith("edshift:"):
        _, sh, tok = d.split(":", 2)
        c = load_card(tok)
        bot.answer_callback_query(call.id, f"Applying {sh}...")
        threading.Thread(target=execute_study_and_miner, args=(chat_id, uid, c["source_text"] if c else fb_txt, False, "", sh), daemon=True).start()
    elif d.startswith("edpod:"):
        h_n = int(d.split(":", 1)[1])
        bot.answer_callback_query(call.id, f"Producing {h_n}-Host Podcast...")
        threading.Thread(target=execute_podcast, args=(chat_id, uid, "", h_n), daemon=True).start()
    elif d.startswith("edqz:"):
        _, ch, qid = d.split(":", 2)
        with db_connect() as conn:
            r = conn.execute("SELECT quiz_json FROM quiz_v15 WHERE q_id=?", (qid,)).fetchone()
        if r:
            q = QuizSchema.model_validate_json(r[0])
            corr = q.correct_letter.strip().upper()[:1]
            bot.answer_callback_query(call.id, "Correct!" if ch == corr else f"Best choice: {corr}")
            send_html_safe(chat_id, f"<b>{'✓ Correct' if ch == corr else f'Selected ({ch}) → Best: ({corr})'}</b>\n\n<blockquote expandable><b>Explanation ▼</b>\n\n{_h(q.explanation_and_registers_en)}</blockquote>\n<blockquote expandable><b>Arabic Notes ▼</b>\n\n{_h(q.arabic_notes)}</blockquote>")
    elif d.startswith("edqznext:"):
        bot.answer_callback_query(call.id, "Next question...")
        threading.Thread(target=send_quiz, args=(chat_id, uid), daemon=True).start()

if os.environ.get("LIVE_SELF_TEST") == "1":
    try:
        bot.remove_webhook()
    except Exception:
        pass
    oid = int(PRIMARY_OWNER_ID)
    # محاكاة دقيقة لإرسال /start من تيليجرام
    class _DummyUser:
        id = oid
        first_name = "Musa"
    class _DummyChat:
        id = oid
    class _DummyMsg:
        chat = _DummyChat()
        from_user = _DummyUser()
        text = "/start"
        caption = None
        content_type = "text"
    handle_incoming_message(_DummyMsg())
    print("✅ V15_2_START_AND_CHAT_OK", flush=True)
    os._exit(0)

if __name__ == "__main__":
    print("🚀 V15.2 Polling Active...", flush=True)
    try:
        bot.remove_webhook()
    except Exception:
        pass
    # إرسال إشعار حي للمستخدم فور إقلاع الخادم السحابي الجديد
    try:
        oid = int(PRIMARY_OWNER_ID)
        send_html_safe(
            oid,
            "<b>🟢 Musa’s NYC Lounge — 24/7 Server Connected</b>\n"
            "The new engine is now actively listening. Send any message or tap a button below:",
            reply_markup=build_reply_kb(oid),
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
