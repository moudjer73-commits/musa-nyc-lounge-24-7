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
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT episodes_generated FROM personal_podcast_budget WHERE user_id = ? AND budget_date = ?",
                (user_id, today),
            ).fetchone()
            used = int(row["episodes_generated"]) if row else 0
            if used >= max_per_day:
                return False
            conn.execute(
                """
                INSERT INTO personal_podcast_budget (user_id, budget_date, episodes_generated)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, budget_date) DO UPDATE SET episodes_generated = excluded.episodes_generated
                """,
                (user_id, today, used + 1),
            )
            conn.commit()
        self._sync()
        return True

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
