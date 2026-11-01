"""M003 (S0 section 4.3): projection rebuild.

Clears the three v2 projections and the two V3.0 ones, then replays the whole event log through
``database.rebuild_projections_in_tx`` (which reads every row through ``upcast``). Sets
``meta('projection_version', '2')``.

Acceptance (S0 4.3), checked here before COMMIT:

* two consecutive rebuilds produce byte-identical tables (the migration aborts if they do not);
* the v1-visible columns equal their pre-migration values. This one is reported rather than
  enforced: ``meta('m003_v1_projection_drift')`` holds the number of differing rows. It is 0 for a
  database whose projections were current; a stale projection (a process killed between a
  ``rebuild=False`` append and the final rebuild) is repaired by the replay and shows up as drift
  instead of blocking the deploy.
  ``phrase_state_projection.updated_at_utc`` is excluded from that comparison: v2 stamped the
  wall-clock time of the rebuild, V3.0 stamps the time of the phrase's last event.

The checksum covers only the SQL below, not the rebuild code, so later sprints can extend the
rebuild without invalidating this applied version.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from migrations import Migration, MigrationContext, VerificationError, table_digest

VERSION = 3
NAME = "projection_rebuild"

SQL = """
DELETE FROM phrase_state_projection;
DELETE FROM error_pattern_projection;
DELETE FROM session_state_projection;
DELETE FROM feedback_item_projection;
DELETE FROM phrase_evidence;
INSERT INTO meta (key, value) VALUES ('projection_version', '2')
  ON CONFLICT(key) DO UPDATE SET value = excluded.value;
"""

PROJECTION_TABLES = (
    "session_state_projection",
    "phrase_state_projection",
    "error_pattern_projection",
    "feedback_item_projection",
    "phrase_evidence",
)

# (table, v1 columns compared, ordering)
V1_VISIBLE = (
    (
        "session_state_projection",
        "session_id, learner_id, turns_since_visible_card, recent_questions_json, "
        "revealed_targets_json, cued_targets_json, updated_at_utc",
        "session_id",
    ),
    (
        "error_pattern_projection",
        "learner_id, normalized_pattern_id, relevant_context_family, issue_kind, "
        "distinct_eligible_occurrences, times_feedback_selected, times_feedback_shown, "
        "workout_scheduled, last_occurrence_event_id, updated_at_utc",
        "learner_id, normalized_pattern_id, relevant_context_family",
    ),
    (
        "phrase_state_projection",
        "learner_id, phrase_id, current_stage, exposure_count, recognition_count, cued_count, "
        "independent_count, spontaneous_count, distinct_valid_sessions, distinct_valid_registers, "
        "span_days, has_novel_context, recent_unretracted_failure, last_evidence_event_id",
        "learner_id, phrase_id",
    ),
)


def _v1_snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")]
        for table, columns, order in V1_VISIBLE
    }


def before(conn: sqlite3.Connection, ctx: MigrationContext) -> dict[str, list[tuple[Any, ...]]]:
    return _v1_snapshot(conn)


def run(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    from database import rebuild_projections_in_tx  # lazy: database imports this package

    rebuild_projections_in_tx(conn, learner_id=None)


def verify(conn: sqlite3.Connection, ctx: MigrationContext, snapshot: dict[str, list[tuple[Any, ...]]]) -> None:
    from database import rebuild_projections_in_tx

    first = {table: table_digest(conn, table) for table in PROJECTION_TABLES}
    rebuild_projections_in_tx(conn, learner_id=None)
    second = {table: table_digest(conn, table) for table in PROJECTION_TABLES}
    unstable = sorted(t for t in PROJECTION_TABLES if first[t] != second[t])
    if unstable:
        raise VerificationError(f"projection rebuild is not deterministic for: {unstable}")

    after = _v1_snapshot(conn)
    drift = 0
    for table in snapshot:
        old_rows, new_rows = set(snapshot[table]), set(after[table])
        drift += len(old_rows ^ new_rows)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('m003_v1_projection_drift', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(drift),),
    )


MIGRATION = Migration(
    version=VERSION,
    name=NAME,
    sql=SQL,
    fk_off=False,
    logic_tag="m003/1",
    before=before,
    run=run,
    verify=verify,
)
