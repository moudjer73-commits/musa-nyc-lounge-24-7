import os
from pathlib import Path
from typing import Literal

GoalType = Literal["work", "study", "travel", "social", "general"]
RegisterName = Literal["casual", "social", "professional", "intellectual"]

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("SEL_DB_PATH", str(DATA_DIR / "spoken_english_lab.db")))
SNAPSHOT_PATH = Path(os.getenv("SEL_SNAPSHOT_PATH", str(DATA_DIR / "state_snapshot.enc")))
SNAPSHOT_PASSPHRASE = os.getenv("SNAPSHOT_PASSPHRASE", "")

PRIMARY_GEMINI_MODEL = os.getenv("GEMINI_PRIMARY_MODEL", "gemini-2.5-flash")
FALLBACK_GEMINI_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")
DEFAULT_DAILY_USER_LLM_BUDGET = int(os.getenv("DAILY_USER_LLM_BUDGET", "80"))

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
