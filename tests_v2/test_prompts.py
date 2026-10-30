"""The prompts and the schemas must not drift apart."""

from __future__ import annotations

import json
import re

import pytest

from coach import prompt_loader as pl
from coach.checks import CARD_MAX_VISIBLE, DEBRIEF_MAX_VISIBLE, validate_telegram_html
from coach.models import (
    Analysis, DilemmaEval, DilemmaSetup, Finding, HtmlOut, InterlocutorReply, PersonaCard, RegisterShiftEval,
    RegisterShiftSetup, ResponderReply, SceneBible, ThinkAloudEval, ThinkAloudSetup, WorkoutEval, WorkoutSetup,
)

ALL_PROMPTS = [
    "responder_system.md", "responder_turn_brief.md", "analyst_system.md", "card_writer_system.md",
    "debrief_writer_system.md", "scene_generator_system.md",
    "mode_register_shift.md", "mode_pragmatic_dilemma.md", "mode_think_aloud.md", "mode_phrase_workout.md",
]


def fenced_json_blocks(text: str, tag: str = "json") -> list[dict]:
    out = []
    for m in re.finditer(r"```" + re.escape(tag) + r"\s*\n(.*?)```", text, re.S):
        out.append(json.loads(m.group(1)))
    return out


# ------------------------------------------------------------------------ rendering


@pytest.mark.parametrize("name", ALL_PROMPTS)
def test_every_prompt_exists_and_has_no_unknown_slots(name):
    text = pl.load_text(name)
    expanded = pl.expand_partials(text)
    slots = set(pl._VAR_RE.findall(expanded))
    allowed = {"learner_cefr", "english_variety", "persona_card", "scene_bible", "persona_name", "transcript",
               "brief_json"}
    assert slots <= allowed, f"{name} has unexpected slots {slots - allowed}"
    assert "{{>" not in expanded


def test_responder_renders_with_persona_and_scene(example_scene):
    p, s = example_scene.persona, example_scene.scene
    out = pl.responder_system(p, s, cefr="B2")
    assert "{{" not in out
    assert "Priya" in out and "Henderson" in out
    assert "THE SIX REGISTERS" in out          # partial expanded
    assert "B2" in out


def test_performer_does_not_see_future_beats_or_need_slots(example_scene):
    out = pl.responder_system(example_scene.persona, example_scene.scene, cefr="C1")
    assert "Tom could help with the numbers section" not in out     # beat b1 text
    assert "t_swamped" not in out                                    # need slot
    assert "max_turns" not in out
    assert "established_facts" in out and "opening_intent" in out


def test_single_pass_render_does_not_reinterpret_user_text():
    rendered = pl.render("Hello {{name}}!", {"name": "{{secret}}"})
    assert rendered == "Hello {{secret}}!"


def test_missing_variable_is_loud():
    with pytest.raises(pl.MissingVariable):
        pl.render("{{a}} {{b}}", {"a": 1})


def test_analyst_prompt_contains_all_partials():
    out = pl.analyst_system()
    for marker in ("L1 ARABIC TRANSFER", "THE FINDING OBJECT", "RULES FOR THE ARABIC COACHING NOTE", "COLLOC_LIGHT_VERB"):
        assert marker in out
    assert "{{" not in out


@pytest.mark.parametrize(
    "mode,sections",
    [
        ("register_shift", ["SETUP", "EVALUATE"]),
        ("pragmatic_dilemma", ["SETUP", "EVALUATE"]),
        ("think_aloud", ["SETUP", "INTERLOCUTOR", "EVALUATE"]),
        ("phrase_workout", ["SETUP", "EVALUATE", "REINFORCE"]),
    ],
)
def test_mode_sections_load_separately(mode, sections, example_scene):
    texts = {}
    for sec in sections:
        kwargs = {}
        if (mode, sec) == ("think_aloud", "INTERLOCUTOR"):
            kwargs = {"persona_name": "Priya", "persona_card": pl.voice_only_persona_json(example_scene.persona)}
        texts[sec] = pl.load_mode_prompt(mode, sec, **kwargs)
        assert texts[sec].strip() and "{{" not in texts[sec]
    assert len(set(texts.values())) == len(texts)     # sections are different prompts
    if mode == "phrase_workout":
        assert "HOW THIS CONNECTS TO MASTERY" not in texts["REINFORCE"]   # developer notes are not prompt text


