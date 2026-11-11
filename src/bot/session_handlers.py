from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.chunks.scheduler import ChunkBank
from src.db.chunk_repo import ChunkRepository
from src.session.daily_manager import DailySessionManager


def register_session_handlers(
    app: Application,
    chunk_repo: ChunkRepository,
    bank: ChunkBank,
    session_mgr: DailySessionManager,
) -> None:
    async def session_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        args = context.args or []
        if args and args[0].lower() == "next":
            phase, desc = session_mgr.advance_phase(uid)
            await update.message.reply_text(f"[{phase}]\n{desc}")
            return

        day_num = max(1, min(365, int(args[0]))) if (args and args[0].lstrip("-").isdigit()) else 1
        state = session_mgr.start_session(uid, day_number=day_num)
        chunks_preview = (
            ", ".join(f'"{bank.get_chunk(cid).phrase}"' for cid in state.target_chunk_ids)
            if state.show_targets_to_learner
            else "(Hidden for unprompted retrieval)"
        )
        await update.message.reply_text(
            f"10-Min Daily Session Started ({state.day_code}: {state.day_focus})\n"
            f"• Interactional Situation: {state.interactional_situation}\n"
            f"• Active Phase: {state.current_phase} (Total planned: {state.planned_minutes} mins)\n"
            f"• Target Chunks: {chunks_preview}\n"
            "Use `/session next` to advance through the 6 stages."
        )

    async def review_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        due = chunk_repo.get_due_records(uid)
        if not due:
            await update.message.reply_text("No chunks due for review right now!")
            return
        rec = due[0]
        new_idx, prompt_text, _ = bank.get_varied_prompt(rec.chunk_id, rec.last_prompt_idx)
        rec.last_prompt_idx = new_idx
        chunk_repo.save_record(rec)
        await update.message.reply_text(
            f"Retrieval Review ({rec.context_key} | Current Level: {rec.e_level}):\n"
            f"Situation Prompt: {prompt_text}\n"
            "(Respond naturally in a full spoken sentence without looking at notes!)"
        )

    async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        args = context.args or []
        hour = max(0, min(23, int(args[0]))) if (args and args[0].lstrip("-").isdigit()) else 18
        chunk_repo.configure_reminder(uid, hour_utc=hour, enabled=True)
        await update.message.reply_text(f"Daily practice reminder set for {hour:02d}:00 UTC.")

    app.add_handler(CommandHandler("session", session_cmd))
    app.add_handler(CommandHandler("review", review_cmd))
    app.add_handler(CommandHandler("remind", remind_cmd))
