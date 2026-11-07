from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, get_connection
from src.models.chunk import ChunkMasteryRecord, FunctionalChunk

CHUNK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunk_mastery (
    user_id INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    context_key TEXT NOT NULL,
    record_json TEXT NOT NULL,
    next_review_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, chunk_id, context_key)
);

CREATE TABLE IF NOT EXISTS user_reminders (
    user_id INTEGER PRIMARY KEY,
    preferred_hour_utc INTEGER NOT NULL DEFAULT 18,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sent_date TEXT
);
"""


class ChunkRepository:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        snapshot_path: Path = SNAPSHOT_PATH,
        passphrase: str = SNAPSHOT_PASSPHRASE,
    ) -> None:
        self.db_path = db_path
        self.snapshot_path = snapshot_path
        self.passphrase = passphrase
        with get_connection(self.db_path) as conn:
            conn.executescript(CHUNK_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def get_or_create_record(self, user_id: int, chunk: FunctionalChunk) -> ChunkMasteryRecord:
        context_key = f"{chunk.function}:{chunk.register_type}"
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT record_json FROM chunk_mastery WHERE user_id = ? AND chunk_id = ? AND context_key = ?",
                (user_id, chunk.chunk_id, context_key),
            ).fetchone()
            if row:
                return ChunkMasteryRecord.model_validate_json(row["record_json"])
        rec = ChunkMasteryRecord(
            user_id=user_id,
            chunk_id=chunk.chunk_id,
            function=chunk.function,
            baseline_register=chunk.register_type,
        )
        self.save_record(rec)
        return rec

    def save_record(self, record: ChunkMasteryRecord) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO chunk_mastery (user_id, chunk_id, context_key, record_json, next_review_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, chunk_id, context_key) DO UPDATE SET
                    record_json = excluded.record_json,
                    next_review_at = excluded.next_review_at,
                    updated_at = excluded.updated_at
                """,
                (
                    record.user_id,
                    record.chunk_id,
                    record.context_key,
                    record.model_dump_json(),
                    record.next_review_at,
                    now_iso,
                ),
            )
            conn.commit()
        self._sync()

    def get_due_records(self, user_id: int, now: datetime | None = None) -> list[ChunkMasteryRecord]:
        ref_iso = (now or datetime.now(timezone.utc)).isoformat()
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT record_json FROM chunk_mastery
                WHERE user_id = ? AND (next_review_at IS NULL OR next_review_at <= ?)
                ORDER BY next_review_at ASC
                """,
                (user_id, ref_iso),
            ).fetchall()
        return [ChunkMasteryRecord.model_validate_json(r["record_json"]) for r in rows]

    def list_user_records(self, user_id: int) -> list[ChunkMasteryRecord]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT record_json FROM chunk_mastery WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [ChunkMasteryRecord.model_validate_json(r["record_json"]) for r in rows]

    def configure_reminder(self, user_id: int, hour_utc: int = 18, enabled: bool = True) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO user_reminders (user_id, preferred_hour_utc, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferred_hour_utc = excluded.preferred_hour_utc,
                    enabled = excluded.enabled
                """,
                (user_id, hour_utc % 24, 1 if enabled else 0),
            )
            conn.commit()
        self._sync()

    def get_due_reminder_users(self, now: datetime | None = None) -> list[int]:
        ref = now or datetime.now(timezone.utc)
        today_str = ref.strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM user_reminders
                WHERE enabled = 1 AND preferred_hour_utc <= ?
                  AND (last_sent_date IS NULL OR last_sent_date != ?)
                """,
                (ref.hour, today_str),
            ).fetchall()
        return [int(r["user_id"]) for r in rows]

    def mark_reminder_sent(self, user_id: int, now: datetime | None = None) -> None:
        today_str = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE user_reminders SET last_sent_date = ? WHERE user_id = ?",
                (today_str, user_id),
            )
            conn.commit()

    def delete_user_chunk_data(self, user_id: int) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM chunk_mastery WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_reminders WHERE user_id = ?", (user_id,))
            conn.commit()
        self._sync()
