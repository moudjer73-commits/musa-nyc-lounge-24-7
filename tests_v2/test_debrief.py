"""The debrief may only say what the conversation log supports."""

from __future__ import annotations

import asyncio
import json
import random

from coach.checks import debrief_unsupported_numbers
from coach.llm import ScriptedLLM
from coach.models import Grounding
from coach.pipeline import CoachPipeline

from conftest import Clock, mk_finding

NOW = 1_800_000_000.0


def session(db, example_scene, learner_words, *, modality="text", texts=None):
    db.get_or_create_user(1, NOW)
    sid = db.start_session(1, "open_scene", NOW, persona=example_scene.persona, scene=example_scene.scene)
    for i, w in enumerate(learner_words):
        text = (texts or {}).get(i, "word " * w)
        db.log_turn(sid, 1, "learner", text.strip(), NOW + 10 * i, words=w, modality=modality)
        db.log_turn(sid, 1, "persona", "Right.", NOW + 10 * i + 1, words=1, meta={"move": "REACT"})
    return sid


def test_facts_describe_length_trend_questions_and_modality(db, example_scene):
    sid = session(db, example_scene, [5, 7, 12, 15, 14, 16], texts={2: "Why is that?", 3: "And then what? Really?"})
    f = db.session_facts(sid)
    assert f.learner_turns == 6 and f.avg_words == 12 and f.longest_turn_words == 16
    assert (f.first_half_avg_words, f.second_half_avg_words, f.length_trend) == (8, 15, "longer")
    assert f.questions_asked == 2 and f.text_turns == 6 and f.voice_turns == 0 and f.cards_shown == 0


def test_short_or_flat_scenes_give_no_trend_so_nothing_can_be_claimed(db, example_scene):
    f = db.session_facts(session(db, example_scene, [8, 3, 9]))
    assert f.learner_turns == 3 and f.length_trend is None and f.first_half_avg_words is None
    db2_f = db.session_facts(session(db, example_scene, [10, 11, 9, 10]))
    assert db2_f.length_trend == "steady"
    shrinking = db.session_facts(session(db, example_scene, [20, 18, 6, 5]))
    assert shrinking.length_trend == "shorter"


def test_beats_and_cards_are_counted_from_the_log(db, example_scene):
    sid = session(db, example_scene, [5, 6])
    db.log_turn(sid, 1, "persona", "So anyway.", NOW + 99, words=2, meta={"move": "PIVOT", "beat_id": example_scene.scene.beats[0].id})
    db.log_finding(1, mk_finding(), "offer_card", NOW + 100, session_id=sid)
    db.log_finding(1, mk_finding(pattern_key="b"), "silent", NOW + 101, session_id=sid)
    f = db.session_facts(sid)
    assert (f.beats_reached, f.beats_total, f.cards_shown) == (1, len(example_scene.scene.beats), 1)


def test_a_session_without_a_scene_has_no_beats(db):
    db.get_or_create_user(1, NOW)
    sid = db.start_session(1, "phrase_workout", NOW)
    db.log_turn(sid, 1, "learner", "hello there", NOW, words=2)
    f = db.session_facts(sid)
    assert f.beats_total == 0 and f.learner_turns == 1


def G(field, value):
    return Grounding(field=field, value=value)


def test_number_guard_requires_declared_figures_and_checks_declarations_against_the_facts():
    payload = {"session_facts": {"first_half_avg_words": 6, "second_half_avg_words": 14, "questions_asked": 2}, "items": []}
    ok = [G("first_half_avg_words", 6), G("second_half_avg_words", 14)]
    assert debrief_unsupported_numbers("Answers grew from about 6 to 14 words.", payload, ok) == []
    # undeclared: even a number that exists in the facts is not enough on its own
    assert len(debrief_unsupported_numbers("Answers grew from about 6 to 14 words.", payload)) == 2
    # declared but false
    bad = debrief_unsupported_numbers("You asked 5 questions.", payload, [G("questions_asked", 5)])
    assert bad[0] == "grounding questions_asked=5 does not match session_facts (questions_asked=2)"
    assert debrief_unsupported_numbers("Fine, <b>no digits</b> here.", payload) == []
    assert debrief_unsupported_numbers("It&#x27;s fine.", payload) == []                      # entity digits are not numbers
    assert debrief_unsupported_numbers("Nice.", payload, [G("nonsense", 1)])[0].startswith("grounding nonsense=1")


