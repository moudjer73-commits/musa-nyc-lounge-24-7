from concurrent.futures import ThreadPoolExecutor
import json
import uuid
from typing import Any, Callable, Optional
from ai_engine import DualLLMEngine
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from policy_engine import (
    get_active_workout_task,
    update_workout_task_after_turn,
    DialogueController,
    _normalize_text_for_match,
    compute_learner_strain,
    decide,
    extract_recent_partner_openers,
)
from schemas import (
    Analysis,
    Diagnostic,
    DirectnessLevel,
    InterventionLevel,
    PersonaCard,
    PolicyDecision,
    ProductionEvidence,
    RegisterProfile,
    ResponderOutput,
    SceneBible,
    SceneMode,
    TrainingMode,
)

_TURN_EXECUTOR = ThreadPoolExecutor(max_workers=6)


class TurnResult:
    def __init__(
        self,
        *,
        correlation_id: str,
        session_id: str,
        responder_output: ResponderOutput,
        policy_decision: PolicyDecision,
        selected_diagnostic: Optional[Diagnostic],
        all_diagnostics: list[Diagnostic],
        card_id: Optional[str],
    ):
        self.correlation_id = correlation_id
        self.session_id = session_id
        self.responder_output = responder_output
        self.policy_decision = policy_decision
        self.selected_diagnostic = selected_diagnostic
        self.all_diagnostics = all_diagnostics
        self.card_id = card_id


