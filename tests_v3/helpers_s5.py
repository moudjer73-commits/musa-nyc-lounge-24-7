"""Builders shared by the S5 tests: a scripted Responder/Analyst, an inline executor, and a small harness that
plays learner turns through the real orchestrator, flows and database (no Telegram, no network)."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Optional

import retrieval
from ai_engine import ResponderResult
from database import EventSourcedDatabase
from flows import V3Flows
from orchestrator import TurnOrchestrator
from schemas import ComprehensionStatus, ResponderOutput

LEARNER = 1001
TEXT = "We discussed about the plan yesterday and I did an effort."
GREETING_1 = "Hello Alex, nice to see you again today."
GREETING_2 = "I was thinking about the weekend plans."


def raw_diag(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        original_span="discussed about",
        normalized_pattern_id="COLLOC_PREP_VERB:discuss_about",
        relevant_context_family="global",
        intended_meaning="talk about a topic in detail",
        kind="collocation",
        severity="medium",
        alternative="discussed",
        alternative_status="required",
        listener_effect="Clear, but it sounds slightly off.",
        explanation_en="In English, 'discuss' takes its object directly: 'discussed the plan'.",
        preserves_intended_meaning=True,
        context_fit_verified=True,
        repair_scope="phrase_span",
        communicative_impact=2,
        learning_value=2,
        linguistic_confidence=0.9,
        domain="linguistic",
        classification="error",
    )
    base.update(over)
    return base


class InlineExecutor:
    """Runs the Analyst at submit time, so a turn's analysis is finished when ``process_turn`` returns."""

    def submit(self, fn, *args, **kwargs) -> "Future[Any]":
        future: "Future[Any]" = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as err:  # noqa: BLE001 - mirrors ThreadPoolExecutor
            future.set_exception(err)
        return future


class DeferredExecutor:
    """Holds each Analyst call until ``run_all``/``run`` is called: the learner can send another turn first."""

    def __init__(self) -> None:
        self.pending: list[tuple["Future[Any]", Any, tuple, dict]] = []

    def submit(self, fn, *args, **kwargs) -> "Future[Any]":
        future: "Future[Any]" = Future()
        self.pending.append((future, fn, args, kwargs))
        return future

    def run(self, index: int = 0) -> None:
        future, fn, args, kwargs = self.pending[index]
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as err:  # noqa: BLE001
            future.set_exception(err)

    def run_all(self) -> None:
        for i in range(len(self.pending)):
            if not self.pending[i][0].done():
                self.run(i)


class ScriptedAI:
    """Stands in for ``DualLLMEngine``: queued Responder replies and Analyst objects, plus a log of every brief."""

    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.analyses: list[Any] = []
        self.briefs: list[dict[str, Any]] = []
        self.analyst_calls = 0
        self.judge_answers: list[Any] = []
        self.judge_calls = 0

    def run_responder_v3(self, **kw: Any) -> ResponderResult:
        self.briefs.append(kw)
        item = self.replies.pop(0) if self.replies else "Right, go on."
        status = ComprehensionStatus.OK
        if isinstance(item, tuple):
            item, status = item
        return ResponderResult(ResponderOutput(reply_text=item), status, None)

    def run_analyst_v3(self, **kw: Any):
        self.analyst_calls += 1
        item = self.analyses.pop(0) if self.analyses else {"diagnostics": []}
        if isinstance(item, BaseException):
            raise item
        return item, None

    def call_llm(self, system_prompt: str, user_prompt: str) -> Any:
        self.judge_calls += 1
        item = self.judge_answers.pop(0) if self.judge_answers else {"linguistic_level": 0, "functional_level": 0, "functional_equivalent_credited": False}
        if isinstance(item, BaseException):
            raise item
        return item

    def build_judge(self):
        return retrieval.make_judge(self.call_llm)


class Harness:
    def __init__(self, db: EventSourcedDatabase, *, learner: int = LEARNER, executor: Any = None) -> None:
        self.db = db
        self.learner = learner
        self.ai = ScriptedAI()
        self.executor = executor or InlineExecutor()
        self.orch = TurnOrchestrator(db, self.ai, executor=self.executor)
        self.flows = V3Flows(db, self.ai)
        self.n = 0

    def say(
        self,
        text: str,
        *,
        analysis: Any = None,
        reply: Any = None,
        origin: str = "scene",
        interaction_id: Optional[int] = None,
        apply: bool = True,
        key: Optional[str] = None,
    ):
        self.n += 1
        if analysis is not None:
            self.ai.analyses.append(analysis)
        if reply is not None:
            self.ai.replies.append(reply)
        res = self.orch.process_turn(
            learner_id=self.learner,
            learner_text=text,
            message_idempotency_key=key or f"m{self.n}",
            origin=origin,
            interaction_id=interaction_id,
        )
        actions = None
        if apply and res.analysis_future is not None and not res.duplicate:
            actions = self.orch.apply_analysis_outcome(
                learner_id=self.learner, handle=res.handle, outcome=res.analysis_future.result()
            )
        return res, actions

    def reach_card(self, diag: Optional[dict[str, Any]] = None, *, extra: Optional[dict[str, Any]] = None):
        """Two quiet turns (the scene warm-up), then the turn with one finding."""
        self.db.update_learner_preference(self.learner, feedback_preference="active")
        self.say(GREETING_1, analysis={"diagnostics": []})
        self.say(GREETING_2, analysis={"diagnostics": []})
        payload: dict[str, Any] = {"diagnostics": [diag or raw_diag()]}
        payload.update(extra or {})
        return self.say(TEXT, analysis=payload, reply="Ah, so you finally talked it through.")

    def events(self, event_type: str, learner: Optional[int] = None) -> list[dict[str, Any]]:
        import json

        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT event_seq, event_id, session_id, payload_json FROM event_log WHERE learner_id = ? AND event_type = ? ORDER BY event_seq",
                (learner or self.learner, event_type),
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]
