# -*- coding: utf-8 -*-
"""
app.py — Main Entrypoint, Handlers & 5.3-Hour GitHub Actions Relay Watchdog
Musa's NYC Lounge (100% Gemini-Powered, Modular Dual-DB Architecture)
"""
import os
import sys
import time
import uuid
import html
import logging
import threading
from typing import Dict, Any, List

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
# 1. UI Formatters & Welcome Banner
# =====================================================================

def sanitize_ai_html(raw_text: str) -> str:
    """Escapes stray < or > from AI output while preserving valid <i>, <b>, and <code> tags."""
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
    # Close unclosed <i> tag if AI forgot closing tag
    if t.count("<i>") > t.count("</i>"):
        t += "</i>" * (t.count("<i>") - t.count("</i>"))
    if t.count("<b>") > t.count("</b>"):
        t += "</b>" * (t.count("<b>") - t.count("</b>"))
    return t


def is_authorized(user_id: int, username: str = "", full_name: str = "") -> bool:
    u = db.get_user(user_id, username, full_name)
    if user_id == ADMIN_ID or ADMIN_ID == 0:
        return True
    return bool(u.get("is_whitelisted", 0))


def build_welcome_banner(user: Dict[str, Any]) -> str:
    stats = db.get_user_stats(user["user_id"])
    mode_names = {
        "spontaneous_chat": "🗣️ NYC Spontaneous Banter",
        "storytelling_gym": "🎭 Storytelling & Wit Gym",
        "register_lab": "🎛️ Register Shifter Lab",
        "subtext_decoder": "🕵️‍♂️ Subtext & Irony Decoder",
        "situation_gambit": "⚡ Social Situation Gambits",
        "fast_lookup": "⚡ Instant SQLite FTS5 Lookup",
        "chunk_miner": "🔬 Native Text & Subtext Miner",
        "podcast_guest": "🎙️ Podcast Studio (You're the Guest)",
        "podcast_custom_topic": "✍️ Custom Podcast Producer",
        "weakness_drill": "🔥 Forced Output Workout",
        "shadowing_drill": "🎙️ Connected Speech Workout"
    }
    cur_mode = mode_names.get(user.get("current_mode", "spontaneous_chat"), user.get("current_mode"))
    reg_badge = REGISTERS_MAP.get(user.get("preferred_register", "articulate"), "[🎙️ Articulate / Natural]")

    return (
        f"🗽 <b>Musa's NYC Lounge — 24/7 Spoken American Mastery</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Member:</b> {html.escape(user.get('full_name') or 'Friend')} | 🎓 <b>Level:</b> <code>{user.get('level', 'C1')}</code>\n"
        f"🎯 <b>Active Studio:</b> {cur_mode}\n"
        f"🎚️ <b>Register Vibe:</b> {reg_badge}\n"
        f"📊 <b>Vault:</b> <code>{stats['total_vault']}</code> frames | 🧠 <b>Due SRS:</b> <code>{stats['due_srs']}</code> | 🌊 <b>Stealth Acquired:</b> <code>{stats['stealth_mastered']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Send a voice note or text anytime, tap <code>🎲 Generate Fresh AI Scenario</code>, or explore the studios below:</i>"
    )


def format_lounge_turn_html(res: LoungeTurnResponse, is_voice: bool = False, stealth_count: int = 0) -> str:
    top_section = ""
    if is_voice and res.transcription:
        top_section += f"🎙️ <i>\"{html.escape(res.transcription)}\"</i>\n\n"

    top_section += f"🗽 {sanitize_ai_html(res.lounge_reply)}\n\n"

    xray_lines = [
        "🔬 <b>NYC Linguistic & Acoustic X-Ray (Tap to Expand)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎭 <b>Register Shifter (3 Living American Vibes):</b>",
        f"• <b>[🎙️ Articulate]:</b> <code>{html.escape(res.rewrite_articulate)}</code>",
        f"• <b>[☕ Casual]:</b> <code>{html.escape(res.rewrite_casual)}</code>",
        f"• <b>[🎭 Witty]:</b> <code>{html.escape(res.rewrite_witty)}</code>",
        "",
        f"🎧 <b>US Connected Speech & Prosody Map:</b>\n{html.escape(res.acoustic_prosody_xray)}"
    ]

    if res.diagnostics:
        xray_lines.append("\n🩺 <b>Surgical Diagnostics & L1 Transfer Check:</b>")
        for d in res.diagnostics[:3]:
            xray_lines.append(
                f"• ❌ <i>{html.escape(d.user_said)}</i> ➔ ✅ <b>{html.escape(d.native_fix)}</b>\n"
                f"  └ <code>{html.escape(d.category)}</code> {html.escape(d.why_note)}"
            )

    if res.lexical_upgrades:
        xray_lines.append("\n💎 <b>3-Part Lexical Frames (Tap ⭐ Save 1/2/3 below):</b>")
        for idx, up in enumerate(res.lexical_upgrades[:3], start=1):
            xray_lines.append(
                f"\n<b>({idx}) {html.escape(up.chunk)}</b> {html.escape(up.register_tag)}\n"
                f"  • 💡 <b>Meaning:</b> {html.escape(up.explanation)}\n"
                f"  • 🎯 <b>Trigger:</b> {html.escape(up.trigger)}\n"
                f"  • 🔗 <b>Pattern:</b> <code>{html.escape(up.pattern)}</code>\n"
                f"  • 💬 <b>Follow-up:</b> <i>\"{html.escape(up.followup)}\"</i>\n"
                f"  • 🗽 <b>Example:</b> <i>\"{html.escape(up.example)}\"</i>"
            )

    if stealth_count > 0:
        xray_lines.append(
            f"\n🌊 <i>Stealth Input Flood: {stealth_count} chunk(s) from your Vault were naturally woven into the host's reply above (in italics)!</i>"
        )

    expandable_body = "\n".join(xray_lines)
    return f"{top_section}<blockquote expandable>{expandable_body}</blockquote>"


