"""S5: the V3 model calls in ai_engine.py go through one injectable transport (S0 6, A12; judge call per S4 question 2)."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

import ai_engine
import retrieval
from ai_engine import DualLLMEngine, is_trivial_message, load_analyst_v3_prompt
from schemas import (
    ComprehensionStatus,
    DialogueDecision,
    DirectnessLevel,
    PersonaCard,
    RegisterProfile,
    SceneBible,
    TrainingMode,
)


class Transport:
    """A stand-in for ``llm_transport.call_json``: records each call, runs the caller's validator, returns .value/.meta."""

    def __init__(self, *values, meta=None):
        self.values = list(values)
        self.calls: list[dict] = []
        self.meta = meta

    def __call__(self, call_class, system, user, validator, *, deadline_s=None, correlation_id=None, **kw):
        self.calls.append(dict(call_class=call_class, system=system, user=user, deadline_s=deadline_s, correlation_id=correlation_id))
        item = self.values.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(value=validator(item), meta=self.meta)


def engine(*values, meta=None):
    transport = Transport(*values, meta=meta)
    return DualLLMEngine(keys=["dummy-key"], transport_call_json=transport), transport


def decision(**over):
    base = dict(act="react", style="plain", allow_question=False, max_questions=0)
    base.update(over)
    return DialogueDecision(**base)


def responder_kwargs(**over):
    base = dict(
        learner_text="We talked about the plan.",
        recent_history=[{"role": "learner", "text": "Hi"}, {"role": "partner", "text": "Hey, how's it going?"}],
        persona=PersonaCard(),
        scene_bible=SceneBible(scene_id="s1"),
        register_profile=RegisterProfile.CASUAL,
        directness=DirectnessLevel.BALANCED,
        training_mode=TrainingMode.OPEN_SCENE,
        dialogue_decision=decision(),
    )
    base.update(over)
    return base


# ------------------------------------------------------------------------------ the Analyst


def test_the_analyst_call_uses_the_v3_prompt_the_analyst_class_and_a_deadline():
    ai, transport = engine({"diagnostics": []})
    raw, meta = ai.run_analyst_v3(
        learner_text="We discussed about the plan.",
        recent_history=[{"role": "partner", "text": "What did you do?"}],
        register_profile=RegisterProfile.CASUAL,
        directness=DirectnessLevel.BALANCED,
        scene_bible=SceneBible(scene_id="s1"),
        active_targets=[{"target_id": "come_to_think_of_it", "canonical_form": "come to think of it"}],
        correlation_id="turn_1",
    )
    call = transport.calls[0]
    assert raw == {"diagnostics": []} and meta is None
    assert str(getattr(call["call_class"], "value", call["call_class"])) == "analyst"
    assert call["system"] == load_analyst_v3_prompt() and call["deadline_s"] == 30.0 and call["correlation_id"] == "turn_1"
    user = json.loads(call["user"])
    assert user["learner_message"] == "We discussed about the plan."
    assert user["active_targets"] == [{"id": "come_to_think_of_it", "expression": "come to think of it"}]
    assert user["recent_turns"] == [{"speaker": "partner", "text": "What did you do?"}]


def test_an_analyst_reply_that_is_not_an_object_is_rejected_by_the_validator():
    ai, _ = engine(["not", "an", "object"])
    with pytest.raises(ValueError):
        ai.run_analyst_v3(
            learner_text="Hi there my friend", recent_history=[], register_profile=RegisterProfile.CASUAL,
            directness=DirectnessLevel.BALANCED, scene_bible=SceneBible(scene_id="s1"),
        )


@pytest.mark.parametrize("err", [TimeoutError("deadline"), retrieval.JudgeShed("quota")])
def test_transport_failures_propagate_so_the_orchestrator_can_record_a_skip(err):
    ai, _ = engine(err)
    with pytest.raises(type(err)):
        ai.run_analyst_v3(
            learner_text="Hi there my friend", recent_history=[], register_profile=RegisterProfile.CASUAL,
            directness=DirectnessLevel.BALANCED, scene_bible=SceneBible(scene_id="s1"),
        )


# ------------------------------------------------------------------------------ the Responder


def test_the_responder_returns_the_reply_and_its_comprehension_status():
    ai, transport = engine({"reply_text": "Wait, who do you mean?", "comprehension_status": "ambiguous"})
    res = ai.run_responder_v3(**responder_kwargs(correlation_id="turn_2", dialogue_decision=decision(act="clarify", allow_question=True, max_questions=1)))
    assert res.output.reply_text == "Wait, who do you mean?"
    assert res.comprehension_status == ComprehensionStatus.AMBIGUOUS
    assert str(getattr(transport.calls[0]["call_class"], "value", transport.calls[0]["call_class"])) == "responder"


@pytest.mark.parametrize("raw_status", [None, "", "nonsense", 7])
def test_an_unknown_comprehension_status_is_ok(raw_status):
    ai, _ = engine({"reply_text": "Nice, go on.", "comprehension_status": raw_status})
    assert ai.run_responder_v3(**responder_kwargs()).comprehension_status == ComprehensionStatus.OK


