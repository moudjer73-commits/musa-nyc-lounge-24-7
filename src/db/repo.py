from datetime import datetime, timezone
from pathlib import Path
import time
from src.config import DB_PATH, DEFAULT_DAILY_USER_LLM_BUDGET, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, get_connection

ANGLES = ("casual_sharp", "exec_clock", "seminar_skeptic")


class TwinRepository:
    def __init__(self, db_path: Path = DB_PATH, snapshot_path: Path = SNAPSHOT_PATH, passphrase: str = SNAPSHOT_PASSPHRASE) -> None:
        self.db_path, self.snapshot_path, self.passphrase = db_path, snapshot_path, passphrase
        with get_connection(self.db_path) as conn:
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def get_user(self, user_id: int) -> dict:
        now = time.time()
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM twin_users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return dict(row)
            conn.execute(
                "INSERT INTO twin_users (user_id, angle, pending_take2, turns_count, updated_at) VALUES (?, 'casual_sharp', '', 0, ?)",
                (user_id, now),
            )
            conn.commit()
        self._sync()
        return {"user_id": user_id, "angle": "casual_sharp", "pending_take2": "", "turns_count": 0, "updated_at": now}

    def set_angle(self, user_id: int, angle: str) -> str:
        target = angle if angle in ANGLES else ANGLES[0]
        self.get_user(user_id)
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE twin_users SET angle = ?, pending_take2 = '', updated_at = ? WHERE user_id = ?",
                (target, time.time(), user_id),
            )
            conn.commit()
        self._sync()
        return target

    def cycle_angle(self, user_id: int) -> str:
        u = self.get_user(user_id)
        cur = u["angle"] if u["angle"] in ANGLES else ANGLES[0]
        nxt = ANGLES[(ANGLES.index(cur) + 1) % len(ANGLES)]
        return self.set_angle(user_id, nxt)

    def set_pending_take2(self, user_id: int, prompt_text: str) -> None:
        self.get_user(user_id)
        with get_connection(self.db_path) as conn:
            conn.execute("UPDATE twin_users SET pending_take2 = ?, updated_at = ? WHERE user_id = ?", (prompt_text, time.time(), user_id))
            conn.commit()
        self._sync()

    def log_turn(self, user_id: int, role: str, angle: str, content: str, chat_message_id: int | None = None, is_voice: bool = False, duration_s: float = 0.0) -> int:
        self.get_user(user_id)
        words = len(content.split())
        wpm = round((words / duration_s) * 60.0, 1) if (is_voice and duration_s >= 2.0 and words > 0) else 0.0
        now = time.time()
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO twin_turns (user_id, chat_message_id, role, angle, content, is_voice, duration_s, wpm, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, chat_message_id, role, angle, content, int(is_voice), duration_s, wpm, now),
            )
            if role == "learner":
                conn.execute("UPDATE twin_users SET turns_count = turns_count + 1, updated_at = ? WHERE user_id = ?", (now, user_id))
            conn.commit()
            row_id = int(cur.lastrowid)
        self._sync()
        return row_id

    def recent_turns(self, user_id: int, limit: int = 12) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM twin_turns WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def last_learner_turn(self, user_id: int) -> str:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT content FROM twin_turns WHERE user_id = ? AND role = 'learner' ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return str(row["content"]) if row else ""

    def find_turn_by_msg_id(self, user_id: int, chat_message_id: int) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM twin_turns WHERE user_id = ? AND chat_message_id = ? ORDER BY id DESC LIMIT 1",
                (user_id, chat_message_id),
            ).fetchone()
        return dict(row) if row else None

    def save_vault_phrase(self, user_id: int, phrase: str, source_context: str = "") -> bool:
        clean = " ".join(phrase.strip().strip('"“”‘’\'').split())[:140]
        if len(clean) < 3:
            return False
        now = time.time()
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO twin_vault (user_id, phrase, source_context, stage, due_ts, hits, created_at) "
                "VALUES (?, ?, ?, 0, ?, 0, ?) "
                "ON CONFLICT(user_id, phrase) DO UPDATE SET due_ts = MIN(twin_vault.due_ts, excluded.due_ts)",
                (user_id, clean, source_context[:220], now, now),
            )
            conn.commit()
        self._sync()
        return True

    def due_vault_phrases(self, user_id: int, limit: int = 2) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM twin_vault WHERE user_id = ? AND due_ts <= ? ORDER BY stage ASC, due_ts ASC LIMIT ?",
                (user_id, time.time(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_vault_phrases(self, user_id: int, limit: int = 20) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM twin_vault WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_vault_hit(self, user_id: int, phrase: str) -> None:
        intervals_days = (2, 6, 18, 45)
        now = time.time()
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT stage, hits FROM twin_vault WHERE user_id = ? AND phrase = ?", (user_id, phrase)).fetchone()
            if not row:
                return
            new_stage = min(int(row["stage"]) + 1, len(intervals_days) - 1)
            next_due = now + intervals_days[new_stage] * 86400
            conn.execute(
                "UPDATE twin_vault SET stage = ?, hits = hits + 1, due_ts = ? WHERE user_id = ? AND phrase = ?",
                (new_stage, next_due, user_id, phrase),
            )
            conn.commit()
        self._sync()

    def log_upgrade(self, user_id: int, original_span: str, native_take: str, tag: str = "flow") -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO twin_upgrades (user_id, original_span, native_take, tag, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, original_span[:260], native_take[:260], tag[:40], time.time()),
            )
            conn.commit()
        self._sync()

    def weekly_pulse(self, user_id: int) -> dict:
        since = time.time() - 7 * 86400
        with get_connection(self.db_path) as conn:
            turns = conn.execute(
                "SELECT * FROM twin_turns WHERE user_id = ? AND role = 'learner' AND created_at >= ?", (user_id, since)
            ).fetchall()
            activated = conn.execute(
                "SELECT phrase, hits FROM twin_vault WHERE user_id = ? AND hits > 0 ORDER BY hits DESC LIMIT 4", (user_id,)
            ).fetchall()
            upgrades = conn.execute(
                "SELECT original_span, native_take, tag FROM twin_upgrades WHERE user_id = ? AND created_at >= ? ORDER BY id DESC LIMIT 3",
                (user_id, since),
            ).fetchall()
        wpms = [float(r["wpm"]) for r in turns if float(r["wpm"]) > 0]
        return {
            "learner_turns": len(turns),
            "peak_wpm": round(max(wpms), 1) if wpms else 0.0,
            "avg_wpm": round(sum(wpms) / len(wpms), 1) if wpms else 0.0,
            "activated_phrases": [dict(r) for r in activated],
            "recent_upgrades": [dict(r) for r in upgrades],
        }

    def check_and_increment_quota(self, user_id: int, daily_limit: int = DEFAULT_DAILY_USER_LLM_BUDGET) -> tuple[bool, int]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if daily_limit <= 0:
            return False, 0
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "INSERT INTO daily_usage (user_id, usage_date, llm_calls) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, usage_date) DO UPDATE SET llm_calls = daily_usage.llm_calls + 1 "
                "WHERE daily_usage.llm_calls < ? RETURNING llm_calls",
                (user_id, today, daily_limit),
            ).fetchall()
            if rows:
                conn.commit()
                return True, int(rows[0]["llm_calls"])
            cur = conn.execute("SELECT llm_calls FROM daily_usage WHERE user_id = ? AND usage_date = ?", (user_id, today)).fetchone()
            conn.commit()
            return False, int(cur["llm_calls"]) if cur else daily_limit

    def refund_quota(self, user_id: int) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            conn.execute("UPDATE daily_usage SET llm_calls = MAX(llm_calls - 1, 0) WHERE user_id = ? AND usage_date = ?", (user_id, today))
            conn.commit()

    def clear_conversation(self, user_id: int) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM twin_turns WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE twin_users SET pending_take2 = '', updated_at = ? WHERE user_id = ?", (time.time(), user_id))
            conn.commit()
        self._sync()

    def purge_user(self, user_id: int) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        deleted = 0
        with get_connection(self.db_path) as conn:
            for tbl in ("twin_users", "twin_turns", "twin_vault", "twin_upgrades"):
                deleted += conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,)).rowcount
            conn.execute("DELETE FROM daily_usage WHERE user_id = ? AND usage_date < ?", (user_id, today))
            conn.commit()
        self._sync()
        return deleted > 0
