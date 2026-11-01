"""Migration runner for Spoken English Lab V3.0 (S0 section 4.0). Owner: Claude.

Rules (all enforced here, none left to the individual migrations):

* ``schema_migrations(version, name, applied_at_utc, checksum)`` is created by the runner,
  outside any migration. A database that has ``event_log`` but no ``schema_migrations`` is
  baseline version 0 (the v2 schema); a brand-new file gets the v2 baseline DDL first.
* Each migration runs in one ``BEGIN IMMEDIATE`` transaction. A migration that needs
  ``PRAGMA foreign_keys=OFF`` (M001) gets it set before ``BEGIN``; the runner runs
  ``PRAGMA foreign_key_check`` before ``COMMIT`` (a non-empty result rolls back) and turns
  foreign keys back on afterwards.
* Idempotent: applied versions are skipped. A changed checksum on an applied version, or a
  database that is ahead of this code, is a hard error (exit code 1 from the CLI).
* The checksum covers the migration's static SQL text, its name, its version and a ``logic_tag``;
  it deliberately does not cover Python helpers that later sprints extend (the projection
  rebuild), so S2/S3 do not invalidate already-applied versions.
* Enums that evolve are validated in Pydantic, not by CHECK constraints (the v1 mistake). The only
  CHECKs the migrations add are ``json_valid`` and numeric ranges.

Entry points: ``python -m database migrate --db-path <path>`` (also called at app start through
``EventSourcedDatabase.migrate``) and ``python -m database status --db-path <path> [--check]``.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional

from clock import Clock, SystemClock, iso_now

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    checksum TEXT NOT NULL
)
"""

_MIGRATION_MODULES = (
    "migrations.m001_event_types_and_event_log",
    "migrations.m002_v3_tables_and_columns",
    "migrations.m003_projection_rebuild",
)

FaultHook = Callable[[str], None]


class MigrationError(RuntimeError):
    """A migration could not be applied; the database was left unchanged."""


class ChecksumMismatchError(MigrationError):
    """An applied migration's SQL no longer matches what was applied."""


class VerificationError(MigrationError):
    """A post-condition of a migration did not hold (it was rolled back)."""


@dataclass(frozen=True)
class MigrationContext:
    now_iso: str
    clock: Clock


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    fk_off: bool = False
    logic_tag: str = ""
    before: Optional[Callable[[sqlite3.Connection, MigrationContext], Any]] = None
    run: Optional[Callable[[sqlite3.Connection, MigrationContext], None]] = None
    verify: Optional[Callable[[sqlite3.Connection, MigrationContext, Any], None]] = None

    @property
    def checksum(self) -> str:
        statements = "\n".join(split_sql(self.sql))
        material = f"{self.version}|{self.name}|{self.logic_tag}|{statements}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ helpers


def split_sql(script: str) -> list[str]:
    """Split a script into single statements (trigger bodies stay in one piece).

    Whitespace is normalised so that line-ending or trailing-space edits do not change a checksum.
    """
    statements: list[str] = []
    buffer: list[str] = []
    for line in script.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        buffer.append(line.rstrip())
        chunk = "\n".join(buffer)
        if sqlite3.complete_statement(chunk):
            text = chunk.strip()
            if text and text != ";":
                statements.append(text)
            buffer = []
    leftover = "\n".join(buffer).strip()
    if leftover and any(not part.strip().startswith("--") for part in leftover.split("\n") if part.strip()):
        raise ValueError(f"incomplete SQL statement near: {leftover[:80]!r}")
    return statements


def all_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for module_name in _MIGRATION_MODULES:
        module = importlib.import_module(module_name)
        migrations.append(module.MIGRATION)
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 1, got {versions}")
    return migrations


def latest_version() -> int:
    return max(m.version for m in all_migrations())


