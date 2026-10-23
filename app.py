# -*- coding: utf-8 -*-
"""
app.py — Main Club Host Engine for Musa's NYC Lounge
Features:
- Pure New York Club Experience (zero technical jargon or startup spam)
- Dual Audio Playback (Host Voice + User Recast Voice) & Voice Mirror
- Live Collocation Explorer with Atomic Save
- Active & Self-Graded Daily Review (Voice or Text practice)
- Silent Self-Cleaning Background Vault Sync
"""
import os
import re
import sys
import time
import uuid
import html
import logging
import threading
from typing import Dict, Any

import telebot

from config import (
    BOT_TOKEN,
    GEMINI_API_KEY,
    ADMIN_ID,
    USER_DB_PATH,
    CORPUS_DB_PATH,
    TEMP_DIR,
    RELAY_MAX_RUNTIME_SECONDS,
    PERIODIC_BACKUP_SECONDS,
    REGISTERS_MAP,
    VOICE_MALE_HOST,
    VOICE_FEMALE_HOST,
    GUIDE_CHAPTERS
)
from database import LoungeDatabase
from media_engine import LoungeMediaEngine
from ai_engine import NYCLoungeAI, LoungeTurnResponse
from keyboards import LoungeKeyboards

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("MusaNYCLounge.App")

START_TIME = time.time()

db = LoungeDatabase(user_db_path=USER_DB_PATH, corpus_db_path=CORPUS_DB_PATH)
ai_engine = NYCLoungeAI(api_key=GEMINI_API_KEY, db=db)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4) if BOT_TOKEN else None


# =====================================================================
# 1. Safe HTML & Clean Display Helpers
# =====================================================================
def sanitize_ai_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    placeholders = {
        "<i>": "___OPEN_I___", "</i>": "___CLOSE_I___",
        "<b>": "___OPEN_B___", "</b>": "___CLOSE_B___",
        "<code>": "___OPEN_C___", "</code>": "___CLOSE_C___"
    }
    t = raw_text
    for tag, ph in placeholders.items():
        t = t.replace(tag, ph)
    t = html.escape(t)
    for tag, ph in placeholders.items():
        t = t.replace(ph, tag)
    if t.count("<i>") > t.count("</i>"):
        t += "</i>" * (t.count("<i>") - t.count("</i>"))
    if t.count("<b>") > t.count("</b>"):
        t += "</b>" * (t.count("<b>") - t.count("</b>"))
    if t.count("<code>") > t.count("</code>"):
        t += "</code>" * (t.count("<code>") - t.count("</code>"))
    return t


def safe_edit_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except Exception as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            return None
        try:
            return bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            plain = re.sub(r"<[^>]+>", "", text)
            return bot.send_message(chat_id, plain, parse_mode=None, reply_markup=reply_markup)


def is_authorized(user_id: int, username: str = "", full_name: str = "") -> bool:
    u = db.get_user(user_id, username, full_name)
    if user_id == ADMIN_ID or ADMIN_ID == 0:
        return True
    return bool(u.get("is_whitelisted", 0))


def build_welcome_banner(user: Dict[str, Any]) -> str:
    stats = db.get_user_stats(user["user_id"])
    mode_names = {
        "spontaneous_chat": "🗣️ NYC Spontaneous Banter",
        "devils_advocate": "🥊 Devil's Advocate Sparring",
        "storytelling_gym": "🎭 Storytelling & Wit Gym",
        "register_lab": "🎛️ Vibe & Style Shifter",
        "subtext_decoder": "🕵️‍♂️ Subtext & Nuance",
        "situation_gambit": "⚡ What to Say When...",
        "fast_lookup": "🔎 Collocation & Phrase Finder",
        "chunk_miner": "🔬 Native Text Miner",
        "podcast_guest": "🎙️ Live Radio Interview",
        "podcast_custom_topic": "✍️ Custom Radio Show",
        "weakness_drill": "🔥 3-Phrase Speaking Challenge",
        "shadowing_drill": "🎙️ Rhythm & Shadowing",
        "srs_active_practice": "🧠 Daily Phrase Practice"
    }
    cur_mode = mode_names.get(user.get("current_mode", "spontaneous_chat"), "🗣️ NYC Spontaneous Banter")
    reg_badge = REGISTERS_MAP.get(user.get("preferred_register", "articulate"), "🎙️ Articulate")

    return (
        f"🗽 <b>Musa's NYC Lounge</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Member:</b> {html.escape(user.get('full_name') or 'Friend')} | 🎚️ <b>Vibe:</b> {reg_badge}\n"
        f"🛋️ <b>Current Room:</b> {cur_mode}\n"
        f"📓 <b>Saved Phrases:</b> <code>{stats['total_vault']}</code> | 🧠 <b>Due for Review:</b> <code>{stats['due_srs']}</code> | ✨ <b>Absorbed in Chat:</b> <code>{stats['stealth_mastered']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Drop a voice note or text anytime, tap <code>🎲 Spark a Fresh Topic</code>, or step into any room below:</i>"
    )


