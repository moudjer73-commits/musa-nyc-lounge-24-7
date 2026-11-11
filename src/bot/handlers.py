import asyncio, hashlib, html, io, logging, re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import telegram.ext as tg_ext

from src.bot.setup_tg import refresh_chat_menu_once, split_telegram_text
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots
from src.db.repo import ANGLES, TwinRepository
from src.llm.client import GeminiClient, LLMUnavailableError, QuotaExceededError
from src.twin.engine import ANGLE_META, EchoTwinEngine, GhostTwinAnalysis
from src.voice.tts import synthesize_partner_voice, synthesize_twin_voice

logger = logging.getLogger(__name__)

BTN_PARTNER = "🎭 Partner"
BTN_SHIFT = "⚡ Shift Angle"
BTN_SPARK = "🎲 Spark Topic"
BTN_COACH = "✨ Coach Last"

STUDIO_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_PARTNER), KeyboardButton(BTN_SHIFT)],
        [KeyboardButton(BTN_SPARK), KeyboardButton(BTN_COACH)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

BRACKET_RE = re.compile(r"\[([^\[\]]{3,80})\]")
_CARD_MEMO: dict[str, tuple[str, str]] = {}


def _stash_card(native_take: str, key_chunk: str) -> str:
    cid = hashlib.sha1(f"{native_take}|{key_chunk}".encode("utf-8")).hexdigest()[:10]
    if len(_CARD_MEMO) > 500:
        _CARD_MEMO.clear()
    _CARD_MEMO[cid] = (native_take, key_chunk)
    return cid


def coach_inline_kb(cid: str, include_audio_btn: bool = True, saved: bool = False) -> InlineKeyboardMarkup:
    save_lbl = "✅ Saved" if saved else "📌 Save Phrase"
    row = [
        InlineKeyboardButton(save_lbl, callback_data=f"coach:save:{cid}"),
        InlineKeyboardButton("🪜 3 Registers", callback_data=f"coach:reg:{cid}"),
    ]
    if include_audio_btn:
        row.append(InlineKeyboardButton("🔊 Hear US", callback_data=f"coach:tts:{cid}"))
    return InlineKeyboardMarkup([row])


def partner_inline_kb(active_angle: str) -> InlineKeyboardMarkup:
    rows = []
    for ang in ANGLES:
        meta = ANGLE_META[ang]
        prefix = "✅ " if ang == active_angle else ""
        rows.append([InlineKeyboardButton(f"{prefix}{meta['short_btn']}", callback_data=f"partner:{ang}")])
    return InlineKeyboardMarkup(rows)


def reset_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Fresh Chat (Keep Saved Phrases)", callback_data="reset:chat")],
        [InlineKeyboardButton("🗑️ Erase All Stored Data", callback_data="reset:all")],
        [InlineKeyboardButton("Cancel", callback_data="reset:cancel")],
    ])


def extract_stealable_chunk(text: str) -> str:
    m = BRACKET_RE.search(text)
    if m:
        return m.group(1).strip()
    code_m = re.search(r"🔑\s*(.+)$", text, flags=re.MULTILINE)
    if code_m:
        return code_m.group(1).strip()
    parts = [p.strip() for p in re.split(r"[.!?\n—]+", text) if len(p.strip().split()) >= 2]
    best = min(parts, key=lambda s: abs(len(s.split()) - 5)) if parts else text.strip()
    return " ".join(best.split()[:10])


class TwinContainer:
    def __init__(self, db_path=DB_PATH, snapshot_path=SNAPSHOT_PATH, passphrase=SNAPSHOT_PASSPHRASE, llm=None) -> None:
        self.db_path, self.snapshot_path, self.passphrase = db_path, snapshot_path, passphrase
        self.repo = TwinRepository(db_path=db_path, snapshot_path=snapshot_path, passphrase=passphrase)
        self.llm = llm or GeminiClient(self.repo)
        self.engine = EchoTwinEngine(self.repo, self.llm)

    def flush(self) -> None:
        flush_pending_snapshots()


