import html
import logging
import time
import telebot
from telebot import types
from config import ADMIN_ID, TELEGRAM_BOT_TOKEN
from database import LoungeDatabase
from ai_engine import CleanAIEngine, is_metalinguistic_question, is_short_greeting

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MusaNYCLounge")

db = LoungeDatabase()
ai = CleanAIEngine()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)

ICONS = {"Marcus": "🧔‍♂️", "Chloe": "👩🏼‍💼", "Jake": "🧢"}


def _e(val) -> str:
    return html.escape(str(val if val is not None else ""))


def build_main_keyboard() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🗽 NYC Banter"),
        types.KeyboardButton("🎭 Read the Room"),
        types.KeyboardButton("🥊 Devil's Advocate"),
        types.KeyboardButton("🔄 New Scene"),
    )
    return markup


def format_banter_html(out, uptake_info=None) -> str:
    lines = []
    for t in out.turns:
        icon = ICONS.get(t.speaker, "🧔‍♂️")
        lines.append(f"{icon} <b>{_e(t.speaker)}:</b> {_e(t.text)}")
    dialogue_block = "\n\n".join(lines)

    if uptake_info:
        ledger_html = (
            "🎯 <b>Active Uptake & Evidence Ledger</b>\n"
            f"• <b>Function:</b> {_e(uptake_info['function'])}\n"
            f"• <b>Target Move:</b> <code>{_e(uptake_info['target_move'])}</code>\n"
            f"• <b>Provenance:</b> {_e(uptake_info['provenance'])}\n"
            f"• <b>Evidence Stage:</b> <code>{_e(uptake_info['stage'])}</code>\n"
            f"• <b>DB Audit:</b> {_e(uptake_info['db_audit'])}"
        )
        return f"{dialogue_block}\n\n{ledger_html}"

    if out.coaching_decision == "surgical_mirror" and out.mirror:
        m = out.mirror
        mirror_html = (
            f"🪞 …<s>{_e(m.wrong_span)}</s> → <b>{_e(m.natural_us)}</b>\n"
            f"↳ {_e(m.why_en)}\n"
            f"↳ بالعربية: {_e(m.why_ar)}"
        )
        return f"{dialogue_block}\n\n{mirror_html}"

    if out.coaching_decision == "recast_move" and out.spoken_move:
        sm = out.spoken_move
        move_html = (
            "💎 <b>Spoken American Move:</b>\n"
            f"🧩 <b>{_e(sm.template_en)}</b> — {_e(sm.arabic_meaning)}\n"
            f'  └ 💬 "{_e(sm.used_in_dialogue)}"\n'
            f"  └ 🎯 Does: {_e(sm.does)}\n"
            f"  └ 🗽 Say it when: {_e(sm.say_it_when)}"
        )
        return f"{dialogue_block}\n\n{move_html}"

    return dialogue_block


