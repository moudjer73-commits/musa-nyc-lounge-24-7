"""Durable interaction state and the callback wire format (S0 sections 5.4 and 7). Owner: Claude.

S1 scope: everything that must survive a process restart. ``active_interaction`` holds at most one
row per learner (``UNIQUE(learner_id)``); the absence of a row is LIVE_SCENE. The row is
operational state (mutable, with a TTL), not evidence: outcomes are written as events elsewhere.

* TTL expiry is lazy: an expired row is deleted the first time anything reads it. Time always comes
  from the caller (an injected ``Clock``), never from the wall clock, so a test can travel past a TTL.
  A row is valid while ``now < expires_at``.
* ``interaction_revision`` (the row's ``revision``) is bumped on every transition; the generic
  ``session_revision`` of a scene lives on ``sessions`` and is not touched here (A13).
* ``open_interaction`` replaces any existing row and issues a new ``interaction_id``
  (AUTOINCREMENT: ids are never reused, so a stale button can never address a newer row).
* The policy resolver (``resolve_interaction_policy``, S0 5.3) and the Telegram handlers are
  separate work (S2 and S4); this module only stores state and routes text.

Callback wire format: ``<op>:<id>:<rev>``, ASCII base-10 integers, at most 32 bytes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Mapping, Optional

from clock import ensure_utc, parse_iso, to_iso
from schemas import InteractionObject, InteractionState

if TYPE_CHECKING:  # pragma: no cover
    from database import EventSourcedDatabase

# ------------------------------------------------------------------ constants

STALE_CALLBACK_TEXT = "That option expired."
SAVED_TO_REVIEW_TEXT = "Saved to Review"

# TTLs from S0 5.4 (seconds).
DEFAULT_TTL_S: dict[InteractionState, int] = {
    InteractionState.UPGRADE_VIEW: 15 * 60,
    InteractionState.RECONSTRUCTION_ACTIVE: 15 * 60,
    InteractionState.RECONSTRUCTION_FEEDBACK: 15 * 60,
    InteractionState.REVIEW_ACTIVE: 30 * 60,  # per item
    InteractionState.DECODE_AWAIT: 30 * 60,
    InteractionState.WORKOUT_ACTIVE: 30 * 60,
}

# Which object a state belongs to.
STATE_OBJECT: dict[InteractionState, InteractionObject] = {
    InteractionState.UPGRADE_VIEW: InteractionObject.UPGRADE,
    InteractionState.RECONSTRUCTION_ACTIVE: InteractionObject.RECONSTRUCTION,
    InteractionState.RECONSTRUCTION_FEEDBACK: InteractionObject.RECONSTRUCTION,
    InteractionState.REVIEW_ACTIVE: InteractionObject.REVIEW_ITEM,
    InteractionState.DECODE_AWAIT: InteractionObject.DECODE,
    InteractionState.WORKOUT_ACTIVE: InteractionObject.WORKOUT,
}

# Moves a live row may make through ``transition`` (same row, same interaction_id, revision + 1).
# Everything else is either "close the row" (any state) or "open a new row" (``open_interaction``):
# S0 5.4 says the ``rc`` button on UPGRADE_VIEW starts RECONSTRUCTION_ACTIVE as a *new row with a new
# interaction_id*, so UPGRADE_VIEW has no in-place move.
ALLOWED_TRANSITIONS: dict[InteractionState, frozenset[InteractionState]] = {
    InteractionState.UPGRADE_VIEW: frozenset(),
    InteractionState.RECONSTRUCTION_ACTIVE: frozenset({InteractionState.RECONSTRUCTION_FEEDBACK}),
    InteractionState.RECONSTRUCTION_FEEDBACK: frozenset(),
    InteractionState.REVIEW_ACTIVE: frozenset({InteractionState.REVIEW_ACTIVE}),
    InteractionState.DECODE_AWAIT: frozenset({InteractionState.DECODE_AWAIT}),
    InteractionState.WORKOUT_ACTIVE: frozenset({InteractionState.WORKOUT_ACTIVE}),
}


class IllegalTransition(ValueError):
    """The requested state change is not in S0 5.4."""


@dataclass(frozen=True)
class ActiveInteraction:
    interaction_id: int
    learner_id: int
    session_id: str
    object_type: InteractionObject
    object_ref: str
    state: InteractionState
    revision: int
    scene_revision_at_open: int
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return ensure_utc(now) >= self.expires_at


def default_ttl_s(state: InteractionState) -> int:
    return DEFAULT_TTL_S[InteractionState(state)]


# ------------------------------------------------------------------ persistence


def _from_row(row: Any) -> ActiveInteraction:
    return ActiveInteraction(
        interaction_id=int(row["interaction_id"]),
        learner_id=int(row["learner_id"]),
        session_id=str(row["session_id"]),
        object_type=InteractionObject(row["object_type"]),
        object_ref=str(row["object_ref"]),
        state=InteractionState(row["state"]),
        revision=int(row["revision"]),
        scene_revision_at_open=int(row["scene_revision_at_open"]),
        payload=json.loads(row["payload_json"]),
        created_at=parse_iso(row["created_at_utc"]),
        expires_at=parse_iso(row["expires_at_utc"]),
    )


def _dump_payload(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise TypeError("interaction payload must be a mapping")
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)


def _check_ttl(ttl_s: float) -> None:
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) or ttl_s <= 0:
        raise ValueError("ttl_s must be a positive number of seconds")


def open_interaction(
    db: "EventSourcedDatabase",
    *,
    learner_id: int,
    session_id: str,
    object_type: InteractionObject,
    object_ref: str,
    state: InteractionState,
    payload: Mapping[str, Any],
    scene_revision: int,
    now: datetime,
    ttl_s: float,
) -> ActiveInteraction:
    """Replace any existing row of the learner with a new one (new ``interaction_id``, revision 1)."""
    state = InteractionState(state)
    object_type = InteractionObject(object_type)
    if STATE_OBJECT[state] != object_type:
        raise ValueError(f"state {state.value} belongs to object {STATE_OBJECT[state].value}, not {object_type.value}")
    _check_ttl(ttl_s)
    now = ensure_utc(now)
    body = _dump_payload(payload)
    with db.transaction() as conn:
        conn.execute("DELETE FROM active_interaction WHERE learner_id = ?", (learner_id,))
        cursor = conn.execute(
            """
            INSERT INTO active_interaction (
                learner_id, session_id, object_type, object_ref, state, revision,
                scene_revision_at_open, payload_json, created_at_utc, expires_at_utc
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                learner_id,
                session_id,
                object_type.value,
                str(object_ref),
                state.value,
                int(scene_revision),
                body,
                to_iso(now),
                to_iso(now + timedelta(seconds=ttl_s)),
            ),
        )
        row = conn.execute("SELECT * FROM active_interaction WHERE interaction_id = ?", (cursor.lastrowid,)).fetchone()
    return _from_row(row)


