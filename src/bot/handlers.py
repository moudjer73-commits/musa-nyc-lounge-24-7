import asyncio, hashlib, html, io, logging, re
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import telegram.ext as tg_ext

from src.bot.setup_tg import refresh_chat_menu_once, split_telegram_text
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots
from src.db.repo import ANGLES, TwinRepository
from src.llm.client import GeminiClient, LLMUnavailableError, QuotaExceededError
from src.twin.calques import scan_transfers
from src.twin.engine import ANGLE_META, EchoTwinEngine, GhostTwinAnalysis, VoiceLabReport
from src.voice.tts import (
    VOICE_CLIP_CACHE,
    synthesize_audio_mirror,
    synthesize_partner_voice,
    synthesize_shadow_drill,
    synthesize_twin_voice,
)

logger = logging.getLogger(__name__)

BTN_LAB = "🔬 Prosody Lab"
BTN_STUDIOS = "🎛️ Studios"
BTN_PARTNER = "🎭 Partner"
BTN_SHIFT = "⚡ Shift Angle"
BTN_SHADOW = "🎯 Shadow 3x"
BTN_COACH = "✨ Coach Last"
BTN_SPARK = "🎲 Spark Topic"
BTN_MIRROR = "🪞 Audio Mirror"

STUDIO_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_LAB), KeyboardButton(BTN_STUDIOS)],
        [KeyboardButton(BTN_PARTNER), KeyboardButton(BTN_SHIFT)],
        [KeyboardButton(BTN_SHADOW), KeyboardButton(BTN_COACH)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

BRACKET_RE = re.compile(r"\[([^\[\]]{3,80})\]")
_CARD_MEMO: dict[str, tuple[str, str]] = {}


def _stash_card(native_take: str, key_chunk: str) -> str:
    cid = hashlib.sha1(f"{native_take}|{key_chunk}".encode("utf-8")).hexdigest()[:10]
    if len(_CARD_MEMO) > 600:
        _CARD_MEMO.clear()
    _CARD_MEMO[cid] = (native_take, key_chunk)
    return cid


def coach_inline_kb(
    cid: str, include_audio_btn: bool = True, saved: bool = False
) -> InlineKeyboardMarkup:
    save_lbl = "✅ Saved" if saved else "📌 Save"
    row1 = [
        InlineKeyboardButton("🎯 Shadow 3x", callback_data=f"coach:drill:{cid}"),
        InlineKeyboardButton(save_lbl, callback_data=f"coach:save:{cid}"),
        InlineKeyboardButton("🪜 3 Registers", callback_data=f"coach:reg:{cid}"),
    ]
    rows = [row1]
    if include_audio_btn:
        rows.append([InlineKeyboardButton("🔊 Hear Normal US", callback_data=f"coach:tts:{cid}")])
    return InlineKeyboardMarkup(rows)


def voice_lab_inline_kb(cid: str, saved: bool = False) -> InlineKeyboardMarkup:
    save_lbl = "✅ Saved" if saved else "📌 Save"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪞 Audio Mirror", callback_data=f"coach:mirror:{cid}"),
            InlineKeyboardButton("🎯 Shadow 3x", callback_data=f"coach:drill:{cid}"),
        ],
        [
            InlineKeyboardButton("🎙️ Retake A/B", callback_data=f"coach:retake:{cid}"),
            InlineKeyboardButton(save_lbl, callback_data=f"coach:save:{cid}"),
            InlineKeyboardButton("🪜 Registers", callback_data=f"coach:reg:{cid}"),
        ],
    ])


def lab_console_kb(lab_mode_on: bool) -> InlineKeyboardMarkup:
    mode_lbl = (
        "🟢 Pure Lab Mode: ON (Tap for Hybrid)"
        if lab_mode_on
        else "⚪ Pure Lab Mode: OFF (Tap for Pure Lab)"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mode_lbl, callback_data="lab:toggle")],
        [
            InlineKeyboardButton("🪞 Mirror Last Clip", callback_data="lab:mirror"),
            InlineKeyboardButton("🔗 Linking & Flap-T", callback_data="lab:drill:linking"),
        ],
        [
            InlineKeyboardButton("🎯 Stress & Schwa", callback_data="lab:drill:stress"),
            InlineKeyboardButton("↘️ Pitch & Focus", callback_data="lab:drill:intonation"),
        ],
    ])


def studios_console_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎲 Spark Debate / Scenario", callback_data="studio:spark"),
            InlineKeyboardButton("🪞 Mirror Last Voice", callback_data="studio:mirror"),
        ],
        [
            InlineKeyboardButton("🎯 3-Beat Shadow Last", callback_data="studio:shadow"),
            InlineKeyboardButton("📊 7-Day Pulse & Vault", callback_data="studio:pulse"),
        ],
    ])


def partner_inline_kb(active_angle: str) -> InlineKeyboardMarkup:
    rows = []
    for ang in ANGLES:
        meta = ANGLE_META[ang]
        prefix = "✅ " if ang == active_angle else ""
        rows.append([
            InlineKeyboardButton(f"{prefix}{meta['short_btn']}", callback_data=f"partner:{ang}")
        ])
    return InlineKeyboardMarkup(rows)


