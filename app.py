import asyncio
import html
import logging
import os
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Optional

import edge_tts
import requests
import telebot
from telebot import apihelper, types

apihelper.SESSION_TIME_TO_LIVE = 600
import interaction
import podcast
import replay
from ai_engine import DualLLMEngine
from config import ADMIN_ID, DEFAULT_VOICE, TELEGRAM_BOT_TOKEN, TEMP_AUDIO_DIR
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from flows import CallbackResult, V3Flows, View, script_hash
from guide_and_branding import GUIDE_CHAPTERS_HTML, generate_bot_branding_images
from orchestrator import ANALYSIS_WAIT_S, AnalysisOutcome, TurnOrchestrator, TurnResult
from policy_engine import (
    complete_decode_session,
    extract_alex_spoken_text,
    format_decode_phase1_message,
    format_decode_phase2_reveal,
    get_active_decode_session,
    register_workout_task,
    start_decode_session,
)
from schemas import ResponderReplyV2, SkipReason

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SpokenEnglishLab")

db = EventSourcedDatabase()
ai = DualLLMEngine()
orchestrator = TurnOrchestrator(db=db, ai=ai)
flows = V3Flows(db, ai)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True, num_threads=4) if TELEGRAM_BOT_TOKEN else None

# V3.0 cutover canary (S0 acceptance): while this is on, only ADMIN_ID is served.
CANARY_ADMIN_ONLY = os.getenv("CANARY_ADMIN_ONLY", "").strip().lower() in ("1", "true", "yes", "on")
CANARY_NOTICE = "🛠️ <i>Spoken English Lab is in a short private test. Back soon.</i>"
RETRY_NOTICE = "⏳ <i>Alex is catching his breath (high traffic). Please resend your message in a few seconds!</i>"


_LEARNER_LOCKS: dict[str, threading.Lock] = {}
_LEARNER_LOCKS_GUARD = threading.Lock()

def _get_learner_lock(lid) -> threading.Lock:
    key = str(lid)
    with _LEARNER_LOCKS_GUARD:
        if key not in _LEARNER_LOCKS:
            _LEARNER_LOCKS[key] = threading.Lock()
        return _LEARNER_LOCKS[key]

def _e(val) -> str:
    return html.escape(str(val if val is not None else ""))


def force_sync_all_telegram_scopes_and_profile(target_chat_id: int = ADMIN_ID) -> dict:
    report = {"ok": False, "steps": []}
    if not TELEGRAM_BOT_TOKEN:
        return report

    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    scopes = [
        {"type": "default"},
        {"type": "all_private_chats"},
        {"type": "all_group_chats"},
        {"type": "all_chat_administrators"},
    ]
    if target_chat_id:
        scopes.append({"type": "chat", "chat_id": int(target_chat_id)})

    lang_codes = ["", "en", "ar"]

    for sc in scopes:
        for lc in lang_codes:
            payload = {"scope": sc}
            if lc:
                payload["language_code"] = lc
            try:
                requests.post(f"{base_api}/deleteMyCommands", json=payload, timeout=8)
            except Exception:
                pass

    commands_list = [
        {"command": "start", "description": "Start or refresh your Spoken English Lab"},
        {"command": "settings", "description": "Adjust register, directness, engine & coaching"},
        {"command": "scene", "description": "Open spontaneous conversation scene"},
        {"command": "dilemma", "description": "Generate a live pragmatic dilemma"},
        {"command": "shift", "description": "Spoken-first register shift (Casual/Diplomatic/Executive)"},
        {"command": "register", "description": "Transform a thought across spoken registers"},
        {"command": "think", "description": "Analytical Think-Aloud sparring"},
        {"command": "workout", "description": "Spontaneous phrase retrieval workout"},
        {"command": "decode", "description": "2-phase connected speech & subtext challenge"},
        {"command": "debrief", "description": "End-of-scene recap: counts and top shifts"},
        {"command": "review", "description": "Review & Retrieval: phrases that are due"},
        {"command": "podcast", "description": "Generate your 2-host NYC learning podcast (V4.0)"},
        {"command": "replay", "description": "Pragmatic Replay Studio across 3 registers (V4.0)"},
        {"command": "progress", "description": "5-dimension mastery profile"},
        {"command": "guide", "description": "Interactive 5-chapter User Guide (EN + AR)"},
    ]
    active_scopes = [{"type": "default"}, {"type": "all_private_chats"}]
    if target_chat_id:
        active_scopes.append({"type": "chat", "chat_id": int(target_chat_id)})

    for sc in active_scopes:
        for lc in lang_codes:
            payload = {"commands": commands_list, "scope": sc}
            if lc:
                payload["language_code"] = lc
            try:
                r = requests.post(f"{base_api}/setMyCommands", json=payload, timeout=8).json()
                report["steps"].append(f"setMyCommands({sc['type']},{lc or 'default'}): {r.get('ok')}")
            except Exception as e:
                report["steps"].append(f"setMyCommands err: {e}")

    for cid in ([None, int(target_chat_id)] if target_chat_id else [None]):
        body = {"menu_button": {"type": "commands"}}
        if cid:
            body["chat_id"] = cid
        try:
            requests.post(f"{base_api}/setChatMenuButton", json=body, timeout=8)
        except Exception:
            pass

    luxury_name = "𝐒𝐩𝐨𝐤𝐞𝐧 𝐄𝐧𝐠𝐥𝐢𝐬𝐡 𝐋𝐚𝐛 🎙️"
    short_bio = (
        "Master real Spoken American English across all registers 🎙️ "
        "معمل التحدث الطبيعي والأسلوب الأمريكي الأصيل."
    )
    full_desc = (
        "Welcome to 𝐒𝐩𝐨𝐤𝐞𝐧 𝐄𝐧𝐠𝐥𝐢𝐬𝐡 𝐋𝐚𝐛 🎙️\n"
        "Your personal studio for mastering real, unscripted Spoken American English (B2–C2).\n\n"
        "• 💬 Natural Flow First: Chat with Alex without robotic interrogations or textbook scripts.\n"
        "• 💡 Surgical On-Demand Coaching: Fix L1-Arabic transfer traps, collocations & tone on your terms.\n"
        "• 🎛️ 6 Live Registers: Shift seamlessly between Casual, Warm, Witty, Diplomatic, Executive & Analytical.\n"
        "• 🤝 Pragmatic Dilemmas, Listen & Decode, and Unaided Retrieval.\n\n"
        "اضغط Start لبدء المحادثة والتدريب العملي فوراً 👇"
    )

    for lc in lang_codes:
        lang_param = {"language_code": lc} if lc else {}
        try:
            requests.post(f"{base_api}/setMyName", json={"name": luxury_name, **lang_param}, timeout=8)
            requests.post(f"{base_api}/setMyShortDescription", json={"short_description": short_bio, **lang_param}, timeout=8)
            requests.post(f"{base_api}/setMyDescription", json={"description": full_desc, **lang_param}, timeout=8)
        except Exception as e:
            logger.warning(f"Profile sync notice ({lc}): {e}")

    report["ok"] = True
    return report


def build_reply_keyboard() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("💬 Open Scene"),
        types.KeyboardButton("🎛️ Register Shift"),
        types.KeyboardButton("🤝 Pragmatic Dilemma"),
        types.KeyboardButton("🧠 Think-Aloud"),
        types.KeyboardButton("🏋️ Phrase Workout"),
        types.KeyboardButton("🎧 Listen & Decode"),
        types.KeyboardButton("⚙️ Register & Tone"),
        types.KeyboardButton("📊 My Progress"),
        types.KeyboardButton("🏁 Scene Debrief"),
        types.KeyboardButton("🔁 Review"),
        types.KeyboardButton("🎙️ Podcast"),
        types.KeyboardButton("🎭 Replay"),
        types.KeyboardButton("📖 User Guide"),
        types.KeyboardButton("🔄 New Scene"),
    )
    return markup


def build_live_settings_panel(lid: int) -> tuple[str, types.InlineKeyboardMarkup]:
    sess = db.get_or_create_active_session(lid)
    cfg = db.get_learner_settings(lid)

    cur_reg = sess.get("register_profile") or cfg.get("default_register") or "casual"
    cur_dir = sess.get("directness") or cfg.get("default_directness") or "balanced"
    cur_fb = cfg.get("feedback_preference") or "light"
    cur_ar = cfg.get("arabic_support") or "bilingual"
    cur_mode = sess.get("training_mode") or "open_scene"

    cur_tier = getattr(ai, "model_tier", "flash")
    active_chain = ai.get_active_model_chain(prefer_pro=(cur_tier == "pro"))
    primary_m = active_chain[0] if active_chain else "gemini-2.5-flash"

    text_html = (
        "🎛️ <b>Spoken English Lab — Live Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Active Mode:</b> <code>{_e(cur_mode)}</code>\n"
        f"• <b>Spoken Register:</b> <b>{_e(cur_reg.upper())}</b>\n"
        f"• <b>Pragmatic Directness:</b> <b>{_e(cur_dir.upper())}</b>\n"
        f"• <b>Coaching Intensity:</b> <b>{_e(cur_fb.upper())}</b>\n"
        f"• <b>Gemini Engine:</b> <code>{_e(primary_m)}</code> (<b>{_e(cur_tier.upper())}</b>)\n"
        f"• <b>Arabic Nuance Notes:</b> <b>{_e(cur_ar.upper())}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap any button below to switch immediately (changes persist across scenes):</i>"
    )

    markup = types.InlineKeyboardMarkup(row_width=3)

    regs = [
        ("casual", "Casual"),
        ("warm", "Warm"),
        ("witty", "Witty"),
        ("diplomatic", "Diplomatic"),
        ("executive", "Executive"),
        ("analytical", "Analytical"),
    ]
    reg_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_reg == k else ''}{label}",
            callback_data=f"setreg:{k}",
        )
        for k, label in regs
    ]
    markup.row(*reg_btns[:3])
    markup.row(*reg_btns[3:])

    dirs = [("soft", "🕊️ Soft"), ("balanced", "⚖️ Balanced"), ("direct", "🎯 Direct")]
    dir_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_dir == k else ''}{label}",
            callback_data=f"setdir:{k}",
        )
        for k, label in dirs
    ]
    markup.row(*dir_btns)

    fbs = [("silent", "🔇 Silent"), ("light", "💡 Light"), ("active", "🔬 Active")]
    fb_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_fb == k else ''}{label}",
            callback_data=f"setfb:{k}",
        )
        for k, label in fbs
    ]
    markup.row(*fb_btns)

    ars = [("bilingual", "🇦🇪 AR Notes: ON"), ("english_only", "🇺🇸 English Only")]
    ar_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_ar == k else ''}{label}",
            callback_data=f"setar:{k}",
        )
        for k, label in ars
    ]
    markup.row(*ar_btns)

    tiers = [("flash", "⚡ Flash (Fastest)"), ("pro", "🧠 Pro (Deepest)")]
    tier_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_tier == k else ''}{label}",
            callback_data=f"setmodel:{k}",
        )
        for k, label in tiers
    ]
    markup.row(*tier_btns)

    auto_v = db.get_auto_voice(lid)
    v_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if not auto_v else ''}🎧 Voice: On Tap",
            callback_data="setvoice:tap",
        ),
        types.InlineKeyboardButton(
            f"{'✅ ' if auto_v else ''}🔊 Voice: Auto",
            callback_data="setvoice:auto",
        ),
    ]
    markup.row(*v_btns)

    return text_html, markup


def render_layer2_card_html(reply_text: str, diag: dict) -> str:
    lines = [
        f"💬 <b>Alex:</b> {_e(reply_text)}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💡 <b>Quick Upgrade</b> (<i>{_e(diag.get('kind', 'usage'))}</i>)",
        f"• <b>You said:</b> “<s>{_e(diag.get('original_span', ''))}</s>”",
    ]
    if diag.get("alternative"):
        lines.append(f"• <b>More natural:</b> “<b>{_e(diag['alternative'])}</b>”")
    for alt in diag.get("secondary_alternatives", []):
        lines.append(
            f"• <b>Also natural ({_e(alt.get('register_profile', 'casual'))}):</b> "
            f"“<b>{_e(alt.get('phrase', ''))}</b>” — <i>{_e(alt.get('pragmatic_effect', ''))}</i>"
        )
    if diag.get("listener_effect"):
        lines.append(f"• <b>How it sounds:</b> <i>{_e(diag['listener_effect'])}</i>")
    lines.append(f"• <b>Why:</b> {_e(diag.get('explanation_en', ''))}")
    if diag.get("l1_source_ar") or diag.get("l1_pattern"):
        src_ar = f"«{_e(diag['l1_source_ar'])}» " if diag.get("l1_source_ar") else ""
        pat_code = f"(<code>{_e(diag['l1_pattern'])}</code>)" if diag.get("l1_pattern") else ""
        lines.append(f"• <b>L1 Transfer:</b> {src_ar}{pat_code}")
    if diag.get("explanation_ar"):
        lines.append(f"• <b>بالعربية:</b> <tg-spoiler>{_e(diag['explanation_ar'])}</tg-spoiler>")

    extra_list = diag.get("additional_turn_findings") or []
    if extra_list:
        lines.append("\n🔎 <b>Also spotted in your turn:</b>")
        for ex in extra_list:
            ex_ar = f" — <tg-spoiler>{_e(ex['explanation_ar'])}</tg-spoiler>" if ex.get("explanation_ar") else ""
            lines.append(
                f"• “<s>{_e(ex.get('original_span', ''))}</s>” → “<b>{_e(ex.get('alternative', ''))}</b>”{ex_ar}"
            )

    return "\n".join(lines)


def synthesize_tts_mp3(text: str) -> str:
    out_path = os.path.join(TEMP_AUDIO_DIR, f"tts_{uuid.uuid4().hex[:8]}.mp3")

    async def _run():
        comm = edge_tts.Communicate(text, DEFAULT_VOICE)
        await comm.save(out_path)

    asyncio.run(_run())
    return out_path


def build_guide_inline_markup(active_key: str = "overview") -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    chapters = [
        ("overview", "📖 Overview"),
        ("ch1_layers", "🏛️ 1. 3-Layer System"),
        ("ch2_modes", "🎯 2. Training Modes"),
        ("ch3_registers", "⚙️ 3. Registers & Tone"),
        ("ch4_mastery", "📊 4. Mastery Ladder"),
    ]
    buttons = []
    for k, label in chapters:
        prefix = "✅ " if k == active_key else ""
        buttons.append(types.InlineKeyboardButton(f"{prefix}{label}", callback_data=f"guide:{k}"))
    markup.add(*buttons)
    return markup


# ------------------------------------------------------------------------------ V3.0 delivery helpers
# Everything below takes the bot as a parameter, so the scripted end-to-end test can drive it with a fake.


def _markup(view: View) -> Optional[types.InlineKeyboardMarkup]:
    if not view.buttons:
        return None
    markup = types.InlineKeyboardMarkup()
    for row in view.buttons:
        markup.row(*[types.InlineKeyboardButton(b.label, callback_data=b.data) for b in row])
    return markup


def _allowed(lid: int) -> bool:
    return (not CANARY_ADMIN_ONLY) or int(lid) == int(ADMIN_ID)


def begin_command(bot_, chat_id: int, lid: int) -> bool:
    """Every command and keyboard button: canary gate, then close the open interaction row (S0 5.4)."""
    if not _allowed(lid):
        bot_.send_message(chat_id, CANARY_NOTICE, parse_mode="HTML")
        return False
    flows.close_active(lid)
    return True


def send_view(bot_, chat_id: int, view: View):
    return bot_.send_message(chat_id, view.text, parse_mode="HTML", reply_markup=_markup(view))


def edit_view(bot_, chat_id: int, message_id: int, view: View) -> None:
    try:
        bot_.edit_message_text(
            view.text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=_markup(view)
        )
    except Exception as err:  # "message is not modified" and deleted messages are not errors for the learner
        logger.info("edit_view skipped: %s", err)


def _answer(bot_, call_id: str, text: Optional[str] = None) -> None:
    try:
        bot_.answer_callback_query(call_id, text) if text else bot_.answer_callback_query(call_id)
    except Exception as err:
        logger.info("answer_callback_query skipped: %s", err)


def send_alex_voice(bot_, chat_id: int, text: str) -> bool:
    """Speak ``text``. A line that was spoken before is sent by its stored Telegram file id (``audio_cache``)."""
    spoken = (extract_alex_spoken_text(text) or text or "").strip()
    if not spoken:
        return False
    key = script_hash(spoken)
    cached = db.get_audio_file_id(key, DEFAULT_VOICE)
    if cached:
        try:
            bot_.send_voice(chat_id, cached)
            return True
        except Exception as err:  # Telegram forgot the file: drop the entry and synthesize again
            logger.info("cached voice rejected: %s", err)
            db.drop_audio_file_id(key, DEFAULT_VOICE)
    path = ""
    try:
        path = synthesize_tts_mp3(spoken)
        with open(path, "rb") as f_audio:
            sent = bot_.send_voice(chat_id, f_audio)
        voice = getattr(sent, "voice", None)
        file_id = getattr(voice, "file_id", None)
        if file_id:
            db.put_audio_file_id(key, DEFAULT_VOICE, str(file_id), getattr(voice, "file_unique_id", None), getattr(voice, "duration", None))
        return True
    except Exception as err:
        logger.warning("TTS error: %s", err)
        return False
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def _listen_markup(reply_event_seq: int, upgrade_callback: Optional[str] = None) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    if upgrade_callback:
        buttons.append(types.InlineKeyboardButton("💡 Quick Upgrade", callback_data=upgrade_callback))
    buttons.append(types.InlineKeyboardButton("🎧 Listen", callback_data=interaction.build_callback("ls", reply_event_seq, 0)))
    markup.add(*buttons)
    return markup


