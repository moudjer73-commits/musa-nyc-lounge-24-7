"""orchestrator.py (V3.0, owner Claude): one learner turn, end to end (S0 6, A13, A14).

``process_turn`` accepts the message (one transaction: ``learner_message_received`` and ``session_revision`` + 1),
asks the Responder, stores the reply and returns at once; the Analyst runs in the background and its future is
on the result. ``apply_analysis_outcome`` then validates nothing itself (``validation.validate_analysis`` already
did), asks ``feedback_policy`` what to show, and writes the analysis, the target assessments, the policy decision
and any P0 follow-up in ONE compare-and-set on ``session_revision``. If the learner has already sent another scene
turn the revision has moved: the findings are written as deferred (they reach the Review queue) and a P0 line is
kept in ``sessions.pending_clarification_json`` for the next Responder brief (A14).

``app.py`` never writes events and never touches revisions; it holds the learner lock around these two calls.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional

import interaction
import retrieval
import validation as V
from ai_engine import DualLLMEngine, is_trivial_message
from database import EventSourcedDatabase, EventSpec
from dialogue import DialogueController
from feedback_policy import PolicyInputs, RuntimeState, resolve_interaction_policy
from policy_engine import (
    compute_learner_strain,
    extract_recent_partner_openers,
    get_active_workout_task,
    update_workout_task_after_turn,
)
from schemas import (
    Analysis,
    AnalysisSkippedV2,
    AnalysisV2,
    ComprehensionStatus,
    Delivery,
    DirectnessLevel,
    FeedbackPreference,
    PersonaCard,
    PolicyDecisionV2,
    ProductionEvidence,
    RegisterProfile,
    ResponderOutput,
    ResponderReplyV2,
    SceneBible,
    SceneMode,
    SkipReason,
    TrainingMode,
    TurnOrigin,
)

logger = logging.getLogger(__name__)

_TURN_EXECUTOR = ThreadPoolExecutor(max_workers=6)
ANALYSIS_WAIT_S = 35.0  # S0 6: app.py waits this long for the background Analyst
_CONTEXT_CACHE_SIZE = 200
_CREDITED_EVIDENCE = (ProductionEvidence.INDEPENDENT_PRODUCTION, ProductionEvidence.SPONTANEOUS_PRODUCTION)


# ------------------------------------------------------------------------------ results (S0 6)


@dataclass(frozen=True)
class TurnHandle:
    turn_event_id: str
    reply_event_seq: int
    turn_session_revision: int
    correlation_id: str
    session_id: str = ""
    learner_id: int = 0


@dataclass(frozen=True)
class AnalysisOutcome:
    status: Literal["ok", "skipped"]
    skip_reason: Optional[SkipReason] = None
    validated: Optional[V.ValidationResult] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class PostTurnActions:
    followup_text: Optional[str] = None
    attach_upgrade_button: bool = False
    upgrade_callback: Optional[str] = None
    deferred_count: int = 0
    card_id: Optional[str] = None


class TurnResult:
    """The live v2 fields plus the V3 ones. At return time the Analyst has not finished, so the policy fields
    are empty; ``apply_analysis_outcome`` returns what to show."""

    def __init__(
        self,
        *,
        correlation_id: str,
        session_id: str,
        responder_output: Optional[ResponderOutput],
        handle: Optional[TurnHandle],
        analysis_future: Optional["Future[AnalysisOutcome]"],
        comprehension_status: ComprehensionStatus = ComprehensionStatus.OK,
        duplicate: bool = False,
    ):
        self.correlation_id = correlation_id
        self.session_id = session_id
        self.responder_output = responder_output
        self.policy_decision: Optional[PolicyDecisionV2] = None
        self.selected_diagnostic = None
        self.all_diagnostics: list[Any] = []
        self.card_id: Optional[str] = None
        self.handle = handle
        self.analysis_future = analysis_future
        self.comprehension_status = comprehension_status
        self.duplicate = duplicate


@dataclass
class _TurnContext:
    session_id: str
    learner_text: str
    register_profile: str
    feedback_preference: FeedbackPreference
    comprehension_status: ComprehensionStatus
    primary_context_family: str = "general"
    input_modality: str = "text"


def skip_reason_for(err: BaseException) -> SkipReason:
    """Map a failed Analyst call to the stored reason: a missed deadline, a quota shed, or anything else."""
    if isinstance(err, TimeoutError):
        return SkipReason.DEADLINE
    shed = tuple(c for c in retrieval.default_shed_exceptions() if c not in (retrieval.JudgeShed, TimeoutError))
    if shed and isinstance(err, shed):
        return SkipReason.SHED
    return SkipReason.ERROR


def _new_event_id() -> str:
    return f"ev_{uuid.uuid4().hex[:12]}"


class TurnOrchestrator:
    def __init__(
        self,
        db: EventSourcedDatabase,
        ai: DualLLMEngine,
        *,
        executor: Optional[ThreadPoolExecutor] = None,
    ):
        self.db = db
        self.ai = ai
        self.default_persona = PersonaCard()
        self._executor = executor or _TURN_EXECUTOR
        self._contexts: "OrderedDict[str, _TurnContext]" = OrderedDict()
        self._contexts_lock = threading.Lock()

    # ------------------------------------------------------------------ the turn

    def process_turn(
        self,
        *,
        learner_id: int,
        learner_text: str,
        message_idempotency_key: Optional[str] = None,
        is_voice_message: bool = False,
        origin: str = "scene",
        interaction_id: Optional[int] = None,
        telegram_message_id: Optional[int] = None,
        on_fast_reply: Optional[Callable[[ResponderOutput, TurnHandle], None]] = None,
    ) -> TurnResult:
        corr_id = f"turn_{uuid.uuid4().hex[:10]}"
        idem_base = message_idempotency_key or corr_id

        learner_cfg = self.db.get_learner_settings(learner_id)
        feedback_pref = _preference(learner_cfg.get("feedback_preference"))
        session = self.db.get_or_create_active_session(learner_id)
        session_id = str(session["session_id"])
        training_mode = TrainingMode(session.get("training_mode", "open_scene"))
        scene_mode = SceneMode(session.get("scene_mode", "real_conversation"))
        register_profile = RegisterProfile(session.get("register_profile", "casual"))
        directness = DirectnessLevel(session.get("directness", "balanced"))
        scene_bible = self._scene_bible(session, session_id, scene_mode)

        recent_history = self.db.get_recent_dialogue_history(session_id, limit=6)
        runtime = self.db.get_session_runtime_state(session_id, learner_id)

        msg, revision = self.db.accept_scene_turn(
            learner_id=learner_id,
            session_id=session_id,
            text=learner_text,
            idempotency_key=f"{idem_base}:msg",
            origin=TurnOrigin(origin),
            correlation_id=corr_id,
            input_modality="voice" if is_voice_message else "text",
            interaction_id=interaction_id,
            telegram_message_id=telegram_message_id,
        )
        if not msg.inserted:  # the same Telegram message delivered twice
            return TurnResult(
                correlation_id=corr_id, session_id=session_id, responder_output=None, handle=None,
                analysis_future=None, duplicate=True,
            )

        pending_clarification = self.db.take_pending_clarification(session_id)
        learner_turns = [h["text"] for h in recent_history if h.get("role") == "learner"] + [learner_text]
        partner_turns = [h["text"] for h in recent_history if h.get("role") == "partner"]
        strain = compute_learner_strain(learner_turns)
        fast_intent = "ask_opinion" if "?" in learner_text else "share_comment"
        decision = DialogueController.select_dialogue_decision(
            learner_text=learner_text,
            analysis=Analysis(communicative_intention=fast_intent, learner_invites_question=("?" in learner_text)),
            recent_optional_question_flags=runtime["recent_questions"],
            register=register_profile,
            training_mode=training_mode,
        ).model_copy(update={"avoid_openers": extract_recent_partner_openers(partner_turns), "strain_score": strain})
        if pending_clarification:  # A14: a P0 line the learner never saw goes into this brief
            decision = decision.model_copy(update={"allow_question": True, "max_questions": 1})

        candidates = self._spontaneous_targets(learner_id, register_profile.value)
        workout_task = get_active_workout_task(learner_id)
        input_modality = "voice" if is_voice_message else "text"

        if is_trivial_message(learner_text):
            analysis_future: "Future[AnalysisOutcome]" = Future()
            analysis_future.set_result(AnalysisOutcome("skipped", SkipReason.TRIVIAL))
        else:
            analysis_future = self._executor.submit(
                self._analyze,
                learner_id=learner_id,
                learner_text=learner_text,
                recent_history=recent_history,
                register_profile=register_profile,
                directness=directness,
                scene_bible=scene_bible,
                candidates=candidates,
                workout_task=workout_task,
                turn_event_id=msg.event_id,
                correlation_id=corr_id,
                input_modality=input_modality,
            )

        try:
            result = self.ai.run_responder_v3(
                learner_text=learner_text,
                recent_history=recent_history,
                persona=self.default_persona,
                scene_bible=scene_bible,
                register_profile=register_profile,
                directness=directness,
                training_mode=training_mode,
                dialogue_decision=decision,
                advisory_recast_candidate=None,
                stealth_target_info=candidates[0] if candidates else None,
                active_workout_task=workout_task,
                pending_clarification=pending_clarification,
                correlation_id=corr_id,
            )
        except BaseException:
            analysis_future.cancel()
            if pending_clarification:  # the learner has not seen it yet
                self.db.set_pending_clarification(session_id, pending_clarification)
            raise

        reply = result.output
        reply_event = self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="responder_reply_delivered",
            payload=ResponderReplyV2(
                kind="reply",
                turn_event_id=msg.event_id,
                reply_text=reply.reply_text,
                second_bubble_text=reply.second_bubble_text,
                comprehension_status=result.comprehension_status,
                recast_used=reply.recast_used,
                elicitation_used=reply.elicitation_used,
                call_meta=result.call_meta,
            ),
            correlation_id=corr_id,
            causation_id=msg.event_id,
            idempotency_key=f"{idem_base}:resp",
            rebuild=True,
        )
        handle = TurnHandle(
            turn_event_id=msg.event_id,
            reply_event_seq=reply_event.event_seq,
            turn_session_revision=revision,
            correlation_id=corr_id,
            session_id=session_id,
            learner_id=learner_id,
        )
        self._remember(
            msg.event_id,
            _TurnContext(
                session_id=session_id,
                learner_text=learner_text,
                register_profile=register_profile.value,
                feedback_preference=feedback_pref,
                comprehension_status=result.comprehension_status,
                primary_context_family=str(session.get("primary_context_family") or "general"),
                input_modality=input_modality,
            ),
        )
        if on_fast_reply is not None:
            try:
                on_fast_reply(reply, handle)
            except Exception:  # delivery trouble must not lose the turn
                logger.warning("on_fast_reply failed", exc_info=True)
        out = TurnResult(
            correlation_id=corr_id,
            session_id=session_id,
            responder_output=reply,
            handle=handle,
            analysis_future=analysis_future,
            comprehension_status=result.comprehension_status,
        )
        return out

    # ------------------------------------------------------------------ the Analyst (background)

    def _analyze(
        self,
        *,
        learner_id: int,
        learner_text: str,
        recent_history: list[dict[str, str]],
        register_profile: RegisterProfile,
        directness: DirectnessLevel,
        scene_bible: SceneBible,
        candidates: list[dict[str, str]],
        workout_task: Optional[dict[str, Any]],
        turn_event_id: str,
        correlation_id: str,
        input_modality: str,
    ) -> AnalysisOutcome:
        try:
            raw, meta = self.ai.run_analyst_v3(
                learner_text=learner_text,
                recent_history=recent_history,
                register_profile=register_profile,
                directness=directness,
                scene_bible=scene_bible,
                active_targets=[
                    {"target_id": c["phrase_id"], "canonical_form": c["canonical_form"]} for c in candidates
                ],
                active_workout_task=workout_task,
                correlation_id=correlation_id,
            )
        except Exception as err:
            logger.warning("Analyst unavailable: %s", err)
            return AnalysisOutcome("skipped", skip_reason_for(err), None, str(err)[:200])
        try:
            validated = V.validate_analysis(
                raw,
                turn_event_id=turn_event_id,
                learner_text=learner_text,
                input_modality=input_modality,
                recent_bot_texts=self._recent_bot_texts(learner_id, recent_history),
                active_target_ids={c["phrase_id"] for c in candidates},
                prior_counts=self.db.get_authoritative_occurrence_counts(learner_id),
                learner_id=learner_id,
                taken_ids=self.db.taken_diagnostic_ids(),
                call_meta=meta,
            )
        except Exception as err:  # validation never raises on bad model output; this is a code bug
            logger.error("validate_analysis failed", exc_info=True)
            return AnalysisOutcome("skipped", SkipReason.ERROR, None, str(err)[:200])
        return AnalysisOutcome("ok", None, validated)

    # ------------------------------------------------------------------ apply (A14)

    def apply_analysis_outcome(
        self,
        *,
        learner_id: int,
        handle: TurnHandle,
        outcome: AnalysisOutcome,
        now: Optional[datetime] = None,
    ) -> PostTurnActions:
        now = now or self.db.clock.now()
        ctx = self._context_for(handle)
        session_id = handle.session_id or ctx.session_id

        if outcome.status != "ok" or outcome.validated is None:
            self.db.append_event_ex(
                learner_id=learner_id,
                session_id=session_id,
                event_type="analysis_skipped",
                payload=AnalysisSkippedV2(
                    turn_event_id=handle.turn_event_id, reason=outcome.skip_reason or SkipReason.ERROR, detail=outcome.detail
                ),
                correlation_id=handle.correlation_id,
                causation_id=handle.turn_event_id,
                idempotency_key=f"{handle.correlation_id}:skipped",
            )
            return PostTurnActions()

        validated = outcome.validated
        analysis = validated.analysis
        if get_active_workout_task(learner_id) is not None:
            update_workout_task_after_turn(learner_id, bool(analysis.communicative.communicative_goal_achieved))

        policy = resolve_interaction_policy(self._policy_inputs(learner_id, handle, ctx, analysis, now), validated.scored)

        analysis_id = _new_event_id()
        specs: list[EventSpec] = [
            EventSpec(
                event_type="analyst_diagnosis_recorded",
                payload=analysis,
                idempotency_key=f"{handle.correlation_id}:diag",
                correlation_id=handle.correlation_id,
                causation_id=handle.turn_event_id,
                event_id=analysis_id,
                rebuild=False,
            )
        ]
        specs += self._target_specs(learner_id, handle, ctx, analysis, analysis_id)

        late = False
        reserved: list[EventSpec] = []
        p0_followup_text: Optional[str] = None
        had_immediate_p0 = False
        if policy.selected_diagnostic_id is not None:
            selected = next((d for d in policy.decisions if d.diagnostic_id == policy.selected_diagnostic_id), None)
            if selected is not None and selected.delivery == Delivery.IMMEDIATE_FOLLOWUP:
                p0_followup_text = analysis.p0.line
                had_immediate_p0 = bool(p0_followup_text)
                if p0_followup_text:
                    reserved.append(
                        EventSpec(
                            event_type="responder_reply_delivered",
                            payload=ResponderReplyV2(
                                kind="p0_followup", turn_event_id=handle.turn_event_id, reply_text=p0_followup_text,
                                comprehension_status=ctx.comprehension_status,
                            ),
                            idempotency_key=f"{handle.correlation_id}:p0",
                            correlation_id=handle.correlation_id,
                            causation_id=handle.turn_event_id,
                            rebuild=False,
                        )
                    )
        policy_spec = EventSpec(
            event_type="policy_decision_recorded",
            payload=policy,
            idempotency_key=f"{handle.correlation_id}:pol",
            correlation_id=handle.correlation_id,
            causation_id=analysis_id,
            rebuild=True,
        )
        results = self.db.append_events_if_revision_ex(
            session_id, handle.turn_session_revision, [*specs, *reserved, policy_spec]
        )
        if results is None:  # the learner moved on: deferred, and a P0 line waits for the next brief
            late = True
            policy = _as_late(policy)
            p0_followup_text = None
            for spec in specs:
                self.db.append_event_ex(
                    learner_id=learner_id,
                    session_id=session_id,
                    event_type=spec.event_type,
                    payload=spec.payload,
                    correlation_id=spec.correlation_id,
                    causation_id=spec.causation_id,
                    idempotency_key=spec.idempotency_key,
                    event_id=spec.event_id,
                    rebuild=False,
                )
            self.db.append_event_ex(
                learner_id=learner_id,
                session_id=session_id,
                event_type="policy_decision_recorded",
                payload=policy,
                correlation_id=handle.correlation_id,
                causation_id=analysis_id,
                idempotency_key=f"{handle.correlation_id}:pol",
                rebuild=True,
            )
        if policy.pending_clarification:
            self.db.set_pending_clarification(session_id, policy.pending_clarification)
        elif late and had_immediate_p0:
            self.db.set_pending_clarification(session_id, analysis.p0.line)

        selected_id = policy.selected_diagnostic_id
        selected_decision = next((d for d in policy.decisions if d.diagnostic_id == selected_id), None) if selected_id else None
        attach = selected_decision is not None and selected_decision.delivery == Delivery.END_OF_TURN
        return PostTurnActions(
            followup_text=p0_followup_text,
            attach_upgrade_button=attach,
            upgrade_callback=interaction.build_callback("ug", handle.reply_event_seq, handle.turn_session_revision) if attach else None,
            deferred_count=sum(1 for d in policy.decisions if d.delivery == Delivery.DEFERRED),
            card_id=selected_id if attach else None,
        )

    # ------------------------------------------------------------------ pieces

    def _policy_inputs(
        self, learner_id: int, handle: TurnHandle, ctx: _TurnContext, analysis: AnalysisV2, now: datetime
    ) -> PolicyInputs:
        session_id = handle.session_id or ctx.session_id
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT turns_since_visible_card, cards_in_scene, turns_since_p0 FROM session_state_projection WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        history = self.db.get_recent_dialogue_history(session_id, limit=6)
        strain = compute_learner_strain([h["text"] for h in history if h.get("role") == "learner"])
        runtime = RuntimeState(
            turns_since_visible_card=int(row["turns_since_visible_card"]) if row else 4,
            cards_in_scene=int(row["cards_in_scene"]) if row else 0,
            turns_since_p0=int(row["turns_since_p0"]) if row else 99,
            strain=min(max(float(strain), 0.0), 1.0),
        )
        return PolicyInputs(
            turn_event_id=handle.turn_event_id,
            scene_turn_index=max(1, self.db.get_debrief_counts(session_id).turns),
            feedback_preference=ctx.feedback_preference,
            comprehension_status=ctx.comprehension_status,
            responder_asked_clarification=ctx.comprehension_status != ComprehensionStatus.OK,
            communicative=analysis.communicative,
            p0=analysis.p0,
            runtime=runtime,
            has_active_interaction=interaction.get_active_interaction(self.db, learner_id, now) is not None,
        )

    def _target_specs(
        self, learner_id: int, handle: TurnHandle, ctx: _TurnContext, analysis: AnalysisV2, analysis_id: str
    ) -> list[EventSpec]:
        """Silent spontaneous detection. The Analyst proposes, Python decides: credit needs the catalog form (or an
        acceptable form) to be in the learner's own text, and echo was already downgraded by validation."""
        specs: list[EventSpec] = []
        for idx, ta in enumerate(analysis.target_assessments):
            if ta.evidence == ProductionEvidence.NONE:
                continue
            if ta.evidence in _CREDITED_EVIDENCE:
                entry = self.db.get_phrase_entry(ta.target_id)
                if entry is None or retrieval.find_match(ctx.learner_text, entry.target()) is None:
                    continue
            specs.append(
                EventSpec(
                    event_type="phrase_production_assessed",
                    payload={
                        **ta.model_dump(mode="json"),
                        "register_profile": ctx.register_profile,
                        "training_mode": "open_scene",
                        "is_novel_context": True,
                    },
                    idempotency_key=f"{handle.correlation_id}:target:{idx}:{ta.target_id}",
                    correlation_id=handle.correlation_id,
                    causation_id=analysis_id,
                    rebuild=False,
                )
            )
        return specs

    def _spontaneous_targets(self, learner_id: int, register: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for phrase_id in self.db.get_spontaneous_candidates(learner_id, register)[:3]:
            entry = self.db.get_phrase_entry(phrase_id)
            if entry is not None:
                out.append(
                    {
                        "phrase_id": entry.phrase_id,
                        "canonical_form": entry.canonical_form,
                        "communicative_intention": entry.communicative_intention,
                    }
                )
        return out

    def _recent_bot_texts(self, learner_id: int, history: list[dict[str, str]]) -> list[str]:
        """What the learner could be copying: Alex's last replies and the text of recently delivered cards."""
        texts = [h["text"] for h in history if h.get("role") == "partner"]
        with self.db.read() as conn:
            rows = conn.execute(
                """
                SELECT original_span, alternative, explanation_en FROM feedback_item_projection
                WHERE learner_id = ? AND status = 'delivered' ORDER BY delivered_at_utc DESC, diagnostic_id LIMIT 3
                """,
                (learner_id,),
            ).fetchall()
        for r in rows:
            texts.extend(str(r[k]) for k in ("alternative", "explanation_en") if r[k])
        return texts

    def _scene_bible(self, session: dict[str, Any], session_id: str, scene_mode: SceneMode) -> SceneBible:
        try:
            raw = json.loads(session.get("scene_bible_json") or "{}")
            facts = list(
                raw.get(
                    "established_facts",
                    ["Respond directly to the learner's real topic; never invent fake crises or 'me-too' mirroring stories."],
                )
            )
            with self.db.read() as conn:
                last_card = conn.execute(
                    """
                    SELECT original_span, alternative, kind, explanation_en
                    FROM feedback_item_projection
                    WHERE session_id = ? AND status = 'delivered'
                    ORDER BY delivered_at_utc DESC, diagnostic_id DESC LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            if last_card is not None:
                facts.append(
                    f"Recent Quick Upgrade card shown to learner ({last_card['kind']}): "
                    f"You said '{last_card['original_span']}' -> More natural '{last_card['alternative']}'. "
                    f"Note: {last_card['explanation_en']}"
                )
            return SceneBible(
                scene_id=session_id,
                scene_mode=scene_mode,
                setting=raw.get("setting", "Everyday conversation"),
                relationship=raw.get("relationship", "friendly peer"),
                established_facts=facts,
                counterpart_goal=raw.get("counterpart_goal", "Engage naturally and authentically"),
                counterpart_pushback_plan=raw.get("counterpart_pushback_plan"),
            )
        except Exception:
            return SceneBible(scene_id=session_id, scene_mode=scene_mode)

    # ------------------------------------------------------------------ turn contexts

    def _remember(self, turn_event_id: str, ctx: _TurnContext) -> None:
        with self._contexts_lock:
            self._contexts[turn_event_id] = ctx
            while len(self._contexts) > _CONTEXT_CACHE_SIZE:
                self._contexts.popitem(last=False)

    def _context_for(self, handle: TurnHandle) -> _TurnContext:
        with self._contexts_lock:
            cached = self._contexts.get(handle.turn_event_id)
        if cached is not None:
            return cached
        # After a restart: rebuild what apply needs from the log.
        with self.db.read() as conn:
            msg = conn.execute(
                "SELECT payload_json, session_id, learner_id FROM event_log WHERE event_id = ?", (handle.turn_event_id,)
            ).fetchone()
            reply = conn.execute(
                "SELECT payload_json FROM event_log WHERE event_seq = ?", (handle.reply_event_seq,)
            ).fetchone()
        text = str(json.loads(msg["payload_json"]).get("text", "")) if msg else ""
        status = ComprehensionStatus.OK
        if reply is not None:
            status = _status(json.loads(reply["payload_json"]).get("comprehension_status"))
        session_id = handle.session_id or (str(msg["session_id"]) if msg else "")
        learner_id = handle.learner_id or (int(msg["learner_id"]) if msg else 0)
        cfg = self.db.get_learner_settings(learner_id) if learner_id else {}
        with self.db.read() as conn:
            srow = conn.execute(
                "SELECT register_profile, primary_context_family FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return _TurnContext(
            session_id=session_id,
            learner_text=text,
            register_profile=str(srow["register_profile"]) if srow else "casual",
            feedback_preference=_preference(cfg.get("feedback_preference")),
            comprehension_status=status,
            primary_context_family=str(srow["primary_context_family"]) if srow else "general",
        )


# ------------------------------------------------------------------------------ helpers


def _preference(value: Any) -> FeedbackPreference:
    try:
        return FeedbackPreference(str(value or "light"))
    except ValueError:
        return FeedbackPreference.LIGHT


def _status(value: Any) -> ComprehensionStatus:
    try:
        return ComprehensionStatus(str(value or "ok"))
    except ValueError:
        return ComprehensionStatus.OK


def _as_late(policy: PolicyDecisionV2) -> PolicyDecisionV2:
    """Every card that would have been shown now is deferred to Review; nothing is selected."""
    decisions = []
    for d in policy.decisions:
        if d.delivery in (Delivery.END_OF_TURN, Delivery.IMMEDIATE_FOLLOWUP):
            d = d.model_copy(update={"delivery": Delivery.DEFERRED, "reasons": [*d.reasons, "late_analysis"]})
        decisions.append(d)
    return policy.model_copy(update={"decisions": decisions, "selected_diagnostic_id": None})


__all__ = [
    "ANALYSIS_WAIT_S",
    "AnalysisOutcome",
    "PostTurnActions",
    "TurnHandle",
    "TurnOrchestrator",
    "TurnResult",
    "skip_reason_for",
]
