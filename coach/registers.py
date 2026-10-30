"""What a register label means for the CODE.

The six registers (casual, warm, witty, diplomatic, executive, analytical) are performance
profiles for the Responder: bundles of dials that make a character sound a certain way. They are
not six points on one scale. `warm` and `witty` are tones, `analytical` is a way of building talk,
and only casual/diplomatic/executive differ mainly in how formal the situation is.

Anything that counts or compares registers (mastery, metrics, eligibility of an expression for a
scene) therefore uses `formality`, an axis with three buckets, instead of the label:

    informal   casual, warm, witty
    neutral    analytical, neutral
    formal     diplomatic, executive

`neutral` as an Expression tag means "safe in any register". It is the only tag that is not tied to
a bucket.
"""

from __future__ import annotations

from typing import Mapping, Sequence

FORMALITY: Mapping[str, str] = {
    "casual": "informal",
    "warm": "informal",
    "witty": "informal",
    "analytical": "neutral",
    "neutral": "neutral",
    "diplomatic": "formal",
    "executive": "formal",
}
FORMALITIES: tuple[str, ...] = ("informal", "neutral", "formal")


def formality_of(register: str | None) -> str | None:
    return FORMALITY.get(register) if register else None


def fits(register_tags: Sequence[str], register: str | None) -> bool:
    """Is an expression tagged `register_tags` natural in a situation of `register`?

    No tags means "not known": nothing is excluded. A `neutral` tag means "safe anywhere". Otherwise
    the expression fits any register of the same formality as one of its tags (a phrase tagged
    `casual` is also fine in a `warm` scene; it is not fine in a `diplomatic` one).
    """
    if not register_tags or register is None or "neutral" in register_tags:
        return True
    wanted = formality_of(register)
    return any(formality_of(t) == wanted for t in register_tags)


def required_formalities(register_tags: Sequence[str]) -> int:
    """How many distinct formality buckets a learner must use an expression in before it counts as STABLE.

    Two is the rule (transfer across situations), but an expression that is only natural in one
    bucket cannot be asked to appear in a second one. Unknown tags keep the strict rule.
    """
    if not register_tags or "neutral" in register_tags:
        return 2
    buckets = {formality_of(t) for t in register_tags if formality_of(t)}
    return max(1, min(2, len(buckets)))
