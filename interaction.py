"""Interaction layer (S0 Contracts, sections 4.2, 5.2, 5.3, 5.4, 6, 7)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from clock import from_utc_iso, to_utc_iso
from schemas_v3 import (
    BudgetSnapshot,
    Classification,
    ComprehensionStatus,
    Delivery,
    DiagnosticV2,
    Domain,
    FindingDecision,
    InteractionObject,
    InteractionState,
    POSignal,
    PolicyDecisionV2,
    PriorityClass,
)

__all__ = [
    "ACTIVE_INTERACTION_DDL",
    "DEFAULT_TTL_S",
    "QUEUE_FLOOR",
    "VALID_CALLBACK_OPS",
    "ActiveInteraction",
    "ParsedCallback",
    "PolicyInputs",
    "TextRoute",
    "build_callback",
    "classify_priority",
    "close_interaction",
    "close_learner_interaction",
    "get_active_interaction",
    "open_interaction",
    "parse_callback",
    "priority_score",
    "resolve_interaction_policy",
    "route_text",
    "transition",
]

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 900
QUEUE_FLOOR = 25.0
P1_MEDIUM_SCORE_THRESHOLD = 55.0
MAX_CARDS_PER_SCENE = 6
STRAIN_THRESHOLD = 0.6
STRAIN_GAP_BONUS = 2
WARMUP_LEARNER_TURNS = 2
PRAGMATIC_P1_GAP = 2
P0_COOLDOWN_TURNS = 3
MIN_LEARNING_VALUE = 2
CALLBACK_MAX_BYTES = 32

_BASE_GAP: dict[str, int] = {"light": 6, "active": 3}
_VALID_PREFERENCES = frozenset({"silent", "light", "active"})
_HIGH_SEVERITIES = ("high", "critical")

ACTIVE_INTERACTION_DDL = """
CREATE TABLE IF NOT EXISTS active_interaction (
    interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL UNIQUE REFERENCES learners(learner_id),
    session_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    scene_revision_at_open INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json)),
    created_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL
);
"""

_SELECT_COLUMNS = (
    "interaction_id, learner_id, session_id, object_type, object_ref, state, "
    "revision, scene_revision_at_open, payload_json, created_at_utc, expires_at_utc"
)


def _severity_weight(severity: str) -> float:
    if severity == "low":
        return 0.33
    if severity == "medium":
        return 0.67
    if severity in _HIGH_SEVERITIES:
        return 1.0
    return 0.33


def _applicable_confidence(d: DiagnosticV2) -> float:
    ling = d.linguistic_confidence
    prag = d.pragmatic_confidence
    if ling is not None and prag is not None:
        return min(ling, prag)
    if d.domain in (Domain.LINGUISTIC, Domain.DISCOURSE):
        return ling if ling is not None else d.confidence_score
    if d.domain == Domain.PRAGMATIC:
        return prag if prag is not None else d.confidence_score
    return d.confidence_score


def priority_score(d: DiagnosticV2, prior_occurrences: int) -> float:
    sev = _severity_weight(d.severity)
    imp = float(d.communicative_impact) / 3.0
    learn = float(d.learning_value) / 3.0
    freq = min(max(prior_occurrences, 0), 4) / 4.0
    conf = _applicable_confidence(d)
    score = 100.0 * (0.30 * sev + 0.20 * imp + 0.20 * learn + 0.15 * freq + 0.15 * conf)
    return max(0.0, min(100.0, round(score, 2)))


def classify_priority(
    d: DiagnosticV2,
    score: float,
    *,
    p0_candidate: bool = False,
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK,
) -> PriorityClass:
    if comprehension_status != ComprehensionStatus.OK or (
        p0_candidate and d.severity in _HIGH_SEVERITIES
    ):
        return PriorityClass.P0
    if d.classification == Classification.ERROR and (
        d.severity in _HIGH_SEVERITIES
        or (d.severity == "medium" and score >= P1_MEDIUM_SCORE_THRESHOLD)
    ):
        return PriorityClass.P1
    return PriorityClass.P4


@dataclass(frozen=True)
class PolicyInputs:
    turn_event_id: str
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK
    p0: POSignal = field(default_factory=POSignal)
    diagnostics: Sequence[DiagnosticV2] = field(default_factory=tuple)
    prior_pattern_counts: Mapping[str, int] = field(default_factory=dict)
    turns_since_visible_card: int = 99
    cards_in_scene: int = 0
    turns_since_p0: int = 99
    strain: float = 0.0
    feedback_preference: str = "active"
    learner_turn_index: int = 3
    has_active_interaction: bool = False
    in_emotional_or_story_flow: bool = False
    curveball_turn: bool = False
    pending_clarification: str | None = None


def resolve_interaction_policy(inputs: PolicyInputs) -> PolicyDecisionV2:
    pref = (
        inputs.feedback_preference
        if inputs.feedback_preference in _VALID_PREFERENCES
        else "active"
    )
    diags = list(inputs.diagnostics)
    count = len(diags)

    scores = [
        priority_score(d, inputs.prior_pattern_counts.get(d.pattern_id, 0)) for d in diags
    ]
    classes = [
        classify_priority(
            d,
            scores[i],
            p0_candidate=inputs.p0.candidate,
            comprehension_status=inputs.comprehension_status,
        )
        for i, d in enumerate(diags)
    ]

    base_gap = _BASE_GAP.get(pref, _BASE_GAP["active"])
    required_gap = base_gap + (STRAIN_GAP_BONUS if inputs.strain >= STRAIN_THRESHOLD else 0)

    def exempt(i: int) -> bool:
        return (
            classes[i] == PriorityClass.P1
            and diags[i].severity in _HIGH_SEVERITIES
            and diags[i].domain == Domain.PRAGMATIC
        )

    def warmup_ok(i: int) -> bool:
        return inputs.learner_turn_index > WARMUP_LEARNER_TURNS or exempt(i)

    def gap_ok(i: int) -> bool:
        gap = PRAGMATIC_P1_GAP if exempt(i) else required_gap
        return inputs.turns_since_visible_card >= gap

    def blockers(i: int) -> list[str]:
        out: list[str] = []
        if pref == "silent":
            out.append("silent")
        if inputs.cards_in_scene >= MAX_CARDS_PER_SCENE:
            out.append("scene_cap")
        if inputs.in_emotional_or_story_flow:
            out.append("emotional_flow")
        if inputs.has_active_interaction:
            out.append("active_interaction")
        if not warmup_ok(i):
            out.append("warmup")
        if not gap_ok(i):
            out.append("gap")
        return out

    def best(indices: Sequence[int]) -> int | None:
        if not indices:
            return None
        return min(indices, key=lambda i: (-scores[i], i))

    delivery: list[Delivery | None] = [None] * count
    reasons: list[list[str]] = [[] for _ in range(count)]

    def defer(i: int, *why: str) -> None:
        if scores[i] < QUEUE_FLOOR:
            delivery[i] = Delivery.SUPPRESSED
            reasons[i].extend(why)
            reasons[i].append("below_queue_floor")
        else:
            delivery[i] = Delivery.DEFERRED
            reasons[i].extend(why)

    p0_idx = [i for i in range(count) if classes[i] == PriorityClass.P0]
    p1_idx = [i for i in range(count) if classes[i] == PriorityClass.P1]
    rest_idx = [
        i for i in range(count) if classes[i] not in (PriorityClass.P0, PriorityClass.P1)
    ]
    top0 = best(p0_idx)
    top1 = best(p1_idx)
    top_rest = best(rest_idx)
    selected: int | None = None

    if p0_idx:
        if inputs.comprehension_status != ComprehensionStatus.OK:
            for i in p0_idx:
                defer(i, "handled_in_reply")
        elif inputs.turns_since_p0 >= P0_COOLDOWN_TURNS:
            assert top0 is not None
            delivery[top0] = Delivery.IMMEDIATE_FOLLOWUP
            reasons[top0].append("p0_followup")
            selected = top0
            for i in p0_idx:
                if i != top0:
                    defer(i, "p0_secondary")
        else:
            for i in p0_idx:
                defer(i, "p0_cooldown")
        for i in p1_idx + rest_idx:
            defer(i, "p0_in_turn")
    else:
        if top1 is not None:
            found = blockers(top1)
            if not found:
                delivery[top1] = Delivery.END_OF_TURN
                reasons[top1].append("budget_ok")
                selected = top1
            else:
                defer(top1, *[f"blocked_{name}" for name in found])
            for i in p1_idx:
                if i != top1:
                    defer(i, "superseded_p1")

    def no_higher_priority(i: int) -> bool:
        cls = classes[i]
        if cls == PriorityClass.P0:
            return i == top0
        if p0_idx:
            return False
        if cls == PriorityClass.P1:
            return i == top1
        return selected is None and i == top_rest

    gate_map: list[dict[str, bool]] = [
        {
            "scene_stability": (
                not inputs.has_active_interaction and not inputs.curveball_turn
            ),
            "timing": pref != "silent" and warmup_ok(i) and gap_ok(i),
            "cognitive": (
                inputs.strain < STRAIN_THRESHOLD and not inputs.in_emotional_or_story_flow
            ),
            "scope": inputs.cards_in_scene < MAX_CARDS_PER_SCENE,
            "no_higher_priority": no_higher_priority(i),
            "learning_value": diags[i].learning_value >= MIN_LEARNING_VALUE,
        }
        for i in range(count)
    ]

    if not p0_idx:
        for i in rest_idx:
            if selected is not None:
                defer(i, "superseded_by_visible_card")
            elif pref != "active":
                defer(i, "preference_not_active")
            else:
                failed = [name for name, ok in gate_map[i].items() if not ok]
                if failed:
                    defer(i, *[f"gate_{name}" for name in failed])
                else:
                    delivery[i] = Delivery.END_OF_TURN
                    reasons[i].append("all_gates_passed")
                    selected = i

    decisions = [
        FindingDecision(
            diagnostic_id=diags[i].diagnostic_id,
            priority_class=classes[i],
            priority_score=scores[i],
            delivery=delivery[i] if delivery[i] is not None else Delivery.DEFERRED,
            reasons=reasons[i],
            gates=gate_map[i],
        )
        for i in range(count)
    ]

    budget_allowed = (
        pref != "silent"
        and inputs.cards_in_scene < MAX_CARDS_PER_SCENE
        and not inputs.in_emotional_or_story_flow
        and not inputs.has_active_interaction
        and inputs.learner_turn_index > WARMUP_LEARNER_TURNS
        and inputs.turns_since_visible_card >= required_gap
    )
    budget = BudgetSnapshot(
        preference=pref,
        turns_since_visible_card=inputs.turns_since_visible_card,
        cards_in_scene=inputs.cards_in_scene,
        strain=inputs.strain,
        allowed=budget_allowed,
    )

    return PolicyDecisionV2(
        turn_event_id=inputs.turn_event_id,
        decisions=decisions,
        selected_diagnostic_id=diags[selected].diagnostic_id if selected is not None else None,
        budget=budget,
        curveball_turn=inputs.curveball_turn,
        pending_clarification=inputs.pending_clarification,
    )


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


def _utc(dt: datetime) -> datetime:
    if not isinstance(dt, datetime):
        raise TypeError(f"expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_connection(db: Any) -> tuple[sqlite3.Connection, bool]:
    if isinstance(db, sqlite3.Connection):
        return db, False
    connect = getattr(db, "_connect", None)
    if callable(connect):
        conn = connect()
        if isinstance(conn, sqlite3.Connection):
            return conn, True
        raise TypeError("db._connect() did not return a sqlite3.Connection")
    for attr in ("conn", "_conn"):
        conn_attr = getattr(db, attr, None)
        if isinstance(conn_attr, sqlite3.Connection):
            return conn_attr, False
    db_path = getattr(db, "db_path", None)
    if db_path is not None:
        return sqlite3.connect(str(db_path)), True
    raise TypeError("unsupported db handle")


@contextmanager
def _session(db: Any) -> Iterator[sqlite3.Connection]:
    conn, owned = _resolve_connection(db)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        if owned:
            conn.close()


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if own and conn.in_transaction:
            conn.rollback()
        raise
    if own and conn.in_transaction:
        conn.commit()


def _dump_payload(payload: Mapping[str, Any] | None) -> str:
    data = dict(payload) if payload is not None else {}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_row(row: sqlite3.Row) -> ActiveInteraction:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        raise ValueError("payload_json must be a JSON object")
    return ActiveInteraction(
        interaction_id=int(row["interaction_id"]),
        learner_id=int(row["learner_id"]),
        session_id=str(row["session_id"]),
        object_type=InteractionObject(row["object_type"]),
        object_ref=str(row["object_ref"]),
        state=InteractionState(row["state"]),
        revision=int(row["revision"]),
        scene_revision_at_open=int(row["scene_revision_at_open"]),
        payload=payload,
        created_at=from_utc_iso(row["created_at_utc"]),
        expires_at=from_utc_iso(row["expires_at_utc"]),
    )


def _delete_by_id(conn: sqlite3.Connection, interaction_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM active_interaction WHERE interaction_id = ?", (interaction_id,)
    )
    return cur.rowcount > 0


def open_interaction(
    db: Any,
    *,
    learner_id: int,
    session_id: str,
    object_type: InteractionObject,
    object_ref: str,
    state: InteractionState,
    payload: dict[str, Any],
    scene_revision: int,
    now: datetime,
    ttl_s: int = DEFAULT_TTL_S,
) -> ActiveInteraction:
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    obj = InteractionObject(object_type)
    st = InteractionState(state)
    now_utc = _utc(now)
    created_iso = to_utc_iso(now_utc)
    expires_iso = to_utc_iso(now_utc + timedelta(seconds=ttl_s))
    payload_json = _dump_payload(payload)

    with _session(db) as conn:
        with _txn(conn):
            conn.execute("DELETE FROM active_interaction WHERE learner_id = ?", (learner_id,))
            cur = conn.execute(
                "INSERT INTO active_interaction (learner_id, session_id, object_type, "
                "object_ref, state, revision, scene_revision_at_open, payload_json, "
                "created_at_utc, expires_at_utc) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    learner_id,
                    session_id,
                    obj.value,
                    object_ref,
                    st.value,
                    scene_revision,
                    payload_json,
                    created_iso,
                    expires_iso,
                ),
            )
            interaction_id = int(cur.lastrowid)

    return ActiveInteraction(
        interaction_id=interaction_id,
        learner_id=learner_id,
        session_id=session_id,
        object_type=obj,
        object_ref=object_ref,
        state=st,
        revision=1,
        scene_revision_at_open=scene_revision,
        payload=json.loads(payload_json),
        created_at=from_utc_iso(created_iso),
        expires_at=from_utc_iso(expires_iso),
    )


def get_active_interaction(db: Any, learner_id: int, now: datetime) -> ActiveInteraction | None:
    now_utc = _utc(now)
    with _session(db) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM active_interaction WHERE learner_id = ?",
            (learner_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            active = _parse_row(row)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("dropping corrupt active_interaction row %s: %s", row["interaction_id"], exc)
            with _txn(conn):
                _delete_by_id(conn, int(row["interaction_id"]))
            return None
        if active.expires_at <= now_utc:
            with _txn(conn):
                _delete_by_id(conn, active.interaction_id)
            return None
        return active


def transition(
    db: Any,
    *,
    interaction_id: int,
    expected_revision: int,
    new_state: InteractionState,
    payload_patch: dict[str, Any],
    now: datetime,
    ttl_s: int = DEFAULT_TTL_S,
) -> ActiveInteraction | None:
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    st = InteractionState(new_state)
    now_utc = _utc(now)
    expires_iso = to_utc_iso(now_utc + timedelta(seconds=ttl_s))

    with _session(db) as conn:
        with _txn(conn):
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM active_interaction WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                current = _parse_row(row)
            except (ValueError, TypeError, KeyError):
                _delete_by_id(conn, interaction_id)
                return None
            if current.expires_at <= now_utc:
                _delete_by_id(conn, interaction_id)
                return None
            if current.revision != expected_revision:
                return None

            merged_json = _dump_payload({**current.payload, **(payload_patch or {})})
            new_revision = expected_revision + 1
            cur = conn.execute(
                "UPDATE active_interaction SET state = ?, revision = ?, payload_json = ?, "
                "expires_at_utc = ? WHERE interaction_id = ? AND revision = ?",
                (st.value, new_revision, merged_json, expires_iso, interaction_id, expected_revision),
            )
            if cur.rowcount != 1:
                return None

    return replace(
        current,
        state=st,
        revision=new_revision,
        payload=json.loads(merged_json),
        expires_at=from_utc_iso(expires_iso),
    )


def close_interaction(
    db: Any, *, interaction_id: int, expected_revision: int | None = None
) -> bool:
    with _session(db) as conn:
        with _txn(conn):
            if expected_revision is None:
                cur = conn.execute(
                    "DELETE FROM active_interaction WHERE interaction_id = ?",
                    (interaction_id,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM active_interaction WHERE interaction_id = ? AND revision = ?",
                    (interaction_id, expected_revision),
                )
            return cur.rowcount > 0


def close_learner_interaction(db: Any, learner_id: int) -> bool:
    with _session(db) as conn:
        with _txn(conn):
            cur = conn.execute(
                "DELETE FROM active_interaction WHERE learner_id = ?", (learner_id,)
            )
            return cur.rowcount > 0


class TextRoute(StrEnum):
    SCENE_TURN = "scene_turn"
    RECONSTRUCTION_ATTEMPT = "reconstruction_attempt"
    REVIEW_ATTEMPT = "review_attempt"
    DECODE_ANSWER = "decode_answer"
    WORKOUT_ATTEMPT = "workout_attempt"


def route_text(
    db: Any, learner_id: int, now: datetime
) -> tuple[TextRoute, ActiveInteraction | None]:
    active = get_active_interaction(db, learner_id, now)
    if active is None:
        return TextRoute.SCENE_TURN, None

    state = active.state
    if state in (InteractionState.UPGRADE_VIEW, InteractionState.RECONSTRUCTION_FEEDBACK):
        close_interaction(db, interaction_id=active.interaction_id)
        return TextRoute.SCENE_TURN, None
    if state == InteractionState.RECONSTRUCTION_ACTIVE:
        return TextRoute.RECONSTRUCTION_ATTEMPT, active
    if state == InteractionState.REVIEW_ACTIVE:
        return TextRoute.REVIEW_ATTEMPT, active
    if state == InteractionState.DECODE_AWAIT:
        return TextRoute.DECODE_ANSWER, active
    if state == InteractionState.WORKOUT_ACTIVE:
        return TextRoute.WORKOUT_ATTEMPT, active

    close_interaction(db, interaction_id=active.interaction_id)
    return TextRoute.SCENE_TURN, None


VALID_CALLBACK_OPS = frozenset({"ug", "rc", "rs", "bk", "rvn", "rvh", "rvs", "ls"})


@dataclass(frozen=True)
class ParsedCallback:
    op: str
    obj_id: int
    rev: int


def parse_callback(data: str) -> ParsedCallback | None:
    try:
        if not isinstance(data, str) or not data:
            return None
        try:
            raw = data.encode("ascii")
        except UnicodeEncodeError:
            return None
        if len(raw) > CALLBACK_MAX_BYTES:
            return None
        parts = data.split(":")
        if len(parts) != 3:
            return None
        op, obj_text, rev_text = parts
        if op not in VALID_CALLBACK_OPS:
            return None
        if not (obj_text.isdigit() and rev_text.isdigit()):
            return None
        obj_id, rev = int(obj_text), int(rev_text)
        if obj_id < 0 or rev < 0:
            return None
        return ParsedCallback(op=op, obj_id=obj_id, rev=rev)
    except Exception:
        return None


def build_callback(op: str, obj_id: int, rev: int) -> str:
    if op not in VALID_CALLBACK_OPS:
        raise ValueError(f"invalid callback op: {op!r}")
    for name, value in (("obj_id", obj_id), ("rev", rev)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    text = f"{op}:{obj_id}:{rev}"
    if len(text.encode("ascii")) > CALLBACK_MAX_BYTES:
        raise ValueError(f"callback data exceeds {CALLBACK_MAX_BYTES} bytes: {text!r}")
    return text