def reset_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Fresh Chat (Keep Vault & Memory)", callback_data="reset:chat")],
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


def _strip_coach_headers(raw_body: str) -> str:
    skip_prefixes = (
        "👥", "🎯", "🎙️", "🔬", "📐", "📈", "👂", "🗺️",
        "📊", "🗣️", "🔗", "🔁", "⚡", "🎧", "🔑", "🪜", "🪞", "• ",
    )
    lines = [ln for ln in raw_body.splitlines() if not ln.startswith(skip_prefixes)]
    joined = " ".join(lines).strip().lstrip("🇺🇸 ").strip().strip('"')
    return joined or raw_body.strip()


def format_prosody_lab_html(report: VoiceLabReport, *, pure_mode: bool = False) -> str:
    a = report.audit
    header = (
        "🔬 <b>US Pronunciation &amp; Prosody — A/B Retake</b>"
        if report.is_retake
        else "🔬 <b>US Pronunciation &amp; Prosody Lab</b>"
    )
    lines = [header]
    if report.delta_block:
        lines.append(f"📈 <b>Before ➔ After:</b>\n<code>{html.escape(report.delta_block)}</code>")
    elif report.dsp_badge:
        lines.append(f"📐 <code>{html.escape(report.dsp_badge)}</code>")

    if report.is_retake and a.retake_verdict:
        lines.append(f"✅ <b>Verdict:</b> {html.escape(a.retake_verdict)}")
    if report.habit_badge:
        lines.append(f"🔁 <b>{html.escape(report.habit_badge)}</b>")
    if a.verbatim_heard:
        lines.append(f"👂 <b>Heard:</b> \"{html.escape(a.verbatim_heard)}\"")
    if a.prosodic_map:
        lines.append(f"🗺️ <b>US Map:</b> <code>{html.escape(a.prosodic_map)}</code>")
    sounds_line = a.word_pronunciations or a.segmental_fix
    if sounds_line:
        lines.append(f"🗣️ <b>Sounds &amp; Words:</b> {html.escape(sounds_line)}")
    if a.stress_and_schwa:
        lines.append(f"🎯 <b>Stress &amp; Schwa:</b> {html.escape(a.stress_and_schwa)}")
    if a.prosody_fix:
        lines.append(f"🔗 <b>Rhythm &amp; Linking:</b> {html.escape(a.prosody_fix)}")
    if a.focus_stress:
        lines.append(f"🔊 <b>Peak:</b> <code>{html.escape(a.focus_stress)}</code>")
    if a.grammar_fix:
        lines.append(f"⚡ <b>Wording Fix:</b> <code>{html.escape(a.grammar_fix)}</code>")
        if a.native_recast:
            lines.append(f"🇺🇸 \"{html.escape(a.native_recast)}\"")
    return "\n".join(lines)


class TwinContainer:
    def __init__(
        self,
        db_path=DB_PATH,
        snapshot_path=SNAPSHOT_PATH,
        passphrase=SNAPSHOT_PASSPHRASE,
        llm=None,
    ) -> None:
        self.db_path = db_path
        self.snapshot_path = snapshot_path
        self.passphrase = passphrase
        self.repo = TwinRepository(
            db_path=db_path, snapshot_path=snapshot_path, passphrase=passphrase
        )
        self.llm = llm or GeminiClient(self.repo)
        self.engine = EchoTwinEngine(self.repo, self.llm)

    def flush(self) -> None:
        flush_pending_snapshots()


async def _send_safe(message, text: str, **kwargs):
    last = None
    chunks = split_telegram_text(text)
    kb = kwargs.pop("reply_markup", STUDIO_KB)
    for i, ch in enumerate(chunks):
        mk = kb if i == len(chunks) - 1 else None
        last = await message.reply_text(ch, reply_markup=mk, **kwargs)
    return last


async def _send_chat_action(
    context: ContextTypes.DEFAULT_TYPE | None, chat_id: int | None, action: str
) -> None:
    bot = getattr(context, "bot", None) if context else None
    fn = getattr(bot, "send_chat_action", None) if bot else None
    if callable(fn) and chat_id:
        try:
            await fn(chat_id=chat_id, action=action)
        except Exception:
            pass


async def _react_quietly(message, emoji: str) -> None:
    fn = getattr(message, "set_reaction", None)
    if callable(fn):
        try:
            await fn(emoji)
        except Exception:
            pass


