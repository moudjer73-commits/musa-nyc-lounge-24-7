"""Learner state as the policy sees it, and the "strain" signal.

Strain is how much the learner is struggling *right now*. The correction budget widens
(fewer visible corrections) when strain is high, which is the code form of "if answers get
shorter or hesitation grows, raise the intervention threshold".
"""

from __future__ import annotations

from statistics import mean
from typing import Sequence

from pydantic import BaseModel, Field

from .models import Cefr, Strictness


def compute_strain(
    recent_words: Sequence[int],
    baseline_words: float,
    hesitation_ratio: float = 0.0,
    window: int = 3,
) -> float:
    """0.0 (comfortable) .. 1.0 (struggling).

    length_drop: how far the average of the last `window` messages fell below the learner's
                 own running baseline. Shorter than usual is the earliest sign of trouble.
    hesitation_ratio: caller-supplied 0..1 (e.g. long pauses + restarts per word from voice
                 metrics; 0 for text).
    The result is the larger of the two so either signal alone can raise the threshold.
    """
    length_drop = 0.0
    if recent_words and baseline_words > 0:
        avg = mean(recent_words[-window:])
        length_drop = max(0.0, 1.0 - avg / baseline_words)
    hesitation = min(1.0, max(0.0, hesitation_ratio))
    return round(min(1.0, max(length_drop, hesitation)), 3)


class LearnerState(BaseModel):
    user_id: int
    cefr: Cefr = "B2"
    dialect: str | None = None
    include_arabic: bool = True
    strictness: Strictness = "normal"
    mode: str = "open_scene"

    turn_index: int = 0                 # learner turns in this session, counting the current one
    turns_since_card: int = 99          # learner turns since a visible correction offer
    cards_this_session: int = 0
    baseline_words: float = 12.0
    recent_words: list[int] = Field(default_factory=list)
    hesitation_ratio: float = 0.0

    pattern_counts: dict[str, int] = Field(default_factory=dict)   # by pattern_key, BEFORE this turn
    drilled_recently: list[str] = Field(default_factory=list)      # pattern_keys on drill cooldown
    queued_drills: list[str] = Field(default_factory=list)         # pattern_keys already waiting

    @property
    def strain(self) -> float:
        return compute_strain(self.recent_words, self.baseline_words, self.hesitation_ratio)

    @property
    def energy(self) -> str:
        s = self.strain
        if s >= 0.5:
            return "low"
        if s <= 0.05 and self.recent_words and self.recent_words[-1] >= 1.6 * self.baseline_words:
            return "high"
        return "normal"
