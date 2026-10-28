import asyncio
import html
import json
import logging
import os
import time
import uuid
import edge_tts
import requests
import telebot
from telebot import types
from ai_engine import DualLLMEngine
from config import ADMIN_ID, DEFAULT_VOICE, TELEGRAM_BOT_TOKEN, TEMP_AUDIO_DIR
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from orchestrator import TurnOrchestrator
from guide_and_branding import GUIDE_CHAPTERS_HTML, generate_bot_branding_images
from schemas import DirectnessLevel, InterventionLevel, RegisterProfile, TrainingMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SpokenEnglishLab")

db = EventSourcedDatabase()
ai = DualLLMEngine()
orchestrator = TurnOrchestrator(db=db, ai=ai)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True, num_threads=4) if TELEGRAM_BOT_TOKEN else None


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
        {"command": "shift", "description": "Transform a thought across registers"},
        {"command": "think", "description": "Analytical Think-Aloud sparring"},
        {"command": "workout", "description": "Spontaneous phrase retrieval workout"},
        {"command": "progress", "description": "View evidence-based acquisition profile"},
        {"command": "guide", "description": "Interactive User Guide (EN + AR)"},
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
        "• 🤝 Pragmatic Dilemmas & Unaided Retrieval.\n\n"
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
        types.KeyboardButton("⚙️ Register & Tone"),
        types.KeyboardButton("📊 My Progress"),
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

    # Row 1 & 2: 6 Registers
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

    # Row 3: 3 Directness levels
    dirs = [("soft", "🕊️ Soft"), ("balanced", "⚖️ Balanced"), ("direct", "🎯 Direct")]
    dir_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_dir == k else ''}{label}",
            callback_data=f"setdir:{k}",
        )
        for k, label in dirs
    ]
    markup.row(*dir_btns)

    # Row 4: 3 Feedback Intensity levels
    fbs = [("silent", "🔇 Silent"), ("light", "💡 Light"), ("active", "🔬 Active")]
    fb_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_fb == k else ''}{label}",
            callback_data=f"setfb:{k}",
        )
        for k, label in fbs
    ]
    markup.row(*fb_btns)

    # Row 5: Arabic Support toggle
    ars = [("bilingual", "🇦🇪 AR Notes: ON"), ("english_only", "🇺🇸 English Only")]
    ar_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_ar == k else ''}{label}",
            callback_data=f"setar:{k}",
        )
        for k, label in ars
    ]
    markup.row(*ar_btns)

    # Row 6: Gemini Model Tier (Flash Speed vs Pro Deep Reasoning)
    tiers = [("flash", "⚡ Flash (Fastest)"), ("pro", "🧠 Pro (Deepest)")]
    tier_btns = [
        types.InlineKeyboardButton(
            f"{'✅ ' if cur_tier == k else ''}{label}",
            callback_data=f"setmodel:{k}",
        )
        for k, label in tiers
    ]
    markup.row(*tier_btns)

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
    lines.append(f"• <b>Why:</b> {_e(diag.get('explanation_en', ''))}")
    if diag.get("explanation_ar"):
        lines.append(f"• <b>بالعربية:</b> <tg-spoiler>{_e(diag['explanation_ar'])}</tg-spoiler>")
    return "\n".join(lines)


def synthesize_tts_mp3(text: str) -> str:
    out_path = os.path.join(TEMP_AUDIO_DIR, f"tts_{uuid.uuid4().hex[:8]}.mp3")

    async def _run():
        comm = edge_tts.Communicate(text, DEFAULT_VOICE)
        await comm.save(out_path)

    asyncio.run(_run())
    return out_path


