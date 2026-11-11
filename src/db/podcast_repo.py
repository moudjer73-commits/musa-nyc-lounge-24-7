from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, get_connection
from src.models.listening_podcast import LearnerPodcastState, PodcastEpisodeRecord

PODCAST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS podcast_episodes (
    episode_id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    user_id INTEGER,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS podcast_learner_states (
    user_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);

CREATE TABLE IF NOT EXISTS personal_podcast_budget (
    user_id INTEGER NOT NULL,
    budget_date TEXT NOT NULL,
    episodes_generated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, budget_date)
);
"""


class PodcastRepository:
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
            conn.executescript(PODCAST_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def get_cached_episode(self, cache_key: str) -> PodcastEpisodeRecord | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT record_json FROM podcast_episodes WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            return PodcastEpisodeRecord.model_validate_json(row["record_json"]) if row else None

    def get_episode_by_id(self, episode_id: str) -> PodcastEpisodeRecord | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT record_json FROM podcast_episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            return PodcastEpisodeRecord.model_validate_json(row["record_json"]) if row else None

    def save_episode(self, record: PodcastEpisodeRecord) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO podcast_episodes (episode_id, cache_key, user_id, record_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO NOTHING
                """,
                (record.episode_id, record.cache_key, record.user_id, record.model_dump_json(), now_iso),
            )
            conn.commit()
        self._sync()

    def check_and_consume_personal_budget(self, user_id: int, max_per_day: int = 1) -> bool:
        if max_per_day <= 0:
            return False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                INSERT INTO personal_podcast_budget (user_id, budget_date, episodes_generated)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, budget_date) DO UPDATE SET
                    episodes_generated = personal_podcast_budget.episodes_generated + 1
                WHERE personal_podcast_budget.episodes_generated < ?
                RETURNING episodes_generated
                """,
                (user_id, today, max_per_day),
            ).fetchall()
            conn.commit()
        if not rows:
            return False
        self._sync()
        return True

    def refund_personal_budget(self, user_id: int) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE personal_podcast_budget SET episodes_generated = MAX(episodes_generated - 1, 0) "
                "WHERE user_id = ? AND budget_date = ?",
                (user_id, today),
            )
            conn.commit()
        self._sync()

    def delete_user_episodes(self, user_id: int) -> None:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT record_json FROM podcast_episodes WHERE user_id = ?", (user_id,)
            ).fetchall()
            conn.execute("DELETE FROM podcast_episodes WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM podcast_learner_states WHERE user_id = ?", (user_id,))
            conn.commit()
        for r in rows:
            try:
                rec = PodcastEpisodeRecord.model_validate_json(r["record_json"])
                if rec.audio_path:
                    base = Path(rec.audio_path)
                    for cand in (
                        base,
                        base.with_suffix(".mp3"),
                        base.with_suffix(".wav"),
                        base.with_name(f"{base.stem}_slow.mp3"),
                        base.with_name(f"{base.stem}_slow.wav"),
                    ):
                        cand.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self._sync()

    def get_or_create_learner_state(self, user_id: int, episode_id: str) -> LearnerPodcastState:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM podcast_learner_states WHERE user_id = ? AND episode_id = ?",
                (user_id, episode_id),
            ).fetchone()
            if row:
                return LearnerPodcastState.model_validate_json(row["state_json"])
        st = LearnerPodcastState(user_id=user_id, episode_id=episode_id)
        self.save_learner_state(st)
        return st

    def save_learner_state(self, state: LearnerPodcastState) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO podcast_learner_states (user_id, episode_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, episode_id) DO UPDATE SET
                    state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                (state.user_id, state.episode_id, state.model_dump_json(), now_iso),
            )
            conn.commit()
        self._sync()
