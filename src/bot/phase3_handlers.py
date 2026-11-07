from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.db.profile_repo import ProfileRepository
from src.phase3.engine import Phase3Engine
from src.prosody.lab import ProsodyLabEngine


def format_full_eight_dimensions(profile) -> str:
    f = profile.full_dimensions
    return (
        "Full 8-Dimension Learner Profile (0–4 Scale):\n"
        f"1. Lexicon: {f.lexicon.score} ({f.lexicon.confidence})\n"
        f"2. Grammar: {f.grammar.score} ({f.grammar.confidence})\n"
        f"3. Fluency & Retrieval: {f.fluency_retrieval.score} ({f.fluency_retrieval.confidence})\n"
        f"4. Pronunciation: {f.pronunciation.score} ({f.pronunciation.confidence})\n"
        f"5. Listening: {f.listening.score} ({f.listening.confidence})\n"
        f"6. Pragmatics: {f.pragmatics.score} ({f.pragmatics.confidence})\n"
        f"7. Interaction: {f.interaction.score} ({f.interaction.confidence})\n"
        f"8. Register Control: {f.register_control.score} ({f.register_control.confidence})"
    )


def register_phase3_handlers(
    app: Application,
    profile_repo: ProfileRepository,
    prosody_lab: ProsodyLabEngine,
    phase3_engine: Phase3Engine,
) -> None:
    async def prosody_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = profile_repo.get_or_create_profile(update.effective_user.id)
        block = prosody_lab.build_5min_prosody_block(profile, include_recognition_items=True)
        drills_txt = "\n".join(
            f"• [{ 'RECOGNITION ONLY' if d.recognition_only else 'PRODUCTION' }] {d.title}: \"{d.minimal_pair_or_prompt}\""
            for d in block.active_drills
        )
        await update.message.reply_text(
            f"Prosody Lab (Separate {block.duration_minutes}-Min Block | Ambition: {block.ambition})\n"
            f"{drills_txt}\n\nNote: {block.feedback_disclaimer}"
        )

    async def full_profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = profile_repo.get_or_create_profile(update.effective_user.id)
        phase3_engine.sync_full_eight_dimensions(profile)
        await update.message.reply_text(format_full_eight_dimensions(profile))

    async def humor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        args = context.args or []
        stage = args[0].upper() if args else "S1"
        ex = phase3_engine.get_humor_exercise(stage=stage, requested_mode="production")  # type: ignore[arg-type]
        await update.message.reply_text(
            f"Humor Training ({ex.mode.upper()} | Stage {stage}):\n"
            f"• Prompt: {ex.prompt_text}\n• Coaching: {ex.explanation}"
        )

    async def group_milestone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        m = phase3_engine.get_group_aggregate_milestone()
        await update.message.reply_text(
            f"{m.milestone_name} (No Leaderboard — Private Records Protected)\n"
            f"• Active Friends in Lab: {m.total_active_learners}\n"
            f"• Collective Sessions Completed: {m.collective_sessions_completed}\n"
            f"• Collective E3/E4 Chunks Activated: {m.collective_e3_targets_reached}"
        )

    async def rate_correction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        args = context.args or []
        rating = int(args[0]) if (args and args[0].isdigit()) else 5
        phase3_engine.record_correction_rating(uid, rating)
        metrics = phase3_engine.get_pilot_metrics(uid)
        await update.message.reply_text(
            f"Recorded rating: {rating}/5.\n"
            f"Pilot Tracker: {metrics.days_of_use} day(s) used | "
            f"{metrics.targets_reaching_e3} E3+ targets | Avg Usefulness: {metrics.avg_correction_usefulness_rating}/5"
        )

    app.add_handler(CommandHandler("prosody", prosody_cmd))
    app.add_handler(CommandHandler("full_profile", full_profile_cmd))
    app.add_handler(CommandHandler("humor", humor_cmd))
    app.add_handler(CommandHandler("group_milestone", group_milestone_cmd))
    app.add_handler(CommandHandler("rate_correction", rate_correction_cmd))