def format_lounge_turn_html(res: LoungeTurnResponse, is_voice: bool = False, stealth_count: int = 0) -> str:
    top_section = ""
    if is_voice and res.transcription:
        top_section += f"🎙️ <i>\"{html.escape(res.transcription)}\"</i>\n\n"

    top_section += f"🗽 {sanitize_ai_html(res.lounge_reply)}\n\n"

    xray_lines = [
        "🔬 <b>Coaching Notes, Rhythm & Expressions (Tap to Expand)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎭 <b>Your Thought Across 3 American Vibes:</b>",
        f"• <b>🎙️ Articulate:</b> <code>{html.escape(res.rewrite_articulate)}</code>",
        f"• <b>☕ Casual:</b> <code>{html.escape(res.rewrite_casual)}</code>",
        f"• <b>🎭 Witty:</b> <code>{html.escape(res.rewrite_witty)}</code>",
        f"• <b>🔄 Mid-Sentence Pivot:</b> <i>{html.escape(res.self_repair_pivot)}</i>" if getattr(res, "self_repair_pivot", "") else "",
        "",
        f"🎧 <b>American Rhythm, Stress & Linking Map:</b>\n{html.escape(res.acoustic_prosody_xray)}"
    ]

    if res.diagnostics:
        xray_lines.append("\n🩺 <b>Precision Polish & Pronunciation Notes:</b>")
        for d in res.diagnostics[:3]:
            xray_lines.append(
                f"• <i>{html.escape(d.user_said)}</i> ➔ ✅ <b>{html.escape(d.native_fix)}</b>\n"
                f"  └ <code>{html.escape(d.category)}</code> {html.escape(d.why_note)}"
            )

    if res.lexical_upgrades:
        xray_lines.append("\n💎 <b>Spoken Expressions to Steal (Tap ⭐ Save 1/2/3):</b>")
        for idx, up in enumerate(res.lexical_upgrades[:3], start=1):
            xray_lines.append(
                f"\n<b>({idx}) {html.escape(up.chunk)}</b> ({html.escape(up.register_tag)})\n"
                f"  • 💡 <b>Nuance:</b> {html.escape(up.explanation)}\n"
                f"  • 🎯 <b>When to use:</b> {html.escape(up.trigger)}\n"
                f"  • 🔗 <b>Pattern:</b> <code>{html.escape(up.pattern)}</code>\n"
                f"  • 💬 <b>Follow-up:</b> <i>\"{html.escape(up.followup)}\"</i>\n"
                f"  • 🗽 <b>In Context:</b> <i>\"{html.escape(up.example)}\"</i>"
            )

    if stealth_count > 0:
        xray_lines.append(
            f"\n✨ <i>Notice the italics above? {stealth_count} expression(s) from your Vault were naturally woven into our conversation!</i>"
        )

    expandable_body = "\n".join(xray_lines)
    return f"{top_section}<blockquote expandable>{expandable_body}</blockquote>"


def send_srs_card_prompt(chat_id: int, user_id: int, card: Dict[str, Any], edit_msg_id: int = 0):
    """Puts user in active review mode so they can either reveal OR send a live voice note/sentence."""
    db.update_user(user_id, current_mode="srs_active_practice")
    db.update_session_meta(user_id, active_srs_card_id=card["id"])

    cloze = card.get("cloze_sentence") or card.get("example_sentence", "").replace(card["chunk"], "______")
    msg_text = (
        f"🧠 <b>Daily Phrase Practice</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎚️ <b>Vibe:</b> {html.escape(card['register_tag'])}\n"
        f"🎯 <b>When to reach for it:</b> {html.escape(card['trigger_context'])}\n"
        f"🔗 <b>Pattern Hint:</b> <code>{html.escape(card['co_text_pattern'])}</code>\n\n"
        f"📝 <b>Fill in the blank:</b>\n"
        f"<i>\"{html.escape(cloze)}\"</i>\n\n"
        f"💡 <b>Two ways to practice:</b>\n"
        f"1️⃣ Tap <code>👁️ Reveal Phrase</code> below to check & self-grade.\n"
        f"2️⃣ Or <b>send a voice note / sentence right now</b> using this expression in your own context for instant coaching!"
    )
    kb = LoungeKeyboards.srs_reveal_kb(card["id"])
    if edit_msg_id:
        safe_edit_text(chat_id, edit_msg_id, msg_text, reply_markup=kb)
    else:
        bot.send_message(chat_id, msg_text, reply_markup=kb)


