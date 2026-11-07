from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, get_connection
from src.models.analyst import AnalystLLMOutput, PRIORITY_RANK

ANALYST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analyst_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    category TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_signature TEXT NOT NULL,
    evidence_span TEXT NOT NULL,
    suggested_alternative TEXT NOT NULL,
    confidence REAL NOT NULL,
    gated_out INTEGER NOT NULL DEFAULT 0,
    action_taken TEXT NOT NULL,
    surfaced_mid_session INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hint_ladder_states (
    user_id INTEGER NOT NULL,
    target_concept TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, target_concept)
);
"""


class AnalystRepository:
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
            conn.executescript(ANALYST_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def count_signature_occurrences(self, user_id: int, session_id: str, signature: str) -> int:
        if not signature or signature == "none":
            return 0
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM analyst_logs
                WHERE user_id = ? AND session_id = ? AND error_signature = ? AND gated_out = 0
                """,
                (user_id, session_id, signature),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def log_analysis(
        self,
        user_id: int,
        session_id: str,
        output: AnalystLLMOutput,
        gated_out: bool,
        action_taken: str,
        surfaced_mid_session: bool,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analyst_logs (
                    user_id, session_id, category, error_type, error_signature,
                    evidence_span, suggested_alternative, confidence, gated_out,
                    action_taken, surfaced_mid_session, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    output.category,
                    output.error_type,
                    output.error_signature,
                    output.evidence_span,
                    output.suggested_alternative,
                    output.confidence,
                    1 if gated_out else 0,
                    action_taken,
                    1 if surfaced_mid_session else 0,
                    now_iso,
                ),
            )
            conn.commit()
        self._sync()

    def get_session_summary_targets(self, user_id: int, session_id: str, max_targets: int = 2) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT category, error_type, error_signature, evidence_span, suggested_alternative, confidence
                FROM analyst_logs
                WHERE user_id = ? AND session_id = ? AND gated_out = 0 AND category IN ('A', 'B', 'D')
                ORDER BY id DESC
                """,
                (user_id, session_id),
            ).fetchall()
        dedup: dict[str, dict] = {}
        for r in rows:
            sig = r["error_signature"] or r["evidence_span"]
            if sig not in dedup:
                dedup[sig] = dict(r)
        ranked = sorted(
            dedup.values(),
            key=lambda item: (PRIORITY_RANK.get(item["error_type"], 99), -float(item["confidence"])),
        )
        return ranked[:max_targets]

    def advance_hint_step(self, user_id: int, concept: str) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT step_index FROM hint_ladder_states WHERE user_id = ? AND target_concept = ?",
                (user_id, concept.lower()),
            ).fetchone()
            next_step = (int(row["step_index"]) + 1) if row else 1
            conn.execute(
                """
                INSERT INTO hint_ladder_states (user_id, target_concept, step_index, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, target_concept) DO UPDATE SET
                    step_index = excluded.step_index, updated_at = excluded.updated_at
                """,
                (user_id, concept.lower(), next_step, now_iso),
            )
            conn.commit()
            return next_step

    def delete_user_analyst_data(self, user_id: int) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM analyst_logs WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM hint_ladder_states WHERE user_id = ?", (user_id,))
            conn.commit()
        self._sync()
