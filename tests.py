from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError

import config
import schemas
from schemas import (
    AlternativeStatus,
    Analysis,
    CandidateAlternative,
    ConfidenceBand,
    ConversationalAct,
    Diagnostic,
    DialogueDecision,
    DirectnessLevel,
    InterventionLevel,
    IssueKind,
    ProductionEvidence,
    RegisterProfile,
    ResponderOutput,
    Severity,
    SurfaceStyle,
    TargetUseAssessment,
    TrainingMode,
)
from policy_engine import (
    DialogueController,
    decide,
    validate_analyst_output_against_turn,
    validate_responder_output,
)
from database import EventSourcedDatabase
from ai_engine import DualLLMEngine
from orchestrator import TurnOrchestrator


class FullProductionVerificationSuite(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_suite.db")
        self.db = EventSourcedDatabase(self.db_path)
        self.learner_id = 8874707150
        self.db.upsert_learner(self.learner_id, feedback_preference="light", opted_in_workouts=True)
        self.db.create_session("sess_1", self.learner_id, training_mode="open_scene", register_profile="casual")

    def tearDown(self):
        self.tmp_dir.cleanup()

    # TEST 01: Code and schema validity
    def test_01_code_and_schema_validity(self):
        d = Diagnostic(
            diagnostic_id="d1",
            original_span="discuss about this",
            normalized_pattern_id="grammar:discuss_about",
            intended_meaning="talk about this matter",
            kind=IssueKind.GRAMMAR,
            severity=Severity.MEDIUM,
            alternative="discuss this",
            secondary_alternatives=[
                CandidateAlternative(
                    phrase="talk this over",
                    register_profile=RegisterProfile.CASUAL,
                    directness=DirectnessLevel.BALANCED,
                    pragmatic_effect="Warm collaborative tone",
                )
            ],
            alternative_status=AlternativeStatus.REQUIRED,
            explanation_en="'Discuss' takes a direct object without 'about'.",
            explanation_ar="الفعل discuss يتعدى بنفسه دون حرف الجر about.",
            l1_transfer_likely=True,
            l1_transfer_evidence="Arabic تناقشنا في/حول maps a preposition onto discuss.",
            preserves_intended_meaning=True,
            context_fit_verified=True,
            confidence_band=ConfidenceBand.HIGH,
            confidence_score=0.92,
        )
        self.assertEqual(d.kind, IssueKind.GRAMMAR)
        self.assertEqual(len(d.secondary_alternatives), 1)

    # TEST 02: Fail-closed diagnostics
    def test_02_fail_closed_diagnostics(self):
        # 2a. Conflicting confidence band (HIGH with 0.45 score) must fail
        with self.assertRaises(ValidationError):
            Diagnostic(
                diagnostic_id="d_bad_conf",
                original_span="do an effort",
                normalized_pattern_id="collocation:do_effort",
                intended_meaning="try hard",
                kind=IssueKind.COLLOCATION,
                severity=Severity.MEDIUM,
                alternative="make an effort",
                alternative_status=AlternativeStatus.RECOMMENDED,
                explanation_en="Use make/put in effort.",
                preserves_intended_meaning=True,
                context_fit_verified=True,
                confidence_band=ConfidenceBand.HIGH,
                confidence_score=0.45,
            )

        # 2b. OPTIONAL_UPGRADE with REQUIRED status must fail
        with self.assertRaises(ValidationError):
            Diagnostic(
                diagnostic_id="d_bad_opt",
                original_span="I made a decision",
                normalized_pattern_id="style:made_decision",
                intended_meaning="I decided",
                kind=IssueKind.OPTIONAL_UPGRADE,
                severity=Severity.LOW,
                alternative="I decided",
                alternative_status=AlternativeStatus.REQUIRED,
                explanation_en="Shorter option.",
                preserves_intended_meaning=True,
                context_fit_verified=True,
                confidence_band=ConfidenceBand.HIGH,
                confidence_score=0.88,
            )

        # 2c. REQUIRED correction missing intended_meaning must fail
        with self.assertRaises(ValidationError):
            Diagnostic(
                diagnostic_id="d_no_meaning",
                original_span="discuss about",
                normalized_pattern_id="grammar:discuss_about",
                intended_meaning=None,
                kind=IssueKind.GRAMMAR,
                severity=Severity.MEDIUM,
                alternative="discuss",
                alternative_status=AlternativeStatus.REQUIRED,
                explanation_en="Direct object required.",
                preserves_intended_meaning=True,
                context_fit_verified=True,
                confidence_band=ConfidenceBand.HIGH,
                confidence_score=0.90,
            )

        # 2d. l1_transfer_likely=True without l1_transfer_evidence must fail
        with self.assertRaises(ValidationError):
            Diagnostic(
                diagnostic_id="d_no_l1_ev",
                original_span="open the light",
                normalized_pattern_id="collocation:open_light",
                intended_meaning="turn on the light",
                kind=IssueKind.COLLOCATION,
                severity=Severity.MEDIUM,
                alternative="turn on the light",
                alternative_status=AlternativeStatus.REQUIRED,
                explanation_en="Use 'turn on' for switches/lights.",
                l1_transfer_likely=True,
                l1_transfer_evidence=None,
                preserves_intended_meaning=True,
                context_fit_verified=True,
                confidence_band=ConfidenceBand.HIGH,
                confidence_score=0.91,
            )

        # 2e. Omitted validation evidence in TargetUseAssessment defaults to NONE
        t_assess = TargetUseAssessment(target_id="run_something_by_someone", matched_span="run this by you")
        self.assertEqual(t_assess.evidence, ProductionEvidence.NONE)

    # TEST 03: Dialogue naturalness constraints
    def test_03_dialogue_naturalness_constraints(self):
        # 3a. Sharing a story with 0% recent questions must NOT force ASK
        story_analysis = Analysis(
            communicative_intention="share_story",
            learner_invites_question=False,
            requires_clarification=False,
        )
        dec = DialogueController.select_dialogue_decision(
            learner_text="I went hiking in the mountains yesterday for five hours and the view was incredible.",
            analysis=story_analysis,
            recent_optional_question_flags=[False, False, False, False, False],
        )
        self.assertFalse(dec.allow_question)
        self.assertEqual(dec.max_questions, 0)
        self.assertNotEqual(dec.act, ConversationalAct.ASK)

        # 3b. Clarification needed permits CLARIFY even when optional question budget is exhausted
        clarify_analysis = Analysis(requires_clarification=True)
        dec_clarify = DialogueController.select_dialogue_decision(
            learner_text="The thing with the schedule went sideways.",
            analysis=clarify_analysis,
            recent_optional_question_flags=[True, True, True, True, True],
        )
        self.assertEqual(dec_clarify.act, ConversationalAct.CLARIFY)
        self.assertTrue(dec_clarify.allow_question)
        self.assertEqual(dec_clarify.max_questions, 1)

        # 3c. decide() keeps clarification separate from teaching intervention
        pol_clarify = decide(
            analysis=clarify_analysis,
            learner_id=self.learner_id,
            turns_since_last_visible_card=5,
            authoritative_occurrence_counts={},
            already_counted_in_message_keys=set(),
            active_learning_goals=[],
        )
        self.assertEqual(pol_clarify.intervention, InterventionLevel.NONE)
        self.assertTrue(pol_clarify.clarification_needed)

    # TEST 04: Question-count robustness
    def test_04_question_count_robustness(self):
        # Contractions + quoted question inside declarative sentence -> 0 genuine questions
        txt_quoted = 'I\'m pretty sure he asked, "Are you coming tomorrow?" before he left, didn\'t he say that.'
        self.assertEqual(DialogueController.count_genuine_questions(txt_quoted), 0)

        # Contractions + short genuine question -> 1 question
        txt_short_q = "I don't think that'll work. Why?"
        self.assertEqual(DialogueController.count_genuine_questions(txt_short_q), 1)

        # Validator rejects a prohibited question when allow_question=False
        no_q_decision = DialogueDecision(
            act=ConversationalAct.REACT,
            style=SurfaceStyle.PLAIN,
            allow_question=False,
            max_questions=0,
        )
        with self.assertRaises(ValueError):
            validate_responder_output(
                output=ResponderOutput(reply_text="That sounds great. What happened next?"),
                decision=no_q_decision,
            )

    # TEST 05: Workout eligibility & separate workout_candidate selection
    def test_05_workout_eligibility(self):
        opt_diag = Diagnostic(
            diagnostic_id="d_opt",
            original_span="I made a decision",
            normalized_pattern_id="style:made_decision",
            intended_meaning="I decided",
            kind=IssueKind.OPTIONAL_UPGRADE,
            severity=Severity.LOW,
            alternative="I decided",
            alternative_status=AlternativeStatus.OPTIONAL,
            explanation_en="Shorter form.",
            preserves_intended_meaning=True,
            context_fit_verified=True,
            confidence_band=ConfidenceBand.HIGH,
            confidence_score=0.90,
        )
        # 5a. OPTIONAL_UPGRADE never triggers a remedial workout
        pol_opt = decide(
            analysis=Analysis(diagnostics=[opt_diag]),
            learner_id=self.learner_id,
            turns_since_last_visible_card=5,
            authoritative_occurrence_counts={f"{self.learner_id}:style:made_decision:global": 5},
            already_counted_in_message_keys=set(),
            active_learning_goals=[],
        )
        self.assertFalse(pol_opt.schedule_micro_workout)

        # 5b. Unrelated learning goal blocks workout scheduling
        recurring_diag = Diagnostic(
            diagnostic_id="d_rec",
            original_span="do an effort",
            normalized_pattern_id="collocation:do_effort",
            intended_meaning="make an effort",
            kind=IssueKind.COLLOCATION,
            severity=Severity.LOW,
            alternative="make an effort",
            alternative_status=AlternativeStatus.REQUIRED,
            explanation_en="Use make/put in effort.",
            preserves_intended_meaning=True,
            context_fit_verified=True,
            confidence_band=ConfidenceBand.HIGH,
            confidence_score=0.90,
        )
        pol_unrelated = decide(
            analysis=Analysis(diagnostics=[recurring_diag]),
            learner_id=self.learner_id,
            turns_since_last_visible_card=5,
            authoritative_occurrence_counts={f"{self.learner_id}:collocation:do_effort:global": 2},
            already_counted_in_message_keys=set(),
            active_learning_goals=["pragmatics:soften_refusal"],
        )
        self.assertFalse(pol_unrelated.schedule_micro_workout)

        # 5c. One-off HIGH severity issue is selected as immediate intervention, while 3rd-time LOW severity issue is selected as workout_candidate
        one_off_high = Diagnostic(
            diagnostic_id="d_high",
            original_span="No I won't do it",
            normalized_pattern_id="pragmatics:blunt_refusal",
            relevant_context_family="manager_refusal",
            intended_meaning="politely decline",
            kind=IssueKind.PRAGMATICS,
            severity=Severity.HIGH,
            alternative="My plate's pretty full right now",
            alternative_status=AlternativeStatus.RECOMMENDED,
            explanation_en="Soften direct refusals with a manager.",
            preserves_intended_meaning=True,
            context_fit_verified=True,
            confidence_band=ConfidenceBand.HIGH,
            confidence_score=0.94,
        )
        pol_dual = decide(
            analysis=Analysis(diagnostics=[one_off_high, recurring_diag]),
            learner_id=self.learner_id,
            turns_since_last_visible_card=5,
            authoritative_occurrence_counts={f"{self.learner_id}:collocation:do_effort:global": 2},
            already_counted_in_message_keys=set(),
            active_learning_goals=[],
        )
        self.assertEqual(pol_dual.selected_diagnostic_id, "d_high")
        self.assertTrue(pol_dual.schedule_micro_workout)
        self.assertEqual(pol_dual.workout_candidate_pattern_id, "collocation:do_effort")

    # TEST 06: Idempotent recurrence & diagnostic retraction
    def test_06_idempotent_recurrence_and_retraction(self):
        diag_payload = {
            "diagnostics": [
                {
                    "diagnostic_id": "d1",
                    "original_span": "discuss about",
                    "normalized_pattern_id": "grammar:discuss_about",
                    "relevant_context_family": "global",
                    "intended_meaning": "discuss",
                    "kind": "grammar",
                    "severity": "medium",
                    "alternative": "discuss",
                    "alternative_status": "required",
                    "explanation_en": "No about after discuss.",
                    "preserves_intended_meaning": True,
                    "context_fit_verified": True,
                    "confidence_band": "high",
                    "confidence_score": 0.91,
                }
            ]
        }
        # Append same event twice with identical idempotency_key
        _, ev_id = self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="analyst_diagnosis_recorded",
            payload=diag_payload,
            correlation_id="turn_101",
            idempotency_key="idem_diag_101",
        )
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="analyst_diagnosis_recorded",
            payload=diag_payload,
            correlation_id="turn_101",
            idempotency_key="idem_diag_101",
        )
        counts = self.db.get_authoritative_occurrence_counts(self.learner_id)
        self.assertEqual(counts.get(f"{self.learner_id}:grammar:discuss_about:global"), 1)

        # Append a retraction event -> occurrence is excluded without deleting history
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="diagnostic_retracted",
            payload={"target_event_id": ev_id, "diagnostic_id": "d1", "reason": "false_positive"},
            correlation_id="turn_101_ret",
            idempotency_key="idem_ret_101",
        )
        counts_after = self.db.get_authoritative_occurrence_counts(self.learner_id)
        self.assertEqual(counts_after.get(f"{self.learner_id}:grammar:discuss_about:global", 0), 0)

    # TEST 07: Feedback accounting (selected vs delivered)
    def test_07_feedback_accounting(self):
        diag_payload = {
            "diagnostics": [
                {
                    "diagnostic_id": "d1",
                    "original_span": "many works",
                    "normalized_pattern_id": "grammar:many_works",
                    "relevant_context_family": "global",
                    "intended_meaning": "a lot of work",
                    "kind": "grammar",
                    "severity": "medium",
                    "alternative": "a lot of work",
                    "alternative_status": "required",
                    "explanation_en": "Work is uncountable here.",
                    "preserves_intended_meaning": True,
                    "context_fit_verified": True,
                    "confidence_band": "high",
                    "confidence_score": 0.95,
                }
            ]
        }
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="analyst_diagnosis_recorded",
            payload=diag_payload,
            correlation_id="turn_202",
            idempotency_key="idem_diag_202",
        )
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="policy_decision_recorded",
            payload={
                "intervention": "brief_card",
                "selected_pattern_id": "grammar:many_works",
                "selected_context_family": "global",
            },
            correlation_id="turn_202",
            idempotency_key="idem_pol_202",
        )
        with self.db._conn() as conn:
            row = conn.execute(
                "SELECT times_feedback_selected, times_feedback_shown FROM error_pattern_projection WHERE learner_id = ? AND normalized_pattern_id = 'grammar:many_works'",
                (self.learner_id,),
            ).fetchone()
            self.assertEqual(row["times_feedback_selected"], 1)
            self.assertEqual(row["times_feedback_shown"], 0)

        # Now record confirmed card delivery
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="feedback_card_delivered",
            payload={"normalized_pattern_id": "grammar:many_works", "relevant_context_family": "global"},
            correlation_id="turn_202",
            idempotency_key="idem_card_202",
        )
        with self.db._conn() as conn:
            row2 = conn.execute(
                "SELECT times_feedback_shown FROM error_pattern_projection WHERE learner_id = ? AND normalized_pattern_id = 'grammar:many_works'",
                (self.learner_id,),
            ).fetchone()
            self.assertEqual(row2["times_feedback_shown"], 1)

    # TEST 08: Event-log integrity (append-only triggers & composite FK)
    def test_08_event_log_integrity(self):
        # First insert a row into event_log so row-level triggers fire on UPDATE and DELETE
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="learner_message_received",
            payload={"text": "Hello Alex"},
            correlation_id="turn_integrity_1",
            idempotency_key="idem_integrity_1",
        )
        with self.db._conn() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE event_log SET event_type = 'session_ended'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM event_log")

        # Mismatched learner_id and session_id must fail composite foreign key
        self.db.upsert_learner(999999)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.append_event(
                learner_id=999999,
                session_id="sess_1",
                event_type="learner_message_received",
                payload={"text": "Mismatch"},
                correlation_id="c_bad",
                idempotency_key="idem_bad",
            )

    # TEST 09: Deterministic projection replay
    def test_09_projection_replay(self):
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="phrase_exposure_recorded",
            payload={"phrase_id": "run_something_by_someone", "revealed_to_learner": True},
            correlation_id="c_exp_1",
            idempotency_key="idem_exp_1",
        )
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_1",
            event_type="responder_reply_delivered",
            payload={"reply_text": "Sure, what's up?", "asked_optional_question": True},
            correlation_id="c_exp_1",
            idempotency_key="idem_resp_1",
        )
        with self.db._conn() as conn:
            s_before = dict(conn.execute("SELECT * FROM session_state_projection WHERE session_id = 'sess_1'").fetchone())
            p_before = dict(conn.execute("SELECT * FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = 'run_something_by_someone'", (self.learner_id,)).fetchone())

        self.db.rebuild_all_projections()
        with self.db._conn() as conn:
            s_after = dict(conn.execute("SELECT * FROM session_state_projection WHERE session_id = 'sess_1'").fetchone())
            p_after = dict(conn.execute("SELECT * FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = 'run_something_by_someone'", (self.learner_id,)).fetchone())

        self.assertEqual(s_before["turns_since_visible_card"], s_after["turns_since_visible_card"])
        self.assertEqual(s_before["recent_questions_json"], s_after["recent_questions_json"])
        self.assertEqual(p_before["current_stage"], p_after["current_stage"])
        self.assertEqual(p_before["exposure_count"], p_after["exposure_count"])

    # TEST 10: Stable acquisition criteria & recent failure recovery window
    def test_10_stable_acquisition_and_failure_recovery(self):
        self.db.create_session("sess_2", self.learner_id, training_mode="open_scene", register_profile="diplomatic")
        self.db.create_session("sess_3", self.learner_id, training_mode="open_scene", register_profile="casual")
        self.db.create_session("sess_4", self.learner_id, training_mode="open_scene", register_profile="diplomatic")
        base_dt = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

        for idx, (sid, reg, days_add, novel) in enumerate(
            [("sess_1", "casual", 0, False), ("sess_2", "diplomatic", 3, True), ("sess_3", "casual", 8, True)], 1
        ):
            ts = (base_dt + timedelta(days=days_add)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.db.append_event(
                learner_id=self.learner_id,
                session_id=sid,
                event_type="phrase_production_assessed",
                payload={
                    "target_id": "run_something_by_someone",
                    "evidence": "spontaneous_production",
                    "was_revealed_in_task": False,
                    "was_directly_cued": False,
                    "fits_communicative_context": True,
                    "fits_register": True,
                    "phrase_specific_rubric_passed": True,
                    "is_novel_context": novel,
                    "register_profile": reg,
                    "training_mode": "open_scene",
                },
                correlation_id=f"c_{idx}",
                idempotency_key=f"idem_{idx}",
                occurred_at_utc=ts,
            )

        with self.db._conn() as conn:
            row = conn.execute(
                "SELECT current_stage FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = 'run_something_by_someone'",
                (self.learner_id,),
            ).fetchone()
            self.assertEqual(row["current_stage"], "Stable")

        # Record a failed attempt on day 9 -> blocks Stable (reverts to Produced-spontaneously)
        ts_fail = (base_dt + timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.append_event(
            learner_id=self.learner_id,
            session_id="sess_4",
            event_type="phrase_production_assessed",
            payload={
                "target_id": "run_something_by_someone",
                "evidence": "none",
                "failed_attempt": True,
            },
            correlation_id="c_fail",
            idempotency_key="idem_fail",
            occurred_at_utc=ts_fail,
        )
        with self.db._conn() as conn:
            row_after_fail = conn.execute(
                "SELECT current_stage, recent_unretracted_failure FROM phrase_state_projection WHERE learner_id = ? AND phrase_id = 'run_something_by_someone'",
                (self.learner_id,),
            ).fetchone()
            self.assertEqual(row_after_fail["current_stage"], "Produced-spontaneously")
            self.assertEqual(row_after_fail["recent_unretracted_failure"], 1)

    # TEST 11: Application boundary validators & Telegram failure isolation
    def test_11_boundary_validators_and_failure_isolation(self):
        raw_analysis = Analysis(
            diagnostics=[
                Diagnostic(
                    diagnostic_id="d_fake",
                    original_span="text not in learner message",
                    normalized_pattern_id="collocation:fake",
                    intended_meaning="something",
                    kind=IssueKind.COLLOCATION,
                    severity=Severity.LOW,
                    alternative="real phrase",
                    alternative_status=AlternativeStatus.OPTIONAL,
                    explanation_en="Hallucinated span.",
                    preserves_intended_meaning=True,
                    context_fit_verified=True,
                    confidence_band=ConfidenceBand.HIGH,
                    confidence_score=0.90,
                )
            ]
        )
        cleaned = validate_analyst_output_against_turn(
            analysis=raw_analysis,
            learner_text="Hey Alex, how is your week going?",
            revealed_target_ids=set(),
            directly_cued_target_ids=set(),
        )
        self.assertEqual(len(cleaned.diagnostics), 0)

        # Leaked stealth target phrase in ResponderOutput must raise ValueError
        dec = DialogueDecision(
            act=ConversationalAct.CONTRIBUTE,
            style=SurfaceStyle.PLAIN,
            allow_question=False,
            max_questions=0,
        )
        with self.assertRaises(ValueError):
            validate_responder_output(
                output=ResponderOutput(reply_text="You can always run something by someone first."),
                decision=dec,
                stealth_target_form="run something by someone",
            )

    # TEST 12: End-to-End 6-step TurnOrchestrator & deterministic repair
    def test_12_end_to_end_orchestrator_pipeline(self):
        ai = DualLLMEngine(keys=["AIzaSyDummyKeyForDeterministicTest12345"])

        def mock_gemini_call(*, system_prompt, user_prompt, validator_fn, temperature, max_tries=2):
            if temperature < 0.4:
                return validator_fn(
                    {
                        "communicative_intention": "share_story",
                        "learner_invites_question": False,
                        "communicative_goal_achieved": True,
                        "requires_clarification": False,
                        "in_emotional_or_story_flow": False,
                        "diagnostics": [
                            {
                                "diagnostic_id": "d1",
                                "original_span": "discussed about our plans",
                                "normalized_pattern_id": "grammar:discuss_about",
                                "relevant_context_family": "global",
                                "intended_meaning": "talked about our plans",
                                "kind": "grammar",
                                "severity": "medium",
                                "alternative": "discussed our plans",
                                "secondary_alternatives": [],
                                "alternative_status": "required",
                                "recast_candidate": "talked over your plans",
                                "explanation_en": "'Discuss' takes a direct object without 'about'.",
                                "explanation_ar": "الفعل discuss يتعدى بنفسه دون حرف الجر about.",
                                "l1_transfer_likely": True,
                                "l1_transfer_evidence": "Arabic تناقشنا في/حول transfer.",
                                "preserves_intended_meaning": True,
                                "context_fit_verified": True,
                                "confidence_band": "high",
                                "confidence_score": 0.93,
                            }
                        ],
                        "target_assessments": [],
                    }
                )
            return validator_fn(
                {
                    "reply_text": "Nice, sounds like you guys finally talked over your plans for the trip.",
                    "second_bubble_text": None,
                    "recast_used": True,
                    "elicitation_used": False,
                }
            )

        ai._call_gemini_json = mock_gemini_call
        orch = TurnOrchestrator(db=self.db, ai=ai)
        res = orch.process_turn(
            learner_id=self.learner_id,
            learner_text="Yesterday I met my friend and we discussed about our plans.",
            message_idempotency_key="turn_e2e_1",
        )
        self.assertTrue(res.responder_output.recast_used)
        self.assertEqual(res.policy_decision.intervention, InterventionLevel.BRIEF_CARD)
        self.assertIsNotNone(res.card_id)

        orch.confirm_card_delivered(
            learner_id=self.learner_id,
            session_id=res.session_id,
            correlation_id=res.correlation_id,
            diagnostic=res.selected_diagnostic,
        )
        with self.db._conn() as conn:
            ep = conn.execute("SELECT times_feedback_shown FROM error_pattern_projection WHERE learner_id = ?", (self.learner_id,)).fetchone()
            self.assertEqual(ep["times_feedback_shown"], 1)



    # TEST 13: Live Control Panel settings persistence (Silent, Active, Directness, Register, Arabic)
    def test_13_settings_buttons_persistence(self):
        # Switch to 'silent'
        self.db.update_learner_preference(self.learner_id, feedback_preference="silent")
        # Calling get_or_create_active_session and get_learner_settings must NOT reset it to 'light'
        _ = self.db.get_or_create_active_session(self.learner_id)
        cfg_silent = self.db.get_learner_settings(self.learner_id)
        self.assertEqual(cfg_silent["feedback_preference"], "silent")

        # Switch to 'active', 'direct', 'executive', and 'english_only'
        self.db.update_learner_preference(
            self.learner_id,
            feedback_preference="active",
            default_register="executive",
            default_directness="direct",
            arabic_support="english_only",
        )
        _ = self.db.get_or_create_active_session(self.learner_id)
        cfg_active = self.db.get_learner_settings(self.learner_id)
        self.assertEqual(cfg_active["feedback_preference"], "active")
        self.assertEqual(cfg_active["default_register"], "executive")
        self.assertEqual(cfg_active["default_directness"], "direct")
        self.assertEqual(cfg_active["arabic_support"], "english_only")



    def test_14_arabic_english_formatting_consistency(self):
        from policy_engine import normalize_arabic_english_formatting

        self.assertEqual(
            normalize_arabic_english_formatting("في الإنجليزية نستخدم «turn on» للأجهزة وليس «open» كما في العربية «افتح المكيف»."),
            "في الإنجليزية نستخدم turn on للأجهزة وليس open كما في العربية «افتح المكيف».",
        )
        self.assertEqual(
            normalize_arabic_english_formatting("تجنب تكرار الضمير العائد («it») في نهاية الجملة."),
            "تجنب تكرار الضمير العائد it في نهاية الجملة.",
        )



    def test_15_zero_benchmark_leakage_in_prompts(self):
        """Ensures zero blind-test sentences or ChatGPT example phrases were hardcoded into ai_engine prompts."""
        ai_source = Path("ai_engine.py").read_text(encoding="utf-8")
        forbidden_benchmark_literals = [
            "Hey, I can't make it today",
            "Something came up",
            "an urgent matter has arisen",
            "today's engagement",
            "Take easy man and kick back",
            "get the cool air blowing",
            "crank up the AC",
            "I don't want to waste my evening",
            "Well, that went great",
            "Whaddaya think? Mighta gone better",
        ]
        for phrase in forbidden_benchmark_literals:
            self.assertNotIn(phrase.lower(), ai_source.lower(), f"Bias/Overfitting detected: '{phrase}' found in ai_engine.py!")

    def test_16_workout_task_completion_and_reinforce(self):
        """Verifies workout task tracking separates linguistic diagnostics from communicative task completion."""
        from policy_engine import (
            get_active_workout_task,
            register_workout_task,
            update_workout_task_after_turn,
        )
        from schemas import DynamicWorkoutScenario

        scen = DynamicWorkoutScenario(
            communicative_intention="request_action",
            situation_setup="You and your friend just sat down in a warm room.",
            prompt_question="Ask your friend to get some fresh air moving.",
        )
        register_workout_task(scen, {"canonical_form": "turn on the fan", "target_id": "t_fan"}, learner_id="u_work")
        active = get_active_workout_task("u_work")
        self.assertIsNotNone(active)
        self.assertEqual(active["status"], "ACTIVE")

        # Turn 1: Learner misses the communicative task -> MISSED + reinforce_required=True
        res1 = update_workout_task_after_turn("u_work", goal_achieved=False)
        self.assertEqual(res1["task_status"], "MISSED")
        self.assertTrue(res1["reinforce_required"])
        self.assertIsNotNone(get_active_workout_task("u_work"))

        # Turn 2: Learner fulfills the communicative task -> FULFILLED + task cleared
        res2 = update_workout_task_after_turn("u_work", goal_achieved=True)
        self.assertEqual(res2["task_status"], "FULFILLED")
        self.assertFalse(res2["reinforce_required"])
        self.assertIsNone(get_active_workout_task("u_work"))

    def test_17_listen_decode_two_phase_state_machine(self):
        """Verifies Listen & Decode Phase 1 never leaks the unpacked form, IPA, or subtext before learner reply."""
        from policy_engine import (
            complete_decode_session,
            format_decode_phase1_message,
            format_decode_phase2_reveal,
            get_active_decode_session,
            start_decode_session,
        )
        from schemas import DecodeEvaluationResult, ListenDecodeChallenge

        ch = ListenDecodeChallenge(
            context_line="Two colleagues chatting after a client call.",
            spoken_clip_text="Didja catch what she meant at the end?",
            full_unreduced_form="Did you catch what she meant at the end?",
            acoustic_reduction_note="'Did you' assimilates into 'Didja' (/dɪdʒə/).",
            pragmatic_subtext_en="The speaker suspects there was a hidden message in the client's closing remark.",
            pragmatic_subtext_ar="يشك المتحدث في وجود رسالة مبطنة في تعليق العميل الأخير.",
            decode_question="Why is the speaker asking about the end of the call?",
        )
        start_decode_session("u_dec", ch, "chat_1")
        self.assertIsNotNone(get_active_decode_session("u_dec"))

        phase1_msg = format_decode_phase1_message(ch, "casual")
        self.assertIn("Didja catch what she meant at the end?", phase1_msg)
        self.assertIn("Why is the speaker asking about the end of the call?", phase1_msg)
        # Zero premature leakage allowed in Phase 1
        self.assertNotIn("Did you catch", phase1_msg)
        self.assertNotIn("/dɪdʒə/", phase1_msg)
        self.assertNotIn("hidden message", phase1_msg)
        self.assertNotIn("رسالة مبطنة", phase1_msg)

        # Phase 2: After learner replies, evaluation + full breakdown is revealed and session completes
        eval_res = DecodeEvaluationResult(
            comprehension_accurate=True,
            coach_feedback_en="Exactly—you spotted that the speaker noticed a subtle hint.",
            coach_feedback_ar="أحسنت، التقطت إشارة المتحدث إلى التلميح الخفي.",
        )
        complete_decode_session("u_dec")
        self.assertIsNone(get_active_decode_session("u_dec"))
        phase2_msg = format_decode_phase2_reveal(ch, eval_res)
        self.assertIn("Did you catch what she meant at the end?", phase2_msg)
        self.assertIn("/dɪdʒə/", phase2_msg)
        self.assertIn("hidden message", phase2_msg)



    def test_18_user_guide_and_bot_commands_sync(self):
        """Verifies the updated 5-chapter v2 user guide and Telegram commands/handlers in app.py."""
        from guide_and_branding import GUIDE_CHAPTERS_HTML

        for ch_key in ("overview", "ch1_layers", "ch2_modes", "ch3_registers", "ch4_mastery"):
            self.assertIn(ch_key, GUIDE_CHAPTERS_HTML)
        self.assertIn("2-Phase Listen", GUIDE_CHAPTERS_HTML["overview"])
        self.assertIn("Spoken-First Register Shift", GUIDE_CHAPTERS_HTML["overview"])
        self.assertIn("2 distinct formality bands", GUIDE_CHAPTERS_HTML["ch4_mastery"])

        app_src = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("force_sync_all_telegram_scopes_and_profile", app_src)
        self.assertIn("format_decode_phase1_message", app_src)
        self.assertIn("format_decode_phase2_reveal", app_src)
        self.assertIn("register_workout_task", app_src)
        self.assertIn("_get_learner_lock", app_src)
        self.assertNotIn("<tg-spoiler>{_e(ch.full_unreduced_form)}", app_src)



    def test_19_alex_reply_tts_every_turn(self):
        """Verifies clean extraction of Alex's spoken text and universal 🎧 Listen button wiring."""
        from policy_engine import extract_alex_spoken_text, get_auto_voice_pref, set_auto_voice_pref

        # 1. Normal chat reply
        self.assertEqual(
            extract_alex_spoken_text("💬 Alex: That sounds like a solid plan."),
            "That sounds like a solid plan.",
        )
        # 2. Expanded Quick Upgrade card (must NOT read the diagnostic card or Arabic notes)
        card_msg = (
            "💬 Alex: Fair point, let's push it to Monday.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Quick Upgrade (usage)\n"
            "• You said: “postpone”"
        )
        self.assertEqual(
            extract_alex_spoken_text(card_msg),
            "Fair point, let's push it to Monday.",
        )
        # 3. Pragmatic Dilemma opening message
        dilemma_msg = (
            "🤝 Live Pragmatic Dilemma (Office)\n"
            "• Active Register: EXECUTIVE\n\n"
            "💬 Alex: “We need to cut scope if we want to ship Friday.”"
        )
        self.assertEqual(
            extract_alex_spoken_text(dilemma_msg),
            "We need to cut scope if we want to ship Friday.",
        )

        # 4. Auto-voice toggle state
        self.assertFalse(get_auto_voice_pref("u_tts"))
        self.assertTrue(set_auto_voice_pref("u_tts", True))
        self.assertTrue(get_auto_voice_pref("u_tts"))
        set_auto_voice_pref("u_tts", False)

        app_src = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('callback_data="tts:msg"', app_src)
        self.assertIn("extract_alex_spoken_text", app_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