async def _send_safe(message, text: str, **kwargs):
    last = None
    chunks = split_telegram_text(text)
    kb = kwargs.pop("reply_markup", STUDIO_KB)
    for i, ch in enumerate(chunks):
        last = await message.reply_text(ch, reply_markup=(kb if i == len(chunks) - 1 else None), **kwargs)
    return last


async def _react_quietly(message, emoji: str) -> None:
    fn = getattr(message, "set_reaction", None)
    if callable(fn):
        try:
            await fn(emoji)
        except Exception:
            pass


async def _deliver_coach_card(message, c: TwinContainer, uid: int, ghost: GhostTwinAnalysis, *, title: str = "Native Take", prefer_voice: bool = False) -> None:
    steal_target = ghost.key_chunk.strip() or extract_stealable_chunk(ghost.native_take)
    cid = _stash_card(ghost.native_take.strip(), steal_target)
    tag_html = f" · <i>{html.escape(ghost.why_tag)}</i>" if ghost.why_tag else ""
    chunk_html = f"\n🔑 <code>{html.escape(steal_target)}</code>"

    sent = None
    if prefer_voice and len(ghost.native_take) <= 750:
        mp3 = await synthesize_twin_voice(ghost.native_take)
        if mp3:
            cap = f"👥 <b>{html.escape(title)}</b>{tag_html}\n<tg-spoiler>{html.escape(ghost.native_take)}</tg-spoiler>{chunk_html}"
            buf = io.BytesIO(mp3)
            buf.name = "native_take.mp3"
            sent = await message.reply_voice(
                voice=buf, caption=cap, parse_mode="HTML", reply_markup=coach_inline_kb(cid, include_audio_btn=False)
            )
    if sent is None:
        body = f"👥 <b>{html.escape(title)}</b>{tag_html}\n\"{html.escape(ghost.native_take)}\"{chunk_html}"
        sent = await message.reply_text(
            body, parse_mode="HTML", reply_markup=coach_inline_kb(cid, include_audio_btn=True)
        )
    c.repo.log_turn(uid, "ghost", c.repo.get_user(uid)["angle"], steal_target, chat_message_id=getattr(sent, "message_id", None))
    if ghost.trigger_take2 and ghost.take2_prompt:
        await message.reply_text(f"🎬 <b>Take 2:</b> {html.escape(ghost.take2_prompt)}", parse_mode="HTML", reply_markup=STUDIO_KB)