def get_active_interaction(db: "EventSourcedDatabase", learner_id: int, now: datetime) -> Optional[ActiveInteraction]:
    """The learner's live row, or ``None`` (LIVE_SCENE). An expired row is deleted here (lazy TTL)."""
    now_iso = to_iso(ensure_utc(now))
    with db.read() as conn:
        row = conn.execute("SELECT * FROM active_interaction WHERE learner_id = ?", (learner_id,)).fetchone()
    if row is None:
        return None
    if row["expires_at_utc"] > now_iso:
        return _from_row(row)
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM active_interaction WHERE interaction_id = ? AND expires_at_utc <= ?",
            (row["interaction_id"], now_iso),
        )
    return None


def transition(
    db: "EventSourcedDatabase",
    *,
    interaction_id: int,
    expected_revision: int,
    new_state: InteractionState,
    payload_patch: Mapping[str, Any],
    now: datetime,
    ttl_s: float,
) -> Optional[ActiveInteraction]:
    """Compare-and-set on the row's revision. On success the state changes, ``payload_patch`` is
    merged shallowly into the payload, the revision is bumped and the TTL restarts from ``now``.

    Returns ``None`` (nothing changed) when the row no longer exists, has expired, or its
    revision is not ``expected_revision``. Raises ``IllegalTransition`` for a move S0 5.4 does not allow.
    """
    new_state = InteractionState(new_state)
    _check_ttl(ttl_s)
    now = ensure_utc(now)
    now_iso = to_iso(now)
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM active_interaction WHERE interaction_id = ?", (interaction_id,)).fetchone()
        if row is None:
            return None
        if row["expires_at_utc"] <= now_iso:
            conn.execute("DELETE FROM active_interaction WHERE interaction_id = ?", (interaction_id,))
            return None
        if int(row["revision"]) != int(expected_revision):
            return None
        current = InteractionState(row["state"])
        if new_state not in ALLOWED_TRANSITIONS[current]:
            raise IllegalTransition(f"{current.value} -> {new_state.value} is not an allowed transition")
        payload = json.loads(row["payload_json"])
        payload.update(dict(payload_patch))
        conn.execute(
            """
            UPDATE active_interaction
            SET state = ?, payload_json = ?, revision = revision + 1, expires_at_utc = ?
            WHERE interaction_id = ? AND revision = ?
            """,
            (
                new_state.value,
                _dump_payload(payload),
                to_iso(now + timedelta(seconds=ttl_s)),
                interaction_id,
                int(expected_revision),
            ),
        )
        updated = conn.execute("SELECT * FROM active_interaction WHERE interaction_id = ?", (interaction_id,)).fetchone()
    return _from_row(updated)


def close_interaction(
    db: "EventSourcedDatabase", *, interaction_id: int, expected_revision: Optional[int] = None
) -> bool:
    """Delete the row. With ``expected_revision`` it only closes that revision. No outcome event is
    written here. Returns whether a row was removed."""
    with db.transaction() as conn:
        if expected_revision is None:
            cursor = conn.execute("DELETE FROM active_interaction WHERE interaction_id = ?", (interaction_id,))
        else:
            cursor = conn.execute(
                "DELETE FROM active_interaction WHERE interaction_id = ? AND revision = ?",
                (interaction_id, int(expected_revision)),
            )
        return cursor.rowcount > 0


