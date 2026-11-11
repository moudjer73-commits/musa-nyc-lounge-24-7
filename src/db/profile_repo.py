from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from src.config import (
    DB_PATH,
    DEFAULT_DAILY_USER_LLM_BUDGET,
    RECALIBRATION_INTERVAL_DAYS,
    SNAPSHOT_PASSPHRASE,
    SNAPSHOT_PATH,
)
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots, get_connection
from src.models.profile import LearnerProfile


class ProfileRepository:
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
            conn.commit()

    def _sync_snapshot(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def flush_snapshot(self) -> None:
        flush_pending_snapshots()

    def get_or_create_profile(self, user_id: int) -> LearnerProfile:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT profile_json FROM learner_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return LearnerProfile.model_validate_json(row["profile_json"])
        profile = LearnerProfile(user_id=user_id)
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: LearnerProfile) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = profile.model_dump_json()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO learner_profiles (user_id, profile_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (profile.user_id, payload, now_iso),
            )
            conn.commit()
        self._sync_snapshot()

    def log_onboarding_turn(self, user_id: int, session_num: int, role: str, content: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO onboarding_logs (user_id, session_num, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, session_num, role, content, now_iso),
            )
            conn.commit()

    def get_onboarding_transcript(self, user_id: int, session_num: Optional[int] = None) -> list[dict]:
        query = "SELECT session_num, role, content, created_at FROM onboarding_logs WHERE user_id = ?"
        params: list = [user_id]
        if session_num is not None:
            query += " AND session_num = ?"
            params.append(session_num)
        query += " ORDER BY id ASC"
        with get_connection(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def needs_recalibration(self, profile: LearnerProfile, now: Optional[datetime] = None) -> bool:
        if profile.onboarding_completed and not profile.last_calibrated_at:
            return True
        if not profile.last_calibrated_at:
            return False
        ref_now = now or datetime.now(timezone.utc)
        last_dt = datetime.fromisoformat(profile.last_calibrated_at)
        return (ref_now - last_dt).days >= RECALIBRATION_INTERVAL_DAYS

    def check_and_increment_quota(
        self, user_id: int, daily_limit: int = DEFAULT_DAILY_USER_LLM_BUDGET
    ) -> tuple[bool, int]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            if daily_limit <= 0:
                cur = conn.execute(
                    "SELECT llm_calls FROM daily_usage WHERE user_id = ? AND usage_date = ?", (user_id, today)
                ).fetchone()
                return False, int(cur["llm_calls"]) if cur else 0
            rows = conn.execute(
                """
                INSERT INTO daily_usage (user_id, usage_date, llm_calls)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, usage_date) DO UPDATE SET
                    llm_calls = daily_usage.llm_calls + 1
                WHERE daily_usage.llm_calls < ?
                RETURNING llm_calls
                """,
                (user_id, today, daily_limit),
            ).fetchall()
            if rows:
                conn.commit()
                return True, int(rows[0]["llm_calls"])
            cur = conn.execute(
                "SELECT llm_calls FROM daily_usage WHERE user_id = ? AND usage_date = ?", (user_id, today)
            ).fetchone()
            conn.commit()
            return False, int(cur["llm_calls"]) if cur else daily_limit

    def refund_quota(self, user_id: int) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE daily_usage SET llm_calls = MAX(llm_calls - 1, 0) WHERE user_id = ? AND usage_date = ?",
                (user_id, today),
            )
            conn.commit()

    def delete_user_data(self, user_id: int) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            c1 = conn.execute("DELETE FROM learner_profiles WHERE user_id = ?", (user_id,)).rowcount
            c2 = conn.execute("DELETE FROM onboarding_logs WHERE user_id = ?", (user_id,)).rowcount
            conn.execute("DELETE FROM daily_usage WHERE user_id = ? AND usage_date < ?", (user_id, today))
            conn.commit()
        self._sync_snapshot()
        return (c1 + c2) > 0
