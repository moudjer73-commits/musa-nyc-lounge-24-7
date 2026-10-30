from __future__ import annotations

import asyncio

import pytest

from coach.db import Database, MAX_ACTIVE_TARGETS, TargetCapError
from coach.llm import ScriptedLLM
from coach.mastery import DAY, Stage
from coach.models import Expression, WorkoutEval
from coach.modes import ModeRunner, record_mode_used_targets, record_workout_result
from coach.models import UsedTarget

from conftest import mk_finding

NOW = 1_800_000_000.0


def seed(db: Database, user_id: int = 1) -> int:
    db.get_or_create_user(user_id, NOW)
    return user_id


def expr(i: int = 1, form: str = "I'm swamped") -> Expression:
    return Expression(id=f"x{i}", form=form, meaning="too busy", acceptable_forms=[form], opportunity_hint="a request")


def test_schema_and_user_defaults(db):
    u = db.get_or_create_user(7, NOW)
    assert u["cefr"] == "B2" and u["strictness"] == "normal" and u["include_arabic"] == 1
    db.update_user(7, cefr="C1", strictness="coach", dialect="Maghrebi")
    assert db.get_user(7)["cefr"] == "C1"
    with pytest.raises(ValueError):
        db.update_user(7, nonsense=1)
    with pytest.raises(KeyError):
        db.get_user(999)


def test_baseline_is_an_ema_that_ignores_one_word_replies(db):
    uid = seed(db)
    before = db.get_user(uid)["baseline_words"]
    db.update_baseline_words(uid, 1)
    assert db.get_user(uid)["baseline_words"] == before
    db.update_baseline_words(uid, 32)
    assert db.get_user(uid)["baseline_words"] == pytest.approx(before * 0.9 + 3.2)


def test_active_targets_are_capped_at_three_and_stable_ones_free_a_slot(db):
    uid = seed(db)
    for i in range(1, MAX_ACTIVE_TARGETS + 2):
        db.upsert_expression(uid, expr(i, f"phrase {i}"), NOW)
    for i in range(1, MAX_ACTIVE_TARGETS + 1):
        db.activate_target(uid, f"x{i}", NOW)
    db.activate_target(uid, "x1", NOW)                       # idempotent
    assert len(db.active_targets(uid)) == MAX_ACTIVE_TARGETS
    with pytest.raises(TargetCapError):
        db.activate_target(uid, f"x{MAX_ACTIVE_TARGETS + 1}", NOW)

    # make x1 STABLE and retire it
    for k, (sess, reg) in enumerate([(1, "casual"), (2, "diplomatic"), (3, "casual")]):
        db.record_event(uid, "x1", "spontaneous", NOW + k * 4 * DAY, correct=True, naturalness=3, register=reg, session_id=sess)
    later = NOW + 12 * DAY
    assert db.stage_of(uid, "x1", later) == Stage.STABLE
    assert db.retire_stable_targets(uid, later) == ["x1"]
    db.activate_target(uid, f"x{MAX_ACTIVE_TARGETS + 1}", later)
    assert {t.id for t in db.active_targets(uid)} == {"x2", "x3", f"x{MAX_ACTIVE_TARGETS + 1}"}


def test_error_patterns_view_counts_events_not_a_counter(db):
    uid = seed(db)
    sid = db.start_session(uid, "open_scene", NOW)
    f = mk_finding()
    for i in range(3):
        db.log_finding(uid, f, "silent", NOW + i, session_id=sid)
    db.log_finding(uid, mk_finding(pattern_key="pragmatic.DIRECTNESS.x", category="pragmatic"), "recast", NOW + 5, session_id=sid)
    assert db.pattern_counts(uid)[f.pattern_key] == 3
    top = db.known_patterns(uid)
    assert top[0] == {"key": f.pattern_key, "count": 3}
    row = db.conn.execute("SELECT * FROM error_patterns WHERE pattern_key = ?", (f.pattern_key,)).fetchone()
    assert row["l1_pattern"] == "COLLOC_LIGHT_VERB" and row["first_seen"] == NOW and row["last_seen"] == NOW + 2


