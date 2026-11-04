"""S5: the V3 turn protocol end to end with a scripted Responder and Analyst (S0 6, A13, A14)."""

from __future__ import annotations

import json

import pytest

import interaction
from helpers_s5 import GREETING_1, GREETING_2, TEXT, DeferredExecutor, Harness, raw_diag
from schemas import ComprehensionStatus, InteractionState, SkipReason

P0_LINE = "Did you mean the Monday meeting?"
P0_ANALYSIS = {"p0": {"candidate": True, "line": P0_LINE}}
# schemas.TargetUseAssessment is fail-closed: credit needs all three fit checks and a medium/high band
CREDITED = dict(fits_communicative_context=True, fits_register=True, phrase_specific_rubric_passed=True, confidence_band="high")


def policy_for(h: Harness, turn_event_id: str) -> dict:
    return next(e["payload"] for e in h.events("policy_decision_recorded") if e["payload"]["turn_event_id"] == turn_event_id)


# ------------------------------------------------------------------------------ the happy path


def test_scene_turn_card_tap_reconstruction_and_send_to_alex(db):
    h = Harness(db)
    res, actions = h.reach_card()

    # 1. the reply was stored before the analysis, and the handle addresses it
    assert res.responder_output.reply_text.startswith("Ah, so")
    assert res.handle.reply_event_seq > 0
    assert actions.attach_upgrade_button and actions.card_id is not None
    assert actions.upgrade_callback == interaction.build_callback("ug", res.handle.reply_event_seq, res.handle.turn_session_revision)
    assert len(actions.upgrade_callback.encode()) <= interaction.MAX_CALLBACK_BYTES

    # 2. the policy decision names the finding, and the analysis is in the log
    pol = policy_for(h, res.handle.turn_event_id)
    assert pol["selected_diagnostic_id"] == actions.card_id
    assert h.events("analyst_diagnosis_recorded")[-1]["payload"]["diagnostics"][0]["diagnostic_id"] == actions.card_id

    # 3. the 💡 offer is the visible card for pacing; the tap is the same card and is not counted twice
    h.flows.mark_card_offered(h.learner, res.session_id, actions.card_id, res.handle.turn_event_id, 77)
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert tapped.toast == "Upgrade opened"
    assert "Quick Upgrade" in tapped.edit.text and "discussed" in tapped.edit.text
    with db.read() as conn:
        assert conn.execute("SELECT cards_in_scene FROM session_state_projection WHERE session_id = ?", (res.session_id,)).fetchone()[0] == 1
    assert [e["payload"]["trigger"] for e in h.events("feedback_card_delivered")] == ["end_of_turn", "tap"]
    row = interaction.get_active_interaction(db, h.learner, db.clock.now())
    assert row.state == InteractionState.UPGRADE_VIEW

    # 4. rc opens a NEW row; the learner's next text is graded, not sent to Alex
    rc = next(b.data for r in tapped.edit.buttons for b in r if b.data.startswith("rc:"))
    started = h.flows.handle_callback(h.learner, rc)
    assert "Say it again" in started.edit.text
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()).state == InteractionState.RECONSTRUCTION_ACTIVE
    routed = h.flows.handle_text(h.learner, "We discussed the plan yesterday.")
    assert routed.scene_turn is False and "That works" in routed.views[0].text
    attempted = h.events("reconstruction_attempted")[-1]["payload"]
    assert attempted["outcome"] == "success" and attempted["attempt_text"] == "We discussed the plan yesterday."
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()).state == InteractionState.RECONSTRUCTION_FEEDBACK

    # 5. rs closes the row and hands the attempt back as a scene turn with origin='reconstruction'
    rs = next(b.data for r in routed.views[0].buttons for b in r if b.data.startswith("rs:"))
    sent = h.flows.handle_callback(h.learner, rs)
    assert sent.submit_turn.origin == "reconstruction" and sent.submit_turn.text == "We discussed the plan yesterday."
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None
    before = db.get_session_revision(res.session_id)
    h.say(sent.submit_turn.text, origin=sent.submit_turn.origin, interaction_id=sent.submit_turn.interaction_id, analysis={"diagnostics": []})
    assert db.get_session_revision(res.session_id) == before + 1
    last = h.events("learner_message_received")[-1]["payload"]
    assert last["origin"] == "reconstruction" and last["interaction_id"] == sent.submit_turn.interaction_id


