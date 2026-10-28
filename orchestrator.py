import json
import uuid
from typing import Any, Optional
from ai_engine import DualLLMEngine
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from policy_engine import DialogueController, decide
from schemas import (
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


class TurnResult:
    def __init__(
        self,
        *,
        correlation_id: str,
        session_id: str,
        responder_output: ResponderOutput,
        policy_decision: PolicyDecision,
        selected_diagnostic: Optional[Diagnostic],
        card_id: Optional[str],
    ):
        self.correlation_id = correlation_id
        self.session_id = session_id
        self.responder_output = responder_output
        self.policy_decision = policy_decision
        self.selected_diagnostic = selected_diagnostic
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
    ) -> TurnResult:
        corr_id = f"turn_{uuid.uuid4().hex[:10]}"
        idem_base = message_idempotency_key or corr_id

        learner_cfg = self.db.get_learner_settings(learner_id)
        feedback_pref = learner_cfg.get("feedback_preference", "light")
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
                    ["Respond directly to the learner's real topic; never invent fake crises."],
                ),
                counterpart_goal=raw_bible.get("counterpart_goal", "Engage naturally and authentically"),
            )
        except Exception:
            scene_bible = SceneBible(scene_id=session_id, scene_mode=scene_mode)

        recent_history = self.db.get_recent_dialogue_history(session_id, limit=6)
        runtime_state = self.db.get_session_runtime_state(session_id, learner_id)

        # STEP 1: Persist incoming learner message event
        _, msg_ev_id = self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="learner_message_received",
            payload={"text": learner_text, "is_voice": is_voice_message},
            correlation_id=corr_id,
            idempotency_key=f"{idem_base}:msg",
        )

        # STEP 2: Run Analyst LLM & persist diagnostic/acquisition events
        catalog_targets = [
            {"target_id": p[0], "canonical_form": p[1], "communicative_intention": p[2]}
            for p in DEFAULT_CURATED_PHRASES
        ]
        prior_occurrence_counts = self.db.get_authoritative_occurrence_counts(learner_id)

        analysis = self.ai.run_analyst(
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

        _, diag_ev_id = self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="analyst_diagnosis_recorded",
            payload=analysis.model_dump(),
            correlation_id=corr_id,
            causation_id=msg_ev_id,
            idempotency_key=f"{idem_base}:diag",
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
                )

        # STEP 3: Run deterministic Policy Engine (`decide()`)
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

        selected_diag: Optional[Diagnostic] = None
        if policy_dec.selected_diagnostic_id:
            for d in analysis.diagnostics:
                if d.diagnostic_id == policy_dec.selected_diagnostic_id:
                    selected_diag = d
                    break

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
            )

        # STEP 4: Select orthogonal DialogueDecision via DialogueController
        dialogue_dec = DialogueController.select_dialogue_decision(
            learner_text=learner_text,
            analysis=analysis,
            recent_optional_question_flags=runtime_state["recent_questions"],
            register=register_profile,
            training_mode=training_mode,
        )

        # Advisory Stealth Target selection (only when no error intervention is competing)
        stealth_target_info = None
        if policy_dec.intervention == InterventionLevel.NONE and not analysis.in_emotional_or_story_flow:
            stealth_target_info = self.db.select_stealth_target_for_session(learner_id, register_profile.value)

        # STEP 5: Run Responder LLM & validate output
        resp_out = self.ai.run_responder(
            learner_text=learner_text,
            recent_history=recent_history,
            persona=self.default_persona,
            scene_bible=scene_bible,
            register_profile=register_profile,
            directness=directness,
            training_mode=training_mode,
            dialogue_decision=dialogue_dec,
            advisory_recast_candidate=policy_dec.advisory_recast_candidate,
            stealth_target_info=stealth_target_info,
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
        )

        card_id: Optional[str] = None
        if policy_dec.intervention == InterventionLevel.BRIEF_CARD and selected_diag:
            card_id = f"card_{uuid.uuid4().hex[:10]}"
            self.db.save_pending_card(
                card_id=card_id,
                learner_id=learner_id,
                session_id=session_id,
                correlation_id=corr_id,
                reply_text=resp_out.reply_text,
                diagnostic_dict=selected_diag.model_dump(),
            )

        return TurnResult(
            correlation_id=corr_id,
            session_id=session_id,
            responder_output=resp_out,
            policy_decision=policy_dec,
            selected_diagnostic=selected_diag,
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
        )
