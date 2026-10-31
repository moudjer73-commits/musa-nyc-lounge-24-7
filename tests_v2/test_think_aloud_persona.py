"""Think-Aloud borrows a persona's VOICE, never the errand it was written for."""

from __future__ import annotations

import asyncio
import re

import coach.prompt_loader as pl
from coach.llm import ScriptedLLM
from coach.modes import ModeRunner
from coach.models import PersonaCard, SpeechSample

SCENE_LEAKS = ["Henderson", "Dan", "Tom", "deck", "dumping", "take over"]   # not "favor": the prompt itself says "no favor to ask"


def test_errand_fields_are_replaced_and_voice_is_kept(example_scene):
    original = example_scene.persona
    v = pl.voice_only_persona(original, example_scene.scene)
    assert v.name == "Priya" and v.voice == original.voice and v.never_does == original.never_does
    assert v.default_register == "analytical"
    assert "Henderson" not in v.wants_in_scene and "coworker" not in v.role_in_scene
    assert original.wants_in_scene.startswith("Find someone")           # the source card is not mutated
    assert pl.voice_only_persona(v, example_scene.scene) == v           # idempotent


def test_only_content_free_moves_survive_even_when_they_share_no_keyword(example_scene):
    v = pl.voice_only_persona(example_scene.persona, example_scene.scene)
    assert {s.move for s in v.speech_samples} <= {"REACT", "BACKCHANNEL", "TEASE"}
    lines = " ".join(s.line for s in v.speech_samples)
    assert "afternoon" not in lines and "Friday" not in lines            # errand lines with no scene keyword

    card = example_scene.persona.model_copy(update={"speech_samples": [
        SpeechSample(move="REACT", line="Ugh, that deck again."),         # content-free move, but names the scene's object
        SpeechSample(move="TEASE", line="Well, aren't we fancy."),
        SpeechSample(move="ASK", line="Any chance you could help me out?"),
    ]})
    assert [s.line for s in pl.voice_only_persona(card, example_scene.scene).speech_samples] == ["Well, aren't we fancy."]


def test_the_rendered_interlocutor_prompt_carries_no_scene_errand(example_scene):
    for scene in (example_scene.scene, None):
        card = pl.voice_only_persona(example_scene.persona, scene)
        text = pl.load_mode_prompt("think_aloud", "INTERLOCUTOR", persona_name=card.name, persona_card=pl.persona_card_json(card))
        assert "Priya" in text
        for word in SCENE_LEAKS:
            assert not re.search(rf"\b{re.escape(word)}\b", text), (word, scene is None)


def test_mode_runner_projects_the_persona_before_prompting(example_scene):
    llm = ScriptedLLM()
    llm.push("think_aloud.interlocutor", {"bubbles": ["Why that one, though?"]})
    asyncio.run(
        ModeRunner(llm).run("think_aloud", "INTERLOCUTOR", {"think_move": "probe"},
                            persona=example_scene.persona, scene=example_scene.scene)
    )
    system = llm.calls[0].system
    assert "Priya" in system and "Henderson" not in system and "dumping" not in system
    assert "no errand" in system


def test_a_card_without_samples_or_background_still_works():
    bare = PersonaCard(name="Sam", role_in_scene="a barista", wants_in_scene="to close early")
    v = pl.voice_only_persona(bare)
    assert v.name == "Sam" and v.speech_samples == [] and v.background == ""


def test_the_responder_prompt_says_the_plan_is_hidden(example_scene):
    system = pl.responder_system(example_scene.persona, example_scene.scene, cefr="B2")
    assert "You see the stable part of the scene only" in system
    assert "Do not invent the next development" in system