def test_nothing_is_shown_during_the_two_turn_warm_up(db):
    h = Harness(db)
    db.update_learner_preference(h.learner, feedback_preference="active")
    _, actions = h.say(TEXT, analysis={"diagnostics": [raw_diag()]})
    assert actions.attach_upgrade_button is False and actions.upgrade_callback is None
    assert actions.deferred_count == 1  # it goes to the Review queue instead


def test_a_second_card_is_held_back_by_the_gap(db):
    h = Harness(db)
    res, first = h.reach_card()
    h.flows.mark_card_offered(h.learner, res.session_id, first.card_id, res.handle.turn_event_id, 77)  # what app.py does
    _, again = h.say("I did an effort again at work today.", analysis={"diagnostics": [raw_diag(original_span="did an effort", normalized_pattern_id="COLLOC_LIGHT_VERB:make_effort", alternative="made an effort")]})
    assert again.attach_upgrade_button is False


def test_a_duplicate_telegram_message_is_ignored(db):
    h = Harness(db)
    first, _ = h.say(GREETING_1, key="same")
    again, actions = h.say(GREETING_1, key="same")
    assert first.duplicate is False and again.duplicate is True and again.handle is None and actions is None
    assert len(h.events("learner_message_received")) == 1
    assert len(h.ai.briefs) == 1


def test_a_greeting_skips_the_analyst(db):
    h = Harness(db)
    res, actions = h.say("hey Alex")
    assert h.ai.analyst_calls == 0
    assert h.events("analysis_skipped")[-1]["payload"]["reason"] == SkipReason.TRIVIAL.value
    assert actions.attach_upgrade_button is False


@pytest.mark.parametrize("err,reason", [(TimeoutError("late"), "deadline"), (RuntimeError("boom"), "error")])
def test_an_analyst_failure_is_recorded_and_the_reply_still_went_out(db, err, reason):
    h = Harness(db)
    res, actions = h.say(TEXT, analysis=err)
    assert res.responder_output is not None
    assert h.events("analysis_skipped")[-1]["payload"]["reason"] == reason
    assert actions.attach_upgrade_button is False
    assert h.events("analyst_diagnosis_recorded") == []


def test_a_quota_shed_is_recorded_as_shed(db, monkeypatch):
    import retrieval

    class QuotaShed(Exception):
        """Stands in for llm_transport.QuotaShed."""

    monkeypatch.setattr(retrieval, "default_shed_exceptions", lambda: (retrieval.JudgeShed, TimeoutError, QuotaShed))
    h = Harness(db)
    h.say(TEXT, analysis=QuotaShed("quota"))
    assert h.events("analysis_skipped")[-1]["payload"]["reason"] == "shed"


def test_a_responder_failure_keeps_the_learner_message_and_raises(db):
    h = Harness(db)

    def boom(**kw):
        raise RuntimeError("no reply")

    h.ai.run_responder_v3 = boom
    with pytest.raises(RuntimeError):
        h.say(TEXT, analysis={"diagnostics": []}, apply=False)
    assert len(h.events("learner_message_received")) == 1
    assert h.events("responder_reply_delivered") == []


def test_the_analyst_gets_the_taken_ids_and_the_validator_drops_a_hallucinated_span(db):
    h = Harness(db)
    res, actions = h.say(TEXT, analysis={"diagnostics": [raw_diag(original_span="text that was never said")]})
    assert actions.card_id is None
    assert h.events("analyst_diagnosis_recorded")[-1]["payload"]["diagnostics"] == []


# ------------------------------------------------------------------------------ late analysis (A13, A14)


def test_a_late_analysis_is_deferred_and_never_shown(db):
    ex = DeferredExecutor()
    h = Harness(db, executor=ex)
    db.update_learner_preference(h.learner, feedback_preference="active")
    for i, text in enumerate((GREETING_1, GREETING_2)):
        h.ai.analyses.append({"diagnostics": []})
        res, _ = h.say(text, apply=False)
        ex.run(i)
        h.orch.apply_analysis_outcome(learner_id=h.learner, handle=res.handle, outcome=res.analysis_future.result())
    h.ai.analyses.append({"diagnostics": [raw_diag()]})
    slow, _ = h.say(TEXT, apply=False)  # its analysis has not finished
    h.ai.analyses.append({"diagnostics": []})
    h.say("Anyway, what about you?", apply=False)  # the learner moved on: session_revision changed
    ex.run(2)
    actions = h.orch.apply_analysis_outcome(learner_id=h.learner, handle=slow.handle, outcome=slow.analysis_future.result())
    assert actions.attach_upgrade_button is False and actions.followup_text is None
    pol = policy_for(h, slow.handle.turn_event_id)
    assert pol["selected_diagnostic_id"] is None
    assert {d["delivery"] for d in pol["decisions"]} == {"deferred"}
    queue = db.get_review_queue(h.learner, db.clock.now())
    assert [e.representative.original_span for e in queue] == ["discussed about"]


