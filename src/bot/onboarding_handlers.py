from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from src.db.profile_repo import ProfileRepository
from src.llm.gemini_client import QuotaExceededError
from src.models.profile import LearnerProfile
from src.onboarding.diagnosis import OnboardingDiagnosisEngine


def format_profile_summary(profile: LearnerProfile) -> str:
    mvp = profile.mvp_dimensions
    weights = ", ".join(f"{k}: {v}%" for k, v in profile.register_weights.items())
    status = "Completed" if profile.onboarding_completed else f"Session {profile.onboarding_session}/3"
    return (
        f"Spoken English Lab — Learner Profile\n"
        f"• Onboarding Status: {status}\n"
        f"• Goal: {profile.goal} ({weights})\n"
        f"• Dialect Hypothesis: {profile.dialect.dialect_name} "
        f"[{profile.dialect.status} | {profile.dialect.confidence}]\n"
        f"• Correction Style: {profile.correction_style} | Arabic Notes: {profile.arabic_support}\n\n"
        f"MVP Dimensions (0–4 scale):\n"
        f"1. Retrieval Independence: {mvp.retrieval_independence.score} ({mvp.retrieval_independence.confidence})\n"
        f"2. Linguistic Quality: {mvp.linguistic_quality.score} ({mvp.linguistic_quality.confidence})\n"
        f"3. Interaction: {mvp.interaction.score} ({mvp.interaction.confidence})\n"
        f"4. Register x Function: {mvp.register_x_function.score} ({mvp.register_x_function.confidence})"
    )


def register_onboarding(app: Application, repo: ProfileRepository, engine: OnboardingDiagnosisEngine) -> None:
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = repo.get_or_create_profile(update.effective_user.id)
        await update.message.reply_text(
            f"Hey! I'm Alex. Welcome to Spoken English Lab (Session {profile.onboarding_session}/3).\n"
            "No tests here—just tell me a bit about what you've been working on or into lately."
        )

    async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        profile = repo.get_or_create_profile(update.effective_user.id)
        await update.message.reply_text(format_profile_summary(profile))

    async def delete_data_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        deleted = repo.delete_user_data(update.effective_user.id)
        msg = "All your profile data and logs have been permanently deleted." if deleted else "No stored data found."
        await update.message.reply_text(msg)

    async def text_turn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        try:
            reply, _ = await engine.process_turn(update.effective_user.id, update.message.text)
            await update.message.reply_text(reply)
        except QuotaExceededError as exc:
            await update.message.reply_text(str(exc))

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("delete_my_data", delete_data_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_turn_handler))