async def _deliver_coach_card(
    message,
    c: TwinContainer,
    uid: int,
    ghost: GhostTwinAnalysis,
    *,
    title: str = "US Coach",
    prefer_voice: bool = False,
) -> None:
    steal_target = ghost.key_chunk.strip() or extract_stealable_chunk(ghost.native_take)
    cid = _stash_card(ghost.native_take.strip(), steal_target)
    tag_html = f" · <i>{html.escape(ghost.why_tag)}</i>" if ghost.why_tag else ""
    hab_html = f"\n🔁 <b>{html.escape(ghost.habit_badge)}</b>" if ghost.habit_badge else ""
    fix_html = f"\n⚡ <code>{html.escape(ghost.fix_summary)}</code>" if ghost.fix_summary else ""
    rhy_html = f"\n🎧 <i>{html.escape(ghost.rhythm_tip)}</i>" if ghost.rhythm_tip else ""
    chunk_html = f"\n🔑 <code>{html.escape(steal_target)}</code>" if steal_target else ""

    body = (
        f"🎯 <b>{html.escape(title)}</b>{tag_html}{hab_html}{fix_html}{rhy_html}\n"
        f"🇺🇸 \"{html.escape(ghost.native_take)}\"{chunk_html}"
    )
    sent = await message.reply_text(
        body, parse_mode="HTML", reply_markup=coach_inline_kb(cid, include_audio_btn=True)
    )
    c.repo.log_turn(
        uid, "ghost", c.repo.get_user(uid)["angle"],
        steal_target, chat_message_id=getattr(sent, "message_id", None),
    )
    if ghost.trigger_take2 and ghost.take2_prompt:
        await message.reply_text(
            f"🎬 <b>Take 2:</b> {html.escape(ghost.take2_prompt)}",
            parse_mode="HTML",
            reply_markup=STUDIO_KB,
        )


async def _deliver_lab_report(
    msg,
    c: TwinContainer,
    uid: int,
    angle: str,
    report: VoiceLabReport,
    *,
    raw_audio: bytes = b"",
    pure_mode: bool = False,
) -> None:
    a = report.audit
    has_gfix = bool(a.grammar_fix.strip() and a.native_recast.strip())
    target_spoken = (
        a.native_recast.strip()
        if has_gfix
        else (a.verbatim_heard.strip() or a.native_recast.strip())
    )
    drill_target = a.shadow_chunk.strip() or target_spoken
    cid = _stash_card(target_spoken, drill_target)
    if raw_audio:
        VOICE_CLIP_CACHE.stash(uid, cid, raw_audio, target_spoken)

    card_html = format_prosody_lab_html(report, pure_mode=pure_mode)
    if pure_mode or report.is_retake:
        mp3 = await synthesize_shadow_drill(drill_target or target_spoken)
    else:
        mp3 = await synthesize_twin_voice(target_spoken) if len(target_spoken) <= 650 else None

    if mp3 and len(card_html) <= 950:
        buf = io.BytesIO(mp3)
        buf.name = "us_pronunciation.mp3"
        sent = await msg.reply_voice(
            voice=buf,
            caption=card_html,
            parse_mode="HTML",
            reply_markup=voice_lab_inline_kb(cid),
        )
    else:
        sent = await msg.reply_text(
            card_html, parse_mode="HTML", reply_markup=voice_lab_inline_kb(cid)
        )
        if mp3:
            buf = io.BytesIO(mp3)
            buf.name = "us_pronunciation.mp3"
            await msg.reply_voice(voice=buf, reply_markup=STUDIO_KB)
    c.repo.log_turn(
        uid, "ghost", angle, drill_target, chat_message_id=getattr(sent, "message_id", None)
    )


async def _send_mirror_reply(
    reply_target, c: TwinContainer, uid: int, cid: str = "", custom_text: str = ""
) -> bool:
    pair = VOICE_CLIP_CACHE.fetch(uid, cid)
    if not pair:
        await reply_target.reply_text(
            "🪞 Send a quick voice note first so I can stitch your voice side-by-side with the US target!",
            reply_markup=STUDIO_KB,
        )
        return False
    learner_bytes, cached_target = pair
    target_txt = (custom_text or cached_target).strip()
    if not target_txt:
        target_txt = c.repo.last_learner_turn(uid) or "Let's try that one more time."
    c.repo.arm_lab_retake(uid, target_txt)
    mirror_mp3 = await synthesize_audio_mirror(learner_bytes, target_txt)
    if not mirror_mp3:
        await reply_target.reply_text(
            "Audio engine busy right now—try tapping 🪞 Audio Mirror again shortly.",
            reply_markup=STUDIO_KB,
        )
        return False
    buf = io.BytesIO(mirror_mp3)
    buf.name = "audio_mirror.mp3"
    cap = (
        "🪞 <b>Audio Mirror (Your Voice ➔ Native US)</b>\n"
        "<i>1️⃣ Your normalized take ➔ 2️⃣ General American target</i>\n"
        f"🇺🇸 <code>{html.escape(target_txt[:220])}</code>\n"
        "🎙️ <i>Record a retake right after to score your Before/After delta!</i>"
    )
    await reply_target.reply_voice(
        voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB
    )
    return True


def _lab_status_text(lab_on: bool) -> str:
    mode_desc = (
        "🟢 <b>Pure Prosody Lab Mode is ON</b>\n"
        "Every voice note goes 100% into clinical acoustic analysis (nPVI, Stress Contrast, "
        "Pitch, US Map, Audio Mirror &amp; 3-Beat Shadowing) with zero small talk."
        if lab_on
        else "⚪ <b>Single-Call Hybrid Mode Active (50% Quota Saver)</b>\n"
        "1 unified pass delivers your partner's distinct US voice reply PLUS your full "
        "Pronunciation &amp; Prosody Lab card + Audio Mirror."
    )
    return (
        f"🔬 <b>US Prosody &amp; Rhythm Lab v6</b>\n\n{mode_desc}\n\n"
        "👇 <b>Launch an acoustic tool or targeted drill:</b>"
    )


