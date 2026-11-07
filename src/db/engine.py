import base64, hashlib, sqlite3
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH

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
"""


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=120_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def export_encrypted_snapshot(
    db_path: Path = DB_PATH,
    snapshot_path: Path = SNAPSHOT_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
) -> Optional[Path]:
    if not passphrase or not db_path.exists():
        return None
    salt = hashlib.sha256(b"spoken-english-lab-v1-salt").digest()[:16]
    fernet = Fernet(_derive_fernet_key(passphrase, salt))
    raw_bytes = db_path.read_bytes()
    encrypted = salt + fernet.encrypt(raw_bytes)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(encrypted)
    return snapshot_path


def restore_encrypted_snapshot(
    snapshot_path: Path = SNAPSHOT_PATH,
    db_path: Path = DB_PATH,
    passphrase: str = SNAPSHOT_PASSPHRASE,
) -> bool:
    if not passphrase or not snapshot_path.exists():
        return False
    payload = snapshot_path.read_bytes()
    if len(payload) <= 16:
        return False
    salt, token = payload[:16], payload[16:]
    fernet = Fernet(_derive_fernet_key(passphrase, salt))
    try:
        decrypted = fernet.decrypt(token)
    except Exception:
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(decrypted)
    return True


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn
