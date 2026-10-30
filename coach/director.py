"""The director: turns "what just happened" into the brief the Responder must obey.

Everything the Responder is told to do on a turn is decided here, in code:
  * which dialogue move (via the Dialogue-Move Sampler),
  * how long the reply should be and whether it may end with a question,
  * whether to weave in a silent recast of the learner's last phrasing,
  * whether to create an opportunity for a stealth target (never together with a recast),
  * which scene beat the character may use, and which opening words to avoid.
"""

from __future__ import annotations

import random
from typing import Sequence

from .checks import ends_with_question, first_word
from .models import (
    Analysis, BeatBrief, DirectorBrief, Expression, LearnerSignal, PersonaCard, RecastBrief,
    SceneBible, StealthTarget,
)
from .move_sampler import MoveContext, choose_length, choose_move
from .policy import Decision
from .registers import fits
from .state import LearnerState

OPPORTUNITY_COOLDOWN_TURNS = 2     # no new stealth opportunity within this many character turns of the last
MAX_TARGET_CANDIDATES = 2


def _recent_moves(persona_turns: Sequence[dict]) -> list[str]:
    return [t["meta"]["move"] for t in persona_turns if t.get("meta", {}).get("move")]


def _opportunity_cooled_down(persona_turns: Sequence[dict]) -> bool:
    recent = list(persona_turns)[-OPPORTUNITY_COOLDOWN_TURNS:]
    return not any(t.get("meta", {}).get("opportunity_ids") for t in recent)


def pick_targets(
    targets: Sequence[Expression],
    scene: SceneBible,
    register: str,
    due_ids: set[str],
) -> list[StealthTarget]:
    """Up to two candidates that have a usable opportunity hint, items due for review first."""
    slots = {s.target_id: s.slot for s in scene.need_slots}
    ranked = sorted(targets, key=lambda e: (e.id not in due_ids,))
    out: list[StealthTarget] = []
    for e in ranked:
        hint = slots.get(e.id) or e.opportunity_hint
        if not hint:
            continue
        if not fits(e.register_tags, register):
            continue          # never create a need for a phrase that would be out of place in this scene
        out.append(
            StealthTarget(
                id=e.id,
                expression=e.form,
                meaning=e.meaning,
                register=register,           # `fits` above guarantees the phrase is natural here
                opportunity_hint=hint,
            )
        )
        if len(out) == MAX_TARGET_CANDIDATES:
            break
    return out


def build_brief(
    *,
    scene: SceneBible,
    persona: PersonaCard,
    register: str,
    state: LearnerState,
    analysis: Analysis | None,
    decision: Decision | None,
    persona_turns: Sequence[dict],
    learner_text: str,
    used_beat_ids: set[str],
    targets: Sequence[Expression],
    due_ids: set[str],
    finding_event_id: int | None,
    rng: random.Random,
    is_opening: bool = False,
) -> DirectorBrief:
    analysis = analysis or Analysis()
    discourse = analysis.discourse_state
    turn_index = state.turn_index
    closing = (not is_opening) and turn_index >= scene.max_turns

    recent_moves = _recent_moves(persona_turns)
    due_beats = [b for b in scene.beats if b.id not in used_beat_ids and b.earliest_turn <= turn_index]
    beat_due = bool(due_beats) and not closing and not is_opening

    forced = "CLARIFY" if (decision and decision.action == "clarify") else None

    # a silent recast needs a move with room to carry it
    recast: RecastBrief | None = None
    if (
        decision
        and decision.wants_recast
        and decision.finding is not None
        and decision.finding.recast_form
        and not closing
        and not is_opening
        and forced is None
    ):
        f = decision.finding
        recast = RecastBrief(
            id=f"rc_{finding_event_id}" if finding_event_id is not None else f"rc_t{turn_index}",
            learner_said=f.span,
            natural_form=f.recast_form,
            meaning=f.recast_meaning or "",
        )

    stealth: list[StealthTarget] = []
    if (
        recast is None
        and not closing
        and not is_opening
        and forced is None
        and discourse == "normal"
        and _opportunity_cooled_down(persona_turns)
    ):
        stealth = pick_targets(targets, scene, register, due_ids)

    exclude: set[str] = set()
    if recast is not None:
        exclude |= {"REACT", "BACKCHANNEL"}
    if stealth:
        exclude |= {"REACT", "BACKCHANNEL", "TEASE"}

    ctx = MoveContext(
        register=register,
        recent_moves=recent_moves,
        turn_index=turn_index,
        max_turns=scene.max_turns,
        learner_words=len(learner_text.split()),
        learner_asked_question="?" in learner_text,
        discourse_state=discourse,
        energy=state.energy,
        persona_humor=persona.voice.humor,
        beat_due=beat_due,
        forced=forced,
        exclude=exclude,
    )
    move = "SHARE" if is_opening else choose_move(ctx, rng)
    length = choose_length(move, register, ctx.learner_words, rng)

    scene_beat = BeatBrief(id=due_beats[0].id, description=due_beats[0].description) if (move == "PIVOT" and due_beats) else None
    openers: list[str] = []
    for t in list(persona_turns)[-3:]:
        w = first_word(t["text"])
        if w and w not in openers:
            openers.append(w)

    return DirectorBrief(
        is_opening=is_opening,
        register=register,
        dialogue_move=move,
        length_words=length,
        end_with_question=move in ("ASK", "CLARIFY"),
        recast=recast,
        stealth_targets=stealth,
        scene_beat=scene_beat,
        learner_signal=LearnerSignal(
            energy=state.energy,
            out_of_frame=discourse == "out_of_frame",
            comprehension_issue=analysis.clarification_needed,
        ),
        avoid_openers=openers,
        clarification_focus=analysis.clarification_focus if move == "CLARIFY" else None,
    )
