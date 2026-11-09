from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from src.bot.analyst_handlers import handle_analyzed_conversation_turn
from src.llm.gemini_client import GeminiService, QuotaExceededError
from src.models.profile import LearnerProfile


def _is_live_gemini(llm) -> bool:
    return isinstance(llm, GeminiService) and bool(getattr(llm, "clients", None))


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


async def route_learner_turn(container, user_id: int, learner_text: str) -> list[str]:
    profile = container.profile_repo.get_or_create_profile(user_id)
    if not profile.onboarding_completed and _is_live_gemini(container.llm):
        profile.onboarding_completed = True
        container.profile_repo.save_profile(profile)

    if not profile.onboarding_completed:
        reply, updated = await container.onboarding_engine.process_turn(user_id, learner_text)
        msgs = [reply]
        if updated.onboarding_completed:
            msgs.append("Onboarding diagnosis complete! Use /profile to view your initial calibration.")
        return msgs

    session_id = f"session_{user_id}_active"
    return await handle_analyzed_conversation_turn(
        user_id=user_id,
        session_id=session_id,
        learner_text=learner_text,
        profile_repo=container.profile_repo,
        scene_repo=container.scene_repo,
        analyst=container.analyst,
        responder=container.responder,
    )