def test_mode_sections_listing():
    assert pl.mode_sections("think_aloud") == ["SETUP", "INTERLOCUTOR TURN", "EVALUATE"]


# ------------------------------------------------------------------------ examples validate


def test_scene_example_validates(example_scene):
    assert example_scene.persona.name == "Priya"
    assert len(example_scene.persona.speech_samples) == 6
    assert example_scene.scene.max_turns == 14


def test_analyst_examples_validate_against_the_schema():
    blocks = fenced_json_blocks(pl.load_text("analyst_system.md"), "json analysis-example")
    assert len(blocks) >= 5
    for b in blocks:
        a = Analysis.model_validate(b)
        for f in a.findings:
            assert f.recast_form is None or f.recast_meaning, "every recast form needs a recast_meaning"


def test_responder_examples_validate():
    text = pl.load_text("responder_system.md")
    blocks = [b for b in fenced_json_blocks(text) if "bubbles" in b]
    assert len(blocks) >= 4
    for b in blocks:
        ResponderReply.model_validate(b)


def test_card_and_debrief_examples_are_valid_telegram_html():
    for name, limit in (("card_writer_system.md", CARD_MAX_VISIBLE), ("debrief_writer_system.md", DEBRIEF_MAX_VISIBLE)):
        blocks = [b for b in fenced_json_blocks(pl.load_text(name)) if "html" in b]
        assert blocks
        for b in blocks:
            HtmlOut.model_validate(b)
            problems = validate_telegram_html(b["html"], max_visible=limit)
            assert problems == [], (name, problems)


def _mode_blocks(mode: str):
    return fenced_json_blocks(pl.load_text(f"mode_{mode}.md"))


def test_mode_setup_schemas_accept_the_documented_shapes():
    # The prompt files show output skeletons; fill in the elided parts and check they validate.
    rs = next(b for b in _mode_blocks("register_shift") if "listeners" in b)
    RegisterShiftSetup.model_validate(rs)
    pd = next(b for b in _mode_blocks("pragmatic_dilemma") if "three_options" in b)
    DilemmaSetup.model_validate(pd)
    ta = next(b for b in _mode_blocks("think_aloud") if "counterpoints" in b)
    ThinkAloudSetup.model_validate(ta)
    pw = next(b for b in _mode_blocks("phrase_workout") if "acceptable_forms" in b and "context_id" in b)
    WorkoutSetup.model_validate(pw)


def test_mode_eval_schemas_accept_the_documented_shapes():
    rs = next(b for b in _mode_blocks("register_shift") if "per_version" in b)
    rs["per_version"][1]["finding"] = None     # the file elides the Finding object
    RegisterShiftEval.model_validate(rs)
    DilemmaEval.model_validate(next(b for b in _mode_blocks("pragmatic_dilemma") if "achieves_goal" in b))
    ThinkAloudEval.model_validate(next(b for b in _mode_blocks("think_aloud") if "moves_observed" in b))
    WorkoutEval.model_validate(next(b for b in _mode_blocks("phrase_workout") if "schedule_hint" in b))
    InterlocutorReply.model_validate({"bubbles": ["What would change your mind?"]})


# ------------------------------------------------------------------------ no drift


def _field_names(model) -> set[str]:
    return set(model.model_fields)