# =====================================================================
# 2. Bot Command & Callback Handlers
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
                f"Send your Invite Code (<code>NYC-XXXXXX</code>) or share your Telegram ID with Musa:\n<code>{uid}</code>"
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
            status_msg = bot.reply_to(message, "⏳ <b>Restoring SQLite database from uploaded file...</b>")
            try:
                file_info = bot.get_file(doc.file_id)
                downloaded = bot.download_file(file_info.file_path)
                with open(USER_DB_PATH, "wb") as f:
                    f.write(downloaded)
                db.init_schema()
                bot.edit_message_text(
                    "✅ <b>Database successfully restored and migrated!</b>",
                    message.chat.id, status_msg.message_id
                )
                db.backup_to_telegram(bot, caption_note="📌 Manual DB Restore Pin")
            except Exception as e:
                bot.edit_message_text(f"❌ Restore failed: {e}", message.chat.id, status_msg.message_id)

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        uid = call.from_user.id
        uname = call.from_user.username or ""
        fname = call.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.answer_callback_query(call.id, "🔒 Private Club: Access Denied.", show_alert=True)
            return

        user = db.get_user(uid, uname, fname)
        data = call.data or ""

        try:
            # --- 1. Interactive 6-Chapter Detailed User Guide ---
            if data.startswith("guide:"):
                chapter_key = data.split(":", 1)[1]
                chapter_html = GUIDE_CHAPTERS.get(chapter_key, GUIDE_CHAPTERS["overview"])
                bot.edit_message_text(
                    chapter_html,
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.guide_navigation_kb(chapter_key)
                )
                bot.answer_callback_query(call.id, "📖 Guide Chapter Loaded")
                return

            # --- 2. Main Navigation Menus ---
            if data == "menu:home":
                bot.edit_message_text(
                    build_welcome_banner(user),
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:podcast":
                bot.edit_message_text(
                    "🎙️ <b>Musa's NYC Lounge — Dual-Host Podcast Studio</b>\n\n"
                    "Our New York hosts (<b>Andrew & Ava</b>) dynamically script and record a lively episode calibrated to your level, weaving in your Vault chunks and pausing mid-show to unpack the pragmatic subtext:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.podcast_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:miner_lookup":
                bot.edit_message_text(
                    "🔬 <b>Native Text Miner & Instant FTS5 Collocation Corpus</b>\n\n"
                    "• <b>Instant FTS5 Lookup (under 5ms):</b> Send 1–3 words to query the SQLite FTS5 Corpus & your Vault.\n"
                    "• <b>Native Text Miner:</b> Paste any article, dialogue, or quote to extract 3-Part Lexical Frames and decode its pragmatic subtext:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.miner_lookup_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:drills":
                bot.edit_message_text(
                    f"🎯 <b>100% Generative Active Output & Prosody Gym (Level {user['level']})</b>\n\n"
                    "Every workout below is dynamically generated on the spot by Gemini 2.5 to match your CEFR level, Vault, and recurring weaknesses:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.drills_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:vault":
                vault_items = db.get_user_vault(uid, limit=10)
                stats = db.get_user_stats(uid)
                preview = "\n".join([
                    f"{i}. <b>{html.escape(v['chunk'])}</b> {html.escape(v['register_tag'])} (🌊 {v['stealth_done']}/{v['stealth_target']})"
                    for i, v in enumerate(vault_items, 1)
                ]) if vault_items else "<i>Your vault is currently empty. Tap [⭐ Save 1/2/3] under any reply to collect frames!</i>"

                bot.edit_message_text(
                    f"📓 <b>My 3-Part Lexical Vault & Exports</b>\n"
                    f"Total Saved: <code>{stats['total_vault']}</code> | Due SRS: <code>{stats['due_srs']}</code> | Contextually Acquired: <code>{stats['stealth_mastered']}</code>\n\n"
                    f"<b>Recently Saved Frames:</b>\n{preview}",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.vault_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:settings":
                stats = db.get_user_stats(uid)
                bot.edit_message_text(
                    f"⚙️ <b>Lounge Calibration & Telemetry</b>\n\n"
                    f"• Active Level: <code>{user['level']}</code> (All AI scenarios & drills adapt to this level)\n"
                    f"• Preferred Register: <code>{REGISTERS_MAP.get(user['preferred_register'], '[🎙️ Articulate]')}</code>\n"
                    f"• Saved Frames: <code>{stats['total_vault']}</code> | Stealth Mastered: <code>{stats['stealth_mastered']}</code>\n"
                    f"• Active Weaknesses: <code>{stats['active_errors']}</code> | Mastered Fixes: <code>{stats['mastered_errors']}</code>\n"
                    f"• Today's Turns: <code>{user['daily_count']} / {user['daily_limit']}</code>\n\n"
                    f"<i>Select your CEFR level below (C1/C2 locks 100% English Immersion):</i>",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:admin" and uid == ADMIN_ID:
                bot.edit_message_text(
                    "🛡️ <b>Founder Admin & 24/7 Relay Control Panel</b>\n\n"
                    "Generate friend invite codes, manage your whitelist, trigger an immediate pinned DB backup, or upload any <code>.db</code> file directly to chat to restore:",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.admin_menu()
                )
                bot.answer_callback_query(call.id)

            # --- 3. Live Generative Topic / Scenario Spark ---
            elif data == "action:spark_topic":
                bot.answer_callback_query(call.id, f"🎲 Generating fresh {user['level']} scenario...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    f"🎲 <b>Inventing a fresh, unscripted NYC scenario calibrated to Level <code>{user['level']}</code>...</b>"
                )
                spark_html = ai_engine.generate_studio_spark(user)
                bot.edit_message_text(
                    spark_html,
                    call.message.chat.id, wait_msg.message_id,
                    reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"))
                )

            # --- 4. Mode Switching ---
            elif data.startswith("mode:"):
                new_mode = data.split(":", 1)[1]
                db.update_user(uid, current_mode=new_mode)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"✅ Studio Activated: {new_mode}")

                # Modes that need direct instructions vs generative studio sparks
                if new_mode in ("fast_lookup", "chunk_miner", "podcast_custom_topic", "admin_add_user", "admin_del_user"):
                    static_prompts = {
                        "fast_lookup": "⚡ <b>Instant SQLite FTS5 Collocation Lookup Activated!</b>\nSend 1 to 3 words now for an instant (under 5ms) search across the FTS5 Corpus and your Vault.",
                        "chunk_miner": "🔬 <b>Native Text & Subtext Miner Activated!</b>\nPaste any English paragraph, dialogue, or article to extract 3-Part Lexical Frames and unpack its subtext.",
                        "podcast_custom_topic": "✍️ <b>Custom Podcast Producer:</b>\nType any topic, dilemma, or scenario you want <b>Andrew & Ava</b> to debate on-air while weaving in your Vault chunks:",
                        "admin_add_user": "➕ Send the numeric <b>Telegram ID</b> of the friend you want to whitelist:",
                        "admin_del_user": "➖ Send the numeric <b>Telegram ID</b> you want to remove from the whitelist:"
                    }
                    bot.send_message(
                        call.message.chat.id,
                        static_prompts[new_mode],
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                else:
                    # Dynamically generate a fresh level-matched scenario/prompt right away!
                    wait_msg = bot.send_message(
                        call.message.chat.id,
                        f"🗽 <b>Entering Studio & generating a live Level-<code>{user['level']}</code> opening prompt...</b>"
                    )
                    spark_html = ai_engine.generate_studio_spark(user, mode=new_mode)
                    bot.edit_message_text(
                        spark_html,
                        call.message.chat.id, wait_msg.message_id,
                        reply_markup=LoungeKeyboards.studio_prompt_bar(new_mode)
                    )

            elif data == "action:cycle_register":
                order = ["articulate", "casual", "witty"]
                cur = user.get("preferred_register", "articulate")
                nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "articulate"
                db.update_user(uid, preferred_register=nxt)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"🎚️ Active Vibe: {REGISTERS_MAP[nxt]}")
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                except Exception:
                    pass

            elif data == "action:clear_chat":
                db.reset_history(uid)
                bot.answer_callback_query(call.id, "🔄 Session conversation memory cleared!", show_alert=True)

            # --- 5. Atomic Save, US Native Audio & FFmpeg Acoustic Mirror ---
            elif data.startswith("save:"):
                _, token_key, idx_str = data.split(":")
                saved_chunk = db.save_atomic_item_to_vault(uid, token_key, int(idx_str))
                if saved_chunk:
                    bot.answer_callback_query(call.id, f"⭐ Saved to Vault & SRS: {saved_chunk}", show_alert=False)
                else:
                    bot.answer_callback_query(call.id, "⚠️ Item expired or already saved.", show_alert=True)

            elif data.startswith("tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("audio_clean_text"):
                    bot.answer_callback_query(call.id, "⚠️ Audio text unavailable.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🎧 Synthesizing US Native Audio...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_us_speech(meta["audio_clean_text"])
                if ogg_path:
                    with open(ogg_path, "rb") as vf:
                        bot.send_voice(
                            call.message.chat.id, vf,
                            caption=f"🇺🇸 <code>{html.escape(meta['audio_clean_text'])}</code>"
                        )
                    LoungeMediaEngine.cleanup_file(ogg_path)

            elif data.startswith("mirror:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("user_voice_file_id"):
                    bot.answer_callback_query(call.id, "⚠️ Acoustic Mirror is only available for voice notes.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🪞 Building FFmpeg Acoustic Mirror (1.0x + 0.85x)...")
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
                                    "🪞 <b>Back-to-Back Acoustic Mirror:</b>\n"
                                    "1️⃣ Your Raw Voice ➔ 🔔 ➔ 2️⃣ Natural US Pace (1.0x) ➔ 🔔 ➔ 3️⃣ Slow-Mo Study Pace (0.85x)\n\n"
                                    f"🎯 <code>{html.escape(meta['audio_clean_text'])}</code>"
                                )
                            )
                        LoungeMediaEngine.cleanup_file(mirror_ogg)
                finally:
                    LoungeMediaEngine.cleanup_file(user_ogg)

            # --- 6. Dual-Host Podcast Studio ---
            elif data == "pod:generate_auto":
                bot.answer_callback_query(call.id, "🎙️ Scripting & mixing NYC Lounge Podcast...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    f"🎙️ <b>NYC Podcast Studio is On Air (Level {user['level']})...</b>\n"
                    "1️⃣ Gemini 2.5 is scripting witty banter between <b>Andrew & Ava</b> weaving in your Vault frames...\n"
                    "2️⃣ Mixing neural voices via <code>FFmpeg</code> (~15s)."
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
                    f"<b>({i}) {html.escape(u.chunk)}</b> {html.escape(u.register_tag)} — {html.escape(u.explanation)}"
                    for i, u in enumerate(ep.key_takeaways, 1)
                ])

                full_caption = (
                    f"🎙️ <b>{html.escape(ep.title)}</b>\n"
                    f"🎯 <b>Target Chunks:</b> <code>{html.escape(', '.join(ep.target_chunks_covered))}</code>\n\n"
                    f"<blockquote expandable>📜 <b>Full Episode Transcript & Takeaways (Tap to Expand):</b>\n\n"
                    f"{script_html}\n\n"
                    f"💎 <b>Key Takeaways:</b>\n{takeaways_html}</blockquote>"
                )

                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            call.message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge (Andrew & Ava)",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    if len(full_caption) > 1024:
                        bot.send_message(
                            call.message.chat.id, full_caption,
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        call.message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                    )
                bot.delete_message(call.message.chat.id, wait_msg.message_id)

            # --- 7. Generative Active Drills & Weakness Profile ---
            elif data.startswith("drill:"):
                drill_type = data.split(":", 1)[1]
                if drill_type == "weakness_list":
                    weaknesses = db.get_weaknesses(uid, limit=6)
                    if not weaknesses:
                        bot.answer_callback_query(call.id, "🎉 Your error profile is completely clean!", show_alert=True)
                        return
                    lines = ["🩺 <b>Your Recurring Weakness & L1 Transfer Profile:</b>\n"]
                    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for w in weaknesses:
                        lines.append(
                            f"• ❌ <i>{html.escape(w['user_said'])}</i> ➔ ✅ <b>{html.escape(w['native_fix'])}</b> "
                            f"(seen {w['frequency']}x)\n  └ <code>{html.escape(w['category'])}</code> {html.escape(w['why_note'])}"
                        )
                        kb.add(telebot.types.InlineKeyboardButton(f"✅ Mastered: {w['native_fix'][:18]}", callback_data=f"master:{w['id']}"))
                    kb.add(telebot.types.InlineKeyboardButton("🔥 Drill These in Forced Output", callback_data="drill:forced_output"))
                    kb.add(telebot.types.InlineKeyboardButton("🔙 Back to Drills", callback_data="menu:drills"))
                    bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=kb)
                    bot.answer_callback_query(call.id)
                    return

                bot.answer_callback_query(call.id, f"🎲 Generating fresh {user['level']} workout...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    f"🎯 <b>Generating a fresh `{html.escape(drill_type)}` workout calibrated to Level <code>{user['level']}</code>...</b>"
                )
                mode_map = {
                    "forced_output": "weakness_drill",
                    "shadowing": "shadowing_drill",
                    "micro_dictation": "shadowing_drill",
                    "diagnostic_test": "spontaneous_chat"
                }
                db.update_user(uid, current_mode=mode_map.get(drill_type, "spontaneous_chat"))
                drill_data = ai_engine.build_active_drill(user, drill_type)
                bot.edit_message_text(
                    drill_data["text"],
                    call.message.chat.id, wait_msg.message_id,
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
                bot.answer_callback_query(call.id, "🎉 Marked as mastered!")

            # --- 8. In-Bot Spaced Repetition (SM-2 SRS) ---
            elif data == "srs:next":
                card = db.get_due_srs_card(uid)
                if not card:
                    bot.answer_callback_query(call.id, "🎉 All caught up! No SRS cards due right now.", show_alert=True)
                    return
                cloze = card.get("cloze_sentence") or card.get("example_sentence", "").replace(card["chunk"], "______")
                msg_text = (
                    f"🧠 <b>Spaced Repetition Review (SM-2)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎚️ <b>Register:</b> {html.escape(card['register_tag'])}\n"
                    f"🎯 <b>Trigger:</b> {html.escape(card['trigger_context'])}\n\n"
                    f"📝 <b>Complete the missing NYC chunk:</b>\n"
                    f"<i>\"{html.escape(cloze)}\"</i>"
                )
                bot.send_message(call.message.chat.id, msg_text, reply_markup=LoungeKeyboards.srs_reveal_kb(card["id"]))
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:reveal:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if not card:
                    bot.answer_callback_query(call.id, "⚠️ Card not found.")
                    return
                ans_text = (
                    f"🧠 <b>SRS Answer & 3-Part Lexical Frame:</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>{html.escape(card['chunk'])}</b> {html.escape(card['register_tag'])}\n"
                    f"• <b>Meaning:</b> {html.escape(card['explanation'])}\n"
                    f"• 🎯 <b>Trigger:</b> {html.escape(card['trigger_context'])}\n"
                    f"• 🔗 <b>Pattern:</b> <code>{html.escape(card['co_text_pattern'])}</code>\n"
                    f"• 💬 <b>Follow-up:</b> <i>\"{html.escape(card['typical_followup'])}\"</i>\n\n"
                    f"✅ <b>Full Example:</b>\n<i>\"{html.escape(card['example_sentence'])}\"</i>\n\n"
                    f"👇 <b>How effortless was your recall?</b>"
                )
                bot.edit_message_text(ans_text, call.message.chat.id, call.message.message_id, reply_markup=LoungeKeyboards.srs_grade_kb(card_id))
                bot.answer_callback_query(call.id)

            elif data.startswith("srs:grade:"):
                _, _, card_id_str, grade_str = data.split(":")
                db.grade_srs_card(int(card_id_str), int(grade_str))
                bot.answer_callback_query(call.id, "✅ Next review interval scheduled!")
                nxt_card = db.get_due_srs_card(uid)
                if nxt_card:
                    cloze = nxt_card.get("cloze_sentence") or nxt_card.get("example_sentence", "").replace(nxt_card["chunk"], "______")
                    bot.edit_message_text(
                        f"🧠 <b>Next SRS Card:</b>\n"
                        f"🎚️ <b>Register:</b> {html.escape(nxt_card['register_tag'])}\n"
                        f"🎯 <b>Trigger:</b> {html.escape(nxt_card['trigger_context'])}\n\n"
                        f"<i>\"{html.escape(cloze)}\"</i>",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.srs_reveal_kb(nxt_card["id"])
                    )
                else:
                    bot.edit_message_text(
                        "🎉 <b>Awesome job! You've finished all due SRS cards for today.</b>",
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )

            elif data.startswith("srs:tts:"):
                card_id = int(data.split(":")[2])
                card = db.get_srs_card_by_id(card_id)
                if card:
                    bot.answer_callback_query(call.id, "🔊 Synthesizing US pronunciation...")
                    ogg_p = LoungeMediaEngine.synthesize_us_speech(card.get("example_sentence") or card["chunk"])
                    if ogg_p:
                        with open(ogg_p, "rb") as vf:
                            bot.send_voice(call.message.chat.id, vf, caption=f"🇺🇸 <b>{html.escape(card['chunk'])}</b>")
                        LoungeMediaEngine.cleanup_file(ogg_p)

            # --- 9. Vault Exports (PDF & Anki CSV) ---
            elif data == "export:pdf":
                vault_items = db.get_user_vault(uid, limit=250)
                if not vault_items:
                    bot.answer_callback_query(call.id, "⚠️ Your vault is empty.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "📄 Rendering PDF Book via WeasyPrint...")
                stats = db.get_user_stats(uid)
                pdf_p = LoungeMediaEngine.export_vault_pdf(user, vault_items, stats)
                if pdf_p:
                    with open(pdf_p, "rb") as pf:
                        bot.send_document(
                            call.message.chat.id, pf,
                            caption="📄 <b>Musa's NYC Lounge — 3-Part Lexical Frames Vault (PDF)</b>"
                        )
                    LoungeMediaEngine.cleanup_file(pdf_p)

            elif data == "export:anki":
                vault_items = db.get_user_vault(uid, limit=500)
                if not vault_items:
                    bot.answer_callback_query(call.id, "⚠️ Your vault is empty.", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🗂️ Generating Anki CSV deck...")
                csv_p = LoungeMediaEngine.export_vault_anki_csv(vault_items)
                if csv_p:
                    with open(csv_p, "rb") as cf:
                        bot.send_document(
                            call.message.chat.id, cf,
                            caption="🗂️ <b>Anki Flashcard Deck Ready for Import (Delimiter: Semicolon ;)</b>"
                        )
                    LoungeMediaEngine.cleanup_file(csv_p)

            # --- 10. Settings & Founder Admin Actions ---
            elif data.startswith("set:level:"):
                new_lv = data.split(":")[2]
                db.update_user(uid, level=new_lv)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"✅ Level calibrated to {new_lv}")
                bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )

            elif data == "set:toggle_eng":
                cur_eng = bool(user.get("english_only", 1))
                if user.get("level") in ("C1", "C2") and cur_eng:
                    bot.answer_callback_query(call.id, "🔒 100% English Immersion is mandatory for C1/C2!", show_alert=True)
                    return
                db.update_user(uid, english_only=(0 if cur_eng else 1))
                user = db.get_user(uid)
                bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id,
                    reply_markup=LoungeKeyboards.settings_menu(user)
                )
                bot.answer_callback_query(call.id, "✅ Immersion preference updated.")

            elif data == "admin:gen_invite" and uid == ADMIN_ID:
                inv_code = db.create_invite_code(daily_limit=80)
                bot.send_message(
                    call.message.chat.id,
                    f"🎟️ <b>New Friend Invite Code Generated:</b>\n\n"
                    f"<code>{inv_code}</code>\n\n"
                    f"<i>Share this code with your friend. Once they send it to the bot, they are automatically whitelisted (80 turns/day)!</i>",
                    reply_markup=LoungeKeyboards.admin_menu()
                )
                bot.answer_callback_query(call.id, "🎟️ Invite Code Created!")

            elif data == "admin:backup_now" and uid == ADMIN_ID:
                bot.answer_callback_query(call.id, "💾 Sending & pinning SQLite backup...")
                db.backup_to_telegram(bot, caption_note="🛡️ Manual Founder Backup")

            elif data == "admin:list_users" and uid == ADMIN_ID:
                wl = db.list_whitelisted()
                txt = "👥 <b>Whitelisted NYC Lounge Members:</b>\n\n" + "\n".join([
                    f"• <code>{u['user_id']}</code> — {html.escape(u['full_name'] or u['username'] or 'Member')} ({u['level']}) [Today: {u['daily_count']}/{u['daily_limit']}]"
                    for u in wl
                ])
                bot.send_message(call.message.chat.id, txt, reply_markup=LoungeKeyboards.admin_menu())
                bot.answer_callback_query(call.id)

        except Exception as e:
            logger.error(f"Callback error ({data}): {e}")
            bot.answer_callback_query(call.id, "⚠️ Action could not be completed.")

    # =====================================================================
    # 3. Raw Voice Input Handler -> Gemini 2.5 Native Audio
    # =====================================================================
    @bot.message_handler(content_types=["voice", "audio"])
    def handle_voice_input(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"

        if not is_authorized(uid, uname, fname):
            bot.reply_to(message, f"🔒 Private Lounge. Your ID: <code>{uid}</code>")
            return

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "⚠️ Daily interaction quota reached. Catch you tomorrow!")
            return

        user = db.get_user(uid, uname, fname)
        status_msg = bot.reply_to(
            message,
            f"🎧 <b>Listening to your raw voice note & analyzing US Connected Speech (Level {user['level']})...</b>"
        )
        bot.send_chat_action(message.chat.id, "typing")

        try:
            voice_obj = message.voice or message.audio
            file_id = voice_obj.file_id
            file_info = bot.get_file(file_id)
            audio_bytes = bot.download_file(file_info.file_path)

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
                user_voice_file_id=file_id
            )

            formatted_html = format_lounge_turn_html(res, is_voice=True, stealth_count=len(stealth_ids))
            bot.edit_message_text(
                formatted_html,
                message.chat.id,
                status_msg.message_id,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=True
                )
            )
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            bot.edit_message_text(
                f"⚠️ Error analyzing voice note: <code>{html.escape(str(e)[:180])}</code>",
                message.chat.id, status_msg.message_id
            )

    # =====================================================================
    # 4. Text Input Handler, FTS5 Lookup & Special Studios
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
                        f"🎉 <b>Welcome to Musa's NYC Lounge!</b>\nYour invite code was accepted (Quota: {lim}/day).",
                        reply_markup=LoungeKeyboards.main_menu(u_new, is_admin=False)
                    )
                    return
            bot.reply_to(
                message,
                f"🔒 <b>Private Club.</b>\nSend your Invite Code (<code>NYC-XXXXXX</code>) or share your ID with Musa: <code>{uid}</code>"
            )
            return

        user = db.get_user(uid, uname, fname)
        mode = user.get("current_mode", "spontaneous_chat")

        # --- A. Founder Quick Whitelist Management ---
        if uid == ADMIN_ID and mode in ("admin_add_user", "admin_del_user"):
            digits = "".join([c for c in text if c.isdigit()])
            if not digits:
                bot.reply_to(message, "⚠️ Please send a numeric Telegram ID only.")
                return
            target_id = int(digits)
            status_val = 1 if mode == "admin_add_user" else 0
            db.set_whitelist(target_id, status=status_val)
            db.update_user(uid, current_mode="spontaneous_chat")
            action_str = "whitelisted" if status_val == 1 else "removed from whitelist"
            bot.reply_to(
                message,
                f"✅ User <code>{target_id}</code> has been {action_str}. Switched back to NYC Banter mode.",
                reply_markup=LoungeKeyboards.main_menu(db.get_user(uid), is_admin=True)
            )
            return

        # --- B. Zero-Latency SQLite FTS5 Collocation Lookup ---
        if mode == "fast_lookup" and len(text.split()) <= 4:
            hits = db.lookup_collocations(text, user_id=uid, limit=8)
            if hits:
                lines = [f"⚡ <b>Instant FTS5 Corpus & Vault Hits for <code>{html.escape(text)}</code>:</b>\n"]
                for h in hits:
                    lines.append(
                        f"• <b>{html.escape(h['chunk'])}</b> {html.escape(h['register_tag'])} <i>({h['source']})</i>\n"
                        f"  └ 💡 {html.escape(h['explanation'])}\n"
                        f"  └ 🔗 <code>{html.escape(h['co_text_pattern'])}</code>\n"
                        f"  └ 🗽 <i>\"{html.escape(h['example_sentence'])}\"</i>"
                    )
                bot.reply_to(message, "\n".join(lines))
                return

        if not db.check_and_increment_quota(uid):
            bot.reply_to(message, "⚠️ Daily interaction quota reached. Catch you tomorrow!")
            return

        # --- C. Custom Podcast Episode Producer ---
        if mode == "podcast_custom_topic":
            wait_msg = bot.reply_to(
                message,
                f"🎙️ <b>Producing Dual-Host NYC Podcast on:</b> <i>{html.escape(text)}</i>..."
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
                    f"<blockquote expandable>📜 <b>Full Episode Transcript (Tap to Expand):</b>\n\n{script_html}</blockquote>"
                )
                if mp3_path:
                    with open(mp3_path, "rb") as af:
                        bot.send_audio(
                            message.chat.id, af,
                            title=ep.title, performer="Musa's NYC Lounge",
                            caption=full_caption[:1024],
                            reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                        )
                    LoungeMediaEngine.cleanup_file(mp3_path)
                else:
                    bot.send_message(
                        message.chat.id, full_caption,
                        reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False)
                    )
                bot.delete_message(message.chat.id, wait_msg.message_id)
            except Exception as e:
                bot.edit_message_text(f"⚠️ Podcast production error: {e}", message.chat.id, wait_msg.message_id)
            return

        # --- D. Standard Turn or Native Text Miner ---
        status_msg = bot.reply_to(message, f"🗽 <b>Crafting NYC reply & 3-Register shifts (Level {user['level']})...</b>")
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
                audio_clean_text=res.tts_clean_target or res.rewrite_articulate
            )

            formatted_html = format_lounge_turn_html(res, is_voice=False, stealth_count=len(stealth_ids))
            bot.edit_message_text(
                formatted_html,
                message.chat.id,
                status_msg.message_id,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key, len(res.lexical_upgrades), has_user_voice=False
                )
            )
        except Exception as e:
            logger.error(f"Text turn error: {e}")
            bot.edit_message_text(
                f"⚠️ Processing error: <code>{html.escape(str(e)[:180])}</code>",
                message.chat.id, status_msg.message_id
            )


