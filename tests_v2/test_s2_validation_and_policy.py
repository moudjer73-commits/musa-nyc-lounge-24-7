"""Sprint S2 Validation, Policy & Orchestrator CAS Acceptance Tests (S0 Contracts, Section 8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from clock import FakeClock
from database import EventSourcedDatabase
from interaction import (
    CALLBACK_MAX_BYTES,
    PolicyInputs,
    build_callback,
    parse_callback,
    resolve_interaction_policy,
)
from orchestrator import AnalysisOutcome, TurnHandle, TurnOrchestrator
from schemas_v3 import (
    Classification,
    ComprehensionStatus,
    Delivery,
    DiagnosticV2,
    Domain,
    DropReason,
    POSignal,
    PriorityClass,
    RepairScope,
)
from validation import AnalystRawV2, normalize_text, validate_analysis


def _raw_diag(**overrides) -> dict:
    base = {
        "kind": "grammar",
        "severity": "medium",
        "original_span": "discussed about",
        "alternative": "discussed",
        "alternative_status": "recommended",
        "explanation_en": "Use 'discuss' directly without 'about'.",
        "explanation_ar": "نستخدم الفعل discuss مباشرة دون حرف الجر about.",
        "pattern_id": "verb_prep_discuss",
        "domain": "linguistic",
        "classification": "error",
        "repair_scope": "phrase_span",
        "communicative_impact": 2,
        "learning_value": 2,
        "linguistic_confidence": 0.92,
    }
    base.update(overrides)
    return base


def test_s2_validation_token_boundary_and_missing_span() -> None:
    learner_text = "I was thinking about the project timeline."
    raw = {
        "diagnostics": [
            # "in" appears inside "thinking", NOT as a standalone token -> SPAN_BOUNDARY
            _raw_diag(original_span="in", alternative="on", pattern_id="prep_choice"),
            # "completely absent" does not appear at all -> SPAN_MISSING
            _raw_diag(original_span="completely absent", alternative="here", pattern_id="missing_test"),
            # Valid contiguous token span
            _raw_diag(original_span="thinking about", alternative="considering", classification="upgrade", alternative_status="optional", pattern_id="lexical_upgrade"),
        ]
    }
    res = validate_analysis(raw, learner_text=learner_text, input_modality="text")
    assert len(res.diagnostics) == 1
    assert res.diagnostics[0].original_span == "thinking about"
    reasons = [d.reason for d in res.dropped]
    assert DropReason.SPAN_BOUNDARY in reasons
    assert DropReason.SPAN_MISSING in reasons


def test_s2_validation_curly_quotes_and_surface_casing_preservation() -> None:
    learner_text = "Honestly, I Don’t Agree with that approach."
    raw = {
        "diagnostics": [
            _raw_diag(
                original_span="i don't agree",
                alternative="I see it differently",
                classification="alternative",
                alternative_status="optional",
                pattern_id="polite_disagreement",
                domain="pragmatic",
                pragmatic_confidence=0.90,
            )
        ]
    }
    res = validate_analysis(raw, learner_text=learner_text, input_modality="text")
    assert len(res.diagnostics) == 1
    # Exact surface casing and curly apostrophe from learner_text are preserved in original_span
    assert res.diagnostics[0].original_span == "I Don’t Agree"
    assert learner_text[res.diagnostics[0].span_start : res.diagnostics[0].span_end] == "I Don’t Agree"


def test_s2_validation_noop_overlap_and_utterance_rewrite() -> None:
    learner_text = "Yesterday I discussed about the budget problem with my team."
    raw = {
        "diagnostics": [
            # No-op: identical after normalization
            _raw_diag(original_span="discussed about", alternative="Discussed   about", pattern_id="noop_pat"),
            # Lower priority overlapping span (low severity)
            _raw_diag(
                original_span="discussed about the budget",
                alternative="talked over the budget",
                severity="low",
                communicative_impact=1,
                learning_value=1,
                linguistic_confidence=0.82,
                pattern_id="low_overlap",
            ),
            # Higher priority overlapping span (high severity)
            _raw_diag(
                original_span="discussed about",
                alternative="discussed",
                severity="high",
                communicative_impact=3,
                learning_value=3,
                linguistic_confidence=0.96,
                pattern_id="high_overlap",
            ),
        ]
    }
    res = validate_analysis(raw, learner_text=learner_text, input_modality="text")
    assert len(res.diagnostics) == 1
    assert res.diagnostics[0].pattern_id == "high_overlap"
    reasons = [d.reason for d in res.dropped]
    assert DropReason.NOOP in reasons
    assert DropReason.OVERLAP in reasons

    # utterance_rewrite ignores LLM span and covers the entire learner_text
    raw_rewrite = {
        "diagnostics": [
            _raw_diag(
                original_span="partial",
                alternative="Yesterday I went over the budget issue with my team.",
                repair_scope="utterance_rewrite",
                classification="upgrade",
                alternative_status="optional",
                pattern_id="full_rewrite",
            )
        ]
    }
    res_rw = validate_analysis(raw_rewrite, learner_text=learner_text, input_modality="text")
    assert len(res_rw.diagnostics) == 1
    assert res_rw.diagnostics[0].span_start == 0
    assert res_rw.diagnostics[0].span_end == len(learner_text)
    assert res_rw.diagnostics[0].original_span == learner_text


def test_s2_validation_domain_modality_classification_tone_abstain_and_echo() -> None:
    learner_text = "I see where you're coming from, but we need more time."
    raw = {
        "diagnostics": [
            # 1. Phonological domain rejected in V3.0
            _raw_diag(domain="phonological", original_span="where", alternative="were"),
            # 2. Non-null acoustic_confidence rejected in V3.0
            _raw_diag(acoustic_confidence=0.9, original_span="where", alternative="were"),
            # 3. Pronunciation claim on text input rejected
            _raw_diag(
                kind="pronunciation",
                explanation_en="You mispronounced the vowel sound.",
                original_span="coming",
                alternative="going",
            ),
            # 4. Classification mismatch: upgrade with required status
            _raw_diag(
                classification="upgrade",
                alternative_status="required",
                original_span="need more time",
                alternative="could use a bit more time",
            ),
            # 5. Invalid pattern_id code
            _raw_diag(
                pattern_id="INVALID-UPPERCASE!",
                original_span="need more time",
                alternative="need extra time",
            ),
            # 6. Upgrade note claiming learner sentence is "wrong" / "خطأ" -> NOTE_TONE
            _raw_diag(
                classification="upgrade",
                alternative_status="optional",
                original_span="need more time",
                alternative="could use some extra time",
                explanation_en="Your phrasing is wrong here.",
                explanation_ar="هذه الجملة خاطئة.",
            ),
            # 7. Abstention below 0.80
            _raw_diag(
                linguistic_confidence=0.65,
                original_span="need more time",
                alternative="require more time",
            ),
        ],
        "target_assessments": [
            {
                "phrase_id": "push_back_softly",
                "used": True,
                "matched_span": "I see where you're coming from",
            }
        ],
    }
    res = validate_analysis(
        raw,
        learner_text=learner_text,
        input_modality="text",
        recent_bot_texts=["Listen: I see where you're coming from, let's think about it."],
        active_target_ids={"push_back_softly"},
        abstain_threshold=0.80,
    )
    assert len(res.diagnostics) == 0
    reasons = [d.reason for d in res.dropped]
    assert reasons.count(DropReason.DOMAIN_MODALITY) == 3
    assert DropReason.CLASSIFICATION_MISMATCH in reasons
    assert DropReason.PATTERN_CODE in reasons
    assert DropReason.NOTE_TONE in reasons
    assert DropReason.ABSTAIN in reasons
    assert DropReason.ECHO in reasons
    assert res.target_assessments[0]["echo"] is True


def test_s2_policy_matrix_p0_p1_p4_budgets_and_gates() -> None:
    p1_err = DiagnosticV2(
        kind="grammar",
        severity="high",
        original_span="discussed about",
        alternative="discussed",
        alternative_status="required",
        pattern_id="verb_prep",
        diagnostic_id="dg_11111111",
        domain=Domain.LINGUISTIC,
        classification=Classification.ERROR,
        repair_scope=RepairScope.PHRASE_SPAN,
        communicative_impact=3,
        learning_value=3,
        linguistic_confidence=0.95,
    )
    p4_upg = DiagnosticV2(
        kind="optional_upgrade",
        severity="low",
        original_span="very good idea",
        alternative="pretty solid idea",
        alternative_status="optional",
        pattern_id="adj_solid",
        diagnostic_id="dg_22222222",
        domain=Domain.LINGUISTIC,
        classification=Classification.UPGRADE,
        repair_scope=RepairScope.PHRASE_SPAN,
        communicative_impact=2,
        learning_value=2,
        linguistic_confidence=0.90,
    )

    # 1. P0 Path A (comprehension_status != ok) -> deferred with 'handled_in_reply'
    dec_p0_a = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_1",
            comprehension_status=ComprehensionStatus.AMBIGUOUS,
            p0=POSignal(candidate=True, line="Did you mean this week or next week?"),
            diagnostics=[p1_err],
        )
    )
    assert dec_p0_a.selected_diagnostic_id is None
    assert dec_p0_a.decisions[0].delivery == Delivery.DEFERRED
    assert "handled_in_reply" in dec_p0_a.decisions[0].reasons

    # 2. P0 Path B (comprehension_status == ok, p0.candidate == True, turns_since_p0 >= 3) -> immediate_followup
    dec_p0_b = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_2",
            comprehension_status=ComprehensionStatus.OK,
            p0=POSignal(candidate=True, line="Wait—do you want him to join or not?"),
            diagnostics=[p1_err, p4_upg],
            turns_since_p0=3,
        )
    )
    assert dec_p0_b.selected_diagnostic_id == "dg_11111111"
    assert dec_p0_b.decisions[0].delivery == Delivery.IMMEDIATE_FOLLOWUP
    assert dec_p0_b.decisions[1].delivery == Delivery.DEFERRED

    # 3. P0 cooldown (turns_since_p0 < 3) -> deferred with 'p0_cooldown'
    dec_p0_cool = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_3",
            comprehension_status=ComprehensionStatus.OK,
            p0=POSignal(candidate=True, line="Wait—do you want him to join or not?"),
            diagnostics=[p1_err],
            turns_since_p0=1,
        )
    )
    assert dec_p0_cool.selected_diagnostic_id is None
    assert "p0_cooldown" in dec_p0_cool.decisions[0].reasons

    # 4. Warm-up blocks normal P1 in turns 1-2, but high-severity pragmatic P1 bypasses warm-up with gap 2
    dec_warm_blocked = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_4",
            diagnostics=[p1_err],
            learner_turn_index=2,
            turns_since_visible_card=10,
        )
    )
    assert dec_warm_blocked.selected_diagnostic_id is None

    prag_p1 = p1_err.model_copy(update={"domain": Domain.PRAGMATIC, "diagnostic_id": "dg_33333333"})
    dec_warm_exempt = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_5",
            diagnostics=[prag_p1],
            learner_turn_index=1,
            turns_since_visible_card=2,
        )
    )
    assert dec_warm_exempt.selected_diagnostic_id == "dg_33333333"
    assert dec_warm_exempt.decisions[0].delivery == Delivery.END_OF_TURN

    # 5. Light preference blocks P4 upgrades and enforces gap of 6 on P1 (+2 when strain >= 0.6)
    dec_light_p4 = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_6",
            diagnostics=[p4_upg],
            feedback_preference="light",
            learner_turn_index=5,
            turns_since_visible_card=10,
        )
    )
    assert dec_light_p4.selected_diagnostic_id is None

    dec_strain_gap = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_7",
            diagnostics=[p1_err],
            feedback_preference="light",
            learner_turn_index=5,
            turns_since_visible_card=7,  # light base 6 + strain bonus 2 = 8 required
            strain=0.65,
        )
    )
    assert dec_strain_gap.selected_diagnostic_id is None

    # 6. Emotional/story flow defers everything except P0
    dec_flow = resolve_interaction_policy(
        PolicyInputs(
            turn_event_id="ev_8",
            diagnostics=[p1_err],
            in_emotional_or_story_flow=True,
            learner_turn_index=5,
            turns_since_visible_card=10,
        )
    )
    assert dec_flow.selected_diagnostic_id is None
    assert dec_flow.decisions[0].delivery == Delivery.DEFERRED


def test_s2_orchestrator_cas_and_late_analysis_deferral(tmp_path: Path) -> None:
    db_path = tmp_path / "orch_s2.db"
    db = EventSourcedDatabase(db_path)
    db.migrate()
    session = db.get_or_create_active_session(501)
    session_id = session["session_id"]

    # Accept turn 1 (revision -> 1)
    res1, rev1 = db.accept_scene_turn(
        learner_id=501,
        session_id=session_id,
        text="I discussed about the contract yesterday.",
        idempotency_key="tg_501_1:learner",
        origin="scene",
        correlation_id="corr_1",
    )
    assert rev1 == 1

    # Advance session_state_projection past warm-up so a card is eligible
    with db._connect() as conn:
        cur = conn.execute(
            "UPDATE session_state_projection SET "
            "learner_turn_count = 4, turns_since_visible_card = 10, "
            "cards_in_scene = 0, turns_since_p0 = 99 WHERE session_id = ?",
            (session_id,),
        )
        if cur.rowcount == 0:
            info = conn.execute("PRAGMA table_info(session_state_projection)").fetchall()
            row_data = {
                "session_id": session_id,
                "learner_id": 501,
                "learner_turn_count": 4,
                "turns_since_visible_card": 10,
                "cards_in_scene": 0,
                "turns_since_p0": 99,
            }
            for col in info:
                cname, ctype, notnull, dflt = str(col[1]), str(col[2]).upper(), int(col[3]), col[4]
                if cname not in row_data and notnull and dflt is None:
                    row_data[cname] = "2026-10-31T18:00:00Z" if "UTC" in cname.upper() else (0 if "INT" in ctype else "")
            cols_sql = ", ".join(row_data.keys())
            placeholders = ", ".join("?" for _ in row_data)
            conn.execute(
                f"INSERT INTO session_state_projection ({cols_sql}) VALUES ({placeholders})",
                tuple(row_data.values()),
            )
        conn.commit()

    val = validate_analysis(
        {"diagnostics": [_raw_diag(severity="high")]},
        learner_text="I discussed about the contract yesterday.",
    )
    try:
        orch = TurnOrchestrator(db)
    except TypeError:
        orch = object.__new__(TurnOrchestrator)
    orch.db = db

    # Case 1: Revision matches (1 == 1) -> attaches upgrade button
    handle1 = TurnHandle(
        turn_event_id=res1.event_id,
        reply_event_seq=20,
        turn_session_revision=1,
        correlation_id="corr_1",
        session_id=session_id,
        idempotency_base="tg_501_1",
    )
    actions1 = orch.apply_analysis_outcome(
        learner_id=501,
        handle=handle1,
        outcome=AnalysisOutcome(status="ok", validated=val),
        now=FakeClock().now(),
    )
    assert actions1.attach_upgrade_button is True
    assert actions1.upgrade_callback == "ug:20:1"

    # Case 2: Learner sends a new turn (revision -> 2), then a late P0 analysis for turn 1 arrives
    _res2, rev2 = db.accept_scene_turn(
        learner_id=501,
        session_id=session_id,
        text="And another thing...",
        idempotency_key="tg_501_2:learner",
        origin="scene",
        correlation_id="corr_2",
    )
    assert rev2 == 2

    val_late_p0 = validate_analysis(
        {
            "diagnostics": [_raw_diag(severity="high")],
            "p0": {"candidate": True, "line": "Did you sign the contract or reject it?"},
        },
        learner_text="I discussed about the contract yesterday.",
    )
    stale_handle = TurnHandle(
        turn_event_id=res1.event_id,
        reply_event_seq=20,
        turn_session_revision=1,  # Stale revision! Current is 2
        correlation_id="corr_late",
        session_id=session_id,
        idempotency_base="tg_501_late",
    )
    actions_late = orch.apply_analysis_outcome(
        learner_id=501,
        handle=stale_handle,
        outcome=AnalysisOutcome(status="ok", validated=val_late_p0),
        now=FakeClock().now(),
    )
    assert actions_late.attach_upgrade_button is False
    assert actions_late.followup_text is None
    assert actions_late.deferred_count == 1
    # Pending clarification was saved for the next Responder brief (Amendment A14)
    assert db.take_pending_clarification(session_id) == "Did you sign the contract or reject it?"


def test_s2_callback_length_at_maximum_ints() -> None:
    cb = build_callback("rvn", 999_999_999, 999_999)
    assert len(cb.encode("ascii")) <= CALLBACK_MAX_BYTES
    parsed = parse_callback(cb)
    assert parsed is not None and parsed.op == "rvn" and parsed.obj_id == 999_999_999