@pytest.mark.parametrize(
    "model,prompt",
    [
        (Finding, "analyst_system.md"),
        (Analysis, "analyst_system.md"),
        (ResponderReply, "responder_system.md"),
        (PersonaCard, "scene_generator_system.md"),
        (SceneBible, "scene_generator_system.md"),
        (RegisterShiftSetup, "mode_register_shift.md"),
        (RegisterShiftEval, "mode_register_shift.md"),
        (DilemmaSetup, "mode_pragmatic_dilemma.md"),
        (DilemmaEval, "mode_pragmatic_dilemma.md"),
        (ThinkAloudSetup, "mode_think_aloud.md"),
        (ThinkAloudEval, "mode_think_aloud.md"),
        (WorkoutSetup, "mode_phrase_workout.md"),
        (WorkoutEval, "mode_phrase_workout.md"),
    ],
)
def test_every_schema_field_is_documented_in_its_prompt(model, prompt):
    text = pl.expand_partials(pl.load_text(prompt))
    missing = [f for f in _field_names(model) if f"`{f}`" not in text and f'"{f}"' not in text]
    assert not missing, f"{model.__name__} fields not mentioned in {prompt}: {missing}"


def test_director_brief_fields_are_documented_in_the_responder_prompt():
    from coach.models import DirectorBrief

    text = pl.load_text("responder_system.md")
    missing = [f for f in DirectorBrief.model_fields if f"`{f}`" not in text and f != "clarification_focus"]
    assert not missing, missing
    assert "clarification_focus" in text


def test_think_aloud_uses_voice_only_persona(example_scene):
    from coach.prompt_loader import voice_only_persona_json
    data = json.loads(voice_only_persona_json(example_scene.persona))
    assert "name" in data and "voice" in data
    assert "role_in_scene" not in data
    assert "wants_in_scene" not in data
    assert "attitude_to_learner" in data


def test_analyst_does_not_treat_absent_fillers_as_an_error():
    taxonomy = pl.load_text("_partials/l1_taxonomy.md")
    assert "Do not flag a learner merely for using few or no fillers" in taxonomy
    assert 'Almost no "so, well, I mean' not in taxonomy


def test_phrase_vault_register_tags_include_all_alternatives():
    from coach.pipeline import expression_from_finding
    from coach.models import Alternative
    finding = Finding(
        span="I disagree",
        category="pragmatic",
        severity=2,
        pattern_key="pragmatic.DIRECTNESS.test",
        recast_form="I see your point, but...",
        recast_meaning="polite disagreement",
        alternatives=[
            Alternative(text="I see your point, but...", register="diplomatic"),
            Alternative(text="I get that, but...", register="casual"),
            Alternative(text="I understand the concern; however...", register="executive"),
        ],
    )
    expr = expression_from_finding(finding)
    assert expr.register_tags == ["diplomatic", "casual", "executive"]


def test_phrase_vault_form_scopes_filter_by_register_compatibility():
    from coach.pipeline import expression_from_finding
    from coach.models import Alternative
    finding = Finding(
        span="No, I can't",
        category="pragmatic",
        severity=2,
        pattern_key="pragmatic.DIRECTNESS.bare_refusal",
        recast_form="my plate's pretty full",
        recast_meaning="too busy to take on more",
        alternatives=[
            Alternative(text="my plate's pretty full", register="diplomatic", nuance="warm", best_for="a manager"),
            Alternative(text="I'm at capacity this week", register="executive", nuance="formal", best_for="senior leadership"),
            Alternative(text="Honestly, I'm swamped", register="casual", nuance="relaxed", best_for="close friends"),
        ],
    )
    expr = expression_from_finding(finding)
    assert len(expr.form_scopes) == 3
    exec_forms = expr.compatible_forms_for("executive")
    assert "I'm at capacity this week" in exec_forms
    assert "Honestly, I'm swamped" not in exec_forms
    casual_forms = expr.compatible_forms_for("casual")
    assert "Honestly, I'm swamped" in casual_forms
    assert "I'm at capacity this week" not in casual_forms