class TurnOrchestrator:
    def __init__(self, db: EventSourcedDatabase, ai: DualLLMEngine):
        self.db = db
        self.ai = ai
        self.default_persona = PersonaCard()

    def process_turn(
        self,
        *,
        learner_id: int,
        learner_text: str,
        message_idempotency_key: Optional[str] = None,
        is_voice_message: bool = False,
        on_fast_reply: Optional[Callable[[ResponderOutput], None]] = None,
    ) -> TurnResult:
        corr_id = f"turn_{uuid.uuid4().hex[:10]}"
        idem_base = message_idempotency_key or corr_id

        learner_cfg = self.db.get_learner_settings(learner_id)
        feedback_pref = learner_cfg.get("feedback_preference", "light")
        arabic_support = learner_cfg.get("arabic_support", "bilingual")
        opted_in_workouts = bool(learner_cfg.get("opted_in_workouts", 1))

        session = self.db.get_or_create_active_session(learner_id)
        session_id = str(session["session_id"])
        training_mode = TrainingMode(session.get("training_mode", "open_scene"))
        scene_mode = SceneMode(session.get("scene_mode", "real_conversation"))
        register_profile = RegisterProfile(session.get("register_profile", "casual"))
        directness = DirectnessLevel(session.get("directness", "balanced"))

        try:
            raw_bible = json.loads(session.get("scene_bible_json") or "{}")
            scene_bible = SceneBible(
                scene_id=session_id,
                scene_mode=scene_mode,
                setting=raw_bible.get("setting", "Everyday conversation"),
                relationship=raw_bible.get("relationship", "friendly peer"),
                established_facts=raw_bible.get(
                    "established_facts",
                    ["Respond directly to the learner's real topic; never invent fake crises or 'me-too' mirroring stories."],
                ),
                counterpart_goal=raw_bible.get("counterpart_goal", "Engage naturally and authentically"),
                counterpart_pushback_plan=raw_bible.get("counterpart_pushback_plan"),
            )
        except Exception:
            scene_bible = SceneBible(scene_id=session_id, scene_mode=scene_mode)

        recent_history = self.db.get_recent_dialogue_history(session_id, limit=6)
        runtime_state = self.db.get_session_runtime_state(session_id, learner_id)
        prior_occurrence_counts = self.db.get_authoritative_occurrence_counts(learner_id)

        learner_turns_txt = [h["text"] for h in recent_history if h.get("role") == "learner"] + [learner_text]
        partner_turns_txt = [h["text"] for h in recent_history if h.get("role") == "partner"]
        strain_val = compute_learner_strain(learner_turns_txt)
        avoid_ops = extract_recent_partner_openers(partner_turns_txt)

        fast_intent = "ask_opinion" if "?" in learner_text else "share_comment"
        pre_analysis = Analysis(
            communicative_intention=fast_intent,
            learner_invites_question=("?" in learner_text),
        )
        dialogue_dec = DialogueController.select_dialogue_decision(
            learner_text=learner_text,
            analysis=pre_analysis,
            recent_optional_question_flags=runtime_state["recent_questions"],
            register=register_profile,
            training_mode=training_mode,
        ).model_copy(update={"avoid_openers": avoid_ops, "strain_score": strain_val})

        stealth_target_info = self.db.select_stealth_target_for_session(learner_id, register_profile.value)

        catalog_targets = [
            {"target_id": p[0], "canonical_form": p[1], "communicative_intention": p[2]}
            for p in DEFAULT_CURATED_PHRASES
        ]

        fut_analyst = _TURN_EXECUTOR.submit(
            self.ai.run_analyst,
            learner_text=learner_text,
            recent_history=recent_history,
            register_profile=register_profile,
            directness=directness,
            training_mode=training_mode,
            scene_bible=scene_bible,
            revealed_target_ids=runtime_state["revealed_targets"],
            directly_cued_target_ids=runtime_state["cued_targets"],
            catalog_targets=catalog_targets,
        )
        fut_responder = _TURN_EXECUTOR.submit(
            self.ai.run_responder,
            learner_text=learner_text,
            recent_history=recent_history,
            persona=self.default_persona,
            scene_bible=scene_bible,
            register_profile=register_profile,
            directness=directness,
            training_mode=training_mode,
            dialogue_decision=dialogue_dec,
            advisory_recast_candidate=None,
            stealth_target_info=stealth_target_info,
        )

        resp_out = fut_responder.result()
        if on_fast_reply is not None:
            try:
                on_fast_reply(resp_out)
            except Exception:
                pass
        analysis = fut_analyst.result()
        if get_active_workout_task(learner_id) is not None:
            update_workout_task_after_turn(learner_id, bool(analysis.communicative_goal_achieved))

        policy_dec = decide(
            analysis=analysis,
            learner_id=learner_id,
            turns_since_last_visible_card=runtime_state["turns_since_visible_card"],
            authoritative_occurrence_counts=prior_occurrence_counts,
            already_counted_in_message_keys=set(),
            active_learning_goals=[],
            feedback_preference=feedback_pref,
            learner_opted_in_workouts=opted_in_workouts,
            workout_already_scheduled=False,
            is_voice_message=is_voice_message,
        )

        if policy_dec.advisory_recast_candidate:
            norm_rec = _normalize_text_for_match(policy_dec.advisory_recast_candidate)
            if norm_rec and norm_rec in _normalize_text_for_match(resp_out.reply_text):
                resp_out = resp_out.model_copy(update={"recast_used": True})

        selected_diag: Optional[Diagnostic] = None
        if policy_dec.selected_diagnostic_id:
            for d in analysis.diagnostics:
                if d.diagnostic_id == policy_dec.selected_diagnostic_id:
                    selected_diag = d
                    break

        _, msg_ev_id = self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="learner_message_received",
            payload={"text": learner_text, "is_voice": is_voice_message},
            correlation_id=corr_id,
            idempotency_key=f"{idem_base}:msg",
            rebuild=False,
        )

        _, diag_ev_id = self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="analyst_diagnosis_recorded",
            payload=analysis.model_dump(),
            correlation_id=corr_id,
            causation_id=msg_ev_id,
            idempotency_key=f"{idem_base}:diag",
            rebuild=False,
        )

        for idx, ta in enumerate(analysis.target_assessments):
            if ta.evidence != ProductionEvidence.NONE:
                self.db.append_event(
                    learner_id=learner_id,
                    session_id=session_id,
                    event_type="phrase_production_assessed",
                    payload={
                        **ta.model_dump(),
                        "register_profile": register_profile.value,
                        "training_mode": training_mode.value,
                        "is_novel_context": True,
                    },
                    correlation_id=corr_id,
                    causation_id=diag_ev_id,
                    idempotency_key=f"{idem_base}:target:{idx}:{ta.target_id}",
                    rebuild=False,
                )

        if selected_diag and selected_diag.alternative and selected_diag.confidence_band.value == "high":
            organic_pid = self.db.register_organic_phrase_from_diagnostic(
                normalized_pattern_id=selected_diag.normalized_pattern_id,
                canonical_form=selected_diag.alternative,
                communicative_intention=analysis.communicative_intention,
                register_profile=register_profile.value,
            )
            if policy_dec.intervention == InterventionLevel.BRIEF_CARD:
                self.db.append_event(
                    learner_id=learner_id,
                    session_id=session_id,
                    event_type="phrase_exposure_recorded",
                    payload={"phrase_id": organic_pid, "revealed_to_learner": True, "directly_cued": False},
                    correlation_id=corr_id,
                    causation_id=diag_ev_id,
                    idempotency_key=f"{idem_base}:org_exp:{organic_pid}",
                    rebuild=False,
                )

        self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="policy_decision_recorded",
            payload={
                **policy_dec.model_dump(),
                "selected_pattern_id": selected_diag.normalized_pattern_id if selected_diag else None,
                "selected_context_family": (
                    selected_diag.relevant_context_family
                    if selected_diag and selected_diag.kind.value in ("pragmatics", "register")
                    else "global"
                ),
            },
            correlation_id=corr_id,
            causation_id=diag_ev_id,
            idempotency_key=f"{idem_base}:pol",
            rebuild=False,
        )

        if policy_dec.schedule_micro_workout and policy_dec.workout_candidate_pattern_id:
            self.db.append_event(
                learner_id=learner_id,
                session_id=session_id,
                event_type="micro_workout_scheduled",
                payload={
                    "normalized_pattern_id": policy_dec.workout_candidate_pattern_id,
                    "relevant_context_family": policy_dec.workout_candidate_context_family or "global",
                },
                correlation_id=corr_id,
                causation_id=diag_ev_id,
                idempotency_key=f"{idem_base}:workout:{policy_dec.workout_candidate_pattern_id}",
                rebuild=False,
            )

        asked_q = DialogueController.count_genuine_questions(resp_out.reply_text) > 0
        self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="responder_reply_delivered",
            payload={
                "reply_text": resp_out.reply_text,
                "second_bubble_text": resp_out.second_bubble_text,
                "recast_used": resp_out.recast_used,
                "asked_optional_question": asked_q and not policy_dec.clarification_needed,
                "was_clarification_question": policy_dec.clarification_needed,
            },
            correlation_id=corr_id,
            causation_id=msg_ev_id,
            idempotency_key=f"{idem_base}:resp",
            rebuild=True,
        )

        if selected_diag and arabic_support == "english_only":
            selected_diag = selected_diag.model_copy(update={"explanation_ar": None})

        card_id: Optional[str] = None
        if policy_dec.intervention == InterventionLevel.BRIEF_CARD and selected_diag:
            card_id = f"card_{uuid.uuid4().hex[:10]}"
            diag_payload = selected_diag.model_dump()
            # Attach secondary turn findings (e.g. 'discuss about', 'took a decision') as extra compact bullets if in Active mode
            other_diags = [
                d.model_dump()
                for d in analysis.diagnostics
                if d.diagnostic_id != selected_diag.diagnostic_id and d.alternative
            ]
            if other_diags:
                diag_payload["additional_turn_findings"] = other_diags[:2]
            self.db.save_pending_card(
                card_id=card_id,
                learner_id=learner_id,
                session_id=session_id,
                correlation_id=corr_id,
                reply_text=resp_out.reply_text,
                diagnostic_dict=diag_payload,
            )

        return TurnResult(
            correlation_id=corr_id,
            session_id=session_id,
            responder_output=resp_out,
            policy_decision=policy_dec,
            selected_diagnostic=selected_diag,
            all_diagnostics=analysis.diagnostics,
            card_id=card_id,
        )

    def confirm_card_delivered(
        self,
        *,
        learner_id: int,
        session_id: str,
        correlation_id: str,
        diagnostic: Diagnostic,
    ) -> None:
        ctx_fam = (
            diagnostic.relevant_context_family
            if diagnostic.kind.value in ("pragmatics", "register")
            else "global"
        )
        self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload={
                "diagnostic_id": diagnostic.diagnostic_id,
                "normalized_pattern_id": diagnostic.normalized_pattern_id,
                "relevant_context_family": ctx_fam,
            },
            correlation_id=correlation_id,
            idempotency_key=f"{correlation_id}:card_delivered:{diagnostic.diagnostic_id}",
            rebuild=True,
        )