def test_a_coincidental_number_no_longer_slips_through():
    """v2.0 accepted any digit found anywhere in the payload: 7 was avg_words, so 'asked 7 questions' passed."""
    payload = {"session_facts": {"avg_words": 7, "questions_asked": 1}, "items": []}
    assert debrief_unsupported_numbers("You asked 7 questions.", payload)                        # undeclared
    assert debrief_unsupported_numbers("You asked 7 questions.", payload, [G("questions_asked", 7)])   # declared, false


def test_digits_in_the_learners_own_phrases_are_free():
    item = mk_finding(span="at 3", recast_form="at three", alternatives=[{"text": "Let's meet at 3.", "register": "casual", "nuance": "x", "best_for": "y"}]).model_dump()
    payload = {"scene_title": "Room 5", "session_facts": {"avg_words": 7}, "items": [item], "wins": []}
    assert debrief_unsupported_numbers("<i>at 3</i> → <b>Let's meet at 3</b>, Room 5.", payload) == []
    assert debrief_unsupported_numbers("<i>at 3</i> and 7 words.", payload)                       # 7 is a fact: must be declared


def make(db, example_scene, learner_words):
    llm = ScriptedLLM()
    pipe = CoachPipeline(db, llm, rng=random.Random(1), clock=Clock())
    return pipe, llm, session(db, example_scene, learner_words)


def test_payload_carries_session_facts_and_no_bare_turn_count(db, example_scene):
    pipe, llm, sid = make(db, example_scene, [5, 7, 12, 15])
    llm.push("debrief", {"html": "Your answers grew from about 6 words to about 14 as the scene went on.",
                         "grounding": [{"field": "first_half_avg_words", "value": 6}, {"field": "second_half_avg_words", "value": 14}]})
    assert asyncio.run(pipe.end_scene(sid)).startswith("Your answers grew")
    payload = json.loads(llm.calls_for("debrief")[0].user)
    assert payload["session_facts"]["length_trend"] == "longer" and "turn_count" not in payload
    assert "fix_notes" not in payload


def test_an_invented_observation_is_retried_with_notes_then_dropped(db, example_scene):
    grounded = {"html": "Your answers grew from about 6 words to about 14.",
                "grounding": [{"field": "first_half_avg_words", "value": 6}, {"field": "second_half_avg_words", "value": 14}]}
    pipe, llm, sid = make(db, example_scene, [5, 7, 12, 15])
    llm.push("debrief", {"html": "You asked 7 questions, great!"}, grounded)
    html = asyncio.run(pipe.end_scene(sid))
    assert html.startswith("Your answers grew")
    second = json.loads(llm.calls_for("debrief")[1].user)
    assert second["fix_notes"] == ["number 7 is not declared in grounding; list it as {field, value} from session_facts or leave it out"]

    # the facts say 0 questions were asked: a declared "7" is false, a coincidence with avg_words (9) does not help either
    pipe, llm, sid = make(db, example_scene, [5, 7, 12, 15])
    lie = {"html": "You asked 7 questions.", "grounding": [{"field": "questions_asked", "value": 7}]}
    llm.push("debrief", lie, lie)
    assert asyncio.run(pipe.end_scene(sid)) == ""                       # nothing rather than something invented
    assert json.loads(llm.calls_for("debrief")[1].user)["fix_notes"] == ["grounding questions_asked=7 does not match session_facts (questions_asked=0)"]
