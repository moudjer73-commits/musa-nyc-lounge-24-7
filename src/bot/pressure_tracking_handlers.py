from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.db.profile_repo import ProfileRepository
from src.pressure.engine import PressureTestEngine
from src.tracking.progress import FIXED_UNSEEN_SPEAKING_TASK, ProgressTracker


def format_private_progress(report) -> str:
    comp = ", ".join(f"{k}: {v:.1f}" for k, v in report.current_competence.items())
    growth = ", ".join(f"{k}: {v:+.1f}" for k, v in report.growth_vs_baseline.items())
    ret = report.retention_counts
    delta_str = f"{report.benchmark_delta:+.2f}" if report.benchmark_delta is not None else "Pending Week 4/8 task"
    return (
        "Private Progress Report (No Leaderboard)\n"
        f"• Current Competence: {comp}\n"
        f"• Growth vs. Baseline: {growth}\n"
        f"• Targets at E3/E4: {report.e3_plus_total}\n"
        f"• Retention Status: active={ret.get('active', 0)}, retained={ret.get('retained', 0)}, "
        f"weakened={ret.get('weakened', 0)}\n"
        f"• Fixed Benchmark Before/After Delta: {delta_str}"
    )


def register_pressure_and_tracking_handlers(
    app: Application,
    profile_repo: ProfileRepository,
    pressure_engine: PressureTestEngine,
    tracker: ProgressTracker,
) -> None:
    async def pressure_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = profile_repo.get_or_create_profile(update.effective_user.id)
        challenge = pressure_engine.start_90s_monologue(profile)
        await update.message.reply_text(
            f"Weekly Pressure Test — 90-Second Unscaffolded Monologue\n"
            f"• Topic: {challenge.topic}\n"
            f"• Target: {challenge.duration_seconds}s (~{challenge.min_words_threshold}+ words)\n"
            "Speak or type your monologue now, or send `/skip_topic` to switch to another neutral topic."
        )

    async def skip_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = profile_repo.get_or_create_profile(update.effective_user.id)
        challenge = pressure_engine.skip_monologue_topic(profile)
        await update.message.reply_text(f"Topic skipped! New neutral topic:\n• {challenge.topic}")

    async def progress_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        report = tracker.build_progress_report(update.effective_user.id)
        await update.message.reply_text(format_private_progress(report))

    async def benchmark_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await update.message.reply_text(
            "Fixed Unseen Speaking Benchmark (Day 0 / Week 4 / Week 8):\n"
            f"{FIXED_UNSEEN_SPEAKING_TASK}"
        )

    app.add_handler(CommandHandler("pressure", pressure_cmd))
    app.add_handler(CommandHandler("skip_topic", skip_topic_cmd))
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(CommandHandler("benchmark", benchmark_cmd))