def test_learner_state_is_a_snapshot_before_this_turns_findings(db):
    uid = seed(db)
    sid = db.start_session(uid, "open_scene", NOW)
    f = mk_finding()
    db.log_finding(uid, f, "silent", NOW, session_id=sid)                       # an earlier turn
    t1 = db.log_turn(sid, uid, "learner", "I took a decision", NOW + 1, words=4)
    state = db.build_learner_state(uid, sid, NOW + 2)
    assert state.turn_index == 1 and state.pattern_counts[f.pattern_key] == 1 and state.turns_since_card == 99
    assert state.recent_words == [4]

    # a visible card, then three more learner turns
    fid = db.log_finding(uid, f, "offer_card", NOW + 3, session_id=sid, turn_id=t1)
    for i in range(3):
        db.log_turn(sid, uid, "learner", "words words words", NOW + 4 + i, words=3)
    state = db.build_learner_state(uid, sid, NOW + 10)
    assert state.cards_this_session == 1 and state.turns_since_card == 3 and state.turn_index == 4
    assert fid


def test_recent_card_text_only_counts_cards_the_learner_just_read(db):
    uid = seed(db)
    sid = db.start_session(uid, "open_scene", NOW)
    t = db.log_turn(sid, uid, "learner", "x", NOW, words=1)
    fid = db.log_finding(uid, mk_finding(), "offer_card", NOW, session_id=sid, turn_id=t)
    db.save_card(fid, "💡 <i>take a decision</i> → <b>make the call</b>", NOW + 1)
    db.log_turn(sid, uid, "learner", "y", NOW + 2, words=1)
    assert db.recent_card_text(sid) == ["💡 take a decision → make the call"]
    for i in range(4):
        db.log_turn(sid, uid, "learner", "z", NOW + 10 + i, words=1)
    assert db.recent_card_text(sid) == []


def test_recap_is_deduplicated_ranked_and_capped(db):
    uid = seed(db)
    sid = db.start_session(uid, "open_scene", NOW)
    for i, (key, sev, action) in enumerate(
        [("a", 2, "defer"), ("a", 2, "recast"), ("b", 3, "defer"), ("c", 2, "offer_card"), ("d", 2, "defer"), ("e", 1, "defer"), ("f", 2, "silent")]
    ):
        db.log_finding(uid, mk_finding(pattern_key=key, severity=sev), action, NOW + i, session_id=sid)
    items = db.recap_items(sid, 3)
    keys = [f.pattern_key for _, f in items]
    assert keys[0] == "b" and len(keys) == 3 and len(set(keys)) == 3
    assert "e" not in keys and "f" not in keys           # severity 1 and silent never reach the recap
    db.mark_recapped([i for i, _ in items], NOW + 100)
    assert all(f.pattern_key not in keys for _, f in db.recap_items(sid, 10))
    # a pattern seen several times in the scene is retired as a whole, not row by row
    rest = db.recap_items(sid, 10)
    assert [f.pattern_key for _, f in rest] == ["a"]
    db.mark_recapped([i for i, _ in rest], NOW + 101)
    assert db.recap_items(sid, 10) == []
    assert db.conn.execute(
        "SELECT COUNT(*) FROM finding_events WHERE pattern_key = 'a' AND recapped_at IS NULL"
    ).fetchone()[0] == 0


def test_due_expressions_follow_the_schedule(db):
    uid = seed(db)
    db.upsert_expression(uid, expr(1), NOW)
    db.upsert_expression(uid, expr(2, "make the call"), NOW)
    db.record_event(uid, "x1", "exposure", NOW)
    db.record_event(uid, "x2", "exposure", NOW + 12 * 3600)
    assert db.due_expressions(uid, NOW + 0.5 * DAY) == []
    assert [e.id for e in db.due_expressions(uid, NOW + 1.2 * DAY)] == ["x1"]
    assert [e.id for e in db.due_expressions(uid, NOW + 2 * DAY)] == ["x1", "x2"]


def test_metrics_are_derived_from_events(db):
    uid = seed(db)
    db.upsert_expression(uid, expr(1), NOW)
    for i in range(4):
        db.record_event(uid, "x1", "opportunity", NOW + i)
    db.record_event(uid, "x1", "spontaneous", NOW + 10, correct=True, naturalness=3, register="casual", session_id=1)
    db.record_event(uid, "x1", "spontaneous", NOW + 11, correct=True, naturalness=3, register="diplomatic", session_id=2)
    m = db.metrics(uid, NOW + DAY)
    assert m["spontaneous_uses"] == 2 and m["opportunities"] == 4
    assert m["spontaneous_rate"] == 0.5
    assert m["formality_diversity"] == pytest.approx(2 / 3)       # informal + formal
    db.record_event(uid, "x1", "spontaneous", NOW + 12, correct=True, naturalness=3, register="warm", session_id=3)
    assert db.metrics(uid, NOW + DAY)["formality_diversity"] == pytest.approx(2 / 3)   # warm is still informal
    assert m["stage_counts"] == {"PRODUCED_SPONTANEOUS": 1}


