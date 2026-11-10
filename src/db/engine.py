import base64
import functools
import hashlib
import logging
import os
import time
import sqlite3
import tempfile
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

_SALT = hashlib.sha256(b"spoken-english-lab-v1-salt").digest()[:16]


@functools.lru_cache(maxsize=8)
def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2 is deliberately slow (~0.1s); cache it so every DB write does not pay for it again."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=120_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _marker(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(".restore_failed")


def restore_failed(snapshot_path: Path = SNAPSHOT_PATH) -> bool:
    return _marker(snapshot_path).exists()


# Seven repositories call export_encrypted_snapshot() after every write. Encrypting the whole DB each time made
# replies slow, so production sets a minimum interval; skipped exports are remembered and flushed later.
_MIN_INTERVAL_S = 0.0
_LAST_EXPORT: dict[str, float] = {}
_DIRTY: dict[str, tuple[Path, Path, str]] = {}


def set_snapshot_min_interval(seconds: float) -> None:
    global _MIN_INTERVAL_S
    _MIN_INTERVAL_S = max(0.0, float(seconds))
    if _MIN_INTERVAL_S == 0:
        _LAST_EXPORT.clear()


def flush_pending_snapshots() -> int:
    """Export every snapshot whose write was coalesced. Returns how many were written."""
    n = 0
    for key, (db_path, snapshot_path, passphrase) in list(_DIRTY.items()):
        if export_encrypted_snapshot(db_path, snapshot_path, passphrase, force=True):
            n += 1
    return n


def export_encrypted_snapshot(
    db_path: Path = DB_PATH,
    snapshot_path: Path = SNAPSHOT_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
    force: bool = False,
) -> Optional[Path]:
    """Encrypt a CONSISTENT copy of the DB (sqlite backup API) and replace the snapshot atomically.

    Never overwrites a snapshot that could not be restored in this run: otherwise a wrong passphrase or a
    corrupt file would make the bot start empty and then overwrite the good snapshot with an empty one.
    """
    if not passphrase or not db_path.exists():
        return None
    if restore_failed(snapshot_path):
        logger.warning("Snapshot export skipped: restore failed earlier in this run (existing snapshot kept).")
        return None
    key = str(Path(snapshot_path).resolve())
    if not force and _MIN_INTERVAL_S > 0 and time.monotonic() - _LAST_EXPORT.get(key, float("-inf")) < _MIN_INTERVAL_S:
        _DIRTY[key] = (db_path, snapshot_path, passphrase)
        return None
    tmp_db = ""
    try:
        fd, tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(tmp_db)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        raw_bytes = Path(tmp_db).read_bytes()
    finally:
        if tmp_db and os.path.exists(tmp_db):
            os.remove(tmp_db)
    fernet = Fernet(_derive_fernet_key(passphrase, _SALT))
    encrypted = _SALT + fernet.encrypt(raw_bytes)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_snap = snapshot_path.with_name(snapshot_path.name + ".tmp")
    tmp_snap.write_bytes(encrypted)
    os.replace(tmp_snap, snapshot_path)
    _LAST_EXPORT[key] = time.monotonic()
    _DIRTY.pop(key, None)
    return snapshot_path


def _forget_schema(db_path) -> None:
    _SCHEMA_READY.discard(str(Path(db_path).resolve()))


def restore_encrypted_snapshot(
    snapshot_path: Path = SNAPSHOT_PATH,
    db_path: Path = DB_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
) -> bool:
    if not passphrase or not snapshot_path.exists():
        return False
    payload = snapshot_path.read_bytes()
    if len(payload) <= 16:
        _marker(snapshot_path).write_text("snapshot too short\n")
        return False
    salt, token = payload[:16], payload[16:]
    fernet = Fernet(_derive_fernet_key(passphrase, salt))
    try:
        decrypted = fernet.decrypt(token)
    except Exception:  # noqa: BLE001
        logger.error("Could not decrypt the state snapshot (wrong SNAPSHOT_PASSPHRASE or corrupt file).")
        _marker(snapshot_path).write_text("decrypt failed\n")
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(decrypted)
    _forget_schema(db_path)  # old snapshots lack newer tables: re-apply the schema on next connect
    try:
        _marker(snapshot_path).unlink(missing_ok=True)
    except OSError:
        pass
    return True


_SCHEMA_READY: set[str] = set()


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    key = str(Path(db_path).resolve())
    # executescript() commits and re-parses the whole schema: do it once per file, not on every query.
    # (restore_encrypted_snapshot replaces the file, so it clears the marker.)
    if key not in _SCHEMA_READY or not Path(db_path).stat().st_size:
        conn.executescript(SCHEMA_SQL)
        _SCHEMA_READY.add(key)
    return conn
