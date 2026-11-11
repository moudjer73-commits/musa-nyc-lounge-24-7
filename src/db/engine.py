import base64, functools, logging, os, sqlite3, tempfile, threading, time
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS twin_users (
    user_id INTEGER PRIMARY KEY,
    angle TEXT NOT NULL DEFAULT 'casual_sharp',
    pending_take2 TEXT NOT NULL DEFAULT '',
    turns_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS twin_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_message_id INTEGER,
    role TEXT NOT NULL,
    angle TEXT NOT NULL,
    content TEXT NOT NULL,
    is_voice INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0.0,
    wpm REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS twin_vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    phrase TEXT NOT NULL,
    source_context TEXT NOT NULL DEFAULT '',
    stage INTEGER NOT NULL DEFAULT 0,
    due_ts REAL NOT NULL DEFAULT 0.0,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE(user_id, phrase)
);

CREATE TABLE IF NOT EXISTS twin_upgrades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    original_span TEXT NOT NULL,
    native_take TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT 'flow',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    user_id INTEGER NOT NULL,
    usage_date TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);
"""

_SNAPSHOT_V2_MAGIC = b"SELSNAP2"
_ITER_V1 = 120_000
_ITER_V2 = 600_000
_PROCESS_SALT = os.urandom(16)
_LOCK = threading.RLock()
_MIN_INTERVAL_S = 0.0
_LAST_EXPORT: dict[str, float] = {}
_DIRTY: dict[str, tuple[Path, Path, str]] = {}
_SCHEMA_READY: set[str] = set()
_WAL_READY: set[str] = set()


@functools.lru_cache(maxsize=32)
def _derive_key(passphrase: str, salt: bytes, iterations: int = _ITER_V2) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _marker(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(".restore_failed")


def restore_failed(snapshot_path: Path = SNAPSHOT_PATH) -> bool:
    return _marker(snapshot_path).exists()


def set_snapshot_min_interval(seconds: float) -> None:
    global _MIN_INTERVAL_S
    with _LOCK:
        _MIN_INTERVAL_S = max(0.0, float(seconds))
        if _MIN_INTERVAL_S == 0:
            _LAST_EXPORT.clear()
            _DIRTY.clear()


def flush_pending_snapshots() -> int:
    with _LOCK:
        pending = list(_DIRTY.items())
    n = 0
    for _k, (db_p, snap_p, pw) in pending:
        if export_encrypted_snapshot(db_p, snap_p, pw, force=True):
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
    with _LOCK:
        if restore_failed(snapshot_path):
            return None
        key = str(Path(snapshot_path).resolve())
        if not force and _MIN_INTERVAL_S > 0:
            _DIRTY[key] = (db_path, snapshot_path, passphrase)
            return None
        tmp_db = tmp_snap = ""
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
            raw = Path(tmp_db).read_bytes()
            enc = _SNAPSHOT_V2_MAGIC + _PROCESS_SALT + Fernet(_derive_key(passphrase, _PROCESS_SALT, _ITER_V2)).encrypt(raw)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            sfd, tmp_snap = tempfile.mkstemp(prefix=snapshot_path.name + ".", suffix=".tmp", dir=str(snapshot_path.parent))
            try:
                os.write(sfd, enc)
            finally:
                os.close(sfd)
            os.replace(tmp_snap, snapshot_path)
            tmp_snap = ""
            _LAST_EXPORT[key] = time.monotonic()
            _DIRTY.pop(key, None)
            return snapshot_path
        finally:
            for f in (tmp_db, tmp_snap):
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass


def restore_encrypted_snapshot(
    snapshot_path: Path = SNAPSHOT_PATH,
    db_path: Path = DB_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
) -> bool:
    if not passphrase or not snapshot_path.exists():
        return False
    with _LOCK:
        payload = snapshot_path.read_bytes()
        if payload.startswith(_SNAPSHOT_V2_MAGIC):
            hdr = len(_SNAPSHOT_V2_MAGIC) + 16
            if len(payload) <= hdr:
                _marker(snapshot_path).write_text("short\n")
                return False
            salt, tok, iters = payload[len(_SNAPSHOT_V2_MAGIC):hdr], payload[hdr:], _ITER_V2
        else:
            if len(payload) <= 16:
                _marker(snapshot_path).write_text("short\n")
                return False
            salt, tok, iters = payload[:16], payload[16:], _ITER_V1
        try:
            dec = Fernet(_derive_key(passphrase, salt, iters)).decrypt(tok)
        except Exception:
            _marker(snapshot_path).write_text("decrypt_failed\n")
            return False
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suf in ("-wal", "-shm"):
            Path(f"{db_path}{suf}").unlink(missing_ok=True)
        db_path.write_bytes(dec)
        resolved = str(Path(db_path).resolve())
        _SCHEMA_READY.discard(resolved)
        _WAL_READY.discard(resolved)
        _marker(snapshot_path).unlink(missing_ok=True)
        return True


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    key = str(Path(db_path).resolve())
    empty = not Path(db_path).exists() or not Path(db_path).stat().st_size
    if key not in _WAL_READY or empty:
        conn.execute("PRAGMA journal_mode=WAL")
        _WAL_READY.add(key)
    if key not in _SCHEMA_READY or empty:
        conn.executescript(SCHEMA_SQL)
        _SCHEMA_READY.add(key)
    return conn