# =====================================================================
# 2. Bot Commands & Callback Handlers
# =====================================================================
if bot:
    @bot.message_handler(commands=["guide", "help"])
    def cmd_guide(message):
        uid = message.from_user.id
        if not is_authorized(uid, message.from_user.username or "", message.from_user.first_name or ""):
            return
        bot.send_message(
            message.chat.id,
            GUIDE_CHAPTERS["overview"],
            reply_markup=LoungeKeyboards.guide_navigation_kb("overview")
        )

    @bot.message_handler(commands=["start", "menu", "lounge"])
    def cmd_start(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.reply_to(
                message,
                f"🔒 <b>Musa's NYC Lounge is a Private Club.</b>\n"
                f"Send your Guest Pass Code (<code>NYC-XXXXXX</code>) or share your ID with Musa:\n<code>{uid}</code>"
            )
            return

        user = db.get_user(uid, uname, fname)
        bot.send_message(
            message.chat.id,
            build_welcome_banner(user),
            reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
        )

    @bot.message_handler(content_types=["document"])
    def handle_document_restore(message):
        uid = message.from_user.id
        if uid != ADMIN_ID:
            return
        doc = message.document
        if doc and doc.file_name and doc.file_name.endswith(".db"):
            status_msg = bot.reply_to(message, "⏳ <b>Restoring your club notebook...</b>")
            try:
                file_info = bot.get_file(doc.file_id)
                downloaded = bot.download_file(file_info.file_path)
                with open(USER_DB_PATH, "wb") as f:
                    f.write(downloaded)
                db.init_schema()
                safe_edit_text(
                    message.chat.id, status_msg.message_id,
                    "✅ <b>Your Vault & progress have been restored!</b>"
                )
                db.backup_to_telegram(bot, caption_note="Synced Club Vault")
            except Exception:
                safe_edit_text(message.chat.id, status_msg.message_id, "⚠️ Could not restore from that file.")

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        uid = call.from_user.id
        uname = call.from_user.username or ""
        fname = call.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.answer_callback_query(call.id, "🔒 Private Club.", show_alert=True)
            return

        user = db.get_user(uid, uname, fname)
        data = call.data or ""

        try:
            # --- 1. Member's Handbook Chapters ---
            if data.startswith("guide:"):
                chapter_key = data.split(":", 1)[1]
                chapter_html = GUIDE_CHAPTERS.get(chapter_key, GUIDE_CHAPTERS["overview"])
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    chapter_html,
                    reply_markup=LoungeKeyboards.guide_navigation_kb(chapter_key)
                )
                bot.answer_callback_query(call.id)
                return

            # --- 2. Club Menus ---
            if data == "menu:home":
                if user.get("current_mode") == "srs_active_practice":
                    db.update_user(uid, current_mode="spontaneous_chat")
                    user = db.get_user(uid)
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    build_welcome_banner(user),
                    reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:podcast":
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "🎙️ <b>The NYC Radio Lounge (Andrew & Ava)</b>\n\n"
                    "Our two New York hosts record a lively conversation weaving in the phrases from your Vault, pausing mid-show to unpack why each expression works in context:",
                    reply_markup=LoungeKeyboards.podcast_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:miner_lookup":
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "🔎 <b>Phrase Finder & Native Text Miner</b>\n\n"
                    "• <b>Collocation & Phrase Finder:</b> Type any word or verb to explore its most natural spoken American collocations and examples.\n"
                    "• <b>Native Text Miner:</b> Paste any article, dialogue, or quote to extract its best spoken expressions and unpack its subtext:",
                    reply_markup=LoungeKeyboards.miner_lookup_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:drills":
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "🎯 <b>Speaking, Rhythm & Ear Workouts</b>\n\n"
                    "Choose a live workout tailored to your saved phrases and speaking habits:",
                    reply_markup=LoungeKeyboards.drills_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:vault":
                vault_items = db.get_user_vault(uid, limit=10)
                stats = db.get_user_stats(uid)
                preview = "\n".join([
                    f"{i}. <b>{html.escape(v['chunk'])}</b> ({html.escape(v['register_tag'])})"
                    for i, v in enumerate(vault_items, 1)
                ]) if vault_items else "<i>Your Vault is empty right now. Tap [⭐ Save 1/2/3] under any reply to collect expressions!</i>"

                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    f"📓 <b>My Personal Phrasebook & Vault</b>\n"
                    f"Saved Phrases: <code>{stats['total_vault']}</code> | Due Today: <code>{stats['due_srs']}</code> | Absorbed in Chat: <code>{stats['stealth_mastered']}</code>\n\n"
                    f"<b>Latest Saved Expressions:</b>\n{preview}",
                    reply_markup=LoungeKeyboards.vault_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:settings":
                stats = db.get_user_stats(uid)
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    f"⚙️ <b>Preferences & Personal Progress</b>\n\n"
                    f"• Coaching Depth: <code>{user['level']}</code>\n"
                    f"• Default Vibe: <code>{REGISTERS_MAP.get(user['preferred_register'], '🎙️ Articulate')}</code>\n"
                    f"• Collected Expressions: <code>{stats['total_vault']}</code> | Absorbed in Chat: <code>{stats['stealth_mastered']}</code>\n"
                    f"• Habits Being Polished: <code>{stats['active_errors']}</code> | Polished & Mastered: <code>{stats['mastered_errors']}</code>\n\n"
                    f"<i>Adjust your coaching depth below:</i>",
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:admin" and uid == ADMIN_ID:
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "🛡️ <b>Club Host Controls</b>\n\n"
                    "Create Guest Passes for friends, manage club members, or save an immediate Vault snapshot:",
                    reply_markup=LoungeKeyboards.admin_menu()
                )
                bot.answer_callback_query(call.id)

            # --- 3. Spoken Fluency & Style Audit ---
            elif data == "action:fluency_audit":
                bot.answer_callback_query(call.id, "📊 Compiling your Spoken Fluency Audit...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "📊 <b>Reviewing your recent turns, Vault phrases & speaking habits...</b>"
                )
                audit_html = ai_engine.generate_fluency_audit(user)
                safe_edit_text(
                    call.message.chat.id, wait_msg.message_id,
                    audit_html,
                    reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"))
                )

            # --- 3B. Spark a Fresh Topic / Scenario ---
            elif data == "action:spark_topic":
                bot.answer_callback_query(call.id, "🎲 Setting up a fresh topic...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "🎲 <b>Pulling together a fresh New York scenario for you...</b>"
                )
                spark_html = ai_engine.generate_studio_spark(user)
                safe_edit_text(
                    call.message.chat.id, wait_msg.message_id,
                    spark_html,
                    reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"))
                )

            # --- 4. Room Switching ---
            elif data.startswith("mode:"):
                new_mode = data.split(":", 1)[1]
                db.update_user(uid, current_mode=new_mode)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, "✅ Room Ready!")

                if new_mode in ("fast_lookup", "chunk_miner", "podcast_custom_topic", "admin_add_user", "admin_del_user"):
                    static_prompts = {
                        "fast_lookup": (
                            "🔎 <b>Collocation & Phrase Finder</b>\n\n"
                            "Send any word, verb, or short phrase (e.g., <i>slack, point, wind up, doubt</i>) and I'll pull up its most natural spoken American collocations, patterns, and examples!"
                        ),
                        "chunk_miner": (
                            "🔬 <b>Native Text & Dialogue Miner</b>\n\n"
                            "Paste any English paragraph, dialogue, or article here to extract its best spoken expressions and unpack its subtext."
                        ),
                        "podcast_custom_topic": (
                            "✍️ <b>Custom Radio Show</b>\n\n"
                            "Type any topic, dilemma, or question you want <b>Andrew & Ava</b> to discuss on-air while weaving in your saved phrases:"
                        ),
                        "admin_add_user": "➕ Send the numeric <b>Telegram ID</b> of the friend you'd like to invite:",
                        "admin_del_user": "➖ Send the numeric <b>Telegram ID</b> you'd like to remove:"
                    }
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        static_prompts[new_mode],
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                else:
                    studio_banners = {
                        "devils_advocate": (
                            "🥊 <b>Devil's Advocate (Pushback & Sparring Room)</b>\n\n"
                            "Share any opinion, hot take, or decision (voice or text)—or tap <code>🎲 Spark Another Topic</code> for a controversial thesis. I'll push back on your premise so you can practice diplomatic disagreement, hedging, and holding the floor!"
                        ),
                        "spontaneous_chat": (
                            "🗣️ <b>NYC Spontaneous Banter</b>\n\n"
                            "Drop a voice note or text on anything—or tap <code>🎲 Spark Another Topic</code> below for a fresh conversation starter!"
                        ),
                        "storytelling_gym": (
                            "🎭 <b>Storytelling & Wit Gym</b>\n\n"
                            "Tell a short story (real or made up) via voice or text to practice dramatic present tense & punchlines—or tap <code>🎲 Spark Another Topic</code> for a story prompt!"
                        ),
                        "register_lab": (
                            "🎛️ <b>Vibe & Style Shifter</b>\n\n"
                            "Send any sentence or thought to see how it naturally shifts across <b>🎙️ Articulate</b>, <b>☕ Casual</b>, and <b>🎭 Witty</b> vibes!"
                        ),
                        "subtext_decoder": (
                            "🕵️‍♂️ <b>Subtext & Nuance Decoder</b>\n\n"
                            "Send something someone said to you (or tap <code>🎲 Spark Another Topic</code> for a subtext puzzle) to decode what's really being said between the lines!"
                        ),
                        "situation_gambit": (
                            "⚡ <b>What to Say When...</b>\n\n"
                            "Describe any social or workplace moment (in English or Arabic) to get the exact instinctive phrases Americans reach for in that second!"
                        ),
                        "podcast_guest": (
                            "🎙️ <b>Live Radio Interview ('Be the Guest')</b>\n\n"
                            "Tap <code>🎲 Spark Another Topic</code> for the host's opening question, or send a voice note to jump right on-air!"
                        )
                    }
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        studio_banners.get(new_mode, "✅ Room ready!"),
                        reply_markup=LoungeKeyboards.studio_prompt_bar(new_mode)
                    )

            elif data == "action:cycle_register":
                order = ["articulate", "casual", "witty"]
                cur = user.get("preferred_register", "articulate")
                nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "articulate"
                db.update_user(uid, preferred_register=nxt)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"🎚️ Vibe: {REGISTERS_MAP[nxt]}")
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                except Exception:
                    pass

            elif data == "action:clear_chat":
                db.reset_history(uid)
                bot.answer_callback_query(call.id, "🔄 Fresh table! Conversation memory cleared.", show_alert=True)

            # --- 5. Saving & Audio Controls (Host Voice, User Recast, Voice Mirror) ---
            elif data.startswith("save:"):
                _, token_key, idx_str = data.split(":")
                saved_chunk = db.save_atomic_item_to_vault(uid, token_key, int(idx_str))
                if saved_chunk:
                    bot.answer_callback_query(call.id, f"⭐ Saved to your Vault: {saved_chunk}", show_alert=False)
                else:
                    bot.answer_callback_query(call.id, "Already in your Vault!", show_alert=False)

            elif data.startswith("host_tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                host_txt = (meta.get("host_reply_text") or meta.get("audio_clean_text") or "") if meta else ""
                if not host_txt:
                    bot.answer_callback_query(call.id, "Audio unavailable for this message.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🎧 Recording host's voice...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_us_speech(host_txt, voice=VOICE_FEMALE_HOST)
                if ogg_path:
                    with open(ogg_path, "rb") as vf:
                        bot.send_voice(
                            call.message.chat.id, vf,
                            caption="🗽 <i>Host's Spoken Reply</i>"
                        )
                    LoungeMediaEngine.cleanup_file(ogg_path)

            elif data.startswith("tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("audio_clean_text"):
                    bot.answer_callback_query(call.id, "Audio unavailable for this message.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🗣️ Recording your American recast...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_us_speech(meta["audio_clean_text"], voice=VOICE_MALE_HOST)
                if ogg_path:
                    with open(ogg_path, "rb") as vf:
                        bot.send_voice(
                            call.message.chat.id, vf,
                            caption=f"🗣️ <code>{html.escape(meta['audio_clean_text'])}</code>"
                        )
                    LoungeMediaEngine.cleanup_file(ogg_path)

            elif data.startswith("mirror:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("user_voice_file_id"):
                    bot.answer_callback_query(call.id, "Send a voice note first to use the Voice Mirror!", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🪞 Preparing your Voice Mirror (1.0x + 0.85x)...")
                bot.send_chat_action(call.message.chat.id, "record_voice")

                user_ogg = os.path.join(TEMP_DIR, f"u_{token_key}.ogg")
                try:
                    f_info = bot.get_file(meta["user_voice_file_id"])
                    raw_voice = bot.download_file(f_info.file_path)
                    with open(user_ogg, "wb") as f:
                        f.write(raw_voice)
                    mirror_ogg = LoungeMediaEngine.build_acoustic_mirror(user_ogg, meta["audio_clean_text"])
                    if mirror_ogg:
                        with open(mirror_ogg, "rb") as mf:
                            bot.send_voice(
                                call.message.chat.id, mf,
                                caption=(
                                    "🪞 <b>Side-by-Side Voice Mirror:</b>\n"
                                    "1️⃣ Your Voice ➔ 🔔 ➔ 2️⃣ Native Pace (1.0x) ➔ 🔔 ➔ 3️⃣ Slow Study Pace (0.85x)\n\n"
                                    f"🎯 <code>{html.escape(meta['audio_clean_text'])}</code>"
                                )
                            )
                        LoungeMediaEngine.cleanup_file(mirror_ogg)
                finally:
                    LoungeMediaEngine.cleanup_file(user_ogg)

            # --- 6. The Radio Lounge (Andrew & Ava) ---
            elif data == "pod:generate_auto":
                bot.answer_callback_query(call.id, "🎙️ Andrew & Ava are stepping up to the mic...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "🎙️ <b>The NYC Radio Lounge is recording your episode...</b>\n"
                    "<i>Andrew & Ava are weaving your saved expressions into today's show (~15s).</i>"
                )
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ep = ai_engine.generate_podcast_episode(user)
                mp3_path = LoungeMediaEngine.produce_dual_host_podcast(ep)

                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in ep.key_takeaways]
                db.store_atomic_batch(uid, token_key, upgrades_dicts, audio_clean_text=ep.title)

                script_html = "\n\n".join([
                    f"<b>{'🧔‍♂️ Andrew' if line.speaker == 'male_host' else '👩🏼‍💼 Ava'}:</b> {html.escape(line.text)}"
                    for line in ep.script
                ])
                takeaways_html = "\n".join([
                    f"<b>({i}) {html.escape(u.chunk)}</b> ({html.escape(u.register_tag)}) — {html.escape(u.explanation)}"
                    for i, u in enumerate(ep.key_takeaways, 1)
                ])

                full_caption = (
                    f"🎙️ <b>{html.escape(ep.title)}</b>\n"
                    f"💎 <b>Featured Expressions:</b> <code>{html.escape(', '.join(ep.target_chunks_covered))}</code>\n\n"
                    f"<blockquote expandable>📜 <b>Full Show Transcript & Notes (Tap to Expand):</b>\n\n"
                    f"{script_html}\n\n"
                    f"✨ <b>Expressions to Save:</b>\n{takeaways_html}</blockquote>"
                )

                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            call.message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge (Andrew & Ava)",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False)
                        )
                    if len(full_caption) > 1024:
                        bot.send_message(
                            call.message.chat.id, full_caption,
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        call.message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False)
                    )
                try:
                    bot.delete_message(call.message.chat.id, wait_msg.message_id)
                except Exception:
                    pass

            # --- 7. Speaking Workouts & Habits Being Polished ---
            elif data.startswith("drill:"):
                drill_type = data.split(":", 1)[1]
                if drill_type == "weakness_list":
                    weaknesses = db.get_weaknesses(uid, limit=6)
                    if not weaknesses:
                        bot.answer_callback_query(call.id, "🎉 No recurring slips on your radar right now!", show_alert=True)
                        return
                    lines = ["🩺 <b>Speaking Habits You're Polishing:</b>\n"]
                    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for w in weaknesses:
                        lines.append(
                            f"• <i>{html.escape(w['user_said'])}</i> ➔ ✅ <b>{html.escape(w['native_fix'])}</b>\n"
                            f"  └ <code>{html.escape(w['category'])}</code> {html.escape(w['why_note'])}"
                        )
                        kb.add(telebot.types.InlineKeyboardButton(f"✅ Got It: {w['native_fix'][:18]}", callback_data=f"master:{w['id']}"))
                    kb.add(telebot.types.InlineKeyboardButton("🔥 Practice These in a 3-Phrase Challenge", callback_data="drill:forced_output"))
                    kb.add(telebot.types.InlineKeyboardButton("🔙 Back to Workouts", callback_data="menu:drills"))
                    safe_edit_text(call.message.chat.id, call.message.message_id, "\n".join(lines), reply_markup=kb)
                    bot.answer_callback_query(call.id)
                    return

                bot.answer_callback_query(call.id, "🎯 Setting up your workout...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "🎯 <b>Preparing a fresh workout tailored to your Vault...</b>"
                )
                mode_map = {
                    "forced_output": "weakness_drill",
                    "contrastive_stress": "shadowing_drill",
                    "shadowing": "shadowing_drill",
                    "micro_dictation": "shadowing_drill",
                    "diagnostic_test": "spontaneous_chat"
                }
                db.update_user(uid, current_mode=mode_map.get(drill_type, "spontaneous_chat"))
                drill_data = ai_engine.build_active_drill(user, drill_type)
                safe_edit_text(
                    call.message.chat.id, wait_msg.message_id,
                    drill_data["text"],
                    reply_markup=LoungeKeyboards.studio_prompt_bar(mode_map.get(drill_type, "spontaneous_chat"))
                )

                if drill_data.get("tts_target"):
                    bot.send_chat_action(call.message.chat.id, "record_voice")
                    ogg_p = LoungeMediaEngine.synthesize_us_speech(drill_data["tts_target"])
                    if ogg_p:
                        with open(ogg_p, "rb") as vf:
                            bot.send_voice(call.message.chat.id, vf, caption="🎧 Listen closely, then reply with your voice note or text:")
                        LoungeMediaEngine.cleanup_file(ogg_p)

            elif data.startswith("master:"):
                err_id = int(data.split(":", 1)[1])
                db.mark_weakness_mastered(err_id)
                bot.answer_callback_query(call.id, "🎉 Checked off your list!")

            # --- 8. Daily Phrase Review (Active & Self-Graded) ---
            elif data == "srs:next":
                card = db.get_due_srs_card(uid)
                if not card:
                    db.update_user(uid, current_mode="spontaneous_chat")
                    bot.answer_callback_query(call.id, "🎉 You're all caught up on reviews for today!", show_alert=True)
                    return
                send_srs_card_prompt(call.message.chat.id, uid, card)
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:reveal:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if not card:
                    bot.answer_callback_query(call.id, "Phrase not found.")
                    return
                ans_text = (
                    f"🧠 <b>Phrase Details & Context:</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>{html.escape(card['chunk'])}</b> ({html.escape(card['register_tag'])})\n"
                    f"• <b>Nuance:</b> {html.escape(card['explanation'])}\n"
                    f"• 🎯 <b>When to use:</b> {html.escape(card['trigger_context'])}\n"
                    f"• 🔗 <b>Pattern:</b> <code>{html.escape(card['co_text_pattern'])}</code>\n"
                    f"• 💬 <b>Follow-up:</b> <i>\"{html.escape(card['typical_followup'])}\"</i>\n\n"
                    f"🗽 <b>In Conversation:</b>\n<i>\"{html.escape(card['example_sentence'])}\"</i>\n\n"
                    f"👇 <b>How natural did this feel—or send a voice note trying it out!</b>"
                )
                safe_edit_text(call.message.chat.id, call.message.message_id, ans_text, reply_markup=LoungeKeyboards.srs_grade_kb(card_id))
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:grade:"):
                _, _, card_id_str, grade_str = data.split(":")
                db.grade_srs_card(int(card_id_str), int(grade_str))
                bot.answer_callback_query(call.id, "✅ Scheduled!")
                nxt_card = db.get_due_srs_card(uid)
                if nxt_card:
                    send_srs_card_prompt(call.message.chat.id, uid, nxt_card, edit_msg_id=call.message.message_id)
                else:
                    db.update_user(uid, current_mode="spontaneous_chat")
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        "🎉 <b>All caught up! You've reviewed all due expressions for today.</b>",
                        reply_markup=LoungeKeyboards.main_menu(db.get_user(uid), is_admin=(uid == ADMIN_ID))
                    )

            elif data.startswith("srs:tts:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if card:
                    bot.answer_callback_query(call.id, "🔊 Playing example...")
                    ogg_p = LoungeMediaEngine.synthesize_us_speech(card.get("example_sentence") or card["chunk"])
                    if ogg_p:
                        with open(ogg_p, "rb") as vf:
                            bot.send_voice(call.message.chat.id, vf, caption=f"🇺🇸 <b>{html.escape(card['chunk'])}</b>")
                        LoungeMediaEngine.cleanup_file(ogg_p)

            # --- 9. Booklet & Flashcard Exports ---
            elif data == "export:pdf":
                vault_items = db.get_user_vault(uid, limit=250)
                if not vault_items:
                    bot.answer_callback_query(call.id, "Your Vault is empty—save a few phrases first!", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "📄 Preparing your PDF Phrasebook...")
                stats = db.get_user_stats(uid)
                pdf_p = LoungeMediaEngine.export_vault_pdf(user, vault_items, stats)
                if pdf_p:
                    with open(pdf_p, "rb") as pf:
                        bot.send_document(
                            call.message.chat.id, pf,
                            caption="📄 <b>Musa's NYC Lounge — Personal Phrasebook</b>"
                        )
                    LoungeMediaEngine.cleanup_file(pdf_p)

            elif data == "export:anki":
                vault_items = db.get_user_vault(uid, limit=500)
                if not vault_items:
                    bot.answer_callback_query(call.id, "Your Vault is empty—save a few phrases first!", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🗂️ Preparing your Flashcard Deck...")
                csv_p = LoungeMediaEngine.export_vault_anki_csv(vault_items)
                if csv_p:
                    with open(csv_p, "rb") as cf:
                        bot.send_document(
                            call.message.chat.id, cf,
                            caption="🗂️ <b>Personal Flashcard Deck (Anki Import Ready)</b>"
                        )
                    LoungeMediaEngine.cleanup_file(csv_p)

            # --- 10. Preferences & Host Controls ---
            elif data.startswith("set:level:"):
                new_lv = data.split(":")[2]
                db.update_user(uid, level=new_lv)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"✅ Coaching depth set to {new_lv}")
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.settings_menu(user)
                    )
                except Exception:
                    pass

            elif data == "set:toggle_eng":
                cur_eng = bool(user.get("english_only", 1))
                if user.get("level") in ("C1", "C2") and cur_eng:
                    bot.answer_callback_query(call.id, "🔒 Full English Immersion stays active at C1/C2 depth!", show_alert=True)
                    return
                db.update_user(uid, english_only=(0 if cur_eng else 1))
                user = db.get_user(uid)
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.settings_menu(user)
                    )
                except Exception:
                    pass
                bot.answer_callback_query(call.id, "✅ Preference saved.")

            elif data == "admin:gen_invite" and uid == ADMIN_ID:
                inv_code = db.create_invite_code(daily_limit=80)
                bot.send_message(
                    call.message.chat.id,
                    f"🎟️ <b>New Guest Pass Created:</b>\n\n"
                    f"<code>{inv_code}</code>\n\n"
                    f"<i>Share this pass with a friend—as soon as they send it to the bot, they're in!</i>",
                    reply_markup=LoungeKeyboards.admin_menu()
                )
                bot.answer_callback_query(call.id, "🎟️ Guest Pass Ready!")

            elif data == "admin:backup_now" and uid == ADMIN_ID:
                bot.answer_callback_query(call.id, "💾 Saving Vault snapshot...")
                db.backup_to_telegram(bot, caption_note="Club Vault Snapshot")

            elif data == "admin:list_users" and uid == ADMIN_ID:
                wl = db.list_whitelisted()
                txt = "👥 <b>Club Members:</b>\n\n" + "\n".join([
                    f"• <code>{u['user_id']}</code> — {html.escape(u['full_name'] or u['username'] or 'Member')} ({u['level']})"
                    for u in wl
                ])
                bot.send_message(call.message.chat.id, txt, reply_markup=LoungeKeyboards.admin_menu())
                bot.answer_callback_query(call.id)

        except Exception as e:
            logger.error(f"Callback error ({data}): {e}")
            bot.answer_callback_query(call.id, "☕ Just a second—try tapping that once more.")

    # =====================================================================
    # 3. Voice Note Handler (Supports Both Live Conversation & Active Review)
    # =====================================================================
    @bot.message_handler(content_types=["voice", "audio"])
    def handle_voice_input(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.reply_to(message, f"🔒 Private Club. Your ID: <code>{uid}</code>")
            return

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "☕ We've hit today's session limit—catch you tomorrow!")
            return

        user = db.get_user(uid, uname, fname)
        mode = user.get("current_mode", "spontaneous_chat")

        try:
            voice_obj = message.voice or message.audio
            file_id = voice_obj.file_id
            file_info = bot.get_file(file_id)
            audio_bytes = bot.download_file(file_info.file_path)

            # --- Active Phrase Practice Mode via Voice Note ---
            if mode == "srs_active_practice":
                meta = db.get_session_meta(uid)
                card_id = int(meta.get("active_srs_card_id", 0))
                card = db.get_srs_card_by_id(card_id) if card_id else None
                if card:
                    wait_msg = bot.reply_to(message, f"🎧 <b>Listening to how you used <code>{html.escape(card['chunk'])}</code>...</b>")
                    ev = ai_engine.evaluate_active_srs_usage(user, card, audio_bytes=audio_bytes)
                    db.grade_srs_card(card_id, ev.recommended_grade)
                    badge = "🌟 <b>Spot-On Delivery!</b>" if ev.used_naturally else "💡 <b>Close—Here's How to Polish It:</b>"
                    reply_html = (
                        f"{badge}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🗽 {html.escape(ev.coach_feedback)}\n\n"
                        f"🗣️ <b>Polished Native Delivery:</b>\n<code>{html.escape(ev.polished_version)}</code>"
                    )
                    safe_edit_text(
                        message.chat.id, wait_msg.message_id, reply_html,
                        reply_markup=LoungeKeyboards.srs_grade_kb(card_id)
                    )
                    return

            # --- Standard Conversational & Phonetic Coaching ---
            status_msg = bot.reply_to(
                message,
                "🎧 <b>Listening closely to your phrasing, vowels & American rhythm...</b>"
            )
            bot.send_chat_action(message.chat.id, "typing")

            res, stealth_ids = ai_engine.process_lounge_turn(
                user=user,
                user_text="",
                audio_bytes=audio_bytes,
                audio_mime="audio/ogg"
            )

            db.append_history(uid, "user", f"[Voice]: {res.transcription}")
            db.append_history(uid, "assistant", res.lounge_reply)
            db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
            if stealth_ids:
                db.increment_stealth_done(stealth_ids)

            token_key = uuid.uuid4().hex[:10]
            upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
            db.store_atomic_batch(
                user_id=uid,
                token_key=token_key,
                items=upgrades_dicts,
                audio_clean_text=res.tts_clean_target or res.rewrite_articulate,
                host_reply_text=res.lounge_reply,
                user_voice_file_id=file_id
            )

            formatted_html = format_lounge_turn_html(res, is_voice=True, stealth_count=len(stealth_ids))
            safe_edit_text(
                message.chat.id,
                status_msg.message_id,
                formatted_html,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=True, has_host_voice=True
                )
            )
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            bot.reply_to(message, "☕ Give me just a second—could you send that voice note once more?")

    # =====================================================================
    # 4. Text Input Handler (Banter, Collocation Explorer, Active Review)
    # =====================================================================
    @bot.message_handler(content_types=["text"])
    def handle_text_input(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"
        text = (message.text or "").strip()

        if not text or text.startswith("/"):
            return

        if not is_authorized(uid, uname, fname):
            if text.upper().startswith("NYC-"):
                lim = db.redeem_invite_code(uid, text)
                if lim:
                    u_new = db.get_user(uid, uname, fname)
                    bot.reply_to(
                        message,
                        "🎉 <b>Welcome to Musa's NYC Lounge!</b>\nYour Guest Pass is active—pull up a chair!",
                        reply_markup=LoungeKeyboards.main_menu(u_new, is_admin=False)
                    )
                    return
            bot.reply_to(
                message,
                f"🔒 <b>Private Club.</b>\nSend your Guest Pass (<code>NYC-XXXXXX</code>) or share your ID with Musa: <code>{uid}</code>"
            )
            return

        user = db.get_user(uid, uname, fname)
        mode = user.get("current_mode", "spontaneous_chat")

        # --- A. Host Member Management ---
        if uid == ADMIN_ID and mode in ("admin_add_user", "admin_del_user"):
            digits = "".join([c for c in text if c.isdigit()])
            if not digits:
                bot.reply_to(message, "Please send a numeric Telegram ID.")
                return
            target_id = int(digits)
            status_val = 1 if mode == "admin_add_user" else 0
            db.set_whitelist(target_id, status=status_val)
            db.update_user(uid, current_mode="spontaneous_chat")
            action_str = "added to the club" if status_val == 1 else "removed"
            bot.reply_to(
                message,
                f"✅ Member <code>{target_id}</code> has been {action_str}.",
                reply_markup=LoungeKeyboards.main_menu(db.get_user(uid), is_admin=True)
            )
            return

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "☕ We've hit today's session limit—catch you tomorrow!")
            return

        # --- B. Active Phrase Practice Mode via Text ---
        if mode == "srs_active_practice":
            meta = db.get_session_meta(uid)
            card_id = int(meta.get("active_srs_card_id", 0))
            card = db.get_srs_card_by_id(card_id) if card_id else None
            if card:
                wait_msg = bot.reply_to(message, f"✨ <b>Checking your sentence with <code>{html.escape(card['chunk'])}</code>...</b>")
                try:
                    ev = ai_engine.evaluate_active_srs_usage(user, card, user_text=text)
                    db.grade_srs_card(card_id, ev.recommended_grade)
                    badge = "🌟 <b>Spot-On Usage!</b>" if ev.used_naturally else "💡 <b>Nice Try—Here's How to Make It Sound Effortless:</b>"
                    reply_html = (
                        f"{badge}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🗽 {html.escape(ev.coach_feedback)}\n\n"
                        f"🗣️ <b>Natural New York Phrasing:</b>\n<code>{html.escape(ev.polished_version)}</code>"
                    )
                    safe_edit_text(
                        message.chat.id, wait_msg.message_id, reply_html,
                        reply_markup=LoungeKeyboards.srs_grade_kb(card_id)
                    )
                except Exception:
                    safe_edit_text(message.chat.id, wait_msg.message_id, "☕ Let's try that sentence once more.")
                return

        # --- C. Collocation & Phrase Finder (Instant Local + Live Collocation Map) ---
        if mode == "fast_lookup" and len(text.split()) <= 5:
            local_hits = db.lookup_collocations(text, user_id=uid, limit=4)
            wait_msg = bot.reply_to(message, f"🔎 <b>Exploring natural American collocations for <code>{html.escape(text)}</code>...</b>")
            try:
                explorer = ai_engine.explore_collocations_live(user, text)
                token_key = uuid.uuid4().hex[:10]
                frames_dicts = [f.model_dump() for f in explorer.collocation_frames]
                first_ex = explorer.collocation_frames[0].example if explorer.collocation_frames else text
                db.store_atomic_batch(uid, token_key, frames_dicts, audio_clean_text=first_ex)

                lines = [
                    f"🔎 <b>Spoken Collocation Map: <code>{html.escape(explorer.headword)}</code></b>",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"💡 <b>Nuance & Feel:</b> {html.escape(explorer.semantic_note)}\n"
                ]
                if local_hits:
                    lines.append("📓 <b>Already in Your Club Phrasebook:</b>")
                    for h in local_hits[:2]:
                        lines.append(f"• <b>{html.escape(h['chunk'])}</b> ({html.escape(h['register_tag'])}) — <i>\"{html.escape(h['example_sentence'])}\"</i>")
                    lines.append("")

                lines.append("💎 <b>Top Spoken Collocations (Tap ⭐ Save 1–4 below):</b>")
                for idx, cf in enumerate(explorer.collocation_frames[:4], 1):
                    lines.append(
                        f"\n<b>({idx}) {html.escape(cf.chunk)}</b> ({html.escape(cf.register_tag)})\n"
                        f"  • 💡 {html.escape(cf.explanation)}\n"
                        f"  • 🎯 <b>When:</b> {html.escape(cf.trigger)}\n"
                        f"  • 🔗 <b>Pattern:</b> <code>{html.escape(cf.pattern)}</code>\n"
                        f"  • 🗽 <i>\"{html.escape(cf.example)}\"</i>"
                    )

                safe_edit_text(
                    message.chat.id, wait_msg.message_id,
                    "\n".join(lines),
                    reply_markup=LoungeKeyboards.turn_action_bar(
                        token_key, len(explorer.collocation_frames[:4]), has_user_voice=False, has_host_voice=False
                    )
                )
            except Exception as e:
                logger.error(f"Collocation explorer error: {e}")
                if local_hits:
                    lines = [f"🔎 <b>Phrasebook Matches for <code>{html.escape(text)}</code>:</b>\n"]
                    for h in local_hits:
                        lines.append(
                            f"• <b>{html.escape(h['chunk'])}</b> ({html.escape(h['register_tag'])})\n"
                            f"  └ 💡 {html.escape(h['explanation'])}\n"
                            f"  └ 🗽 <i>\"{html.escape(h['example_sentence'])}\"</i>"
                        )
                    safe_edit_text(message.chat.id, wait_msg.message_id, "\n".join(lines))
                else:
                    safe_edit_text(message.chat.id, wait_msg.message_id, "☕ Try sending a single verb, noun, or short expression.")
            return

        # --- D. Custom Radio Episode Producer ---
        if mode == "podcast_custom_topic":
            wait_msg = bot.reply_to(
                message,
                f"🎙️ <b>Andrew & Ava are recording an episode on:</b> <i>{html.escape(text)}</i>..."
            )
            try:
                db.update_user(uid, current_mode="spontaneous_chat")
                ep = ai_engine.generate_podcast_episode(user, custom_topic=text)
                mp3_path = LoungeMediaEngine.produce_dual_host_podcast(ep)

                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in ep.key_takeaways]
                db.store_atomic_batch(uid, token_key, upgrades_dicts, audio_clean_text=ep.title)

                script_html = "\n\n".join([
                    f"<b>{'🧔‍♂️ Andrew' if line.speaker == 'male_host' else '👩🏼‍💼 Ava'}:</b> {html.escape(line.text)}"
                    for line in ep.script
                ])
                full_caption = (
                    f"🎙️ <b>{html.escape(ep.title)}</b>\n\n"
                    f"<blockquote expandable>📜 <b>Full Show Transcript (Tap to Expand):</b>\n\n{script_html}</blockquote>"
                )
                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False)
                    )
                try:
                    bot.delete_message(message.chat.id, wait_msg.message_id)
                except Exception:
                    pass
            except Exception:
                safe_edit_text(message.chat.id, wait_msg.message_id, "☕ Studio mics were busy for a second—try that topic once more!")
            return

        # --- E. Standard Conversational Turn or Native Text Miner ---
        status_msg = bot.reply_to(message, "🗽 <b>Thinking...</b>")
        bot.send_chat_action(message.chat.id, "typing")

        try:
            if mode == "chunk_miner":
                res = ai_engine.mine_text_and_subtext(user, text)
                stealth_ids = []
            else:
                res, stealth_ids = ai_engine.process_lounge_turn(user=user, user_text=text)

            db.append_history(uid, "user", text)
            db.append_history(uid, "assistant", res.lounge_reply)
            db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
            if stealth_ids:
                db.increment_stealth_done(stealth_ids)

            token_key = uuid.uuid4().hex[:10]
            upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
            db.store_atomic_batch(
                user_id=uid,
                token_key=token_key,
                items=upgrades_dicts,
                audio_clean_text=res.tts_clean_target or res.rewrite_articulate,
                host_reply_text=res.lounge_reply
            )

            formatted_html = format_lounge_turn_html(res, is_voice=False, stealth_count=len(stealth_ids))
            safe_edit_text(
                message.chat.id,
                status_msg.message_id,
                formatted_html,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=False, has_host_voice=True
                )
            )
        except Exception as e:
            logger.error(f"Text turn error: {e}")
            safe_edit_text(
                message.chat.id, status_msg.message_id,
                "☕ Give me just a second—send that thought once more."
            )


