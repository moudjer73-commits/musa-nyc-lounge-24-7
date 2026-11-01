"""S0 5.4 (state machine) and 7 (callback wire format)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta

import pytest

from clock import FakeClock, to_iso
from database import EventSourcedDatabase
from interaction import (
    ALLOWED_TRANSITIONS,
    CALLBACK_OPS,
    DEFAULT_TTL_S,
    MAX_CALLBACK_BYTES,
    MAX_OBJECT_ID,
    MAX_REVISION,
    SAVED_TO_REVIEW_TEXT,
    STALE_CALLBACK_TEXT,
    STATE_OBJECT,
    IllegalTransition,
    ParsedCallback,
    TextRoute,
    build_callback,
    callback_matches,
    close_interaction,
    close_learner_interaction,
    default_ttl_s,
    get_active_interaction,
    open_interaction,
    parse_callback,
    route_text,
    transition,
    ug_is_current,
)
from schemas import InteractionObject as Obj
from schemas import InteractionState as St

LEARNER = 1001
TTL_15 = 15 * 60


def open_row(db, clock, state=St.UPGRADE_VIEW, *, learner=LEARNER, ref="dg_0a1b2c3d", payload=None, scene_revision=0, ttl_s=None):
    return open_interaction(
        db, learner_id=learner, session_id="s1", object_type=STATE_OBJECT[state], object_ref=ref, state=state,
        payload=payload if payload is not None else {"n": 0}, scene_revision=scene_revision, now=clock.now(),
        ttl_s=ttl_s if ttl_s is not None else DEFAULT_TTL_S[state],
    )


def count_rows(db) -> int:
    with db.read() as conn:
        return conn.execute("SELECT COUNT(*) FROM active_interaction").fetchone()[0]


# ------------------------------------------------------------------ constants


def test_ttls_and_texts_follow_s0():
    assert DEFAULT_TTL_S == {
        St.UPGRADE_VIEW: 900, St.RECONSTRUCTION_ACTIVE: 900, St.RECONSTRUCTION_FEEDBACK: 900,
        St.REVIEW_ACTIVE: 1800, St.DECODE_AWAIT: 1800, St.WORKOUT_ACTIVE: 1800,
    }
    assert default_ttl_s(St.REVIEW_ACTIVE) == 1800
    assert STALE_CALLBACK_TEXT == "That option expired." and SAVED_TO_REVIEW_TEXT == "Saved to Review"


def test_every_state_belongs_to_exactly_one_object():
    assert set(STATE_OBJECT) == set(St)
    assert STATE_OBJECT[St.UPGRADE_VIEW] == Obj.UPGRADE
    assert STATE_OBJECT[St.RECONSTRUCTION_ACTIVE] == STATE_OBJECT[St.RECONSTRUCTION_FEEDBACK] == Obj.RECONSTRUCTION


def test_in_place_moves_never_change_the_object_type():
    for current, targets in ALLOWED_TRANSITIONS.items():
        assert all(STATE_OBJECT[t] == STATE_OBJECT[current] for t in targets)
    assert ALLOWED_TRANSITIONS[St.UPGRADE_VIEW] == frozenset()  # rc opens a NEW row (S0 5.4)


# ------------------------------------------------------------------ persistence


def test_open_creates_revision_1_with_the_ttl_and_payload(db, clock):
    row = open_row(db, clock, payload={"diagnostic_id": "dg_0a1b2c3d", "original": "discuss about"}, scene_revision=7)
    assert (row.revision, row.state, row.object_type, row.object_ref) == (1, St.UPGRADE_VIEW, Obj.UPGRADE, "dg_0a1b2c3d")
    assert (row.learner_id, row.session_id, row.scene_revision_at_open) == (LEARNER, "s1", 7)
    assert row.payload == {"diagnostic_id": "dg_0a1b2c3d", "original": "discuss about"}
    assert row.created_at == clock.now() and row.expires_at == clock.now() + timedelta(seconds=TTL_15)
    assert not row.is_expired(clock.now()) and row.is_expired(clock.now() + timedelta(seconds=TTL_15))


def test_open_replaces_the_learners_row_and_ids_are_never_reused(db, clock):
    first = open_row(db, clock)
    second = open_row(db, clock, St.REVIEW_ACTIVE, ref="review")
    assert second.interaction_id != first.interaction_id and count_rows(db) == 1
    assert get_active_interaction(db, LEARNER, clock.now()).interaction_id == second.interaction_id
    close_interaction(db, interaction_id=second.interaction_id)
    third = open_row(db, clock)
    assert third.interaction_id > second.interaction_id > first.interaction_id
    # a button from the very first row can never address a newer one
    assert not callback_matches(third, ParsedCallback("rc", first.interaction_id, 1))


def test_open_validates_its_inputs(db, clock):
    with pytest.raises(ValueError):  # a review state cannot hold an upgrade object
        open_interaction(db, learner_id=LEARNER, session_id="s1", object_type=Obj.UPGRADE, object_ref="x",
                         state=St.REVIEW_ACTIVE, payload={}, scene_revision=0, now=clock.now(), ttl_s=60)
    for bad in (0, -5, True, "60", float("-inf")):
        with pytest.raises(ValueError):
            open_row(db, clock, ttl_s=bad)
    with pytest.raises(ValueError):
        open_interaction(db, learner_id=LEARNER, session_id="s1", object_type="nonsense", object_ref="x",
                         state=St.UPGRADE_VIEW, payload={}, scene_revision=0, now=clock.now(), ttl_s=60)
    with pytest.raises(TypeError):
        open_row(db, clock, payload=["not", "a", "mapping"])
    with pytest.raises(ValueError):  # naive datetimes are a bug
        open_interaction(db, learner_id=LEARNER, session_id="s1", object_type=Obj.UPGRADE, object_ref="x",
                         state=St.UPGRADE_VIEW, payload={}, scene_revision=0, now=clock.now().replace(tzinfo=None), ttl_s=60)
    assert count_rows(db) == 0


def test_the_row_survives_a_process_restart(db_path, clock):
    first = EventSourcedDatabase(db_path, clock=clock)
    first.upsert_learner(LEARNER)
    first.create_session("s1", LEARNER, started_at_utc="2026-10-01T09:00:00.000000Z")
    opened = open_row(first, clock, St.RECONSTRUCTION_ACTIVE, payload={"gloss": "مرحبا", "attempts": 1})
    del first
    reborn = EventSourcedDatabase(db_path, clock=clock)
    again = get_active_interaction(reborn, LEARNER, clock.now())
    assert again == opened
    assert again.payload["gloss"] == "مرحبا"


def test_ttl_expiry_is_lazy_and_driven_by_the_injected_clock(db, clock):
    open_row(db, clock)
    clock.advance(TTL_15 - 0.001)
    assert get_active_interaction(db, LEARNER, clock.now()) is not None and count_rows(db) == 1
    clock.advance(0.001)  # now == expires_at: no longer valid
    assert count_rows(db) == 1  # nothing deletes it until somebody reads it
    assert get_active_interaction(db, LEARNER, clock.now()) is None
    assert count_rows(db) == 0


def test_rows_are_per_learner(db, clock):
    db.upsert_learner(1002)
    db.create_session("s2", 1002, started_at_utc="2026-10-01T09:00:00.000000Z")
    mine = open_row(db, clock)
    theirs = open_row(db, clock, St.REVIEW_ACTIVE, learner=1002)
    assert count_rows(db) == 2
    assert close_learner_interaction(db, LEARNER) is True
    assert get_active_interaction(db, LEARNER, clock.now()) is None
    assert get_active_interaction(db, 1002, clock.now()).interaction_id == theirs.interaction_id
    assert close_learner_interaction(db, LEARNER) is False
    assert mine.interaction_id != theirs.interaction_id


def test_the_table_enforces_one_row_per_learner(db, clock):
    open_row(db, clock)
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO active_interaction (learner_id, session_id, object_type, object_ref, state, scene_revision_at_open, created_at_utc, expires_at_utc) "
                "VALUES (?, 's1', 'upgrade', 'x', 'upgrade_view', 0, 'a', 'b')", (LEARNER,),
            )


# ------------------------------------------------------------------ transitions


def test_transition_is_a_cas_that_bumps_the_revision_merges_payload_and_restarts_the_ttl(db, clock):
    row = open_row(db, clock, St.RECONSTRUCTION_ACTIVE, payload={"keep": 1, "replace": "old", "nested": {"a": 1}})
    clock.advance(600)
    moved = transition(db, interaction_id=row.interaction_id, expected_revision=1, new_state=St.RECONSTRUCTION_FEEDBACK,
                       payload_patch={"replace": "new", "attempt_text": "I'd like to discuss it"}, now=clock.now(), ttl_s=TTL_15)
    assert moved is not None
    assert (moved.interaction_id, moved.revision, moved.state, moved.object_type) == (row.interaction_id, 2, St.RECONSTRUCTION_FEEDBACK, Obj.RECONSTRUCTION)
    assert moved.payload == {"keep": 1, "replace": "new", "nested": {"a": 1}, "attempt_text": "I'd like to discuss it"}
    assert moved.created_at == row.created_at
    assert moved.expires_at == clock.now() + timedelta(seconds=TTL_15)
    assert get_active_interaction(db, LEARNER, clock.now()) == moved


def test_a_stale_revision_changes_nothing(db, clock):
    row = open_row(db, clock, St.REVIEW_ACTIVE, payload={"index": 0})
    first = transition(db, interaction_id=row.interaction_id, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                       payload_patch={"index": 1}, now=clock.now(), ttl_s=1800)
    again = transition(db, interaction_id=row.interaction_id, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                       payload_patch={"index": 99}, now=clock.now(), ttl_s=1800)
    assert first.revision == 2 and again is None
    assert get_active_interaction(db, LEARNER, clock.now()).payload == {"index": 1}


def test_a_missing_or_expired_row_cannot_be_moved(db, clock):
    assert transition(db, interaction_id=12345, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                      payload_patch={}, now=clock.now(), ttl_s=60) is None
    row = open_row(db, clock, St.REVIEW_ACTIVE)
    clock.advance(1800)
    assert transition(db, interaction_id=row.interaction_id, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                      payload_patch={}, now=clock.now(), ttl_s=1800) is None
    assert count_rows(db) == 0  # and the dead row was cleaned up


@pytest.mark.parametrize("current", list(St))
@pytest.mark.parametrize("target", list(St))
def test_only_the_s0_moves_are_allowed(db, clock, current, target):
    row = open_row(db, clock, current)
    args = dict(interaction_id=row.interaction_id, expected_revision=1, new_state=target, payload_patch={}, now=clock.now(), ttl_s=60)
    if target in ALLOWED_TRANSITIONS[current]:
        assert transition(db, **args).state == target
    else:
        with pytest.raises(IllegalTransition):
            transition(db, **args)
        unchanged = get_active_interaction(db, LEARNER, clock.now())
        assert (unchanged.state, unchanged.revision) == (current, 1)


def test_review_items_can_be_stepped_many_times(db, clock):
    row = open_row(db, clock, St.REVIEW_ACTIVE, payload={"phrase_ids": ["a", "b", "c", "d", "e"], "index": 0, "hint_level": 0})
    for step in range(1, 6):
        row = transition(db, interaction_id=row.interaction_id, expected_revision=row.revision, new_state=St.REVIEW_ACTIVE,
                         payload_patch={"index": step}, now=clock.now(), ttl_s=1800)
        assert row.revision == step + 1 and row.payload["index"] == step


def test_rc_flow_opens_a_new_row_and_kills_the_old_button(db, clock):
    upgrade = open_row(db, clock, St.UPGRADE_VIEW)
    old_button = parse_callback(build_callback("rc", upgrade.interaction_id, upgrade.revision))
    assert callback_matches(get_active_interaction(db, LEARNER, clock.now()), old_button)
    reconstruction = open_row(db, clock, St.RECONSTRUCTION_ACTIVE, ref=upgrade.object_ref, payload={"from_upgrade": upgrade.interaction_id})
    assert reconstruction.interaction_id != upgrade.interaction_id and reconstruction.revision == 1
    assert not callback_matches(get_active_interaction(db, LEARNER, clock.now()), old_button)  # "That option expired."


def test_two_racing_taps_move_the_row_exactly_once(db_path, clock):
    first = EventSourcedDatabase(db_path, clock=clock)
    first.upsert_learner(LEARNER)
    first.create_session("s1", LEARNER, started_at_utc="2026-10-01T09:00:00.000000Z")
    row = open_row(first, clock, St.REVIEW_ACTIVE, payload={"index": 0})
    barrier = threading.Barrier(6)
    results: list = []
    errors: list = []

    def tap(n: int) -> None:
        try:
            local = EventSourcedDatabase(db_path, clock=clock, auto_migrate=False)
            barrier.wait(30)
            results.append(transition(local, interaction_id=row.interaction_id, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                                      payload_patch={"winner": n}, now=clock.now(), ttl_s=1800))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=tap, args=(n,)) for n in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not errors, errors
    assert len([r for r in results if r is not None]) == 1
    assert get_active_interaction(first, LEARNER, clock.now()).revision == 2


# ------------------------------------------------------------------ closing and text routing


def test_close_respects_the_expected_revision(db, clock):
    row = open_row(db, clock, St.REVIEW_ACTIVE)
    assert close_interaction(db, interaction_id=row.interaction_id, expected_revision=7) is False
    assert count_rows(db) == 1
    assert close_interaction(db, interaction_id=row.interaction_id, expected_revision=1) is True
    assert close_interaction(db, interaction_id=row.interaction_id) is False


def test_free_text_with_no_row_is_a_scene_turn(db, clock):
    assert route_text(db, LEARNER, clock.now()) == (TextRoute.SCENE_TURN, None)


@pytest.mark.parametrize(
    ("state", "route", "row_stays"),
    [
        (St.UPGRADE_VIEW, TextRoute.SCENE_TURN, False),
        (St.RECONSTRUCTION_FEEDBACK, TextRoute.SCENE_TURN, False),
        (St.RECONSTRUCTION_ACTIVE, TextRoute.RECONSTRUCTION_ATTEMPT, True),
        (St.REVIEW_ACTIVE, TextRoute.REVIEW_ATTEMPT, True),
        (St.DECODE_AWAIT, TextRoute.DECODE_ANSWER, True),
        (St.WORKOUT_ACTIVE, TextRoute.WORKOUT_ATTEMPT, True),
    ],
)
def test_text_routing_per_state(db, clock, state, route, row_stays):
    row = open_row(db, clock, state)
    got_route, got_row = route_text(db, LEARNER, clock.now())
    assert got_route == route
    assert (got_row == row) if row_stays else (got_row is None)
    assert count_rows(db) == (1 if row_stays else 0)


def test_text_after_the_ttl_is_a_scene_turn_again(db, clock):
    open_row(db, clock, St.REVIEW_ACTIVE)
    clock.advance(1800)
    assert route_text(db, LEARNER, clock.now()) == (TextRoute.SCENE_TURN, None)
    assert count_rows(db) == 0


# ------------------------------------------------------------------ callback wire format


def test_known_ops_are_exactly_the_s0_table():
    assert CALLBACK_OPS == {"ug", "rc", "rs", "bk", "rvn", "rvh", "rvs", "ls"}


@pytest.mark.parametrize("op", sorted(CALLBACK_OPS))
def test_callbacks_round_trip(op):
    for obj_id, rev in ((0, 0), (1, 1), (42, 3), (123456789012 % (MAX_OBJECT_ID + 1), 999)):
        data = build_callback(op, obj_id, rev)
        assert data == f"{op}:{obj_id}:{rev}"
        assert parse_callback(data) == ParsedCallback(op, obj_id, rev)


def test_the_widest_callback_still_fits_in_32_bytes():
    widest = max((build_callback(op, MAX_OBJECT_ID, MAX_REVISION) for op in CALLBACK_OPS), key=len)
    assert len(widest.encode("ascii")) <= MAX_CALLBACK_BYTES == 32
    parsed = parse_callback(widest)
    assert (parsed.obj_id, parsed.rev) == (MAX_OBJECT_ID, MAX_REVISION) and len(parsed.op) == 3


@pytest.mark.parametrize(
    "args",
    [("zz", 1, 1), ("RC", 1, 1), ("", 1, 1), ("rc", -1, 1), ("rc", 1, -1), ("rc", MAX_OBJECT_ID + 1, 1),
     ("rc", 1, MAX_REVISION + 1), ("rc", True, 1), ("rc", 1, False), ("rc", 1.0, 1), ("rc", "1", 1), ("rc", None, 1)],
)
def test_build_refuses_what_it_could_not_parse_back(args):
    with pytest.raises(ValueError):
        build_callback(*args)


@pytest.mark.parametrize(
    "data",
    [
        # legacy callbacks stay with their old handlers
        "setmode:open_scene", "setreg:diplomatic", "setfb:active", "tts:12", "card:abc123", "guide:2", "decode_reveal",
        # malformed
        "", "rc", "rc:1", "rc:1:", "rc::1", "rc:1:2:3", ":1:2", "rc:01:2", "rc:1:02", "rc:-1:2", "rc:1:-2", "rc:+1:2",
        "rc: 1:2", "rc:1:2 ", " rc:1:2", "rc:1:2\n", "RC:1:2", "rc:1.0:2", "rc:0x10:2", "rc:1e3:2", "rc:1_000:2",
        "xx:1:2", "r:1:2", "rcvn:1:2", "rc:١:2", "rc:1:٢", "rc;1;2", "rc:" + "9" * 13 + ":1", "rc:1:" + "9" * 10,
        "rc:" + "1" * 40,
    ],
)
def test_parse_returns_none_for_anything_that_is_not_a_v3_callback(data):
    assert parse_callback(data) is None


@pytest.mark.parametrize("data", [None, 5, 5.5, b"rc:1:2", ["rc:1:2"], object()])
def test_parse_never_raises_on_non_strings(data):
    assert parse_callback(data) is None


def test_callback_matches_needs_the_same_id_and_revision(db, clock):
    row = open_row(db, clock, St.REVIEW_ACTIVE)
    assert callback_matches(row, ParsedCallback("rvn", row.interaction_id, row.revision))
    assert not callback_matches(row, ParsedCallback("rvn", row.interaction_id + 1, row.revision))
    assert not callback_matches(row, ParsedCallback("rvn", row.interaction_id, row.revision + 1))
    assert not callback_matches(None, ParsedCallback("rvn", 1, 1))
    moved = transition(db, interaction_id=row.interaction_id, expected_revision=1, new_state=St.REVIEW_ACTIVE,
                       payload_patch={}, now=clock.now(), ttl_s=1800)
    assert not callback_matches(moved, ParsedCallback("rvn", row.interaction_id, 1))  # yesterday's button
    assert callback_matches(moved, ParsedCallback("rvn", row.interaction_id, 2))


def test_ug_is_current_only_for_the_current_revision_with_nothing_open(db, clock):
    _, revision = db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="x", idempotency_key="k1", correlation_id="c1")
    tap = ParsedCallback("ug", 17, revision)
    kwargs = dict(learner_id=LEARNER, session_id="s1", now=clock.now())
    assert ug_is_current(db, parsed=tap, **kwargs) is True
    assert ug_is_current(db, parsed=ParsedCallback("ug", 17, revision - 1), **kwargs) is False
    assert ug_is_current(db, parsed=ParsedCallback("rc", 17, revision), **kwargs) is False
    assert ug_is_current(db, parsed=tap, learner_id=LEARNER, session_id="missing", now=clock.now()) is False
    open_row(db, clock)
    assert ug_is_current(db, parsed=tap, **kwargs) is False  # something else is open: "Saved to Review"
    db.accept_scene_turn(learner_id=LEARNER, session_id="s1", text="y", idempotency_key="k2", correlation_id="c2")
    close_learner_interaction(db, LEARNER)
    assert ug_is_current(db, parsed=tap, **kwargs) is False  # a newer scene turn made the old 💡 stale
