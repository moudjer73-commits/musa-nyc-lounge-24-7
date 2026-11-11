from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.interaction.track import InteractionTrackEngine
from src.session.reentry import ReEntrySessionManager


def register_interaction_and_reentry_handlers(
    app: Application,
    interaction_engine: InteractionTrackEngine,
    reentry_mgr: ReEntrySessionManager,
) -> None:
    async def story_check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        text = " ".join(context.args or [])
        if not text:
            await update.message.reply_text("Usage: /story_check <paste or dictate your short story>")
            return
        diag = interaction_engine.diagnose_storytelling(text)
        await update.message.reply_text(
            f"Storytelling Diagnostic Frame:\n"
            f"• Orientation: {diag.has_orientation} | Complication: {diag.has_complication}\n"
            f"• Development: {diag.has_development} | Evaluation: {diag.has_evaluation} | Resolution: {diag.has_resolution}\n"
            f"• Note: {diag.diagnostic_note}"
        )

    async def reentry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        args = context.args or []
        if args and args[0].lower() == "next":
            block, desc = reentry_mgr.advance_block(uid)
            await update.message.reply_text(f"[{block}]\n{desc}")
            return

        days = max(1, min(365, int(args[0]))) if (args and args[0].lstrip("-").isdigit()) else 12
        state = reentry_mgr.start_reentry_session(uid, days_away=days)
        await update.message.reply_text(
            f"10-Min Re-Entry Session Started ({state.days_away} days away)\n"
            f"• Planned Time: {state.total_planned_minutes} mins (2m talk > 3m retrieval > 3m skill+target > 2m comparison)\n"
            f"• Current Block: {state.current_block}\n"
            "Use `/reentry next` to step through the reactivation blocks."
        )

    app.add_handler(CommandHandler("story_check", story_check_cmd))
    app.add_handler(CommandHandler("reentry", reentry_cmd))
