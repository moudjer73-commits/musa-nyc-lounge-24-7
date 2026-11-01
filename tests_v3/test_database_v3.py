"""S1 database behaviour: V2 writers, revisions and CAS (A13/A14), operational state, projections."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing

import pytest
from pydantic import ValidationError

import database
from clock import FakeClock
from database import EventSourcedDatabase, EventSpec
from helpers import decision_dict, v1_analysis_payload, v1_diagnostic, v2_analysis_dict, v2_diagnostic_dict, v2_policy_dict
from schemas import TurnOrigin

PROJECTIONS = (
    "session_state_projection",
    "phrase_state_projection",
    "error_pattern_projection",
    "feedback_item_projection",
    "phrase_evidence",
)
LEARNER = 1001


def t(minute: int, second: int = 0) -> str:
    return f"2026-10-01T09:{minute:02d}:{second:02d}.000000Z"


def rows(db: EventSourcedDatabase, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with db.read() as conn:
        return conn.execute(sql, params).fetchall()


def log_rows(db: EventSourcedDatabase) -> list[sqlite3.Row]:
    return rows(db, "SELECT * FROM event_log ORDER BY event_seq")


def projection_rows(db: EventSourcedDatabase) -> dict[str, list[tuple]]:
    return {t_: sorted(tuple(r) for r in rows(db, f"SELECT * FROM {t_}")) for t_ in PROJECTIONS}


def put(db, event_type, payload, key, *, at, corr="turn_1", causation=None, event_id=None, session="s1", learner=LEARNER, **kw):
    return db.append_event_ex(
        learner_id=learner, session_id=session, event_type=event_type, payload=payload,
        correlation_id=corr, idempotency_key=key, causation_id=causation, occurred_at_utc=at, event_id=event_id, **kw
    )


def reply_payload(turn_event_id, text="Go on."):
    return {"kind": "reply", "turn_event_id": turn_event_id, "reply_text": text, "second_bubble_text": None,
            "comprehension_status": "ok", "recast_used": False, "elicitation_used": False,
            "telegram_message_ids": [], "call_meta": None}


# ------------------------------------------------------------------ writers


def test_append_event_ex_stamps_v2_and_uses_the_injected_clock(db):
    result = put(db, "analysis_skipped", {"turn_event_id": "ev_t", "reason": "trivial", "detail": None}, "k1", at=None)
    assert result.inserted and result.event_seq == 1 and result.event_id.startswith("ev_")
    row = log_rows(db)[0]
    assert (row["schema_version"], row["rubric_version"]) == ("2.0", "v2.0")
    assert row["occurred_at_utc"] == row["recorded_at_utc"] == "2026-10-01T09:00:00.000000Z"
    assert json.loads(row["payload_json"]) == {"turn_event_id": "ev_t", "reason": "trivial", "detail": None}


def test_append_event_ex_is_idempotent_on_the_key(db):
    first = put(db, "analysis_skipped", {"turn_event_id": "ev_t", "reason": "trivial"}, "same", at=t(1))
    second = put(db, "analysis_skipped", {"turn_event_id": "ev_other", "reason": "shed"}, "same", at=t(2))
    assert (second.inserted, second.event_id, second.event_seq) == (False, first.event_id, first.event_seq)
    assert len(log_rows(db)) == 1
    assert json.loads(log_rows(db)[0]["payload_json"])["turn_event_id"] == "ev_t"  # the first write stands


def test_v2_payloads_are_validated_and_a_bad_one_writes_nothing(db):
    bad = v2_analysis_dict("ev_t", v2_diagnostic_dict(acoustic_confidence=0.9))
    with pytest.raises(ValidationError):
        put(db, "analyst_diagnosis_recorded", bad, "k_bad", at=t(1))
    assert log_rows(db) == []
    with pytest.raises(ValidationError):
        put(db, "analysis_skipped", {"turn_event_id": "ev_t", "reason": "bored"}, "k_bad2", at=t(1))
    assert log_rows(db) == []


def test_models_are_accepted_and_normalised(db):
    from schemas import AnalysisSkippedV2

    put(db, "analysis_skipped", AnalysisSkippedV2(turn_event_id="ev_t", reason="deadline", detail="35 s"), "k_model", at=t(1))
    assert json.loads(log_rows(db)[0]["payload_json"]) == {"turn_event_id": "ev_t", "reason": "deadline", "detail": "35 s"}


def test_bad_inputs_are_rejected_before_touching_the_log(db):
    with pytest.raises(sqlite3.IntegrityError):  # event_types foreign key
        put(db, "not_registered", {}, "k1", at=t(1))
    with pytest.raises(ValueError):
        put(db, "session_started", {}, "k2", at=t(1), schema_version="3.0")
    with pytest.raises(TypeError):
        put(db, "session_started", ["list"], "k3", at=t(1))
    assert log_rows(db) == []


def test_legacy_append_event_still_returns_a_tuple_and_stamps_v1(db):
    inserted, event_id = db.append_event(
        learner_id=LEARNER, session_id="s1", event_type="learner_message_received",
        payload={"text": "hello", "is_voice": False}, correlation_id="turn_1", idempotency_key="legacy-1",
    )
    assert inserted is True and event_id.startswith("ev_")
    row = log_rows(db)[0]
    assert (row["schema_version"], row["rubric_version"]) == ("1.0", "v1.0")
    assert db.append_event(
        learner_id=LEARNER, session_id="s1", event_type="learner_message_received",
        payload={"text": "hello", "is_voice": False}, correlation_id="turn_1", idempotency_key="legacy-1",
    ) == (False, event_id)


def test_the_log_stays_append_only_through_the_public_connection(db):
    put(db, "analysis_skipped", {"turn_event_id": "ev_t", "reason": "trivial"}, "k1", at=t(1))
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE event_log SET session_id = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM event_log")


def test_rebuild_false_defers_the_projection_until_asked(db):
    put(db, "phrase_exposure_recorded", {"phrase_id": "plate_is_full", "revealed_to_learner": True, "directly_cued": False},
        "k1", at=t(1), rebuild=False)
    assert json.loads(rows(db, "SELECT revealed_targets_json FROM session_state_projection WHERE session_id='s1'")[0][0]) == []
    db.rebuild_all_projections()
    assert json.loads(rows(db, "SELECT revealed_targets_json FROM session_state_projection WHERE session_id='s1'")[0][0]) == ["plate_is_full"]


def test_transaction_rolls_back_on_error_and_read_never_writes(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("UPDATE sessions SET session_revision = 41 WHERE session_id='s1'")
            raise RuntimeError("abort")
    assert db.get_session_revision("s1") == 0
    with db.read() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE sessions SET session_revision = 41")


# ------------------------------------------------------------------ revisions (A13)


def test_accept_scene_turn_appends_a_v2_message_and_bumps_session_revision(db):
    result, revision = db.accept_scene_turn(
        learner_id=LEARNER, session_id="s1", text="I want to discuss about it.", idempotency_key="tg:1:100",
        correlation_id="turn_1", input_modality="voice", telegram_message_id=100,
    )
    assert result.inserted and revision == 1 and db.get_session_revision("s1") == 1
    row = log_rows(db)[0]
    assert (row["event_type"], row["schema_version"]) == ("learner_message_received", "2.0")
    assert json.loads(row["payload_json"]) == {
        "text": "I want to discuss about it.", "input_modality": "voice", "origin": "scene",
        "session_revision": 1, "interaction_id": None, "telegram_message_id": 100,
    }
    _, second = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="Next", idempotency_key="tg:1:101", correlation_id="turn_2")
    assert second == 2


def test_accept_scene_turn_replay_returns_the_original_revision_and_changes_nothing(db):
    first, rev1 = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="one", idempotency_key="k1", correlation_id="c1")
    db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="two", idempotency_key="k2", correlation_id="c2")
    replay, rev_again = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="one", idempotency_key="k1", correlation_id="c1")
    assert (replay.inserted, replay.event_id, replay.event_seq) == (False, first.event_id, first.event_seq)
    assert rev_again == rev1 == 1
    assert db.get_session_revision("s1") == 2 and len(log_rows(db)) == 2


def test_accept_scene_turn_is_atomic(db):
    with pytest.raises(ValidationError):
        db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="x", idempotency_key="k1", correlation_id="c1", input_modality="video")
    assert db.get_session_revision("s1") == 0 and log_rows(db) == []
    with pytest.raises(LookupError):
        db.accept_scene_turn(learner_id=LEARNER, session_id="nope", text="x", idempotency_key="k2", correlation_id="c2")
    with pytest.raises(LookupError):  # someone else's session
        db.accept_scene_turn(learner_id=999, session_id="s1", text="x", idempotency_key="k3", correlation_id="c3")
    assert log_rows(db) == []


def test_concurrent_turns_get_unique_consecutive_revisions(db_path, clock):
    EventSourcedDatabase(db_path, clock=clock).upsert_learner(LEARNER)
    EventSourcedDatabase(db_path, clock=clock).create_session("race", LEARNER, started_at_utc=t(0))
    revisions: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            local = EventSourcedDatabase(db_path, clock=clock, auto_migrate=False)
            _, rev = local.accept_scene_turn(learner_id=LEARNER, session_id="race", text=f"m{n}", idempotency_key=f"race:{n}", correlation_id=f"c{n}")
            with lock:
                revisions.append(rev)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not errors, errors
    assert sorted(revisions) == list(range(1, 9))
    final = EventSourcedDatabase(db_path, clock=clock, auto_migrate=False)
    assert final.get_session_revision("race") == 8
    assert len(rows(final, "SELECT 1 FROM event_log WHERE event_type='learner_message_received'")) == 8


def test_scene_revision_is_a_separate_counter(db):
    assert db.get_scene_revision("s1") == 0
    assert db.bump_scene_revision("s1") == 1 and db.bump_scene_revision("s1") == 2
    assert db.get_session_revision("s1") == 0
    db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="x", idempotency_key="k1", correlation_id="c1")
    assert (db.get_session_revision("s1"), db.get_scene_revision("s1")) == (1, 2)
    for fn in (db.get_session_revision, db.get_scene_revision, db.bump_scene_revision):
        with pytest.raises(LookupError):
            fn("missing")


# ------------------------------------------------------------------ compare-and-set (A14)


def skipped(n: int, **kw) -> EventSpec:
    return EventSpec(event_type="analysis_skipped", payload={"turn_event_id": f"ev_{n}", "reason": "trivial", "detail": None},
                     idempotency_key=f"cas:{n}", correlation_id="turn_1", **kw)


def test_cas_writes_every_event_when_the_revision_matches(db):
    _, rev = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="x", idempotency_key="k1", correlation_id="c1")
    results = db.append_events_if_revision_ex("s1", rev, [skipped(1), skipped(2)])
    assert results is not None and [r.inserted for r in results] == [True, True]
    assert db.append_events_if_revision("s1", rev, [skipped(3)]) is True
    assert len(rows(db, "SELECT 1 FROM event_log WHERE event_type='analysis_skipped'")) == 3


def test_cas_writes_nothing_when_the_revision_moved(db):
    _, rev = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="x", idempotency_key="k1", correlation_id="c1")
    db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="newer", idempotency_key="k2", correlation_id="c2")
    before = len(log_rows(db))
    assert db.append_events_if_revision_ex("s1", rev, [skipped(1), skipped(2)]) is None
    assert db.append_events_if_revision("s1", rev, [skipped(1)]) is False
    assert len(log_rows(db)) == before


def test_cas_is_all_or_nothing_when_one_event_is_invalid(db):
    bad = EventSpec(event_type="analysis_skipped", payload={"turn_event_id": "ev_x", "reason": "bored"},
                    idempotency_key="cas:bad", correlation_id="turn_1")
    with pytest.raises(ValidationError):
        db.append_events_if_revision("s1", 0, [skipped(1), bad])
    assert log_rows(db) == []


def test_cas_replay_is_safe(db):
    assert db.append_events_if_revision("s1", 0, [skipped(1), skipped(2)])
    again = db.append_events_if_revision_ex("s1", 0, [skipped(1), skipped(2), skipped(3)])
    assert [r.inserted for r in again] == [False, False, True]
    assert len(log_rows(db)) == 3


def test_cas_unknown_session_and_empty_spec_list(db):
    with pytest.raises(LookupError):
        db.append_events_if_revision("missing", 0, [skipped(1)])
    assert db.append_events_if_revision("s1", 0, []) is True


def test_cas_rebuilds_projections_for_the_learner(db):
    spec = EventSpec(event_type="phrase_exposure_recorded", payload={"phrase_id": "plate_is_full", "revealed_to_learner": True, "directly_cued": False},
                     idempotency_key="cas:expo", correlation_id="turn_1", schema_version="1.0", rubric_version="v1.0")
    assert db.append_events_if_revision("s1", 0, [spec])
    assert json.loads(rows(db, "SELECT revealed_targets_json FROM session_state_projection WHERE session_id='s1'")[0][0]) == ["plate_is_full"]


# ------------------------------------------------------------------ pending clarification (A14)


def test_pending_clarification_is_stored_and_injected_once(db):
    assert db.take_pending_clarification("s1") is None
    db.set_pending_clarification("s1", "Sorry — which report do you mean؟")
    assert db.take_pending_clarification("s1") == "Sorry — which report do you mean؟"
    assert db.take_pending_clarification("s1") is None


def test_pending_clarification_survives_a_restart_and_can_be_cleared(db_path, clock):
    first = EventSourcedDatabase(db_path, clock=clock)
    first.upsert_learner(LEARNER)
    first.create_session("s1", LEARNER, started_at_utc=t(0))
    first.set_pending_clarification("s1", "Do you mean the Q3 report?")
    second = EventSourcedDatabase(db_path, clock=clock)
    assert second.take_pending_clarification("s1") == "Do you mean the Q3 report?"
    second.set_pending_clarification("s1", "again")
    second.set_pending_clarification("s1", "   ")  # blank clears
    assert second.take_pending_clarification("s1") is None
    second.set_pending_clarification("s1", "again")
    second.set_pending_clarification("s1", None)
    assert second.take_pending_clarification("s1") is None


def test_pending_clarification_unknown_session(db):
    with pytest.raises(LookupError):
        db.set_pending_clarification("missing", "x")
    with pytest.raises(LookupError):
        db.take_pending_clarification("missing")


# ------------------------------------------------------------------ learner settings and audio cache


def test_auto_voice_defaults_off_and_persists(db_path, clock):
    first = EventSourcedDatabase(db_path, clock=clock)
    assert first.get_auto_voice(4242) is False  # unknown learner
    first.set_auto_voice(4242, True)  # creates the learner if needed
    assert EventSourcedDatabase(db_path, clock=clock).get_auto_voice(4242) is True
    first.set_auto_voice(4242, False)
    assert first.get_auto_voice(4242) is False
    assert rows(first, "SELECT updated_at_utc FROM learner_settings WHERE learner_id=4242")[0][0] == "2026-10-01T09:00:00.000000Z"


def test_audio_cache_roundtrip_replace_and_drop(db, clock):
    assert db.get_audio_file_id("h1", "en-US:a") is None
    db.put_audio_file_id("h1", "en-US:a", "FILE_1", "UNIQ_1", 3.4)
    db.put_audio_file_id("h1", "en-GB:b", "FILE_B", None, None)
    assert db.get_audio_file_id("h1", "en-US:a") == "FILE_1" and db.get_audio_file_id("h1", "en-GB:b") == "FILE_B"
    clock.advance(60)
    db.put_audio_file_id("h1", "en-US:a", "FILE_2", "UNIQ_2", 3.5)
    assert db.get_audio_file_id("h1", "en-US:a") == "FILE_2"
    assert rows(db, "SELECT created_at_utc FROM audio_cache WHERE voice_config='en-US:a'")[0][0] == "2026-10-01T09:01:00.000000Z"
    db.drop_audio_file_id("h1", "en-US:a")
    assert db.get_audio_file_id("h1", "en-US:a") is None and db.get_audio_file_id("h1", "en-GB:b") == "FILE_B"
    db.drop_audio_file_id("h1", "en-US:a")  # dropping twice is fine


# ------------------------------------------------------------------ phrases


def test_register_phrase_never_wipes_the_v3_catalog_columns(db):
    db.register_phrase("plate_is_full", "My plate is full.", "decline_politely", "possessive + be + full", ["casual"], "core")
    with db.transaction() as conn:
        conn.execute("UPDATE phrase_catalog SET catalog_version = '3.0.1', arabic_gloss = 'مشغول' WHERE phrase_id = 'plate_is_full'")
    db.register_phrase("plate_is_full", "My plate is quite full.", "decline_politely", "possessive + be + full", ["casual", "diplomatic"], "core")
    row = rows(db, "SELECT canonical_form, catalog_version, arabic_gloss FROM phrase_catalog WHERE phrase_id='plate_is_full'")[0]
    assert tuple(row) == ("My plate is quite full.", "3.0.1", "مشغول")


def test_start_up_does_not_overwrite_a_refined_builtin_phrase(db_path, clock):
    first = EventSourcedDatabase(db_path, clock=clock)
    with first.transaction() as conn:
        conn.execute("UPDATE phrase_catalog SET canonical_form = 'REFINED' WHERE phrase_id = (SELECT MIN(phrase_id) FROM phrase_catalog)")
        refined = conn.execute("SELECT phrase_id FROM phrase_catalog WHERE canonical_form = 'REFINED'").fetchone()[0]
    EventSourcedDatabase(db_path, clock=clock)
    assert rows(first, "SELECT canonical_form FROM phrase_catalog WHERE phrase_id=?", (refined,))[0][0] == "REFINED"


# ------------------------------------------------------------------ projections driven by V2 events


def scenario_turn(db, n: int, minute: int, *, pattern="grammar:discuss_about", dg_error=None, dg_upgrade=None, skip=None):
    """One scene turn: message, (optional skip), analysis with an error and an upgrade, policy, reply."""
    dg_error = dg_error or f"dg_{n:08x}"
    dg_upgrade = dg_upgrade or f"dg_{n + 0x1000:08x}"
    msg, _ = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="I want to discuss about this plan.",
                                  idempotency_key=f"turn:{n}", correlation_id=f"turn_{n}", occurred_at_utc=t(minute))
    if skip:
        put(db, "analysis_skipped", {"turn_event_id": msg.event_id, "reason": skip, "detail": None}, f"skip:{n}", at=t(minute, 1), corr=f"turn_{n}")
    analysis = put(
        db, "analyst_diagnosis_recorded",
        v2_analysis_dict(
            msg.event_id,
            v2_diagnostic_dict(dg_error, normalized_pattern_id=pattern),
            v2_diagnostic_dict(dg_upgrade, kind="optional_upgrade", classification="upgrade", alternative_status="optional",
                               normalized_pattern_id="style:plan", original_span="plan", alternative="proposal",
                               span_start=29, span_end=33, severity="low", confidence_score=0.82, linguistic_confidence=0.82),
        ),
        f"analysis:{n}", at=t(minute, 2), corr=f"turn_{n}", causation=msg.event_id,
    )
    return msg, analysis, dg_error, dg_upgrade


def test_findings_become_queue_items_and_follow_the_policy_decision(db):
    msg, analysis, dg_err, dg_up = scenario_turn(db, 1, 1)
    items = {r["diagnostic_id"]: r for r in rows(db, "SELECT * FROM feedback_item_projection")}
    assert set(items) == {dg_err, dg_up}
    assert (items[dg_err]["status"], items[dg_err]["delivery"], items[dg_err]["priority_class"]) == ("pending", "suppressed", 4)
    assert items[dg_err]["turn_event_seq"] == msg.event_seq and items[dg_err]["created_at_utc"] == t(1, 2)

    put(db, "policy_decision_recorded",
        v2_policy_dict(msg.event_id, decision_dict(dg_err, 1, 72.5, "end_of_turn"), decision_dict(dg_up, 4, 10.0, "deferred"), selected=dg_err),
        "policy:1", at=t(1, 3), causation=analysis.event_id)
    items = {r["diagnostic_id"]: r for r in rows(db, "SELECT * FROM feedback_item_projection")}
    assert (items[dg_err]["priority_class"], items[dg_err]["priority_score"], items[dg_err]["delivery"]) == (1, 72.5, "end_of_turn")
    assert (items[dg_up]["priority_class"], items[dg_up]["delivery"]) == (4, "deferred")


def test_a_delivered_card_updates_item_session_and_pattern_counters(db):
    msg, analysis, dg_err, dg_up = scenario_turn(db, 1, 1)
    put(db, "policy_decision_recorded", v2_policy_dict(msg.event_id, decision_dict(dg_err), selected=dg_err), "policy:1", at=t(1, 3))
    put(db, "responder_reply_delivered", reply_payload(msg.event_id), "reply:1", at=t(1, 4), causation=msg.event_id)
    put(db, "feedback_card_delivered", {"diagnostic_id": dg_err, "card_id": "card_1", "trigger": "end_of_turn", "telegram_message_id": 5},
        "card:1", at=t(1, 5))
    item = rows(db, "SELECT status, delivered_at_utc FROM feedback_item_projection WHERE diagnostic_id=?", (dg_err,))[0]
    assert tuple(item) == ("delivered", t(1, 5))
    session = rows(db, "SELECT turns_since_visible_card, cards_in_scene FROM session_state_projection WHERE session_id='s1'")[0]
    assert tuple(session) == (0, 1)
    pattern = rows(db, "SELECT distinct_eligible_occurrences, times_feedback_selected, times_feedback_shown "
                       "FROM error_pattern_projection WHERE normalized_pattern_id='grammar:discuss_about'")[0]
    assert tuple(pattern) == (1, 1, 1)
    # an upgrade is a queue item but never an error occurrence
    assert rows(db, "SELECT 1 FROM error_pattern_projection WHERE normalized_pattern_id='style:plan'") == []
    put(db, "responder_reply_delivered", reply_payload(msg.event_id), "reply:2", at=t(2), causation=msg.event_id)
    assert rows(db, "SELECT turns_since_visible_card FROM session_state_projection WHERE session_id='s1'")[0][0] == 1


def test_a_debrief_delivery_merges_the_earlier_items_of_the_same_pattern(db):
    _, _, first_err, _ = scenario_turn(db, 1, 1)
    _, _, second_err, _ = scenario_turn(db, 2, 5, dg_upgrade="dg_00002002")
    put(db, "feedback_card_delivered", {"diagnostic_id": second_err, "card_id": "card_2", "trigger": "debrief"}, "card:2", at=t(9))
    state = {r["diagnostic_id"]: (r["status"], r["merged_into"]) for r in rows(db, "SELECT * FROM feedback_item_projection")}
    assert state[second_err] == ("delivered", None)
    assert state[first_err] == ("merged", second_err)
    # an in-scene tap does not merge anything
    _, _, third_err, _ = scenario_turn(db, 3, 11, dg_upgrade="dg_00003003")
    _, _, fourth_err, _ = scenario_turn(db, 4, 13, dg_upgrade="dg_00004004")
    put(db, "feedback_card_delivered", {"diagnostic_id": fourth_err, "card_id": "card_4", "trigger": "tap"}, "card:4", at=t(15))
    state = {r["diagnostic_id"]: r["status"] for r in rows(db, "SELECT * FROM feedback_item_projection")}
    assert state[third_err] == "pending" and state[fourth_err] == "delivered"


def test_late_analysis_after_a_shed_turn_is_forced_to_deferred(db):
    msg, analysis, dg_err, _ = scenario_turn(db, 1, 1, skip="shed")
    put(db, "policy_decision_recorded", v2_policy_dict(msg.event_id, decision_dict(dg_err, 1, 80.0, "end_of_turn"), selected=dg_err), "policy:1", at=t(1, 3))
    item = rows(db, "SELECT delivery, priority_class, status FROM feedback_item_projection WHERE diagnostic_id=?", (dg_err,))[0]
    assert tuple(item) == ("deferred", 1, "pending")


def test_a_trivial_skip_is_not_a_backfill(db):
    msg, _, dg_err, _ = scenario_turn(db, 1, 1, skip="trivial")
    put(db, "policy_decision_recorded", v2_policy_dict(msg.event_id, decision_dict(dg_err, 1, 80.0, "end_of_turn"), selected=dg_err), "policy:1", at=t(1, 3))
    assert rows(db, "SELECT delivery FROM feedback_item_projection WHERE diagnostic_id=?", (dg_err,))[0][0] == "end_of_turn"


def test_a_retracted_diagnostic_leaves_the_queue_and_the_error_counts(db):
    msg, analysis, dg_err, dg_up = scenario_turn(db, 1, 1)
    put(db, "diagnostic_retracted", {"target_event_id": analysis.event_id, "diagnostic_id": dg_err, "reason": "false_positive"}, "ret:1", at=t(2))
    assert rows(db, "SELECT status FROM feedback_item_projection WHERE diagnostic_id=?", (dg_err,))[0][0] == "retracted"
    assert rows(db, "SELECT status FROM feedback_item_projection WHERE diagnostic_id=?", (dg_up,))[0][0] == "pending"
    assert rows(db, "SELECT 1 FROM error_pattern_projection") == []


def test_a_p0_followup_resets_turns_since_p0(db):
    msg, _, dg_err, _ = scenario_turn(db, 1, 1)
    assert rows(db, "SELECT turns_since_p0 FROM session_state_projection WHERE session_id='s1'")[0][0] == 99
    put(db, "policy_decision_recorded", v2_policy_dict(msg.event_id, decision_dict(dg_err, 0, 100.0, "immediate_followup"), selected=dg_err), "policy:1", at=t(1, 3))
    assert rows(db, "SELECT turns_since_p0 FROM session_state_projection WHERE session_id='s1'")[0][0] == 0
    put(db, "responder_reply_delivered", reply_payload(msg.event_id), "reply:1", at=t(1, 4), causation=msg.event_id)
    assert rows(db, "SELECT turns_since_p0 FROM session_state_projection WHERE session_id='s1'")[0][0] == 1


def test_incremental_projections_equal_a_full_rebuild(db):
    msg, analysis, dg_err, dg_up = scenario_turn(db, 1, 1)
    put(db, "policy_decision_recorded", v2_policy_dict(msg.event_id, decision_dict(dg_err), selected=dg_err), "policy:1", at=t(1, 3))
    put(db, "feedback_card_delivered", {"diagnostic_id": dg_err, "card_id": "card_1", "trigger": "end_of_turn"}, "card:1", at=t(1, 5))
    scenario_turn(db, 2, 4, dg_upgrade="dg_00002002")
    put(db, "diagnostic_retracted", {"target_event_id": analysis.event_id, "diagnostic_id": dg_up}, "ret:1", at=t(6))
    incremental = projection_rows(db)
    db.rebuild_all_projections()
    assert projection_rows(db) == incremental


def test_events_are_read_in_occurrence_order_not_insertion_order(db):
    first_msg, _, dg_err, _ = scenario_turn(db, 1, 10)
    # recorded later but happened earlier: replay must put it first
    put(db, "responder_reply_delivered", reply_payload(first_msg.event_id), "reply:early", at=t(1), causation=first_msg.event_id)
    put(db, "feedback_card_delivered", {"diagnostic_id": dg_err, "card_id": "c", "trigger": "end_of_turn"}, "card:1", at=t(11))
    assert tuple(rows(db, "SELECT turns_since_visible_card, cards_in_scene FROM session_state_projection WHERE session_id='s1'")[0]) == (0, 1)


# ------------------------------------------------------------------ phrase evidence (S0 5.5 legacy mapping)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("recognition", [("recognition", 100.0, 1.0, "drill")]),
        ("cued_production", [("production", 75.0, 0.8, "drill")]),
        ("independent_production", [("independence", 75.0, 1.0, "drill"), ("production", 75.0, 1.0, "drill")]),
        ("spontaneous_production", [("independence", 75.0, 1.0, "spontaneous"), ("production", 75.0, 1.0, "spontaneous")]),
    ],
)
def test_legacy_assessments_map_to_phrase_evidence_rows(db, evidence, expected):
    db.append_event(
        learner_id=LEARNER, session_id="s1", event_type="phrase_production_assessed",
        payload={"target_id": "plate_is_full", "evidence": evidence, "was_revealed_in_task": False, "was_directly_cued": evidence == "cued_production",
                 "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": True,
                 "register_profile": "casual", "training_mode": "open_scene"},
        correlation_id="a1", idempotency_key=f"assess:{evidence}", occurred_at_utc=t(3), event_id="ev_assessment1",
    )
    got = rows(db, "SELECT dimension, score, weight, origin FROM phrase_evidence WHERE phrase_id='plate_is_full' ORDER BY dimension")
    assert [tuple(r) for r in got] == expected
    assert {r["source_event_id"] for r in rows(db, "SELECT source_event_id FROM phrase_evidence")} == {"ev_assessment1"}


def test_failed_legacy_assessment_writes_no_positive_evidence(db):
    db.append_event(
        learner_id=LEARNER, session_id="s1", event_type="phrase_production_assessed",
        payload={"target_id": "plate_is_full", "evidence": "independent_production", "was_revealed_in_task": False, "was_directly_cued": False,
                 "fits_communicative_context": True, "fits_register": True, "phrase_specific_rubric_passed": False,
                 "register_profile": "casual", "training_mode": "open_scene"},
        correlation_id="a1", idempotency_key="assess:fail", occurred_at_utc=t(3),
    )
    assert rows(db, "SELECT 1 FROM phrase_evidence WHERE retracted = 0 AND score > 0") == []


# ------------------------------------------------------------------ v1 data read through the new code


def test_v1_rows_written_by_the_legacy_wrapper_rebuild_like_v2_did(db):
    payload = v1_analysis_payload(v1_diagnostic("d1"), v1_diagnostic("d2", span="plan", pattern="style:plan",
                                                                    kind=__import__("schemas").IssueKind.OPTIONAL_UPGRADE,
                                                                    alternative="proposal",
                                                                    status=__import__("schemas").AlternativeStatus.OPTIONAL, score=0.82))
    db.append_event(learner_id=LEARNER, session_id="s1", event_type="analyst_diagnosis_recorded", payload=payload,
                    correlation_id="turn_1", idempotency_key="v1-analysis", occurred_at_utc=t(1), causation_id="ev_msg1")
    assert [tuple(r) for r in rows(db, "SELECT normalized_pattern_id, distinct_eligible_occurrences FROM error_pattern_projection")] == [("grammar:discuss_about", 1)]
    assert len(rows(db, "SELECT 1 FROM feedback_item_projection")) == 2
    assert all(r[0].startswith("dg_") for r in rows(db, "SELECT diagnostic_id FROM feedback_item_projection"))