# =====================================================================
# 5. Silent Background Lifecycle & Clean Profile Metadata
# =====================================================================
def sync_bot_profile_metadata():
    if not bot:
        return
    try:
        bot.set_my_short_description(
            "🗽 Musa's NYC Lounge — Private Manhattan Club for Spoken American English, Accent Precision & Witty Banter."
        )
        bot.set_my_description(
            "🗽 Welcome to Musa's NYC Lounge!\n\n"
            "Your private Manhattan conversational club for effortless Spoken American English.\n\n"
            "✨ Inside the Club:\n"
            "• 🗣️ Unscripted New York Banter & Storytelling Gym\n"
            "• 🎛️ 3 Living Vibes (Articulate, Casual, Witty)\n"
            "• 🪞 Side-by-Side Voice Mirror & Pronunciation Coaching\n"
            "• 🎙️ The NYC Radio Lounge with Andrew & Ava\n"
            "• 🔎 Spoken Collocation Finder & Personal Phrasebook\n\n"
            "Tap Start or /guide to pull up a chair!"
        )
        bot.set_my_commands([
            telebot.types.BotCommand("start", "🗽 Enter the Lounge & Main Menu"),
            telebot.types.BotCommand("guide", "📖 Open the Member's Handbook"),
            telebot.types.BotCommand("lounge", "🛋️ Switch Conversation Rooms"),
            telebot.types.BotCommand("menu", "🏠 Show Club Dashboard")
        ])
    except Exception as e:
        logger.warning(f"Profile sync note: {e}")


