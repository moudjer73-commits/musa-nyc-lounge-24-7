"""Builders shared by the S1 tests: v1 payloads as the live v2 code wrote them, V2 payload dicts,
and a golden v2 database that contains all 14 event types."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any, Optional

from schemas import (
    Analysis,
    AlternativeStatus,
    ConfidenceBand,
    Diagnostic,
    InterventionLevel,
    IssueKind,
    PolicyDecision,
    Severity,
    TargetUseAssessment,
    ProductionEvidence,
)

# ---------------------------------------------------------------- v1 payloads (what the v2 code stored)


def v1_diagnostic(
    diag_id: str = "d1",
    span: str = "discuss about",
    pattern: str = "grammar:discuss_about",
    kind: IssueKind = IssueKind.GRAMMAR,
    severity: Severity = Severity.MEDIUM,
    alternative: Optional[str] = "discuss",
    status: AlternativeStatus = AlternativeStatus.REQUIRED,
    band: ConfidenceBand = ConfidenceBand.HIGH,
    score: float = 0.91,
    family: str = "global",
    meaning: Optional[str] = "talk about the plan",
) -> Diagnostic:
    return Diagnostic(
        diagnostic_id=diag_id,
        original_span=span,
        normalized_pattern_id=pattern,
        relevant_context_family=family,
        intended_meaning=meaning,
        kind=kind,
        severity=severity,
        alternative=alternative,
        alternative_status=status,
        explanation_en="English verbs differ in whether they take a preposition.",
        preserves_intended_meaning=True,
        context_fit_verified=True,
        confidence_band=band,
        confidence_score=score,
    )


def v1_analysis_payload(*diagnostics: Diagnostic, target_assessments: Optional[list[TargetUseAssessment]] = None) -> dict[str, Any]:
    return Analysis(
        communicative_intention="share_plan",
        diagnostics=list(diagnostics),
        target_assessments=target_assessments or [],
    ).model_dump(mode="json")


def v1_policy_payload(
    intervention: InterventionLevel = InterventionLevel.BRIEF_CARD,
    selected: Optional[str] = "d1",
    pattern: Optional[str] = "grammar:discuss_about",
    family: str = "global",
) -> dict[str, Any]:
    payload = PolicyDecision(intervention=intervention, selected_diagnostic_id=selected).model_dump(mode="json")
    payload["selected_pattern_id"] = pattern
    payload["selected_context_family"] = family
    return payload


def v2_diagnostic_dict(dg_id: str = "dg_0a1b2c3d", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "diagnostic_id": dg_id,
        "original_span": "discuss about",
        "normalized_pattern_id": "grammar:discuss_about",
        "relevant_context_family": "global",
        "intended_meaning": "talk about the plan",
        "kind": "grammar",
        "severity": "medium",
        "alternative": "discuss",
        "secondary_alternatives": [],
        "alternative_status": "required",
        "explanation_en": "English verbs differ in whether they take a preposition.",
        "preserves_intended_meaning": True,
        "context_fit_verified": True,
        "confidence_band": "high",
        "confidence_score": 0.91,
        "domain": "linguistic",
        "classification": "error",
        "repair_scope": "phrase_span",
        "timing_hint": None,
        "span_start": 7,
        "span_end": 20,
        "communicative_impact": 2,
        "learning_value": 2,
        "linguistic_confidence": 0.91,
        "pragmatic_confidence": None,
        "acoustic_confidence": None,
    }
    base.update(over)
    return base


def v2_analysis_dict(turn_event_id: str, *diagnostics: dict[str, Any], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "turn_event_id": turn_event_id,
        "rubric_version": "v2.0",
        "communicative": {},
        "diagnostics": list(diagnostics),
        "target_assessments": [],
        "p0": {"candidate": False, "line": None},
        "dropped": [],
        "call_meta": None,
    }
    base.update(over)
    return base


def v2_policy_dict(turn_event_id: str, *decisions: dict[str, Any], selected: Optional[str] = None) -> dict[str, Any]:
    return {
        "turn_event_id": turn_event_id,
        "decisions": list(decisions),
        "selected_diagnostic_id": selected,
        "budget": {
            "preference": "active",
            "turns_since_visible_card": 5,
            "cards_in_scene": 0,
            "strain": 0.0,
            "allowed": True,
        },
        "curveball_turn": False,
        "pending_clarification": None,
    }


def decision_dict(dg_id: str, priority_class: int = 1, score: float = 70.0, delivery: str = "end_of_turn") -> dict[str, Any]:
    return {
        "diagnostic_id": dg_id,
        "priority_class": priority_class,
        "priority_score": score,
        "delivery": delivery,
        "reasons": [],
        "gates": {},
    }


# ---------------------------------------------------------------- raw event_log rows (for upcast tests)


def raw_row(event_type: str, payload_json: str, *, schema_version: str = "1.0", event_id: str = "ev_000000000001", **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_seq": 1,
        "event_id": event_id,
        "learner_id": 1001,
        "session_id": "s1",
        "event_type": event_type,
        "occurred_at_utc": "2026-10-01T09:00:00.000000Z",
        "recorded_at_utc": "2026-10-01T09:00:00.000000Z",
        "schema_version": schema_version,
        "model_version": None,
        "rubric_version": "v1.0" if schema_version.startswith("1") else "v2.0",
        "correlation_id": "turn_x",
        "causation_id": "ev_msg00000001",
        "idempotency_key": f"idem_{event_id}",
        "payload_json": payload_json,
    }
    row.update(over)
    return row


# ---------------------------------------------------------------- golden v2 database


LEARNER_A = 1001
LEARNER_B = 1002

PROJECTION_TABLES_V1 = ("session_state_projection", "phrase_state_projection", "error_pattern_projection")


def build_golden_v2_db(path: str) -> dict[str, Any]:
    """A database exactly as the v2 code leaves it: v2 schema, v1 payloads, projections current.

    Contains all 14 live event types, two learners, three sessions for learner A, a retracted
    diagnostic, a retracted piece of phrase evidence, a phrase that reaches Stable, and two v1
    diagnostics that share the id 'd1' (v1 ids are only unique inside one analysis).
    Returns facts the tests assert on.
    """
    import legacy_v2_database as legacy  # noqa: PLC0415 (path is added by conftest)

    db = legacy.EventSourcedDatabase(path)
    db.upsert_learner(LEARNER_A, feedback_preference="active")
    db.upsert_learner(LEARNER_B)
    db.create_session("g_s1", LEARNER_A, training_mode="open_scene", register_profile="casual", started_at_utc="2026-09-01T09:00:00.000000Z")
    db.create_session("g_s2", LEARNER_A, training_mode="open_scene", register_profile="diplomatic", started_at_utc="2026-09-04T09:00:00.000000Z")
    db.create_session("g_s3", LEARNER_A, training_mode="open_scene", register_profile="casual", started_at_utc="2026-09-10T09:00:00.000000Z")
    db.create_session("g_b1", LEARNER_B, training_mode="open_scene", register_profile="casual", started_at_utc="2026-09-02T09:00:00.000000Z")

    counter = {"n": 0}

    def put(learner: int, session: str, etype: str, payload: dict[str, Any], when: str, corr: str, *, event_id: Optional[str] = None, causation: Optional[str] = None) -> str:
        counter["n"] += 1
        inserted, ev_id = db.append_event(
            learner_id=learner,
            session_id=session,
            event_type=etype,
            payload=payload,
            correlation_id=corr,
            idempotency_key=f"golden:{counter['n']}",
            causation_id=causation,
            occurred_at_utc=when,
            event_id=event_id,
        )
        assert inserted
        return ev_id

    # --- learner A, scene 1, turn 1
    put(LEARNER_A, "g_s1", "session_started", {"training_mode": "open_scene"}, "2026-09-01T09:00:01.000000Z", "boot_1", event_id="ev_g_start0001")
    msg1 = put(LEARNER_A, "g_s1", "learner_message_received", {"text": "I want to discuss about this plan.", "is_voice": False}, "2026-09-01T09:01:00.000000Z", "turn_1", event_id="ev_g_msg000001")
    diag1 = put(
        LEARNER_A, "g_s1", "analyst_diagnosis_recorded",
        v1_analysis_payload(
            v1_diagnostic("d1"),
            v1_diagnostic("d2", span="plan", pattern="style:plan", kind=IssueKind.OPTIONAL_UPGRADE, severity=Severity.LOW,
                          alternative="proposal", status=AlternativeStatus.OPTIONAL, score=0.82),
            v1_diagnostic("d3", span="this", pattern="ctx:this", kind=IssueKind.INSUFFICIENT_CONTEXT, severity=Severity.LOW,
                          alternative=None, status=AlternativeStatus.OPTIONAL, band=ConfidenceBand.LOW, score=0.4, meaning=None),
            v1_diagnostic("d4", span="I want", pattern="pragmatics:i_want", kind=IssueKind.PRAGMATICS, family="workplace",
                          alternative="I'd like", status=AlternativeStatus.RECOMMENDED, band=ConfidenceBand.MEDIUM, score=0.7),
        ),
        "2026-09-01T09:01:02.000000Z", "turn_1", event_id="ev_g_diag00001", causation=msg1,
    )
    put(LEARNER_A, "g_s1", "policy_decision_recorded", v1_policy_payload(), "2026-09-01T09:01:03.000000Z", "turn_1", event_id="ev_g_pol000001", causation=diag1)
    put(LEARNER_A, "g_s1", "responder_reply_delivered",
        {"reply_text": "Sure, what's up?", "second_bubble_text": None, "recast_used": False, "asked_optional_question": True, "was_clarification_question": False},
        "2026-09-01T09:01:04.000000Z", "turn_1", event_id="ev_g_reply00001", causation=msg1)
    put(LEARNER_A, "g_s1", "feedback_card_delivered",
        {"diagnostic_id": "d1", "normalized_pattern_id": "grammar:discuss_about", "relevant_context_family": "global"},
        "2026-09-01T09:01:30.000000Z", "turn_1", event_id="ev_g_card00001")
    put(LEARNER_A, "g_s1", "phrase_exposure_recorded", {"phrase_id": "run_something_by_someone", "revealed_to_learner": True, "directly_cued": False},
        "2026-09-01T09:01:31.000000Z", "turn_1", event_id="ev_g_expo00001")
    put(LEARNER_A, "g_s1", "micro_workout_scheduled", {"normalized_pattern_id": "grammar:discuss_about", "relevant_context_family": "global"},
        "2026-09-01T09:01:32.000000Z", "turn_1", event_id="ev_g_wosch0001")
    put(LEARNER_A, "g_s1", "micro_workout_completed", {"normalized_pattern_id": "grammar:discuss_about", "relevant_context_family": "global"},
        "2026-09-01T09:05:00.000000Z", "turn_1b", event_id="ev_g_wodone001")

    # --- learner A, scene 1, turn 2: a second diagnostic that is also called 'd1', later retracted
    msg2 = put(LEARNER_A, "g_s1", "learner_message_received", {"text": "Give me the report today"}, "2026-09-01T09:06:00.000000Z", "turn_2", event_id="ev_g_msg000002")
    diag2 = put(
        LEARNER_A, "g_s1", "analyst_diagnosis_recorded",
        v1_analysis_payload(v1_diagnostic("d1", span="Give me", pattern="pragmatics:give_me", kind=IssueKind.PRAGMATICS, family="workplace",
                                          alternative="Could you send me", status=AlternativeStatus.RECOMMENDED, severity=Severity.HIGH)),
        "2026-09-01T09:06:02.000000Z", "turn_2", event_id="ev_g_diag00002", causation=msg2,
    )
    put(LEARNER_A, "g_s1", "policy_decision_recorded", v1_policy_payload(InterventionLevel.NONE, None, None), "2026-09-01T09:06:03.000000Z", "turn_2", event_id="ev_g_pol000002", causation=diag2)
    put(LEARNER_A, "g_s1", "responder_reply_delivered",
        {"reply_text": "Which report do you mean?", "second_bubble_text": None, "recast_used": False, "asked_optional_question": False, "was_clarification_question": True},
        "2026-09-01T09:06:04.000000Z", "turn_2", event_id="ev_g_reply00002", causation=msg2)
    put(LEARNER_A, "g_s1", "diagnostic_retracted", {"target_event_id": diag2, "diagnostic_id": "d1", "reason": "false_positive"},
        "2026-09-01T09:07:00.000000Z", "turn_2_ret", event_id="ev_g_retract001")

    # --- phrase evidence: three spontaneous productions across three sessions (reaches Stable)
    for idx, (sess, reg, when, novel) in enumerate(
        [("g_s1", "casual", "2026-09-01T10:00:00.000000Z", False), ("g_s2", "diplomatic", "2026-09-04T10:00:00.000000Z", True), ("g_s3", "casual", "2026-09-10T10:00:00.000000Z", True)], 1
    ):
        put(LEARNER_A, sess, "phrase_production_assessed",
            {"target_id": "run_something_by_someone", "evidence": "spontaneous_production", "was_revealed_in_task": False,
             "was_directly_cued": False, "fits_communicative_context": True, "fits_register": True,
             "phrase_specific_rubric_passed": True, "is_novel_context": novel, "register_profile": reg, "training_mode": "open_scene"},
            when, f"assess_{idx}", event_id=f"ev_g_assess{idx:04d}")
    put(LEARNER_A, "g_s2", "phrase_production_assessed",
        {"target_id": "plate_is_full", "evidence": "cued_production", "was_revealed_in_task": False, "was_directly_cued": True,
         "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": True,
         "register_profile": "diplomatic", "training_mode": "phrase_workout"},
        "2026-09-04T10:05:00.000000Z", "assess_cued", event_id="ev_g_assesscued")
    put(LEARNER_A, "g_s2", "phrase_production_assessed",
        {"target_id": "talk_something_over", "evidence": "recognition", "was_revealed_in_task": False, "was_directly_cued": False,
         "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": True,
         "register_profile": "diplomatic", "training_mode": "listen_and_decode"},
        "2026-09-04T10:06:00.000000Z", "assess_recog", event_id="ev_g_assessrec1")
    bad = put(LEARNER_A, "g_s3", "phrase_production_assessed",
              {"target_id": "cuts_both_ways", "evidence": "independent_production", "was_revealed_in_task": False, "was_directly_cued": False,
               "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": True,
               "register_profile": "casual", "training_mode": "open_scene"},
              "2026-09-10T10:10:00.000000Z", "assess_bad", event_id="ev_g_assessbad1")
    put(LEARNER_A, "g_s3", "phrase_evidence_retracted", {"target_event_id": bad, "reason": "judge_overruled"},
        "2026-09-10T10:20:00.000000Z", "assess_bad_ret", event_id="ev_g_evretract1")
    put(LEARNER_A, "g_s2", "review_completed", {"phrase_id": "plate_is_full", "result": "ok"}, "2026-09-11T08:00:00.000000Z", "review_1", event_id="ev_g_review0001")
    put(LEARNER_A, "g_s1", "session_ended", {"reason": "learner_ended"}, "2026-09-11T09:00:00.000000Z", "end_1", event_id="ev_g_end0000001")

    # --- learner B: a short exchange
    mb = put(LEARNER_B, "g_b1", "learner_message_received", {"text": "Hello Alex", "is_voice": True}, "2026-09-02T09:01:00.000000Z", "turn_b1", event_id="ev_g_bmsg000001")
    put(LEARNER_B, "g_b1", "responder_reply_delivered",
        {"reply_text": "Hey!", "second_bubble_text": None, "recast_used": False, "asked_optional_question": False, "was_clarification_question": False},
        "2026-09-02T09:01:02.000000Z", "turn_b1", event_id="ev_g_breply0001", causation=mb)

    with closing(sqlite3.connect(path)) as conn:
        types = {r[0] for r in conn.execute("SELECT DISTINCT event_type FROM event_log")}
    return {"event_types": types, "diag2_event_id": diag2, "diag1_event_id": diag1}


def table_rows(path: str, table: str, columns: str = "*", order: str = "rowid") -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return [tuple(r) for r in conn.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")]
