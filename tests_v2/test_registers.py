from __future__ import annotations

import pytest

from coach.db import Database
from coach.mastery import DAY, Event, Stage, compute_stage, stable_criteria_met
from coach.models import Expression, Listener, RegisterShiftSetup
from coach.registers import FORMALITY, fits, formality_of, required_formalities

from conftest import mk_finding

T0 = 1_800_000_000.0


def spont(day, session, register):
    return Event(ts=T0 + day * DAY, type="spontaneous", correct=True, session_id=session, register=register, naturalness=3)


def test_every_register_has_a_formality():
    from coach.models import REGISTERS

    assert set(REGISTERS) <= set(FORMALITY)
    assert [formality_of(r) for r in ("casual", "warm", "witty")] == ["informal"] * 3
    assert formality_of("diplomatic") == formality_of("executive") == "formal"
    assert formality_of("analytical") == "neutral" and formality_of(None) is None and formality_of("nope") is None


def test_casual_and_warm_are_one_formality_not_two_registers():
    uses = [spont(0, 1, "casual"), spont(4, 2, "warm"), spont(9, 3, "witty")]
    assert not stable_criteria_met(uses)                              # three labels, one formality
    assert stable_criteria_met(uses, min_formalities=1)
    assert compute_stage(uses, T0 + 10 * DAY) == Stage.PRODUCED_SPONTANEOUS
    mixed = [spont(0, 1, "casual"), spont(4, 2, "executive"), spont(9, 3, "warm")]
    assert compute_stage(mixed, T0 + 10 * DAY) == Stage.STABLE


def test_fits_uses_formality_and_treats_neutral_as_safe_anywhere():
    assert fits([], "diplomatic") and fits(["neutral"], "casual") and fits(["casual"], None)
    assert fits(["casual"], "warm") and fits(["casual"], "witty")
    assert not fits(["casual"], "diplomatic") and not fits(["diplomatic"], "casual")
    assert fits(["executive"], "diplomatic")                         # same formality
    assert not fits(["analytical"], "casual")


def test_required_formalities_respects_the_range_of_the_expression():
    assert required_formalities([]) == 2 and required_formalities(["neutral"]) == 2
    assert required_formalities(["diplomatic"]) == 1 and required_formalities(["casual", "warm"]) == 1
    assert required_formalities(["casual", "executive"]) == 2


def test_stage_of_asks_less_of_an_expression_that_has_one_natural_register_range():
    db = Database(":memory:")
    db.get_or_create_user(1, T0)
    db.upsert_expression(1, Expression(id="narrow", form="my plate's pretty full", register_tags=["casual", "warm"]), T0)
    db.upsert_expression(1, Expression(id="wide", form="I'll make the call", register_tags=["neutral"]), T0)
    db.upsert_expression(1, Expression(id="unknown", form="heads up", register_tags=[]), T0)
    for expr_id in ("narrow", "wide", "unknown"):
        for day, sess, reg in [(0, 1, "casual"), (4, 2, "warm"), (9, 3, "casual")]:
            db.record_event(1, expr_id, "spontaneous", T0 + day * DAY, correct=True, naturalness=3, register=reg, session_id=sess)
    now = T0 + 10 * DAY
    assert db.stage_of(1, "narrow", now) == Stage.STABLE             # it cannot be asked to appear somewhere unnatural
    assert db.stage_of(1, "wide", now) == Stage.PRODUCED_SPONTANEOUS
    assert db.stage_of(1, "unknown", now) == Stage.PRODUCED_SPONTANEOUS


def listener(i, register):
    return Listener(id=f"L{i}", name="x", relationship="y", register=register)


def test_register_shift_must_actually_shift_formality():
    ok = [listener(1, "casual"), listener(2, "diplomatic"), listener(3, "executive")]
    RegisterShiftSetup(title="t", intent="i", core_message="c", listeners=ok, learner_prompt="p")
    with pytest.raises(ValueError, match="differ in formality"):
        RegisterShiftSetup(title="t", intent="i", core_message="c", learner_prompt="p",
                           listeners=[listener(1, "casual"), listener(2, "warm"), listener(3, "witty")])
    with pytest.raises(ValueError, match="different registers"):
        RegisterShiftSetup(title="t", intent="i", core_message="c", learner_prompt="p",
                           listeners=[listener(1, "casual"), listener(2, "casual"), listener(3, "executive")])
