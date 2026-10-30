"""Spoken-English coach for L1 Arabic learners.

Design rule that runs through the whole package: the LLM proposes, the code decides.
Nothing the learner sees about *whether* or *when* to be corrected comes from a model's mood;
it comes from `policy.decide`, the correction budget, the dialogue-move sampler and the
event-sourced mastery ladder in `mastery.py`.
"""

__all__ = [
    "models",
    "state",
    "prompt_loader",
    "move_sampler",
    "policy",
    "mastery",
    "db",
    "checks",
    "director",
    "llm",
    "pipeline",
]
