from datetime import datetime, timezone
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.chunk_repo import ChunkRepository
from src.db.engine import export_encrypted_snapshot, get_connection
from src.db.profile_repo import ProfileRepository
from src.models.profile import FullProfileDimensions, LearnerProfile
from src.models.prosody_phase3 import AggregateGroupMilestone, HumorExercise, PilotMetricsSummary
from src.models.register_scene import LearningStage
from src.tracking.progress import ProgressTracker

PHASE3_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS correction_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    active_date TEXT NOT NULL
);
"""


class Phase3Engine:
    def __init__(
        self,
        profile_repo: ProfileRepository,
        chunk_repo: ChunkRepository,
        tracker: ProgressTracker,
        db_path: Path = DB_PATH,
        snapshot_path: Path = SNAPSHOT_PATH,
        passphrase: str = SNAPSHOT_PASSPHRASE,
    ) -> None:
        self.profile_repo = profile_repo
        self.chunk_repo = chunk_repo
        self.tracker = tracker
        self.db_path = db_path
        self.snapshot_path = snapshot_path
        self.passphrase = passphrase
        with get_connection(self.db_path) as conn:
            conn.executescript(PHASE3_SCHEMA_SQL)
            conn.commit()

    def _sync(self) -> None:
        if self.passphrase:
            export_encrypted_snapshot(self.db_path, self.snapshot_path, self.passphrase)

    def sync_full_eight_dimensions(
        self,
        profile: LearnerProfile,
        pronunciation_score: float | None = None,
        pronunciation_evidence: str = "",
        listening_score: float | None = None,
        listening_evidence: str = "",
    ) -> FullProfileDimensions:
        sc = max(1, profile.completed_sessions_count)
        mvp = profile.mvp_dimensions
        full = profile.full_dimensions

        full.fluency_retrieval.update_with_evidence(
            mvp.retrieval_independence.score,
            mvp.retrieval_independence.evidence_spans[-1] if mvp.retrieval_independence.evidence_spans else "mvp sync",
            sc,
        )
        full.lexicon.update_with_evidence(mvp.linguistic_quality.score, "lexical precision sync", sc)
        full.grammar.update_with_evidence(mvp.linguistic_quality.score, "grammatical control sync", sc)
        full.interaction.update_with_evidence(mvp.interaction.score, "interactional flow sync", sc)
        full.pragmatics.update_with_evidence(mvp.register_x_function.score, "pragmatic calibration sync", sc)
        full.register_control.update_with_evidence(mvp.register_x_function.score, "register agility sync", sc)

        if pronunciation_score is not None:
            full.pronunciation.update_with_evidence(pronunciation_score, pronunciation_evidence or "prosody lab", sc)
        if listening_score is not None:
            full.listening.update_with_evidence(listening_score, listening_evidence or "listening ladder", sc)

        self.profile_repo.save_profile(profile)
        return full

    def get_humor_exercise(self, stage: LearningStage, requested_mode: str = "production") -> HumorExercise:
        """Blueprint 4.2: Humor production starts at S3 and recognition comes before production."""
        effective_mode = "production" if (requested_mode == "production" and stage in ("S3", "S4", "S5")) else "recognition"
        if effective_mode == "recognition":
            return HumorExercise(
                exercise_id="hum_rec_01",
                mode="recognition",
                register_setting="social",
                prompt_text="Colleague: 'Oh great, another 4 PM Friday alignment call—just what my weekend needed.'",
                explanation="Recognition focus: Identify dry understatement/irony without taking the words literally.",
            )
        return HumorExercise(
            exercise_id="hum_prod_01",
            mode="production",
            register_setting="casual",
            prompt_text="Use light self-deprecating understatement to describe a minor cooking or tech disaster.",
            explanation="S3+ Production focus: Keep delivery deadpan and natural without forced slang.",
        )

    def record_correction_rating(self, user_id: int, rating: int) -> None:
        clamped = max(1, min(5, rating))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO correction_ratings (user_id, rating, active_date) VALUES (?, ?, ?)",
                (user_id, clamped, today),
            )
            conn.commit()
        self._sync()

    def get_pilot_metrics(self, user_id: int) -> PilotMetricsSummary:
        """Blueprint Section 5 Pilot: days of use, E3 count, correction usefulness rating, before/after task."""
        with get_connection(self.db_path) as conn:
            r_row = conn.execute(
                "SELECT AVG(rating) AS avg_r, COUNT(DISTINCT active_date) AS r_days FROM correction_ratings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            u_row = conn.execute(
                "SELECT COUNT(DISTINCT usage_date) AS u_days FROM daily_usage WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        avg_r = round(float(r_row["avg_r"]), 2) if (r_row and r_row["avg_r"] is not None) else 0.0
        days = max(int(r_row["r_days"] or 0), int(u_row["u_days"] or 0), 1)
        prog = self.tracker.build_progress_report(user_id)
        return PilotMetricsSummary(
            user_id=user_id,
            days_of_use=days,
            targets_reaching_e3=prog.e3_plus_total,
            avg_correction_usefulness_rating=avg_r,
            fixed_task_before_after_delta=prog.benchmark_delta,
        )

    def get_group_aggregate_milestone(self) -> AggregateGroupMilestone:
        """Blueprint 4.10: No leaderboard; records are private; aggregate milestones only."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT user_id, profile_json FROM learner_profiles").fetchall()
        total_users = len(rows)
        total_sessions = 0
        total_e3 = 0
        for r in rows:
            p = LearnerProfile.model_validate_json(r["profile_json"])
            total_sessions += p.completed_sessions_count
            recs = self.chunk_repo.list_user_records(p.user_id)
            total_e3 += sum(1 for c in recs if c.e_level in ("E3", "E4"))
        return AggregateGroupMilestone(
            milestone_name="Spoken English Lab — Shared Circle Milestone",
            leaderboard_enabled=False,
            private_records_exposed=False,
            total_active_learners=total_users,
            collective_sessions_completed=total_sessions,
            collective_e3_targets_reached=total_e3,
        )