@bot.message_handler(commands=["start", "reset"])
def handle_start_or_reset(message):
    uid = message.from_user.id
    uname = message.from_user.username or ""
    fname = message.from_user.first_name or "Member"
    db.get_user(uid, uname, fname)
    new_scene = db.start_new_scene(uid, mode="spontaneous_chat")
    bot.send_message(
        message.chat.id,
        f"🗽 <b>Welcome to Musa’s NYC Lounge (Clean-Slate V4)</b>\n"
        f"Fresh scene initialized (<code>{new_scene}</code>). Marcus, Chloe, and Jake are hanging out—jump right in!",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


@bot.message_handler(commands=["audit"])
def handle_audit_command(message):
    uid = message.from_user.id
    rows = db.get_recent_ledger_rows(uid, limit=8)
    if not rows:
        bot.send_message(message.chat.id, "📋 <b>SQLite Evidence Ledger:</b> No events recorded yet.", parse_mode="HTML")
        return
    lines = ["📋 <b>Live SQLite Evidence Ledger (`v3_evidence_ledger`):</b>"]
    for r in rows:
        lines.append(
            f"• <code>id=#{r['id']}</code> | scene=<code>{_e(r['scene_id'])}</code> | parent=<code>{_e(r['parent_id'])}</code> | "
            f"type=<b>{_e(r['type'])}</b> | target=<code>{_e(r['target'])}</code> | attempt={r['attempt']} | outcome=<b>{_e(r['outcome'])}</b>"
        )
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(content_types=["text"])
def handle_text_message(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if not text:
        return

    if not db.is_whitelisted(uid):
        bot.reply_to(message, f"🔒 Private Club. Share your ID with Musa: <code>{uid}</code>", parse_mode="HTML")
        return

    user = db.get_user(uid, message.from_user.username or "", message.from_user.first_name or "")

    if text.startswith("🗽"):
        sc = db.start_new_scene(uid, mode="spontaneous_chat")
        bot.send_message(chat_id, f"🗽 <b>NYC Banter Mode</b> (Scene <code>{sc}</code>). Say anything to Marcus, Chloe & Jake!", parse_mode="HTML")
        return
    if text.startswith("🎭"):
        sc = db.start_new_scene(uid, mode="read_the_room")
        bot.send_message(
            chat_id,
            f"🎭 <b>Read the Room — 2-Beat Negotiation</b> (Scene <code>{sc}</code>).\n"
            "🧔‍♂️ <b>Marcus:</b> “We’re running really tight on space and time for this proposal. Can we just cut the background section completely so we hit the 4 PM deadline?”\n\n"
            "<i>(Your constraint: Acknowledge the time pressure, but protect the essential background context.)</i>",
            parse_mode="HTML",
        )
        db.append_turn(uid, sc, "assistant", "Marcus", "We’re running really tight on space and time for this proposal. Can we just cut the background section completely so we hit the 4 PM deadline?")
        return
    if text.startswith("🥊"):
        sc = db.start_new_scene(uid, mode="devils_advocate")
        bot.send_message(chat_id, f"🥊 <b>Devil's Advocate Mode</b> (Scene <code>{sc}</code>). Share any opinion or take, and we'll test its strength!", parse_mode="HTML")
        return
    if text.startswith("🔄"):
        sc = db.start_new_scene(uid)
        bot.send_message(chat_id, f"🔄 Started a fresh conversation scene (<code>{sc}</code>).", parse_mode="HTML")
        return

    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass

    if is_short_greeting(text):
        scene_id = db.start_new_scene(uid)
        history_lines = []
    else:
        scene_id = user["active_scene_id"]
        history_lines = db.get_scene_history(uid, scene_id, limit=6)

    mode = user.get("current_mode", "spontaneous_chat")

    try:
        if is_metalinguistic_question(text):
            meta_out = ai.generate_metalinguistic_consultation(text, history_lines)
            icon = ICONS.get(meta_out.counterpart_speaker, "🧔‍♂️")
            reply_html = (
                "🧭 <b>Linguistic Consultation (Scene Paused)</b>\n"
                f"{_e(meta_out.coach_explanation_en)}\n"
                f"↳ بالعربية: {_e(meta_out.coach_explanation_ar)}\n\n"
                "🎬 <b>Scene Resumed</b>\n"
                f"{icon} <b>{_e(meta_out.counterpart_speaker)}:</b> “{_e(meta_out.scene_resume_line)}”"
            )
            bot.send_message(chat_id, reply_html, parse_mode="HTML")
            return

        if mode == "read_the_room":
            neg_state = user.get("negotiation_state") or {}
            neg_out = ai.evaluate_negotiation_turn(text, neg_state, history_lines)
            db.append_turn(uid, scene_id, "user", "Learner", text)

            if neg_out.evaluation_stage == "beat1_cavein":
                ev_b1 = db.record_evidence(
                    user_id=uid,
                    scene_id=scene_id,
                    event_type="pragmatic_negotiation",
                    functional_category=neg_out.functional_category,
                    target_key="preserve_core_constraint",
                    provenance="Read the Room Scenario",
                    attempt_number=1,
                    outcome="premature_cave_in",
                    evidence_stage="Attempt 1: Constraint Surrendered (Retry Triggered)",
                    learner_text=text,
                )
                db.update_user(uid, negotiation_state={"beat1_event_id": ev_b1["row_id"], "attempt_1": text})
                reply_html = (
                    "🎭 <b>Read the Room — Beat 1 Evaluation</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👂 <b>How It Lands:</b> {_e(neg_out.how_it_lands or 'You sounded cooperative, but surrendered your core constraint.')}\n\n"
                    "🎯 <b>Constraint Check:</b>\n"
                    f"• Acknowledge counterpart pressure: {'✅ Achieved' if neg_out.acknowledged_pressure else '❌ Missed'}\n"
                    f"• Preserve core constraint: {'✅ Achieved' if neg_out.preserved_core_constraint else '❌ Surrendered (premature cave-in)'}\n\n"
                    f"🔄 <b>Try Beat 1 Again ({ICONS.get(neg_out.counterpart_speaker, '🧔‍♂️')} {_e(neg_out.counterpart_speaker)}):</b>\n"
                    f"{_e(neg_out.retry_prompt or 'Propose a concrete compromise that respects the constraint without giving up your core position.')}"
                )
                bot.send_message(chat_id, reply_html, parse_mode="HTML")
                return

            if neg_out.evaluation_stage == "beat2_compromise":
                parent_id = neg_state.get("beat1_event_id")
                ev_b2 = db.record_evidence(
                    user_id=uid,
                    scene_id=scene_id,
                    event_type="pragmatic_negotiation",
                    functional_category=neg_out.functional_category,
                    target_key="preserve_core_constraint",
                    provenance=f"Read the Room Scenario (Linked to Beat 1 id=#{parent_id})",
                    attempt_number=2,
                    outcome="compromise_achieved",
                    evidence_stage="Pragmatic Strategy Production (Functional Success — single-scenario)",
                    learner_text=text,
                    parent_event_id=parent_id,
                )
                db.update_user(uid, negotiation_state={}, current_mode="spontaneous_chat")
                icon = ICONS.get(neg_out.counterpart_speaker, "🧔‍♂️")
                reply_html = (
                    f"{icon} <b>{_e(neg_out.counterpart_speaker)}:</b> “{_e(neg_out.counterpart_reply)}”\n\n"
                    "📊 <b>Negotiation Debrief & Exit</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Attempt 1 vs. Attempt 2:</b> {_e(neg_out.attempt1_vs_attempt2_comparison or 'You adapted from a premature cave-in to a balanced compromise that protected your core constraint.')}\n"
                    f"• <b>📋 Evidence Ledger:</b> Recorded <code>Pragmatic Strategy Production (Functional Success)</code> "
                    f"[SQLite row_id=#{ev_b2['row_id']}, parent_id=#{parent_id}, scene=<code>{scene_id}</code>]."
                )
                bot.send_message(chat_id, reply_html, parse_mode="HTML")
                return

            icon = ICONS.get(neg_out.counterpart_speaker, "🧔‍♂️")
            db.append_turn(uid, scene_id, "assistant", neg_out.counterpart_speaker, neg_out.counterpart_reply)
            bot.send_message(chat_id, f"{icon} <b>{_e(neg_out.counterpart_speaker)}:</b> “{_e(neg_out.counterpart_reply)}”", parse_mode="HTML")
            return

        if mode == "devils_advocate":
            da_out = ai.generate_devils_advocate_turn(text, history_lines)
            db.append_turn(uid, scene_id, "user", "Learner", text)
            icon = ICONS.get(da_out.challenger, "🧔‍♂️")
            combined_reply = f"{da_out.fair_restatement} {da_out.counter_argument} {da_out.probing_question}"
            db.append_turn(uid, scene_id, "assistant", da_out.challenger, combined_reply)
            reply_html = (
                f"🥊 <b>Devil's Advocate ({icon} {_e(da_out.challenger)})</b>\n"
                f"• <b>Fair Restatement:</b> {_e(da_out.fair_restatement)}\n\n"
                f"{icon} <b>{_e(da_out.challenger)}:</b> {_e(da_out.counter_argument)} {_e(da_out.probing_question)}"
            )
            if da_out.mirror:
                m = da_out.mirror
                reply_html += (
                    f"\n\n🪞 …<s>{_e(m.wrong_span)}</s> → <b>{_e(m.natural_us)}</b>\n"
                    f"↳ {_e(m.why_en)}"
                )
            bot.send_message(chat_id, reply_html, parse_mode="HTML")
            return

        uptake_info = db.detect_uptake_from_exposed_targets(uid, scene_id, text)
        banter_out = ai.generate_banter_turn(text, history_lines)

        db.append_turn(uid, scene_id, "user", "Learner", text)
        for t in banter_out.turns:
            db.append_turn(uid, scene_id, "assistant", t.speaker, t.text)

        if not uptake_info and banter_out.coaching_decision == "recast_move" and banter_out.spoken_move:
            db.record_evidence(
                user_id=uid,
                scene_id=scene_id,
                event_type="exposure",
                functional_category=banter_out.spoken_move.functional_category,
                target_key=banter_out.spoken_move.template_en,
                provenance="Bot Recast Exposure",
                attempt_number=1,
                outcome="exposed",
                evidence_stage="Exposure (not counted as learner production)",
                learner_text=text,
            )

        final_html = format_banter_html(banter_out, uptake_info=uptake_info)
        bot.send_message(chat_id, final_html, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Turn processing error: {e}", exc_info=True)
        bot.send_message(
            chat_id,
            f"⚠️ <i>AI Engine Notice: {_e(str(e)[:160])}</i>",
            parse_mode="HTML",
        )


if __name__ == "__main__":
    logger.info("Musa's NYC Lounge Clean-Slate V4 starting...")
    if ADMIN_ID:
        try:
            bot.send_message(
                ADMIN_ID,
                "🟢 <b>Musa’s NYC Lounge (Clean-Slate V4 — Built from Scratch)</b> is live!\n"
                "All legacy files and polluted history have been wiped.",
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        except Exception as boot_err:
            logger.warning(f"Could not send boot ping: {boot_err}")

    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=25, long_polling_timeout=25)
        except Exception as poll_err:
            logger.error(f"Polling loop recovered from error: {poll_err}")
            time.sleep(3)