if bot is not None:
    @bot.message_handler(commands=["start", "reset", "scene"])
    def handle_start(message):
        lid = message.from_user.id
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
            "Talk naturally about anything on your mind, or tap <b>⚙️ Register & Tone</b> below to customize your session.",
            parse_mode="HTML",
            reply_markup=build_reply_keyboard(),
        )

    @bot.message_handler(commands=["sync_telegram"])
    def handle_sync_telegram_cmd(message):
        force_sync_all_telegram_scopes_and_profile(message.chat.id)
        bot.send_message(
            message.chat.id,
            "✅ <b>Telegram Profile, Scopes & Commands Force-Synced!</b>\n"
            "<i>Note: Close and reopen the Telegram app once to clear mobile profile cache.</i>",
            parse_mode="HTML",
            reply_markup=build_reply_keyboard(),
        )

    @bot.message_handler(commands=["progress", "audit"])
    def handle_progress(message):
        lid = message.from_user.id
        with db._conn() as conn:
            p_rows = conn.execute(
                "SELECT phrase_id, current_stage, spontaneous_count, independent_count, exposure_count FROM phrase_state_projection WHERE learner_id = ?",
                (lid,),
            ).fetchall()
            e_rows = conn.execute(
                "SELECT normalized_pattern_id, issue_kind, distinct_eligible_occurrences, times_feedback_shown, workout_scheduled FROM error_pattern_projection WHERE learner_id = ? ORDER BY distinct_eligible_occurrences DESC LIMIT 5",
                (lid,),
            ).fetchall()

        out_lines = ["📊 <b>Evidence-Based Acquisition Profile</b>", "━━━━━━━━━━━━━━━━━━━━"]
        if p_rows:
            out_lines.append("<b>Tracked Expressions:</b>")
            for r in p_rows:
                out_lines.append(
                    f"• <code>{_e(r['phrase_id'])}</code> → <b>{_e(r['current_stage'])}</b> "
                    f"(spontaneous={r['spontaneous_count']}, independent={r['independent_count']}, seen={r['exposure_count']})"
                )
        else:
            out_lines.append("• No tracked phrase events yet.")

        if e_rows:
            out_lines.append("\n<b>Recurring Diagnostic Patterns:</b>")
            for r in e_rows:
                out_lines.append(
                    f"• <code>{_e(r['normalized_pattern_id'])}</code> ({_e(r['issue_kind'])}): "
                    f"occurrences={r['distinct_eligible_occurrences']}, cards_shown={r['times_feedback_shown']}, "
                    f"workout={'✅ due' if r['workout_scheduled'] else '—'}"
                )
        bot.send_message(message.chat.id, "\n".join(out_lines), parse_mode="HTML")

    @bot.message_handler(commands=["settings"])
    def handle_settings_cmd(message):
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
        db.append_event(
            learner_id=lid,
            session_id=sid,
            event_type="responder_reply_delivered",
            payload={"reply_text": dilemma.opening_line, "asked_optional_question": "?" in dilemma.opening_line},
            correlation_id=corr_id,
            idempotency_key=f"{corr_id}:open",
        )
        bot.send_message(
            chat_id,
            f"🤝 <b>Live Pragmatic Dilemma</b> (<i>{_e(dilemma.setting)} — {_e(dilemma.relationship)}</i>)\n"
            f"• <b>Active Register:</b> <b>{_e(reg.upper())}</b>\n"
            f"• <b>Your Goal:</b> {_e(dilemma.learner_pragmatic_goal)}\n\n"
            f"💬 <b>Alex:</b> “{_e(dilemma.opening_line)}”",
            parse_mode="HTML",
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
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        cfg = db.get_learner_settings(lid)
        reg = sess.get("register_profile") or cfg.get("default_register") or "diplomatic"
        _launch_dynamic_dilemma(message.chat.id, lid, str(sess["session_id"]), reg)

    @bot.message_handler(commands=["workout"])
    def handle_workout_cmd(message):
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        cfg = db.get_learner_settings(lid)
        reg = sess.get("register_profile") or cfg.get("default_register") or "casual"
        _launch_dynamic_workout(message.chat.id, lid, str(sess["session_id"]), reg)

    @bot.message_handler(commands=["shift"])
    def handle_shift_cmd(message):
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        db.update_session_settings(str(sess["session_id"]), lid, training_mode="register_shift", scene_mode="micro_drill")
        bot.send_message(
            message.chat.id,
            "🎛️ <b>Register Shift Mode:</b> Send any message or communicative intention, and I'll calibrate it across Casual, Diplomatic, and Executive registers.",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["think"])
    def handle_think_cmd(message):
        lid = message.from_user.id
        sess = db.get_or_create_active_session(lid)
        db.update_session_settings(str(sess["session_id"]), lid, training_mode="think_aloud", scene_mode="real_conversation")
        bot.send_message(
            message.chat.id,
            f"🧠 <b>Think-Aloud Mode</b> (Register: <b>{_e(sess.get('register_profile', 'analytical').upper())}</b>): Share a view or argument on any topic, and let's test and refine it.",
            parse_mode="HTML",
        )

    def build_guide_inline_markup(active_key: str = "overview") -> types.InlineKeyboardMarkup:
        markup = types.InlineKeyboardMarkup(row_width=2)
        chapters = [
            ("overview", "📖 Overview"),
            ("ch1_layers", "🏛️ 1. 3-Layer System"),
            ("ch2_modes", "🎯 2. 5 Training Modes"),
            ("ch3_registers", "⚙️ 3. Registers & Tone"),
            ("ch4_mastery", "📊 4. Mastery Ladder"),
        ]
        buttons = []
        for k, label in chapters:
            prefix = "✅ " if k == active_key else ""
            buttons.append(types.InlineKeyboardButton(f"{prefix}{label}", callback_data=f"guide:{k}"))
        markup.add(*buttons)
        return markup

    @bot.message_handler(commands=["guide", "help"])
    def handle_guide_cmd(message):
        bot.send_message(
            message.chat.id,
            GUIDE_CHAPTERS_HTML["overview"],
            parse_mode="HTML",
            reply_markup=build_guide_inline_markup("overview"),
        )

    @bot.message_handler(commands=["branding"])
    def handle_branding_cmd(message):
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
                caption="🖼️ <b>1. Bot Profile Picture (800×800)</b>\nUse this in <code>@BotFather</code> → <code>/setuserpic</code> (or Edit Botpic).",
                parse_mode="HTML",
            )
        with open(d_img, "rb") as fd_doc:
            bot.send_document(
                chat_id,
                fd_doc,
                caption="📎 <b>2. Uncompressed 640×360 PNG</b> for <code>@BotFather</code> → <code>Edit Description Picture</code>.",
                parse_mode="HTML",
            )

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        data = call.data or ""
        lid = call.from_user.id
        sess = db.get_or_create_active_session(lid)
        sid = str(sess["session_id"])

        if data.startswith("guide:"):
            ch_key = data.split(":", 1)[1]
            ch_html = GUIDE_CHAPTERS_HTML.get(ch_key, GUIDE_CHAPTERS_HTML["overview"])
            bot.edit_message_text(
                ch_html,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=build_guide_inline_markup(ch_key),
            )
            bot.answer_callback_query(call.id, "Guide chapter loaded")
            return

        if data.startswith("card:"):
            card_id = data.split(":", 1)[1]
            info = db.get_pending_card(card_id)
            if not info:
                bot.answer_callback_query(call.id, "Card expired.")
                return
            expanded_html = render_layer2_card_html(info["reply_text"], info["diagnostic"])
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎧 Listen", callback_data=f"tts:{card_id}"))
            bot.edit_message_text(
                expanded_html,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=markup,
            )
            bot.answer_callback_query(call.id, "Upgrade expanded")
            return

        if data.startswith("tts:"):
            card_id = data.split(":", 1)[1]
            info = db.get_pending_card(card_id)
            target_txt = info["reply_text"] if info else (call.message.text or "")
            bot.answer_callback_query(call.id, "Generating audio...")
            try:
                mp3_p = synthesize_tts_mp3(target_txt)
                with open(mp3_p, "rb") as f_audio:
                    bot.send_voice(call.message.chat.id, f_audio)
            except Exception as e:
                logger.warning(f"TTS error: {e}")
            return

        if data.startswith(("setreg:", "setdir:", "setfb:", "setar:", "setmodel:")):
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
            else:
                chain = ai.set_model_tier(val)
                toast = f"Engine → {chain[0] if chain else val.upper()}"

            panel_html, markup = build_live_settings_panel(lid)
            try:
                bot.edit_message_text(
                    panel_html,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except Exception:
                pass
            bot.answer_callback_query(call.id, f"✅ {toast}")
            return

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

        sess = db.get_or_create_active_session(lid)
        cfg = db.get_learner_settings(lid)
        sid = str(sess["session_id"])
        active_reg = sess.get("register_profile") or cfg.get("default_register") or "casual"
        active_dir = sess.get("directness") or cfg.get("default_directness") or "balanced"

        if text.startswith("💬"):
            db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation", register_profile=active_reg, directness=active_dir)
            bot.send_message(
                chat_id,
                f"💬 <b>Open Scene</b> active (Register: <b>{_e(active_reg.upper())}</b> | Directness: <b>{_e(active_dir.upper())}</b>).",
                parse_mode="HTML",
            )
            return

        if text.startswith("🎛️"):
            handle_shift_cmd(message)
            return

        if text.startswith("🤝"):
            _launch_dynamic_dilemma(chat_id, lid, sid, active_reg)
            return

        if text.startswith("🧠"):
            handle_think_cmd(message)
            return

        if text.startswith("🏋️"):
            _launch_dynamic_workout(chat_id, lid, sid, active_reg)
            return

        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass

        try:
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
                bot.send_message(chat_id, rs_html, parse_mode="HTML")
                return

            turn_res = orchestrator.process_turn(
                learner_id=lid,
                learner_text=text,
                message_idempotency_key=f"tg_msg_{chat_id}_{message.message_id}",
                is_voice_message=False,
            )

            reply_html = f"💬 <b>Alex:</b> {_e(turn_res.responder_output.reply_text)}"
            markup = None

            if turn_res.policy_decision.intervention == InterventionLevel.BRIEF_CARD and turn_res.card_id and turn_res.selected_diagnostic:
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("💡 Quick Upgrade", callback_data=f"card:{turn_res.card_id}"),
                    types.InlineKeyboardButton("🎧 Listen", callback_data=f"tts:{turn_res.card_id}"),
                )

            sent_msg = bot.send_message(chat_id, reply_html, parse_mode="HTML", reply_markup=markup)

            if turn_res.responder_output.second_bubble_text:
                bot.send_message(chat_id, f"💬 <b>Alex:</b> {_e(turn_res.responder_output.second_bubble_text)}", parse_mode="HTML")

            if sent_msg and turn_res.policy_decision.intervention == InterventionLevel.BRIEF_CARD and turn_res.selected_diagnostic:
                orchestrator.confirm_card_delivered(
                    learner_id=lid,
                    session_id=turn_res.session_id,
                    correlation_id=turn_res.correlation_id,
                    diagnostic=turn_res.selected_diagnostic,
                )

            if turn_res.policy_decision.schedule_micro_workout and not turn_res.policy_decision.defer_workout_until_scene_end:
                pat = turn_res.policy_decision.workout_candidate_pattern_id
                bot.send_message(
                    chat_id,
                    f"🎯 <b>Pattern Notice:</b> <code>{_e(pat)}</code> has come up 3 times across your turns. Tap 🎛️ <b>Register Shift</b> or 🏋️ <b>Phrase Workout</b> whenever you'd like a quick focused practice.",
                    parse_mode="HTML",
                )

        except Exception as err:
            logger.error(f"Turn error: {err}", exc_info=True)
            bot.send_message(chat_id, f"⚠️ <i>Service notice: {_e(str(err)[:160])}</i>", parse_mode="HTML")


if __name__ == "__main__":
    if not bot:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    logger.info("Force-synchronizing Telegram Bot scopes, luxury name, descriptions & commands...")
    sync_rep = force_sync_all_telegram_scopes_and_profile(ADMIN_ID)
    logger.info(f"Telegram sync report: {sync_rep}")
    if ADMIN_ID:
        try:
            active_m = ai.get_active_model_chain()[0]
            bot.send_message(
                ADMIN_ID,
                f"🟢 <b>𝐒𝐩𝐨𝐤𝐞𝐧 𝐄𝐧𝐠𝐥𝐢𝐬𝐡 𝐋𝐚𝐛 🎙️ — Turbo Engine Live!</b>\n"
                f"• Primary Discovered Model: <code>{_e(active_m)}</code>\n"
                "• Zero-Retry JSON Schemas + Parallel Execution enabled.",
                parse_mode="HTML",
                reply_markup=build_reply_keyboard(),
            )
            panel_html, panel_markup = build_live_settings_panel(ADMIN_ID)
            bot.send_message(
                ADMIN_ID,
                panel_html,
                parse_mode="HTML",
                reply_markup=panel_markup,
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