def register_handlers(app: Application, c: TwinContainer) -> None:
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        logger.error("Unhandled error: %s", err, exc_info=err)
        msg = getattr(update, "effective_message", None)
        if msg:
            is_q = isinstance(err, (QuotaExceededError, LLMUnavailableError))
            txt = str(err) if is_q else "Hit a brief snag—send that again."
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
        lab_on = bool(c.repo.get_lab_state(uid).get("lab_mode"))
        meta = ANGLE_META.get(u["angle"], ANGLE_META["casual_sharp"])
        mode_tag = "🔬 Pure Prosody Lab" if lab_on else meta["badge"]
        welcome = (
            f"🎙️ Spoken English Lab v6 — {mode_tag}\n\n"
            "• 🔬 Prosody Lab — Universal phonetic audit, Audio Mirror 🪞 & A/B retakes\n"
            "• 🎛️ Studios — Live scenarios, Audio Mirror, Shadowing & 7-Day Pulse\n"
            "• 🎭 Partner — Distinct US voices for Malik, Claire & Prof. Vance\n\n"
            f"{meta['name']}: {meta['opener']}"
        )
        sent = await update.message.reply_text(welcome, reply_markup=STUDIO_KB)
        c.repo.log_turn(
            uid, "partner", u["angle"], meta["opener"],
            chat_message_id=getattr(sent, "message_id", None),
        )

    async def lab_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        st = c.repo.get_lab_state(uid)
        on_flag = bool(st.get("lab_mode"))
        await update.message.reply_text(
            _lab_status_text(on_flag),
            parse_mode="HTML",
            reply_markup=lab_console_kb(on_flag),
        )

    async def studios_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        body = (
            "🎛️ <b>Spoken US Training Studios</b>\n\n"
            "• 🎲 <b>Spark Scenario</b> — Jump into a real US debate or workplace dilemma\n"
            "• 🪞 <b>Audio Mirror</b> — Hear your last voice clip stitched next to native US\n"
            "• 🎯 <b>3-Beat Shadow</b> — Slow-linked ➔ natural ➔ speak-along drill\n"
            "• 📊 <b>Pulse &amp; Vault</b> — Track your recurring habits &amp; saved chunks"
        )
        await update.message.reply_text(
            body, parse_mode="HTML", reply_markup=studios_console_kb()
        )

    async def mirror_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        chat_id = update.effective_chat.id if update.effective_chat else uid
        await _send_chat_action(context, chat_id, "record_voice")
        custom = " ".join(context.args).strip() if context.args else ""
        await _send_mirror_reply(update.message, c, uid, cid="", custom_text=custom)

    async def partner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        u = c.repo.get_user(update.effective_user.id)
        await update.message.reply_text(
            "🎭 Choose your interlocutor (each has a dedicated US voice & pacing):\n"
            "• Malik — Warm, relaxed everyday US (Guy)\n"
            "• Claire — Crisp, jargon-free US workplace (Aria)\n"
            "• Prof. Vance — Measured US seminar & office hours (Christopher)",
            reply_markup=partner_inline_kb(u["angle"]),
        )

    async def shadow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        rep = getattr(update.message, "reply_to_message", None)
        r_raw = (getattr(rep, "text", None) or getattr(rep, "caption", None) or "").strip() if rep else ""
        r_clean = _strip_coach_headers(r_raw) if r_raw else ""
        st = c.repo.get_lab_state(uid)
        fallback_line = st.get("recast_text") or st.get("target_text") or c.repo.last_learner_turn(uid)
        target = " ".join(context.args).strip() if context.args else (r_clean or fallback_line)
        if not target:
            await _send_safe(
                update.message,
                "Send a voice note or message first, or tap 🔬 Prosody Lab to launch a drill!",
            )
            return
        chat_id = update.effective_chat.id if update.effective_chat else uid
        await _send_chat_action(context, chat_id, "record_voice")
        c.repo.arm_lab_retake(uid, target)
        drill_mp3 = await synthesize_shadow_drill(target)
        if drill_mp3:
            buf = io.BytesIO(drill_mp3)
            buf.name = "shadow_3x.mp3"
            cap = (
                "🎯 <b>3-Beat Connected US Shadowing</b>\n"
                "<i>1️⃣ Linked slow (-14%) ➔ 2️⃣ Natural US ➔ 3️⃣ Speak along!</i>\n"
                f"🇺🇸 <code>{html.escape(target[:220])}</code>\n"
                "🎙️ <i>Send a voice note right after to get your A/B Score!</i>"
            )
            await update.message.reply_voice(
                voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB
            )
        else:
            await _send_safe(update.message, "Audio engine busy—try again in a moment.")

    async def do_shift(update: Update, context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        chat_id = update.effective_chat.id if update.effective_chat else uid
        await _send_chat_action(context, chat_id, "typing")
        out = await c.engine.shift_angle_live(uid)
        sent = await _send_safe(update.message, f"{out.badge}\n\n{out.partner_reply}")
        c.repo.log_turn(
            uid, "partner", out.angle, out.partner_reply,
            chat_message_id=getattr(sent, "message_id", None),
        )
        if out.reframed_point:
            chunk = out.key_chunk.strip() or extract_stealable_chunk(out.reframed_point)
            cid = _stash_card(out.reframed_point, chunk)
            if out.same_across_registers:
                body = (
                    f"🪜 <b>Your line here</b> · <i>Plain US across registers</i>\n"
                    f"🇺🇸 \"{html.escape(out.reframed_point)}\""
                )
            else:
                body = (
                    f"🪜 <b>How you'd say your point here</b>\n"
                    f"🇺🇸 \"{html.escape(out.reframed_point)}\"\n"
                    f"🔑 <code>{html.escape(chunk)}</code>"
                )
            card_msg = await update.message.reply_text(
                body, parse_mode="HTML", reply_markup=coach_inline_kb(cid, include_audio_btn=True)
            )
            c.repo.log_turn(
                uid, "ghost", out.angle, chunk,
                chat_message_id=getattr(card_msg, "message_id", None),
            )

    async def _send_spark_to(reply_msg, uid: int) -> None:
        angle, badge, line = c.engine.spark_topic(uid)
        sent = await _send_safe(reply_msg, f"🎲 {badge}\n\n{line}")
        c.repo.log_turn(
            uid, "partner", angle, line, chat_message_id=getattr(sent, "message_id", None)
        )

    async def do_spark(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        await _send_spark_to(update.message, update.effective_user.id)

    async def do_coach_last(update: Update) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        last_txt = c.repo.last_learner_turn(uid)
        if not last_txt:
            await _send_safe(
                update.message,
                "Send a voice note or message first, then tap ✨ Coach Last anytime!",
            )
            return
        ghost = await c.engine.coach_on_demand(uid, last_txt)
        if not ghost:
            await _send_safe(update.message, "Couldn't build a recast right now—try again shortly.")
            return
        await _deliver_coach_card(update.message, c, uid, ghost, title="US Upgrade")

    async def spark_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await do_spark(update)

    async def shift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await do_shift(update, context)

    async def _build_pulse_text(uid: int) -> str:
        p = c.repo.weekly_pulse(uid)
        items = c.repo.all_vault_phrases(uid, limit=8)
        mem_txt, _ = c.repo.get_memory(uid)
        pace = (
            f"{p['avg_wpm']} avg / {p['peak_wpm']} peak WPM"
            if p["avg_wpm"]
            else "Send voice notes to measure spoken pace"
        )
        habits = p.get("top_habits") or []
        habit_lines = (
            "\n".join(
                f"• {h['label']}: {h['hits']}x · "
                + (f"✅ {h['clean_streak']} clean" if h["clean_streak"] else "🎯 active")
                for h in habits
            )
            if habits
            else "• Send voice notes to map your prosody & grammar focus areas."
        )
        phrase_lines = (
            "\n".join(
                f"• \"{it['phrase']}\" — "
                + (f"🔥 used {it['hits']}x" if it["hits"] else "⏳ queued")
                for it in items
            )
            if items
            else "• Tap 📌 Save on any card to collect chunks."
        )
        mem_block = f"\n\n🧠 Partner Memory:\n{mem_txt}" if mem_txt else ""
        return (
            f"📊 7-Day Studio Pulse\n• Turns: {p['learner_turns']} | Pace: {pace}\n\n"
            f"🔁 Habit Radar:\n{habit_lines}\n\n🔐 Saved US Chunks:\n{phrase_lines}{mem_block}"
        )

    async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        if context.args:
            ph = " ".join(context.args).strip()
            if c.repo.save_vault_phrase(uid, ph, "manual"):
                await _send_safe(update.message, f"📌 Saved to Vault: \"{ph}\"")
            return
        await _send_safe(update.message, await _build_pulse_text(uid))

    async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "Choose what you'd like to reset:", reply_markup=reset_inline_kb()
        )

    async def lab_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user or not q.message:
            return
        uid = update.effective_user.id
        data = q.data or ""
        if data == "lab:toggle":
            cur = bool(c.repo.get_lab_state(uid).get("lab_mode"))
            now_on = c.repo.set_lab_mode(uid, not cur)
            await q.answer("Pure Prosody Lab: ON" if now_on else "Single-Call Hybrid: ON")
            try:
                await q.edit_message_text(
                    _lab_status_text(now_on),
                    parse_mode="HTML",
                    reply_markup=lab_console_kb(now_on),
                )
            except Exception:
                pass
            return
        if data == "lab:mirror":
            await q.answer("Building side-by-side Audio Mirror...")
            await _send_mirror_reply(q.message, c, uid)
            return
        if data.startswith("lab:drill:"):
            cat = data.split(":", 2)[2]
            await q.answer("Generating 3-beat US prosody drill...")
            sentence, p_map, focus_note = c.engine.pick_prosody_drill(uid, cat)
            cid = _stash_card(sentence, sentence)
            drill_mp3 = await synthesize_shadow_drill(sentence)
            cap = (
                f"🔬 <b>Prosody Lab Drill ({html.escape(cat.title())})</b>\n"
                f"🗺️ <b>US Map:</b> <code>{html.escape(p_map)}</code>\n"
                f"🔗 <b>Focus:</b> {html.escape(focus_note)}\n"
                f"🇺🇸 \"{html.escape(sentence)}\"\n"
                "🎙️ <i>Listen to the 3 beats, then record this line to score your rhythm!</i>"
            )
            if drill_mp3:
                buf = io.BytesIO(drill_mp3)
                buf.name = "lab_drill.mp3"
                await q.message.reply_voice(
                    voice=buf, caption=cap, parse_mode="HTML", reply_markup=voice_lab_inline_kb(cid)
                )
            else:
                await q.message.reply_text(
                    cap, parse_mode="HTML", reply_markup=voice_lab_inline_kb(cid)
                )

    async def studio_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user or not q.message:
            return
        uid = update.effective_user.id
        act = (q.data or "").split(":", 1)[1]
        if act == "spark":
            await q.answer("Launching live scenario...")
            await _send_spark_to(q.message, uid)
        elif act == "mirror":
            await q.answer("Stitching Audio Mirror...")
            await _send_mirror_reply(q.message, c, uid)
        elif act == "shadow":
            st = c.repo.get_lab_state(uid)
            tgt = st.get("recast_text") or st.get("target_text") or c.repo.last_learner_turn(uid)
            if not tgt:
                await q.answer("Send a voice note or message first!", show_alert=True)
                return
            await q.answer("Building 3-beat US shadow drill...")
            c.repo.arm_lab_retake(uid, tgt)
            mp3 = await synthesize_shadow_drill(tgt)
            if mp3:
                buf = io.BytesIO(mp3)
                buf.name = "shadow_3x.mp3"
                cap = (
                    "🎯 <b>3-Beat US Shadow Drill</b>\n"
                    f"<code>{html.escape(tgt[:220])}</code>\n"
                    "🎙️ <i>Record right after for your Before/After delta!</i>"
                )
                await q.message.reply_voice(
                    voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB
                )
        elif act == "pulse":
            await q.answer()
            await _send_safe(q.message, await _build_pulse_text(uid))

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
                f"✅ Switched to {badge}", reply_markup=partner_inline_kb(chosen)
            )
        except Exception:
            pass
        if q.message:
            sent = await _send_safe(q.message, f"{meta['name']}: {opener}")
            c.repo.log_turn(
                uid, "partner", chosen, opener, chat_message_id=getattr(sent, "message_id", None)
            )

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
        native_line = cached[0] if cached else _strip_coach_headers(msg_body)
        chunk = cached[1] if cached else extract_stealable_chunk(msg_body)

        if action == "save":
            c.repo.save_vault_phrase(uid, chunk, native_line[:160])
            await q.answer(f"Saved: {chunk}")
            has_voice = getattr(q.message, "voice", None) is not None
            is_vl = ("Pronunciation" in msg_body) or ("Prosody" in msg_body) or ("US Map:" in msg_body)
            n_kb = voice_lab_inline_kb(cid, True) if is_vl else coach_inline_kb(cid, not has_voice, True)
            try:
                await q.edit_message_reply_markup(reply_markup=n_kb)
            except Exception:
                pass
        elif action == "mirror":
            await q.answer("Stitching Your Voice ➔ Native US...")
            await _send_mirror_reply(q.message, c, uid, cid=cid, custom_text=native_line or chunk)
        elif action == "retake":
            c.repo.arm_lab_retake(uid, native_line or chunk)
            await q.answer("Retake armed! Record your voice note now.")
            await q.message.reply_text(
                "🎙️ <b>A/B Retake Armed</b>\n"
                "Record this line in your natural voice (focus on linking &amp; schwa):\n"
                f"🇺🇸 <code>{html.escape(native_line or chunk)}</code>",
                parse_mode="HTML",
                reply_markup=STUDIO_KB,
            )
        elif action == "reg":
            await q.answer("Checking real US registers...")
            regs = await c.engine.explain_registers(uid, native_line or chunk)
            await _send_safe(q.message, f"🪜 3 Spoken US Registers:\n{regs}")
        elif action == "drill":
            await q.answer("Building 3-beat US shadow drill...")
            c.repo.arm_lab_retake(uid, native_line or chunk)
            drill_mp3 = await synthesize_shadow_drill(chunk or native_line)
            if drill_mp3:
                buf = io.BytesIO(drill_mp3)
                buf.name = "shadow_3x.mp3"
                cap = (
                    "🎯 <b>3-Beat US Shadow Drill</b> (Slow linked ➔ Natural ➔ Speak along)\n"
                    f"<code>{html.escape(chunk or native_line)}</code>\n"
                    "🎙️ <i>Send your voice retake next for an instant Before/After score!</i>"
                )
                await q.message.reply_voice(
                    voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB
                )
            else:
                await q.message.reply_text(
                    "Audio unavailable right now—try again shortly.", reply_markup=STUDIO_KB
                )
        elif action == "tts":
            await q.answer("Generating US audio...")
            mp3 = await synthesize_twin_voice(native_line or chunk)
            if mp3:
                buf = io.BytesIO(mp3)
                buf.name = "us_take.mp3"
                await q.message.reply_voice(voice=buf, reply_markup=STUDIO_KB)
            else:
                await q.message.reply_text(
                    "Audio unavailable right now—try again shortly.", reply_markup=STUDIO_KB
                )

    async def reset_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user:
            return
        await q.answer()
        uid = update.effective_user.id
        act = (q.data or "").split(":", 1)[1]
        if act == "chat":
            c.repo.clear_conversation(uid)
            c.repo.clear_retake_flag(uid)
            u = c.repo.get_user(uid)
            meta = ANGLE_META.get(u["angle"], ANGLE_META["casual_sharp"])
            await q.edit_message_text(
                f"🧹 Fresh chat started (Vault, Habit Radar & Memory intact).\n\n"
                f"{meta['name']}: {meta['opener']}"
            )
        elif act == "all":
            erased = await asyncio.to_thread(c.repo.purge_user, uid)
            if c.passphrase:
                await asyncio.to_thread(
                    export_encrypted_snapshot, c.db_path, c.snapshot_path, c.passphrase, True
                )
            await q.edit_message_text(
                "🗑️ All stored data permanently erased." if erased else "Nothing stored."
            )
        else:
            await q.edit_message_text("Cancelled—nothing changed.")

    async def _fallback_coach_worker(message, uid: int, text: str) -> None:
        try:
            ghost = await c.engine.coach_on_demand(uid, text)
            if ghost:
                await _deliver_coach_card(message, c, uid, ghost, title="US Coach")
        except Exception as exc:
            logger.debug("Fallback coach skipped: %s", exc)

    async def _deliver_partner_voice_or_text(msg, uid: int, outcome, *, is_voice: bool) -> None:
        if is_voice:
            mp3 = await synthesize_partner_voice(outcome.full_reply, angle=outcome.angle)
            if mp3:
                buf = io.BytesIO(mp3)
                buf.name = "reply.mp3"
                if len(outcome.full_reply) <= 820:
                    cap = f"<tg-spoiler>{html.escape(outcome.full_reply)}</tg-spoiler>"
                    sent = await msg.reply_voice(
                        voice=buf, caption=cap, parse_mode="HTML", reply_markup=STUDIO_KB
                    )
                else:
                    await msg.reply_voice(voice=buf, reply_markup=STUDIO_KB)
                    sent = await _send_safe(
                        msg, f"<tg-spoiler>{html.escape(outcome.full_reply)}</tg-spoiler>",
                        parse_mode="HTML",
                    )
                c.repo.log_turn(
                    uid, "partner", outcome.angle, outcome.full_reply,
                    chat_message_id=getattr(sent, "message_id", None), is_voice=True,
                )
                return
        for seg in outcome.segments:
            sent = await _send_safe(msg, seg)
            c.repo.log_turn(
                uid, "partner", outcome.angle, seg,
                chat_message_id=getattr(sent, "message_id", None),
            )

    async def _process_text_turn(
        update: Update, context: ContextTypes.DEFAULT_TYPE, learner_text: str
    ) -> None:
        msg = update.message
        uid = update.effective_user.id
        chat_id = update.effective_chat.id if update.effective_chat else uid
        asyncio.create_task(refresh_chat_menu_once(getattr(context, "bot", None), chat_id, force=False))
        await _send_chat_action(context, chat_id, "typing")
        outcome = await asyncio.wait_for(
            c.engine.respond_turn(
                uid, learner_text,
                chat_message_id=getattr(msg, "message_id", None),
                is_voice=False, log_partner=False,
            ),
            timeout=40.0,
        )
        if outcome.vault_celebrations:
            await _react_quietly(msg, "🔥")
        await _deliver_partner_voice_or_text(msg, uid, outcome, is_voice=False)
        if outcome.inline_coach and not outcome.was_repair:
            await _deliver_coach_card(msg, c, uid, outcome.inline_coach, title="US Coach")
        elif not outcome.was_repair and scan_transfers(learner_text):
            asyncio.create_task(_fallback_coach_worker(msg, uid, learner_text))
        asyncio.create_task(c.engine.maybe_sync_memory(uid))

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        uid = update.effective_user.id
        txt = update.message.text.strip()

        if txt in (BTN_LAB, "Prosody Lab"):
            await lab_cmd(update, context)
            return
        if txt in (BTN_STUDIOS, "Studios"):
            await studios_cmd(update, context)
            return
        if txt in (BTN_PARTNER, "Partner"):
            await partner_cmd(update, context)
            return
        if txt in (BTN_SHIFT, "⚡ Shift", "Shift", "⚡"):
            await do_shift(update, context)
            return
        if txt in (BTN_SPARK, "Spark Topic"):
            await do_spark(update)
            return
        if txt in (BTN_MIRROR, "Audio Mirror"):
            context.args = []
            await mirror_cmd(update, context)
            return
        if txt in (BTN_SHADOW, "🎯 Shadow 2x", "Shadow 3x"):
            context.args = []
            await shadow_cmd(update, context)
            return
        if txt in (BTN_COACH, "Coach Last"):
            await do_coach_last(update)
            return

        rep = getattr(update.message, "reply_to_message", None)
        rep_txt = (getattr(rep, "text", None) or getattr(rep, "caption", None) or "").strip()
        if rep_txt and txt in ("?", "؟"):
            regs = await c.engine.explain_registers(uid, _strip_coach_headers(rep_txt))
            await _send_safe(update.message, f"🪜 3 Spoken US Registers:\n{regs}")
            return
        if rep_txt and (txt == "+" or txt.startswith("+ ")):
            custom = txt[1:].strip()
            chunk = custom if len(custom) >= 3 else extract_stealable_chunk(rep_txt)
            if c.repo.save_vault_phrase(uid, chunk, rep_txt[:160]):
                await _react_quietly(update.message, "👍")
                await _send_safe(update.message, f"📌 Saved to Vault: \"{chunk}\"")
            return

        await _process_text_turn(update, context, txt)

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.voice:
            return
        msg = update.message
        uid = update.effective_user.id
        v = msg.voice
        dur = float(getattr(v, "duration", 0) or 0)
        size = int(getattr(v, "file_size", 0) or 0)
        mime_type = getattr(v, "mime_type", None) or "audio/ogg"
        if dur > 180 or size > 8_000_000:
            await _send_safe(
                msg, "Keep voice notes under 3 minutes so phonetic analysis stays fast!"
            )
            return
        chat_id = update.effective_chat.id if update.effective_chat else uid
        asyncio.create_task(refresh_chat_menu_once(getattr(context, "bot", None), chat_id, force=False))
        await _send_chat_action(context, chat_id, "record_voice")

        tg_file = await v.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        lab_st = c.repo.get_lab_state(uid)
        pure_or_retake = bool(lab_st.get("lab_mode") or lab_st.get("pending_retake"))

        if pure_or_retake:
            angle = c.repo.get_user(uid)["angle"]
            report = await asyncio.wait_for(
                c.engine.run_deep_prosody_audit(
                    uid, raw, "", mime_type=mime_type,
                    force_retake=bool(lab_st.get("pending_retake")),
                    log_learner_turn=True,
                    chat_message_id=getattr(msg, "message_id", None),
                    duration_s=dur,
                ),
                timeout=45.0,
            )
            if report is None:
                await _send_safe(
                    msg, "Couldn't detect speech—try recording a bit closer to the mic."
                )
                return
            await _deliver_lab_report(
                msg, c, uid, angle, report, raw_audio=raw, pure_mode=True
            )
            return

        # Single-Call Hybrid Mode: 1 API call returns Partner reply + Full Prosody Lab!
        outcome, report = await asyncio.wait_for(
            c.engine.respond_hybrid_voice_turn(
                uid, raw, mime_type=mime_type,
                chat_message_id=getattr(msg, "message_id", None),
                duration_s=dur, log_partner=False,
            ),
            timeout=45.0,
        )
        if outcome.vault_celebrations:
            await _react_quietly(msg, "🔥")
        await _deliver_partner_voice_or_text(msg, uid, outcome, is_voice=True)
        if report is not None:
            await _deliver_lab_report(
                msg, c, uid, outcome.angle, report, raw_audio=raw, pure_mode=False
            )
        asyncio.create_task(c.engine.maybe_sync_memory(uid))

    async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        mr = getattr(update, "message_reaction", None)
        if not mr or not getattr(mr, "new_reaction", None) or not getattr(mr, "user", None):
            return
        uid = mr.user.id
        turn = c.repo.find_turn_by_msg_id(uid, mr.message_id)
        if turn and turn.get("content"):
            chunk = (
                turn["content"]
                if turn["role"] == "ghost"
                else extract_stealable_chunk(turn["content"])
            )
            c.repo.save_vault_phrase(uid, chunk, turn["content"][:160])

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("lab", lab_cmd))
    app.add_handler(CommandHandler("studios", studios_cmd))
    app.add_handler(CommandHandler("mirror", mirror_cmd))
    app.add_handler(CommandHandler("shadow", shadow_cmd))
    app.add_handler(CommandHandler("partner", partner_cmd))
    app.add_handler(CommandHandler("spark", spark_cmd))
    app.add_handler(CommandHandler("shift", shift_cmd))
    app.add_handler(CommandHandler(["vault", "pulse", "phrases"], vault_cmd))
    app.add_handler(CommandHandler(["reset", "wipe", "delete_everything"], reset_cmd))
    app.add_handler(CallbackQueryHandler(lab_cb, pattern=r"^lab:"))
    app.add_handler(CallbackQueryHandler(studio_cb, pattern=r"^studio:"))
    app.add_handler(CallbackQueryHandler(partner_cb, pattern=r"^partner:"))
    app.add_handler(CallbackQueryHandler(coach_cb, pattern=r"^coach:"))
    app.add_handler(CallbackQueryHandler(reset_cb, pattern=r"^reset:"))
    ReactionCls = getattr(tg_ext, "MessageReactionHandler", None)
    if ReactionCls is not None:
        app.add_handler(ReactionCls(reaction_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)
