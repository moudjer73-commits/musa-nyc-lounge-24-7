"""S0 4.0 / 4.1-4.3 acceptance: the migration runner and M001-M003 on a golden v2 database."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest

import database
import migrations
from clock import FakeClock
from helpers import LEARNER_A, LEARNER_B, build_golden_v2_db
from migrations import (
    ChecksumMismatchError,
    Migration,
    MigrationError,
    VerificationError,
    all_migrations,
    latest_version,
    split_sql,
    status,
    table_digest,
)

ROOT = Path(__file__).resolve().parents[1]
LOG_COLUMNS = (
    "event_seq, event_id, learner_id, session_id, event_type, occurred_at_utc, recorded_at_utc, "
    "schema_version, model_version, rubric_version, correlation_id, causation_id, idempotency_key, payload_json"
)
PROJECTIONS = (
    "session_state_projection",
    "phrase_state_projection",
    "error_pattern_projection",
    "feedback_item_projection",
    "phrase_evidence",
)
FAULT_POINTS = ("before_sql", "after_sql", "after_run", "before_commit")
NOW = "2026-10-01T09:00:00Z"


# ------------------------------------------------------------------ fixtures and helpers


@pytest.fixture(scope="module")
def golden_template(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("golden") / "template.db")
    build_golden_v2_db(path)
    return path


def copy_db(src: str, dst: str) -> str:
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)
    return dst


@pytest.fixture
def golden(golden_template, tmp_path) -> str:
    return copy_db(golden_template, str(tmp_path / "golden.db"))


def do_migrate(path: str, **kw) -> list[int]:
    kw.setdefault("clock", FakeClock(NOW))
    return migrations.migrate(path, baseline_ddl=database.BASELINE_DDL, **kw)


def log_fingerprint(path: str) -> tuple[int, int, str]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(f"SELECT {LOG_COLUMNS} FROM event_log ORDER BY event_seq").fetchall()
    return len(rows), rows[-1][0], hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def v1_columns(path: str) -> dict[str, list[str]]:
    """The columns each v2 projection has BEFORE migration: exactly the v1-visible ones."""
    out: dict[str, list[str]] = {}
    with closing(sqlite3.connect(path)) as conn:
        for table in ("session_state_projection", "phrase_state_projection", "error_pattern_projection"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if table == "phrase_state_projection":
                cols.remove("updated_at_utc")  # v2 stamped the wall clock of the rebuild; V3 stamps the last event time
                cols.remove("next_review_at_utc")  # a v2 column that v2 never wrote; the V3 scheduler fills it (S3). M003's own V1_VISIBLE list never included it
            out[table] = cols
    return out


def v1_projection_rows(path: str, columns: dict[str, list[str]]) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            table: [tuple(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}")]
            for table, cols in columns.items()
        }


def schema_digest(path: str) -> str:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        parts = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")]
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            parts.append((table, table_digest(conn, table)))
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def projection_digests(path: str) -> dict[str, str]:
    with closing(migrations.connect(path)) as conn:
        return {t: table_digest(conn, t) for t in PROJECTIONS}


def applied(path: str) -> list[int]:
    with closing(migrations.connect(path)) as conn:
        return sorted(migrations.applied_versions(conn))


def projection_rows(path: str) -> dict[str, list[tuple]]:
    """Content of every projection, independent of physical row order (a per-learner rebuild
    re-inserts that learner's rows at the end of the table)."""
    with closing(migrations.connect(path)) as conn:
        return {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in PROJECTIONS}


def has_table(path: str, name: str) -> bool:
    with closing(migrations.connect(path)) as conn:
        return migrations.table_exists(conn, name)


class Boom(RuntimeError):
    pass


def boom_at(point: str):
    def hook(name: str) -> None:
        if name == point:
            raise Boom(point)

    return hook


# ------------------------------------------------------------------ golden migration (S0 4.1, 4.3)


def test_golden_db_has_all_14_types_and_is_baseline_version_0(golden):
    report = status(golden)
    assert report["has_event_log"] is True and report["current_version"] == 0
    assert report["pending"] == [1, 2, 3] and report["latest_version"] == 3
    assert has_table(golden, "schema_migrations") is False  # status is read-only


def test_event_log_survives_migration_byte_for_byte(golden):
    before = log_fingerprint(golden)
    assert before[0] > 20
    assert do_migrate(golden) == [1, 2, 3]
    assert log_fingerprint(golden) == before
    assert applied(golden) == [1, 2, 3]


def test_append_only_triggers_still_raise_after_migration(golden):
    do_migrate(golden)
    with closing(migrations.connect(golden)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE event_log SET payload_json='{}' WHERE event_seq=1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM event_log WHERE event_seq=1")


def test_new_events_continue_the_sequence_and_unknown_types_are_refused(golden):
    last_seq = log_fingerprint(golden)[1]
    do_migrate(golden)
    db = database.EventSourcedDatabase(golden, clock=FakeClock(NOW))
    result = db.append_event_ex(
        learner_id=LEARNER_A, session_id="g_s1", event_type="analysis_skipped",
        payload={"turn_event_id": "ev_x", "reason": "trivial", "detail": None},
        idempotency_key="after-migration", correlation_id="turn_new",
    )
    assert result.inserted and result.event_seq == last_seq + 1
    with closing(migrations.connect(golden)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_types").fetchone()[0] == 17
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO event_log (event_id, learner_id, session_id, event_type, occurred_at_utc, recorded_at_utc, "
                "schema_version, correlation_id, idempotency_key, payload_json) "
                "VALUES ('ev_bad', 1001, 'g_s1', 'not_a_type', 'x', 'x', '2.0', 'c', 'k', '{}')"
            )


def test_v1_visible_projections_are_identical_after_replay(golden):
    columns = v1_columns(golden)
    before = v1_projection_rows(golden, columns)
    assert all(before[t] for t in before)
    do_migrate(golden)
    assert v1_projection_rows(golden, columns) == before
    with closing(migrations.connect(golden)) as conn:
        assert dict(conn.execute("SELECT key, value FROM meta").fetchall()) == {
            "projection_version": "2",
            "m003_v1_projection_drift": "0",
        }


def test_stale_v2_projection_is_repaired_and_reported_as_drift(golden):
    columns = v1_columns(golden)
    correct = v1_projection_rows(golden, columns)
    with closing(sqlite3.connect(golden)) as conn:
        conn.execute("UPDATE session_state_projection SET turns_since_visible_card = 99 WHERE session_id='g_s1'")
        conn.commit()
    do_migrate(golden)
    assert v1_projection_rows(golden, columns) == correct
    with closing(migrations.connect(golden)) as conn:
        assert int(conn.execute("SELECT value FROM meta WHERE key='m003_v1_projection_drift'").fetchone()[0]) >= 1


def test_new_projections_are_populated_from_the_log(golden):
    do_migrate(golden)
    with closing(migrations.connect(golden)) as conn:
        items = conn.execute("SELECT * FROM feedback_item_projection").fetchall()
        evidence = conn.execute("SELECT * FROM phrase_evidence").fetchall()
        assert items and evidence
        statuses = {r["status"] for r in items}
        assert statuses <= {"pending", "delivered", "retracted", "merged"} and "retracted" in statuses
        assert {r["learner_id"] for r in evidence} == {LEARNER_A}
        # one retracted independent_production = its two legacy rows (production + independence), same source event
        retracted = conn.execute("SELECT source_event_id, dimension FROM phrase_evidence WHERE retracted = 1 ORDER BY dimension").fetchall()
        assert [(r["source_event_id"], r["dimension"]) for r in retracted] == [
            ("ev_g_assessbad1", "independence"),
            ("ev_g_assessbad1", "production"),
        ]
        # the backfill never turns history into notifications
        assert not conn.execute("SELECT 1 FROM feedback_item_projection WHERE status = 'pending' AND delivery NOT IN ('deferred', 'suppressed')").fetchall()


def test_learner_settings_are_backfilled_one_row_per_learner(golden):
    do_migrate(golden)
    with closing(migrations.connect(golden)) as conn:
        rows = conn.execute("SELECT learner_id, auto_voice, updated_at_utc FROM learner_settings ORDER BY learner_id").fetchall()
    assert [(r["learner_id"], r["auto_voice"]) for r in rows] == [(LEARNER_A, 0), (LEARNER_B, 0)]
    assert all(r["updated_at_utc"].startswith("2026-10-01T09:00:00") for r in rows)


def test_second_run_changes_nothing(golden):
    do_migrate(golden)
    digest = schema_digest(golden)
    assert do_migrate(golden) == []
    assert schema_digest(golden) == digest


def test_rebuilds_are_byte_identical_and_independent_of_the_machine(golden_template, tmp_path):
    one = copy_db(golden_template, str(tmp_path / "one.db"))
    two = copy_db(golden_template, str(tmp_path / "two.db"))
    do_migrate(one)
    do_migrate(two)
    assert projection_digests(one) == projection_digests(two)
    db = database.EventSourcedDatabase(one, clock=FakeClock(NOW))
    db.rebuild_all_projections()
    db.rebuild_all_projections()
    assert projection_digests(one) == projection_digests(two)


def test_rebuilding_one_learner_equals_rebuilding_everyone(golden):
    do_migrate(golden)
    expected = projection_rows(golden)
    assert all(expected[t] for t in ("session_state_projection", "phrase_state_projection", "feedback_item_projection"))
    db = database.EventSourcedDatabase(golden, clock=FakeClock(NOW))
    for learner in (LEARNER_A, LEARNER_B):
        with db.transaction() as conn:
            database.rebuild_projections_in_tx(conn, learner_id=learner)
        assert projection_rows(golden) == expected
    # rebuilding learner B must not have touched learner A's rows, and vice versa
    with db.transaction() as conn:
        conn.execute("UPDATE session_state_projection SET turns_since_visible_card = 77 WHERE learner_id = ?", (LEARNER_A,))
        database.rebuild_projections_in_tx(conn, learner_id=LEARNER_B)
    with closing(migrations.connect(golden)) as conn:
        assert {r[0] for r in conn.execute("SELECT turns_since_visible_card FROM session_state_projection WHERE learner_id = ?", (LEARNER_A,))} == {77}


def test_baseline_ddl_is_idempotent_on_a_v3_database(golden):
    do_migrate(golden)
    digest = schema_digest(golden)
    with closing(sqlite3.connect(golden)) as conn:
        conn.executescript(database.BASELINE_DDL)
    assert schema_digest(golden) == digest


# ------------------------------------------------------------------ fresh databases and the runner


def test_fresh_database_reaches_latest(tmp_path):
    path = str(tmp_path / "fresh.db")
    assert do_migrate(path) == [1, 2, 3]
    report = status(path)
    assert report["current_version"] == report["latest_version"] == latest_version() == 3
    assert report["pending"] == [] and report["checksum_ok"] is True and report["ahead_of_code"] == []
    with closing(migrations.connect(path)) as conn:
        referenced = {r["table"] for r in conn.execute("PRAGMA foreign_key_list(event_log)")}
        assert "event_types" in referenced
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table in ("phrase_evidence", "feedback_item_projection", "active_interaction", "learner_settings",
                      "audio_cache", "competency_subskills", "meta", "event_types", "schema_migrations"):
            assert migrations.table_exists(conn, table), table


def test_constructing_the_database_class_migrates_and_a_second_instance_is_a_noop(golden):
    first = database.EventSourcedDatabase(golden, clock=FakeClock(NOW))
    assert applied(golden) == [1, 2, 3]
    second = database.EventSourcedDatabase(golden, clock=FakeClock(NOW))
    assert first.migrate() == [] and second.migrate() == []


def test_auto_migrate_false_leaves_the_file_alone(golden):
    digest = schema_digest(golden)
    database.EventSourcedDatabase(golden, clock=FakeClock(NOW), auto_migrate=False)
    assert schema_digest(golden) == digest


def test_target_applies_a_prefix(golden):
    assert do_migrate(golden, target=1) == [1]
    assert do_migrate(golden, target=2) == [2]
    assert do_migrate(golden) == [3]


def test_migration_list_is_contiguous_and_checksums_are_stable():
    migs = all_migrations()
    assert [m.version for m in migs] == [1, 2, 3]
    assert [m.name for m in migs] == ["event_types_and_event_log_rebuild", "v3_tables_and_columns", "projection_rebuild"]
    assert len({m.checksum for m in migs}) == 3
    assert all(m.checksum == dataclasses.replace(m).checksum for m in migs)


def test_split_sql_keeps_trigger_bodies_whole():
    script = """
    CREATE TABLE a (x INTEGER);
    CREATE TRIGGER t BEFORE UPDATE ON a BEGIN
        SELECT RAISE(ABORT, 'append-only; no updates');
    END;
    -- a comment between statements
    INSERT INTO a VALUES (1);
    """
    statements = split_sql(script)
    assert len(statements) == 3
    assert statements[1].startswith("CREATE TRIGGER") and statements[1].rstrip().endswith("END;")
    with pytest.raises(ValueError):
        split_sql("CREATE TABLE broken (")


def test_checksum_ignores_whitespace_but_not_content():
    base = Migration(version=9, name="x", sql="CREATE TABLE a (x INTEGER);\nINSERT INTO a VALUES (1);")
    spaced = dataclasses.replace(base, sql="CREATE TABLE a (x INTEGER);   \r\nINSERT INTO a VALUES (1);\r\n")
    changed = dataclasses.replace(base, sql="CREATE TABLE a (x INTEGER);\nINSERT INTO a VALUES (2);")
    assert base.checksum == spaced.checksum != changed.checksum


# ------------------------------------------------------------------ failure modes


def test_tampered_checksum_is_a_hard_error(golden):
    do_migrate(golden)
    with closing(sqlite3.connect(golden)) as conn:
        conn.execute("UPDATE schema_migrations SET checksum = 'deadbeef' WHERE version = 2")
        conn.commit()
    with pytest.raises(ChecksumMismatchError, match="immutable"):
        do_migrate(golden)
    assert status(golden)["checksum_ok"] is False
    with pytest.raises(ChecksumMismatchError):
        database.EventSourcedDatabase(golden, clock=FakeClock(NOW))


def test_database_ahead_of_the_code_is_refused(golden):
    do_migrate(golden)
    with closing(sqlite3.connect(golden)) as conn:
        conn.execute("INSERT INTO schema_migrations (version, name, applied_at_utc, checksum) VALUES (99, 'future', 'x', 'y')")
        conn.commit()
    before = schema_digest(golden)
    with pytest.raises(MigrationError, match="refusing to run old code"):
        do_migrate(golden)
    assert status(golden)["ahead_of_code"] == [99]
    assert schema_digest(golden) == before


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("point", FAULT_POINTS)
def test_an_exception_at_any_point_rolls_back_only_that_migration(golden, version, point):
    pristine = log_fingerprint(golden)
    with pytest.raises(MigrationError, match="rolled back"):
        do_migrate(golden, fault_hook=boom_at(f"{point}:{version}"))
    # everything before `version` committed, nothing of `version` is visible
    assert applied(golden) == list(range(1, version))
    assert log_fingerprint(golden) == pristine
    with closing(migrations.connect(golden)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not migrations.table_exists(conn, "event_types") or version > 1
        assert not migrations.table_exists(conn, "active_interaction") or version > 2
        assert not migrations.table_exists(conn, "meta") or version > 2
    # and the next run finishes the job and ends where an uninterrupted run ends
    assert do_migrate(golden) == list(range(version, 4))
    assert applied(golden) == [1, 2, 3]
    assert log_fingerprint(golden) == pristine


def test_interrupted_run_equals_uninterrupted_run(golden_template, tmp_path):
    clean = copy_db(golden_template, str(tmp_path / "clean.db"))
    flaky = copy_db(golden_template, str(tmp_path / "flaky.db"))
    do_migrate(clean)
    for point in ("after_sql:1", "before_commit:2", "after_run:3"):
        with pytest.raises(MigrationError):
            do_migrate(flaky, fault_hook=boom_at(point))
    do_migrate(flaky)
    assert projection_digests(flaky) == projection_digests(clean)
    assert log_fingerprint(flaky) == log_fingerprint(clean)
    with closing(migrations.connect(flaky)) as a, closing(migrations.connect(clean)) as b:
        sql = "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        assert [tuple(r) for r in a.execute(sql)] == [tuple(r) for r in b.execute(sql)]


@pytest.mark.parametrize("point", ["after_sql:1", "before_commit:1", "after_run:2", "before_commit:3"])
def test_killing_the_process_mid_migration_leaves_a_consistent_database(golden, tmp_path, point):
    pristine = log_fingerprint(golden)
    script = tmp_path / "kill_me.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, sys
            sys.path.insert(0, {str(ROOT)!r})
            import database, migrations

            def hook(name):
                if name == sys.argv[2]:
                    os._exit(9)   # no cleanup, no rollback, no atexit: a power cut

            migrations.migrate(sys.argv[1], baseline_ddl=database.BASELINE_DDL, fault_hook=hook)
            """
        )
    )
    done = subprocess.run([sys.executable, str(script), golden, point], cwd=str(ROOT), env=os.environ.copy(), timeout=120)
    assert done.returncode == 9
    version = int(point.split(":")[1])
    assert applied(golden) == list(range(1, version))
    assert log_fingerprint(golden) == pristine
    with closing(sqlite3.connect(golden)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert do_migrate(golden) == list(range(version, 4))
    assert log_fingerprint(golden) == pristine


def patched_migrations(monkeypatch, version: int, **changes) -> None:
    original = migrations.all_migrations
    monkeypatch.setattr(
        migrations,
        "all_migrations",
        lambda: [dataclasses.replace(m, **changes) if m.version == version else m for m in original()],
    )


def test_a_failed_verification_rolls_the_migration_back(golden, monkeypatch):
    def refuse(conn, ctx, snapshot):
        raise VerificationError("post-condition not met")

    patched_migrations(monkeypatch, 2, verify=refuse)
    with pytest.raises(VerificationError, match="post-condition"):
        do_migrate(golden)
    assert applied(golden) == [1]
    for table in ("active_interaction", "learner_settings", "phrase_evidence", "meta"):
        assert not has_table(golden, table), table
    with closing(migrations.connect(golden)) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert not {"scene_revision", "pending_clarification_json"} & columns


def test_a_foreign_key_violation_blocks_the_commit(golden, monkeypatch):
    real_run = all_migrations()[1].run

    def plant_orphan(conn, ctx):
        real_run(conn, ctx)  # keep M002's own post-conditions satisfied; only the orphan is wrong
        conn.execute(
            "INSERT INTO event_log (event_id, learner_id, session_id, event_type, occurred_at_utc, recorded_at_utc, "
            "schema_version, correlation_id, idempotency_key, payload_json) "
            "VALUES ('ev_orphan', 1001, 'g_s1', 'ghost_type', 'x', 'x', '2.0', 'c', 'orphan', '{}')"
        )

    patched_migrations(monkeypatch, 2, fk_off=True, run=plant_orphan)
    with pytest.raises(VerificationError, match="foreign_key_check"):
        do_migrate(golden)
    assert applied(golden) == [1]
    with closing(migrations.connect(golden)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id='ev_orphan'").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_m001_refuses_a_log_it_cannot_copy_faithfully(golden, monkeypatch):
    """If the copy changed one byte of one payload, the fingerprint check aborts the migration."""
    from migrations import m001_event_types_and_event_log as m001

    real_run = m001.MIGRATION.run

    def corrupt(conn, ctx):
        if real_run:
            real_run(conn, ctx)
        conn.execute("DROP TRIGGER IF EXISTS event_log_no_update")
        conn.execute("UPDATE event_log SET payload_json = payload_json || ' ' WHERE event_seq = 1")

    patched_migrations(monkeypatch, 1, run=corrupt)
    pristine = log_fingerprint(golden)
    with pytest.raises(MigrationError):
        do_migrate(golden)
    assert applied(golden) == []
    assert log_fingerprint(golden) == pristine


def test_foreign_keys_are_back_on_after_a_successful_m001(golden):
    do_migrate(golden)
    with closing(migrations.connect(golden)) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
