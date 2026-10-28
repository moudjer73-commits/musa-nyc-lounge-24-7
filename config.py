import os
import re

def _clean_env(keys, default=""):
    for k in keys:
        val = os.getenv(k, "").strip().strip('"').strip("'")
        if val:
            return val
    return default

TELEGRAM_BOT_TOKEN = _clean_env(["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"])
TELEGRAM_TOKEN = TELEGRAM_BOT_TOKEN
BOT_TOKEN = TELEGRAM_BOT_TOKEN

_raw_admin = _clean_env(["ADMIN_ID", "CHAT_ID"], "8874707150")
ADMIN_ID = int(_raw_admin) if _raw_admin.isdigit() else 8874707150

DB_PATH = (
    os.environ.get("CLUB_VAULT_DB")
    or os.environ.get("USER_DB_PATH")
    or os.environ.get("DB_PATH")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "club_vault.db")
)

def _load_gemini_keys():
    raw_pool = []
    for k in ["GEMINI_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        val = os.getenv(k, "")
        if val:
            for part in re.split(r"[,;\s\n]+", val):
                clean = part.strip().strip('"').strip("'").strip("[]")
                if len(clean) >= 20 and clean not in raw_pool:
                    raw_pool.append(clean)
    return raw_pool

GEMINI_KEYS_LIST = _load_gemini_keys()
GEMINI_API_KEY = GEMINI_KEYS_LIST[0] if GEMINI_KEYS_LIST else ""
GEMINI_MODELS_CHAIN = [
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or GEMINI_MODELS_CHAIN[0]
