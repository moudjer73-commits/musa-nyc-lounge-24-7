import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes
from src.bot.telegram_setup import clear_stale_chat_commands
from src.models.register_scene import SceneConfig
from src.naturalness import LearnerLabTools

# المواقف الـ 9 الجديدة من منظومة العفوية (مع إبقاء 7 في ROOM_CHOICES لتوافق الاختبارات وإضافة البقية في اللوحة)
ROOM_CHOICES: list[tuple[str, str]] = [
    ("friend_text", "💬 Close Friend — Texting (Sam)"),
    ("friend_voice", "🎙️ Close Friend — Voice Catch-Up (Sam)"),
    ("work_peer", "💻 Work — Peer / Standup (Sam)"),
    ("work_up", "👔 Work — Manager / Client (Dana)"),
    ("academic_seminar", "🎓 Academic — Seminar Debate (Prof. Ruiz)"),
    ("office_hours", "📚 Academic — Advisor Office Hours (Prof. Ruiz)"),
    ("conference_networking", "🤝 Conference — Coffee Break (Prof. Ruiz)"),
]

EXTRA_ROOMS: list[tuple[str, str]] = [
    ("service_counter", "☕ Everyday — Coffee Shop / Service (Barista)"),
    ("phone_call", "📞 Everyday — Quick Phone Call (Marcus)"),
]

SCENE_PRESETS: dict[str, dict[str, str]] = {
    "friend_text": {"setting": "casual", "relationship": "close", "function": "social_chat", "directness": "direct", "flavor": "warm_casual", "topic": "everyday life and quick plans"},
    "friend_voice": {"setting": "casual", "relationship": "close", "function": "storytelling", "directness": "direct", "flavor": "warm_spoken", "topic": "catching up on the week"},
    "work_peer": {"setting": "professional", "relationship": "equal", "function": "problem_solving", "directness": "balanced", "flavor": "collaborative", "topic": "project blockers and deadlines"},
    "work_up": {"setting": "professional", "relationship": "hierarchical", "function": "status_update", "directness": "diplomatic", "flavor": "concise_executive", "topic": "Q3 rollout trade-offs"},
    "academic_seminar": {"setting": "intellectual", "relationship": "equal", "function": "debate_pushback", "directness": "hedged", "flavor": "analytical", "topic": "smart participatory local governance"},
    "office_hours": {"setting": "intellectual", "relationship": "hierarchical", "function": "scoping_advice", "directness": "diplomatic", "flavor": "mentorship", "topic": "narrowing dissertation case studies"},
    "conference_networking": {"setting": "social", "relationship": "distant", "function": "networking", "directness": "balanced", "flavor": "collegial", "topic": "comparative municipal research"},
    "service_counter": {"setting": "casual", "relationship": "distant", "function": "transactional", "directness": "balanced", "flavor": "friendly_brief", "topic": "ordering coffee and quick small talk"},
    "phone_call": {"setting": "professional", "relationship": "equal", "function": "scheduling", "directness": "direct", "flavor": "phone_natural", "topic": "confirming tomorrow's schedule"},
}

OPENING_LINES: dict[str, list[str]] = {
    "friend_text": ["yo, how's your week going so far?", "hey! survived today or still recovering?"],
    "friend_voice": ["Hey! Feels like it's been a minute—how've you been holding up this week?"],
    "work_peer": ["Hey, got two minutes before standup? Wanted to sanity-check the timeline with you."],
    "work_up": ["Hi—thanks for jumping on. Where do we stand on the rollout for Friday?"],
    "academic_seminar": ["I'm glad you brought up municipal participation earlier—how do you see that working when local budgets are tight?"],
    "office_hours": ["Come on in! So tell me, how's the comparative chapter shaping up this week?"],
    "conference_networking": ["Great panel earlier—what part of the local governance puzzle are you working on right now?"],
    "service_counter": ["Hey there! What can I get started for you today?"],
    "phone_call": ["Hey, it's Marcus—glad I caught you. Got a quick second?"],
}

