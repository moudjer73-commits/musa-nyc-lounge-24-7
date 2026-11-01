from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(Path(__file__).resolve().parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

# config.py reads SPOKEN_LAB_DB at import: never let a test fall back to the production file path.
os.environ.setdefault("SPOKEN_LAB_DB", os.path.join(tempfile.gettempdir(), "spoken_lab_tests_v3_default.db"))

from clock import FakeClock  # noqa: E402
from database import EventSourcedDatabase  # noqa: E402


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock("2026-10-01T09:00:00Z")


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "lab.db")


@pytest.fixture
def db(db_path, clock) -> EventSourcedDatabase:
    database = EventSourcedDatabase(db_path, clock=clock)
    database.upsert_learner(1001)
    database.create_session("s1", 1001, started_at_utc="2026-10-01T09:00:00.000000Z")
    return database
