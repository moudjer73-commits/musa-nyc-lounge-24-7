"""sparring.py (V4.0): 3-Way Multi-Persona Sparring Table (Learner + Alex + Maya)."""

from __future__ import annotations

import html
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SparringExchange(BaseModel):
    alex_reply: str = Field(..., min_length=4, max_length=320)
    maya_counter: str = Field(..., min_length=4, max_length=320)
    pragmatic_move_spotlight: str = Field(..., min_length=4, max_length=200)
    suggested_pivot_chunk: str = Field(..., min_length=2, max_length=90)
    arabic_strategy_tip_ar: Optional[str] = None


@dataclass
class SparringSession:
    learner_id: int
    topic_title: str
    opening_dilemma: str
    history: list[dict[str, str]] = field(default_factory=list)


_SPARRING_LOCK = threading.Lock()
_ACTIVE_SPARRING: dict[int, SparringSession] = {}

SPARRING_TOPICS: dict[str, tuple[str, str]] = {
    "ai_decisions": (
        "Algorithmic Governance vs. Human Discretion",
        "Alex argues city budgets and service routing should be automated by AI to eliminate bias, while Maya warns that algorithmic opacity destroys local accountability. Where do you draw the line?",
    ),
    "remote_autonomy": (
        "High-Output Autonomy vs. Core Sync Hours",
        "Alex wants strict 4-hour daily team overlap to prevent bottlenecks, while Maya insists top performers should be judged purely on deliverables, zero clock-watching. What's your call?",
    ),
    "candor_culture": (
        "Radical Candor vs. Diplomatic Cushioning",
        "Maya believes blunt, immediate critique in meetings saves weeks of drift, while Alex argues public bluntness makes smart people defensive and kills psychological safety. How do you handle it?",
    ),
}

SPARRING_SYSTEM_PROMPT = """You run a dynamic 3-way American English C1-C2 intellectual sparring table with two distinct native hosts responding to the learner:
- **Alex**: Pragmatic operational realist—values feasibility, trade-offs, and measurable execution.
- **Maya**: Sharp analytical challenger (devil's advocate)—probes assumptions, power dynamics, and unintended consequences.

Rules:
1. Respond directly to the learner's latest argument (`learner_turn`).
2. `alex_reply`: 1-2 natural spoken sentences (builds on or tests the practical side of the learner's point).
3. `maya_counter`: 1-2 natural spoken sentences (introduces a sharp counter-angle or edge case, ending with a pointed question for the learner).
4. `pragmatic_move_spotlight`: 1 crisp sentence noting what worked in the learner's argument or how to hold the floor against two speakers.
5. `suggested_pivot_chunk`: A high-level C1-C2 spoken connector the learner can use in their next turn (e.g., "I take Maya's point on..., though in practice...").
6. `arabic_strategy_tip_ar`: نصيحة حجاجية موجزة بالعربية الفصحى لإدارة النقاش المزدوج.

Return ONLY valid JSON:
{
  "alex_reply": "...",
  "maya_counter": "...",
  "pragmatic_move_spotlight": "...",
  "suggested_pivot_chunk": "...",
  "arabic_strategy_tip_ar": "..."
}"""


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def close_sparring(learner_id: int) -> bool:
    with _SPARRING_LOCK:
        return _ACTIVE_SPARRING.pop(int(learner_id), None) is not None


def get_sparring(learner_id: int) -> Optional[SparringSession]:
    with _SPARRING_LOCK:
        return _ACTIVE_SPARRING.get(int(learner_id))


def start_sparring(learner_id: int, topic_key: str = "ai_decisions") -> SparringSession:
    title, dilemma = SPARRING_TOPICS.get(topic_key, SPARRING_TOPICS["ai_decisions"])
    sess = SparringSession(learner_id=int(learner_id), topic_title=title, opening_dilemma=dilemma)
    with _SPARRING_LOCK:
        _ACTIVE_SPARRING[int(learner_id)] = sess
    return sess


def build_deterministic_sparring_turn(sess: SparringSession, learner_text: str) -> SparringExchange:
    return SparringExchange(
        alex_reply="I hear you on the big picture, and operationally that kind of balance sounds great on paper—until a deadline slips and someone has to own the fallout.",
        maya_counter="Hold on, Alex—you're assuming top-down control actually prevents fallout. If you don't protect autonomy when pressure hits, doesn't your whole principle collapse?",
        pragmatic_move_spotlight="Strong stance—now synthesize both angles by conceding the operational risk while defending your core principle.",
        suggested_pivot_chunk="I grant Alex's point on execution risk, but to Maya's question,",
        arabic_strategy_tip_ar="للسيطرة على طاولة حوار ثلاثية، ابدأ بتسليم جزئي للنقطة العملية ثموجّه ردك الحاسم نحو السؤال النقدي.",
    )


def run_sparring_turn(ai: Any, sess: SparringSession, learner_text: str) -> SparringExchange:
    sess.history.append({"role": "learner", "text": learner_text})
    payload = json.dumps(
        {
            "topic": sess.topic_title,
            "dilemma": sess.opening_dilemma,
            "recent_history": sess.history[-6:],
            "learner_turn": learner_text,
        },
        ensure_ascii=False,
    )

    def _validate(raw: dict[str, Any]) -> SparringExchange:
        return SparringExchange.model_validate(raw)

    exch: Optional[SparringExchange] = None
    if ai is not None and hasattr(ai, "_call_json"):
        try:
            res = ai._call_json(
                "debrief",
                SPARRING_SYSTEM_PROMPT,
                payload,
                _validate,
                deadline_s=15.0,
                temperature=0.65,
            )
            exch = res.parsed
        except Exception as err:
            logger.warning("Sparring LLM call fell back to deterministic exchange: %s", err)

    if exch is None:
        exch = build_deterministic_sparring_turn(sess, learner_text)

    sess.history.append({"role": "alex", "text": exch.alex_reply})
    sess.history.append({"role": "maya", "text": exch.maya_counter})
    return exch


def render_sparring_opening_html(sess: SparringSession) -> str:
    return "\n".join(
        [
            f"⚔️ <b>3-Way Sparring Table: {_e(sess.topic_title)}</b>",
            "",
            f"🎯 <b>The Standoff:</b> {_e(sess.opening_dilemma)}",
            "",
            "🗣️ <b>Alex (Realist)</b> and 💬 <b>Maya (Challenger)</b> are both at the table.",
            "<i>Type or record your opening stance—both hosts will respond to test your C1-C2 framing.</i>",
        ]
    )


def render_sparring_exchange_html(exch: SparringExchange, *, with_arabic: bool = True) -> str:
    lines = [
        f"🗣️ <b>Alex:</b> {_e(exch.alex_reply)}",
        "",
        f"💬 <b>Maya:</b> {_e(exch.maya_counter)}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"♟️ <b>Sparring Spotlight:</b> {_e(exch.pragmatic_move_spotlight)}",
        f"🔗 <b>Power Pivot Chunk:</b> <code>{_e(exch.suggested_pivot_chunk)}</code>",
    ]
    if with_arabic and exch.arabic_strategy_tip_ar:
        lines.append(f"🇦🇪 <tg-spoiler>{_e(exch.arabic_strategy_tip_ar)}</tg-spoiler>")
    return "\n".join(lines)