def relay_lifecycle_watchdog():
    last_periodic_backup = time.time()
    while True:
        time.sleep(25)
        elapsed = time.time() - START_TIME

        if time.time() - last_periodic_backup >= PERIODIC_BACKUP_SECONDS and bot:
            db.backup_to_telegram(bot, caption_note="Synced Club Vault")
            last_periodic_backup = time.time()

        if elapsed >= RELAY_MAX_RUNTIME_SECONDS:
            logger.info("⏳ Handing over club shift seamlessly...")
            if bot:
                db.backup_to_telegram(bot, caption_note="Synced Club Vault")
                try:
                    bot.stop_polling()
                except Exception:
                    pass
            os._exit(0)


def main():
    if not BOT_TOKEN or not GEMINI_API_KEY:
        logger.error("Missing required credentials.")
        sys.exit(1)

    logger.info("🗽 Starting Musa's NYC Lounge...")

    restored = db.restore_from_telegram_pin(bot)
    sync_bot_profile_metadata()

    watchdog_thread = threading.Thread(target=relay_lifecycle_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        bot.remove_webhook()
        # Only send a welcome message if this is a brand-new setup, never spam on 5.3h shift handovers!
        if ADMIN_ID and not restored and not db.get_sys_state("club_initialized"):
            db.set_sys_state("club_initialized", "1")
            u_admin = db.get_user(ADMIN_ID, "Admin", "Musa")
            bot.send_message(
                ADMIN_ID,
                build_welcome_banner(u_admin),
                reply_markup=LoungeKeyboards.main_menu(u_admin, is_admin=True)
            )
    except Exception as e:
        logger.warning(f"Startup note: {e}")

    logger.info("✅ Club doors are open and polling!")
    bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=False)


if __name__ == "__main__":
    main()