# =====================================================================
# 5. 5.3-Hour GitHub Relay Lifecycle Manager & Auto-Profile Sync
# =====================================================================
def sync_bot_profile_metadata():
    if not bot:
        return
    try:
        bot.set_my_short_description(
            "🗽 Musa's NYC Lounge — 24/7 Spoken American English, Witty Banter, Acoustic Mirror & Dual-Host Podcast Studio."
        )
        bot.set_my_description(
            "🗽 Welcome to Musa's NYC Lounge!\n\n"
            "An authentic Manhattan conversational club designed for C1/C2 Spoken American Mastery.\n\n"
            "✨ 6 Core Innovations:\n"
            "• 🌊 Stealth Input Flood & 2-Layer Collapsible X-Ray\n"
            "• 🎛️ 3 Living Registers ([Articulate], [Casual], [Witty])\n"
            "• 🪞 FFmpeg Acoustic Mirror (Your Voice + 1.0x + 0.85x US)\n"
            "• 🎙️ Dual-Host NYC Podcast Studio (Andrew & Ava)\n"
            "• 💎 3-Part Lexical Frames, FTS5 Corpus & Smart SRS\n\n"
            "Press Start or /guide to step into the Lounge!"
        )
        bot.set_my_commands([
            telebot.types.BotCommand("start", "🗽 Enter Musa's NYC Lounge & Main Menu"),
            telebot.types.BotCommand("guide", "📖 Detailed 6-Chapter Interactive User Guide"),
            telebot.types.BotCommand("lounge", "🎛️ Open Studios & Generative AI Drills"),
            telebot.types.BotCommand("menu", "🏠 Show Quick Dashboard")
        ])
    except Exception as e:
        logger.warning(f"Profile metadata sync note: {e}")


