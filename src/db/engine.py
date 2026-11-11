import base64
import functools
import logging
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learner_profiles (
    user_id INTEGER PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS onboarding_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_num INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    user_id INTEGER NOT NULL,
    usage_date TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);

CREATE TABLE IF NOT EXISTS nat_turn_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    profile TEXT NOT NULL,
    dice_id TEXT NOT NULL,
    cards_json TEXT NOT NULL,
    n_words INTEGER NOT NULL,
    ends_with_question INTEGER NOT NULL,
    guard_hits_json TEXT NOT NULL,
    first_word TEXT NOT NULL,
    text TEXT NOT NULL,
    flagged INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS nat_heard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    phrase TEXT NOT NULL,
    context_note TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
);
"""

_SNAPSHOT_V2_MAGIC = b"SELSNAP2"
_PBKDF2_ITERATIONS_V1 = 120_000
_PBKDF2_ITERATIONS_V2 = 600_000
_PROCESS_SALT = os.urandom(16)
_SNAPSHOT_LOCK = threading.RLock()


@functools.lru_cache(maxsize=32)
def _derive_fernet_key(passphrase: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS_V2) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _warn_if_short_passphrase(passphrase: str) -> None:
    if passphrase and len(passphrase) < 16:
        logger.warning("SNAPSHOT_PASSPHRASE is shorter than 16 characters; use a stronger passphrase.")


def _marker(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(".restore_failed")


def restore_failed(snapshot_path: Path = SNAPSHOT_PATH) -> bool:
    return _marker(snapshot_path).exists()


_MIN_INTERVAL_S = 0.0
_LAST_EXPORT: dict[str, float] = {}
_DIRTY: dict[str, tuple[Path, Path, str]] = {}


def set_snapshot_min_interval(seconds: float) -> None:
    global _MIN_INTERVAL_S
    with _SNAPSHOT_LOCK:
        _MIN_INTERVAL_S = max(0.0, float(seconds))
        if _MIN_INTERVAL_S == 0:
            _LAST_EXPORT.clear()
            _DIRTY.clear()


def flush_pending_snapshots() -> int:
    with _SNAPSHOT_LOCK:
        pending = list(_DIRTY.items())
    n = 0
    for _key, (db_path, snapshot_path, passphrase) in pending:
        if export_encrypted_snapshot(db_path, snapshot_path, passphrase, force=True):
            n += 1
    return n


def export_encrypted_snapshot(
    db_path: Path = DB_PATH,
    snapshot_path: Path = SNAPSHOT_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
    force: bool = False,
) -> Optional[Path]:
    if not passphrase or not db_path.exists():
        return None
    _warn_if_short_passphrase(passphrase)
    with _SNAPSHOT_LOCK:
        if restore_failed(snapshot_path):
            logger.warning("Snapshot export skipped: restore failed earlier in this run (existing snapshot kept).")
            return None
        key = str(Path(snapshot_path).resolve())
        if not force and _MIN_INTERVAL_S > 0:
            _DIRTY[key] = (db_path, snapshot_path, passphrase)
            return None
        tmp_db = ""
        tmp_snap = ""
        try:
            fd, tmp_db = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            src = sqlite3.connect(str(db_path), timeout=15)
            try:
                dst = sqlite3.connect(tmp_db)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            raw_bytes = Path(tmp_db).read_bytes()
            fernet = Fernet(_derive_fernet_key(passphrase, _PROCESS_SALT, _PBKDF2_ITERATIONS_V2))
            encrypted = _SNAPSHOT_V2_MAGIC + _PROCESS_SALT + fernet.encrypt(raw_bytes)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            sfd, tmp_snap = tempfile.mkstemp(
                prefix=snapshot_path.name + ".", suffix=".tmp", dir=str(snapshot_path.parent)
            )
            try:
                os.write(sfd, encrypted)
            finally:
                os.close(sfd)
            os.replace(tmp_snap, snapshot_path)
            tmp_snap = ""
            _LAST_EXPORT[key] = time.monotonic()
            _DIRTY.pop(key, None)
            return snapshot_path
        finally:
            for leftover in (tmp_db, tmp_snap):
                if leftover and os.path.exists(leftover):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass


_SCHEMA_READY: set[str] = set()
_WAL_READY: set[str] = set()


def _forget_schema(db_path) -> None:
    resolved = str(Path(db_path).resolve())
    _SCHEMA_READY.discard(resolved)
    _WAL_READY.discard(resolved)


def restore_encrypted_snapshot(
    snapshot_path: Path = SNAPSHOT_PATH,
    db_path: Path = DB_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
) -> bool:
    if not passphrase or not snapshot_path.exists():
        return False
    _warn_if_short_passphrase(passphrase)
    with _SNAPSHOT_LOCK:
        payload = snapshot_path.read_bytes()
        if payload.startswith(_SNAPSHOT_V2_MAGIC):
            hdr = len(_SNAPSHOT_V2_MAGIC) + 16
            if len(payload) <= hdr:
                _marker(snapshot_path).write_text("snapshot too short\n")
                return False
            salt, token = payload[len(_SNAPSHOT_V2_MAGIC):hdr], payload[hdr:]
            iterations = _PBKDF2_ITERATIONS_V2
        else:
            if len(payload) <= 16:
                _marker(snapshot_path).write_text("snapshot too short\n")
                return False
            salt, token = payload[:16], payload[16:]
            iterations = _PBKDF2_ITERATIONS_V1
        fernet = Fernet(_derive_fernet_key(passphrase, salt, iterations))
        try:
            decrypted = fernet.decrypt(token)
        except Exception:  # noqa: BLE001
            logger.error("Could not decrypt the state snapshot (wrong SNAPSHOT_PASSPHRASE or corrupt file).")
            _marker(snapshot_path).write_text("decrypt failed\n")
            return False
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suf in ("-wal", "-shm"):
            try:
                Path(f"{db_path}{suf}").unlink(missing_ok=True)
            except OSError:
                pass
        db_path.write_bytes(decrypted)
        _forget_schema(db_path)
        try:
            _marker(snapshot_path).unlink(missing_ok=True)
        except OSError:
            pass
        return True


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    key = str(Path(db_path).resolve())
    is_empty = not Path(db_path).exists() or not Path(db_path).stat().st_size
    if key not in _WAL_READY or is_empty:
        conn.execute("PRAGMA journal_mode=WAL")
        _WAL_READY.add(key)
    if key not in _SCHEMA_READY or is_empty:
        conn.executescript(SCHEMA_SQL)
        _SCHEMA_READY.add(key)
    return conn
