import asyncio
import html
import json
import logging
import os
import time
import uuid
import edge_tts
import telebot
from telebot import types
from ai_engine import DualLLMEngine
from config import ADMIN_ID, DEFAULT_VOICE, TELEGRAM_BOT_TOKEN, TEMP_AUDIO_DIR
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase
from orchestrator import TurnOrchestrator
from schemas import DirectnessLevel, InterventionLevel, RegisterProfile, TrainingMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SpokenEnglishLab")

db = EventSourcedDatabase()
ai = DualLLMEngine()
orchestrator = TurnOrchestrator(db=db, ai=ai)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False) if TELEGRAM_BOT_TOKEN else None


def _e(val) -> str:
    return html.escape(str(val if val is not None else ""))


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
        types.KeyboardButton("🔄 New Scene"),
    )
    return markup


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
    @bot.message_handler(commands=["start", "reset"])
    def handle_start(message):
        lid = message.from_user.id
        new_sid = f"sess_{uuid.uuid4().hex[:10]}"
        db.upsert_learner(lid)
        db.create_session(new_sid, lid, training_mode="open_scene", scene_mode="real_conversation", register_profile="casual")
        bot.send_message(
            message.chat.id,
            "🟢 <b>Spoken English Lab — Ready</b>\n"
            f"Fresh scene initialized (<code>{new_sid}</code>, Register: <b>casual</b>).\n"
            "Talk naturally about anything on your mind, or pick a training mode below.",
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

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        data = call.data or ""
        lid = call.from_user.id
        sess = db.get_or_create_active_session(lid)
        sid = str(sess["session_id"])

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

        elif data.startswith("tts:"):
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

        elif data.startswith("setreg:"):
            new_reg = data.split(":", 1)[1]
            db.update_session_settings(sid, lid, register_profile=new_reg)
            bot.answer_callback_query(call.id, f"Register set to {new_reg}")
            bot.edit_message_text(
                f"⚙️ Active Register updated to: <b>{_e(new_reg)}</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
            )

        elif data.startswith("setfb:"):
            new_fb = data.split(":", 1)[1]
            db.update_learner_preference(lid, feedback_preference=new_fb)
            bot.answer_callback_query(call.id, f"Feedback set to {new_fb}")
            bot.edit_message_text(
                f"⚙️ Feedback intensity updated to: <b>{_e(new_fb)}</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
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

        sess = db.get_or_create_active_session(lid)
        sid = str(sess["session_id"])

        if text.startswith("⚙️"):
            markup = types.InlineKeyboardMarkup(row_width=3)
            regs = ["casual", "warm", "witty", "diplomatic", "executive", "analytical"]
            markup.add(*[types.InlineKeyboardButton(r.capitalize(), callback_data=f"setreg:{r}") for r in regs])
            markup.add(
                types.InlineKeyboardButton("🔇 Silent", callback_data="setfb:silent"),
                types.InlineKeyboardButton("💡 Light (Default)", callback_data="setfb:light"),
                types.InlineKeyboardButton("🔬 Active", callback_data="setfb:active"),
            )
            bot.send_message(
                chat_id,
                f"⚙️ <b>Session Settings</b>\n"
                f"• Current Register: <b>{_e(sess.get('register_profile', 'casual'))}</b>\n"
                "Select a register or feedback intensity below:",
                parse_mode="HTML",
                reply_markup=markup,
            )
            return

        if text.startswith("💬"):
            db.update_session_settings(sid, lid, training_mode="open_scene", scene_mode="real_conversation", register_profile="casual")
            bot.send_message(chat_id, "💬 <b>Open Scene (Casual)</b> active. What's on your mind?", parse_mode="HTML")
            return

        if text.startswith("🎛️"):
            db.update_session_settings(sid, lid, training_mode="register_shift", scene_mode="micro_drill", register_profile="diplomatic")
            bot.send_message(
                chat_id,
                "🎛️ <b>Register Shift Mode:</b> Send any thought or intention (e.g., <i>'I want to tell someone their timeline is unrealistic'</i>), and we'll calibrate it across Casual, Diplomatic, and Executive registers.",
                parse_mode="HTML",
            )
            return

        if text.startswith("🤝"):
            dilemma_bible = {
                "scene_id": sid,
                "scene_mode": "explicit_simulation",
                "setting": "Project handoff call under tight deadline",
                "relationship": "peer colleague",
                "established_facts": [
                    "Alex is asking you to take over the data section tonight.",
                    "Your schedule is already full, so you must protect your boundary while keeping rapport.",
                ],
                "counterpart_goal": "Get help with Friday's deliverable or agree on a realistic compromise",
            }
            db.update_session_settings(
                sid,
                lid,
                training_mode="pragmatic_dilemma",
                scene_mode="explicit_simulation",
                register_profile="diplomatic",
                scene_bible=dilemma_bible,
            )
            bot.send_message(
                chat_id,
                "🤝 <b>Pragmatic Dilemma (Simulation):</b>\n"
                "💬 <b>Alex:</b> “Hey, we're really backed up on Friday's deliverable. Can you take over the data section tonight so we don't slip?”\n"
                "<i>(Respond naturally: protect your schedule while keeping the relationship intact.)</i>",
                parse_mode="HTML",
            )
            return

        if text.startswith("🧠"):
            db.update_session_settings(sid, lid, training_mode="think_aloud", scene_mode="real_conversation", register_profile="analytical")
            bot.send_message(
                chat_id,
                "🧠 <b>Think-Aloud Mode (Analytical):</b> Share an opinion on any topic, and I'll test your reasoning and help you practice nuanced spoken qualifiers.",
                parse_mode="HTML",
            )
            return

        if text.startswith("🏋️"):
            target = db.select_stealth_target_for_session(lid, sess.get("register_profile", "casual")) or {
                "phrase_id": DEFAULT_CURATED_PHRASES[0][0],
                "canonical_form": DEFAULT_CURATED_PHRASES[0][1],
                "communicative_intention": DEFAULT_CURATED_PHRASES[0][2],
            }
            db.update_session_settings(sid, lid, training_mode="phrase_workout", scene_mode="micro_drill")
            bot.send_message(
                chat_id,
                f"🏋️ <b>Phrase Workout (Unaided Retrieval):</b>\n"
                f"• <b>Communicative Goal:</b> <code>{_e(target['communicative_intention'])}</code>\n"
                "You have a quick idea for a project and want to briefly present it to a colleague to get their reaction. What do you say naturally?",
                parse_mode="HTML",
            )
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
                    f"🎯 <b>Pattern Notice:</b> We've seen <code>{_e(pat)}</code> come up 3 times across your turns. Tap 🎛️ <b>Register Shift</b> or 🏋️ <b>Phrase Workout</b> whenever you'd like a 2-minute practice on it.",
                    parse_mode="HTML",
                )

        except Exception as err:
            logger.error(f"Turn error: {err}", exc_info=True)
            bot.send_message(chat_id, f"⚠️ <i>Service notice: {_e(str(err)[:160])}</i>", parse_mode="HTML")


if __name__ == "__main__":
    if not bot:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    logger.info("Spoken English Lab starting...")
    if ADMIN_ID:
        try:
            bot.send_message(
                ADMIN_ID,
                "🟢 <b>Spoken English Lab (Clean-Slate Audited Architecture)</b> is live!",
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
            bot.polling(none_stop=True, interval=1, timeout=25, long_polling_timeout=25)
        except Exception as poll_err:
            logger.error(f"Polling loop error: {poll_err}")
            time.sleep(3)