def connect(db_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Autocommit connection (transactions are explicit), rows as ``sqlite3.Row``."""
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0, isolation_level=None)
    else:
        conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def applied_versions(conn: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(conn, "schema_migrations"):
        return {}
    return {int(r["version"]): str(r["checksum"]) for r in conn.execute("SELECT version, checksum FROM schema_migrations")}


def table_digest(conn: sqlite3.Connection, table: str, *, exclude: tuple[str, ...] = ()) -> str:
    """SHA-256 over every row of ``table`` in rowid order. Two rebuilds that produce the same
    digest produced byte-identical tables."""
    columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["name"] not in exclude]
    column_list = ", ".join(f'"{c}"' for c in columns)
    digest = hashlib.sha256()
    for row in conn.execute(f"SELECT {column_list} FROM {table} ORDER BY rowid"):
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _hook(fault_hook: Optional[FaultHook], point: str) -> None:
    if fault_hook is not None:
        fault_hook(point)


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _check_foreign_keys(conn: sqlite3.Connection) -> None:
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        sample = ", ".join(f"{v[0]}(rowid={v[1]})->{v[2]}" for v in violations[:10])
        raise VerificationError(f"foreign_key_check found {len(violations)} violation(s): {sample}")


# ------------------------------------------------------------------ runner


def migrate(
    db_path: str,
    *,
    baseline_ddl: Optional[str] = None,
    clock: Optional[Clock] = None,
    target: Optional[int] = None,
    fault_hook: Optional[FaultHook] = None,
) -> list[int]:
    """Apply every pending migration (up to ``target``). Returns the versions applied by this call.

    ``baseline_ddl`` is the v2 schema (``database.BASELINE_DDL``); it is idempotent
    (``CREATE ... IF NOT EXISTS``), so running it on a V3 database changes nothing.
    ``fault_hook(point)`` is called at named points inside each transaction
    (``before_sql:N``, ``after_sql:N``, ``after_run:N``, ``before_commit:N``); tests use it
    to kill the process mid-migration.
    """
    clock = clock or SystemClock()
    migrations = all_migrations()
    known = {m.version: m for m in migrations}
    conn = connect(db_path)
    applied_now: list[int] = []
    try:
        if baseline_ddl is not None:
            conn.executescript(baseline_ddl)
        conn.execute(SCHEMA_MIGRATIONS_DDL)
        stored = applied_versions(conn)
        unknown = sorted(v for v in stored if v not in known)
        if unknown:
            raise MigrationError(
                f"database is at version {max(stored)} but this code only knows up to {max(known)}; "
                f"refusing to run old code on a newer database"
            )
        for version, checksum in sorted(stored.items()):
            if checksum != known[version].checksum:
                raise ChecksumMismatchError(
                    f"migration {version} ({known[version].name}) was applied with checksum {checksum[:12]}... "
                    f"but its SQL now hashes to {known[version].checksum[:12]}...; applied migrations are immutable"
                )
        for migration in migrations:
            if target is not None and migration.version > target:
                break
            if _apply_one(conn, migration, clock, fault_hook):
                applied_now.append(migration.version)
    finally:
        conn.close()
    return applied_now


def _apply_one(conn: sqlite3.Connection, migration: Migration, clock: Clock, fault_hook: Optional[FaultHook]) -> bool:
    if migration.version in applied_versions(conn):
        return False
    if migration.fk_off:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if migration.version in applied_versions(conn):  # another process applied it while we waited
                conn.execute("ROLLBACK")
                return False
            ctx = MigrationContext(now_iso=iso_now(clock), clock=clock)
            snapshot = migration.before(conn, ctx) if migration.before else None
            _hook(fault_hook, f"before_sql:{migration.version}")
            for statement in split_sql(migration.sql):
                conn.execute(statement)
            _hook(fault_hook, f"after_sql:{migration.version}")
            if migration.run:
                migration.run(conn, ctx)
            _hook(fault_hook, f"after_run:{migration.version}")
            if migration.verify:
                migration.verify(conn, ctx, snapshot)
            _check_foreign_keys(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at_utc, checksum) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, ctx.now_iso, migration.checksum),
            )
            _hook(fault_hook, f"before_commit:{migration.version}")
            conn.execute("COMMIT")
        except MigrationError:
            _rollback_quietly(conn)
            raise
        except Exception as exc:
            _rollback_quietly(conn)
            raise MigrationError(f"migration {migration.version} ({migration.name}) failed and was rolled back: {exc}") from exc
        except BaseException:
            _rollback_quietly(conn)
            raise
    finally:
        if migration.fk_off:
            conn.execute("PRAGMA foreign_keys=ON")
    return True


def status(db_path: str) -> dict[str, Any]:
    """Read-only report used by the workflow to decide whether a pre-migrate anchor is needed."""
    migrations = all_migrations()
    latest = migrations[-1].version
    report: dict[str, Any] = {
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "has_event_log": False,
        "current_version": 0,
        "latest_version": latest,
        "pending": list(range(1, latest + 1)),
        "checksum_ok": True,
    }
    if not report["db_exists"]:
        return report
    conn = connect(db_path, readonly=True)
    try:
        report["has_event_log"] = table_exists(conn, "event_log")
        stored = applied_versions(conn)
        known = {m.version: m for m in migrations}
        report["current_version"] = max(stored) if stored else 0
        report["pending"] = [m.version for m in migrations if m.version not in stored]
        report["checksum_ok"] = all(v in known and known[v].checksum == c for v, c in stored.items())
        report["ahead_of_code"] = sorted(v for v in stored if v not in known)
    finally:
        conn.close()
    return report
