from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coach.db import Database  # noqa: E402
from coach.models import Finding, PersonaScene  # noqa: E402
from coach.state import LearnerState  # noqa: E402


@pytest.fixture
def example_scene() -> PersonaScene:
    data = json.loads((ROOT / "examples" / "persona_scene_example.json").read_text(encoding="utf-8"))
    return PersonaScene.model_validate(data)


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


def mk_finding(**over) -> Finding:
    base = dict(
        span="take a decision",
        category="collocation",
        l1_transfer=True,
        l1_pattern="COLLOC_LIGHT_VERB",
        l1_source_ar="اتخذ قرارًا",
        severity=2,
        teachable_now=True,
        pattern_key="collocation.COLLOC_LIGHT_VERB.take_decision",
        listener_effect="understood, but sounds translated",
        why_en="English pairs decision with make.",
        ar_note="",
        recast_form="make the call",
        recast_meaning="to decide something",
        alternatives=[
            {"text": "I'll make a decision tomorrow.", "register": "neutral", "nuance": "standard", "best_for": "anyone"}
        ],
    )
    base.update(over)
    return Finding(**base)


def mk_state(**over) -> LearnerState:
    base = dict(user_id=1, cefr="B2", turn_index=6, turns_since_card=99, baseline_words=12.0, recent_words=[12, 11, 13])
    base.update(over)
    return LearnerState(**base)


class Clock:
    """Strictly increasing fake clock: one minute per call."""

    def __init__(self, start: float = 1_800_000_000.0, step: float = 60.0) -> None:
        self._it = itertools.count()
        self.start, self.step = start, step

    def __call__(self) -> float:
        return self.start + next(self._it) * self.step


# ------------------------------------------------------------------ fake responder

OPENERS = ["Honestly", "Okay", "Right", "So", "Yeah", "Look", "Hm"]


def extract_brief(user_prompt: str) -> dict:
    m = re.search(r"<director_brief>\s*(\{.*\})\s*</director_brief>", user_prompt, re.S)
    assert m, "director brief not found in the responder prompt"
    return json.loads(m.group(1))


def fake_responder(call):
    """A scripted 'character' that obeys the director brief, so pipeline tests are deterministic."""
    b = extract_brief(call.user)
    avoid = {w.lower() for w in b["avoid_openers"]}
    opener = next(o for o in OPENERS if o.lower() not in avoid)
    move = b["dialogue_move"]
    parts: list[str] = []
    if move == "BACKCHANNEL":
        parts = [f"{opener}. Go on."]
    elif move == "REACT":
        parts = [f"{opener}, same."]
    elif move == "CLARIFY":
        parts = [f"{opener}, who was late?"]
    elif move == "ASK":
        parts = [f"{opener}, which deck?"] if not b["recast"] else [f"{opener}, {b['recast']['natural_form']}. Which deck?"]
    else:
        parts = [f"{opener}, that is how it goes."] if not b["recast"] else [f"{opener}, {b['recast']['natural_form']}."]
    out = {
        "bubbles": [" ".join(parts)],
        "recast_applied": [b["recast"]["id"]] if b["recast"] else [],
        "opportunities_created": [b["stealth_targets"][0]["id"]] if b["stealth_targets"] else [],
        "scene_beat_used": b["scene_beat"]["id"] if b["scene_beat"] else None,
    }
    return out
