from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.alex.responder import AlexResponder
from src.db.scene_repo import SceneRepository
from src.quality.guard import QualityGuard
from src.register.engine import PRESET_ROOMS, RegisterEngine


def register_alex_handlers(
    app: Application,
    scene_repo: SceneRepository,
    responder: AlexResponder,
    reg_engine: RegisterEngine,
    guard: QualityGuard | None = None,
) -> None:
    quality_guard = guard or QualityGuard()

    async def room_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        args = context.args or []
        if not args:
            current = scene_repo.get_active_scene(uid)
            rooms_list = ", ".join(PRESET_ROOMS.keys())
            await update.message.reply_text(
                f"Active Scene: {current.room_id} ({current.setting} | {current.relationship} | "
                f"directness={current.directness} | flavor={current.flavor})\n"
                f"Available rooms: {rooms_list}\nUsage: /room <room_id>"
            )
            return
        room_id = args[0].strip()
        scene = reg_engine.create_room_scene(room_id=room_id)
        scene_repo.save_active_scene(uid, scene)
        await update.message.reply_text(
            f"Switched to room '{scene.room_id}': {scene.setting} ({scene.relationship}), "
            f"directness={scene.directness}, flavor={scene.flavor}."
        )

    async def switch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        current = scene_repo.get_active_scene(uid)
        args = context.args or []
        mode_arg = args[0].lower() if args else "a"
        mode_map = {
            "a": "a_bot_scene_change",
            "b": "b_learner_requested",
            "c": "c_mid_conversation_shift",
        }
        mode = mode_map.get(mode_arg, "a_bot_scene_change")
        new_setting = args[1] if len(args) > 1 else ("professional" if current.setting != "professional" else "casual")
        new_rel = "hierarchical" if new_setting == "professional" else "equal"

        event = reg_engine.switch_scene(
            current=current,
            mode=mode,  # type: ignore[arg-type]
            new_setting=new_setting,  # type: ignore[arg-type]
            new_relationship=new_rel,  # type: ignore[arg-type]
        )
        scene_repo.save_active_scene(uid, event.new_scene)
        await update.message.reply_text(
            f"Scene Switched ({event.mode}) -> {event.new_scene.setting}/{event.new_scene.relationship} "
            f"(directness={event.new_scene.directness}, max evidence={event.max_evidence_level})."
        )

    async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        scene = scene_repo.get_active_scene(uid)
        turns = scene_repo.get_recent_turns(uid, role="alex", limit=10)
        res = quality_guard.audit_turns(turns, scene)
        status = "PASSED" if res.passed else f"FAILED ({'; '.join(res.reasons)})"
        await update.message.reply_text(
            f"Quality Guard Audit (last {len(turns)} Alex turns):\n"
            f"• Status: {status}\n"
            f"• Question Ratio: {res.question_ratio}"
        )

    app.add_handler(CommandHandler("room", room_cmd))
    app.add_handler(CommandHandler("switch", switch_cmd))
    app.add_handler(CommandHandler("audit", audit_cmd))
