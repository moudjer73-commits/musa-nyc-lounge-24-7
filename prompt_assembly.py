"""Prompt assembly + analyst output guard for Spoken English Lab v3/v4."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

def _resolve_prompt_path(filename: str) -> Path:
    p_prompts = Path(__file__).parent / "prompts" / filename
    if p_prompts.exists():
        return p_prompts
    return Path(__file__).with_name(filename)

PARTNER_PROMPT_PATH = _resolve_prompt_path("alex_partner_v3_system.md")
ANALYST_PROMPT_PATH = _resolve_prompt_path("analyst_v3_system.md")

ROOMS = ("casual", "diplomatic", "executive", "analytical")
DIRECTNESS = ("soft", "balanced", "direct")
FLAVORS = ("neutral", "warm", "witty")
MODES = ("open", "reply")

AXES = {
    "too_written", "too_casual", "over_hedged",
    "under_committed", "corporate_filler", "missing_move",
}
COSTS = {"credibility", "relationship", "clarity"}
MIN_D_CONFIDENCE = 0.75

_BLOCK_RE = re.compile(
    r"<!-- BEGIN:(?P<key>[A-Za-z_]+(?::[A-Za-z_]+)?) -->\n(?P<body>.*?)\n<!-- END:(?P=key) -->",
    re.S,
)


@lru_cache(maxsize=1)
def _partner_blocks() -> dict[str, str]:
    path = _resolve_prompt_path("alex_partner_v3_system.md")
    text = path.read_text(encoding="utf-8")
    blocks = {m["key"]: m["body"].strip() for m in _BLOCK_RE.finditer(text)}
    needed = (
        ["CORE", "SESSION"]
        + [f"ROOM:{r}" for r in ROOMS]
        + [f"DIRECTNESS:{d}" for d in DIRECTNESS]
        + [f"FLAVOR:{f}" for f in FLAVORS]
        + [f"MODE:{m}" for m in MODES]
    )
    missing = [k for k in needed if k not in blocks]
    if missing:
        raise ValueError(f"Partner prompt is missing blocks: {missing}")
    return blocks


def normalize_3d_config(
    register_profile: str = "casual",
    directness: str = "balanced",
    flavor: str | None = None,
) -> tuple[str, str, str]:
    """Map legacy 6-register + directness or 3D settings into (room, directness, flavor)."""
    reg_clean = (register_profile or "casual").strip().lower()
    dir_clean = (directness or "balanced").strip().lower()
    if dir_clean not in DIRECTNESS:
        dir_clean = "balanced"

    if reg_clean in ("warm", "witty"):
        room = "casual"
        resolved_flavor = reg_clean if not flavor or flavor == "neutral" else flavor.strip().lower()
    elif reg_clean in ROOMS:
        room = reg_clean
        resolved_flavor = (flavor or "neutral").strip().lower()
    else:
        room = "casual"
        resolved_flavor = (flavor or "neutral").strip().lower()

    if resolved_flavor not in FLAVORS:
        resolved_flavor = "neutral"
    return room, dir_clean, resolved_flavor


def build_partner_prompt(
    *, room: str, directness: str, flavor: str, mode: str = "reply", topic_hint: str | None = None
) -> str:
    room, directness, flavor = normalize_3d_config(room, directness, flavor)
    if mode not in MODES:
        mode = "reply"
    b = _partner_blocks()
    session = b["SESSION"]
    for key, value in {
        "room": room, "directness": directness, "flavor": flavor,
        "topic_hint": (topic_hint or "none").strip() or "none",
    }.items():
        session = session.replace("{{" + key + "}}", value)
    return "\n\n".join([
        f"<core>\n{b['CORE']}\n</core>",
        f"<room>\n{b['ROOM:' + room]}\n</room>",
        f"<directness>\n{b['DIRECTNESS:' + directness]}\n</directness>",
        f"<flavor>\n{b['FLAVOR:' + flavor]}\n</flavor>",
        f"<mode>\n{b['MODE:' + mode]}\n</mode>",
        f"<session>\n{session}\n</session>",
    ])


def load_analyst_prompt() -> str:
    return _resolve_prompt_path("analyst_v3_system.md").read_text(encoding="utf-8")


def build_analyst_user_message(
    *, room: str, directness: str, flavor: str, learner_turn: str,
    alex_last_turn: str = "", recent_turns: list[str] | None = None,
    recent_pattern_tags: list[str] | None = None,
) -> str:
    room, directness, flavor = normalize_3d_config(room, directness, flavor)
    payload = {
        "room": room, "directness": directness, "flavor": flavor,
        "learner_turn": learner_turn, "alex_last_turn": alex_last_turn,
        "recent_turns": recent_turns or [],
        "recent_pattern_tags": recent_pattern_tags or [],
        "learner_l1": "ar",
    }
    return json.dumps(payload, ensure_ascii=False)


def validate_analyst_output(obj: object) -> dict:
    items = obj.get("diagnostics") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return {"diagnostics": []}
    clean: list[dict] = []
    seen_d = False
    for d in items:
        if not isinstance(d, dict):
            continue
        ups = d.get("upgrades")
        if (
            not isinstance(d.get("span"), str) or not d["span"].strip()
            or not isinstance(d.get("why"), str) or not d["why"].strip()
            or not isinstance(ups, list) or not 1 <= len(ups) <= 2
            or not all(isinstance(u, str) and u.strip() for u in ups)
        ):
            continue
        cat = d.get("category")
        if cat == "D":
            conf = d.get("confidence")
            if seen_d:
                continue
            if d.get("mismatch_axis") not in AXES or d.get("cost") not in COSTS:
                continue
            if not isinstance(conf, (int, float)) or conf < MIN_D_CONFIDENCE:
                continue
            seen_d = True
        elif cat in ("A", "B"):
            d["mismatch_axis"] = None
            d["cost"] = None
        else:
            continue
        clean.append(d)
    clean.sort(key=lambda x: x["category"] == "D")
    return {"diagnostics": clean[:2]}


def parse_analyst_output(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return validate_analyst_output(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        return {"diagnostics": []}