def register_handlers(app: Application, c: TwinContainer) -> None:
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        logger.error("Unhandled error: %s", err, exc_info=err)
        msg = getattr(update, "effective_message", None)
        if msg:
            txt = str(err) if isinstance(err, (QuotaExceededError, LLMUnavailableError)) else "Hit a brief snag—send that again."
            try:
                await msg.reply_text(txt, reply_markup=STUDIO_KB)
            except Exception:
                pass

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        chat_id = update.effective_chat.id if update.effective_chat else uid
        asyncio.create_task(refresh_chat_menu_once(getattr(context, "bot", None), chat_id, force=True))
        u = c.repo.get_user(uid)
        meta = ANGLE_META.get(u["angle"], ANGLE_META["casual_sharp"])
        welcome = (
            f"🎙️ Spoken English Lab — {meta['badge']}\n\n"
            "Talk naturally by text or voice note:\n"
            "• 🎭 Partner — Switch between Malik, Claire & Prof. Vance\n"
            "• ⚡ Shift Angle — Flip the room onto your exact last thought\n"
            "• 🎲 Spark Topic — Jump into a live scenario or debate\n"
            "• ✨ Coach Last — Upgrade your last line into crisp US English\n\n"
            f"{meta['name']}: {meta['opener']}"
        )
        sent = await update.message.reply_text(welcome, reply_markup=STUDIO_KB)
        c.repo.log_turn(uid, "partner", u["angle"], meta["opener"], chat_message_id=getattr(sent, "message_id", None))

    async def partner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        u = c.repo.get_user(update.effective_user.id)
        await update.message.reply_text(
            "🎭 Choose who you're talking with:\n"
            "• Malik — Warm, quick-witted everyday Chicago English\n"
            "• Claire — Crisp NY workplace & executive strategy\n"
            "• Prof. Vance — Collegiate seminar pushback & ideas",
            reply_markup=partner_inline_kb(u["angle"]),
        )

    async def do_shift(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        new_angle, badge, line = await c.engine.shift_angle_live(uid)
        sent = await _send_safe(update.message, f"{badge}\n\n{line}")
        c.repo.log_turn(uid, "partner", new_angle, line, chat_message_id=getattr(sent, "message_id", None))

    async def do_spark(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        angle, badge, line = c.engine.spark_topic(uid)
        sent = await _send_safe(update.message, f"🎲 {badge}\n\n{line}")
        c.repo.log_turn(uid, "partner", angle, line, chat_message_id=getattr(sent, "message_id", None))

    async def do_coach_last(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        last_txt = c.repo.last_learner_turn(uid)
        if not last_txt:
            await _send_safe(update.message, "Send a voice note or message first, then tap ✨ Coach Last anytime to polish it!")
            return
        ghost = await c.engine.coach_on_demand(uid, last_txt)
        if not ghost:
            await _send_safe(update.message, "Couldn't build a recast right now—try again in a second.")
            return
        await _deliver_coach_card(update.message, c, uid, ghost, title="US Upgrade", prefer_voice=False)

    async def spark_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await do_spark(update)

    async def shift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await do_shift(update)

    async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        if context.args:
            ph = " ".join(context.args).strip()
            if c.repo.save_vault_phrase(uid, ph, "manual"):
                await _send_safe(update.message, f"📌 Saved to Vault: \"{ph}\" — I'll weave it into upcoming chats.")
            return
        p = c.repo.weekly_pulse(uid)
        items = c.repo.all_vault_phrases(uid, limit=10)
        pace = f"{p['avg_wpm']} avg / {p['peak_wpm']} peak WPM" if p["avg_wpm"] else "Send voice notes to measure spoken pace"
        phrase_lines = (
            "\n".join(f"• \"{it['phrase']}\" — {'🔥 used ' + str(it['hits']) + 'x' if it['hits'] else '⏳ queued'}" for it in items)
            if items
            else "• Tap 📌 Save Phrase on any coach card (or react 👍) to collect chunks."
        )
        txt = (
            f"📊 7-Day Studio Pulse\n"
            f"• Turns spoken: {p['learner_turns']} | Pace: {pace}\n\n"
            f"🔐 Saved US Chunks:\n{phrase_lines}"
        )
        await _send_safe(update.message, txt)

    async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text("Choose what you'd like to reset:", reply_markup=reset_inline_kb())

    async def partner_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user:
            return
        await q.answer()
        uid = update.effective_user.id
        target = (q.data or "").split(":", 1)[1]
        chosen, badge, opener = c.engine.switch_partner(uid, target)
        meta = ANGLE_META[chosen]
        try:
            await q.edit_message_text(
                f"✅ Switched to {badge}",
                reply_markup=partner_inline_kb(chosen),
            )
        except Exception:
            pass
        if q.message:
            sent = await _send_safe(q.message, f"{meta['name']}: {opener}")
            c.repo.log_turn(uid, "partner", chosen, opener, chat_message_id=getattr(sent, "message_id", None))

    async def coach_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user or not q.message:
            return
        parts = (q.data or "").split(":", 2)
        if len(parts) < 3:
            await q.answer()
            return
        _, action, cid = parts
        uid = update.effective_user.id
        msg_body = (getattr(q.message, "text", None) or getattr(q.message, "caption", None) or "").strip()
        cached = _CARD_MEMO.get(cid)
        native_line = cached[0] if cached else re.sub(r"^👥.*?\n", "", msg_body).split("\n🔑")[0].strip().strip('"')
        chunk = cached[1] if cached else extract_stealable_chunk(msg_body)

        if action == "save":
            c.repo.save_vault_phrase(uid, chunk, native_line[:160])
            await q.answer(f"Saved: {chunk}")
            has_voice = getattr(q.message, "voice", None) is not None
            try:
                await q.edit_message_reply_markup(reply_markup=coach_inline_kb(cid, include_audio_btn=(not has_voice), saved=True))
            except Exception:
                pass
        elif action == "reg":
            await q.answer("Building 3 registers...")
            regs = await c.engine.explain_registers(uid, native_line or chunk)
            await _send_safe(q.message, f"🪜 3 Spoken US Registers:\n{regs}")
        elif action == "tts":
            await q.answer("Generating US audio...")
            mp3 = await synthesize_twin_voice(native_line or chunk)
            if mp3:
                buf = io.BytesIO(mp3)
                buf.name = "us_take.mp3"
                await q.message.reply_voice(voice=buf, reply_markup=STUDIO_KB)
            else:
                await q.message.reply_text("Audio unavailable right now—try again shortly.", reply_markup=STUDIO_KB)

    async def reset_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user:
            return
        await q.answer()
        uid = update.effective_user.id
        act = (q.data or "").split(":", 1)[1]
        if act == "chat":
            c.repo.clear_conversation(uid)
            u = c.repo.get_user(uid)
            meta = ANGLE_META.get(u["angle"], ANGLE_META["casual_sharp"])
            await q.edit_message_text(f"🧹 Fresh conversation started (your saved Vault phrases are intact).\n\n{meta['name']}: {meta['opener']}")
        elif act == "all":
            erased = await asyncio.to_thread(c.repo.purge_user, uid)
            if c.passphrase:
                await asyncio.to_thread(export_encrypted_snapshot, c.db_path, c.snapshot_path, c.passphrase, True)
            await q.edit_message_text("🗑️ All your conversation history and saved data have been permanently erased." if erased else "Nothing stored for your account.")
        else:
            await q.edit_message_text("Cancelled—nothing changed.")

    async def _ghost_worker(message, uid: int, text: str, is_voice: bool, duration_s: float) -> None:
        try:
            ghost = await c.engine.analyze_ghost_twin(uid, text, is_voice=is_voice, duration_s=duration_s)
            if ghost:
                await _deliver_coach_card(message, c, uid, ghost, title="Native Take", prefer_voice=is_voice)
        except Exception as exc:
            logger.debug("Ghost Twin skipped: %s", exc)

    async def _process_turn(update: Update, context: ContextTypes.DEFAULT_TYPE, learner_text: str, *, is_voice: bool, duration_s: float = 0.0) -> None:
        msg = update.message
        uid = update.effective_user.id
        chat_id = update.effective_chat.id if update.effective_chat else uid
        asyncio.create_task(refresh_chat_menu_once(getattr(context, "bot", None), chat_id, force=False))

        outcome = await c.engine.respond_turn(
            uid, learner_text, chat_message_id=getattr(msg, "message_id", None),
            is_voice=is_voice, duration_s=duration_s, log_partner=False,
        )
        if outcome.vault_celebrations:
            await _react_quietly(msg, "🔥")

        if is_voice:
            mp3 = await synthesize_partner_voice(outcome.full_reply, angle=outcome.angle)
            if mp3:
                buf = io.BytesIO(mp3)
                buf.name = "reply.mp3"
                if len(outcome.full_reply) <= 820:
                    cap = f"<tg-spoiler>{html.escape(outcome.full_reply)}</tg-spoiler>"
                    sent = await msg.reply_voice(voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB)
                else:
                    await msg.reply_voice(voice=buf, reply_markup=STUDIO_KB)
                    sent = await _send_safe(msg, f"<tg-spoiler>{html.escape(outcome.full_reply)}</tg-spoiler>", parse_mode="HTML")
                c.repo.log_turn(uid, "partner", outcome.angle, outcome.full_reply, chat_message_id=getattr(sent, "message_id", None), is_voice=True)
            else:
                sent = await _send_safe(msg, outcome.full_reply)
                c.repo.log_turn(uid, "partner", outcome.angle, outcome.full_reply, chat_message_id=getattr(sent, "message_id", None))
        else:
            for seg in outcome.segments:
                sent = await _send_safe(msg, seg)
                c.repo.log_turn(uid, "partner", outcome.angle, seg, chat_message_id=getattr(sent, "message_id", None))

        if not outcome.was_repair:
            asyncio.create_task(_ghost_worker(msg, uid, learner_text, is_voice, duration_s))

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        uid = update.effective_user.id
        txt = update.message.text.strip()

        if txt in (BTN_PARTNER, "Partner"):
            await partner_cmd(update, context)
            return
        if txt in (BTN_SHIFT, "⚡ Shift", "Shift", "⚡"):
            await do_shift(update)
            return
        if txt in (BTN_SPARK, "Spark Topic"):
            await do_spark(update)
            return
        if txt in (BTN_COACH, "Coach Last"):
            await do_coach_last(update)
            return

        rep = getattr(update.message, "reply_to_message", None)
        rep_txt = (getattr(rep, "text", None) or getattr(rep, "caption", None) or "").strip()
        if rep_txt and txt in ("?", "؟"):
            clean_target = re.sub(r"^👥.*?\n", "", rep_txt).split("\n🔑")[0].strip()
            regs = await c.engine.explain_registers(uid, clean_target)
            await _send_safe(update.message, f"🪜 3 Spoken US Registers:\n{regs}")
            return
        if rep_txt and (txt == "+" or txt.startswith("+ ")):
            custom = txt[1:].strip()
            chunk = custom if len(custom) >= 3 else extract_stealable_chunk(rep_txt)
            if c.repo.save_vault_phrase(uid, chunk, rep_txt[:160]):
                await _react_quietly(update.message, "👍")
                await _send_safe(update.message, f"📌 Saved to Vault: \"{chunk}\"")
            return

        await _process_turn(update, context, txt, is_voice=False)

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.voice:
            return
        v = update.message.voice
        dur = float(getattr(v, "duration", 0) or 0)
        size = int(getattr(v, "file_size", 0) or 0)
        if dur > 180 or size > 8_000_000:
            await _send_safe(update.message, "Keep voice notes under 3 minutes so we stay fast and sharp!")
            return
        tg_file = await v.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        transcript = (await c.llm.transcribe_voice(update.effective_user.id, raw)).strip()
        if not transcript:
            await _send_safe(update.message, "Couldn't catch that audio clearly—try one more take?")
            return
        await _process_turn(update, context, transcript, is_voice=True, duration_s=dur)

    async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        mr = getattr(update, "message_reaction", None)
        if not mr or not getattr(mr, "new_reaction", None) or not getattr(mr, "user", None):
            return
        uid = mr.user.id
        turn = c.repo.find_turn_by_msg_id(uid, mr.message_id)
        if turn and turn.get("content"):
            chunk = turn["content"] if turn["role"] == "ghost" else extract_stealable_chunk(turn["content"])
            c.repo.save_vault_phrase(uid, chunk, turn["content"][:160])

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("partner", partner_cmd))
    app.add_handler(CommandHandler("spark", spark_cmd))
    app.add_handler(CommandHandler("shift", shift_cmd))
    app.add_handler(CommandHandler(["vault", "pulse", "phrases"], vault_cmd))
    app.add_handler(CommandHandler(["reset", "wipe", "delete_everything"], reset_cmd))
    app.add_handler(CallbackQueryHandler(partner_cb, pattern=r"^partner:"))
    app.add_handler(CallbackQueryHandler(coach_cb, pattern=r"^coach:"))
    app.add_handler(CallbackQueryHandler(reset_cb, pattern=r"^reset:"))
    ReactionCls = getattr(tg_ext, "MessageReactionHandler", None)
    if ReactionCls is not None:
        app.add_handler(ReactionCls(reaction_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)