def test_repeat_after_drill_metric(db):
    uid = seed(db)
    sid = db.start_session(uid, "open_scene", NOW)
    f = mk_finding()
    for d in (-12, -8, -3):
        db.log_finding(uid, f, "silent", NOW + d * DAY, session_id=sid)
    did = db.queue_drill(uid, f.pattern_key, "phrase_workout", NOW - 2 * DAY)
    db.finish_drill(did, NOW, "done")
    db.log_finding(uid, f, "silent", NOW + 5 * DAY, session_id=sid)
    m = db.metrics(uid, NOW + 20 * DAY)
    assert m["repeat_after_drill"] == pytest.approx(1 / 3)
    assert db.drilled_recently(uid, NOW + 3 * DAY) == [f.pattern_key]
    assert db.drilled_recently(uid, NOW + 30 * DAY) == []


def test_foreign_keys_protect_the_event_log(db):
    seed(db)
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.record_event(1, "nope", "exposure", NOW)


def test_drills_and_modes_can_never_produce_spontaneous_events(db):
    uid = seed(db)
    db.upsert_expression(uid, expr(1), NOW)
    ok = WorkoutEval(outcome="target_correct", naturalness=3, register_fit=True, feedback_text="x", schedule_hint="advance")
    miss = WorkoutEval(outcome="miss", schedule_hint="reset")
    record_workout_result(db, uid, "x1", ok, NOW, hint_level=0, register="casual")
    assert db.stage_of(uid, "x1", NOW + 1) == Stage.PRODUCED_PROMPTED
    record_workout_result(db, uid, "x1", miss, NOW + 10, hint_level=0)
    assert db.stage_of(uid, "x1", NOW + 11) == Stage.RECOGNIZED          # one rung down after a miss
    record_workout_result(db, uid, "x1", ok, NOW + 20, hint_level=3)       # answer was revealed: exposure only
    types = [e.type for e in db.events_for(uid, "x1")]
    assert types == ["prompted", "prompted", "exposure"]

    ids = record_mode_used_targets(
        db, uid, [UsedTarget(target_id="x1", form_used="I'm swamped", correct=True, naturalness=3, echo=False),
                  UsedTarget(target_id="x1", form_used="I'm swamped", correct=True, echo=True),
                  UsedTarget(target_id="ghost", form_used="x", correct=True)],
        NOW + 30, mode="pragmatic_dilemma", known_ids={"x1"},
    )
    assert len(ids) == 1
    assert "spontaneous" not in {e.type for e in db.events_for(uid, "x1")}


def test_mode_runner_validates_and_routes_by_role(db, example_scene):
    llm = ScriptedLLM()
    llm.push(
        "phrase_workout.evaluate",
        {"outcome": "equivalent", "naturalness": 2, "register_fit": True, "feedback_text": "fine", "schedule_hint": "repeat_soon"},
    )
    out = asyncio.run(
        ModeRunner(llm).run("phrase_workout", "EVALUATE", {"expression": {"id": "x1"}, "learner_answer": "I'm so busy"})
    )
    assert out.outcome == "equivalent"
    call = llm.calls[0]
    assert call.role == "phrase_workout.evaluate" and "learner_answer" in call.user and "{{" not in call.system

    llm.push("think_aloud.interlocutor", {"bubbles": ["Say more about that?", "second"]})
    reply = asyncio.run(ModeRunner(llm).run("think_aloud", "INTERLOCUTOR", {"think_move": "probe"}, persona=example_scene.persona))
    assert reply.bubbles == ["Say more about that?"]            # interlocutor is one bubble
    assert "Priya" in llm.calls[1].system
    with pytest.raises(ValueError):
        asyncio.run(ModeRunner(llm).run("think_aloud", "INTERLOCUTOR", {}))
