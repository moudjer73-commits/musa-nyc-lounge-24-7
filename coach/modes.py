"""Glue for the four structured practice modes (prompts/mode_*.md).

Register Shift, Pragmatic Dilemma, Think-Aloud and Phrase Workout each have a SETUP prompt that
designs the exercise and an EVALUATE prompt that judges the learner's attempt. This module:
  * runs a mode prompt and validates its JSON against the right model;
  * turns evaluations into mastery events with the honesty rule applied in code:
        drills write `prompted` events only. They can never write `spontaneous`, so they can
        never move an expression to PRODUCED_SPONTANEOUS or STABLE.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel

from .db import Database
from .llm import LLMClient
from .mastery import workout_event_fields
from .models import (
    DilemmaEval, DilemmaSetup, InterlocutorReply, PersonaCard, RegisterShiftEval, RegisterShiftSetup,
    ThinkAloudEval, ThinkAloudSetup, UsedTarget, WorkoutEval, WorkoutSetup,
)
from .prompt_loader import load_mode_prompt, voice_only_persona_json

MODE_SCHEMAS: dict[tuple[str, str], type[BaseModel]] = {
    ("register_shift", "SETUP"): RegisterShiftSetup,
    ("register_shift", "EVALUATE"): RegisterShiftEval,
    ("pragmatic_dilemma", "SETUP"): DilemmaSetup,
    ("pragmatic_dilemma", "EVALUATE"): DilemmaEval,
    ("think_aloud", "SETUP"): ThinkAloudSetup,
    ("think_aloud", "INTERLOCUTOR"): InterlocutorReply,
    ("think_aloud", "EVALUATE"): ThinkAloudEval,
    ("phrase_workout", "SETUP"): WorkoutSetup,
    ("phrase_workout", "EVALUATE"): WorkoutEval,
    ("phrase_workout", "REINFORCE"): WorkoutSetup,
}

MODE_TEMPERATURE = {"SETUP": 0.8, "REINFORCE": 0.8, "INTERLOCUTOR": 0.7, "EVALUATE": 0.2}


class ModeRunner:
    def __init__(self, llm: LLMClient, *, timeout: float = 30.0) -> None:
        self.llm = llm
        self.timeout = timeout

    async def run(self, mode: str, section: str, payload: dict[str, Any], *, persona: PersonaCard | None = None) -> Any:
        section = section.upper()
        schema = MODE_SCHEMAS[(mode, section)]
        variables: dict[str, Any] = {}
        if (mode, section) == ("think_aloud", "INTERLOCUTOR"):
            if persona is None:
                raise ValueError("the think-aloud interlocutor needs a persona")
            variables = {"persona_name": persona.name, "persona_card": voice_only_persona_json(persona)}
        system = load_mode_prompt(mode, section, **variables)
        return await self.llm.generate_json(
            role=f"{mode}.{section.lower()}",
            system=system,
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            schema=schema,
            temperature=MODE_TEMPERATURE[section],
            timeout=self.timeout,
        )


def record_workout_result(
    db: Database,
    user_id: int,
    expr_id: str,
    result: WorkoutEval,
    now: float,
    *,
    hint_level: int | None = None,
    register: str | None = None,
    context_id: str | None = None,
    session_id: int | None = None,
) -> int:
    """Write the event a Phrase Workout attempt produces (table in mode_phrase_workout.md)."""
    fields = workout_event_fields(result.outcome, naturalness=result.naturalness, hint_level=hint_level)
    return db.record_event(
        user_id, expr_id, fields["type"], now,
        correct=fields["correct"], outcome=fields["outcome"], naturalness=fields["naturalness"],
        register=register, hint_level=hint_level, context_id=context_id, session_id=session_id,
        mode="phrase_workout",
    )


def record_mode_used_targets(
    db: Database,
    user_id: int,
    used: Sequence[UsedTarget],
    now: float,
    *,
    mode: str,
    register: str | None = None,
    session_id: int | None = None,
    known_ids: set[str] | None = None,
) -> list[int]:
    """Targets the Analyst saw inside a structured mode count as `prompted`, never `spontaneous`."""
    ids: list[int] = []
    for u in used:
        if u.echo or (known_ids is not None and u.target_id not in known_ids):
            continue
        ids.append(
            db.record_event(
                user_id, u.target_id, "prompted", now,
                correct=u.correct, outcome="target_correct" if u.correct else "target_attempt",
                naturalness=u.naturalness, register=register, session_id=session_id, mode=mode,
            )
        )
    return ids