# ==============================================================================
# V3.0 S2 Orchestrator Extensions (S0 Contracts, Amendment A14 & Section 6)
# ==============================================================================
from concurrent.futures import Future
from dataclasses import dataclass as _s2_dataclass
from datetime import datetime as _s2_datetime
from typing import Literal as _s2_Literal

from clock import SystemClock as _S2SystemClock, to_utc_iso as _s2_to_utc_iso
from db_v3_migrations import EventSpec as _S2EventSpec
from interaction import (
    PolicyInputs as _S2PolicyInputs,
    build_callback as _s2_build_callback,
    get_active_interaction as _s2_get_active_interaction,
    resolve_interaction_policy as _s2_resolve_interaction_policy,
)
from schemas_v3 import (
    AnalysisSkippedV2 as _S2AnalysisSkippedV2,
    AnalysisV2 as _S2AnalysisV2,
    CallMetaDict as _S2CallMetaDict,
    ComprehensionStatus as _S2ComprehensionStatus,
    Delivery as _S2Delivery,
    PolicyDecisionV2 as _S2PolicyDecisionV2,
    ResponderReplyV2 as _S2ResponderReplyV2,
    SkipReason as _S2SkipReason,
)
from validation import ValidationResult as _S2ValidationResult


@_s2_dataclass(frozen=True)
class TurnHandle:
    turn_event_id: str
    reply_event_seq: int
    turn_session_revision: int
    correlation_id: str
    session_id: str = ""
    idempotency_base: str = ""
    comprehension_status: _S2ComprehensionStatus = _S2ComprehensionStatus.OK


