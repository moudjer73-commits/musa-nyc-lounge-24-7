from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, get_connection
from src.models.register_scene import SceneConfig

SCENE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS active_scenes (
    user_id INTEGER PRIMARY KEY,
    scene_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    room_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SceneRepository:
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
            conn.executescript(SCENE_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def get_active_scene(self, user_id: int) -> SceneConfig:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT scene_json FROM active_scenes WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return SceneConfig.model_validate_json(row["scene_json"])
        scene = SceneConfig()
        self.save_active_scene(user_id, scene)
        return scene

    def save_active_scene(self, user_id: int, scene: SceneConfig) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO active_scenes (user_id, scene_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    scene_json = excluded.scene_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, scene.model_dump_json(), now_iso),
            )
            conn.commit()
        self._sync()

    def log_turn(self, user_id: int, room_id: str, role: str, content: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO conversation_turns (user_id, room_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, room_id, role, content, now_iso),
            )
            conn.commit()

    def get_recent_turns(self, user_id: int, role: str | None = None, limit: int = 10) -> list[str]:
        query = "SELECT content FROM conversation_turns WHERE user_id = ?"
        params: list = [user_id]
        if role:
            query += " AND role = ?"
            params.append(role)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with get_connection(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [r["content"] for r in reversed(rows)]

    def delete_user_scenes(self, user_id: int) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM active_scenes WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM conversation_turns WHERE user_id = ?", (user_id,))
            conn.commit()
        self._sync()

    def get_recent_dialogue(self, user_id: int, limit: int = 12) -> list[dict[str, str]]:
        query = "SELECT role, content FROM conversation_turns WHERE user_id = ? ORDER BY id DESC LIMIT ?"
        with get_connection(self.db_path) as conn:
            rows = conn.execute(query, (user_id, limit)).fetchall()
        return [{"role": r["role"], "text": r["content"]} for r in reversed(rows)]
