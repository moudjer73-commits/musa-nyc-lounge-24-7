import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import requests
import edge_tts
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from src.config import resolve_gemini_keys

logger = logging.getLogger(__name__)

ALEX_VOICE = "en-US-AndrewMultilingualNeural"
MAYA_VOICE = "en-US-AvaMultilingualNeural"

ROOMS_INFO: dict[str, tuple[str, str, str]] = {
    "casual_friends": ("Casual", "Close American Friend at a Coffee Shop", "relaxed, warm, uses natural contractions, phrasal verbs, and everyday American idioms"),
    "casual_roommate": ("Casual", "Apartment Roommate in NYC", "informal, direct, conversational, everyday household and life topics"),
    "social_networking": ("Social", "Tech & Policy Mixer Guest", "friendly, engaging, articulate, balances warmth with sharp insights"),
    "pro_peer_brainstorm": ("Professional", "Senior Colleague in a Strategy Brainstorm", "collaborative, pragmatic, uses idiomatic American workplace phrasing"),
    "pro_executive": ("Professional", "VP in an Executive Briefing", "concise, high-impact, structured, diplomatic pushback"),
    "intellectual_seminar": ("Intellectual", "Postgrad Seminar Partner", "analytical, nuanced, explores complex governance, psychology, and policy ideas naturally"),
    "intellectual_professor": ("Intellectual", "Visiting Professor in Office Hours", "rigorous, probing, pushes for precise C1/C2 lexical choices and clear argumentation"),
}

PRACTICE_CHUNKS: list[dict[str, str]] = [
    {
        "chunk": "push back on that a bit",
        "meaning": "Disagree politely but firmly in professional or intellectual discussions.",
        "example": "I'd actually push back on that a bit—the local data tells a different story.",
        "prompt": "Someone claims that digital governance works the same way in every municipality. How do you politely disagree?",
    },
    {
        "chunk": "that tracks with what I've seen",
        "meaning": "Confirm that someone's point matches your own experience or observations.",
        "example": "Yeah, that tracks with what I've seen on the ground over the past year.",
        "prompt": "A colleague points out that people engage more when local projects have visible short-term results. Agree naturally:",
    },
    {
        "chunk": "walk me through the logic here",
        "meaning": "Ask someone to explain their reasoning step by step without sounding hostile.",
        "example": "Help me out—walk me through the logic here before we lock in the plan.",
        "prompt": "A teammate proposes cutting field research in half. Ask them to explain their reasoning:",
    },
    {
        "chunk": "we're talking past each other",
        "meaning": "Point out that two sides are debating different things and need to realign.",
        "example": "Hold on, I think we're talking past each other—let's define the core goal first.",
        "prompt": "Two people in a seminar are arguing about budget vs. strategy without listening. Intervene naturally:",
    },
    {
        "chunk": "when it comes right down to it",
        "meaning": "Focus on the most essential reality of a situation.",
        "example": "When it comes right down to it, trust is what makes participatory governance work.",
        "prompt": "Summarize the single most important factor in building a habit of daily speaking practice:",
    },
]

PROSODY_DRILLS: list[dict[str, str]] = [
    {
        "title": "Flap T & Connected Linking",
        "sentence": "What did you think about it when you got a chance to look it over?",
        "tip": "Link 'What did you' -> 'Wha-di-joo', 'got a' -> 'ga-da', 'look it over' -> 'loo-ki-do-ver'.",
    },
    {
        "title": "Contrastive Stress Shift",
        "sentence": "I didn't say we should SCRAP the model—I said we should ADAPT it.",
        "tip": "Drop pitch on unstressed function words and punch the vowel peak on SCRAP vs. ADAPT.",
    },
    {
        "title": "Reduction of Auxiliary Cluster",
        "sentence": "We should've been able to figure that out way earlier.",
        "tip": "Pronounce 'should've been' smoothly as 'SHU-duh-bin' without choppy pauses.",
    },
    {
        "title": "Thought Group Pausing",
        "sentence": "To be fair, / the theory looks solid on paper, / but execution is where it falls apart.",
        "tip": "Use slight rising intonation on 'paper' and a firm falling cadence on 'apart'.",
    },
]

