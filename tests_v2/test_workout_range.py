"""Phrase Workout and stealth targets must stay inside the range in which an expression is natural."""

from __future__ import annotations

import asyncio

from coach.db import Database
from coach.director import pick_targets
from coach.llm import ScriptedLLM
from coach.models import Expression, Variant, WorkoutSetup
from coach.modes import ModeRunner, run_workout_setup, workout_expression_payload, workout_setup_problems
from coach.pipeline import expression_from_finding

from conftest import mk_finding

NOW = 1_800_000_000.0

ALTS = [
    {"text": "I'd love to, but my plate's pretty full right now.", "register": "diplomatic", "nuance": "warm", "best_for": "a manager"},
    {"text": "Honestly, I'm swamped right now.", "register": "casual", "nuance": "relaxed", "best_for": "a peer"},
    {"text": "I'm at capacity this week.", "register": "executive", "nuance": "direct", "best_for": "a manager"},
]


def finding(**kw):
    return mk_finding(recast_form="my plate's pretty full", recast_meaning="too busy", alternatives=ALTS, **kw)


def setup(register="casual", situation="Your roommate asks you to cover her dishes again.", forms=("I'm swamped",)) -> dict:
    return {
        "context_id": "c1", "situation": situation, "response_slot": "You say:", "support": None,
        "acceptable_forms": list(forms), "near_misses": [], "register": register,
    }


def test_range_of_a_form_comes_from_alternatives_that_contain_it_and_from_the_scene_only():
    e = expression_from_finding(finding(), scene_register="casual")
    assert e.form == "my plate's pretty full"
    assert e.register_tags == ["diplomatic", "casual"]               # NOT executive: that alternative is another phrase
    assert {v.form: v.register_tags for v in e.variants} == {
        "Honestly, I'm swamped right now.": ["casual"], "I'm at capacity this week.": ["executive"],
    }
    assert "I'm at capacity this week." in e.acceptable_forms          # still recognised when the learner uses it
    assert expression_from_finding(finding()).register_tags == ["diplomatic"]   # no scene evidence: only what contains it


def test_upsert_grows_ranges_from_evidence_and_keeps_variants_separate():
    db = Database(":memory:")
    db.get_or_create_user(1, NOW)
    first = expression_from_finding(finding(), scene_register="casual")
    db.upsert_expression(1, first, NOW)
    again = expression_from_finding(finding(), scene_register="executive")      # shown again in a formal scene
    db.upsert_expression(1, again, NOW + 1)
    got = db.get_expression(1, first.id)
    assert got.register_tags == ["diplomatic", "casual", "executive"]
    swamped = next(v for v in got.variants if "swamped" in v.form)
    assert swamped.register_tags == ["casual"]                            # unchanged by the other scene
    assert len(got.variants) == 2


def test_setup_is_rejected_when_the_register_is_outside_the_range():
    e = Expression(id="x", form="my plate's pretty full", register_tags=["diplomatic"],
                   variants=[Variant(form="I'm swamped", register_tags=["casual"])])
    assert workout_setup_problems(WorkoutSetup.model_validate(setup("executive", forms=())), e) == []   # same formality
    bad = workout_setup_problems(WorkoutSetup.model_validate(setup("casual", forms=())), e)
    assert any("outside this expression's range" in p for p in bad)
    # a variant is only accepted where ITS range allows it
    bad = workout_setup_problems(WorkoutSetup.model_validate(setup("executive", forms=("I'm swamped",))), e)
    assert any("not natural" in p for p in bad)
    assert workout_setup_problems(WorkoutSetup.model_validate(setup("casual", forms=("I'm swamped",))), e.model_copy(update={"register_tags": ["casual"]})) == []


def test_unknown_or_neutral_range_excludes_nothing_and_leaks_are_caught():
    open_ended = Expression(id="x", form="make the call", register_tags=[])
    assert workout_setup_problems(WorkoutSetup.model_validate(setup("executive", forms=())), open_ended) == []
    leak = workout_setup_problems(WorkoutSetup.model_validate(setup(situation="Ask him to make the call today.", forms=())), open_ended)
    assert any("contains the expression" in p for p in leak)


def test_runner_retries_once_with_fix_notes_and_reports_what_remains():
    e = Expression(id="x", form="my plate's pretty full", register_tags=["diplomatic"])
    llm = ScriptedLLM()
    llm.push("phrase_workout.setup", setup("casual", forms=()), setup("diplomatic", situation="Your manager asks you to take over the Henderson deck.", forms=()))
    out, problems = asyncio.run(run_workout_setup(ModeRunner(llm), e, previous_contexts=["c0"]))
    assert problems == [] and out.register == "diplomatic"
    first, second = llm.calls_for("phrase_workout.setup")
    assert '"fix_notes"' not in first.user and "outside this expression's range" in second.user
    assert '"register_tags"' in first.user and "fix_notes" in second.system

    llm.push("phrase_workout.setup", setup("casual", forms=()), setup("casual", forms=()))
    out, problems = asyncio.run(run_workout_setup(ModeRunner(llm), e))
    assert problems and out.register == "casual"                           # surfaced, never hidden


def test_reinforce_uses_the_same_guard():
    e = Expression(id="x", form="my plate's pretty full", register_tags=["diplomatic"])
    llm = ScriptedLLM()
    llm.push("phrase_workout.reinforce", {**setup("diplomatic", situation="Your boss wants one more favour.", forms=()), "bridge": "It comes back in a new place."})
    out, problems = asyncio.run(run_workout_setup(ModeRunner(llm), e, section="REINFORCE", extra={"failed_context": "c1", "learner_answer": "x"}))
    assert problems == [] and out.bridge


def test_payload_exposes_range_and_variants():
    e = expression_from_finding(finding(), scene_register="casual")
    p = workout_expression_payload(e)
    assert p["register_tags"] == ["diplomatic", "casual"] and len(p["variants"]) == 2


# ------------------------------------------------------------------ stealth targets


def test_a_target_is_never_created_in_a_scene_where_it_would_be_out_of_place(example_scene):
    formal_only = Expression(id="f", form="I'd lean toward the second option", register_tags=["diplomatic"], opportunity_hint="a choice")
    casual_ok = Expression(id="c", form="no big deal", register_tags=["casual"], opportunity_hint="an apology")
    anywhere = Expression(id="a", form="sounds good", register_tags=[], opportunity_hint="an offer")
    got = pick_targets([formal_only, casual_ok, anywhere], example_scene.scene, "casual", set())
    assert {t.id for t in got} == {"c", "a"}
    got = pick_targets([formal_only, casual_ok, anywhere], example_scene.scene, "executive", set())
    assert {t.id for t in got} == {"f", "a"}
    assert all(t.register in ("casual", "executive") for t in got)


def test_scene_generation_hands_ranges_to_the_generator(db, example_scene):
    import json, random
    from coach.pipeline import CoachPipeline

    llm = ScriptedLLM()
    pipe = CoachPipeline(db, llm, rng=random.Random(1))
    db.get_or_create_user(1, NOW)
    e = expression_from_finding(finding(), scene_register="casual")
    db.upsert_expression(1, e, NOW)
    db.activate_target(1, e.id, NOW)
    llm.push("scene", json.loads(example_scene.model_dump_json()))
    asyncio.run(pipe.create_scene(1))
    payload = json.loads(llm.calls_for("scene")[0].user)
    assert payload["stealth_targets"][0]["register_tags"] == ["diplomatic", "casual"]
