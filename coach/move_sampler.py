"""Dialogue-Move Sampler.

The character's *next move* (REACT, SHARE, BUILD, TEASE, ASK, PUSHBACK, CLARIFY,
BACKCHANNEL, PIVOT, WRAP) is chosen here, in code, and handed to the Responder in the
director brief. This is what stops the model from drifting into the classic bot habit of
ending every message with an interview question.

Pipeline:  hard constraints -> allowed set -> register weights -> situational multipliers
           -> seeded weighted draw.

Hard constraints (all unit-tested):
  * never the same move twice in a row;
  * ASK is not allowed right after ASK or CLARIFY;
  * questions (ASK + CLARIFY) never exceed 30% of the last 10 persona moves;
  * CLARIFY only when the policy forces it; WRAP only in the closing turn(s);
  * PIVOT only when a scene beat is due;
  * TEASE/PUSHBACK are blocked for emotional, frustrated or low-energy learners and early in the scene;
  * BACKCHANNEL is for a learner who is talking a lot, never for one-word answers;
  * if the learner asked a direct question, the character must answer: no REACT/BACKCHANNEL/TEASE.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .models import MOVES

QUESTION_MOVES = frozenset({"ASK", "CLARIFY"})
QUESTION_RATIO_CAP = 0.30
RATIO_WINDOW = 10
MIN_HISTORY_FOR_RATIO = 4

# Base weight of each move per register (columns: the moves the sampler may pick on its own).
BASE_WEIGHTS: Mapping[str, Mapping[str, float]] = {
    #            REACT SHARE BUILD TEASE  ASK PUSHBACK BACKCH
    "casual":     {"REACT": 16, "SHARE": 22, "BUILD": 18, "TEASE": 10, "ASK": 18, "PUSHBACK": 6,  "BACKCHANNEL": 6},
    "warm":       {"REACT": 14, "SHARE": 24, "BUILD": 18, "TEASE": 3,  "ASK": 16, "PUSHBACK": 2,  "BACKCHANNEL": 10},
    "witty":      {"REACT": 18, "SHARE": 18, "BUILD": 14, "TEASE": 20, "ASK": 10, "PUSHBACK": 8,  "BACKCHANNEL": 4},
    "diplomatic": {"REACT": 6,  "SHARE": 18, "BUILD": 16, "TEASE": 0,  "ASK": 20, "PUSHBACK": 20, "BACKCHANNEL": 6},
    "executive":  {"REACT": 6,  "SHARE": 16, "BUILD": 14, "TEASE": 0,  "ASK": 22, "PUSHBACK": 20, "BACKCHANNEL": 2},
    "analytical": {"REACT": 4,  "SHARE": 20, "BUILD": 22, "TEASE": 2,  "ASK": 20, "PUSHBACK": 20, "BACKCHANNEL": 6},
}
PIVOT_WEIGHT_WHEN_DUE = 30.0

# Target word range per move, scaled per register.
BASE_LENGTH: Mapping[str, tuple[int, int]] = {
    "REACT": (4, 10), "SHARE": (14, 30), "BUILD": (12, 26), "TEASE": (6, 14), "ASK": (8, 16),
    "PUSHBACK": (12, 24), "CLARIFY": (5, 12), "BACKCHANNEL": (2, 4), "PIVOT": (14, 28), "WRAP": (8, 18),
}
REGISTER_LENGTH_MULT: Mapping[str, float] = {
    "casual": 0.85, "warm": 1.0, "witty": 0.85, "diplomatic": 1.3, "executive": 0.9, "analytical": 1.4,
}
HARD_LENGTH_CAPS: Mapping[str, int] = {"REACT": 12, "BACKCHANNEL": 5}
MAX_LENGTH_WORDS = 55  # the prompt forbids monologues over ~60 words


@dataclass
class MoveContext:
    register: str
    recent_moves: Sequence[str]            # persona moves, oldest -> newest
    turn_index: int                        # learner turns so far, 1-based, counting the current one
    max_turns: int
    learner_words: int
    learner_asked_question: bool = False
    discourse_state: str = "normal"        # normal|storytelling|emotional|frustrated|out_of_frame
    energy: str = "normal"                 # low|normal|high
    persona_humor: str = "none"            # none|dry|gentle|goofy|sarcastic
    beat_due: bool = False
    forced: str | None = None              # e.g. "CLARIFY" from the policy
    exclude: set[str] = field(default_factory=set)  # moves ruled out by the director (e.g. a recast needs room)


def question_ratio(recent_moves: Sequence[str], window: int = RATIO_WINDOW) -> float:
    recent = list(recent_moves)[-window:]
    if not recent:
        return 0.0
    return sum(1 for m in recent if m in QUESTION_MOVES) / len(recent)


def allowed_moves(ctx: MoveContext) -> set[str]:
    if ctx.forced:
        return {ctx.forced}
    if ctx.turn_index >= ctx.max_turns:
        return {"WRAP"}

    recent = list(ctx.recent_moves)
    last = recent[-1] if recent else None
    allowed = set(MOVES) - {"CLARIFY", "WRAP"}

    if not ctx.beat_due:
        allowed.discard("PIVOT")
    if last:
        allowed.discard(last)

    # question discipline
    if last in QUESTION_MOVES:
        allowed.discard("ASK")
    window = recent[-RATIO_WINDOW:]
    if len(window) >= MIN_HISTORY_FOR_RATIO:
        q = sum(1 for m in window if m in QUESTION_MOVES)
        if (q + 1) / (len(window) + 1) > QUESTION_RATIO_CAP:
            allowed.discard("ASK")

    # BACKCHANNEL only when the learner is actually talking
    if ctx.learner_words <= 5 and ctx.discourse_state != "storytelling":
        allowed.discard("BACKCHANNEL")
    if "BACKCHANNEL" in recent[-4:]:
        allowed.discard("BACKCHANNEL")

    # a direct question must be answered
    if ctx.learner_asked_question:
        allowed -= {"BACKCHANNEL", "REACT", "TEASE", "PIVOT"}

    # no machine-gun reactions
    if recent[-5:].count("REACT") >= 2:
        allowed.discard("REACT")

    # banter needs a persona who jokes, a register that allows it, and a learner who is okay
    if ctx.persona_humor == "none" or ctx.register not in {"casual", "warm", "witty"}:
        allowed.discard("TEASE")
    if "TEASE" in recent[-5:]:
        allowed.discard("TEASE")
    if "PUSHBACK" in recent[-3:]:
        allowed.discard("PUSHBACK")
    if ctx.turn_index <= 2:
        allowed -= {"TEASE", "PUSHBACK"}
    if ctx.energy == "low" or ctx.discourse_state in {"emotional", "frustrated"}:
        allowed -= {"TEASE", "PUSHBACK", "PIVOT"}
    if ctx.discourse_state == "storytelling":
        allowed -= {"TEASE", "PUSHBACK", "PIVOT"}
    if ctx.discourse_state == "out_of_frame":
        allowed -= {"TEASE", "PUSHBACK", "PIVOT", "BACKCHANNEL"}

    allowed -= set(ctx.exclude)
    return allowed or {"SHARE"}


def move_weights(ctx: MoveContext) -> dict[str, float]:
    allowed = allowed_moves(ctx)
    if ctx.forced:
        return {ctx.forced: 1.0}
    if allowed == {"WRAP"}:
        return {"WRAP": 1.0}

    base = dict(BASE_WEIGHTS.get(ctx.register, BASE_WEIGHTS["casual"]))
    base["PIVOT"] = PIVOT_WEIGHT_WHEN_DUE if ctx.beat_due else 0.0
    w = {m: base.get(m, 0.0) for m in allowed}

    if ctx.learner_words <= 3:
        _scale(w, SHARE=1.5, BUILD=1.3, ASK=0.6)
    if ctx.energy == "low":
        _scale(w, SHARE=1.6, BUILD=1.3, ASK=0.4)
    if ctx.discourse_state == "storytelling" or ctx.learner_words >= 35:
        _scale(w, BACKCHANNEL=4.0, REACT=1.5)
    if ctx.learner_asked_question:
        _scale(w, SHARE=2.0, BUILD=1.3, PUSHBACK=1.2)
    if ctx.turn_index >= ctx.max_turns - 2:
        _scale(w, ASK=0.5)

    w = {m: v for m, v in w.items() if v > 0}
    return w or {"SHARE": 1.0}


def _scale(weights: dict[str, float], **factors: float) -> None:
    for move, f in factors.items():
        if move in weights:
            weights[move] *= f


def choose_move(ctx: MoveContext, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    weights = move_weights(ctx)
    moves = sorted(weights)  # sorted for seed-stable behaviour
    return rng.choices(moves, weights=[weights[m] for m in moves], k=1)[0]


def choose_length(move: str, register: str, learner_words: int, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    lo, hi = BASE_LENGTH[move]
    n = rng.uniform(lo, hi) * REGISTER_LENGTH_MULT.get(register, 1.0)
    if learner_words <= 5:
        n *= 0.8
    elif learner_words >= 40:
        n *= 1.15
    n = max(2, int(round(n)))
    if move in HARD_LENGTH_CAPS:
        n = min(n, HARD_LENGTH_CAPS[move])
    return min(n, MAX_LENGTH_WORDS)


def explain(ctx: MoveContext) -> dict[str, object]:
    """For logs and tests: what was allowed and with what weights."""
    return {"allowed": sorted(allowed_moves(ctx)), "weights": move_weights(ctx)}