def test_a_late_analysis_still_counts_its_evidence_in_the_projection(db):
    ex = DeferredExecutor()
    h = Harness(db, executor=ex)
    h.ai.analyses.append({"diagnostics": [raw_diag()]})
    slow, _ = h.say(TEXT, apply=False)
    h.say("Anyway, what about you?", apply=False)
    ex.run(0)
    h.orch.apply_analysis_outcome(learner_id=h.learner, handle=slow.handle, outcome=slow.analysis_future.result())
    with db.read() as conn:
        row = conn.execute("SELECT distinct_eligible_occurrences FROM error_pattern_projection WHERE learner_id = ?", (h.learner,)).fetchone()
    assert row is not None and row[0] == 1


# ------------------------------------------------------------------------------ P0 (S0 5.3)


def test_p0_path_b_sends_one_short_follow_up_and_suppresses_other_cards(db):
    h = Harness(db)
    h.say(GREETING_1, analysis={"diagnostics": []})
    res, actions = h.say(
        TEXT,
        analysis={**P0_ANALYSIS, "diagnostics": [raw_diag(severity="high"), raw_diag(original_span="did an effort", normalized_pattern_id="COLLOC_LIGHT_VERB:make_effort", alternative="made an effort")]},
    )
    assert actions.followup_text == P0_LINE and len(P0_LINE.split()) < 20
    assert actions.attach_upgrade_button is False
    followup = [e for e in h.events("responder_reply_delivered") if e["payload"]["kind"] == "p0_followup"]
    assert len(followup) == 1 and followup[0]["payload"]["reply_text"] == P0_LINE
    pol = policy_for(h, res.handle.turn_event_id)
    assert sorted(d["delivery"] for d in pol["decisions"]) == ["deferred", "immediate_followup"]


def test_p0_path_a_the_reply_already_is_the_clarification(db):
    h = Harness(db)
    h.say(GREETING_1, analysis={"diagnostics": []})
    res, actions = h.say(
        TEXT,
        reply=("Sorry, who do you mean?", ComprehensionStatus.AMBIGUOUS),
        analysis={"diagnostics": [raw_diag(severity="high")]},
    )
    assert actions.followup_text is None and actions.attach_upgrade_button is False
    pol = policy_for(h, res.handle.turn_event_id)
    assert pol["decisions"][0]["reasons"] == ["handled_in_reply"]
    assert [e for e in h.events("responder_reply_delivered") if e["payload"]["kind"] == "p0_followup"] == []


def test_p0_cooldown_keeps_the_line_for_the_next_brief(db):
    h = Harness(db)
    h.say(GREETING_1, analysis={"diagnostics": []})
    h.say(TEXT, analysis={**P0_ANALYSIS, "diagnostics": [raw_diag(severity="high")]})  # path B resets the counter
    _, second = h.say(
        "We discussed about it again with the team.",
        analysis={"p0": {"candidate": True, "line": "Which team do you mean?"}, "diagnostics": [raw_diag(severity="high")]},
    )
    assert second.followup_text is None  # inside the cooldown: no second interruption
    h.say("And then we moved on, fine.", analysis={"diagnostics": []})
    assert h.ai.briefs[-1]["pending_clarification"] == "Which team do you mean?"
    h.say("Right, that settles it.", analysis={"diagnostics": []})
    assert h.ai.briefs[-1]["pending_clarification"] is None  # said once, then gone


def test_a_p0_line_whose_revision_moved_is_injected_into_the_next_brief(db):
    ex = DeferredExecutor()
    h = Harness(db, executor=ex)
    h.ai.analyses.append({**P0_ANALYSIS, "diagnostics": [raw_diag(severity="high")]})
    slow, _ = h.say(TEXT, apply=False)
    h.ai.analyses.append({"diagnostics": []})
    h.say("Anyway, what about you?", apply=False)
    ex.run(0)
    actions = h.orch.apply_analysis_outcome(learner_id=h.learner, handle=slow.handle, outcome=slow.analysis_future.result())
    assert actions.followup_text is None  # too late to interrupt
    assert [e for e in h.events("responder_reply_delivered") if e["payload"]["kind"] == "p0_followup"] == []
    h.ai.analyses.append({"diagnostics": []})
    h.say("So, where was I?", apply=False)
    assert h.ai.briefs[-1]["pending_clarification"] == P0_LINE
    h.ai.analyses.append({"diagnostics": []})
    h.say("Right, that settles it.", apply=False)
    assert h.ai.briefs[-1]["pending_clarification"] is None