def run_scene_turn(
    bot_,
    chat_id: int,
    lid: int,
    text: str,
    *,
    message_id: Optional[int] = None,
    origin: str = "scene",
    interaction_id: Optional[int] = None,
    background: bool = True,
) -> Optional[TurnResult]:
    """One scene turn (S0 6): the reply goes out as soon as the Responder has it; the Analyst finishes in the background."""
    if message_id is not None:
        key = f"tg_msg_{chat_id}_{message_id}"
    else:
        key = f"tg_{origin}_{chat_id}_{interaction_id if interaction_id is not None else uuid.uuid4().hex[:8]}"
    sent: dict[str, Any] = {}

    def _deliver(reply, handle) -> None:
        markup = _listen_markup(handle.reply_event_seq)
        sent["first"] = bot_.send_message(chat_id, f"💬 <b>Alex:</b> {_e(reply.reply_text)}", parse_mode="HTML", reply_markup=markup)
        if reply.second_bubble_text:
            bot_.send_message(chat_id, f"💬 <b>Alex:</b> {_e(reply.second_bubble_text)}", parse_mode="HTML", reply_markup=markup)
        if db.get_auto_voice(lid):
            spoken = reply.reply_text if not reply.second_bubble_text else f"{reply.reply_text} {reply.second_bubble_text}"
            send_alex_voice(bot_, chat_id, spoken)

    try:
        try:
            bot_.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        with _get_learner_lock(lid):
            res = orchestrator.process_turn(
                learner_id=lid,
                learner_text=text,
                message_idempotency_key=key,
                is_voice_message=False,
                origin=origin,
                interaction_id=interaction_id,
                telegram_message_id=message_id,
                on_fast_reply=_deliver,
            )
    except Exception as err:
        logger.error("Turn error: %s", err, exc_info=True)
        bot_.send_message(chat_id, RETRY_NOTICE, parse_mode="HTML")
        return None
    if res.duplicate or res.handle is None:
        return None
    if background:
        threading.Thread(target=finish_turn, args=(bot_, chat_id, lid, res, sent.get("first")), daemon=True).start()
    else:
        finish_turn(bot_, chat_id, lid, res, sent.get("first"))
    return res


def finish_turn(bot_, chat_id: int, lid: int, res: TurnResult, first_message: Any = None) -> None:
    """Wait for the Analyst, apply its outcome (one compare-and-set on the revision), then show what the policy allows."""
    handle = res.handle
    try:
        outcome = res.analysis_future.result(timeout=ANALYSIS_WAIT_S)
    except FutureTimeout:
        outcome = AnalysisOutcome("skipped", SkipReason.DEADLINE, None, "analysis wait elapsed")
    except Exception as err:
        outcome = AnalysisOutcome("skipped", SkipReason.ERROR, None, str(err)[:200])
    try:
        with _get_learner_lock(lid):
            actions = orchestrator.apply_analysis_outcome(learner_id=lid, handle=handle, outcome=outcome)
    except Exception as err:
        logger.error("apply_analysis_outcome failed: %s", err, exc_info=True)
        return
    try:
        if actions.followup_text:
            bot_.send_message(chat_id, f"💬 <b>Alex:</b> {_e(actions.followup_text)}", parse_mode="HTML")
        if actions.attach_upgrade_button and actions.upgrade_callback and actions.card_id and first_message is not None:
            bot_.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=first_message.message_id,
                reply_markup=_listen_markup(handle.reply_event_seq, actions.upgrade_callback),
            )
            flows.mark_card_offered(lid, res.session_id, actions.card_id, handle.turn_event_id, first_message.message_id)
    except Exception as err:
        logger.warning("post-turn delivery failed: %s", err)


def on_text(bot_, chat_id: int, lid: int, text: str, message_id: Optional[int] = None, *, background: bool = True) -> None:
    """A free-text message that is not a keyboard button: the open interaction, a legacy mode, or a scene turn."""
    if not _allowed(lid):
        bot_.send_message(chat_id, CANARY_NOTICE, parse_mode="HTML")
        return
    try:
        with _get_learner_lock(lid):
            sess = db.get_or_create_active_session(lid)
            sid = str(sess["session_id"])
            routed = flows.handle_text(lid, text)
            if routed.views:
                for view in routed.views:
                    send_view(bot_, chat_id, view)
                return
            active_dec = get_active_decode_session(str(lid)) or get_active_decode_session(str(chat_id))
            if active_dec is not None:
                ch_obj = active_dec["challenge"]
                eval_res = ai.evaluate_listen_decode_attempt(ch_obj, text)
                complete_decode_session(str(lid))
                complete_decode_session(str(chat_id))
                db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation")
                bot_.send_message(chat_id, format_decode_phase2_reveal(ch_obj, eval_res), parse_mode="HTML")
                return
            if sess.get("training_mode") == "register_shift":
                rs_out = ai.generate_register_shift_coaching(text)
                db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation")
                rs_html = (
                    f"🎛️ <b>Register Shift: {_e(rs_out.communicative_intention)}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Casual (Friend):</b> “{_e(rs_out.casual_friend_version)}”\n"
                    f"• <b>Diplomatic (Colleague):</b> “{_e(rs_out.diplomatic_colleague_version)}”\n"
                    f"• <b>Executive (Manager):</b> “{_e(rs_out.executive_manager_version)}”\n\n"
                    f"<b>Why it shifts:</b> {_e(rs_out.key_pragmatic_distinction_en)}\n"
                    f"<b>بالعربية:</b> <tg-spoiler>{_e(rs_out.key_pragmatic_distinction_ar)}</tg-spoiler>\n\n"
                    f"🎯 <b>Try it:</b> {_e(rs_out.practice_prompt)}"
                )
                bot_.send_message(chat_id, rs_html, parse_mode="HTML")
                return
    except Exception as err:
        logger.error("Text routing error: %s", err, exc_info=True)
        bot_.send_message(chat_id, RETRY_NOTICE, parse_mode="HTML")
        return
    run_scene_turn(bot_, chat_id, lid, text, message_id=message_id, background=background)



