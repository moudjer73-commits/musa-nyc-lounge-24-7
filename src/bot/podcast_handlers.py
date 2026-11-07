from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from src.db.podcast_repo import PodcastRepository
from src.listening.ladder import ListeningLadderEngine
from src.models.listening_podcast import LearnerPodcastState, PodcastEpisodeRecord
from src.podcast.pipeline import PodcastPipeline


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


def register_podcast_handlers(
    app: Application,
    repo: PodcastRepository,
    pipeline: PodcastPipeline,
    ladder: ListeningLadderEngine,
) -> None:
    async def podcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        ep = await pipeline.get_or_create_episode(
            user_id=uid,
            register="professional",
            function="disagree",
            level="L2",
            target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
            personal=False,
        )
        state = repo.get_or_create_learner_state(uid, ep.episode_id)
        kb = build_podcast_keyboard(ep.episode_id, state)
        await update.message.reply_text(
            f"[{ep.label}] Spoken English Lab Podcast ({ep.script.register_label} | {ep.script.level})\n"
            "Step 1: Listen first without transcript, then answer Gist & Detail to unlock the text.",
            reply_markup=kb,
        )

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
        _, action, ep_id = query.data.split(":", 2)
        ep = repo.get_episode_by_id(ep_id)
        if not ep:
            return
        msg, state = handle_podcast_button_action(repo, ep, update.effective_user.id, action)
        await query.message.reply_text(msg, reply_markup=build_podcast_keyboard(ep_id, state))

    app.add_handler(CommandHandler("podcast", podcast_cmd))
    app.add_handler(CommandHandler("curated", curated_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^pod:"))
