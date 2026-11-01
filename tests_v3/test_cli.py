"""``python -m database migrate|status`` as the deploy workflow uses it."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import database
from helpers import build_golden_v2_db

ROOT = Path(__file__).resolve().parents[1]


def run_main(capsys, *argv: str) -> tuple[int, dict | None, str]:
    code = database.main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else None), captured.err


@pytest.fixture
def golden(tmp_path) -> str:
    path = str(tmp_path / "golden.db")
    build_golden_v2_db(path)
    return path


def test_status_of_a_missing_file_is_read_only_and_reports_everything_pending(capsys, tmp_path):
    path = str(tmp_path / "nothing.db")
    code, report, _ = run_main(capsys, "status", "--db-path", path)
    assert code == 0 and report["db_exists"] is False and report["pending"] == [1, 2, 3] and report["current_version"] == 0
    assert run_main(capsys, "status", "--check", "--db-path", path)[0] == 10
    assert not os.path.exists(path)


def test_status_of_a_v2_database_is_baseline_zero_and_changes_nothing(capsys, golden):
    with closing(sqlite3.connect(golden)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
    code, report, _ = run_main(capsys, "status", "--db-path", golden)
    assert code == 0
    assert (report["has_event_log"], report["current_version"], report["pending"], report["checksum_ok"]) == (True, 0, [1, 2, 3], True)
    assert run_main(capsys, "status", "--check", "--db-path", golden)[0] == 10
    with closing(sqlite3.connect(golden)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == before


def test_migrate_then_status_check_is_clean(capsys, golden):
    code, report, _ = run_main(capsys, "migrate", "--db-path", golden)
    assert code == 0 and report["applied_now"] == [1, 2, 3] and report["current_version"] == 3 and report["pending"] == []
    code, report, _ = run_main(capsys, "status", "--check", "--db-path", golden)
    assert code == 0 and report["pending"] == []
    code, report, _ = run_main(capsys, "migrate", "--db-path", golden)
    assert code == 0 and report["applied_now"] == []


def test_migrate_creates_a_fresh_database(capsys, tmp_path):
    path = str(tmp_path / "fresh.db")
    code, report, _ = run_main(capsys, "migrate", "--db-path", path)
    assert code == 0 and report["applied_now"] == [1, 2, 3] and os.path.exists(path)


def test_a_tampered_checksum_exits_1_with_the_reason_on_stderr(capsys, golden):
    run_main(capsys, "migrate", "--db-path", golden)
    with closing(sqlite3.connect(golden)) as conn:
        conn.execute("UPDATE schema_migrations SET checksum = 'x' WHERE version = 1")
        conn.commit()
    code, report, err = run_main(capsys, "migrate", "--db-path", golden)
    assert code == 1 and report is None and "ChecksumMismatchError" in err
    code, report, _ = run_main(capsys, "status", "--check", "--db-path", golden)
    assert code == 0 and report["checksum_ok"] is False  # status reports; migrate refuses


def test_a_database_ahead_of_the_code_exits_1(capsys, golden):
    run_main(capsys, "migrate", "--db-path", golden)
    with closing(sqlite3.connect(golden)) as conn:
        conn.execute("INSERT INTO schema_migrations (version, name, applied_at_utc, checksum) VALUES (4, 'future', 'x', 'y')")
        conn.commit()
    code, _, err = run_main(capsys, "migrate", "--db-path", golden)
    assert code == 1 and "refusing to run old code" in err
    assert run_main(capsys, "status", "--db-path", golden)[1]["ahead_of_code"] == [4]


def test_an_unreadable_file_exits_1(capsys, tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a sqlite database" * 100)
    for command in ("migrate", "status"):
        code, _, err = run_main(capsys, command, "--db-path", str(junk))
        assert code == 1 and "failed" in err


def test_a_missing_subcommand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as stop:
        database.main([])
    assert stop.value.code == 2


def test_it_runs_as_python_dash_m_the_way_the_workflow_calls_it(tmp_path, golden):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    def call(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "database", *args, "--db-path", golden], cwd=str(tmp_path), env=env,
                              capture_output=True, text=True, timeout=120)

    pending = call("status", "--check")
    assert pending.returncode == 10 and json.loads(pending.stdout)["pending"] == [1, 2, 3]
    done = call("migrate")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["applied_now"] == [1, 2, 3]
    clean = call("status", "--check")
    assert clean.returncode == 0 and json.loads(clean.stdout)["current_version"] == 3