def relay_lifecycle_watchdog():
    last_periodic_backup = time.time()
    while True:
        time.sleep(25)
        elapsed = time.time() - START_TIME

        if time.time() - last_periodic_backup >= PERIODIC_BACKUP_SECONDS and bot:
            db.backup_to_telegram(bot, caption_note="⏱️ Periodic 2-Hour Checkpoint")
            last_periodic_backup = time.time()

        if elapsed >= RELAY_MAX_RUNTIME_SECONDS:
            logger.info("⏳ 5.3-Hour Relay cycle reached. Performing final DB backup and graceful exit...")
            if bot:
                db.backup_to_telegram(bot, caption_note="🏁 End-of-Cycle Relay Handover")
                try:
                    bot.stop_polling()
                except Exception:
                    pass
            os._exit(0)


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is empty! Make sure secrets are passed to `env:` in your GitHub Workflow.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        logger.error("❌ GEMINI_API_KEY is empty! Make sure secrets are passed to `env:` in your GitHub Workflow.")
        sys.exit(1)

    logger.info("🗽 Starting Musa's NYC Lounge (Modular v3.0 — 100% Gemini Generative Edition)...")

    restored = db.restore_from_telegram_pin(bot)
    if not restored:
        logger.info("ℹ️ Starting with fresh or local SQLite database v3.")

    sync_bot_profile_metadata()

    watchdog_thread = threading.Thread(target=relay_lifecycle_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        bot.remove_webhook()
        if ADMIN_ID:
            u_admin = db.get_user(ADMIN_ID, "Admin", "Musa")
            bot.send_message(
                ADMIN_ID,
                "🗽 <b>Musa's NYC Lounge v3.0 (Modular & 100% Generative) is NOW LIVE!</b>\n\n" + build_welcome_banner(u_admin),
                reply_markup=LoungeKeyboards.main_menu(u_admin, is_admin=True)
            )
    except Exception as e:
        logger.warning(f"Startup notify warning: {e}")

    logger.info("✅ Bot is live and polling!")
    bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=False)


if __name__ == "__main__":
    main()
