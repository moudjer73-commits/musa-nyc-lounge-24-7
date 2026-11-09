from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from src.bot.telegram_setup import clear_stale_chat_commands
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

ROOM_CHOICES: list[tuple[str, str]] = [
    ("casual_friends", "☕ Casual — Friends Catch-Up (Sam)"),
    ("casual_roommate", "🏠 Casual — Evening Chat (Maya)"),
    ("social_networking", "🤝 Conference — Networking Mixer (Prof. Ruiz)"),
    ("pro_peer_brainstorm", "💡 Work — Team Brainstorm (Sam)"),
    ("pro_executive", "💼 Work — Executive Briefing (Dana)"),
    ("intellectual_seminar", "🎓 Study — Seminar Debate (Prof. Ruiz)"),
    ("intellectual_professor", "📚 Study — Office Hours (Prof. Ruiz)"),
]


class _MessageWithMarkup:
    def __init__(self, msg, default_markup=None) -> None:
        self._msg = msg
        self._markup = default_markup

    async def reply_text(self, text: str, **kwargs):
        if "reply_markup" not in kwargs and self._markup is not None:
            kwargs["reply_markup"] = self._markup
        return await self._msg.reply_text(text, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._msg, name)


class _UpdateProxy:
    def __init__(self, update: Update, default_markup=None) -> None:
        self.effective_user = update.effective_user
        self.effective_chat = update.effective_chat
        self.callback_query = update.callback_query
        base_msg = update.effective_message or update.message
        self.message = _MessageWithMarkup(base_msg, default_markup) if base_msg else None


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚪 Scene & Partner", callback_data="ui:menu:room"),
            InlineKeyboardButton("🔄 Flip Register", callback_data="ui:menu:flip"),
        ],
        [
            InlineKeyboardButton("🎙️ 10-Min Session", callback_data="ui:cmd:session"),
            InlineKeyboardButton("🎧 Daily Podcast", callback_data="ui:cmd:podcast"),
        ],
        [
            InlineKeyboardButton("🗣️ Prosody Lab", callback_data="ui:cmd:prosody"),
            InlineKeyboardButton("⏱️ 90s Challenge", callback_data="ui:cmd:pressure"),
        ],
        [
            InlineKeyboardButton("🔄 Switch Tone", callback_data="ui:menu:switch"),
            InlineKeyboardButton("📚 Expressions", callback_data="ui:cmd:review"),
        ],
        [
            InlineKeyboardButton("🔊 Voice ON/OFF", callback_data="ui:cmd:voice"),
            InlineKeyboardButton("📊 Stats & Profile", callback_data="ui:cmd:progress"),
        ],
    ])


def turn_action_keyboard(msg_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Flip Register", callback_data="ui:menu:flip"),
            InlineKeyboardButton("🎭 Feels Artificial", callback_data=f"nat:flag:{msg_id}"),
        ]
    ])


def flip_choices_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☕ Close Friend", callback_data="ui:flip:friend_text"),
            InlineKeyboardButton("💼 Work Peer", callback_data="ui:flip:work_peer"),
        ],
        [
            InlineKeyboardButton("👔 Boss / Client", callback_data="ui:flip:work_up"),
            InlineKeyboardButton("🎓 Academic Seminar", callback_data="ui:flip:academic_seminar"),
        ],
    ])


def rooms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"ui:cmd:room:{room_id}")]
        for room_id, label in ROOM_CHOICES
    ])


def switch_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Casual (Smooth)", callback_data="ui:cmd:switch:a:casual"),
            InlineKeyboardButton("Social (Smooth)", callback_data="ui:cmd:switch:a:social"),
        ],
        [
            InlineKeyboardButton("Work (Quick Shift)", callback_data="ui:cmd:switch:b:professional"),
            InlineKeyboardButton("Study (Quick Shift)", callback_data="ui:cmd:switch:b:intellectual"),
        ],
        [
            InlineKeyboardButton("Work (High-Stakes)", callback_data="ui:cmd:switch:c:professional"),
            InlineKeyboardButton("Study (High-Stakes)", callback_data="ui:cmd:switch:c:intellectual"),
        ],
    ])


def session_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Next Step ➡️", callback_data="ui:cmd:session:next")]])


def reentry_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Next Warm-Up Block ➡️", callback_data="ui:cmd:reentry:next")]])


def pressure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Skip Topic 🔄", callback_data="ui:cmd:skip_topic")]])


def remind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🕗 08:00", callback_data="ui:cmd:remind:08:00"),
        InlineKeyboardButton("🕑 14:00", callback_data="ui:cmd:remind:14:00"),
        InlineKeyboardButton("🕖 19:00", callback_data="ui:cmd:remind:19:00"),
        InlineKeyboardButton("🕘 21:00", callback_data="ui:cmd:remind:21:00"),
    ]])