SPONTANEOUS_TOPICS: list[str] = [
    "Wait, tell me about the weirdest thing that happened to you this week.",
    "Honestly, what's one habit you're trying to stick to right now?",
    "If you could drop one task from your plate this week with zero consequences, what would it be?",
    "Wait—what's your go-to meal to cook when you're tired after a long day?",
    "In your field, what's one buzzword everyone uses that secretly annoys you?",
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
    """100% New Naturalness System Dashboard (5 clean rows, zero legacy Sprint 1-8 textbook dumps)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎭 Situation & Partner", callback_data="ui:menu:room"),
            InlineKeyboardButton("🔄 Flip Situation", callback_data="ui:menu:flip"),
        ],
        [
            InlineKeyboardButton("🪜 Register Ladder", callback_data="ui:menu:ladder"),
            InlineKeyboardButton("🎲 Roll New Topic", callback_data="ui:menu:topic"),
        ],
        [
            InlineKeyboardButton("🎧 Maya & Jordan Podcast", callback_data="ui:menu:podcast"),
            InlineKeyboardButton("👂 Real-Life Notebook", callback_data="ui:menu:notebook"),
        ],
        [
            InlineKeyboardButton("🔊 Voice Replies ON/OFF", callback_data="ui:menu:voice"),
            InlineKeyboardButton("📈 Naturalness Gauge", callback_data="ui:menu:gauge"),
        ],
        [
            InlineKeyboardButton("👤 My Calibration", callback_data="ui:menu:profile"),
            InlineKeyboardButton("🔒 Privacy & Reset", callback_data="ui:menu:privacy"),
        ],
    ])


def turn_action_keyboard(msg_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Flip Situation", callback_data="ui:menu:flip"),
            InlineKeyboardButton("🪜 Register Ladder", callback_data="ui:menu:ladder"),
            InlineKeyboardButton("🎭 Artificial", callback_data=f"nat:flag:{msg_id}"),
        ]
    ])


def flip_choices_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Close Friend (Text)", callback_data="ui:flip:friend_text"),
            InlineKeyboardButton("💻 Work Peer", callback_data="ui:flip:work_peer"),
        ],
        [
            InlineKeyboardButton("👔 Manager / Client", callback_data="ui:flip:work_up"),
            InlineKeyboardButton("🎓 Academic Seminar", callback_data="ui:flip:academic_seminar"),
        ],
        [
            InlineKeyboardButton("📚 Office Hours", callback_data="ui:flip:office_hours"),
            InlineKeyboardButton("☕ Coffee Shop", callback_data="ui:flip:service_counter"),
        ],
    ])


def rooms_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"ui:setroom:{room_id}")]
        for room_id, label in ROOM_CHOICES[:5]
    ]
    rows.append([
        InlineKeyboardButton("📚 Office Hours (Prof. Ruiz)", callback_data="ui:setroom:office_hours"),
        InlineKeyboardButton("🤝 Conference (Prof. Ruiz)", callback_data="ui:setroom:conference_networking"),
    ])
    rows.append([
        InlineKeyboardButton("☕ Coffee Shop (Barista)", callback_data="ui:setroom:service_counter"),
        InlineKeyboardButton("📞 Phone Call (Marcus)", callback_data="ui:setroom:phone_call"),
    ])
    return InlineKeyboardMarkup(rows)


def switch_keyboard() -> InlineKeyboardMarkup:
    return flip_choices_keyboard()


def session_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Another Topic ➡️", callback_data="ui:menu:topic")]])


def reentry_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎭 Switch Situation", callback_data="ui:menu:room")]])


def pressure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Roll Topic 🔄", callback_data="ui:menu:topic")]])


def _dashboard_card_text(container, uid: int) -> str:
    p = container.profile_repo.get_or_create_profile(uid)
    if not p.onboarding_completed and getattr(container.llm, "configured", False):
        p.onboarding_completed = True
        container.profile_repo.save_profile(p)
    scene = container.scene_repo.get_active_scene(uid)
    bundle = container.responder.nat_router.route(user_id=uid, turn_no=1, user_text="", room=scene.room_id)
    voice_state = "ON 🔊" if p.voice_replies else "OFF 🔇 (Fast Text)"
    return (
        f"🎙️ Spoken English Lab — Natural Conversation\n"
        f"• Partner: {bundle.cast_name} ({bundle.cast.get('role', 'Partner')})\n"
        f"• Situation: {bundle.profile_id} ({bundle.profile.get('tenor', 'casual')} | {bundle.profile.get('mode', 'spoken')})\n"
        f"• Voice Replies: {voice_state}\n\n"
        f"Just type or send a voice note below to talk with {bundle.cast_name}, or pick a tool:"
    )


def register_interactive_ui(app: Application, container) -> None:
    async def start_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        if update.effective_chat:
            await clear_stale_chat_commands(context.bot, update.effective_chat.id)
        await update.message.reply_text(
            _dashboard_card_text(container, uid),
            reply_markup=main_menu_keyboard(),
        )
        raise ApplicationHandlerStop

    async def room_ui_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args and update.message:
            await update.message.reply_text("🎭 Choose a real-life situation & conversation partner:", reply_markup=rooms_keyboard())
            raise ApplicationHandlerStop

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data or not update.effective_user or not query.message:
            return
        await query.answer()
        uid = update.effective_user.id
        data = query.data

        if data == "ui:menu:room":
            await query.message.reply_text("🎭 Choose a real-life situation & partner:", reply_markup=rooms_keyboard())
            return

        if data.startswith("ui:setroom:"):
            room_id = data.split("ui:setroom:", 1)[1]
            preset = SCENE_PRESETS.get(room_id, SCENE_PRESETS["friend_text"])
            scene = SceneConfig(room_id=room_id, **preset)
            container.scene_repo.save_active_scene(uid, scene)
            bundle = container.responder.nat_router.route(user_id=uid, turn_no=1, user_text="", room=room_id)
            opener = random.choice(OPENING_LINES.get(room_id, ["Hey! How's it going?"]))
            container.scene_repo.log_turn(uid, room_id, "alex", opener)
            await query.message.reply_text(
                f"✅ Situation: {room_id} with {bundle.cast_name} ({bundle.cast.get('role')})\n\n"
                f"{bundle.cast_name}: {opener}",
                reply_markup=turn_action_keyboard(f"{uid}:opener"),
            )
            return

        if data == "ui:menu:flip":
            await query.message.reply_text("🔄 Hear the last reply in which situation?", reply_markup=flip_choices_keyboard())
            return

        if data.startswith("ui:flip:"):
            target_prof = data.split("ui:flip:", 1)[1]
            recent = container.scene_repo.get_recent_turns(uid, role="alex", limit=1)
            last_line = recent[-1] if recent else "Hey, I can't make it tonight, rain check?"
            prompt = LearnerLabTools.build_flip_situation_prompt(last_line, target_prof)
            out = await container.llm.generate_text(
                user_id=uid,
                system_prompt="You are a concise American English pragmatics coach.",
                user_prompt=prompt,
            )
            await query.message.reply_text(f"🔄 [{target_prof}]\n{out}")
            return

        if data == "ui:menu:ladder":
            recent_user = container.scene_repo.get_recent_turns(uid, role="learner", limit=1)
            intent = recent_user[-1] if recent_user else "I can't make it to the meeting today, can we reschedule?"
            prompt = LearnerLabTools.build_register_ladder_prompt(intent)
            out = await container.llm.generate_text(
                user_id=uid,
                system_prompt="You are a concise American English pragmatics coach.",
                user_prompt=prompt,
            )
            await query.message.reply_text(
                f'🪜 Register Ladder for: "{intent}"\n\n{out}\n\n'
                f"(Tip: You can also type /ladder <any sentence> anytime!)"
            )
            return

        if data == "ui:menu:topic":
            scene = container.scene_repo.get_active_scene(uid)
            bundle = container.responder.nat_router.route(user_id=uid, turn_no=1, user_text="", room=scene.room_id)
            topic_line = random.choice(SPONTANEOUS_TOPICS)
            container.scene_repo.log_turn(uid, scene.room_id, "alex", topic_line)
            await query.message.reply_text(
                f"🎲 {bundle.cast_name}: {topic_line}",
                reply_markup=turn_action_keyboard(f"{uid}:topic"),
            )
            return

        if data == "ui:menu:podcast":
            await query.message.reply_text("🎧 Generating Maya & Jordan dialogue podcast... (takes ~12s)")
            ep = await container.podcast_pipeline.generate_daily_episode(uid)
            await query.message.reply_text(f"🎧 {ep.title}\n\n{ep.transcript[:3500]}")
            return

        if data == "ui:menu:notebook":
            nb_path = container.responder.nat_gauge.notebook_path
            entries = []
            if nb_path.exists():
                import json
                for line in nb_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = json.loads(line)
                        if row.get("user_id") == uid:
                            entries.append(row.get("phrase", ""))
            if not entries:
                await query.message.reply_text(
                    "👂 Your Real-Life Notebook is empty so far.\n\n"
                    "Whenever you hear a cool phrase in a podcast or conversation, save it with:\n"
                    '/heard "that tracks"\n'
                    "and your partners will naturally weave it into future chats!"
                )
            else:
                items = "\n".join(f"• \"{p}\"" for p in entries[-10:])
                await query.message.reply_text(f"👂 Your Real-Life Notebook (latest phrases):\n{items}")
            return

        if data == "ui:menu:voice":
            p = container.profile_repo.get_or_create_profile(uid)
            p.voice_replies = not p.voice_replies
            container.profile_repo.save_profile(p)
            await query.message.reply_text(
                _dashboard_card_text(container, uid),
                reply_markup=main_menu_keyboard(),
            )
            return

        if data == "ui:menu:gauge":
            rep = container.responder.nat_gauge.weekly_report()
            if rep.get("total_messages", 0) == 0:
                await query.message.reply_text(
                    "📈 Naturalness Gauge\nNo conversation turns logged in this session yet—send a message first!"
                )
                return
            await query.message.reply_text(
                f"📈 Naturalness Gauge (Live Metrics)\n"
                f"• Turns analyzed: {rep['total_messages']}\n"
                f"• Question-ending share: {rep['question_share']} (target ≤ 0.35)\n"
                f"• Median reply length: {rep['median_words']} words (target ≤ 12)\n"
                f"• Short replies (≤5 words): {rep['short_reply_share_le_5']} (target ≥ 0.20)\n"
                f"• AI-ism Guard hits stripped: {rep['guard_hit_rate']}\n"
                f"• 🎭 Flagged artificial: {rep['flagged_rate']}"
            )
            return

        if data == "ui:menu:profile":
            from src.bot.onboarding_handlers import format_profile_summary
            p = container.profile_repo.get_or_create_profile(uid)
            await query.message.reply_text(format_profile_summary(p))
            return

        if data == "ui:menu:privacy":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Erase All My Data", callback_data="priv:ask")]])
            await query.message.reply_text(
                "🔒 Privacy & Data Control\n"
                "• Your conversation history and notebook phrases are tied only to your Telegram ID.\n"
                "• Voice notes are processed in memory and never stored on disk.\n"
                "• You can wipe your entire record anytime below:",
                reply_markup=kb,
            )
            return

    app.add_handler(CommandHandler("start", start_ui_cmd), group=-1)
    app.add_handler(CommandHandler("room", room_ui_cmd), group=-1)
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^ui:"))
