import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from src.bot.ui_buttons import register_interactive_ui
from src.alex.responder import AlexResponder
from src.analyst.engine import AnalystEngine
from src.bot.alex_handlers import register_alex_handlers
from src.bot.analyst_handlers import register_analyst_handlers
from src.bot.interaction_reentry_handlers import register_interaction_and_reentry_handlers
from src.bot.phase3_handlers import register_phase3_handlers
from src.bot.podcast_handlers import register_podcast_handlers
from src.bot.pressure_tracking_handlers import register_pressure_and_tracking_handlers
from src.bot.session_handlers import register_session_handlers
from src.chunks.scheduler import AcquisitionScheduler, ChunkBank
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH, admin_user_ids
from src.db.analyst_repo import AnalystRepository
from src.db.chunk_repo import ChunkRepository
from src.db.engine import flush_pending_snapshots, get_connection, restore_encrypted_snapshot, set_snapshot_min_interval
from src.db.podcast_repo import PodcastRepository
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository
from src.interaction.track import InteractionTrackEngine
from src.listening.ladder import ListeningLadderEngine
from src.llm.gemini_client import GeminiService, LLMUnavailableError, QuotaExceededError
from src.onboarding.diagnosis import OnboardingDiagnosisEngine
from src.phase3.engine import Phase3Engine
from src.podcast.pipeline import PodcastPipeline
from src.pressure.engine import PressureTestEngine
from src.prosody.lab import ProsodyLabEngine
from src.naturalness import NaturalnessGauge
from src.quality.guard import QualityGuard
from src.register.engine import RegisterEngine
from src.session.daily_manager import DailySessionManager
from src.session.reentry import ReEntrySessionManager
from src.tracking.progress import ProgressTracker
from src.bot.onboarding_handlers import format_profile_summary, route_learner_turn
from src.bot import voice_io
from src.bot.voice_io import deliver_prepared_voice, send_alex_voice

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong on my side — please send that again in a moment."
VOICE_FALLBACK = "I couldn't process that voice note right now — please send that turn as text so we keep going."


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
        # encrypting the whole DB on every tiny write made each reply slower; coalesce to one export per 30 s
        if passphrase:
            set_snapshot_min_interval(30.0)
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
        self.responder = AlexResponder(
            self.scene_repo, self.llm, self.guard, nat_gauge=NaturalnessGauge(db_path=db_path)
        )
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

    def flush(self) -> None:
        """Write any coalesced state to the encrypted snapshot (background loop and shutdown)."""
        try:
            flush_pending_snapshots()
        except Exception:  # noqa: BLE001
            logger.exception("snapshot flush failed")

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
            conn.execute("DELETE FROM nat_turn_log WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM nat_heard WHERE user_id = ?", (user_id,))
            conn.commit()
        return self.profile_repo.delete_user_data(user_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global safety net: any handler that raises still gives the learner a visible answer."""
    err = context.error
    try:
        from telegram.error import Conflict, NetworkError, TimedOut

        if isinstance(err, Conflict):
            logger.error("Telegram Conflict: another instance is polling this bot token (overlapping runner?)")
            return
        if isinstance(err, (NetworkError, TimedOut)) and not isinstance(update, Update):
            logger.warning("Transient Telegram network error: %s", err)
            return
    except ImportError:
        pass
    logger.error("Unhandled error while processing update", exc_info=err)
    if not isinstance(update, Update):
        return
    msg = update.effective_message
    if not msg:
        return
    text = str(err) if isinstance(err, (QuotaExceededError, LLMUnavailableError)) else GENERIC_ERROR
    try:
        await msg.reply_text(text)
    except Exception:  # noqa: BLE001
        logger.warning("Could not deliver error notice to the learner")


def register_all_modules(app: Application, container: LabContainer) -> None:
    """Single clean registration call wiring all 8 sprints into Telegram Application."""
    register_alex_handlers(app, container.scene_repo, container.responder, container.reg_engine, container.guard)
    register_analyst_handlers(app, container.profile_repo, container.analyst)
    register_session_handlers(app, container.chunk_repo, container.chunk_bank, container.session_mgr)
    register_pressure_and_tracking_handlers(app, container.profile_repo, container.pressure_engine, container.tracker)
    register_podcast_handlers(
        app, container.podcast_repo, container.podcast_pipeline, container.listening_ladder, profile_repo=container.profile_repo
    )
    register_interaction_and_reentry_handlers(app, container.interaction_engine, container.reentry_mgr)
    register_phase3_handlers(app, container.profile_repo, container.prosody_lab, container.phase3_engine)
    register_interactive_ui(app, container)
    register_core_turn_and_privacy_handlers(app, container)
    app.add_error_handler(on_error)


PRIVACY_TEXT = (
    "🔒 Your privacy in Spoken English Lab\n"
    "• Stored: your learning profile, practice history, expression progress and ratings — tied only to your Telegram ID.\n"
    "• Voice notes are processed in memory and are not kept; Alex's voice replies are generated on the fly.\n"
    "• Your text/voice is sent to Google's Gemini API to generate replies and analysis.\n"
    "• Nobody in the group can see your records; there is no leaderboard.\n"
    "• You can erase everything at any time with /delete_everything (asks for confirmation)."
)


def register_core_turn_and_privacy_handlers(app: Application, container: LabContainer) -> None:
    async def _typing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if update.effective_chat:
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _typing_pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await _typing(update, context)
        stop = asyncio.Event()

        async def _beat() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    await _typing(update, context)

        beat_task = asyncio.create_task(_beat())
        try:
            yield
        finally:
            stop.set()
            beat_task.cancel()
            try:
                await beat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _deliver(update: Update, uid: int, replies: list[str]) -> None:
        from src.bot.ui_buttons import turn_action_keyboard
        turn_no = container.responder._user_turn_counts.get(uid, 1)
        profile = container.profile_repo.get_or_create_profile(uid)
        audio_task = None
        replies = [r.strip() for r in replies if isinstance(r, str) and r.strip()]
        if not replies:
            replies = ["Got it, makes sense."]
        if profile.voice_replies and replies:
            # start synthesizing now so the voice note is ready by the time the text has been sent
            audio_task = asyncio.create_task(voice_io.synthesize_mp3_bytes(" ".join(replies)))
        try:
            for idx, r in enumerate(replies):
                is_last = (idx == len(replies) - 1)
                kb = turn_action_keyboard(f"{uid}:{turn_no}") if is_last else None
                await update.message.reply_text(r, reply_markup=kb)
        except BaseException:
            if audio_task:
                audio_task.cancel()
            raise
        if audio_task:
            await deliver_prepared_voice(update.message, audio_task)

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        p = container.profile_repo.get_or_create_profile(update.effective_user.id)
        mode = "Active Practice" if p.onboarding_completed else f"Onboarding Session {p.onboarding_session}/3"
        await update.message.reply_text(
            f"Hey! I'm Alex ({mode}). Send a voice note or text message anytime to jump right in."
        )

    async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        p = container.profile_repo.get_or_create_profile(update.effective_user.id)
        await update.message.reply_text(format_profile_summary(p))

    async def voice_toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        p = container.profile_repo.get_or_create_profile(update.effective_user.id)
        arg = (context.args[0].lower() if context.args else "")
        p.voice_replies = (arg == "on") if arg in ("on", "off") else (not p.voice_replies)
        container.profile_repo.save_profile(p)
        await update.message.reply_text(
            "🔊 Alex will now answer with voice notes." if p.voice_replies
            else "🔇 Voice replies are off. Alex will reply with text only (use /voice to turn them back on)."
        )

    async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Delete everything", callback_data="priv:ask")]])
        await update.message.reply_text(PRIVACY_TEXT, reply_markup=kb)

    async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await _ask_delete_confirmation(update.message)

    async def _ask_delete_confirmation(message) -> None:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, erase everything", callback_data="priv:confirm"),
            InlineKeyboardButton("↩️ Cancel", callback_data="priv:cancel"),
        ]])
        await message.reply_text(
            "This permanently deletes your profile, history, progress and ratings. It cannot be undone. Are you sure?",
            reply_markup=kb,
        )

    async def privacy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data or not update.effective_user:
            return
        await query.answer()
        action = query.data.split(":", 1)[1] if ":" in query.data else ""
        if action == "ask":
            await _ask_delete_confirmation(query.message)
        elif action == "confirm":
            deleted = container.purge_all_user_data(update.effective_user.id)
            await query.message.reply_text(
                "All your records have been permanently erased." if deleted else "No stored data was found for you."
            )
        else:
            await query.message.reply_text("Cancelled — nothing was deleted.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        uid = update.effective_user.id
        await _typing(update, context)
        async def _keep_typing() -> None:
            while True:
                await asyncio.sleep(4.0)
                await _typing(update, context)
        typing_task = asyncio.create_task(_keep_typing())
        try:
            replies = await route_learner_turn(container, uid, update.message.text)
            typing_task.cancel()
            await _deliver(update, uid, replies)
        except (QuotaExceededError, LLMUnavailableError) as exc:
            await update.message.reply_text(str(exc))
        except Exception:  # noqa: BLE001 - never leave the learner without an answer
            logger.exception("Text turn failed for user %s", uid)
            await update.message.reply_text(GENERIC_ERROR)
        finally:
            typing_task.cancel()

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.voice:
            return
        uid = update.effective_user.id
        await _typing(update, context)
        try:
            tg_file = await update.message.voice.get_file()
            raw_bytes = bytes(await tg_file.download_as_bytearray())  # memory-only: never saved to disk
            transcript = await container.llm.transcribe_voice(
                uid, raw_bytes, update.message.voice.mime_type or "audio/ogg"
            )
        except QuotaExceededError as exc:
            await update.message.reply_text(str(exc))
            return
        except Exception:  # noqa: BLE001
            logger.exception("Voice transcription failed for user %s", uid)
            await update.message.reply_text(VOICE_FALLBACK)
            return
        try:
            await update.message.reply_text(f"🎙️ You said: {transcript}")
            replies = await route_learner_turn(container, uid, transcript, voice_mode=True)
            await _deliver(update, uid, replies)
        except (QuotaExceededError, LLMUnavailableError) as exc:
            await update.message.reply_text(str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Voice turn failed for user %s", uid)
            await update.message.reply_text(GENERIC_ERROR)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("voice", voice_toggle_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler(["delete_my_data", "delete_everything"], delete_cmd))
    app.add_handler(CallbackQueryHandler(privacy_callback, pattern=r"^priv:"))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