PRESSURE_PROMPTS: list[str] = [
    "Speak for 60-90 seconds: Why do many e-governance initiatives fail at the local municipal level, and what actually fixes them?",
    "Speak for 60-90 seconds: If you had to redesign how adults learn spoken English, what is the first traditional rule you would throw out?",
    "Speak for 60-90 seconds: Is high-stakes tactical discipline more important than individual creativity in football management? Defend your take.",
    "Speak for 60-90 seconds: Describe a tough trade-off where short-term comfort conflicts with long-term mastery.",
]


def _clean_for_tts(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("💡", "🎙️ You said:", "📊", "───")):
            continue
        lines.append(s)
    joined = " ".join(lines)
    joined = re.sub(r"[*_`#~>|•]", " ", joined)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined[:900] if joined else "Hey, I'm Alex. Let's keep practicing."


async def synthesize_speech_mp3(text: str, voice: str = ALEX_VOICE) -> str:
    clean = _clean_for_tts(text)
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    comm = edge_tts.Communicate(clean, voice=voice)
    await comm.save(path)
    return path


def _call_gemini_sync(parts: list[dict], system_instruction: str) -> str:
    keys = resolve_gemini_keys()
    if not keys:
        return (
            "Hey! I hear you loud and clear, though my Gemini API key isn't configured in environment secrets yet. "
            "Add GEMINI_API_KEY in GitHub Secrets so I can give full dynamic responses!"
        )
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 650},
    }
    last_err = ""
    for model in models:
        for key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                resp = requests.post(url, json=payload, timeout=35)
                if resp.status_code == 200:
                    data = resp.json()
                    cands = data.get("candidates", [])
                    if cands:
                        out_parts = cands[0].get("content", {}).get("parts", [])
                        txt = "".join(p.get("text", "") for p in out_parts).strip()
                        if txt:
                            return txt
                else:
                    last_err = f"{model} HTTP {resp.status_code}: {resp.text[:160]}"
            except Exception as exc:
                last_err = str(exc)
    logger.warning("Gemini API fallback triggered: %s", last_err)
    return "That's a really interesting point—tell me a bit more about how you see that playing out in practice?"


class RuntimeUserState:
    def __init__(self) -> None:
        self.room_id: str = "casual_friends"
        self.auto_voice: bool = True
        self.session_step: int = 0
        self.chunk_idx: int = 0
        self.prosody_idx: int = 0
        self.pressure_idx: int = 0
        self.reentry_step: int = 0
        self.turns_count: int = 0
        self.voice_turns: int = 0
        self.history: list[str] = []
        self.last_alex_reply: str = "Hey! I'm Alex. Ready whenever you are."
        self.reminder_time: str = "20:00"
        self.ratings: list[int] = []


USER_STATES: dict[int, RuntimeUserState] = {}


def get_user_state(uid: int) -> RuntimeUserState:
    if uid not in USER_STATES:
        USER_STATES[uid] = RuntimeUserState()
    return USER_STATES[uid]


def main_menu_kb(st: RuntimeUserState) -> InlineKeyboardMarkup:
    v_label = "🔊 Auto-Voice: ON" if st.auto_voice else "🔇 Auto-Voice: OFF"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎙️ 10-Min Session", callback_data="ui:session:start"),
            InlineKeyboardButton("🚪 Change Room", callback_data="ui:room:menu"),
        ],
        [
            InlineKeyboardButton("🔄 Switch Tone", callback_data="ui:switch:menu"),
            InlineKeyboardButton("📚 Review Chunks", callback_data="ui:review:next"),
        ],
        [
            InlineKeyboardButton("🎧 Audio Podcast", callback_data="ui:podcast:make"),
            InlineKeyboardButton("🗣️ Prosody Lab", callback_data="ui:prosody:next"),
        ],
        [
            InlineKeyboardButton("⏱️ 90s Challenge", callback_data="ui:pressure:start"),
            InlineKeyboardButton("📊 Progress & Profile", callback_data="ui:progress:show"),
        ],
        [
            InlineKeyboardButton(v_label, callback_data="ui:voice:toggle"),
            InlineKeyboardButton("🔊 Listen Last Reply", callback_data="ui:voice:speak_last"),
        ],
    ])


def chat_reply_kb(st: RuntimeUserState) -> InlineKeyboardMarkup:
    v_label = "🔊 Auto-Voice: ON" if st.auto_voice else "🔇 Auto-Voice: OFF"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔊 Listen Again", callback_data="ui:voice:speak_last"),
            InlineKeyboardButton(v_label, callback_data="ui:voice:toggle"),
        ],
        [
            InlineKeyboardButton("🚪 Change Room", callback_data="ui:room:menu"),
            InlineKeyboardButton("🎙️ 10-Min Session", callback_data="ui:session:start"),
        ],
    ])


