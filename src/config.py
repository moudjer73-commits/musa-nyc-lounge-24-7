import os
from pathlib import Path
from typing import Literal

GoalType = Literal["work", "study", "travel", "social", "general"]
RegisterName = Literal["casual", "social", "professional", "intellectual"]

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("SEL_DB_PATH") or str(DATA_DIR / "spoken_english_lab.db"))
SNAPSHOT_PATH = Path(os.getenv("SEL_SNAPSHOT_PATH") or str(DATA_DIR / "state_snapshot.enc"))
SNAPSHOT_PASSPHRASE = os.getenv("SNAPSHOT_PASSPHRASE", "")


def _env(name: str, default: str) -> str:
    """os.getenv that treats an EMPTY value as unset (GitHub Actions passes unset vars as "")."""
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


PRIMARY_GEMINI_MODEL = _env("GEMINI_PRIMARY_MODEL", "gemini-3.8-flash")
FALLBACK_GEMINI_MODEL = _env("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
DEFAULT_DAILY_USER_LLM_BUDGET = _env_int("DAILY_USER_LLM_BUDGET", 80)
SNAPSHOT_PUSH_MINUTES = _env_int("SEL_SNAPSHOT_PUSH_MIN", 60)  # 0 disables the in-run git publish


def admin_user_ids() -> set[int]:
    ids: set[int] = set()
    for part in _env("ADMIN_USER_IDS", "").replace(";", ",").split(","):
        if part.strip().isdigit():
            ids.add(int(part.strip()))
    return ids


RECALIBRATION_INTERVAL_DAYS = 28
ONBOARDING_TURNS_PER_SESSION = 4
MIN_REGISTER_EXPOSURE = 10

STARTING_REGISTER_WEIGHTS: dict[GoalType, dict[RegisterName, int]] = {
    "work": {"casual": 20, "social": 15, "professional": 45, "intellectual": 20},
    "study": {"casual": 15, "social": 15, "professional": 20, "intellectual": 50},
    "travel": {"casual": 25, "social": 40, "professional": 15, "intellectual": 20},
    "social": {"casual": 30, "social": 40, "professional": 10, "intellectual": 20},
    "general": {"casual": 25, "social": 25, "professional": 25, "intellectual": 25},
}

REGISTER_KEYS: tuple[RegisterName, ...] = ("casual", "social", "professional", "intellectual")


def get_initial_register_weights(goal: GoalType) -> dict[str, int]:
    return dict(STARTING_REGISTER_WEIGHTS.get(goal, STARTING_REGISTER_WEIGHTS["general"]))


def normalize_register_weights(raw_weights: dict[str, int]) -> dict[str, int]:
    """Enforces minimum exposure floor (10%) per register and normalizes sum to 100%."""
    clamped = {k: max(MIN_REGISTER_EXPOSURE, int(raw_weights.get(k, 25))) for k in REGISTER_KEYS}
    Available_pool = 100 - (MIN_REGISTER_EXPOSURE * len(REGISTER_KEYS))
    excess = {k: clamped[k] - MIN_REGISTER_EXPOSURE for k in REGISTER_KEYS}
    total_excess = sum(excess.values())

    if total_excess == 0:
        return {k: 25 for k in REGISTER_KEYS}

    normalized = {
        k: MIN_REGISTER_EXPOSURE + int(round((excess[k] / total_excess) * Available_pool))
        for k in REGISTER_KEYS
    }
    diff = 100 - sum(normalized.values())
    if diff != 0:
        target_key = max(REGISTER_KEYS, key=lambda k: normalized[k])
        normalized[target_key] += diff
    return normalized


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


def gemini_model_chain() -> list[str]:
    """Ordered model fallback chain. Override with GEMINI_MODEL_CHAIN="m1,m2,m3" (no code change needed
    when Google retires a model)."""
    raw = os.getenv("GEMINI_MODEL_CHAIN", "")
    names = [p.strip() for p in raw.split(",") if p.strip()]
    if not names:
        # Legacy 2.5 models stay as a last resort: Google still serves them to older API projects.
        names = [PRIMARY_GEMINI_MODEL, FALLBACK_GEMINI_MODEL, "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    chain: list[str] = []
    for n in names:
        if n not in chain:
            chain.append(n)
    return chain

