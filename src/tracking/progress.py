from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.chunk_repo import ChunkRepository
from src.db.engine import export_encrypted_snapshot, get_connection
from src.db.profile_repo import ProfileRepository
from src.models.pressure_tracking import BenchmarkRubricScore, CheckpointCode, ProgressReport

TRACKING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS baseline_snapshots (
    user_id INTEGER PRIMARY KEY,
    baseline_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_scores (
    user_id INTEGER NOT NULL,
    checkpoint TEXT NOT NULL,
    score_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (user_id, checkpoint)
);
"""

FIXED_UNSEEN_SPEAKING_TASK = (
    "A community center in your city has received a modest grant to either upgrade its digital lab "
    "or expand evening workshops for working adults. Explain which option you would back, address "
    "the strongest counterargument, and propose a practical compromise in 60–90 seconds."
)


class ProgressTracker:
    def __init__(
        self,
        profile_repo: ProfileRepository,
        chunk_repo: ChunkRepository,
        db_path: Path = DB_PATH,
        snapshot_path: Path = SNAPSHOT_PATH,
        passphrase: str = SNAPSHOT_PASSPHRASE,
    ) -> None:
        self.profile_repo = profile_repo
        self.chunk_repo = chunk_repo
        self.db_path = db_path
        self.snapshot_path = snapshot_path
        self.passphrase = passphrase
        with get_connection(self.db_path) as conn:
            conn.executescript(TRACKING_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def _extract_mvp_scores(self, user_id: int) -> dict[str, float]:
        p = self.profile_repo.get_or_create_profile(user_id)
        mvp = p.mvp_dimensions
        return {
            "retrieval_independence": mvp.retrieval_independence.score,
            "linguistic_quality": mvp.linguistic_quality.score,
            "interaction": mvp.interaction.score,
            "register_x_function": mvp.register_x_function.score,
        }

    def ensure_baseline_recorded(self, user_id: int) -> dict[str, float]:
        import json
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT baseline_json FROM baseline_snapshots WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return json.loads(row["baseline_json"])
            scores = self._extract_mvp_scores(user_id)
            conn.execute(
                "INSERT INTO baseline_snapshots (user_id, baseline_json, recorded_at) VALUES (?, ?, ?)",
                (user_id, json.dumps(scores), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        self._sync()
        return scores

    def record_benchmark_score(self, score: BenchmarkRubricScore) -> BenchmarkRubricScore:
        now_iso = datetime.now(timezone.utc).isoformat()
        score.recorded_at = now_iso
        if not score.task_prompt:
            score.task_prompt = FIXED_UNSEEN_SPEAKING_TASK
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO benchmark_scores (user_id, checkpoint, score_json, recorded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, checkpoint) DO UPDATE SET
                    score_json = excluded.score_json, recorded_at = excluded.recorded_at
                """,
                (score.user_id, score.checkpoint, score.model_dump_json(), now_iso),
            )
            conn.commit()
        self._sync()
        return score

    def get_benchmark_score(self, user_id: int, checkpoint: CheckpointCode) -> BenchmarkRubricScore | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT score_json FROM benchmark_scores WHERE user_id = ? AND checkpoint = ?",
                (user_id, checkpoint),
            ).fetchone()
            return BenchmarkRubricScore.model_validate_json(row["score_json"]) if row else None

    def build_progress_report(self, user_id: int) -> ProgressReport:
        baseline = self.ensure_baseline_recorded(user_id)
        current = self._extract_mvp_scores(user_id)
        growth = {k: round(current[k] - baseline.get(k, 2.0), 2) for k in current}

        records = self.chunk_repo.list_user_records(user_id)
        retention_counts = {"active": 0, "retained": 0, "weakened": 0, "temporarily_inaccessible": 0, "uncertain": 0}
        e3_plus = 0
        for r in records:
            retention_counts[r.retention_state] = retention_counts.get(r.retention_state, 0) + 1
            if r.e_level in ("E3", "E4"):
                e3_plus += 1

        d0 = self.get_benchmark_score(user_id, "day_0")
        w8 = self.get_benchmark_score(user_id, "week_8") or self.get_benchmark_score(user_id, "week_4")
        b_delta = round(w8.composite_score - d0.composite_score, 2) if (d0 and w8) else None

        return ProgressReport(
            user_id=user_id,
            is_private=True,
            leaderboard_enabled=False,
            current_competence=current,
            growth_vs_baseline=growth,
            retention_counts=retention_counts,
            e3_plus_total=e3_plus,
            benchmark_delta=b_delta,
        )
