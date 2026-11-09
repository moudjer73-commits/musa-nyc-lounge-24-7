from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.alex.responder import AlexResponder
from src.analyst.engine import AnalystEngine
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository


async def handle_analyzed_conversation_turn(
    user_id: int,
    session_id: str,
    learner_text: str,
    profile_repo: ProfileRepository,
    scene_repo: SceneRepository,
    analyst: AnalystEngine,
    responder: AlexResponder,
) -> list[str]:
    if getattr(responder.llm, "configured", False):
        alex_reply, _, _ = await responder.reply(user_id=user_id, learner_text=learner_text)
        segments = getattr(responder, "last_segments", {}).get(user_id) or [alex_reply]
        return list(segments)
    """Coordinates Analyst + Alex per turn without ever exposing raw Analyst JSON."""
    profile = profile_repo.get_or_create_profile(user_id)
    scene = scene_repo.get_active_scene(user_id)
    decision = await analyst.analyze_turn(profile, scene, session_id, learner_text)

    messages: list[str] = []
    if decision.action == "immediate_meaning_repair" and decision.learner_feedback_message:
        messages.append(decision.learner_feedback_message)
        if decision.retry_prompt:
            messages.append(decision.retry_prompt)
        return messages

    alex_reply, _, _ = await responder.reply(
        user_id=user_id,
        learner_text=learner_text,
        recast_phrase=decision.recast_phrase_for_alex,
    )
    messages.append(alex_reply)

    if decision.visible_to_learner and decision.learner_feedback_message:
        messages.append(f"{decision.learner_feedback_message}\n{decision.retry_prompt or ''}".strip())

    if decision.arabic_post_turn_note:
        messages.append(f"ملاحظة سياقية: {decision.arabic_post_turn_note}")

    return messages


def register_analyst_handlers(
    app: Application,
    profile_repo: ProfileRepository,
    analyst: AnalystEngine,
) -> None:
    async def hint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /hint <concept or word> (add 'ar' at the end if you need Arabic)")
            return
        ask_ar = args[-1].lower() == "ar" and len(args) > 1
        target = " ".join(args[:-1] if ask_ar else args)
        profile = profile_repo.get_or_create_profile(update.effective_user.id)
        step_name, hint_text = await analyst.get_next_hint(profile, target, ask_arabic=ask_ar)
        await update.message.reply_text(f"[Hint — {step_name}]\n{hint_text}")

    async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        session_id = f"session_{uid}_active"
        text = analyst.format_end_of_session_summary(uid, session_id)
        await update.message.reply_text(text)

    app.add_handler(CommandHandler("hint", hint_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