def test_a_failed_responder_call_does_not_lose_a_waiting_p0_line(db):
    h = Harness(db)
    sid = str(db.get_or_create_active_session(h.learner)["session_id"])
    db.set_pending_clarification(sid, "Which one?")
    real = h.ai.run_responder_v3

    def boom(**kw):
        raise RuntimeError("down")

    h.ai.run_responder_v3 = boom
    with pytest.raises(RuntimeError):
        h.say(TEXT, analysis={"diagnostics": []}, apply=False)
    h.ai.run_responder_v3 = real
    h.say("Sorry about that, I said we talked.", analysis={"diagnostics": []}, apply=False)
    assert h.ai.briefs[-1]["pending_clarification"] == "Which one?"


# ------------------------------------------------------------------------------ spontaneous detection


def test_spontaneous_use_of_a_scheduled_phrase_is_credited_by_python(db):
    from helpers_s3 import exposure, attempt, tiny_seed, expression

    db.seed_catalog(tiny_seed(expression("come_to_think_of_it")))
    exposure(db, "come_to_think_of_it", 0, "exp1")
    attempt(db, "come_to_think_of_it", 0, "att1", outcome="success", due=False)
    db.clock.travel(days=3)
    h = Harness(db)
    h.say(GREETING_1, analysis={"diagnostics": []})
    h.say(GREETING_2, analysis={"diagnostics": []})
    text = "Come to think of it, we never booked the room."
    h.say(
        text,
        analysis={"diagnostics": [], "target_assessments": [{"target_id": "come_to_think_of_it", "matched_span": "Come to think of it", "evidence": "spontaneous_production", **CREDITED}]},
    )
    assert h.ai.briefs[-1]["stealth_target_info"] is not None  # the brief carried the target, the learner never saw it
    assessed = h.events("phrase_production_assessed")
    assert len(assessed) == 1 and assessed[0]["payload"]["target_id"] == "come_to_think_of_it"


def test_credit_is_refused_when_the_catalog_form_is_not_in_the_learners_text(db):
    from helpers_s3 import exposure, attempt, tiny_seed, expression

    db.seed_catalog(tiny_seed(expression("come_to_think_of_it")))
    exposure(db, "come_to_think_of_it", 0, "exp1")
    attempt(db, "come_to_think_of_it", 0, "att1", outcome="success", due=False)
    db.clock.travel(days=3)
    h = Harness(db)
    h.say(
        "We never booked the room, so think of it as done.",
        analysis={"diagnostics": [], "target_assessments": [{"target_id": "come_to_think_of_it", "matched_span": "think of it", "evidence": "spontaneous_production", **CREDITED}]},
    )
    assert h.events("analyst_diagnosis_recorded")[-1]["payload"]["target_assessments"][0]["evidence"] == "spontaneous_production"  # the Analyst claimed it
    assert h.events("phrase_production_assessed") == []  # Python refused: the catalog form is not there


def test_a_target_that_only_repeats_alexs_words_gets_no_spontaneous_credit(db):
    from helpers_s3 import exposure, attempt, tiny_seed, expression

    db.seed_catalog(tiny_seed(expression("come_to_think_of_it")))
    exposure(db, "come_to_think_of_it", 0, "exp1")
    attempt(db, "come_to_think_of_it", 0, "att1", outcome="success", due=False)
    db.clock.travel(days=3)
    h = Harness(db)
    h.say(GREETING_1, analysis={"diagnostics": []}, reply="Come to think of it, we never booked the room.")
    h.say(
        "Yes, come to think of it, we never booked the room.",
        analysis={"diagnostics": [], "target_assessments": [{"target_id": "come_to_think_of_it", "matched_span": "come to think of it", "evidence": "spontaneous_production", **CREDITED}]},
    )
    kept = h.events("analyst_diagnosis_recorded")[-1]["payload"]
    assert [d["reason"] for d in kept["dropped"]] == ["echo"]
    assert kept["target_assessments"][0]["evidence"] == "cued_production"
