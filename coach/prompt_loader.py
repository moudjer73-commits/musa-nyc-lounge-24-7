"""Loads prompt files, expands `{{> partial}}` includes and fills `{{variable}}` slots.

Rendering is single-pass: a learner message that happens to contain "{{x}}" can never be
re-interpreted as a template slot, because variables are substituted exactly once and the
result is never scanned again.

Mode files (mode_*.md) hold several prompts in one file, under headings like
"## PROMPT A: SETUP". `load_mode_prompt("register_shift", "EVALUATE")` returns just that
section.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import PersonaCard, SceneBible

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

_PARTIAL_RE = re.compile(r"\{\{>\s*([A-Za-z0-9_]+)\s*\}\}")
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_H2_RE = re.compile(r"^## .*$", re.M)
_PROMPT_HEAD_RE = re.compile(r"^## PROMPT ([A-Z]): (.+?)\s*$")


class MissingVariable(KeyError):
    """A `{{name}}` slot had no value supplied."""


@lru_cache(maxsize=None)
def load_text(name: str) -> str:
    path = PROMPT_DIR / name
    return path.read_text(encoding="utf-8")


def expand_partials(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return load_text(f"_partials/{m.group(1)}.md").strip()

    return _PARTIAL_RE.sub(repl, text)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def render(template: str, variables: dict[str, Any] | None = None) -> str:
    variables = variables or {}
    template = expand_partials(template)

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise MissingVariable(key)
        return _stringify(variables[key])

    return _VAR_RE.sub(repl, template)


def render_prompt(name: str, **variables: Any) -> str:
    """Render a whole prompt file, e.g. render_prompt("analyst_system.md")."""
    return render(load_text(name), variables)


def load_mode_prompt(mode: str, section: str, **variables: Any) -> str:
    """Return one prompt section of prompts/mode_<mode>.md, rendered.

    `section` is matched against the heading letter ("A") or the heading words
    ("SETUP", "EVALUATE", "REINFORCE", "INTERLOCUTOR TURN"), case-insensitively.
    """
    text = load_text(f"mode_{mode}.md")
    heads = list(_H2_RE.finditer(text))
    wanted = section.strip().lower()
    for i, h in enumerate(heads):
        m = _PROMPT_HEAD_RE.match(h.group(0))
        if not m:
            continue
        letter, title = m.group(1).lower(), m.group(2).lower()
        if wanted == letter or title.startswith(wanted) or wanted in title:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            body = text[h.end():end].strip()
            body = re.sub(r"(\n\s*---\s*)+$", "", body).strip()
            return render(body, variables)
    raise KeyError(f"no section {section!r} in mode_{mode}.md")


def mode_sections(mode: str) -> list[str]:
    text = load_text(f"mode_{mode}.md")
    out = []
    for h in _H2_RE.finditer(text):
        m = _PROMPT_HEAD_RE.match(h.group(0))
        if m:
            out.append(m.group(2))
    return out


# --------------------------------------------------------------------------------------
# Persona / scene views for the performer
# --------------------------------------------------------------------------------------

# The performer must not see what is scheduled for later: beats are handed over one at a time
# through the director brief, need slots become `opportunity_hint`s, and the register plan
# becomes the brief's `register`.
_HIDDEN_FROM_PERFORMER = {"beats", "need_slots", "max_turns", "register_plan"}


def persona_card_json(card: PersonaCard) -> str:
    return json.dumps(card.model_dump(), ensure_ascii=False, indent=2)


# --- voice-only view of a persona, for modes that are not the scene it was written for -------------

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_STOP = frozenset(
    "about after again also always because been before being both could does doing done each even ever from "
    "going gonna have having here into just kind like made make many more most much never only other over "
    "really same some still such than that their them then there these they thing this those very want were "
    "what when where which while will with would your yours you're don't didn't isn't it's that's i've i'm".split()
)


_CONTENT_FREE_MOVES = frozenset({"REACT", "BACKCHANNEL", "TEASE"})


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) >= 4 and w.lower() not in _STOP}


def _scene_words(card: PersonaCard, scene: SceneBible | None) -> set[str]:
    """Everything that ties a persona to ONE scene: its errand, the people and things in the room."""
    words = _content_words(card.role_in_scene) | _content_words(card.wants_in_scene)
    if scene is not None:
        parts = [scene.title, scene.situation, scene.setting.where, *scene.established_facts, *scene.open_threads]
        for person in scene.people:
            parts += [person.name, person.relation, *person.facts]
        for part in parts:
            words |= _content_words(part)
    # the persona's own name is theirs in every scene
    return words - {w.lower() for w in _WORD.findall(card.name)}


def voice_only_persona(card: PersonaCard, scene: SceneBible | None = None) -> PersonaCard:
    """Keep how this person talks; drop what they wanted in the scene they came from.

    A Persona Card mixes idiolect (voice, habits, fillers, never_does) with scene errands
    (`wants_in_scene`, `role_in_scene`, samples about the deck, the manager, the deadline).
    Handing the whole card to another mode would drag that errand along. Conservative on purpose:
      * speech samples survive only for the content-free moves (REACT, BACKCHANNEL, TEASE) and only when
        they share no content word with the scene; ASK/SHARE/BUILD/PUSHBACK lines are about the errand by nature
        ("any chance you'd have an hour this afternoon?") and share no keyword with it, so a word filter cannot catch them;
      * background sentences that share a content word with the scene are dropped (personality traits stay).
    Losing a sample costs a little tone anchoring; keeping one leaks the plot. The `voice` object
    (habits, fillers, humor) carries the idiolect either way.
    Pass `scene` whenever the card came from one; without it only the card's own errand fields are used.
    """
    bound = _scene_words(card, scene)

    def free(text: str) -> bool:
        return not (_content_words(text) & bound)

    background = " ".join(x for x in _SENTENCE.split(card.background.strip()) if x and free(x))
    return card.model_copy(
        update={
            "role_in_scene": "a thoughtful conversation partner",
            "wants_in_scene": "to understand how the learner reasons about the topic, and to keep them talking",
            "attitude_to_learner": "curious and respectful; treats the learner as a capable adult",
            "default_register": "analytical",
            "background": background,
            "speech_samples": [x for x in card.speech_samples if x.move.upper() in _CONTENT_FREE_MOVES and free(x.line)],
        }
    )


def scene_bible_json(scene: SceneBible) -> str:
    data = {k: v for k, v in scene.model_dump().items() if k not in _HIDDEN_FROM_PERFORMER}
    return json.dumps(data, ensure_ascii=False, indent=2)


def responder_system(card: PersonaCard, scene: SceneBible, *, cefr: str, english_variety: str = "General American") -> str:
    """The static part of the Responder prompt (persona + scene + registers + rules)."""
    return render_prompt(
        "responder_system.md",
        learner_cefr=cefr,
        english_variety=english_variety,
        persona_card=persona_card_json(card),
        scene_bible=scene_bible_json(scene),
    )


def responder_user(persona_name: str, transcript: list[dict[str, str]], brief_json: str) -> str:
    return render_prompt(
        "responder_turn_brief.md",
        persona_name=persona_name,
        transcript="\n".join(f"{t['speaker']}: {t['text']}" for t in transcript) or "(scene is just starting)",
        brief_json=brief_json,
    )


def analyst_system() -> str:
    return render_prompt("analyst_system.md")


def card_writer_system() -> str:
    return render_prompt("card_writer_system.md")


def debrief_writer_system() -> str:
    return render_prompt("debrief_writer_system.md")


def scene_generator_system() -> str:
    return render_prompt("scene_generator_system.md")