def close_learner_interaction(db: "EventSourcedDatabase", learner_id: int) -> bool:
    """Close whatever the learner has open: a keyboard mode button, ``/start`` or the Session
    button does this first, with no outcome event (S0 5.4 global rules)."""
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM active_interaction WHERE learner_id = ?", (learner_id,))
        return cursor.rowcount > 0


# ------------------------------------------------------------------ routing of free text


class TextRoute(StrEnum):
    SCENE_TURN = "scene_turn"
    RECONSTRUCTION_ATTEMPT = "reconstruction_attempt"
    REVIEW_ATTEMPT = "review_attempt"
    DECODE_ANSWER = "decode_answer"
    WORKOUT_ATTEMPT = "workout_attempt"


_TEXT_ROUTES: dict[InteractionState, TextRoute] = {
    InteractionState.RECONSTRUCTION_ACTIVE: TextRoute.RECONSTRUCTION_ATTEMPT,
    InteractionState.REVIEW_ACTIVE: TextRoute.REVIEW_ATTEMPT,
    InteractionState.DECODE_AWAIT: TextRoute.DECODE_ANSWER,
    InteractionState.WORKOUT_ACTIVE: TextRoute.WORKOUT_ATTEMPT,
}


def route_text(
    db: "EventSourcedDatabase", learner_id: int, now: datetime
) -> tuple[TextRoute, Optional[ActiveInteraction]]:
    """Where a free-text message goes. In UPGRADE_VIEW and RECONSTRUCTION_FEEDBACK the row is closed
    (side effect) and the text is an ordinary scene turn; in the other states the open row comes back
    with the route that consumes it."""
    row = get_active_interaction(db, learner_id, now)
    if row is None:
        return TextRoute.SCENE_TURN, None
    route = _TEXT_ROUTES.get(row.state)
    if route is None:
        close_interaction(db, interaction_id=row.interaction_id, expected_revision=row.revision)
        return TextRoute.SCENE_TURN, None
    return route, row


# ------------------------------------------------------------------ callback wire format (S0 7)

CALLBACK_OPS = frozenset({"ug", "rc", "rs", "bk", "rvn", "rvh", "rvs", "ls"})
MAX_CALLBACK_BYTES = 32  # Telegram allows 64
MAX_OBJECT_ID = 10**12 - 1  # 12 digits: the same width as the snapshot sequence numbers
MAX_REVISION = 10**9 - 1

_CALLBACK_RE = re.compile(r"([a-z]{2,3}):(0|[1-9][0-9]{0,11}):(0|[1-9][0-9]{0,8})")


@dataclass(frozen=True)
class ParsedCallback:
    op: str
    obj_id: int
    rev: int


def build_callback(op: str, obj_id: int, rev: int) -> str:
    """``<op>:<id>:<rev>``. Raises ``ValueError`` for an unknown op, a negative or oversized number,
    or a result longer than 32 bytes."""
    if op not in CALLBACK_OPS:
        raise ValueError(f"unknown callback op {op!r}")
    for name, value, limit in (("obj_id", obj_id, MAX_OBJECT_ID), ("rev", rev, MAX_REVISION)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
            raise ValueError(f"{name} must be an integer between 0 and {limit}, got {value!r}")
    data = f"{op}:{obj_id}:{rev}"
    if len(data.encode("ascii")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback data longer than {MAX_CALLBACK_BYTES} bytes: {data!r}")
    return data


def parse_callback(data: Any) -> Optional[ParsedCallback]:
    """Parse V3.0 callback data. Anything else (legacy ``set*``, ``tts:``, ``card:``, ``guide:``,
    ``decode_reveal``, malformed or unknown ops, non-ASCII digits, signs, spaces, leading zeros)
    returns ``None``; the caller answers ``STALE_CALLBACK_TEXT`` and never raises."""
    if not isinstance(data, str) or len(data) > MAX_CALLBACK_BYTES:
        return None
    match = _CALLBACK_RE.fullmatch(data)
    if match is None or match.group(1) not in CALLBACK_OPS:
        return None
    return ParsedCallback(op=match.group(1), obj_id=int(match.group(2)), rev=int(match.group(3)))


def callback_matches(interaction: Optional[ActiveInteraction], parsed: ParsedCallback) -> bool:
    """True when a button addresses the live row at its current revision (rc, rs, bk, rvn, rvh, rvs)."""
    return interaction is not None and interaction.interaction_id == parsed.obj_id and interaction.revision == parsed.rev


def ug_is_current(
    db: "EventSourcedDatabase", *, learner_id: int, session_id: str, parsed: ParsedCallback, now: datetime
) -> bool:
    """S0 7: a ``ug`` tap opens the card only while its revision is the session's current
    ``session_revision`` and nothing else is open. ``False`` means the caller answers
    ``SAVED_TO_REVIEW_TEXT`` (the finding is already queued), not ``STALE_CALLBACK_TEXT``."""
    if parsed.op != "ug":
        return False
    if get_active_interaction(db, learner_id, now) is not None:
        return False
    try:
        return db.get_session_revision(session_id) == parsed.rev
    except LookupError:
        return False
