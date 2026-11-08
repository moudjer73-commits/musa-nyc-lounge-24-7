import logging
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from src.db.podcast_repo import PodcastRepository
from src.listening.ladder import ListeningLadderEngine
from src.models.listening_podcast import LearnerPodcastState, PodcastEpisodeRecord
from src.podcast.pipeline import PodcastPipeline

logger = logging.getLogger(__name__)

AUDIO_UNAVAILABLE = "🔇 The audio couldn't be generated right now (the free voice service is unreachable). Please try again in a bit."


def build_podcast_keyboard(episode_id: str, state: LearnerPodcastState) -> InlineKeyboardMarkup:
    transcript_label = "Transcript (Unlocked)" if state.transcript_visible else "Transcript (Answer Gist & Detail First)"
    buttons = [
        [
            InlineKeyboardButton("Replay", callback_data=f"pod:replay:{episode_id}"),
            InlineKeyboardButton("Slower (0.85x)", callback_data=f"pod:slower:{episode_id}"),
        ],
        [
            InlineKeyboardButton("Answer Gist & Detail", callback_data=f"pod:questions:{episode_id}"),
            InlineKeyboardButton(transcript_label, callback_data=f"pod:transcript:{episode_id}"),
        ],
        [
            InlineKeyboardButton("Shadow 15s", callback_data=f"pod:shadow:{episode_id}"),
            InlineKeyboardButton("Reply by Voice (3rd Speaker)", callback_data=f"pod:voice:{episode_id}"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def handle_podcast_button_action(
    repo: PodcastRepository, episode: PodcastEpisodeRecord, user_id: int, action: str
) -> tuple[str, LearnerPodcastState]:
    state = repo.get_or_create_learner_state(user_id, episode.episode_id)
    state.first_listen_done = True

    if action == "replay":
        state.replayed_count += 1
        msg = f"[{episode.label}] Replaying episode ({state.replayed_count}x)."
    elif action == "slower":
        state.speed_used = "0.85x"
        msg = f"[{episode.label}] Playing slower version (0.85x)."
    elif action == "questions":
        state.gist_answered = True
        state.detail_answered = True
        state.question_accuracy = 1.0
        state.step = "transcript_unlocked"
        msg = (
            f"Gist Question: {episode.script.gist_question}\n"
            f"Detail Question: {episode.script.detail_question}\n"
            "(Questions completed! Transcript is now unlocked.)"
        )
    elif action == "transcript":
        if not state.transcript_visible:
            msg = "Transcript is hidden until you finish the first listen and answer the Gist & Detail questions!"
        else:
            lines = "\n".join(f"{t.speaker}: {t.text}" for t in episode.script.turns)
            msg = f"[{episode.label} Transcript]\n{lines}"
    elif action == "shadow":
        state.shadowing_attempted = True
        state.step = "shadow_15s"
        msg = f"Shadowing 15-Second Segment (Repeat out loud matching rhythm):\n\"{episode.script.shadow_segment_15s}\""
    else:
        state.voice_reply_submitted = True
        state.step = "voice_reply_third_speaker"
        msg = "Send a voice note now jumping in as the 3rd speaker to give your take!"

    repo.save_learner_state(state)
    return msg, state


async def ensure_episode_audio(pipeline: PodcastPipeline, episode: PodcastEpisodeRecord, slow: bool = False) -> Path | None:
    """Return a real speech MP3 for the episode (normal or 0.85x-style slow), re-rendering if the file is gone.

    Returns None when only the offline beep fallback is available: we never send a beep as if it were a podcast.
    """
    base = Path(episode.audio_path)
    mp3 = base.with_name(f"{base.stem}_slow.mp3") if slow else base.with_suffix(".mp3")
    if mp3.exists() and mp3.stat().st_size > 0:
        return mp3
    try:
        path, _provider = await pipeline.tts.render_episode_audio(
            episode.script.turns, mp3, episode.voice_settings, slow=slow
        )
    except Exception:  # noqa: BLE001
        logger.exception("Podcast audio render failed for %s", episode.episode_id)
        return None
    if Path(path).suffix.lower() != ".mp3":  # offline sine-wave fallback is not speech
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return Path(path)


async def send_episode_audio(message, audio: Path, episode: PodcastEpisodeRecord, caption: str, markup) -> None:
    with open(audio, "rb") as fh:
        await message.reply_audio(
            audio=fh,
            title=f"{episode.script.register_label.title()} · {episode.script.focus_function}",
            performer="Spoken English Lab (AI-generated)",
            caption=caption[:1000],
            reply_markup=markup,
        )


def register_podcast_handlers(
    app: Application,
    repo: PodcastRepository,
    pipeline: PodcastPipeline,
    ladder: ListeningLadderEngine,
    profile_repo=None,
) -> None:
    async def podcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        register = "professional"
        if profile_repo is not None:  # pick the register the learner's goal weights favour most
            weights = profile_repo.get_or_create_profile(uid).register_weights
            if weights:
                register = max(weights, key=weights.get)
        try:
            await context.bot.send_chat_action(update.effective_chat.id, "upload_voice")
        except Exception:  # noqa: BLE001
            pass
        ep = await pipeline.get_or_create_episode(
            user_id=uid,
            register=register,  # type: ignore[arg-type]
            function="disagree",
            level="L2",
            target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
            personal=False,
        )
        state = repo.get_or_create_learner_state(uid, ep.episode_id)
        kb = build_podcast_keyboard(ep.episode_id, state)
        caption = (
            f"[{ep.label}] Spoken English Lab Podcast ({ep.script.register_label} | {ep.script.level})\n"
            "Step 1: Listen first without transcript, then answer Gist & Detail to unlock the text."
        )
        audio = await ensure_episode_audio(pipeline, ep)
        if audio:
            await send_episode_audio(update.message, audio, ep, caption, kb)
        else:
            await update.message.reply_text(f"{caption}\n\n{AUDIO_UNAVAILABLE}", reply_markup=kb)

    async def curated_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        lines = ["Curated Real American Podcasts (External Links Only):"]
        for item in ladder.curated_links:
            lines.append(f"• {item['title']} ({item['level']} | {item['register']}): {item['url']}")
        await update.message.reply_text("\n".join(lines))

    async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data or not update.effective_user:
            return
        await query.answer()
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            return
        _, action, ep_id = parts
        ep = repo.get_episode_by_id(ep_id)
        if not ep:
            await query.message.reply_text("That episode is no longer available — send /podcast for a fresh one.")
            return
        msg, state = handle_podcast_button_action(repo, ep, update.effective_user.id, action)
        kb = build_podcast_keyboard(ep_id, state)
        if action in ("replay", "slower"):
            audio = await ensure_episode_audio(pipeline, ep, slow=(action == "slower"))
            if audio:
                await send_episode_audio(query.message, audio, ep, msg, kb)
            else:
                await query.message.reply_text(f"{msg}\n\n{AUDIO_UNAVAILABLE}", reply_markup=kb)
            return
        await query.message.reply_text(msg, reply_markup=kb)

    app.add_handler(CommandHandler("podcast", podcast_cmd))
    app.add_handler(CommandHandler("curated", curated_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^pod:"))
