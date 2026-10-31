from __future__ import annotations

import random

import pytest

from coach.move_sampler import (
    MoveContext, QUESTION_MOVES, QUESTION_RATIO_CAP, RATIO_WINDOW, allowed_moves, choose_length, choose_move,
    explain, move_weights, question_ratio,
)
from coach.models import MOVES, REGISTERS


def ctx(**kw) -> MoveContext:
    base = dict(register="casual", recent_moves=[], turn_index=5, max_turns=14, learner_words=12, persona_humor="dry")
    base.update(kw)
    return MoveContext(**base)


def simulate(n_steps: int, seed: int, **kw) -> list[str]:
    rng = random.Random(seed)
    history: list[str] = []
    for i in range(n_steps):
        c = ctx(recent_moves=history, turn_index=3 + (i % 8), **kw)
        history.append(choose_move(c, rng))
    return history


@pytest.mark.parametrize("register", REGISTERS)
def test_never_the_same_move_twice_in_a_row(register):
    for seed in range(30):
        seq = simulate(60, seed, register=register)
        assert all(a != b for a, b in zip(seq, seq[1:]))


@pytest.mark.parametrize("register", REGISTERS)
def test_questions_obey_the_ratio_cap_and_never_come_back_to_back(register):
    for seed in range(40):
        rng = random.Random(seed)
        history: list[str] = []
        for i in range(60):
            c = ctx(register=register, recent_moves=history, turn_index=3 + (i % 8), beat_due=(i % 5 == 0))
            m = choose_move(c, rng)
            if m == "ASK":
                assert not history or history[-1] not in QUESTION_MOVES
                window = history[-RATIO_WINDOW:]
                if len(window) >= 4:
                    q = sum(1 for x in window if x in QUESTION_MOVES)
                    assert (q + 1) / (len(window) + 1) <= QUESTION_RATIO_CAP + 1e-9
            history.append(m)
        # and the long-run share of questions is modest
        assert sum(1 for m in history if m in QUESTION_MOVES) / len(history) <= 0.40


def test_forced_clarify_wins():
    assert choose_move(ctx(forced="CLARIFY")) == "CLARIFY"
    assert allowed_moves(ctx(forced="CLARIFY")) == {"CLARIFY"}


def test_clarify_and_wrap_are_never_sampled_freely():
    for seed in range(50):
        seq = simulate(40, seed)
        assert "CLARIFY" not in seq and "WRAP" not in seq


def test_closing_turn_wraps():
    assert allowed_moves(ctx(turn_index=14, max_turns=14)) == {"WRAP"}
    assert choose_move(ctx(turn_index=15, max_turns=14)) == "WRAP"


def test_pivot_only_when_a_beat_is_due():
    assert "PIVOT" not in allowed_moves(ctx(beat_due=False))
    assert "PIVOT" in allowed_moves(ctx(beat_due=True))
    assert move_weights(ctx(beat_due=True))["PIVOT"] > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(discourse_state="emotional"),
        dict(discourse_state="frustrated"),
        dict(energy="low"),
        dict(persona_humor="none"),
        dict(register="diplomatic"),
        dict(register="executive"),
        dict(turn_index=2),
    ],
)
def test_no_teasing_or_pushback_when_it_would_hurt(kwargs):
    allowed = allowed_moves(ctx(**kwargs))
    assert "TEASE" not in allowed
    if set(kwargs) & {"discourse_state", "energy"} or kwargs.get("turn_index") == 2:
        assert "PUSHBACK" not in allowed


def test_backchannel_rules():
    assert "BACKCHANNEL" not in allowed_moves(ctx(learner_words=2))
    assert "BACKCHANNEL" in allowed_moves(ctx(learner_words=2, discourse_state="storytelling"))
    assert "BACKCHANNEL" not in allowed_moves(ctx(learner_words=40, recent_moves=["SHARE", "BACKCHANNEL", "BUILD"]))
    w_story = move_weights(ctx(learner_words=50, discourse_state="storytelling"))
    assert max(w_story, key=w_story.get) in {"BACKCHANNEL", "REACT", "SHARE", "BUILD"}
    assert w_story["BACKCHANNEL"] >= 20


def test_a_direct_question_must_be_answered():
    allowed = allowed_moves(ctx(learner_asked_question=True, beat_due=True))
    assert not allowed & {"REACT", "BACKCHANNEL", "TEASE", "PIVOT"}
    assert allowed & {"SHARE", "BUILD"}


def test_low_energy_gets_material_not_an_interview():
    normal = move_weights(ctx())
    low = move_weights(ctx(energy="low"))
    assert low["SHARE"] / sum(low.values()) > normal["SHARE"] / sum(normal.values())
    assert low["ASK"] / sum(low.values()) < normal["ASK"] / sum(normal.values())


def test_director_exclusions_are_respected():
    for seed in range(30):
        m = choose_move(ctx(exclude={"REACT", "BACKCHANNEL", "ASK"}), random.Random(seed))
        assert m not in {"REACT", "BACKCHANNEL", "ASK"}


def test_react_is_not_machine_gunned():
    assert "REACT" not in allowed_moves(ctx(recent_moves=["REACT", "SHARE", "REACT", "BUILD"]))


def test_same_seed_same_sequence():
    assert simulate(30, 7) == simulate(30, 7)
    assert simulate(30, 7) != simulate(30, 8)


def test_question_ratio():
    assert question_ratio([]) == 0
    assert question_ratio(["ASK", "SHARE", "CLARIFY", "BUILD"]) == 0.5


def test_explain_is_consistent_with_allowed():
    e = explain(ctx())
    assert set(e["weights"]) <= set(e["allowed"])
    assert set(e["allowed"]) <= set(MOVES)


def test_lengths_respect_hard_caps_and_register():
    rng = random.Random(0)
    for _ in range(300):
        assert choose_length("REACT", "analytical", 60, rng) <= 12
        assert choose_length("BACKCHANNEL", "analytical", 60, rng) <= 5
        for reg in REGISTERS:
            assert 2 <= choose_length("SHARE", reg, 12, rng) <= 55
    avg = lambda reg: sum(choose_length("SHARE", reg, 12, random.Random(i)) for i in range(200)) / 200  # noqa: E731
    assert avg("analytical") > avg("casual")
    assert avg("diplomatic") > avg("executive")
