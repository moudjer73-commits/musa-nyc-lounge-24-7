"""The Analyst evaluation harness: the dataset is sound and the scoring cannot be fooled."""

from __future__ import annotations

import asyncio
import json

import pytest

from coach import prompt_loader as pl
from coach.checks import normalize_text
from coach.evalset import Case, CaseResult, load_cases, run_cases, summarize
from coach.llm import LLMError, ScriptedLLM
from coach.models import Analysis

from conftest import mk_finding

CASES = load_cases()
REQUIRED_NEGATIVE_TAGS = {
    "direct_but_professional", "short_natural_no_markers", "register_fits_scene",
    "correct_but_uncommon", "natural_variety", "proportionate_softening",
}


# ------------------------------------------------------------------------ the data


def test_dataset_is_well_formed():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        if c.kind == "negative":
            assert c.expect.findings == [], c.id
        else:
            assert c.expect.findings, c.id
            for exp in c.expect.findings:
                assert normalize_text(exp.span) in normalize_text(c.learner_message), (c.id, "span must be verbatim")


def test_negatives_cover_every_false_alarm_family_the_review_asked_for():
    tags = {c.tag for c in CASES if c.kind == "negative"}
    assert REQUIRED_NEGATIVE_TAGS <= tags
    assert sum(c.kind == "negative" for c in CASES) >= sum(c.kind == "positive" for c in CASES)   # precision comes first


def test_the_negatives_agree_with_the_prompt_they_test():
    analyst = pl.analyst_system()
    assert "Do not flag" in analyst
    # every negative family has a matching rule in the do-not-flag list
    for needle in ("direct refusal", "ABSENCE", "Informal or slangy", "uncommon", "natural variety", "cushioning", "Speech-to-text"):
        assert needle.lower() in analyst.lower(), needle


# ------------------------------------------------------------------------ scoring, with scripted analysts


def by_message(llm_answers):
    def fn(call):
        msg = json.loads(call.user)["learner_message"]
        return llm_answers(msg)
    return fn


def perfect(msg: str) -> dict:
    case = next(c for c in CASES if c.learner_message == msg)
    if case.kind == "negative":
        return Analysis().model_dump()
    exp = case.expect.findings[0]
    f = mk_finding(span=exp.span, category=exp.categories[0], pattern_key=f"{exp.categories[0]}.GEN.{case.id}",
                   l1_transfer=False, l1_pattern=None, l1_source_ar=None, severity=1)
    return Analysis(findings=[f]).model_dump()


def eager(msg: str) -> dict:
    """Flags the first word of everything as a severe pragmatic problem."""
    first = msg.split()[0].strip(".,!?")
    f = mk_finding(span=first, category="pragmatic", pattern_key=f"pragmatic.GEN.x_{first.lower()}", severity=3,
                   l1_transfer=False, l1_pattern=None, l1_source_ar=None)
    return Analysis(findings=[f]).model_dump()


def run(answers, example_scene, cases=CASES):
    llm = ScriptedLLM()
    llm.push("analyst", *[by_message(answers)] * len(cases))
    return llm, asyncio.run(run_cases(llm, cases, example_scene))


def test_a_perfect_analyst_scores_perfectly(example_scene):
    _, results = run(perfect, example_scene)
    s = summarize(results)
    assert s["failed"] == [] and s["errors"] == []
    assert s["false_alarm_rate"] == 0.0 and s["visible_false_alarm_rate"] == 0.0 and s["recall"] == 1.0
    assert set(s["negatives_by_tag"]) >= REQUIRED_NEGATIVE_TAGS


def test_an_over_eager_analyst_is_caught_by_visible_false_alarms(example_scene):
    _, results = run(eager, example_scene)
    s = summarize(results)
    assert s["false_alarm_rate"] == 1.0
    assert s["visible_false_alarm_rate"] == 1.0        # budget wide open: nothing hid the mistake
    assert s["recall"] < 0.5
    assert len(s["failed"]) == len(CASES) - round(s["recall"] * s["positive_cases"])


def test_a_finding_in_the_wrong_category_or_place_is_not_a_hit(example_scene):
    case = next(c for c in CASES if c.id == "pos_light_verb")

    def wrong_category(msg):
        f = mk_finding(span="take a decision", category="grammar", pattern_key="grammar.GEN.x", l1_transfer=False,
                       l1_pattern=None, l1_source_ar=None, severity=1)
        return Analysis(findings=[f]).model_dump()

    def wrong_place(msg):
        f = mk_finding(span="Friday", category="collocation", pattern_key="collocation.GEN.x", l1_transfer=False,
                       l1_pattern=None, l1_source_ar=None, severity=1)
        return Analysis(findings=[f]).model_dump()

    for answers in (wrong_category, wrong_place):
        _, results = run(answers, example_scene, [case])
        assert not results[0].hit and not results[0].passed


def test_an_analyst_outage_is_an_error_not_a_clean_pass(example_scene):
    neg = next(c for c in CASES if c.kind == "negative")
    llm = ScriptedLLM()
    llm.push("analyst", LLMError("503"))
    results = asyncio.run(run_cases(llm, [neg], example_scene))
    assert results[0].error and not results[0].passed
    s = summarize(results)
    assert s["errors"] == [neg.id] and s["negative_cases"] == 0      # excluded from the rates, not counted as clean


def test_cases_reach_the_analyst_through_the_real_payload(example_scene):
    voice_case = next(c for c in CASES if c.modality == "voice")
    manager_case = next(c for c in CASES if c.id == "neg_direct_refusal_with_reason")
    llm, _ = run(perfect, example_scene, [voice_case, manager_case])
    v, m = (json.loads(c.user) for c in llm.calls_for("analyst"))
    assert v["modality"] == "voice" and v["voice_metrics"] is not None
    assert m["modality"] == "text" and m["voice_metrics"] is None
    assert m["scene"] == {"register": "executive", "relationship": "their manager", "situation_summary": manager_case.scene.situation}
    assert m["learner_cefr"] == "B2"
    assert llm.calls_for("analyst")[0].system == pl.analyst_system()


def test_summarize_arithmetic():
    def res(kind, *, flagged=False, visible=False, hit=False, tag="t"):
        case = Case(id="x", kind=kind, tag=tag, scene={"register": "casual", "relationship": "r", "situation": "s"},
                    learner_message="hello there", expect={"findings": [] if kind == "negative" else [{"span": "hello", "categories": ["grammar"]}]}, why="w")
        r = CaseResult(case=case, action="offer_card" if visible else "silent")
        if flagged:
            r.findings = [mk_finding(span="hello", category="grammar" if hit else "collocation", pattern_key="p")]
        return r

    results = [res("negative"), res("negative", flagged=True), res("negative", flagged=True, visible=True),
               res("negative"), res("positive", flagged=True, hit=True), res("positive")]
    s = summarize(results)
    assert s["false_alarm_rate"] == 0.5 and s["visible_false_alarm_rate"] == 0.25 and s["recall"] == 0.5
    assert s["negatives_by_tag"]["t"] == {"cases": 4, "flagged": 2, "visible": 1}