@_s2_dataclass(frozen=True)
class AnalysisOutcome:
    status: _s2_Literal["ok", "skipped"]
    skip_reason: _S2SkipReason | None = None
    validated: _S2ValidationResult | None = None
    call_meta: _S2CallMetaDict | None = None
    detail: str | None = None


@_s2_dataclass(frozen=True)
class PostTurnActions:
    followup_text: str | None
    attach_upgrade_button: bool
    upgrade_callback: str | None
    deferred_count: int
    card_id: str | None


def _apply_analysis_outcome_impl(
    self,
    *,
    learner_id: int,
    handle: TurnHandle,
    outcome: AnalysisOutcome,
    now: _s2_datetime | None = None,
) -> PostTurnActions:
    """Atomic CAS application of background Analyst outcome (Section 6 & Amendment A14)."""
    now_dt = now or _S2SystemClock().now()
    now_iso = _s2_to_utc_iso(now_dt)
    session_id = handle.session_id or self.db.get_or_create_active_session(learner_id)["session_id"]
    idem_base = handle.idempotency_base or f"turn_{handle.turn_event_id}"

    # Ensure V3 tables exist on the target DB
    if hasattr(self.db, "migrate"):
        self.db.migrate()

    if outcome.status == "skipped" or outcome.validated is None:
        skip_payload = _S2AnalysisSkippedV2(
            turn_event_id=handle.turn_event_id,
            reason=outcome.skip_reason or _S2SkipReason.ERROR,
            detail=outcome.detail,
        )
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="analysis_skipped",
            payload=skip_payload,
            correlation_id=handle.correlation_id,
            idempotency_key=f"{idem_base}:skip",
            causation_id=handle.turn_event_id,
            occurred_at_utc=now_iso,
        )
        return PostTurnActions(
            followup_text=None,
            attach_upgrade_button=False,
            upgrade_callback=None,
            deferred_count=0,
            card_id=None,
        )

    val = outcome.validated
    # Read runtime session state & prior pattern counts before policy resolution
    with self.db._connect() as conn:
        s_row = conn.execute(
            "SELECT * FROM session_state_projection WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        l_row = conn.execute(
            "SELECT * FROM learners WHERE learner_id = ?",
            (learner_id,),
        ).fetchone()
        pat_rows = conn.execute(
            "SELECT * FROM error_pattern_projection WHERE learner_id = ?",
            (learner_id,),
        ).fetchall()

    s_dict = dict(s_row) if s_row is not None else {}
    l_dict = dict(l_row) if l_row is not None else {}
    turn_idx = int(s_dict.get("learner_turn_count") or s_dict.get("turn_count") or 3)
    turns_since_card = int(
        s_dict["turns_since_visible_card"]
        if s_dict.get("turns_since_visible_card") is not None
        else 99
    )
    cards_in_scene = int(s_dict.get("cards_in_scene") or 0)
    turns_since_p0 = int(
        s_dict["turns_since_p0"] if s_dict.get("turns_since_p0") is not None else 99
    )
    pref = str(l_dict.get("feedback_preference") or "active").lower()

    prior_counts: dict[str, int] = {}
    for r in pat_rows:
        rd = dict(r)
        pkey = rd.get("pattern_id") or rd.get("pattern_code") or rd.get("kind")
        pcnt = (
            rd.get("occurrence_count")
            if rd.get("occurrence_count") is not None
            else rd.get("active_occurrences", 0)
        )
        if pkey:
            prior_counts[str(pkey)] = int(pcnt or 0)
    has_active = _s2_get_active_interaction(self.db, learner_id, now_dt) is not None

    comm = val.communicative or {}
    strain = float(comm.get("strain", 0.0) or 0.0)
    in_flow = bool(comm.get("in_emotional_or_story_flow", False))

    policy_inputs = _S2PolicyInputs(
        turn_event_id=handle.turn_event_id,
        comprehension_status=handle.comprehension_status,
        p0=val.p0,
        diagnostics=val.diagnostics,
        prior_pattern_counts=prior_counts,
        turns_since_visible_card=turns_since_card,
        cards_in_scene=cards_in_scene,
        turns_since_p0=turns_since_p0,
        strain=strain,
        feedback_preference=pref,
        learner_turn_index=max(1, turn_idx),
        has_active_interaction=has_active,
        in_emotional_or_story_flow=in_flow,
        curveball_turn=False,
    )
    decision_v2 = _s2_resolve_interaction_policy(policy_inputs)

    analysis_v2 = _S2AnalysisV2(
        turn_event_id=handle.turn_event_id,
        rubric_version="v2.0",
        communicative=val.communicative,
        diagnostics=val.diagnostics,
        target_assessments=val.target_assessments,
        p0=val.p0,
        dropped=val.dropped,
        call_meta=outcome.call_meta,
    )

    events_to_commit = [
        _S2EventSpec(
            learner_id=learner_id,
            session_id=session_id,
            event_type="analyst_diagnosis_recorded",
            payload=analysis_v2,
            correlation_id=handle.correlation_id,
            idempotency_key=f"{idem_base}:analysis",
            causation_id=handle.turn_event_id,
            occurred_at_utc=now_iso,
        ),
        _S2EventSpec(
            learner_id=learner_id,
            session_id=session_id,
            event_type="policy_decision_recorded",
            payload=decision_v2,
            correlation_id=handle.correlation_id,
            idempotency_key=f"{idem_base}:policy",
            causation_id=handle.turn_event_id,
            occurred_at_utc=now_iso,
        ),
    ]

    cas_matched = self.db.append_events_if_revision(
        session_id,
        handle.turn_session_revision,
        events_to_commit,
    )

    decision_by_id = {fd.diagnostic_id: fd for fd in decision_v2.decisions}

    if not cas_matched:
        # Revision moved on (learner already sent another message):
        # 1. If P0 was signaled, store pending clarification for next Responder brief (A14).
        if val.p0.candidate and val.p0.line:
            self.db.set_pending_clarification(session_id, val.p0.line)
        # 2. Force all non-suppressed findings to deferred and persist events + projections.
        forced_decisions = [
            fd.model_copy(
                update={
                    "delivery": _S2Delivery.SUPPRESSED
                    if fd.delivery == _S2Delivery.SUPPRESSED
                    else _S2Delivery.DEFERRED,
                    "reasons": list(fd.reasons) + ["late_analysis_revision_moved"],
                }
            )
            for fd in decision_v2.decisions
        ]
        late_policy = decision_v2.model_copy(
            update={
                "decisions": forced_decisions,
                "selected_diagnostic_id": None,
                "pending_clarification": val.p0.line if val.p0.candidate else None,
            }
        )
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="analyst_diagnosis_recorded",
            payload=analysis_v2,
            correlation_id=handle.correlation_id,
            idempotency_key=f"{idem_base}:analysis",
            causation_id=handle.turn_event_id,
            occurred_at_utc=now_iso,
        )
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="policy_decision_recorded",
            payload=late_policy,
            correlation_id=handle.correlation_id,
            idempotency_key=f"{idem_base}:policy",
            causation_id=handle.turn_event_id,
            occurred_at_utc=now_iso,
        )
        deferred_cnt = sum(1 for fd in forced_decisions if fd.delivery == _S2Delivery.DEFERRED)
        self._persist_feedback_items(
            learner_id=learner_id,
            session_id=session_id,
            turn_event_seq=handle.reply_event_seq,
            diagnostics=val.diagnostics,
            decisions={fd.diagnostic_id: fd for fd in forced_decisions},
            now_iso=now_iso,
        )
        return PostTurnActions(
            followup_text=None,
            attach_upgrade_button=False,
            upgrade_callback=None,
            deferred_count=deferred_cnt,
            card_id=None,
        )

    # CAS succeeded: revision is still current!
    self._persist_feedback_items(
        learner_id=learner_id,
        session_id=session_id,
        turn_event_seq=handle.reply_event_seq,
        diagnostics=val.diagnostics,
        decisions=decision_by_id,
        now_iso=now_iso,
    )

    followup_text: str | None = None
    attach_btn = False
    ug_cb: str | None = None
    card_id: str | None = None

    selected_id = decision_v2.selected_diagnostic_id
    selected_fd = decision_by_id.get(selected_id) if selected_id else None

    if selected_fd is not None:
        if selected_fd.delivery == _S2Delivery.IMMEDIATE_FOLLOWUP and val.p0.line:
            followup_text = val.p0.line
            reply_v2 = _S2ResponderReplyV2(
                kind="p0_followup",
                turn_event_id=handle.turn_event_id,
                reply_text=followup_text,
                comprehension_status=handle.comprehension_status,
            )
            self.db.append_event_ex(
                learner_id=learner_id,
                session_id=session_id,
                event_type="responder_reply_delivered",
                payload=reply_v2,
                correlation_id=handle.correlation_id,
                idempotency_key=f"{idem_base}:p0_followup",
                causation_id=handle.turn_event_id,
                occurred_at_utc=now_iso,
            )
            with self.db._connect() as conn:
                conn.execute(
                    "UPDATE session_state_projection SET turns_since_p0 = 0 WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
        elif selected_fd.delivery == _S2Delivery.END_OF_TURN:
            attach_btn = True
            ug_cb = _s2_build_callback("ug", handle.reply_event_seq, handle.turn_session_revision)
            card_id = f"card_{selected_fd.diagnostic_id}"
            with self.db._connect() as conn:
                conn.execute(
                    "UPDATE session_state_projection SET "
                    "turns_since_visible_card = 0, "
                    "cards_in_scene = COALESCE(cards_in_scene, 0) + 1 "
                    "WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()

    deferred_cnt = sum(
        1 for fd in decision_v2.decisions if fd.delivery == _S2Delivery.DEFERRED
    )
    return PostTurnActions(
        followup_text=followup_text,
        attach_upgrade_button=attach_btn,
        upgrade_callback=ug_cb,
        deferred_count=deferred_cnt,
        card_id=card_id,
    )


def _persist_feedback_items_impl(
    self,
    *,
    learner_id: int,
    session_id: str,
    turn_event_seq: int,
    diagnostics: list,
    decisions: dict,
    now_iso: str,
) -> None:
    with self.db._connect() as conn:
        for d in diagnostics:
            fd = decisions.get(d.diagnostic_id)
            if fd is None:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO feedback_item_projection (
                    diagnostic_id, learner_id, session_id, turn_event_seq,
                    pattern_id, context_family, domain, classification,
                    repair_scope, kind, severity, original_span, alternative,
                    explanation_en, explanation_ar, priority_class, priority_score,
                    delivery, status, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, 'general', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    d.diagnostic_id,
                    learner_id,
                    session_id,
                    int(turn_event_seq),
                    d.pattern_id,
                    str(d.domain),
                    str(d.classification),
                    str(d.repair_scope),
                    d.kind,
                    d.severity,
                    d.original_span,
                    d.alternative,
                    d.explanation_en,
                    d.explanation_ar,
                    int(fd.priority_class),
                    float(fd.priority_score),
                    str(fd.delivery),
                    now_iso,
                ),
            )
        conn.commit()


TurnOrchestrator.apply_analysis_outcome = _apply_analysis_outcome_impl
TurnOrchestrator._persist_feedback_items = _persist_feedback_items_impl


# ==============================================================================
# V3.0 S4 Orchestrator UI & Debrief Bindings
# ==============================================================================
from ui_v3 import (
    UIResponse as _S4UIResponse,
    build_scene_debrief as _s4_build_scene_debrief,
    build_v3_progress_report as _s4_build_v3_progress_report,
    handle_v3_callback as _s4_handle_v3_callback,
    handle_v3_text_turn as _s4_handle_v3_text_turn,
)


def _orch_handle_v3_callback(
    self,
    *,
    learner_id: int,
    callback_data: str,
    session_id: str | None = None,
    now: _s2_datetime | None = None,
) -> _S4UIResponse:
    sid = session_id or self.db.get_or_create_active_session(learner_id)["session_id"]
    return _s4_handle_v3_callback(
        self.db,
        learner_id=learner_id,
        session_id=sid,
        callback_data=callback_data,
        now=now,
    )


def _orch_handle_v3_text_turn(
    self,
    *,
    learner_id: int,
    text: str,
    session_id: str | None = None,
    now: _s2_datetime | None = None,
    idempotency_key: str | None = None,
) -> _S4UIResponse:
    sid = session_id or self.db.get_or_create_active_session(learner_id)["session_id"]
    return _s4_handle_v3_text_turn(
        self.db,
        learner_id=learner_id,
        session_id=sid,
        text=text,
        now=now,
        idempotency_key=idempotency_key,
    )


def _orch_build_scene_debrief(
    self,
    *,
    learner_id: int,
    session_id: str | None = None,
    now: _s2_datetime | None = None,
) -> list[dict]:
    sid = session_id or self.db.get_or_create_active_session(learner_id)["session_id"]
    return _s4_build_scene_debrief(
        self.db,
        learner_id=learner_id,
        session_id=sid,
        now=now,
    )


def _orch_build_v3_progress_report(
    self,
    *,
    learner_id: int,
    now: _s2_datetime | None = None,
) -> dict:
    return _s4_build_v3_progress_report(self.db, learner_id=learner_id, now=now)


TurnOrchestrator.handle_v3_callback = _orch_handle_v3_callback
TurnOrchestrator.handle_v3_text_turn = _orch_handle_v3_text_turn
TurnOrchestrator.build_scene_debrief = _orch_build_scene_debrief
TurnOrchestrator.build_v3_progress_report = _orch_build_v3_progress_report
