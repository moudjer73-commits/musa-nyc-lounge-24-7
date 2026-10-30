"""Measure the Analyst on labelled utterances, through the SAME path a live turn takes.

Why this exists: the unit tests prove that prompts and code agree with each other. They cannot prove
that a model judges real English well. The decisive number is not "how many errors were found" but
"how often does the system react to something a native would not notice". So negative cases (natural
sentences that must be left alone) are first-class here.

    run_cases(llm, cases)  -> list[CaseResult]   (one Analyst call per case, sanitised, then `decide`)
    summarize(results)      -> dict               (the numbers worth watching)

A case result records two things for a negative case: any finding at all (`flagged`), and whether the
policy would have shown the learner something (`visible`: a recast, a card offer or a clarification
question, with the budget wide open so the policy cannot hide a false alarm behind pacing).
"""

from __future__ import annotations

import json
import random
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .checks import fuzzy_contains, normalize_text
from .db import Database
from .llm import LLMClient
from .models import Analysis, Category, Cefr, Finding, PersonaScene
from .pipeline import CoachPipeline
from .policy import CorrectionBudget, decide
from .state import LearnerState

# same note as in models.py: `register` is a field name in the prompt contracts, the warning is noise
warnings.filterwarnings("ignore", message='Field name "register".*shadows an attribute')

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "eval" / "analyst_cases.json"
VISIBLE_ACTIONS = frozenset({"recast", "offer_card", "clarify"})


class SceneSpec(BaseModel):
    register: str
    relationship: str
    situation: str


class ExpectedFinding(BaseModel):
    span: str
    categories: list[Category] = Field(min_length=1)


class Expect(BaseModel):
    findings: list[ExpectedFinding] = Field(default_factory=list)


class Case(BaseModel):
    id: str
    kind: Literal["negative", "positive"]
    tag: str
    scene: SceneSpec
    cefr: Cefr = "B2"
    modality: Literal["text", "voice"] = "text"
    learner_message: str
    expect: Expect
    why: str


@dataclass
class CaseResult:
    case: Case
    findings: list[Finding] = field(default_factory=list)
    action: str = "silent"
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.findings)

    @property
    def visible(self) -> bool:
        return self.action in VISIBLE_ACTIONS

    @property
    def hit(self) -> bool:
        """Positive case: some finding has an expected category and overlaps the expected span."""
        for exp in self.case.expect.findings:
            for f in self.findings:
                if f.category in exp.categories and _overlaps(f.span, exp.span):
                    return True
        return False

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return (not self.visible and not self.flagged) if self.case.kind == "negative" else self.hit


def _overlaps(a: str, b: str) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    return bool(na and nb) and (na in nb or nb in na or fuzzy_contains(a, b) or fuzzy_contains(b, a))


def load_cases(path: Path | str = DEFAULT_CASES) -> list[Case]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Case.model_validate(c) for c in data["cases"]]


def _state(case: Case) -> LearnerState:
    # a mid-session learner with an open budget: nothing may be hidden behind warm-up or pacing
    return LearnerState(
        user_id=1, cefr=case.cefr, turn_index=8, turns_since_card=99, baseline_words=12.0, recent_words=[12, 11, 13],
    )


async def run_case(pipe: CoachPipeline, example: PersonaScene, case: Case) -> CaseResult:
    persona = example.persona.model_copy(update={"role_in_scene": case.scene.relationship})
    scene = example.scene.model_copy(update={"situation": case.scene.situation})
    state = _state(case)
    history = [{"speaker": "learner", "text": case.learner_message}]
    voice = {"words_per_minute": 130, "pause_count": 0, "long_pause_count": 0, "filler_count": 0, "restart_count": 0} \
        if case.modality == "voice" else None
    analysis, notes = await pipe._analyse(  # same path as a live turn: payload, timeout, sanitising
        case.learner_message, case.modality, voice, persona, scene, case.scene.register, state, history, [], [], [],
    )
    decision = decide(analysis, CorrectionBudget(), state)
    # `_analyse` swallows a failed call so a live turn can carry on; here that would read as a clean
    # pass on every natural sentence, so an unavailable analyst is an error, never a result.
    outage = next((n for n in notes if n.startswith("analyst unavailable")), None)
    return CaseResult(case=case, findings=list(analysis.findings), action=decision.action, notes=notes, error=outage)


async def run_cases(llm: LLMClient, cases: list[Case], example: PersonaScene) -> list[CaseResult]:
    db = Database(":memory:")
    db.get_or_create_user(1, 0.0)
    pipe = CoachPipeline(db, llm, rng=random.Random(0))
    results: list[CaseResult] = []
    for case in cases:
        try:
            results.append(await run_case(pipe, example, case))
        except Exception as e:  # one bad case must not hide the rest
            results.append(CaseResult(case=case, error=repr(e)))
    return results


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    neg = [r for r in results if r.case.kind == "negative" and not r.error]
    pos = [r for r in results if r.case.kind == "positive" and not r.error]

    def rate(n: int, d: int) -> float | None:
        return round(n / d, 3) if d else None

    by_tag: dict[str, dict[str, int]] = {}
    for r in neg:
        t = by_tag.setdefault(r.case.tag, {"cases": 0, "flagged": 0, "visible": 0})
        t["cases"] += 1
        t["flagged"] += r.flagged
        t["visible"] += r.visible
    return {
        "negative_cases": len(neg),
        "positive_cases": len(pos),
        "errors": [r.case.id for r in results if r.error],
        "false_alarm_rate": rate(sum(r.flagged for r in neg), len(neg)),               # any finding on a natural sentence
        "visible_false_alarm_rate": rate(sum(r.visible for r in neg), len(neg)),       # the learner would have seen it
        "recall": rate(sum(r.hit for r in pos), len(pos)),
        "negatives_by_tag": by_tag,
        "failed": [r.case.id for r in results if not r.passed],
    }
