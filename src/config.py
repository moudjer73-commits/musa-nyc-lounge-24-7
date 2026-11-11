import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("SEL_DB_PATH") or str(DATA_DIR / "spoken_english_lab.db"))
SNAPSHOT_PATH = Path(os.getenv("SEL_SNAPSHOT_PATH") or str(DATA_DIR / "state_snapshot.enc"))
SNAPSHOT_PASSPHRASE = os.getenv("SNAPSHOT_PASSPHRASE", "")


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


DEFAULT_DAILY_USER_LLM_BUDGET = _env_int("DAILY_USER_LLM_BUDGET", 80)
SNAPSHOT_PUSH_MINUTES = _env_int("SEL_SNAPSHOT_PUSH_MIN", 60)
ECHO_TWIN_ENABLED = _env("SEL_ECHO_TWIN", "1") != "0"


def resolve_telegram_token() -> str:
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "BOT_TOKEN", "TG_TOKEN"):
        val = os.getenv(k, "").strip()
        if val:
            return val
    return ""


def resolve_gemini_keys() -> list[str]:
    keys: list[str] = []
    for k in ("GEMINI_API_KEY", "GEMINI_KEYS", "GOOGLE_API_KEY"):
        raw = os.getenv(k, "").strip()
        for part in raw.replace("\n", ",").split(","):
            clean = part.strip()
            if clean and clean not in keys:
                keys.append(clean)
    return keys


def gemini_model_chain(prefer_deep_chat: bool = False) -> list[str]:
    raw = _env("GEMINI_MODEL_CHAIN", "gemini-2.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash-lite,gemini-3.8-flash")
    chain: list[str] = []
    for part in raw.split(","):
        m = part.strip()
        if m and m not in chain:
            chain.append(m)
    if prefer_deep_chat:
        full_models = [m for m in chain if "lite" not in m.lower()]
        lite_models = [m for m in chain if "lite" in m.lower()]
        return full_models + lite_models
    lite_models = [m for m in chain if "lite" in m.lower()]
    full_models = [m for m in chain if "lite" not in m.lower()]
    return lite_models + full_models