def rooms_kb() -> InlineKeyboardMarkup:
    rows = []
    for rid, (reg, role, _) in ROOMS_INFO.items():
        rows.append([InlineKeyboardButton(f"{reg} — {role}", callback_data=f"ui:room:set:{rid}")])
    return InlineKeyboardMarkup(rows)


def switch_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☕ Casual Catch-Up", callback_data="ui:room:set:casual_friends"),
            InlineKeyboardButton("🤝 Social Mixer", callback_data="ui:room:set:social_networking"),
        ],
        [
            InlineKeyboardButton("💡 Team Brainstorm", callback_data="ui:room:set:pro_peer_brainstorm"),
            InlineKeyboardButton("💼 Executive Briefing", callback_data="ui:room:set:pro_executive"),
        ],
        [
            InlineKeyboardButton("🎓 Seminar Debate", callback_data="ui:room:set:intellectual_seminar"),
            InlineKeyboardButton("📚 Professor Office Hours", callback_data="ui:room:set:intellectual_professor"),
        ],
    ])


async def send_alex_voice_if_enabled(msg, text: str, force: bool = False, st: RuntimeUserState | None = None) -> None:
    if not force and st is not None and not st.auto_voice:
        return
    path = None
    try:
        await msg.chat.send_action(ChatAction.RECORD_VOICE)
        path = await synthesize_speech_mp3(text, voice=ALEX_VOICE)
        with open(path, "rb") as f:
            await msg.reply_voice(voice=f, caption="🔊 Alex — American English Pronunciation")
    except Exception as exc:
        logger.warning("TTS voice generation failed: %s", exc)
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def register_live_telegram_handlers(app: Application, container=None) -> None:
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        reg, role, _ = ROOMS_INFO[st.room_id]
        welcome = (
            f"Hey! I'm Alex, your American English speaking partner.\n\n"
            f"• Current Room: {reg} ({role})\n"
            f"• Voice Replies: {'ON 🔊' if st.auto_voice else 'OFF 🔇'}\n\n"
            f"Send me a voice note 🎙️ or text message 💬 in English right now to start talking, or tap any tool below:"
        )
        st.last_alex_reply = "Hey! I'm Alex, your American English speaking partner. Send me a voice note or text message anytime and let's dive right in."
        await msg.reply_text("Clearing old keyboard...", reply_markup=ReplyKeyboardRemove())
        await msg.reply_text(welcome, reply_markup=main_menu_kb(st))

    async def cmd_room(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        if context.args and context.args[0] in ROOMS_INFO:
            st.room_id = context.args[0]
            reg, role, _ = ROOMS_INFO[st.room_id]
            await msg.reply_text(f"✅ Room switched to: {reg} ({role}). Send a message or voice note to start!", reply_markup=chat_reply_kb(st))
            return
        reg, role, _ = ROOMS_INFO[st.room_id]
        await msg.reply_text(f"🚪 Current Room: {reg} — {role}\nTap a setting below to change how Alex speaks with you:", reply_markup=rooms_kb())

    async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return
        await msg.reply_text(
            "🔄 Register & Tone Shift:\nPick a new social or professional setting below and adapt how you express your idea:",
            reply_markup=switch_kb(),
        )

    async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        item = PRACTICE_CHUNKS[st.chunk_idx % len(PRACTICE_CHUNKS)]
        steps = [
            ("Step 1/4 — Warm-Up & Context", f"Target Expression: \"{item['chunk']}\"\nMeaning: {item['meaning']}\nExample: \"{item['example']}\"\n\nTap '🔊 Hear Expression' or 'Next Step ➡️'."),
            ("Step 2/4 — Guided Scenario", f"Scenario using \"{item['chunk']}\":\n{item['prompt']}\n\nReply with a voice note 🎙️ or text message using the expression!"),
            ("Step 3/4 — Register Adaptation", f"Now try shifting the tone: how would you express the same idea in a high-stakes executive meeting vs. casual coffee with a friend?"),
            ("Step 4/4 — Session Wrap-Up", f"Great work locking in \"{item['chunk']}\"! Send a voice note for final feedback or tap below to start a fresh chunk."),
        ]
        idx = st.session_step % len(steps)
        title, body = steps[idx]
        st.last_alex_reply = f"{item['chunk']}. {item['example']}"
        st.session_step += 1
        if idx == len(steps) - 1:
            st.chunk_idx += 1
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔊 Hear Expression", callback_data="ui:voice:speak_last"),
                InlineKeyboardButton("Next Step ➡️", callback_data="ui:session:start"),
            ]
        ])
        await msg.reply_text(f"🎙️ 10-Minute Practice Session — {title}\n\n{body}", reply_markup=kb)

    async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        item = PRACTICE_CHUNKS[st.chunk_idx % len(PRACTICE_CHUNKS)]
        st.chunk_idx += 1
        st.last_alex_reply = f"{item['chunk']}. For example: {item['example']}"
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔊 Hear Pronunciation", callback_data="ui:voice:speak_last"),
                InlineKeyboardButton("Next Expression ➡️", callback_data="ui:review:next"),
            ]
        ])
        await msg.reply_text(
            f"📚 Spaced Retrieval Review:\n\n"
            f"• Situation: {item['prompt']}\n"
            f"• Target C1/C2 Chunk: \"{item['chunk']}\"\n"
            f"• Native Example: \"{item['example']}\"\n\n"
            f"Send a voice note using this expression in your own sentence!",
            reply_markup=kb,
        )

    async def cmd_prosody(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        drill = PROSODY_DRILLS[st.prosody_idx % len(PROSODY_DRILLS)]
        st.prosody_idx += 1
        st.last_alex_reply = drill["sentence"]
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔊 Hear American Prosody", callback_data="ui:voice:speak_last"),
                InlineKeyboardButton("Next Drill ➡️", callback_data="ui:prosody:next"),
            ]
        ])
        await msg.reply_text(
            f"🗣️ American Prosody & Connected Speech Lab:\n\n"
            f"• Focus: {drill['title']}\n"
            f"• Shadow this line: \"{drill['sentence']}\"\n"
            f"• Coaching Tip: {drill['tip']}\n\n"
            f"Tap '🔊 Hear American Prosody', then send a voice note shadowing the exact rhythm!",
            reply_markup=kb,
        )
        await send_alex_voice_if_enabled(msg, drill["sentence"], force=True, st=st)

    async def cmd_podcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        await msg.reply_text("🎧 Generating a two-host American English dialogue (Alex & Maya) with audio...")
        await msg.chat.send_action(ChatAction.RECORD_VOICE)
        script_lines = [
            ("Alex", "Hey Maya, have you noticed how municipal e-governance projects always look great on slides, yet stall on the ground?"),
            ("Maya", "Totally, Alex. And when it comes right down to it, people don't want another portal—they want to see their feedback actually move the needle."),
            ("Alex", "That tracks with what I've seen. If local officials don't close the feedback loop fast, citizen trust just evaporates."),
            ("Maya", "Exactly. You've got to build trust before you scale the tech. So, to our listener—how would you push back on a purely top-down digital plan?"),
        ]
        transcript = "\n\n".join(f"🎙️ {speaker}: {line}" for speaker, line in script_lines)
        st.last_alex_reply = " ".join(line for _, line in script_lines)
        temp_files = []
        combined_path = None
        try:
            for speaker, line in script_lines:
                v = ALEX_VOICE if speaker == "Alex" else MAYA_VOICE
                p = await synthesize_speech_mp3(line, voice=v)
                temp_files.append(p)
            fd, combined_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            with open(combined_path, "wb") as out_f:
                for p in temp_files:
                    with open(p, "rb") as in_f:
                        out_f.write(in_f.read())
            with open(combined_path, "rb") as audio_f:
                await msg.reply_voice(voice=audio_f, caption="🎧 Spoken English Lab Podcast — Alex & Maya")
        except Exception as exc:
            logger.warning("Podcast synthesis error: %s", exc)
        finally:
            for p in temp_files:
                if os.path.exists(p):
                    os.remove(p)
            if combined_path and os.path.exists(combined_path):
                os.remove(combined_path)
        await msg.reply_text(
            f"📝 Episode Transcript:\n\n{transcript}\n\n💬 Send a voice note answering Maya's question at the end!",
            reply_markup=chat_reply_kb(st),
        )

    async def cmd_curated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return
        await msg.reply_text(
            "📻 Curated American English Podcasts for Advanced (B2+/C1/C2) Listening:\n\n"
            "1. Deep Questions with Cal Newport — sharp professional & academic discourse.\n"
            "2. Plain English with Derek Thompson — analytical policy, economics & society.\n"
            "3. Freakonomics Radio — natural storytelling + idiomatic American interviews.\n\n"
            "Listen to 5 minutes of any episode, then send me a 60-second voice note summarizing your take!"
        )

    async def cmd_pressure(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        prompt = PRESSURE_PROMPTS[st.pressure_idx % len(PRESSURE_PROMPTS)]
        st.pressure_idx += 1
        st.last_alex_reply = prompt
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔊 Hear Prompt", callback_data="ui:voice:speak_last"),
                InlineKeyboardButton("Skip Topic 🔄", callback_data="ui:pressure:start"),
            ]
        ])
        await msg.reply_text(
            f"⏱️ 90-Second Spontaneous Speaking Challenge:\n\n• {prompt}\n\n"
            f"Record a continuous voice note now without stopping to self-correct!",
            reply_markup=kb,
        )

    async def cmd_reentry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        blocks = [
            "Block 1/3 (Easy Recall): Send a quick 20-second voice note about how your week is going—zero pressure.",
            "Block 2/3 (Chunk Refresh): Use 'when it comes right down to it' in one casual sentence.",
            "Block 3/3 (Mini-Chat): Tell Alex one thing you're researching or working on today.",
        ]
        b = blocks[st.reentry_step % len(blocks)]
        st.reentry_step += 1
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Next Warm-Up Block ➡️", callback_data="ui:reentry:next")]])
        await msg.reply_text(f"🌱 Low-Pressure Warm-Up Session:\n\n• {b}", reply_markup=kb)

    async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        avg_r = f"{sum(st.ratings)/len(st.ratings):.1f}/5 ⭐" if st.ratings else "No ratings yet"
        await msg.reply_text(
            f"📊 Your Speaking Progress Report:\n\n"
            f"• Total Interactive Turns: {st.turns_count}\n"
            f"• Voice Notes Practiced: {st.voice_turns}\n"
            f"• Active Register Room: {ROOMS_INFO[st.room_id][0]} ({ROOMS_INFO[st.room_id][1]})\n"
            f"• Expressions Reviewed: {st.chunk_idx}\n"
            f"• Prosody Drills Completed: {st.prosody_idx}\n"
            f"• Coaching Helpfulness Rating: {avg_r}",
            reply_markup=main_menu_kb(st),
        )

    async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        reg, role, _ = ROOMS_INFO[st.room_id]
        await msg.reply_text(
            f"👤 Learner Profile Summary:\n\n"
            f"• Target Dialect: Spoken American English (C1/C2)\n"
            f"• Current Setting: {reg} — {role}\n"
            f"• Auto Voice Replies: {'Enabled 🔊' if st.auto_voice else 'Disabled 🔇'}\n"
            f"• Daily Reminder: {st.reminder_time}\n"
            f"• Register Mix: Casual 25% | Social 25% | Work 25% | Intellectual 25%",
            reply_markup=main_menu_kb(st),
        )

    async def cmd_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💡 push back", callback_data="ui:hint:0"),
                InlineKeyboardButton("💡 that tracks", callback_data="ui:hint:1"),
                InlineKeyboardButton("💡 walk me through", callback_data="ui:hint:2"),
            ]
        ])
        await msg.reply_text(
            "💡 English-Only Hint Ladder:\nTap an expression below to see a situational clue without L1 translation:",
            reply_markup=kb,
        )

    async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        st = get_user_state(user.id)
        if context.args:
            st.reminder_time = context.args[0]
            await msg.reply_text(f"⏰ Daily practice reminder updated to {st.reminder_time}!")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🕗 08:00", callback_data="ui:remind:08:00"),
            InlineKeyboardButton("🕑 14:00", callback_data="ui:remind:14:00"),
            InlineKeyboardButton("🕖 19:00", callback_data="ui:remind:19:00"),
            InlineKeyboardButton("🕘 21:00", callback_data="ui:remind:21:00"),
        ]])
        await msg.reply_text(f"⏰ Current reminder time: {st.reminder_time}\nTap a time below to update:", reply_markup=kb)

    async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{i} ⭐", callback_data=f"ui:rate:{i}") for i in range(1, 6)
        ]])
        await msg.reply_text("⭐ How helpful were Alex's speaking tips today?", reply_markup=kb)

    async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Reset My Session Data", callback_data="ui:privacy:reset")]])
        await msg.reply_text(
            "🔒 Privacy & Data Controls:\n"
            "• Raw voice notes are processed in memory and deleted immediately after analysis.\n"
            "• You can wipe your active session history anytime below or with /delete_everything.",
            reply_markup=kb,
        )

    async def cmd_delete_everything(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        USER_STATES.pop(user.id, None)
        await msg.reply_text("🗑️ All your profile and conversation state has been permanently wiped. Send /start to begin fresh.")

    async def handle_text_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user or not msg.text:
            return
        user_text = msg.text.strip()
        st = get_user_state(user.id)
        st.turns_count += 1
        reg, role, style_desc = ROOMS_INFO[st.room_id]
        await msg.chat.send_action(ChatAction.TYPING)

        history_str = "\n".join(st.history[-6:])
        sys_prompt = (
            f"You are Alex, an intelligent, natural American English conversation partner.\n"
            f"Current Register: {reg} | Your Role: {role} ({style_desc}).\n"
            f"Learner Profile: B2+/C1 speaker aiming for spontaneous C1/C2 American English fluency.\n"
            f"Rules:\n"
            f"1. Never use Arabic. Respond entirely in natural spoken American English.\n"
            f"2. First, reply naturally to the user's message in 2-4 engaging sentences, using contractions and authentic American idioms/chunks, and end with a natural follow-up question.\n"
            f"3. After your reply, if (and only if) the user's phrasing had an unnatural expression, calque, or could be upgraded to a sharper C1/C2 American chunk, add a new line starting with '💡 Natural Upgrade: ' showing a brief, encouraging American phrasing upgrade.\n"
            f"Recent conversation:\n{history_str}"
        )
        reply = await asyncio.to_thread(_call_gemini_sync, [{"text": user_text}], sys_prompt)
        st.history.append(f"User: {user_text}")
        st.history.append(f"Alex: {reply}")
        st.last_alex_reply = reply

        await msg.reply_text(reply, reply_markup=chat_reply_kb(st))
        await send_alex_voice_if_enabled(msg, reply, force=False, st=st)

    async def handle_voice_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        voice_obj = msg.voice or msg.audio
        if not voice_obj:
            return
        st = get_user_state(user.id)
        st.turns_count += 1
        st.voice_turns += 1
        reg, role, style_desc = ROOMS_INFO[st.room_id]
        await msg.chat.send_action(ChatAction.TYPING)

        ogg_path = None
        try:
            tg_file = await context.bot.get_file(voice_obj.file_id)
            fd, ogg_path = tempfile.mkstemp(suffix=".ogg")
            os.close(fd)
            await tg_file.download_to_drive(custom_path=ogg_path)
            with open(ogg_path, "rb") as f:
                b64_audio = base64.b64encode(f.read()).decode("utf-8")

            history_str = "\n".join(st.history[-6:])
            sys_prompt = (
                f"You are Alex, an American English speaking partner and prosody/pragmatics coach.\n"
                f"Current Setting: {reg} ({role} — {style_desc}).\n"
                f"Never use Arabic. Format your response in 3 clear parts:\n"
                f"🎙️ You said: \"<exact transcription of the user's English speech>\"\n\n"
                f"<Your natural 2-4 sentence conversational response as Alex in character, ending with a follow-up question>\n\n"
                f"💡 Coaching Note: <1 concise tip upgrading a phrase to idiomatic C1/C2 American English or noting connected speech/stress>\n"
                f"Recent context:\n{history_str}"
            )
            parts = [
                {"inline_data": {"mime_type": "audio/ogg", "data": b64_audio}},
                {"text": "Listen to the user's voice note, transcribe it, respond naturally as Alex, and provide one concise American English coaching note."},
            ]
            reply = await asyncio.to_thread(_call_gemini_sync, parts, sys_prompt)
            st.history.append(f"User (voice): [audio turn]")
            st.history.append(f"Alex: {reply}")
            st.last_alex_reply = reply

            await msg.reply_text(reply, reply_markup=chat_reply_kb(st))
            await send_alex_voice_if_enabled(msg, reply, force=False, st=st)
        except Exception as exc:
            logger.exception("Voice processing error: %s", exc)
            await msg.reply_text("I caught your voice note, though I hit a brief connection hiccup analyzing the audio. Try sending it again!")
        finally:
            if ogg_path and os.path.exists(ogg_path):
                try:
                    os.remove(ogg_path)
                except OSError:
                    pass

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        msg = update.effective_message
        if not query or not query.data or not user or not msg:
            return
        await query.answer()
        st = get_user_state(user.id)
        parts = query.data.split(":")

        if parts[1] == "voice" and parts[2] == "toggle":
            st.auto_voice = not st.auto_voice
            state_txt = "ON 🔊 (Alex will send voice notes automatically)" if st.auto_voice else "OFF 🔇 (Tap '🔊 Listen' whenever you want audio)"
            await msg.reply_text(f"Auto-Voice Replies turned {state_txt}", reply_markup=main_menu_kb(st))
        elif parts[1] == "voice" and parts[2] == "speak_last":
            await send_alex_voice_if_enabled(msg, st.last_alex_reply, force=True, st=st)
        elif parts[1] == "room" and parts[2] == "menu":
            await cmd_room(update, context)
        elif parts[1] == "room" and parts[2] == "set":
            rid = parts[3]
            if rid in ROOMS_INFO:
                st.room_id = rid
                reg, role, _ = ROOMS_INFO[rid]
                opener = f"Hey! We're now in the {reg} setting ({role}). What's on your mind today?"
                st.last_alex_reply = opener
                await msg.reply_text(f"✅ Switched to {reg} — {role}\n\n💬 Alex: \"{opener}\"", reply_markup=chat_reply_kb(st))
                await send_alex_voice_if_enabled(msg, opener, force=False, st=st)
        elif parts[1] == "switch" and parts[2] == "menu":
            await cmd_switch(update, context)
        elif parts[1] == "session" and parts[2] == "start":
            await cmd_session(update, context)
        elif parts[1] == "review" and parts[2] == "next":
            await cmd_review(update, context)
        elif parts[1] == "prosody" and parts[2] == "next":
            await cmd_prosody(update, context)
        elif parts[1] == "podcast" and parts[2] == "make":
            await cmd_podcast(update, context)
        elif parts[1] == "pressure" and parts[2] == "start":
            await cmd_pressure(update, context)
        elif parts[1] == "reentry" and parts[2] == "next":
            await cmd_reentry(update, context)
        elif parts[1] == "progress" and parts[2] == "show":
            await cmd_progress(update, context)
        elif parts[1] == "hint":
            idx = int(parts[2]) % len(PRACTICE_CHUNKS)
            item = PRACTICE_CHUNKS[idx]
            st.last_alex_reply = f"{item['chunk']}. {item['example']}"
            await msg.reply_text(
                f"💡 Clue 1 (Function): {item['meaning']}\n"
                f"💡 Clue 2 (Pattern): {item['chunk'][:6]}...\n"
                f"💡 Full Expression: \"{item['chunk']}\"\n"
                f"• Example: \"{item['example']}\"",
                reply_markup=chat_reply_kb(st),
            )
        elif parts[1] == "remind":
            st.reminder_time = f"{parts[2]}:{parts[3]}" if len(parts) > 3 else parts[2]
            await msg.reply_text(f"⏰ Daily practice reminder set for {st.reminder_time}!")
        elif parts[1] == "rate":
            score = int(parts[2])
            st.ratings.append(score)
            await msg.reply_text(f"Thanks! Recorded your {score}/5 ⭐ rating.")
        elif parts[1] == "privacy" and parts[2] == "reset":
            await cmd_delete_everything(update, context)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled Telegram exception:", exc_info=context.error)

    # تسجيل جميع الأوامر الـ 16 + معالج الرسائل النصية + معالج البصمات الصوتية + معالج الأزرار
    cmd_map = {
        "start": cmd_start,
        "session": cmd_session,
        "room": cmd_room,
        "switch": cmd_switch,
        "review": cmd_review,
        "podcast": cmd_podcast,
        "curated": cmd_curated,
        "prosody": cmd_prosody,
        "pressure": cmd_pressure,
        "skip_topic": cmd_pressure,
        "reentry": cmd_reentry,
        "progress": cmd_progress,
        "profile": cmd_profile,
        "hint": cmd_hint,
        "remind": cmd_remind,
        "rate_correction": cmd_rate,
        "privacy": cmd_privacy,
        "delete_everything": cmd_delete_everything,
    }
    for name, fn in cmd_map.items():
        app.add_handler(CommandHandler(name, fn), group=-2)

    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^ui:"), group=-2)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_chat), group=-2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_chat), group=-2)
    app.add_error_handler(on_error)