_LAST_PODCAST_EPISODES: dict[int, podcast.PodcastEpisodeScript] = {}


def deliver_podcast_episode(bot_, chat_id: int, lid: int, mode: podcast.PodcastMode = "recap", message_id: Optional[int] = None) -> None:
    try:
        bot_.send_chat_action(chat_id, "record_voice")
    except Exception:
        pass
    ep = podcast.generate_podcast_episode(db, ai, lid, mode=mode)
    _LAST_PODCAST_EPISODES[int(lid)] = ep
    with_ar = str(db.get_learner_settings(lid).get("arabic_support", "bilingual")) != "english_only"
    card_html = podcast.render_podcast_card_html(ep, show_transcript=False, with_arabic=with_ar)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📜 Show Transcript", callback_data="pod:tx:show"),
        types.InlineKeyboardButton("🔄 Recap", callback_data="pod:mode:recap"),
        types.InlineKeyboardButton("☕ Briefing", callback_data="pod:mode:briefing"),
        types.InlineKeyboardButton("🎬 Narrative", callback_data="pod:mode:narrative"),
    )
    if message_id is not None:
        try:
            bot_.edit_message_text(card_html, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
        except Exception:
            bot_.send_message(chat_id, card_html, parse_mode="HTML", reply_markup=markup)
    else:
        bot_.send_message(chat_id, card_html, parse_mode="HTML", reply_markup=markup)

    key = podcast.episode_script_hash(ep)
    cached_id = db.get_audio_file_id(key, podcast.PODCAST_VOICE_TAG)
    if cached_id:
        try:
            bot_.send_voice(chat_id, cached_id, caption=f"🎙️ {ep.title} (Alex & Maya)")
            return
        except Exception:
            db.drop_audio_file_id(key, podcast.PODCAST_VOICE_TAG)

    mp3_path = ""
    try:
        mp3_path = podcast.synthesize_podcast_mp3(ep)
        with open(mp3_path, "rb") as f_audio:
            sent = bot_.send_voice(chat_id, f_audio, caption=f"🎙️ {ep.title} (Alex & Maya)")
        voice = getattr(sent, "voice", None)
        file_id = getattr(voice, "file_id", None)
        if file_id:
            db.put_audio_file_id(key, podcast.PODCAST_VOICE_TAG, str(file_id), getattr(voice, "file_unique_id", None), getattr(voice, "duration", None))
    except Exception as err:
        logger.warning("Podcast TTS synthesis warning: %s", err)
    finally:
        if mp3_path and os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass


def send_progress(bot_, chat_id: int, lid: int) -> None:
    send_view(bot_, chat_id, flows.progress_view(lid))


def send_debrief(bot_, chat_id: int, lid: int) -> None:
    send_view(bot_, chat_id, flows.debrief_view(lid))


def send_review(bot_, chat_id: int, lid: int) -> None:
    with _get_learner_lock(lid):
        views = flows.start_review(lid)
    for view in views:
        send_view(bot_, chat_id, view)


def _v3_callback(bot_, call_id: str, lid: int, chat_id: int, message_id: int, data: str, *, background: bool = True) -> None:
    from flows import TurnSubmission  # noqa: F401  (type of CallbackResult.submit_turn)

    with _get_learner_lock(lid):
        res: CallbackResult = flows.handle_callback(lid, data)
    _answer(bot_, call_id, res.toast)
    if res.edit is not None:
        edit_view(bot_, chat_id, message_id, res.edit)
    for view in res.send:
        send_view(bot_, chat_id, view)
    if res.tts_text:
        send_alex_voice(bot_, chat_id, res.tts_text)
    if res.submit_turn is not None:
        sub = res.submit_turn
        run_scene_turn(
            bot_, chat_id, lid, sub.text, origin=sub.origin, interaction_id=sub.interaction_id, background=background
        )


def on_callback(bot_, call_id: str, lid: int, chat_id: int, message_id: int, data: str, message_text: str = "", *, background: bool = True) -> None:
    if not _allowed(lid):
        _answer(bot_, call_id, "Private test")
        return
    if data.startswith("pod:"):
        if data.startswith("pod:mode:"):
            mode_val = data.split(":", 2)[2]
            m_clean: podcast.PodcastMode = mode_val if mode_val in ("recap", "briefing", "narrative") else "recap"
            _answer(bot_, call_id, f"🎙️ Recording {m_clean.title()} episode...")
            deliver_podcast_episode(bot_, chat_id, lid, mode=m_clean, message_id=message_id)
            return
        if data.startswith("pod:tx:"):
            show_tx = data.endswith(":show")
            ep = _LAST_PODCAST_EPISODES.get(int(lid)) or podcast.generate_podcast_episode(db, None, lid, mode="recap")
            with_ar = str(db.get_learner_settings(lid).get("arabic_support", "bilingual")) != "english_only"
            card_html = podcast.render_podcast_card_html(ep, show_transcript=show_tx, with_arabic=with_ar)
            markup = types.InlineKeyboardMarkup(row_width=2)
            toggle_btn = (
                types.InlineKeyboardButton("🙈 Hide Transcript", callback_data="pod:tx:hide")
                if show_tx
                else types.InlineKeyboardButton("📜 Show Transcript", callback_data="pod:tx:show")
            )
            markup.add(
                toggle_btn,
                types.InlineKeyboardButton("🔄 Recap", callback_data="pod:mode:recap"),
                types.InlineKeyboardButton("☕ Briefing", callback_data="pod:mode:briefing"),
                types.InlineKeyboardButton("🎬 Narrative", callback_data="pod:mode:narrative"),
            )
            try:
                bot_.edit_message_text(card_html, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
            except Exception:
                pass
            _answer(bot_, call_id, "Transcript updated")
            return
    if data.startswith("rep:"):
        with _get_learner_lock(lid):
            res_v4 = flows.handle_v4_callback(lid, data)
        _answer(bot_, call_id, res_v4.toast)
        if res_v4.edit is not None:
            edit_view(bot_, chat_id, message_id, res_v4.edit)
        if res_v4.submit_turn is not None:
            sub = res_v4.submit_turn
            run_scene_turn(bot_, chat_id, lid, sub.text, origin=sub.origin, interaction_id=sub.interaction_id, background=background)
        return
    if data.split(":", 1)[0] in interaction.CALLBACK_OPS:
        _v3_callback(bot_, call_id, lid, chat_id, message_id, data, background=background)
        return

    sess = db.get_or_create_active_session(lid)
    sid = str(sess["session_id"])

    if data == "decode_reveal":
        st = complete_decode_session(str(lid)) or complete_decode_session(str(chat_id))
        if not st:
            _answer(bot_, call_id, "Breakdown already revealed.")
            return
        ch_obj = st["challenge"]
        db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation")
        _answer(bot_, call_id, "Revealing sound & subtext breakdown...")
        bot_.send_message(chat_id, format_decode_phase2_reveal(ch_obj, None), parse_mode="HTML")
        return

    if data.startswith("guide:"):
        ch_key = data.split(":", 1)[1]
        ch_html = GUIDE_CHAPTERS_HTML.get(ch_key, GUIDE_CHAPTERS_HTML["overview"])
        bot_.edit_message_text(
            ch_html, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=build_guide_inline_markup(ch_key)
        )
        _answer(bot_, call_id, "Guide chapter loaded")
        return

    if data.startswith("card:"):  # buttons on messages sent before V3.0 keep working
        card_id = data.split(":", 1)[1]
        info = db.get_pending_card(card_id)
        if not info:
            _answer(bot_, call_id, "Card expired.")
            return
        expanded_html = render_layer2_card_html(info["reply_text"], info["diagnostic"])
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎧 Listen", callback_data=f"tts:{card_id}"))
        bot_.edit_message_text(expanded_html, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
        _answer(bot_, call_id, "Upgrade expanded")
        return

    if data.startswith("tts:"):
        card_id = data.split(":", 1)[1]
        info = db.get_pending_card(card_id) if card_id != "msg" else None
        raw_target = info["reply_text"] if info else (message_text or "")
        _answer(bot_, call_id, "🎧 Generating Alex's voice...")
        send_alex_voice(bot_, chat_id, extract_alex_spoken_text(raw_target))
        return

    if data.startswith(("setreg:", "setdir:", "setfb:", "setar:", "setmodel:", "setvoice:")):
        kind, val = data.split(":", 1)
        if kind == "setreg":
            db.update_session_settings(sid, lid, register_profile=val)
            db.update_learner_preference(lid, default_register=val)
            toast = f"Register → {val.upper()}"
        elif kind == "setdir":
            db.update_session_settings(sid, lid, directness=val)
            db.update_learner_preference(lid, default_directness=val)
            toast = f"Directness → {val.upper()}"
        elif kind == "setfb":
            db.update_learner_preference(lid, feedback_preference=val)
            toast = f"Coaching → {val.upper()}"
        elif kind == "setar":
            db.update_learner_preference(lid, arabic_support=val)
            toast = f"Arabic Notes → {val.upper()}"
        elif kind == "setvoice":
            enabled = val == "auto"
            db.set_auto_voice(lid, enabled)
            toast = "Alex Voice → AUTO 🔊" if enabled else "Alex Voice → ON TAP 🎧"
        else:
            chain = ai.set_model_tier(val)
            toast = f"Engine → {chain[0] if chain else val.upper()}"
        panel_html, markup = build_live_settings_panel(lid)
        try:
            bot_.edit_message_text(panel_html, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        _answer(bot_, call_id, f"✅ {toast}")
        return

    _answer(bot_, call_id, interaction.STALE_CALLBACK_TEXT)


if bot is not None:
    @bot.message_handler(commands=["start", "reset", "scene"])
    def handle_start(message):
        lid = message.from_user.id
        if not begin_command(bot, message.chat.id, lid):
            return
        new_sid = f"sess_{uuid.uuid4().hex[:10]}"
        db.upsert_learner(lid)
        cfg = db.get_learner_settings(lid)
        reg = cfg.get("default_register", "casual")
        direct = cfg.get("default_directness", "balanced")
        fb = cfg.get("feedback_preference", "light")
        db.create_session(
            new_sid,
            lid,
            training_mode="open_scene",
            scene_mode="real_conversation",
            register_profile=reg,
            directness=direct,
        )
        bot.send_message(
            message.chat.id,
            f"🟢 <b>𝐒𝐩𝐨𝐤𝐞𝐧 𝐄𝐧𝐠𝐥𝐢𝐬𝐡 𝐋𝐚𝐛 🎙️</b>\n"
            f"• Scene: <code>{new_sid}</code>\n"
            f"• Register: <b>{_e(reg.upper())}</b> | Directness: <b>{_e(direct.upper())}</b> | Coaching: <b>{_e(fb.upper())}</b>\n\n"
            "Talk naturally about anything on your mind, or use the mode buttons below.",
            parse_mode="HTML",
            reply_markup=build_reply_keyboard(),
        )

    @bot.message_handler(commands=["sync_telegram"])
    def handle_sync_telegram_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        force_sync_all_telegram_scopes_and_profile(message.chat.id)
        bot.send_message(
            message.chat.id,
            "✅ <b>Telegram Profile, Scopes & Commands Force-Synced!</b>",
            parse_mode="HTML",
            reply_markup=build_reply_keyboard(),
        )

    @bot.message_handler(commands=["progress", "audit"])
    def handle_progress(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        send_progress(bot, message.chat.id, message.from_user.id)


    @bot.message_handler(commands=["podcast"])
    def handle_podcast_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        send_view(bot, message.chat.id, flows.podcast_hub_view(message.from_user.id))

    @bot.message_handler(commands=["replay"])
    def handle_replay_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        send_view(bot, message.chat.id, flows.replay_hub_view(message.from_user.id))

    @bot.message_handler(commands=["review"])
    def handle_review_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        send_review(bot, message.chat.id, message.from_user.id)

    @bot.message_handler(commands=["debrief"])
    def handle_debrief_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        send_debrief(bot, message.chat.id, message.from_user.id)

    @bot.message_handler(commands=["settings"])
    def handle_settings_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        panel_html, markup = build_live_settings_panel(lid)
        bot.send_message(
            message.chat.id,
            panel_html,
            parse_mode="HTML",
            reply_markup=markup,
        )

    def _launch_dynamic_dilemma(chat_id: int, lid: int, sid: str, reg: str):
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        dilemma = ai.generate_dynamic_dilemma_scene(register_profile=reg)
        dilemma_bible = {
            "scene_id": sid,
            "scene_mode": "explicit_simulation",
            "setting": dilemma.setting,
            "relationship": dilemma.relationship,
            "established_facts": dilemma.established_facts,
            "counterpart_goal": dilemma.counterpart_goal,
            "counterpart_pushback_plan": dilemma.counterpart_pushback_plan,
        }
        db.update_session_settings(
            sid,
            lid,
            training_mode="pragmatic_dilemma",
            scene_mode="explicit_simulation",
            register_profile=reg,
            scene_bible=dilemma_bible,
        )
        corr_id = f"dil_{uuid.uuid4().hex[:8]}"
        db.bump_scene_revision(sid)
        db.append_event_ex(
            learner_id=lid,
            session_id=sid,
            event_type="responder_reply_delivered",
            payload=ResponderReplyV2(kind="opening", reply_text=dilemma.opening_line),
            correlation_id=corr_id,
            idempotency_key=f"{corr_id}:open",
        )
        dil_markup = types.InlineKeyboardMarkup()
        dil_markup.add(types.InlineKeyboardButton("🎧 Listen", callback_data="tts:msg"))
        bot.send_message(
            chat_id,
            f"🤝 <b>Live Pragmatic Dilemma</b> (<i>{_e(dilemma.setting)} — {_e(dilemma.relationship)}</i>)\n"
            f"• <b>Active Register:</b> <b>{_e(reg.upper())}</b>\n"
            f"• <b>Your Goal:</b> {_e(dilemma.learner_pragmatic_goal)}\n\n"
            f"💬 <b>Alex:</b> “{_e(dilemma.opening_line)}”",
            parse_mode="HTML",
            reply_markup=dil_markup,
        )

    def _launch_dynamic_workout(chat_id: int, lid: int, sid: str, reg: str):
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        target = db.select_stealth_target_for_session(lid, reg) or {
            "phrase_id": DEFAULT_CURATED_PHRASES[0][0],
            "canonical_form": DEFAULT_CURATED_PHRASES[0][1],
            "communicative_intention": DEFAULT_CURATED_PHRASES[0][2],
        }
        workout = ai.generate_dynamic_workout_scenario(target, register_profile=reg)
        register_workout_task(workout, target, learner_id=str(lid))
        workout_bible = {
            "scene_id": sid,
            "scene_mode": "micro_drill",
            "setting": workout.situation_setup,
            "relationship": "conversational counterpart",
            "established_facts": [workout.situation_setup],
            "counterpart_goal": f"Evaluate unaided retrieval for intention: {workout.communicative_intention}",
        }
        db.update_session_settings(
            sid,
            lid,
            training_mode="phrase_workout",
            scene_mode="micro_drill",
            scene_bible=workout_bible,
        )
        db.bump_scene_revision(sid)
        bot.send_message(
            chat_id,
            f"🏋️ <b>Spontaneous Retrieval Workout</b> (Register: <b>{_e(reg.upper())}</b>)\n"
            f"• <b>Communicative Intention:</b> <code>{_e(workout.communicative_intention)}</code>\n\n"
            f"{_e(workout.situation_setup)}\n\n"
            f"👉 <b>{_e(workout.prompt_question)}</b>",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["dilemma"])
    def handle_dilemma_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        cfg = db.get_learner_settings(lid)
        reg = sess.get("register_profile") or cfg.get("default_register") or "diplomatic"
        _launch_dynamic_dilemma(message.chat.id, lid, str(sess["session_id"]), reg)

    @bot.message_handler(commands=["workout"])
    def handle_workout_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        cfg = db.get_learner_settings(lid)
        reg = sess.get("register_profile") or cfg.get("default_register") or "casual"
        _launch_dynamic_workout(message.chat.id, lid, str(sess["session_id"]), reg)

    @bot.message_handler(commands=["shift", "register"])
    def handle_shift_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        db.update_session_settings(str(sess["session_id"]), lid, training_mode="register_shift", scene_mode="micro_drill")
        db.bump_scene_revision(str(sess["session_id"]))
        bot.send_message(
            message.chat.id,
            "🎛️ <b>Register Shift Mode:</b> Send any message or communicative intention, and I'll calibrate it across Casual, Diplomatic, and Executive registers.",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["think"])
    def handle_think_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        db.update_session_settings(str(sess["session_id"]), lid, training_mode="think_aloud", scene_mode="real_conversation")
        db.bump_scene_revision(str(sess["session_id"]))
        bot.send_message(
            message.chat.id,
            f"🧠 <b>Think-Aloud Mode</b> (Register: <b>{_e(sess.get('register_profile', 'analytical').upper())}</b>): Share a view or argument on any topic, and let's test and refine it.",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["decode"])
    def handle_decode_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        lid = message.from_user.id
        chat_id = message.chat.id
        sess = db.get_or_create_active_session(lid)
        reg = sess.get("register_profile", "casual")
        try:
            bot.send_chat_action(chat_id, "record_voice")
        except Exception:
            pass
        ch = ai.generate_listen_decode_challenge(register_profile=reg)
        db.update_session_settings(str(sess["session_id"]), lid, training_mode="listen_and_decode", scene_mode="micro_drill")
        db.bump_scene_revision(str(sess["session_id"]))
        try:
            mp3_p = synthesize_tts_mp3(ch.spoken_clip_text)
            with open(mp3_p, "rb") as f_audio:
                bot.send_voice(chat_id, f_audio, caption=f"🎧 <b>Context:</b> {_e(ch.context_line)}", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Decode TTS fallback: {e}")

        start_decode_session(str(lid), ch, str(chat_id))
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Reveal Breakdown", callback_data="decode_reveal"))
        bot.send_message(
            chat_id,
            format_decode_phase1_message(ch, reg),
            parse_mode="HTML",
            reply_markup=markup,
        )

    @bot.message_handler(commands=["guide", "help"])
    def handle_guide_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        bot.send_message(
            message.chat.id,
            GUIDE_CHAPTERS_HTML["overview"],
            parse_mode="HTML",
            reply_markup=build_guide_inline_markup("overview"),
        )

    @bot.message_handler(commands=["branding"])
    def handle_branding_cmd(message):
        if not begin_command(bot, message.chat.id, message.from_user.id):
            return
        chat_id = message.chat.id
        try:
            bot.send_chat_action(chat_id, "upload_photo")
        except Exception:
            pass
        p_img, d_img = generate_bot_branding_images(TEMP_AUDIO_DIR)
        with open(p_img, "rb") as fp:
            bot.send_photo(
                chat_id,
                fp,
                caption="🖼️ <b>1. Bot Profile Picture (800×800)</b>",
                parse_mode="HTML",
            )
        with open(d_img, "rb") as fd_doc:
            bot.send_document(
                chat_id,
                fd_doc,
                caption="📎 <b>2. Uncompressed 640×360 PNG</b>",
                parse_mode="HTML",
            )

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        on_callback(
            bot,
            call.id,
            call.from_user.id,
            call.message.chat.id,
            call.message.message_id,
            call.data or "",
            call.message.text or "",
        )

    @bot.message_handler(content_types=["text"])
    def handle_text(message):
        lid = message.from_user.id
        chat_id = message.chat.id
        text = (message.text or "").strip()
        if not text:
            return

        if text.startswith("🔄"):
            handle_start(message)
            return
        if text.startswith("📊"):
            handle_progress(message)
            return
        if text.startswith("📖"):
            handle_guide_cmd(message)
            return
        if text.startswith("⚙️"):
            handle_settings_cmd(message)
            return
        if text.startswith("🔁"):
            handle_review_cmd(message)
            return
        if text.startswith("🎙️"):
            handle_podcast_cmd(message)
            return
        if text.startswith("🎭"):
            handle_replay_cmd(message)
            return
        if text.startswith("🏁"):
            handle_debrief_cmd(message)
            return

        keyboard_modes = ("💬", "🎛️", "🤝", "🧠", "🏋️", "🎧")
        if text.startswith(keyboard_modes):
            if not begin_command(bot, chat_id, lid):
                return
            sess = db.get_or_create_active_session(lid)
            cfg = db.get_learner_settings(lid)
            sid = str(sess["session_id"])
            active_reg = sess.get("register_profile") or cfg.get("default_register") or "casual"
            active_dir = sess.get("directness") or cfg.get("default_directness") or "balanced"
            if text.startswith("💬"):
                db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation", register_profile=active_reg, directness=active_dir)
                db.bump_scene_revision(sid)
                bot.send_message(
                    chat_id,
                    f"💬 <b>Open Scene</b> active (Register: <b>{_e(active_reg.upper())}</b> | Directness: <b>{_e(active_dir.upper())}</b>).",
                    parse_mode="HTML",
                )
            elif text.startswith("🎛️"):
                handle_shift_cmd(message)
            elif text.startswith("🤝"):
                _launch_dynamic_dilemma(chat_id, lid, sid, active_reg)
            elif text.startswith("🧠"):
                handle_think_cmd(message)
            elif text.startswith("🏋️"):
                _launch_dynamic_workout(chat_id, lid, sid, active_reg)
            else:
                handle_decode_cmd(message)
            return

        on_text(bot, chat_id, lid, text, message.message_id)


if __name__ == "__main__":
    if not bot:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    logger.info("Force-synchronizing Telegram Bot scopes, luxury name, descriptions & commands...")
    sync_rep = force_sync_all_telegram_scopes_and_profile(ADMIN_ID)
    logger.info(f"Telegram sync report: {sync_rep}")
    try:
        from ai_engine import probe_and_rank_live_models
        import config as _cfg
        if _cfg.GEMINI_KEYS_LIST:
            healthy = probe_and_rank_live_models(_cfg.GEMINI_KEYS_LIST, list(_cfg.GEMINI_MODEL_CHAIN))
            _cfg.GEMINI_MODEL_CHAIN[:] = healthy
            _cfg.GEMINI_FLASH_TIER_CHAIN[:] = healthy
            logger.info(f"Self-healing model probe ranked chain: {healthy}")
    except Exception as _probe_err:
        logger.warning(f"Startup model probe skipped: {_probe_err}")
    if ADMIN_ID:
        try:
            active_m = ai.get_active_model_chain()[0]
            bot.send_message(
                ADMIN_ID,
                "🟢 <b>𝐒𝐩𝐨𝐤𝐞𝐧 𝐄𝐧𝐠𝐥𝐢𝐬𝐡 𝐋𝐚𝐛 🎙️ — V4.0 Studio online</b>\n"
                f"• Primary Discovered Model: <code>{_e(active_m)}</code>\n"
                f"• Access: {'admin-only canary' if CANARY_ADMIN_ONLY else 'all learners'}",
                parse_mode="HTML",
                reply_markup=build_reply_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Boot notification skipped: {e}")

    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    while True:
        try:
            bot.polling(none_stop=True, interval=0.2, timeout=20, long_polling_timeout=20)
        except Exception as poll_err:
            logger.error(f"Polling loop error: {poll_err}")
            time.sleep(3)
