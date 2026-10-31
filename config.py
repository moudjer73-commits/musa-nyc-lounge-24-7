import os
import re
import tempfile


def _clean_env(keys: list[str], default: str = "") -> str:
    for k in keys:
        val = os.getenv(k, "").strip().strip('"').strip("'")
        if val:
            return val
    return default


TELEGRAM_BOT_TOKEN: str = _clean_env(["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"])
TELEGRAM_TOKEN: str = TELEGRAM_BOT_TOKEN
BOT_TOKEN: str = TELEGRAM_BOT_TOKEN

_raw_admin = _clean_env(["ADMIN_ID", "CHAT_ID"], "8874707150")
ADMIN_ID: int = int(_raw_admin) if _raw_admin.isdigit() else 8874707150

DB_PATH: str = (
    os.environ.get("SPOKEN_LAB_DB")
    or os.environ.get("CLUB_VAULT_DB")
    or os.environ.get("USER_DB_PATH")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "spoken_english_lab.db")
)

TEMP_AUDIO_DIR: str = os.path.join(tempfile.gettempdir(), "spoken_english_lab_audio")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)


def load_gemini_keys() -> list[str]:
    raw_pool: list[str] = []
    for k in ["GEMINI_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        val = os.getenv(k, "")
        if val:
            for part in re.split(r"[,;\s\n]+", val):
                clean = part.strip().strip('"').strip("'").strip("[]")
                if len(clean) >= 20 and clean not in raw_pool:
                    raw_pool.append(clean)
    return raw_pool


GEMINI_KEYS_LIST: list[str] = load_gemini_keys()
GEMINI_API_KEY: str = GEMINI_KEYS_LIST[0] if GEMINI_KEYS_LIST else ""

# 2026 Official Best-in-Class Gemini Model Tiers:
GEMINI_FLASH_TIER_CHAIN: list[str] = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]

GEMINI_PRO_TIER_CHAIN: list[str] = [
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

GEMINI_MODELS_CHAIN: list[str] = GEMINI_FLASH_TIER_CHAIN
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL") or GEMINI_FLASH_TIER_CHAIN[0]

DEFAULT_VOICE: str = "en-US-AndrewMultilingualNeural"

# --- V3.0 S2 Config Constants & Lane Helpers (S0 Contracts A11, O4) ---
ABSTAIN_THRESHOLD: float = float(os.getenv("ABSTAIN_THRESHOLD", "0.80"))

if "GEMINI_API_KEYS" not in globals():
    _raw_env_keys = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
    GEMINI_API_KEYS: list[str] = [k.strip() for k in _raw_env_keys.split(",") if k.strip()]

if "GEMINI_MODEL_CHAIN" not in globals():
    GEMINI_MODEL_CHAIN: list[str] = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
    ]


def get_labeled_gemini_keys() -> list[tuple[str, str]]:
    """Unlabeled keys share the 'default' project label (A11)."""
    raw_list = globals().get("GEMINI_API_KEYS") or globals().get("GEMINI_KEYS") or []
    if isinstance(raw_list, str):
        raw_list = [k.strip() for k in raw_list.split(",") if k.strip()]
    out: list[tuple[str, str]] = []
    for raw in raw_list:
        entry = str(raw).strip()
        if not entry:
            continue
        if ":" in entry and not entry.startswith("AIza"):
            proj, k = entry.split(":", 1)
            out.append((proj.strip() or "default", k.strip()))
        else:
            out.append(("default", entry))
    return out


def get_lane_models(call_class: str) -> list[str]:
    """Returns ordered preference list for each call_class; Analyst never starts with Pro (A11)."""
    chain_src = globals().get("GEMINI_MODEL_CHAIN") or ["gemini-2.5-flash", "gemini-2.0-flash"]
    chain = [m for m in chain_src if m]
    non_pro = [m for m in chain if "pro" not in m.lower()]
    pro_only = [m for m in chain if "pro" in m.lower()]
    if call_class == "analyst":
        if len(non_pro) >= 2:
            return [non_pro[1], non_pro[0]] + non_pro[2:] + pro_only
        return non_pro + pro_only
    return non_pro + pro_only
