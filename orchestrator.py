from concurrent.futures import ThreadPoolExecutor
import json
import uuid
from typing import Any, Callable, Optional
from ai_engine import DualLLMEngine
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from policy_engine import (
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
