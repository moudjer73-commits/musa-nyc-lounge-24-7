from pathlib import Path
from telegram.ext import Application
from src.alex.responder import AlexResponder
from src.analyst.engine import AnalystEngine
from src.bot.alex_handlers import register_alex_handlers
from src.bot.analyst_handlers import register_analyst_handlers
from src.bot.interaction_reentry_handlers import register_interaction_and_reentry_handlers
from src.bot.onboarding_handlers import register_onboarding
from src.bot.phase3_handlers import register_phase3_handlers
from src.bot.podcast_handlers import register_podcast_handlers
from src.bot.pressure_tracking_handlers import register_pressure_and_tracking_handlers
from src.bot.session_handlers import register_session_handlers
from src.chunks.scheduler import AcquisitionScheduler, ChunkBank
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.analyst_repo import AnalystRepository
from src.db.chunk_repo import ChunkRepository
from src.db.engine import get_connection, restore_encrypted_snapshot
from src.db.podcast_repo import PodcastRepository
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository
from src.interaction.track import InteractionTrackEngine
from src.listening.ladder import ListeningLadderEngine
from src.llm.gemini_client import GeminiService
from src.onboarding.diagnosis import OnboardingDiagnosisEngine
from src.phase3.engine import Phase3Engine
from src.podcast.pipeline import PodcastPipeline
from src.pressure.engine import PressureTestEngine
from src.prosody.lab import ProsodyLabEngine
from src.quality.guard import QualityGuard
from src.register.engine import RegisterEngine
from src.session.daily_manager import DailySessionManager
from src.session.reentry import ReEntrySessionManager
from src.tracking.progress import ProgressTracker


class LabContainer:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        snapshot_path: Path = SNAPSHOT_PATH,
        passphrase: str = SNAPSHOT_PASSPHRASE,
        llm: GeminiService | None = None,
    ) -> None:
        self.db_path = db_path
        self.snapshot_path = snapshot_path
        self.passphrase = passphrase
        if passphrase and snapshot_path.exists() and not db_path.exists():
            restore_encrypted_snapshot(snapshot_path, db_path, passphrase)

        self.profile_repo = ProfileRepository(db_path, snapshot_path, passphrase)
        self.scene_repo = SceneRepository(db_path, snapshot_path, passphrase)
        self.analyst_repo = AnalystRepository(db_path, snapshot_path, passphrase)
        self.chunk_repo = ChunkRepository(db_path, snapshot_path, passphrase)
        self.podcast_repo = PodcastRepository(db_path, snapshot_path, passphrase)

        self.llm = llm or GeminiService(self.profile_repo)
        self.guard = QualityGuard()
        self.reg_engine = RegisterEngine()
        self.chunk_bank = ChunkBank()
        self.scheduler = AcquisitionScheduler()
        self.onboarding_engine = OnboardingDiagnosisEngine(self.profile_repo, self.llm)
        self.responder = AlexResponder(self.scene_repo, self.llm, self.guard)
        self.analyst = AnalystEngine(self.analyst_repo, self.llm)
        self.session_mgr = DailySessionManager(self.chunk_repo, self.chunk_bank, self.scheduler, self.analyst)
        self.pressure_engine = PressureTestEngine(self.reg_engine)
        self.tracker = ProgressTracker(self.profile_repo, self.chunk_repo, db_path, snapshot_path, passphrase)
        self.listening_ladder = ListeningLadderEngine()
        self.podcast_pipeline = PodcastPipeline(self.podcast_repo, self.llm)
        self.interaction_engine = InteractionTrackEngine()
        self.reentry_mgr = ReEntrySessionManager(self.chunk_repo)
        self.prosody_lab = ProsodyLabEngine()
        self.phase3_engine = Phase3Engine(self.profile_repo, self.chunk_repo, self.tracker, db_path, snapshot_path, passphrase)

    def purge_all_user_data(self, user_id: int) -> bool:
        """Complete per-user privacy wipe across every table in Spoken English Lab."""
        self.scene_repo.delete_user_scenes(user_id)
        self.analyst_repo.delete_user_analyst_data(user_id)
        self.chunk_repo.delete_user_chunk_data(user_id)
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM baseline_snapshots WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM benchmark_scores WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM podcast_learner_states WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM personal_podcast_budget WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM correction_ratings WHERE user_id = ?", (user_id,))
            conn.commit()
        return self.profile_repo.delete_user_data(user_id)


def register_all_modules(app: Application, container: LabContainer) -> None:
    """Single clean registration call wiring all 8 sprints into Telegram Application."""
    register_alex_handlers(app, container.scene_repo, container.responder, container.reg_engine, container.guard)
    register_analyst_handlers(app, container.profile_repo, container.analyst)
    register_session_handlers(app, container.chunk_repo, container.chunk_bank, container.session_mgr)
    register_pressure_and_tracking_handlers(app, container.profile_repo, container.pressure_engine, container.tracker)
    register_podcast_handlers(app, container.podcast_repo, container.podcast_pipeline, container.listening_ladder)
    register_interaction_and_reentry_handlers(app, container.interaction_engine, container.reentry_mgr)
    register_phase3_handlers(app, container.profile_repo, container.prosody_lab, container.phase3_engine)
    register_onboarding(app, container.profile_repo, container.onboarding_engine)
