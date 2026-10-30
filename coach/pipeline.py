"""One free-conversation turn, end to end.

    learner text
        -> log turn, build LearnerState (pattern counts are "before this turn")
        -> Analyst (low temperature, short timeout)  -> sanitize_analysis
        -> policy.decide(...)                         <- deterministic; the LLM never decides this
        -> persist findings + target events
        -> director.build_brief(...)                  <- move, length, recast, stealth target
        -> Responder (+ one silent retry on rule violations via check_reply)
        -> verify what the Responder claims (recast really present? beat really offered?)
        -> persist the character's turn, return bubbles (+ optional 💡 button)

Design note: the Analyst runs BEFORE the Responder (not in parallel) so that a silent recast can
land in the very next message. Recasts are most effective when immediate. If the Analyst times out or
fails, the turn degrades gracefully: no findings, no recast, the conversation continues.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from html import escape
from typing import Any, Callable

from .checks import (
    CARD_MAX_VISIBLE, DEBRIEF_MAX_VISIBLE, check_reply, first_word, forbidden_phrases_for, fuzzy_contains,
    normalize_text, real_questions, sanitize_analysis, validate_telegram_html, word_count,
)
from .db import Database, TargetCapError
from .director import build_brief
from .llm import DEFAULT_TEMPERATURES, LLMClient, LLMError
from .mastery import Stage, infer_formality_band, infer_relationship
from .models import (
    AlternativeScope, Analysis, Expression, Finding, HtmlOut, PersonaCard, PersonaScene, ResponderReply, SceneBible, TurnResult,
)
from .policy import CorrectionBudget, Decision, decide, suggest_drill_mode
from .prompt_loader import (
    analyst_system, card_writer_system, debrief_writer_system, responder_system, responder_user,
    scene_generator_system,
)


@dataclass
class PipelineConfig:
    analyst_timeout_s: float = 8.0
    responder_timeout_s: float = 25.0
    card_timeout_s: float = 15.0
    max_reply_retries: int = 1
    english_variety: str = "General American"
    transcript_turns: int = 8
    temperatures: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TEMPERATURES))


def hesitation_from_voice(voice_metrics: dict[str, Any] | None, words: int) -> float:
    """0..1 hesitation signal from speech-to-text metrics (0 for typed text).

    Weighted events per ten words: long pauses count double, restarts once, fillers and ordinary
    pauses half. Four weighted events per ten words saturates at 1.0. Tune on real voice notes.
    """
    if not voice_metrics:
        return 0.0
    weighted = (
        2.0 * voice_metrics.get("long_pause_count", 0)
        + 1.0 * voice_metrics.get("restart_count", 0)
        + 0.5 * voice_metrics.get("filler_count", 0)
        + 0.5 * voice_metrics.get("pause_count", 0)
    )
    rate = weighted / max(words / 10.0, 1.0)
    return round(min(1.0, rate / 4.0), 3)


def _excluded_registers_for(reg: str, nuance: str, best_for: str) -> list[str]:
    low = f"{nuance} {best_for}".lower()
    excluded: list[str] = []
    if reg in ("casual", "witty") or "friends" in low or "informal" in low or "peer" in low:
        if "manager" not in low and "any" not in low:
            excluded.extend(["executive", "diplomatic"])
    if reg == "executive" or "avoid with peers" in low or "formal" in low:
        excluded.extend(["casual", "warm", "witty"])
    return list(dict.fromkeys(excluded))


def expression_from_finding(f: Finding) -> Expression | None:
    """Turn the natural form a card or recast showed into a Phrase Vault item."""
    form = f.recast_form or (f.alternatives[0].text if f.alternatives else None)
    if not form:
        return None
    expr_id = "x_" + hashlib.sha1(normalize_text(form).encode()).hexdigest()[:10]
    short_alts = [a.text for a in f.alternatives if word_count(a.text) <= 7]
    tags = list(dict.fromkeys(a.register for a in f.alternatives if a.register != "neutral"))
    scopes: list[AlternativeScope] = []
    seen_forms: set[str] = set()
    for a in f.alternatives:
        if word_count(a.text) <= 7 and a.text not in seen_forms:
            seen_forms.add(a.text)
            scopes.append(
                AlternativeScope(
                    form=a.text,
                    register=a.register,
                    nuance=a.nuance,
                    best_for=a.best_for,
                    excluded_registers=_excluded_registers_for(a.register, a.nuance, a.best_for),
                )
            )
    if form not in seen_forms:
        primary_reg = f.alternatives[0].register if f.alternatives else "neutral"
        scopes.insert(
            0,
            AlternativeScope(
                form=form,
                register=primary_reg,
                nuance=f.recast_meaning or "",
                best_for="general spoken use",
                excluded_registers=[],
            ),
        )
    hint = f"a moment where the learner would naturally need to say something meaning: {f.recast_meaning}" if f.recast_meaning else None
    return Expression(
        id=expr_id,
        form=form,
        meaning=f.recast_meaning or "",
        register_tags=tags,
        l1_trap=f.span,
        acceptable_forms=list(dict.fromkeys([form, *short_alts])),
        form_scopes=scopes,
        opportunity_hint=hint,
    )


def _same_finding(a: Finding, b: Finding | None) -> bool:
    return b is not None and a.pattern_key == b.pattern_key and a.span == b.span


class CoachPipeline:
    def __init__(
        self,
        db: Database,
        llm: LLMClient,
        *,
        budget: CorrectionBudget | None = None,
        config: PipelineConfig | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.llm = llm
        self.budget = budget or CorrectionBudget()
        self.config = config or PipelineConfig()
        self.rng = rng or random.Random()
        self.clock = clock
        self._analyst_prompt = analyst_system()

    # ------------------------------------------------------------------ scenes

    async def create_scene(
        self, user_id: int, *, seed: str = "auto", register_hint: str = "auto",
        interests: list[str] | None = None, goals: list[str] | None = None, recent_scenes: list[str] | None = None,
    ) -> PersonaScene:
        user = self.db.get_user(user_id)
        targets = self.db.active_targets(user_id)
        payload = {
            "scenario_seed": seed,
            "learner_profile": {
                "cefr": user["cefr"], "interests": interests or [], "goals": goals or [], "dialect": user["dialect"],
            },
            "register_hint": register_hint,
            "stealth_targets": [
                {"id": t.id, "expression": t.form, "opportunity_hint": t.opportunity_hint or t.meaning} for t in targets
            ],
            "recent_scenes": recent_scenes or [],
            "english_variety": self.config.english_variety,
        }
        return await self.llm.generate_json(
            role="scene", system=scene_generator_system(), user=json.dumps(payload, ensure_ascii=False, indent=2),
            schema=PersonaScene, temperature=self.config.temperatures["scene"], timeout=self.config.responder_timeout_s,
        )

    def start_scene(self, user_id: int, persona: PersonaCard, scene: SceneBible) -> int:
        return self.db.start_session(user_id, "open_scene", self.clock(), persona=persona, scene=scene)

    async def open_scene(self, session_id: int) -> TurnResult:
        """The character speaks first, following scene.opening_intent."""
        now = self.clock()
        sess = self.db.get_session(session_id)
        user_id = sess["user_id"]
        persona, scene = self.db.load_persona_scene(session_id)
        state = self.db.build_learner_state(user_id, session_id, now)
        register = sess["register"] or scene.register_plan.default
        brief = build_brief(
            scene=scene, persona=persona, register=register, state=state, analysis=None, decision=None,
            persona_turns=[], learner_text="", used_beat_ids=set(), targets=[], due_ids=set(),
            finding_event_id=None, rng=self.rng, is_opening=True,
        )
        reply, violations = await self._respond(persona, scene, state, [], brief, [])
        self.db.log_turn(
            session_id, user_id, "persona", reply.text, now,
            words=word_count(reply.text),
            meta={"move": brief.dialogue_move, "opener": first_word(reply.bubbles[0]), "register": register,
                  "violations": violations},
        )
        return TurnResult(bubbles=reply.bubbles, speaker=persona.name, action="open", debug={"brief": brief.model_dump()})

    # ------------------------------------------------------------- the main turn

    async def handle_turn(
        self, session_id: int, text: str, *, modality: str = "text", voice_metrics: dict[str, Any] | None = None,
    ) -> TurnResult:
        now = self.clock()
        sess = self.db.get_session(session_id)
        user_id = sess["user_id"]
        persona, scene = self.db.load_persona_scene(session_id)
        register = sess["register"] or scene.register_plan.default
        wc = word_count(text)

        turn_id = self.db.log_turn(session_id, user_id, "learner", text, now, words=wc, modality=modality)
        state = self.db.build_learner_state(
            user_id, session_id, now, hesitation_ratio=hesitation_from_voice(voice_metrics, wc),
        )
        targets = self.db.active_targets(user_id)
        history = self.db.recent_turns(session_id, self.config.transcript_turns)
        persona_turns = self.db.persona_turns(session_id, 10)
        recent_bot = [t["text"] for t in persona_turns[-2:]]
        recent_cards = self.db.recent_card_text(session_id, 2)

        analysis, notes = await self._analyse(
            text, modality, voice_metrics, persona, scene, register, state, history, recent_bot, recent_cards, targets,
        )
        decision = decide(analysis, self.budget, state)

        finding_id = self._persist_findings(user_id, session_id, turn_id, analysis, decision, now)
        self._record_target_use(
            user_id, session_id, turn_id, analysis, register, state.mode, now,
            relationship=infer_relationship(persona.role_in_scene),
        )

        due_ids = {e.id for e in self.db.due_expressions(user_id, now)}
        brief = build_brief(
            scene=scene, persona=persona, register=register, state=state, analysis=analysis, decision=decision,
            persona_turns=persona_turns, learner_text=text, used_beat_ids=self.db.used_beat_ids(session_id),
            targets=targets, due_ids=due_ids, finding_event_id=finding_id, rng=self.rng,
        )
        forbidden = forbidden_phrases_for(
            [t for t in targets if t.id in {s.id for s in brief.stealth_targets}]
        )
        reply, violations = await self._respond(persona, scene, state, history, brief, forbidden)

        # verify what the Responder *claims*; the code is the source of truth
        recast_applied = bool(brief.recast) and fuzzy_contains(reply.text, brief.recast.natural_form)
        beat_used = (
            brief.scene_beat.id if brief.scene_beat and reply.scene_beat_used == brief.scene_beat.id else None
        )
        offered = {s.id for s in brief.stealth_targets}
        opportunities = [i for i in reply.opportunities_created if i in offered][:1]

        self.db.log_turn(
            session_id, user_id, "persona", reply.text, self.clock(),
            words=word_count(reply.text),
            meta={
                "move": brief.dialogue_move, "opener": first_word(reply.bubbles[0]), "register": register,
                "recast_id": brief.recast.id if recast_applied else None,
                "opportunity_ids": opportunities, "beat_id": beat_used, "violations": violations,
            },
        )
        for expr_id in opportunities:
            self.db.record_event(
                user_id, expr_id, "opportunity", now, register=register, session_id=session_id, turn_id=turn_id,
                mode=state.mode,
            )
        if recast_applied and decision.finding is not None:
            self._note_exposure(user_id, decision.finding, now, session_id, turn_id, register, activate=False)
        if beat_used:
            for shift in scene.register_plan.shifts:
                if shift.at_beat == beat_used:
                    self.db.set_register(session_id, shift.to)
        self.db.update_baseline_words(user_id, wc)
        self.db.retire_stable_targets(user_id, now)

        show_card = decision.action == "offer_card" and finding_id is not None
        return TurnResult(
            bubbles=reply.bubbles,
            speaker=persona.name,
            action=decision.action,
            card_finding_id=finding_id if show_card else None,
            proactive_card=bool(show_card and decision.finding and decision.finding.severity == 3),
            clarify=decision.action == "clarify",
            out_of_frame=analysis.discourse_state == "out_of_frame",
            scene_over=state.turn_index >= scene.max_turns,
            debug={
                "decision": {"action": decision.action, "reasons": list(decision.reasons), "scores": list(decision.scores)},
                "analysis_notes": notes,
                "brief": brief.model_dump(),
                "violations": violations,
                "recast_applied": recast_applied,
                "opportunities": opportunities,
                "drill_patterns": list(decision.drill_patterns),
                "drill_mode": suggest_drill_mode(decision.finding) if decision.finding and decision.drill_patterns else None,
            },
        )

    # ------------------------------------------------------------------ analyst

    async def _analyse(
        self, text, modality, voice_metrics, persona, scene, register, state, history, recent_bot, recent_cards, targets,
    ) -> tuple[Analysis, list[str]]:
        payload = {
            "learner_message": text,
            "modality": "voice" if modality == "voice" else "text",
            "voice_metrics": voice_metrics if modality == "voice" else None,
            "scene": {"register": register, "relationship": persona.role_in_scene, "situation_summary": scene.situation},
            "transcript": [
                {"speaker": persona.name if t["speaker"] == "persona" else "learner", "text": t["text"]}
                for t in history[:-1]
            ],
            "recent_bot_text": recent_bot,
            "recent_card_text": recent_cards,
            "learner_cefr": state.cefr,
            "learner_dialect": state.dialect,
            "active_targets": [
                {"id": t.id, "expression": t.form, "meaning": t.meaning, "acceptable_forms": t.acceptable_forms}
                for t in targets
            ],
            "known_patterns": self.db.known_patterns(state.user_id),
        }
        try:
            raw = await asyncio.wait_for(
                self.llm.generate_json(
                    role="analyst", system=self._analyst_prompt, user=json.dumps(payload, ensure_ascii=False, indent=2),
                    schema=Analysis, temperature=self.config.temperatures["analyst"], timeout=self.config.analyst_timeout_s,
                ),
                self.config.analyst_timeout_s + 1,
            )
        except (LLMError, asyncio.TimeoutError) as e:
            return Analysis(), [f"analyst unavailable: {e!r}"]
        return sanitize_analysis(
            raw, text, active_target_ids=[t.id for t in targets], recent_texts=[*recent_bot, *recent_cards],
        )

    def _persist_findings(
        self, user_id: int, session_id: int, turn_id: int, analysis: Analysis, decision: Decision, now: float,
    ) -> int | None:
        chosen_id: int | None = None
        deferred = list(decision.deferred)
        for f in analysis.findings:
            if _same_finding(f, decision.finding):
                action = decision.action
            elif any(_same_finding(f, d) for d in deferred):
                action = "defer"
            else:
                action = "silent"
            fid = self.db.log_finding(user_id, f, action, now, session_id=session_id, turn_id=turn_id)
            if _same_finding(f, decision.finding):
                chosen_id = fid
        for key in decision.drill_patterns:
            f = next((x for x in analysis.findings if x.pattern_key == key), None)
            if f is not None:
                self.db.queue_drill(user_id, key, suggest_drill_mode(f), now)
        return chosen_id

    def _record_target_use(
        self, user_id: int, session_id: int, turn_id: int, analysis: Analysis, register: str, mode: str, now: float,
        *, relationship: str | None = None,
    ) -> None:
        """Spontaneous events come ONLY from free scenes and ONLY when the Analyst saw non-echo use."""
        for u in analysis.used_targets:
            if u.echo:
                continue
            self.db.record_event(
                user_id, u.target_id, "spontaneous" if mode == "open_scene" else "prompted", now,
                correct=u.correct, outcome="target_correct" if u.correct else "target_attempt",
                naturalness=u.naturalness, register=register,
                formality=infer_formality_band(register), relationship=relationship,
                session_id=session_id, turn_id=turn_id, mode=mode,
            )

    # ---------------------------------------------------------------- responder

    async def _respond(
        self, persona: PersonaCard, scene: SceneBible, state, history: list[dict], brief, forbidden: list[str],
    ) -> tuple[ResponderReply, list[str]]:
        system = responder_system(persona, scene, cefr=state.cefr, english_variety=self.config.english_variety)
        transcript = [
            {"speaker": persona.name if t["speaker"] == "persona" else "Learner", "text": t["text"]}
            for t in history
        ]
        attempt = brief
        reply: ResponderReply | None = None
        violations: list[str] = []
        for _ in range(self.config.max_reply_retries + 1):
            user = responder_user(persona.name, transcript, attempt.model_dump_json(indent=2))
            reply = await self.llm.generate_json(
                role="responder", system=system, user=user, schema=ResponderReply,
                temperature=self.config.temperatures["responder"], timeout=self.config.responder_timeout_s,
            )
            violations = check_reply(reply, brief, forbidden_phrases=forbidden)
            if not violations:
                break
            attempt = brief.model_copy(update={"rewrite_notes": violations})
        assert reply is not None
        return reply, violations

    # ------------------------------------------------------------------ vault

    def _note_exposure(
        self, user_id: int, finding: Finding, now: float, session_id: int | None, turn_id: int | None,
        register: str | None, *, activate: bool,
    ) -> Expression | None:
        expr = expression_from_finding(finding)
        if expr is None:
            return None
        self.db.upsert_expression(user_id, expr, now, source="card" if activate else "recast")
        self.db.record_event(
            user_id, expr.id, "exposure", now, register=register, session_id=session_id, turn_id=turn_id,
            context_id=f"finding:{finding.pattern_key}",
        )
        if activate:
            try:
                self.db.activate_target(user_id, expr.id, now)
            except TargetCapError:
                pass  # the vault keeps it; it becomes a target when a slot frees up
        return expr

    # -------------------------------------------------------------------- cards

    async def build_card(self, finding_event_id: int, *, proactive: bool = False) -> str:
        """Telegram HTML for the 💡 card. Idempotent: a finding's card is written once."""
        finding, row = self.db.get_finding(finding_event_id)
        if row["card_html"]:
            return row["card_html"]
        now = self.clock()
        user = self.db.get_user(row["user_id"])
        persona, scene = self.db.load_persona_scene(row["session_id"])
        sess = self.db.get_session(row["session_id"])
        utterance = self.db.get_turn(row["turn_id"])["text"] if row["turn_id"] else finding.span
        include_arabic = bool(user["include_arabic"])
        payload = {
            "finding": finding.model_dump(),
            "learner_utterance": utterance,
            "scene": {
                "register": sess["register"] or scene.register_plan.default,
                "relationship": persona.role_in_scene,
                "situation_summary": scene.situation,
            },
            "learner_cefr": user["cefr"],
            "learner_dialect": user["dialect"],
            "include_arabic": include_arabic,
            "proactive": proactive,
        }
        html = ""
        problems: list[str] = ["not generated"]
        for _ in range(2):
            try:
                out = await self.llm.generate_json(
                    role="card", system=card_writer_system(), user=json.dumps(payload, ensure_ascii=False, indent=2),
                    schema=HtmlOut, temperature=self.config.temperatures["card"], timeout=self.config.card_timeout_s,
                )
            except LLMError:
                continue
            problems = validate_telegram_html(out.html, max_visible=CARD_MAX_VISIBLE, allow_arabic=include_arabic)
            if not problems:
                html = out.html
                break
        if not html:
            html = self._safe_card(finding)  # last resort: never show a broken or off-tone card
        self.db.save_card(finding_event_id, html, now)
        self._note_exposure(row["user_id"], finding, now, row["session_id"], row["turn_id"], payload["scene"]["register"], activate=True)
        return html

    @staticmethod
    def _safe_card(f: Finding) -> str:
        alts = " / ".join(f"<b>{escape(a.text)}</b>" for a in f.alternatives[:2])
        return f"💡 <i>{escape(f.span)}</i> → {alts}" if alts else f"💡 <i>{escape(f.span)}</i>"

    # ------------------------------------------------------------------ debrief

    async def end_scene(self, session_id: int) -> str:
        now = self.clock()
        sess = self.db.get_session(session_id)
        user = self.db.get_user(sess["user_id"])
        persona, scene = self.db.load_persona_scene(session_id)
        items = self.db.recap_items(session_id, 3)
        include_arabic = bool(user["include_arabic"])
        all_turns = self.db.recent_turns(session_id, limit=10000)
        learner_messages = [t for t in all_turns if t.get("speaker") == "learner"]
        learner_lengths = [word_count(t.get("text", "")) for t in learner_messages]
        used_beats = self.db.used_beat_ids(session_id)
        beats_reached = [b.id for b in scene.beats if b.id in used_beats]
        question_counts_per_turn = [len(real_questions(t.get("text", ""))) for t in learner_messages]
        registers_used = list(
            dict.fromkeys(
                t.get("meta", {}).get("register")
                for t in all_turns
                if t.get("speaker") == "persona" and t.get("meta", {}).get("register")
            )
        )
        session_facts = {
            "learner_turn_count": len(learner_messages),
            "average_learner_turn_words": round(sum(learner_lengths) / len(learner_lengths), 1) if learner_lengths else None,
            "longest_learner_turn_words": max(learner_lengths) if learner_lengths else None,
            "first_half_average_words": round(sum(learner_lengths[:max(1, len(learner_lengths)//2)]) / len(learner_lengths[:max(1, len(learner_lengths)//2)]), 1) if learner_lengths else None,
            "second_half_average_words": round(sum(learner_lengths[len(learner_lengths)//2:]) / len(learner_lengths[len(learner_lengths)//2:]), 1) if len(learner_lengths) >= 2 else None,
            "learner_questions_asked": sum(question_counts_per_turn),
            "learner_question_turns": sum(1 for c in question_counts_per_turn if c > 0),
            "beats_reached": beats_reached,
            "beats_reached_count": len(beats_reached),
            "beats_planned_count": len(scene.beats),
            "all_beats_reached": bool(scene.beats) and len(beats_reached) == len(scene.beats),
            "registers_used": registers_used,
        }
        if session_facts["first_half_average_words"] is not None and session_facts["second_half_average_words"] is not None:
            session_facts["turn_length_trend"] = (
                "longer" if session_facts["second_half_average_words"] > session_facts["first_half_average_words"] * 1.15
                else "shorter" if session_facts["second_half_average_words"] < session_facts["first_half_average_words"] * 0.85
                else "roughly steady"
            )
        payload = {
            "scene_title": scene.title,
            "register": sess["register"] or scene.register_plan.default,
            "turn_count": len(learner_messages),
            "session_facts": session_facts,
            "items": [f.model_dump() for _, f in items],
            "wins": [{"expression": w, "spontaneous": True} for w in self.db.session_wins(session_id)],
            "learner_cefr": user["cefr"],
            "include_arabic": include_arabic,
        }
        html = ""
        for _ in range(2):
            try:
                out = await self.llm.generate_json(
                    role="debrief", system=debrief_writer_system(), user=json.dumps(payload, ensure_ascii=False, indent=2),
                    schema=HtmlOut, temperature=self.config.temperatures["debrief"], timeout=self.config.card_timeout_s,
                )
            except LLMError:
                continue
            if not validate_telegram_html(out.html, max_visible=DEBRIEF_MAX_VISIBLE, allow_arabic=include_arabic):
                html = out.html
                break
        self.db.mark_recapped([i for i, _ in items], now)
        self.db.end_session(session_id, now)
        self.db.retire_stable_targets(sess["user_id"], now)
        return html