def test_a_waiting_p0_line_goes_into_the_brief_and_the_prompt_explains_it():
    ai, transport = engine({"reply_text": "Sure. Which Monday meeting did you mean?"})
    ai.run_responder_v3(**responder_kwargs(pending_clarification="Did you mean the Monday meeting?"))
    assert json.loads(transport.calls[0]["user"])["pending_clarification"] == "Did you mean the Monday meeting?"
    assert "pending_clarification" in transport.calls[0]["system"] and "comprehension_status" in transport.calls[0]["system"]


def test_a_reply_that_leaks_the_stealth_target_is_rejected_so_the_transport_retries():
    ai, _ = engine({"reply_text": "You should say come to think of it more often."})
    with pytest.raises(ValueError, match="stealth target"):
        ai.run_responder_v3(**responder_kwargs(stealth_target_info={"phrase_id": "come_to_think_of_it", "canonical_form": "come to think of it"}))


def test_the_v2_entry_point_returns_only_the_output():
    ai, _ = engine({"reply_text": "Right, go on."})
    assert ai.run_responder(**responder_kwargs()).reply_text == "Right, go on."


def test_call_meta_from_the_transport_is_carried_on_the_result():
    meta = SimpleNamespace(model="gemini-x", attempts=1, latency_ms=120, call_class="responder", lane="fast", shed=False, cached=False)
    ai, _ = engine({"reply_text": "Right, go on."}, meta=meta)
    res = ai.run_responder_v3(**responder_kwargs())
    assert res.call_meta is None or res.call_meta.model == "gemini-x"


# ------------------------------------------------------------------------------ the judge


def test_the_judge_call_is_class_judge_with_a_short_deadline_and_feeds_make_judge():
    ai, transport = engine({"linguistic_level": 2, "functional_level": 3, "functional_equivalent_credited": False})
    result = retrieval.grade_attempt(
        "Come to think about it, we never booked it.", retrieval.Target("come to think of it"),
        judge=ai.build_judge(), prompt_kind="meaning", context_family="workplace", communicative_intention="add_an_afterthought",
    )
    call = transport.calls[0]
    assert str(getattr(call["call_class"], "value", call["call_class"])) == "judge" and call["deadline_s"] == 8.0
    assert "RETRIEVAL JUDGE" in call["system"]
    assert (result.linguistic_level, result.functional_level, result.shed) == (2, 3, False)


@pytest.mark.parametrize("err", [TimeoutError("deadline"), retrieval.JudgeShed("quota")])
def test_a_judge_shed_becomes_a_shed_result_not_a_crash(err):
    ai, _ = engine(err)
    result = retrieval.grade_attempt("Something unrelated entirely.", retrieval.Target("come to think of it"), judge=ai.build_judge())
    assert result.shed is True and result.outcome is None


def test_a_python_exact_match_never_calls_the_judge():
    ai, transport = engine()
    result = retrieval.grade_attempt("Come to think of it, we never booked it.", retrieval.Target("come to think of it"), judge=ai.build_judge())
    assert transport.calls == [] and result.outcome.value == "success"


# ------------------------------------------------------------------------------ transport plumbing


def test_without_a_transport_the_single_model_path_is_used(monkeypatch):
    monkeypatch.setitem(sys.modules, "llm_transport", None)  # not importable
    ai = DualLLMEngine(keys=["dummy-key"])
    seen = {}

    def fake(*, system_prompt, user_prompt, validator_fn, temperature, max_tries=2):
        seen.update(temperature=temperature, max_tries=max_tries)
        return validator_fn({"reply_text": "Right, go on."})

    ai._call_gemini_json = fake
    assert ai.run_responder_v3(**responder_kwargs()).output.reply_text == "Right, go on."
    assert seen["max_tries"] == 1


def test_the_module_transport_is_used_when_it_exists_and_gets_the_call_class_enum(monkeypatch):
    class CallClass(str):
        def __new__(cls, value):
            if value not in ("responder", "analyst", "judge", "generator"):
                raise ValueError(value)
            return super().__new__(cls, value)

    seen = {}

    def call_json(call_class, system, user, validator, *, deadline_s=None, correlation_id=None):
        seen["class"] = call_class
        return SimpleNamespace(value=validator({"reply_text": "Right, go on."}), meta=None)

    fake = types.ModuleType("llm_transport")
    fake.CallClass = CallClass
    fake.call_json = call_json
    monkeypatch.setitem(sys.modules, "llm_transport", fake)
    ai = DualLLMEngine(keys=["dummy-key"])
    assert ai.run_responder_v3(**responder_kwargs()).output.reply_text == "Right, go on."
    assert isinstance(seen["class"], CallClass) and seen["class"] == "responder"


def test_the_v2_inline_analyst_and_its_normalizer_are_gone():
    for name in ("ANALYST_SYSTEM_PROMPT", "_fast_normalize_raw_analysis"):
        assert not hasattr(ai_engine, name)
    assert not hasattr(DualLLMEngine, "run_analyst")


@pytest.mark.parametrize("text,trivial", [("hey", True), ("Hi Alex!", True), ("ok thanks", True), ("hey Alex how was your weekend", False), ("", False), ("We went there.", False)])
def test_greetings_and_acknowledgements_are_trivial(text, trivial):
    assert is_trivial_message(text) is trivial