def hint_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 push back", callback_data="ui:cmd:hint:push_back"),
            InlineKeyboardButton("💡 elaborate", callback_data="ui:cmd:hint:elaborate"),
            InlineKeyboardButton("💡 compromise", callback_data="ui:cmd:hint:compromise"),
        ]
    ])


def rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{i} ⭐", callback_data=f"ui:cmd:rate_correction:{i}") for i in range(1, 6)
    ]])


def _find_command_callback(app: Application, cmd_name: str):
    for group_id, handlers in app.handlers.items():
        if group_id < 0:
            continue
        for h in handlers:
            if isinstance(h, CommandHandler) and cmd_name in h.commands:
                return h.callback
    return None


def register_interactive_ui(app: Application, container) -> None:
    async def run_delegated(cmd_name: str, update: Update, context: ContextTypes.DEFAULT_TYPE, markup=None) -> bool:
        cb = _find_command_callback(app, cmd_name)
        if not cb:
            target = update.effective_message
            if target:
                await target.reply_text(f"Sorry, /{cmd_name} isn't available right now.")
            return False
        proxy = _UpdateProxy(update, default_markup=markup)
        await cb(proxy, context)
        return True

    async def start_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        if update.effective_chat:
            await clear_stale_chat_commands(context.bot, update.effective_chat.id)
        p = container.profile_repo.get_or_create_profile(update.effective_user.id)
        if not p.onboarding_completed and getattr(container.llm, "configured", False):
            p.onboarding_completed = True
            container.profile_repo.save_profile(p)
        mode = "Active Practice" if p.onboarding_completed else f"Onboarding Session {p.onboarding_session}/3"
        await update.message.reply_text(
            f"Hey! I'm Alex ({mode}). Send a voice note or text message anytime, or use the quick menu below:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.message.reply_text("Quick Actions:", reply_markup=main_menu_keyboard())
        raise ApplicationHandlerStop

    async def room_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await run_delegated("room", update, context, markup=rooms_keyboard())
            raise ApplicationHandlerStop

    async def switch_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args and update.message:
            await update.message.reply_text("Choose a tone and situation to switch into:", reply_markup=switch_keyboard())
            raise ApplicationHandlerStop

    async def session_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await run_delegated("session", update, context, markup=session_next_keyboard())
        raise ApplicationHandlerStop

    async def pressure_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await run_delegated("pressure", update, context, markup=pressure_keyboard())
        raise ApplicationHandlerStop

    async def reentry_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await run_delegated("reentry", update, context, markup=reentry_next_keyboard())
        raise ApplicationHandlerStop

    async def remind_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args and update.message:
            await update.message.reply_text("Pick a daily reminder time (or type /remind HH:MM):", reply_markup=remind_keyboard())
            raise ApplicationHandlerStop

    async def hint_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args and update.message:
            await update.message.reply_text("Tap an expression to get a 3-step English clue (or type /hint <word>):", reply_markup=hint_keyboard())
            raise ApplicationHandlerStop

    async def rate_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args and update.message:
            await update.message.reply_text("How helpful were today's speaking tips?", reply_markup=rating_keyboard())
            raise ApplicationHandlerStop

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data or not update.effective_user:
            return
        await query.answer()
        parts = query.data.split(":")
        if len(parts) < 3:
            return

        if parts[1] == "menu" and parts[2] == "room":
            await query.message.reply_text("Choose a conversation room:", reply_markup=rooms_keyboard())
            return
        if parts[1] == "menu" and parts[2] == "switch":
            await query.message.reply_text("Choose a tone and situation to switch into:", reply_markup=switch_keyboard())
            return

        if parts[1] == "cmd":
            cmd_name = parts[2]
            context.args = parts[3:] if len(parts) > 3 else []
            markup = None
            if cmd_name == "session":
                markup = session_next_keyboard()
            elif cmd_name in ("pressure", "skip_topic"):
                markup = pressure_keyboard()
            elif cmd_name == "reentry":
                markup = reentry_next_keyboard()
            await run_delegated(cmd_name, update, context, markup=markup)

    app.add_handler(CommandHandler("start", start_ui_cmd), group=-1)
    app.add_handler(CommandHandler("room", room_ui_cmd), group=-1)
    app.add_handler(CommandHandler("switch", switch_ui_cmd), group=-1)
    app.add_handler(CommandHandler("session", session_ui_cmd), group=-1)
    app.add_handler(CommandHandler("pressure", pressure_ui_cmd), group=-1)
    app.add_handler(CommandHandler("reentry", reentry_ui_cmd), group=-1)
    app.add_handler(CommandHandler("remind", remind_ui_cmd), group=-1)
    app.add_handler(CommandHandler("hint", hint_ui_cmd), group=-1)
    app.add_handler(CommandHandler("rate_correction", rate_ui_cmd), group=-1)
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^ui:"))
