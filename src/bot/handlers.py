import asyncio, html, io, logging, re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import telegram.ext as tg_ext

from src.bot.setup_tg import split_telegram_text
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots
from src.db.repo import TwinRepository
from src.llm.client import GeminiClient, LLMUnavailableError, QuotaExceededError
from src.twin.engine import ANGLE_META, EchoTwinEngine
from src.voice.tts import synthesize_partner_voice, synthesize_twin_voice

logger = logging.getLogger(__name__)

SHIFT_KB = ReplyKeyboardMarkup([[KeyboardButton("⚡ Shift")]], resize_keyboard=True, is_persistent=True)
BRACKET_RE = re.compile(r"\[([^\[\]]{3,80})\]")


def extract_stealable_chunk(text: str) -> str:
    m = BRACKET_RE.search(text)
    if m:
        return m.group(1).strip()
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
    kb = kwargs.pop("reply_markup", SHIFT_KB)
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


def register_handlers(app: Application, c: TwinContainer) -> None:
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        logger.error("Unhandled error: %s", err, exc_info=err)
        msg = getattr(update, "effective_message", None)
        if msg:
            txt = str(err) if isinstance(err, (QuotaExceededError, LLMUnavailableError)) else "Hit a brief snag—send that again."
            try:
                await msg.reply_text(txt, reply_markup=SHIFT_KB)
            except Exception:
                pass

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        u = c.repo.get_user(update.effective_user.id)
        badge = ANGLE_META.get(u["angle"], ANGLE_META["casual_sharp"])["badge"]
        welcome = (
            f"🎙️ Echo Twin — Active: {badge}\n\n"
            "Just talk or drop a voice note—no menus needed:\n"
            "• Voice notes get spoken replies + tap-to-reveal spoiler text.\n"
            "• Mix Arabic or French mid-sentence anytime to get the exact US idiom [in brackets].\n"
            "• 👥 Native Take steps in with your own point spoken like an American.\n"
            "• Tap ⚡ Shift below to flip the room on your exact last thought.\n"
            "• React 👍/❤️ (or reply +) to steal a phrase; reply ? for a 4-line US X-Ray."
        )
        await update.message.reply_text(welcome, reply_markup=SHIFT_KB)

    async def do_shift(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        new_angle, badge, line = await c.engine.shift_angle_live(uid)
        sent = await _send_safe(update.message, f"{badge}\n\n{line}")
        c.repo.log_turn(uid, "partner", new_angle, line, chat_message_id=getattr(sent, "message_id", None))

    async def shift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await do_shift(update)

    async def pulse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        p = c.repo.weekly_pulse(update.effective_user.id)
        pace = f"{p['avg_wpm']} avg / {p['peak_wpm']} peak WPM" if p["avg_wpm"] else "Send voice notes to track spoken pace"
        act = ", ".join(f"\"{x['phrase']}\" ({x['hits']}x)" for x in p["activated_phrases"]) or "React 👍 or reply + on any line to start stealing chunks"
        ups = "\n".join(f"• {u['tag']}: \"{u['native_take']}\"" for u in p["recent_upgrades"]) or "• Keep talking—Native Takes will log your sharpest shifts here."
        txt = f"📊 7-Day Echo Pulse\n• Turns spoken: {p['learner_turns']}\n• Voice pace: {pace}\n• Activated chunks: {act}\n\nRecent Native Recasts:\n{ups}"
        await _send_safe(update.message, txt)

    async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        if context.args:
            ph = " ".join(context.args).strip()
            if c.repo.save_vault_phrase(uid, ph, "manual"):
                await _send_safe(update.message, f"📌 Locked in vault: \"{ph}\" — I'll bait you to use it soon.")
            return
        items = c.repo.all_vault_phrases(uid, limit=12)
        if not items:
            await _send_safe(update.message, "Vault is empty. React 👍/❤️ or swipe-reply + on any bubble to steal US expressions.")
            return
        lines = [f"• \"{it['phrase']}\" — {'🔥 spoken ' + str(it['hits']) + 'x' if it['hits'] else '⏳ waiting in ambush'}" for it in items]
        await _send_safe(update.message, "🔐 Your Stolen US Chunks:\n" + "\n".join(lines))

    async def wipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Erase everything", callback_data="wipe:yes"),
            InlineKeyboardButton("Cancel", callback_data="wipe:no"),
        ]])
        await update.message.reply_text("Erase all your conversation history, vault chunks, and pulse logs?", reply_markup=kb)

    async def wipe_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user:
            return
        await q.answer()
        if q.data == "wipe:yes":
            erased = await asyncio.to_thread(c.repo.purge_user, update.effective_user.id)
            if c.passphrase:
                await asyncio.to_thread(export_encrypted_snapshot, c.db_path, c.snapshot_path, c.passphrase, True)
            await q.edit_message_text("All your stored history and vault chunks have been permanently erased." if erased else "Nothing stored for your account.")
        else:
            await q.edit_message_text("Cancelled—your data is untouched.")

    async def _ghost_worker(message, uid: int, text: str, is_voice: bool, duration_s: float) -> None:
        try:
            ghost = await c.engine.analyze_ghost_twin(uid, text, is_voice=is_voice, duration_s=duration_s)
            if not ghost:
                return
            tag_html = f" · <i>{html.escape(ghost.why_tag)}</i>" if ghost.why_tag else ""
            chunk_html = f"\n🔑 <code>{html.escape(ghost.key_chunk)}</code>" if ghost.key_chunk else ""
            mp3 = await synthesize_twin_voice(ghost.native_take)
            sent = None
            if mp3 and len(ghost.native_take) <= 750:
                cap = f"👥 <b>Native Take</b>{tag_html}\n<tg-spoiler>{html.escape(ghost.native_take)}</tg-spoiler>{chunk_html}"
                buf = io.BytesIO(mp3)
                buf.name = "native_take.mp3"
                sent = await message.reply_voice(voice=buf, caption=cap, parse_mode="HTML", reply_markup=SHIFT_KB)
            else:
                body = f"👥 <b>Native Take</b>{tag_html}\n\"{html.escape(ghost.native_take)}\"{chunk_html}"
                sent = await message.reply_text(body, parse_mode="HTML", reply_markup=SHIFT_KB)
            steal_target = ghost.key_chunk.strip() or extract_stealable_chunk(ghost.native_take)
            c.repo.log_turn(uid, "ghost", c.repo.get_user(uid)["angle"], steal_target, chat_message_id=getattr(sent, "message_id", None))
            if ghost.trigger_take2 and ghost.take2_prompt:
                await message.reply_text(f"🎬 <b>Take 2:</b> {html.escape(ghost.take2_prompt)}", parse_mode="HTML", reply_markup=SHIFT_KB)
        except Exception as exc:
            logger.debug("Ghost Twin skipped: %s", exc)

    async def _process_turn(update: Update, context: ContextTypes.DEFAULT_TYPE, learner_text: str, *, is_voice: bool, duration_s: float = 0.0) -> None:
        msg = update.message
        uid = update.effective_user.id
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
                    sent = await msg.reply_voice(voice=buf, caption=cap, parse_mode="HTML", reply_markup=SHIFT_KB)
                    c.repo.log_turn(uid, "partner", outcome.angle, outcome.full_reply, chat_message_id=getattr(sent, "message_id", None), is_voice=True)
                else:
                    await msg.reply_voice(voice=buf, reply_markup=SHIFT_KB)
                    sent = await _send_safe(msg, f"<tg-spoiler>{html.escape(outcome.full_reply)}</tg-spoiler>", parse_mode="HTML")
                    c.repo.log_turn(uid, "partner", outcome.angle, outcome.full_reply, chat_message_id=getattr(sent, "message_id", None), is_voice=True)
            else:
                sent = await _send_safe(msg, outcome.full_reply)
                c.repo.log_turn(uid, "partner", outcome.angle, outcome.full_reply, chat_message_id=getattr(sent, "message_id", None))
        else:
            for seg in outcome.segments:
                sent = await _send_safe(msg, seg)
                c.repo.log_turn(uid, "partner", outcome.angle, seg, chat_message_id=getattr(sent, "message_id", None))

        asyncio.create_task(_ghost_worker(msg, uid, learner_text, is_voice, duration_s))

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        uid = update.effective_user.id
        txt = update.message.text.strip()
        if txt in ("⚡ Shift", "Shift", "⚡"):
            await do_shift(update)
            return

        rep = getattr(update.message, "reply_to_message", None)
        rep_txt = (getattr(rep, "text", None) or getattr(rep, "caption", None) or "").strip()
        if rep_txt and txt in ("?", "؟"):
            clean_target = re.sub(r"^👥\s*Native Take.*?\n", "", rep_txt).strip()
            xray = await c.engine.explain_swipe(uid, clean_target)
            await _send_safe(update.message, xray)
            return
        if rep_txt and (txt == "+" or txt.startswith("+ ")):
            custom = txt[1:].strip()
            chunk = custom if len(custom) >= 3 else extract_stealable_chunk(rep_txt)
            if c.repo.save_vault_phrase(uid, chunk, rep_txt[:160]):
                await _react_quietly(update.message, "👍")
                await _send_safe(update.message, f"📌 Stolen: \"{chunk}\" — watch for a chance to drop it naturally.")
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
    app.add_handler(CommandHandler("shift", shift_cmd))
    app.add_handler(CommandHandler("pulse", pulse_cmd))
    app.add_handler(CommandHandler("vault", vault_cmd))
    app.add_handler(CommandHandler(["wipe", "delete_everything"], wipe_cmd))
    app.add_handler(CallbackQueryHandler(wipe_cb, pattern=r"^wipe:"))
    ReactionCls = getattr(tg_ext, "MessageReactionHandler", None)
    if ReactionCls is not None:
        app.add_handler(ReactionCls(reaction_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)
