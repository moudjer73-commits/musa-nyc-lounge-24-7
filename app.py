import html
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
# -*- coding: utf-8 -*-
"""
app.py — Main Club Host Engine for Musa's NYC Lounge
Features:
- Full Trio & Individual Host Experience (Marcus, Chloe, Jake & Trio Table)
- Long-Form (3+ Min) Duo & Trio Podcast Studio with [MM:SS] Noticing Timestamps
- Active Productive Uptake Celebration (Swain's Output Hypothesis) & 'Take 2' Retry Loop
- 1-to-1 Calibrated Voice Mirror with Loudness Normalization
- Graceful SIGTERM/SIGINT Backup Handler for Zero-Loss GitHub Actions Relay
"""
import os
import re
import sys
import time
import uuid
import html
_orig_html_escape = getattr(html, '_orig_html_escape', html.escape)
html._orig_html_escape = _orig_html_escape
html.escape = lambda s, quote=False: _orig_html_escape(str(s if s is not None else ''), quote=quote)
import signal
import logging
import threading
from typing import Dict, Any, List

import telebot

import json
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
    PERSONAS,
    CAST_PRESETS,
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
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=16) if BOT_TOKEN else None


# =====================================================================
# 1. Safe HTML & Clean Display Helpers
# =====================================================================
def sanitize_ai_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    escaped = html.escape(str(raw_text), quote=False)
    allowed_tags = ("b", "i", "u", "s", "code")
    for tag in allowed_tags:
        open_pat = re.compile(rf"&lt;{tag}&gt;", flags=re.IGNORECASE)
        close_pat = re.compile(rf"&lt;/{tag}&gt;", flags=re.IGNORECASE)
        if len(open_pat.findall(escaped)) == len(close_pat.findall(escaped)) and len(open_pat.findall(escaped)) > 0:
            escaped = open_pat.sub(f"<{tag}>", escaped)
            escaped = close_pat.sub(f"</{tag}>", escaped)
    return escaped


def safe_edit_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    if len(text) <= 4050:
        try:
            return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return None
            return bot.send_message(chat_id, text, reply_markup=reply_markup)
    split_pos = text.rfind("\n💎", 0, 3900)
    if split_pos == -1:
        split_pos = text.rfind("\n", 0, 3900)
    if split_pos == -1:
        split_pos = 3900
    part1 = text[:split_pos].strip()
    part2 = text[split_pos:].strip()
    try:
        bot.edit_message_text(part1, chat_id, message_id)
    except Exception:
        bot.send_message(chat_id, part1)
    return bot.send_message(chat_id, part2, reply_markup=reply_markup)


def is_authorized(user_id: int, username: str = "", full_name: str = "") -> bool:
    u = db.get_user(user_id, username, full_name)
    if user_id == ADMIN_ID or ADMIN_ID == 0:
        return True
    return bool(u.get("is_whitelisted", 0))


def _persona_badge(persona_key: str) -> str:
    preset = CAST_PRESETS.get((persona_key or "trio").lower(), CAST_PRESETS["trio"])
    return preset["label"]


def build_welcome_banner(user: Dict[str, Any]) -> str:
    stats = db.get_user_stats(user["user_id"])
    mode_names = {
        "spontaneous_chat": "🗣️ NYC Spontaneous Banter",
        "devils_advocate": "🥊 Devil's Advocate Sparring",
        "subtext_decoder": "🕵️‍♂️ Subtext & Nuance",
        "situation_gambit": "⚡ What to Say When...",
        "podcast_guest": "🎤 Live Radio Interview",
        "podcast_custom_trio": "✍️ Custom Trio Podcast",
        "podcast_custom_duo": "✍️ Custom Duo Podcast",
    }
    raw_mode = user.get("current_mode", "spontaneous_chat")
    cur_mode = mode_names.get(raw_mode, "🗣️ NYC Spontaneous Banter")
    reg_badge = REGISTERS_MAP.get(user.get("preferred_register", "articulate"), "🎙️ Articulate")
    host_badge = _persona_badge(user.get("active_persona", "trio"))
    return (
        f"🗽 <b>Musa's NYC Lounge</b>\n"
        f"<i>Private C1–C2 Spoken American English Club & Podcast Studio</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Member:</b> {html.escape(user.get('full_name') or 'Friend')} | 🎚️ <b>Vibe:</b> {reg_badge}\n"
        f"👥 <b>Active Hosts:</b> {host_badge}\n"
        f"🛋️ <b>Current Room:</b> {cur_mode}\n"
        f"📓 <b>Saved Expressions in Vault:</b> <code>{stats['total_vault']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Drop a voice note, message, or article anytime—or choose your hosts, room, Vibe (5 styles), or Podcast Lounge below:</i>"
    )


def get_active_recast(res: LoungeTurnResponse, pref_reg: str) -> str:
    if pref_reg == "casual":
        return res.rewrite_casual or res.tts_clean_target or res.rewrite_articulate
    if pref_reg == "witty":
        return res.rewrite_witty or res.tts_clean_target or res.rewrite_articulate
    return res.rewrite_articulate or res.tts_clean_target or res.rewrite_casual


def format_lounge_turn_html(
    res: LoungeTurnResponse,
    is_voice: bool = False,
    stealth_count: int = 0,
    active_uptakes: List[str] = None,
    active_reg: str = "articulate"
) -> str:
    def _e(val: Any) -> str:
        return html.escape(str(val if val is not None else ""), quote=False)

    LRM = "\u200e"
    top_section = ""
    if active_uptakes:
        up_badges = ", ".join([f"<code>{_e(u)}</code>" for u in active_uptakes])
        top_section += f"🔥 <b>Active Mastery! You naturally used from your Coaching Targets:</b> {up_badges}\n\n"

    if is_voice and res.transcription:
        top_section += f"🎙️ <i>\"{_e(res.transcription)}\"</i>\n\n"

    resp_p = (getattr(res, "responding_persona", "marcus") or "marcus").lower()
    p_icon = {"marcus": "🧔‍♂️", "chloe": "👩🏼‍💼", "jake": "🧢"}.get(resp_p, "🗽")
    reply_clean = sanitize_ai_html(res.lounge_reply)
    if any(tag in reply_clean for tag in ("Marcus:", "Chloe:", "Jake:")):
        top_section += f"{reply_clean}\n\n"
    else:
        top_section += f"{p_icon} {reply_clean}\n\n"

    if getattr(res, "is_fallback", False):
        return top_section.strip()

    vibe_label = REGISTERS_MAP.get(active_reg, "🎙️ Articulate")
    natural_flow = res.rewrite_articulate or res.tts_clean_target or ""
    punchy_cut = res.rewrite_casual or ""

    mirror_lines = [
        f"🪞 <b>5-Second Native Mirror ({vibe_label}):</b>",
        f"• <b>Natural Flow:</b> <code>{_e(natural_flow)}</code>",
    ]
    if punchy_cut and punchy_cut.strip() != natural_flow.strip():
        mirror_lines.append(f"• <b>Punchy Cut:</b> <code>{_e(punchy_cut)}</code>")

    if is_voice and getattr(res, "acoustic_prosody_xray", ""):
        mirror_lines.append(f"• 🎧 <b>Voice & Rhythm Check:</b> {_e(res.acoustic_prosody_xray)}")

    diag_status = getattr(res, "diagnostics_status", "checked_no_corrections")
    if res.diagnostics:
        d0 = res.diagnostics[0]
        label_0 = "Priority Fix" if len(res.diagnostics) > 1 else "Quick Fix"
        mirror_lines.append(
            f"• ⚡ <b>{label_0}:</b> <s>{_e(d0.user_said)}</s> ➔ ✅ <b>{_e(d0.native_fix)}</b> "
            f"<i>({_e(d0.why_note)})</i>"
        )
        if len(res.diagnostics) > 1:
            mirror_lines.append("• 🔍 <b>Other Fixes in Your Sentence:</b>")
            for extra_d in res.diagnostics[1:4]:
                mirror_lines.append(
                    f"  └ <s>{_e(extra_d.user_said)}</s> ➔ ✅ <b>{_e(extra_d.native_fix)}</b> "
                    f"<i>({_e(extra_d.why_note)})</i>"
                )
    elif diag_status == "not_assessed":
        mirror_lines.append(
            "• ℹ️ <b>Language Check:</b> <i>Scenario/topic prompt — direct coaching mode.</i>"
        )
    else:
        mirror_lines.append(
            "• ✨ <b>Quick Fix:</b> Checked — your original phrasing is already natural American English."
        )

    if res.lexical_upgrades:
        mirror_lines.append("\n💎 <b>Steal These Expressions (Tap ⭐ Save 1/2/3):</b>")
        for idx, up in enumerate(res.lexical_upgrades[:3], start=1):
            b2_foil_str = getattr(up, "b2_foil", "") or getattr(up, "b2_equivalent", "")
            foil_part = f" (instead of: <i>{_e(b2_foil_str)}</i>)" if b2_foil_str else ""
            ar_eq = getattr(up, "arabic_equivalent", "").strip()
            expl_str = getattr(up, "explanation", "").strip()
            if ar_eq and expl_str and ar_eq not in expl_str:
                note_txt = f"{ar_eq}{LRM} — {LRM}{expl_str}"
            else:
                note_txt = ar_eq or expl_str
            expl_part = f"\n{LRM}  └ 💡 {_e(note_txt)}" if note_txt else ""
            mirror_lines.append(
                f"{LRM}<b>({idx})</b> <code>{_e(up.chunk)}</code>{foil_part}{expl_part}\n"
                f"{LRM}  └ 🗽 <i>\"{_e(up.example)}\"</i>"
            )

    mirror_block = "\n".join(mirror_lines)
    return f"{top_section}<blockquote expandable>{mirror_block}</blockquote>"


def format_devils_advocate_html(d_res: Any, active_reg: str = "articulate") -> str:
    def _e(val: Any) -> str:
        return html.escape(str(val if val is not None else ""), quote=False)

    vibe_label = REGISTERS_MAP.get(active_reg, "🎙️ Articulate")
    lines = [
        f"😈 <b>Devil’s Advocate — Intellectual Counter-Challenge ({vibe_label})</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🗣️ <b>Your Premise:</b> <i>\"{_e(d_res.learner_input_text)}\"</i>\n",
    ]
    if d_res.show_diagnostic_mirror and d_res.diagnostic_mirror is not None:
        dm = d_res.diagnostic_mirror
        lines.extend([
            f"🪞 <b>Diagnostic Mirror (<code>{_e(dm.primary_issue_label)}</code>):</b>",
            f"• {_e(dm.explanation)}",
        ])
        if dm.revised_version:
            lines.append(f"• <b>Revised:</b> <code>\"{_e(dm.revised_version)}\"</code>\n")
    else:
        lines.append(
            f"🪞 <b>Diagnostic Mirror:</b> <code>Suppressed (learner_diagnosis={_e(d_res.learner_diagnosis)} | show_diagnostic_mirror=False)</code>\n"
        )

    lines.append("🔥 <b>Counter-Arguments (Challenging Underlying Assumptions)</b>")
    persona_icons = {
        "marcus": "🧔‍♂️ <b>Marcus (Challenging the Causal Assumption):</b>",
        "chloe": "👩🏼‍💼 <b>Chloe (Exposing Second-Order Trade-Offs):</b>",
        "jake": "🧢 <b>Jake (Counter-Angle):</b>",
    }
    for t in (d_res.host_turns or []):
        icon = persona_icons.get(t.speaker, f"🎙️ <b>{_e(t.speaker)}:</b>")
        lines.extend([
            f"\n{icon} (<code>{_e(t.turn_id)}</code>)",
            f"\"{_e(t.text)}\"",
        ])

    if d_res.lexical_items:
        lines.append("\n💎 <b>Verified Target Expressions (<code>LoungeTurnResponse</code>)</b>")
        for idx, it in enumerate(d_res.lexical_items, start=1):
            if it.source_type == "related_extension":
                src_line = f"📍 <b>Source (<code>related_extension</code>):</b> <i>{_e(it.usage_status_label)}</i>"
            else:
                src_line = (
                    f"💬 <b>In the Dialogue (<code>{_e(it.source_ref_id)}</code> | speaker=<code>{_e(it.source_speaker)}</code>):</b> \"{_e(it.full_sentence_citation)}\""
                )
            lines.extend([
                f"\n<b>({idx}) 🧩 Base Pattern: <code>{_e(it.expression)}</code></b>",
                f"  └ {src_line}",
                f"  └ 📖 <b>Core Meaning:</b> {_e(it.core_lexical_meaning)}",
                f"  └ 🎯 <b>Pragmatic Function:</b> {_e(it.contextual_function)}",
                f"  └ 🏷️ <b>Register & Constraints:</b> {_e(it.register_and_usage)}",
                f"  └ 🇸🇦 <b>Arabic:</b> {_e(it.arabic_equivalent)}",
            ])
            if it.contrastive_note:
                lines.append(f"  └ 🔄 <b>Contrastive Note:</b> {_e(it.contrastive_note)}")

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Pydantic v2 Log:</b> <code>PASSED (LoungeTurnResponse | mode={_e(d_res.active_mode)} [submode=devils_advocate] | "
        f"diagnosis={_e(d_res.learner_diagnosis)} | mirror={_e(d_res.show_diagnostic_mirror)} | "
        f"{len(d_res.host_turns)} host_turns | {len(d_res.lexical_items)} citations verified)</code>"
    )
    return "\n".join(lines)


def format_situation_gambit_html(g_res: Any, active_reg: str = "articulate") -> str:
    from validation_layer import count_spoken_words
    def _e(val: Any) -> str:
        return html.escape(str(val if val is not None else ""), quote=False)

    vibe_label = REGISTERS_MAP.get(active_reg, "🎙️ Articulate")

    if hasattr(g_res, "strategy_playbook") and g_res.strategy_playbook is not None:
        pb = g_res.strategy_playbook
        src_tag = getattr(g_res, "_gen_source", "LIVE_GEMINI")
        lines = [
            f"⚡ <b>Conversation Strategy Playbook ({vibe_label})</b>",
            f"🎯 <b>Goal:</b> {_e(pb.situation_objective)}",
            f"🔒 <b>Constraint:</b> {_e(pb.relationship_constraints)}",
        ]
        persona_icons = {"marcus": "🧔‍♂️ <b>Marcus:</b>", "chloe": "👩🏼‍💼 <b>Chloe:</b>", "jake": "🧢 <b>Jake:</b>"}
        for t in (g_res.host_turns or []):
            lines.append(f"{persona_icons.get(t.speaker, '🎙️')} {_e(t.text)}")

        lines.append("\n🗣️ <b>Strategic Spoken Options (12–22 Words)</b>")
        num_badges = {"option_1": "1️⃣", "option_2": "2️⃣", "option_3": "3️⃣"}
        word_counts = []
        for opt in pb.strategic_options:
            wc = count_spoken_words(opt.spoken_line)
            word_counts.append(f"{opt.option_id}={wc}w")
            badge = num_badges.get(opt.option_id, "•")
            lines.extend([
                f"\n{badge} <b>{_e(opt.archetype)} (<code>{_e(opt.option_id)}</code> — {wc}w):</b>",
                f"<code>\"{_e(opt.spoken_line)}\"</code>",
                f"• 🇸🇦 {_e(opt.arabic_equivalent)}",
                f"• <b>Explicit:</b> {_e(opt.makes_explicit)} | <b>Implicit:</b> {_e(opt.leaves_implicit)}",
                f"• <b>Risk:</b> {_e(opt.remaining_risk)} | <b>Best fit:</b> {_e(opt.best_fit_when)}",
            ])

        lines.append("\n🔄 <b>Follow-Up Branches</b>")
        for idx, br in enumerate(pb.follow_up_branches, start=1):
            lines.extend([
                f"• <b>{idx}. {_e(br.reaction_type)}:</b> \"{_e(br.their_likely_reply)}\"",
                f"  ↳ <b>You:</b> <code>\"{_e(br.your_follow_up_line)}\"</code> (<i>{_e(br.tactical_note)}</i>)",
            ])

        lines.append(f"• 🛑 <b>When to pause:</b> {_e(pb.when_to_let_it_go)}")

        if g_res.lexical_items:
            lines.append("\n💎 <b>Verified Target Expressions</b>")
            for idx, it in enumerate(g_res.lexical_items, start=1):
                if it.source_type == "related_extension":
                    src_line = f"📍 <code>related_extension</code>: <i>{_e(it.usage_status_label)}</i>"
                else:
                    src_line = f"📍 <code>{_e(it.source_type)} ({_e(it.source_ref_id)})</code>: \"{_e(it.full_sentence_citation)}\""
                lines.extend([
                    f"<b>({idx}) <code>{_e(it.expression)}</code></b> — {_e(it.core_lexical_meaning)}",
                    f"  └ {src_line}",
                    f"  └ 🎯 {_e(it.contextual_function)} | 🏷️ {_e(it.register_and_usage)}",
                    f"  └ 🇸🇦 {_e(it.arabic_equivalent)}" + (f" | 🔄 {_e(it.contrastive_note)}" if it.contrastive_note else ""),
                ])

        lines.append(
            f"\n✅ <b>Pydantic v2 Log:</b> <code>PASSED ({src_tag} | mode={_e(g_res.active_mode)} | "
            f"mirror={_e(g_res.show_diagnostic_mirror)} | {', '.join(word_counts)} | {len(g_res.lexical_items)} citations verified)</code>"
        )
        return "\n".join(lines)

    return f"⚡ <b>What to Say When...</b>\n{_e(getattr(g_res, 'best_line', ''))}"


def send_srs_card_prompt(chat_id: int, user_id: int, card: Dict[str, Any], edit_msg_id: int = 0):
    """Puts user in active review mode so they can either reveal OR send a live voice note/sentence."""
    db.update_user(user_id, current_mode="srs_active_practice")
    db.update_session_meta(user_id, active_srs_card_id=card["id"])

    cloze = card.get("cloze_sentence") or "______"
    b2_foil = card.get("b2_foil", "")
    foil_hint = f"🔄 <b>Instead of flat B2:</b> <i>\"{html.escape(b2_foil)}\"</i>\n" if b2_foil else ""

    msg_text = (
        f"🧠 <b>Daily Phrase Practice (Active Recall)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎚️ <b>Vibe:</b> {html.escape(card['register_tag'])}\n"
        f"🎯 <b>When to reach for it:</b> {html.escape(card['trigger_context'])}\n"
        f"{foil_hint}"
        f"🔗 <b>Colligation Hint:</b> <code>{html.escape(card['co_text_pattern'])}</code>\n\n"
        f"📝 <b>Complete the sentence:</b>\n"
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


def run_and_send_podcast_episode(
    chat_id: int,
    user: Dict[str, Any],
    format_mode: str = "trio",
    duo_pair: str = "marcus_chloe",
    custom_topic: str = "",
    wait_msg_id: int = 0
):
    """Generates and sends a 3+ minute Duo or Trio NotebookLM-style episode with [MM:SS] timestamps."""
    uid = user["user_id"]
    bot.send_chat_action(chat_id, "record_voice")

    ep = ai_engine.generate_podcast_episode(
        user,
        custom_topic=custom_topic,
        format_mode=format_mode,
        duo_pair=duo_pair
    )
    mp3_path = LoungeMediaEngine.produce_podcast(ep, format_mode=format_mode)

    token_key = uuid.uuid4().hex[:10]
    upgrades_dicts = [u.model_dump() for u in ep.key_takeaways]
    db.store_atomic_batch(uid, token_key, upgrades_dicts, audio_clean_text=ep.title)

    speaker_badges = {
        "marcus": "🧔‍♂️ Marcus",
        "male_host": "🧔‍♂️ Marcus",
        "chloe": "👩🏼‍💼 Chloe",
        "female_host": "👩🏼‍💼 Chloe",
        "jake": "🧢 Jake"
    }

    turn_timestamps = getattr(ep, "turn_timestamps", []) or []
    chunk_timestamps = getattr(ep, "chunk_timestamps", {}) or {}
    duration_label = getattr(ep, "duration_label", "03:10")

    # Format Noticing Hypothesis Timestamp Index
    ts_badges = []
    for ch in ep.target_chunks_covered:
        ts_val = chunk_timestamps.get(ch, "")
        if ts_val:
            ts_badges.append(f"⏱️ <code>[{ts_val}]</code> <b>{html.escape(ch)}</b>")
        else:
            ts_badges.append(f"💎 <b>{html.escape(ch)}</b>")
    noticing_bar = " • ".join(ts_badges) if ts_badges else "<code>Spoken American Collocations</code>"

    script_lines_html = []
    for idx, line in enumerate(ep.script):
        sp_label = speaker_badges.get((line.speaker or "marcus").lower(), "🧔‍♂️ Marcus")
        ts_prefix = f"<code>[{turn_timestamps[idx]}]</code> " if idx < len(turn_timestamps) else ""
        script_lines_html.append(f"{ts_prefix}<b>{sp_label}:</b> {html.escape(line.text)}")

    script_html = "\n\n".join(script_lines_html)
    takeaways_html = "\n".join([
        f"<b>({i}) {html.escape(u.chunk)}</b> ({html.escape(u.register_tag)}) — {html.escape(u.explanation)}"
        for i, u in enumerate(ep.key_takeaways, 1)
    ])

    cast_title = (
        "Marcus, Chloe & Jake" if format_mode == "trio"
        else ("Chloe & Jake" if duo_pair == "chloe_jake" else "Marcus & Chloe")
    )

    short_caption = (
        f"🎙️ <b>{html.escape(ep.title)}</b>\n"
        f"👥 <b>Cast:</b> {cast_title} | ⏳ <b>Length:</b> <code>{duration_label}</code>\n"
        f"🎯 <b>Listen For:</b> {noticing_bar}"
    )

    full_transcript_msg = (
        f"📜 <b>Show Transcript & Timestamped Notes: {html.escape(ep.title)}</b>\n"
        f"🎯 {noticing_bar}\n\n"
        f"<blockquote expandable>{script_html[:4050]}\n\n"
        f"✨ <b>Featured Expressions (Tap ⭐ Save 1–4):</b>\n{takeaways_html}</blockquote>"
    )

    kb = LoungeKeyboards.turn_action_bar(token_key, len(ep.key_takeaways), False, False, persona_key="marcus")

    if mp3_path:
        with open(mp3_path, "rb") as af:
            bot.send_audio(
                chat_id, af,
                title=ep.title,
                performer=f"Musa's NYC Lounge ({cast_title})",
                caption=short_caption[:1020],
                reply_markup=kb
            )
        bot.send_message(chat_id, full_transcript_msg, reply_markup=kb)
        LoungeMediaEngine.cleanup_file(mp3_path)
    else:
        bot.send_message(chat_id, full_transcript_msg, reply_markup=kb)

    if wait_msg_id:
        try:
            bot.delete_message(chat_id, wait_msg_id)
        except Exception:
            pass


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

    @bot.message_handler(commands=["trio", "podcast"])
    def cmd_quick_podcast(message):
        uid = message.from_user.id
        if not is_authorized(uid, message.from_user.username or "", message.from_user.first_name or ""):
            return
        bot.send_message(
            message.chat.id,
            "🎙️ <b>The NYC Podcast Lounge (NotebookLM Style — 3+ Min Episodes)</b>\n\n"
            "Choose a <b>Trio Roundtable (Marcus, Chloe & Jake)</b> or a <b>Duo Deep-Dive</b> below:",
            reply_markup=LoungeKeyboards.podcast_menu()
        )

    @bot.message_handler(commands=["spark", "devil", "audit", "review", "hosts", "lookup", "drills", "vault", "pdf", "fresh", "new", "chat"])
    def cmd_shortcuts(message):
        uid = message.from_user.id
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or "Friend"
        if not is_authorized(uid, uname, fname):
            return
        user = db.get_user(uid, uname, fname)
        cmd = (message.text or "").split()[0].lstrip("/").lower()

        if cmd == "devil":
            db.update_user(uid, current_mode="devils_advocate")
            user = db.get_user(uid)
            spark_html = ai_engine.generate_studio_spark(user, mode="devils_advocate")
            bot.send_message(
                message.chat.id, spark_html,
                reply_markup=LoungeKeyboards.studio_prompt_bar("devils_advocate", user.get("active_persona", "trio"))
            )
        elif cmd == "spark":
            spark_html = ai_engine.generate_studio_spark(user)
            bot.send_message(
                message.chat.id, spark_html,
                reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"), user.get("active_persona", "trio"))
            )
        elif cmd == "audit":
            wait_m = bot.send_message(message.chat.id, "📊 <b>The Trio is compiling your Spoken Fluency Audit...</b>")
            audit_html = ai_engine.generate_fluency_audit(user)
            safe_edit_text(
                message.chat.id, wait_m.message_id, audit_html,
                reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"), user.get("active_persona", "trio"))
            )
        elif cmd == "review":
            card = db.get_due_srs_card(uid)
            if not card:
                bot.send_message(message.chat.id, "🎉 <b>You're all caught up on your Daily Review for today!</b>")
            else:
                send_srs_card_prompt(message.chat.id, uid, card)
        elif cmd == "hosts":
            cur_cast = user.get("active_persona", "trio")
            bot.send_message(
                message.chat.id,
                "👥 <b>Choose Your Conversation Hosts (1, 2, or 3 Characters)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Current Table Cast:</b> {_persona_badge(cur_cast)}\n\n"
                "Pick Trio (3), Duo (2), or Solo (1) below:",
                reply_markup=LoungeKeyboards.cast_selector_menu(cur_cast)
            )
        elif cmd == "lookup":
            db.update_user(uid, current_mode="fast_lookup")
            bot.send_message(
                message.chat.id,
                "🔎 <b>Collocation & Phrase Finder</b>\n\n"
                "Send any word, verb, or short phrase to explore its natural spoken American collocations and B2-to-C2 upgrades!"
            )
        elif cmd == "drills":
            bot.send_message(
                message.chat.id,
                "🎯 <b>Speaking, Rhythm & Ear Workouts</b>\n\nChoose a live workout below:",
                reply_markup=LoungeKeyboards.drills_menu()
            )
        elif cmd == "vault":
            vault_items = db.get_user_vault(uid, limit=10)
            stats = db.get_user_stats(uid)
            preview = "\n".join([
                f"{i}. <b>{html.escape(v['chunk'])}</b> ({html.escape(v['register_tag'])})"
                for i, v in enumerate(vault_items, 1)
            ]) if vault_items else "<i>Your Vault is empty right now.</i>"
            bot.send_message(
                message.chat.id,
                f"📓 <b>My Personal Phrasebook & Vault</b>\n"
                f"Saved: <code>{stats['total_vault']}</code> | 🔥 Produced: <code>{stats.get('productive_mastered', 0)}</code>\n\n{preview}",
                reply_markup=LoungeKeyboards.vault_menu()
            )
        elif cmd == "pdf":
            vault_items = db.get_user_vault(uid, limit=250)
            if not vault_items:
                bot.send_message(message.chat.id, "📓 Your Vault is empty—save a few phrases first!")
            else:
                stats = db.get_user_stats(uid)
                pdf_p = LoungeMediaEngine.export_vault_pdf(user, vault_items, stats)
                if pdf_p:
                    with open(pdf_p, "rb") as pf:
                        bot.send_document(message.chat.id, pf, caption="📄 <b>Musa's NYC Lounge — Personal Phrasebook</b>")
                    LoungeMediaEngine.cleanup_file(pdf_p)
        elif cmd in ("fresh", "new"):
            db.reset_history(uid)
            bot.send_message(message.chat.id, "🔄 <b>Fresh table! Conversation memory cleared.</b>")
        elif cmd == "chat":
            db.update_user(uid, current_mode="spontaneous_chat")
            user = db.get_user(uid)
            bot.send_message(
                message.chat.id,
                build_welcome_banner(user),
                reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
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

            # --- 1B. Cast Selector Menu & 1/2/3 Host Switching ---
            if data == "menu:cast_selector":
                cur_cast = user.get("active_persona", "trio")
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "👥 <b>Choose Your Conversation Hosts (1, 2, or 3 Characters)</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>Current Table Cast:</b> {_persona_badge(cur_cast)}\n\n"
                    "• <b>🗽 Trio (3 Hosts):</b> Marcus, Chloe & Jake all react, banter with each other, and reply together.\n"
                    "• <b>👥 Duo (2 Hosts):</b> Pick any 2-host pair for a focused back-and-forth dialogue.\n"
                    "• <b>👤 Solo (1 Host):</b> Chat 1-on-1 with Marcus, Chloe, or Jake:",
                    reply_markup=LoungeKeyboards.cast_selector_menu(cur_cast)
                )
                bot.answer_callback_query(call.id)
                return

            if data.startswith("persona:"):
                new_p = data.split(":", 1)[1]
                if new_p in CAST_PRESETS:
                    reg_map = {"marcus": "articulate", "chloe": "witty", "jake": "casual"}
                    upd_kwargs: Dict[str, Any] = {"active_persona": new_p}
                    if new_p in reg_map:
                        upd_kwargs["preferred_register"] = reg_map[new_p]
                    db.update_user(uid, **upd_kwargs)
                    user = db.get_user(uid)
                    bot.answer_callback_query(call.id, f"✅ {_persona_badge(new_p)}")
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        build_welcome_banner(user),
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )
                return

            if data == "action:cycle_persona":
                order = list(CAST_PRESETS.keys())
                cur_p = user.get("active_persona", "trio")
                nxt_p = order[(order.index(cur_p) + 1) % len(order)] if cur_p in order else "trio"
                db.update_user(uid, active_persona=nxt_p)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"👥 {_persona_badge(nxt_p)}")
                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id, call.message.message_id,
                        reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"), nxt_p)
                    )
                except Exception:
                    pass
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
                    "🎙️ <b>The NYC Podcast Lounge (NotebookLM Style — 3+ Min Episodes)</b>\n\n"
                    "Experience rapid-fire micro-turns, spatial stereo panning, and timestamped phrase callouts with <b>🧔‍♂️ Marcus, 👩🏼‍💼 Chloe, and 🧢 Jake</b>.\n\n"
                    "Choose a <b>Trio Roundtable (3 Hosts)</b> or a <b>Duo Deep-Dive (2 Hosts)</b>:",
                    reply_markup=LoungeKeyboards.podcast_menu()
                )
                bot.answer_callback_query(call.id)

            elif data == "menu:miner_lookup":
                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    "🔎 <b>Phrase Finder & Native Text Miner</b>\n\n"
                    "• <b>Collocation & Phrase Finder:</b> Type any word or verb to explore its most natural spoken American collocations and B2-to-C2 upgrades.\n"
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
                    + (f" 🔥<code>{v['productive_count']}x</code>" if int(v.get("productive_count", 0)) > 0 else "")
                    for i, v in enumerate(vault_items, 1)
                ]) if vault_items else "<i>Your Vault is empty right now. Tap [⭐ Save 1/2/3] under any reply to collect expressions!</i>"

                safe_edit_text(
                    call.message.chat.id, call.message.message_id,
                    f"📓 <b>My Personal Phrasebook & Vault</b>\n"
                    f"Saved: <code>{stats['total_vault']}</code> | 🔥 Actively Produced: <code>{stats.get('productive_mastered', 0)}</code> | Due Today: <code>{stats['due_srs']}</code>\n\n"
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
                    f"• Active Hosts: <code>{_persona_badge(user.get('active_persona', 'trio'))}</code>\n"
                    f"• Default Vibe: <code>{REGISTERS_MAP.get(user['preferred_register'], '🎙️ Articulate')}</code>\n"
                    f"• Collected Expressions: <code>{stats['total_vault']}</code> | Actively Produced: <code>{stats.get('productive_mastered', 0)}</code>\n"
                    f"• Habits Being Polished: <code>{stats['active_errors']}</code> | Polished & Mastered: <code>{stats['mastered_errors']}</code>\n\n"
                    f"<i>Adjust your coaching depth or active hosts below:</i>",
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
                    "📊 <b>Marcus, Chloe & Jake are reviewing your recent turns, Vault phrases & speaking habits...</b>"
                )
                audit_html = ai_engine.generate_fluency_audit(user)
                safe_edit_text(
                    call.message.chat.id, wait_msg.message_id,
                    audit_html,
                    reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"), user.get("active_persona", "trio"))
                )

            # --- 3B. Spark a Fresh Topic / Scenario ---
            elif data == "action:spark_topic":
                if user.get("current_mode") in ("srs_active_practice", "fast_lookup", "chunk_miner"):
                    db.update_user(uid, current_mode="spontaneous_chat")
                    user = db.get_user(uid)
                bot.answer_callback_query(call.id, "🎲 Setting up a fresh topic...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    "🎲 <b>Pulling together a fresh New York scenario for you...</b>"
                )
                spark_html = ai_engine.generate_studio_spark(user)
                safe_edit_text(
                    call.message.chat.id, wait_msg.message_id,
                    spark_html,
                    reply_markup=LoungeKeyboards.studio_prompt_bar(user.get("current_mode", "spontaneous_chat"), user.get("active_persona", "trio"))
                )

            # --- 4. Room Switching ---
            elif data.startswith("mode:"):
                new_mode = data.split(":", 1)[1]
                allowed_modes = {
                    "spontaneous_chat", "devils_advocate", "storytelling_gym", "register_lab",
                    "subtext_decoder", "situation_gambit", "podcast_guest", "fast_lookup",
                    "chunk_miner", "podcast_custom_trio", "podcast_custom_duo",
                    "podcast_custom_topic", "admin_add_user", "admin_del_user"
                }
                if new_mode not in allowed_modes:
                    bot.answer_callback_query(call.id, "⚠️ Unknown room.", show_alert=False)
                    return
                if new_mode in ("admin_add_user", "admin_del_user") and uid != ADMIN_ID:
                    bot.answer_callback_query(call.id, "🔒 Admin access required.", show_alert=True)
                    return
                db.update_user(uid, current_mode=new_mode)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, "✅ Room Ready!")

                if new_mode in ("fast_lookup", "chunk_miner", "podcast_custom_trio", "podcast_custom_duo", "podcast_custom_topic", "admin_add_user", "admin_del_user"):
                    static_prompts = {
                        "fast_lookup": (
                            "🔎 <b>Collocation & Phrase Finder</b>\n\n"
                            "Send any word, verb, or short phrase (e.g., <i>slack, point, wind up, doubt</i>) and I'll pull up its most natural spoken American collocations, patterns, and B2-to-C2 upgrades!"
                        ),
                        "chunk_miner": (
                            "🔬 <b>Native Text & Dialogue Miner</b>\n\n"
                            "Paste any English paragraph, dialogue, or article here to extract its best spoken expressions and unpack its subtext."
                        ),
                        "podcast_custom_trio": (
                            "✍️ <b>Custom Trio Roundtable (3+ Min: Marcus, Chloe & Jake)</b>\n\n"
                            "Type any topic, dilemma, cultural question, or scenario you want <b>all 3 hosts</b> to debate in depth while weaving in your Vault phrases:"
                        ),
                        "podcast_custom_duo": (
                            "✍️ <b>Custom Duo Deep-Dive (3+ Min: Marcus & Chloe)</b>\n\n"
                            "Type any topic, dilemma, or idea you want <b>Marcus & Chloe</b> to unpack in a 2-host NotebookLM-style deep dive:"
                        ),
                        "podcast_custom_topic": (
                            "✍️ <b>Custom Radio Show</b>\n\n"
                            "Type any topic or dilemma you want the hosts to discuss on-air:"
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
                            "🥊 <b>Devil's Advocate (Led by 🧔‍♂️ Marcus & 👩🏼‍💼 Chloe)</b>\n\n"
                            "Share any opinion, hot take, or decision (voice or text)—or tap <code>🎲 Spark Another Topic</code> for a controversial thesis. We'll challenge your premise so you can practice diplomatic pushback, hedging, and holding the floor!"
                        ),
                        "spontaneous_chat": (
                            "🗣️ <b>NYC Spontaneous Banter</b>\n\n"
                            "Drop a voice note or text on anything—or tap <code>🎲 Spark Another Topic</code> below for a fresh conversation starter with the Trio!"
                        ),
                        "storytelling_gym": (
                            "🎭 <b>Storytelling & Wit Gym (Led by 🧢 Jake)</b>\n\n"
                            "Tell a short story (real or made up) via voice or text to practice dramatic present tense & punchlines—or tap <code>🎲 Spark Another Topic</code> for a story prompt!"
                        ),
                        "register_lab": (
                            "🎛️ <b>Vibe & Style Shifter (Marcus vs. Jake vs. Chloe)</b>\n\n"
                            "Send any sentence or thought to see how it naturally shifts across <b>🎙️ Articulate</b>, <b>☕ Casual</b>, and <b>🎭 Witty</b> vibes!"
                        ),
                        "subtext_decoder": (
                            "🕵️‍♂️ <b>Subtext & Nuance Decoder (Led by 👩🏼‍💼 Chloe)</b>\n\n"
                            "Send something someone said to you (or tap <code>🎲 Spark Another Topic</code> for a subtext puzzle) to decode what's really being said between the lines!"
                        ),
                        "situation_gambit": (
                            "⚡ <b>What to Say When...</b>\n\n"
                            "Describe any social or workplace moment (in English or Arabic) to get the exact instinctive phrases Americans reach for in that second!"
                        ),
                        "podcast_guest": (
                            "🎤 <b>Live Radio Interview ('Be the Guest' with the Trio)</b>\n\n"
                            "Tap <code>🎲 Spark Another Topic</code> for the Trio's opening question, or send a voice note to jump right on-air!"
                        )
                    }
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        studio_banners.get(new_mode, "✅ Room ready!"),
                        reply_markup=LoungeKeyboards.studio_prompt_bar(new_mode, user.get("active_persona", "trio"))
                    )

            elif data.startswith("action:cycle_register"):
                order = ["articulate", "casual", "witty", "executive", "diplomatic"]
                cur = user.get("preferred_register", "articulate")
                nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "articulate"
                db.update_user(uid, preferred_register=nxt)
                user = db.get_user(uid)
                bot.answer_callback_query(call.id, f"🎚️ All club hosts locked to: {REGISTERS_MAP[nxt]}", show_alert=False)
                if data == "action:cycle_register":
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        build_welcome_banner(user),
                        reply_markup=LoungeKeyboards.main_menu(user, is_admin=(uid == ADMIN_ID))
                    )

            elif data == "action:clear_chat":
                db.reset_history(uid)
                bot.answer_callback_query(call.id, "🔄 Fresh table! Conversation memory cleared.", show_alert=True)

            # --- 5. Saving, Take 2 Retry & Audio Controls ---
            elif data.startswith("save:"):
                _, token_key, idx_str = data.split(":")
                saved_chunk = db.save_atomic_item_to_vault(uid, token_key, int(idx_str))
                if saved_chunk:
                    bot.answer_callback_query(call.id, f"⭐ Saved to your Vault: {saved_chunk}", show_alert=False)
                else:
                    bot.answer_callback_query(call.id, "Already in your Vault!", show_alert=False)

            elif data.startswith("take2:"):
                cb_uid = call.from_user.id
                tok = data.split(":", 1)[1] if ":" in data else ""
                msg_raw_text = getattr(call.message, "text", "") or getattr(call.message, "caption", "") or ""
                prompt_html, _ = build_contextual_take2_prompt(cb_uid, tok, msg_raw_text)
                bot.answer_callback_query(call.id, "🎙️ Take 2 activated! Reply with a voice note or text.")
                kb_take2 = InlineKeyboardMarkup(row_width=2)
                kb_take2.add(
                    InlineKeyboardButton("🗣️ Hear My Recast", callback_data=f"tts:{tok}"),
                    InlineKeyboardButton("🎧 Trio Voice", callback_data=f"host_tts:{tok}")
                )
                bot.send_message(call.message.chat.id, prompt_html, parse_mode="HTML", reply_markup=kb_take2)
                return

            elif data.startswith("host_tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                raw_host_payload = (meta.get("host_reply_text") or "") if meta else ""
                p_key = (meta.get("active_persona") or "trio") if meta else "trio"
                if not raw_host_payload:
                    bot.answer_callback_query(call.id, "Audio unavailable for this message.", show_alert=True)
                    return
                chat_turns_list = []
                fallback_plain = raw_host_payload
                if raw_host_payload.startswith("[{"):
                    try:
                        chat_turns_list = json.loads(raw_host_payload)
                        fallback_plain = " ".join([t.get("text", "") for t in chat_turns_list])
                    except Exception:
                        chat_turns_list = []

                cast_badge = _persona_badge(p_key)
                bot.answer_callback_query(call.id, f"🎧 Recording {cast_badge}...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_chat_turns(
                    chat_turns_list, fallback_text=fallback_plain, fallback_persona=p_key
                )
                if ogg_path:
                    with open(ogg_path, "rb") as vf:
                        bot.send_voice(
                            call.message.chat.id, vf,
                            caption=f"🗽 <i>{html.escape(cast_badge)}</i>"
                        )
                    LoungeMediaEngine.cleanup_file(ogg_path)

            elif data.startswith("tts:"):
                token_key = data.split(":", 1)[1]
                meta = db.get_atomic_meta(token_key)
                if not meta or not meta.get("audio_clean_text"):
                    bot.answer_callback_query(call.id, "Audio unavailable for this message.", show_alert=True)
                    return
                p_key = meta.get("active_persona") or "marcus"
                bot.answer_callback_query(call.id, "🗣️ Recording your American recast...")
                bot.send_chat_action(call.message.chat.id, "record_voice")
                ogg_path = LoungeMediaEngine.synthesize_us_speech(meta["audio_clean_text"], persona_key=p_key)
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
                bot.answer_callback_query(call.id, "🪞 Preparing loudness-leveled Voice Mirror (1.0x + 0.85x)...")
                bot.send_chat_action(call.message.chat.id, "record_voice")

                # Use 1-to-1 calibrated mirror sentence so acoustic comparison matches the user's words
                mirror_target = meta.get("user_transcript_text") or meta.get("audio_clean_text") or ""
                p_key = meta.get("active_persona") or "marcus"
                user_ogg = os.path.join(TEMP_DIR, f"u_{token_key}.ogg")
                try:
                    f_info = bot.get_file(meta["user_voice_file_id"])
                    raw_voice = bot.download_file(f_info.file_path)
                    with open(user_ogg, "wb") as f:
                        f.write(raw_voice)
                    mirror_ogg = LoungeMediaEngine.build_acoustic_mirror(
                        user_ogg, mirror_target, persona_key=p_key
                    )
                    if mirror_ogg:
                        with open(mirror_ogg, "rb") as mf:
                            bot.send_voice(
                                call.message.chat.id, mf,
                                caption=(
                                    "🪞 <b>1-to-1 Calibrated Voice Mirror:</b>\n"
                                    "1️⃣ Your Voice (Leveled) ➔ 🔔 ➔ 2️⃣ Native Pace (1.0x) ➔ 🔔 ➔ 3️⃣ Slow Study (0.85x)\n\n"
                                    f"🎯 <code>{html.escape(mirror_target)}</code>"
                                )
                            )
                        LoungeMediaEngine.cleanup_file(mirror_ogg)
                finally:
                    LoungeMediaEngine.cleanup_file(user_ogg)

            # --- 6. The Podcast Lounge (Trio Roundtable & Duo Deep-Dives) ---
            elif data in ("pod:trio_vault", "pod:duo_mc", "pod:duo_cj", "pod:generate_auto"):
                if data == "pod:duo_mc":
                    fmt_mode, d_pair = "duo", "marcus_chloe"
                    cast_str = "🧔‍♂️ Marcus & 👩🏼‍💼 Chloe (Duo Deep-Dive)"
                elif data == "pod:duo_cj":
                    fmt_mode, d_pair = "duo", "chloe_jake"
                    cast_str = "👩🏼‍💼 Chloe & 🧢 Jake (Duo Street Banter)"
                else:
                    fmt_mode, d_pair = "trio", "all"
                    cast_str = "🧔‍♂️ Marcus, 👩🏼‍💼 Chloe & 🧢 Jake (Trio Roundtable)"

                bot.answer_callback_query(call.id, "🎙️ Stepping up to the studio mics...")
                wait_msg = bot.send_message(
                    call.message.chat.id,
                    f"🎙️ <b>Recording a 3+ minute episode with {cast_str}...</b>\n"
                    "<i>Weaving in your Vault phrases, trimming silence & panning stereo mics (~18–25s).</i>"
                )
                run_and_send_podcast_episode(
                    call.message.chat.id,
                    user,
                    format_mode=fmt_mode,
                    duo_pair=d_pair,
                    custom_topic="",
                    wait_msg_id=wait_msg.message_id
                )

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
                    reply_markup=LoungeKeyboards.studio_prompt_bar(mode_map.get(drill_type, "spontaneous_chat"), user.get("active_persona", "trio"))
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
                weaknesses = db.get_weaknesses(uid, limit=6)
                if not weaknesses:
                    safe_edit_text(
                        call.message.chat.id, call.message.message_id,
                        "🎉 <b>All clean! You've polished and checked off every habit on your list.</b>",
                        reply_markup=LoungeKeyboards.drills_menu()
                    )
                else:
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
                b2_foil = card.get("b2_foil", "")
                foil_line = f"  • 🔄 <b>Upgrade:</b> <s>{html.escape(b2_foil)}</s> ➔ <b>{html.escape(card['chunk'])}</b>\n" if b2_foil else ""
                ans_text = (
                    f"🧠 <b>Phrase Details & Context:</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>{html.escape(card['chunk'])}</b> ({html.escape(card['register_tag'])})\n"
                    f"{foil_line}"
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
                            caption="📄 <b>Musa's NYC Lounge — Personal C1/C2 Phrasebook</b>"
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
    # 3. Voice Note Handler (Live Conversation, Active Uptake & SRS Review)
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
                    db.update_user(uid, current_mode="spontaneous_chat")
                    if ev.used_naturally:
                        db.detect_and_record_active_uptake(uid, card["chunk"])
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

            # --- Reset static text-only submodes if user sends a voice note ---
            if mode in ("fast_lookup", "chunk_miner", "podcast_custom_topic", "admin_add_user", "admin_del_user"):
                db.update_user(uid, current_mode="spontaneous_chat")
                user = db.get_user(uid)

            # --- Standard Conversational & Phonetic Coaching ---
            status_msg = bot.reply_to(
                message,
                "🎧 <b>Listening closely to your phrasing, vowels, nuclear stress & linking...</b>"
            )
            bot.send_chat_action(message.chat.id, "typing")

            res, stealth_ids = ai_engine.process_lounge_turn(
                user=user,
                user_text="",
                audio_bytes=audio_bytes,
                audio_mime="audio/ogg"
            )
            if mode in ("weakness_drill", "shadowing_drill"):
                db.update_user(uid, current_mode="spontaneous_chat")

            active_uptakes = db.detect_and_record_active_uptake(uid, res.transcription)

            db.append_history(uid, "user", f"[Voice]: {res.transcription}")
            db.append_history(uid, "assistant", res.lounge_reply)
            db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
            if stealth_ids:
                db.increment_stealth_done(stealth_ids)

            token_key = uuid.uuid4().hex[:10]
            upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
            active_cast_key = user.get("active_persona", "trio")
            mirror_sent = res.mirror_clean_target or res.tts_clean_target or res.rewrite_articulate
            turns_json = json.dumps([ct.model_dump() for ct in getattr(res, "chat_turns", [])], ensure_ascii=False) if getattr(res, "chat_turns", None) else res.lounge_reply

            db.store_atomic_batch(
                user_id=uid,
                token_key=token_key,
                items=upgrades_dicts,
                audio_clean_text=get_active_recast(res, user.get('preferred_register', 'articulate')),
                user_transcript_text=mirror_sent,
                host_reply_text=turns_json,
                active_persona=active_cast_key,
                user_voice_file_id=file_id
            )

            formatted_html = format_lounge_turn_html(
                res, is_voice=True, stealth_count=len(stealth_ids), active_uptakes=active_uptakes, active_reg=user.get('preferred_register', 'articulate')
            )
            safe_edit_text(
                message.chat.id,
                status_msg.message_id,
                formatted_html,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key,
                    len(res.lexical_upgrades),
                    has_user_voice=True,
                    has_host_voice=True,
                    persona_key=active_cast_key
                )
            )
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            bot.reply_to(message, "☕ Give me just a second—could you send that voice note once more?")

    # =====================================================================
    # 4. Text Input Handler (Banter, Collocation Explorer, Custom Podcasts)
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
        # [CLAUDE V2 LIVE HOOK]
        if handle_claude_v2_turn(bot, ai, db, message.chat.id, message.from_user.id, user, text, message.message_id):
            return
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
                    db.update_user(uid, current_mode="spontaneous_chat")
                    if ev.used_naturally:
                        db.detect_and_record_active_uptake(uid, text)
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

        # --- C. Collocation & Phrase Finder ---
        if mode == "__disabled_fast_lookup__" and len(text.split()) <= 10:
            local_hits = db.lookup_collocations(text, user_id=uid, limit=4)
            wait_msg = bot.reply_to(message, f"🔎 <b>Exploring natural American collocations for <code>{html.escape(text)}</code>...</b>")
            try:
                explorer = ai_engine.explore_collocations_live(user, text)
                token_key = uuid.uuid4().hex[:10]
                frames_dicts = [f.model_dump() for f in explorer.collocation_frames]
                first_ex = ' ... '.join([f.example for f in explorer.collocation_frames[:4]]) if explorer.collocation_frames else text
                db.store_atomic_batch(uid, token_key, frames_dicts, audio_clean_text=first_ex)

                lines = [
                    f"🔎 <b>Spoken Collocation Map: <code>{html.escape(explorer.headword)}</code></b>",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"💡 <b>Lexical Priming & Feel:</b> {html.escape(explorer.semantic_note)}\n"
                ]
                if local_hits:
                    lines.append("📓 <b>Already in Your Club Phrasebook:</b>")
                    for h in local_hits[:2]:
                        lines.append(f"• <b>{html.escape(h['chunk'])}</b> ({html.escape(h['register_tag'])}) — <i>\"{html.escape(h['example_sentence'])}\"</i>")
                    lines.append("")

                lines.append("💎 <b>Top Spoken Collocations (Tap ⭐ Save 1–4 below):</b>")
                for idx, cf in enumerate(explorer.collocation_frames[:4], 1):
                    b2_f = getattr(cf, "b2_foil", "")
                    foil_s = f"  • 🔄 <b>Upgrade from:</b> <s>{html.escape(b2_f)}</s>\n" if b2_f else ""
                    lines.append(
                        f"\n<b>({idx}) {html.escape(cf.chunk)}</b> ({html.escape(cf.register_tag)})\n"
                        f"{foil_s}"
                        f"  • 💡 {html.escape(cf.explanation)}\n"
                        f"  • 🎯 <b>When:</b> {html.escape(cf.trigger)}\n"
                        f"  • 🔗 <b>Pattern:</b> <code>{html.escape(cf.pattern)}</code>\n"
                        f"  • 🗽 <i>\"{html.escape(cf.example)}\"</i>"
                    )

                safe_edit_text(
                    message.chat.id, wait_msg.message_id,
                    "\n".join(lines),
                    reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(explorer.collocation_frames[:4]), has_user_voice=False, has_host_voice=False, has_recast_voice=True, recast_label='🔊 Hear Examples')
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

        # --- D. Custom Long-Form Podcast Producer (Trio or Duo) ---
        if mode in ("podcast_custom_trio", "podcast_custom_duo", "podcast_custom_topic"):
            fmt_mode = "duo" if mode == "podcast_custom_duo" else "trio"
            cast_desc = "Marcus & Chloe (Duo)" if fmt_mode == "duo" else "Marcus, Chloe & Jake (Trio)"
            wait_msg = bot.reply_to(
                message,
                f"🎙️ <b>{cast_desc} are recording a 3+ minute episode on:</b>\n<i>{html.escape(text)}</i>..."
            )
            try:
                db.update_user(uid, current_mode="spontaneous_chat")
                run_and_send_podcast_episode(
                    message.chat.id,
                    user,
                    format_mode=fmt_mode,
                    duo_pair="marcus_chloe",
                    custom_topic=text,
                    wait_msg_id=wait_msg.message_id
                )
            except Exception as e:
                logger.error(f"Custom podcast error: {e}")
                safe_edit_text(message.chat.id, wait_msg.message_id, "☕ Studio mics were busy for a second—try that topic once more!")
            return

        # --- E. Standard Conversational Turn or Native Text Miner ---
        status_msg = bot.reply_to(message, "🗽 <b>The Lounge is on it...</b>")
        bot.send_chat_action(message.chat.id, "typing")

        try:
            if mode in ("devils_advocate", "subtext", "subtext_decoder") or any(k in text.lower() for k in ("[devil", "devil's advocate:")):
                d_res = ai_engine.process_devils_advocate(user=user, user_text=text)
                db.append_history(uid, "user", f"[Devil's Advocate]: {text}")
                db.append_history(uid, "assistant", d_res.host_commentary)
                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in (d_res.lexical_upgrades or [])]
                active_cast_key = d_res.responding_persona or "marcus"
                host_turns_json = json.dumps(
                    [{"speaker": t.speaker, "pacing": "normal", "text": t.text} for t in (d_res.host_turns or [])],
                    ensure_ascii=False
                )
                db.store_atomic_batch(
                    user_id=uid,
                    token_key=token_key,
                    items=upgrades_dicts,
                    audio_clean_text=d_res.host_commentary,
                    user_transcript_text=text,
                    host_reply_text=host_turns_json,
                    active_persona=active_cast_key
                )
                formatted_html = format_devils_advocate_html(d_res, active_reg=user.get("preferred_register", "articulate"))
                safe_edit_text(
                    message.chat.id,
                    status_msg.message_id,
                    formatted_html,
                    reply_markup=LoungeKeyboards.turn_action_bar(token_key, len(upgrades_dicts), has_user_voice=False, has_host_voice=True, persona_key=active_cast_key, recast_label="🗣️ Hear Debate")
                )
                return
            elif mode == "situation_gambit" or any(k in text.lower() for k in ("how do i push back", "what do i say", "without making it sound like", "without sounding uncooperative")):
                g_res = ai_engine.process_situation_gambit(user=user, user_text=text)
                db.append_history(uid, "user", f"[Situation]: {text}")
                db.append_history(uid, "assistant", g_res.best_line)
                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in (g_res.lexical_upgrades or [])]
                active_cast_key = g_res.responding_persona or "chloe"
                host_turns_json = json.dumps([{"speaker": active_cast_key, "pacing": "normal", "text": f"{g_res.host_commentary} {g_res.best_line}".strip()}], ensure_ascii=False)
                db.store_atomic_batch(
                    user_id=uid,
                    token_key=token_key,
                    items=upgrades_dicts,
                    audio_clean_text=g_res.best_line,
                    user_transcript_text=text,
                    host_reply_text=host_turns_json,
                    active_persona=active_cast_key
                )
                formatted_html = format_situation_gambit_html(g_res, active_reg=user.get("preferred_register", "articulate"))
                safe_edit_text(
                    message.chat.id,
                    status_msg.message_id,
                    formatted_html,
                    reply_markup=LoungeKeyboards.turn_action_bar(
                        token_key,
                        len(upgrades_dicts),
                        has_user_voice=False,
                        has_host_voice=True,
                        persona_key=active_cast_key,
                        recast_label="🗣️ Hear Best Line"
                    )
                )
                return
            elif mode == "__disabled_chunk_miner__":
                res = ai_engine.mine_text_and_subtext(user, text)
                stealth_ids = []
                active_uptakes = []
            else:
                res, stealth_ids = ai_engine.process_lounge_turn(user=user, user_text=text)
                active_cast_key = user.get("active_persona", "trio")
                token_key = uuid.uuid4().hex[:10]
                upgrades_dicts = [u.model_dump() for u in res.lexical_upgrades]
                turns_json = json.dumps([ct.model_dump() for ct in getattr(res, "chat_turns", [])], ensure_ascii=False) if getattr(res, "chat_turns", None) else res.lounge_reply

                # 1. Render HTML BEFORE opening the DB transaction so a formatting error never leaves partial DB state
                with db.atomic_turn_transaction():
                    active_uptakes = db.detect_and_record_active_uptake(uid, text)
                    formatted_html = format_lounge_turn_html(
                        res, is_voice=False, stealth_count=len(stealth_ids), active_uptakes=active_uptakes, active_reg=user.get('preferred_register', 'articulate')
                    )
                    db.append_history(uid, "user", text)
                    db.append_history(uid, "assistant", res.lounge_reply)
                    db.record_diagnostics(uid, [d.model_dump() for d in res.diagnostics])
                    if stealth_ids:
                        db.increment_stealth_done(stealth_ids)
                    db.store_atomic_batch(
                        user_id=uid,
                        token_key=token_key,
                        items=upgrades_dicts,
                        audio_clean_text=get_active_recast(res, user.get('preferred_register', 'articulate')),
                        user_transcript_text=res.mirror_clean_target or text,
                        host_reply_text=turns_json,
                        active_persona=active_cast_key
                    )
            safe_edit_text(
                message.chat.id,
                status_msg.message_id,
                formatted_html,
                reply_markup=LoungeKeyboards.turn_action_bar(
                    token_key,
                    len(res.lexical_upgrades),
                    has_user_voice=False,
                    has_host_voice=True,
                    persona_key=active_cast_key
                )
            )
        except Exception as e:
            logger.error(f"Text turn error: {e}")
            safe_edit_text(
                message.chat.id, status_msg.message_id,
                "☕ Give me just a second—send that thought once more."
            )


# =====================================================================
# 5. Silent Background Lifecycle & Graceful Signal Protection
# =====================================================================
def sync_bot_profile_metadata():
    if not bot:
        return
    short_desc = (
        "🗽 Private C1-C2 Spoken American English Club & NYC Podcast Studio. "
        "Live banter, 5-second native recasts & 5 vibes."
    )
    full_desc = (
        "🗽 Welcome to Musa's NYC Lounge\n"
        "Private C1-C2 Spoken American English Club & Podcast Studio\n\n"
        "• 🗣️ Live NYC Banter: Fast-paced chat & pushback with Marcus, Chloe & Jake (Solo, Duo, or Trio).\n"
        "• 🪞 5-Second Native Mirror: Instant recasts of your exact thought + 1 stealable C1-C2 expression per turn.\n"
        "• 🎚️ 5 Spoken Vibes: Articulate, Casual, Witty, Executive & Diplomatic.\n"
        "• 🎙️ Podcast Lounge: 3+ min Trio & Duo episodes with full transcripts."
    )
    commands = [
        telebot.types.BotCommand("start", "🗽 Main Club Menu & Lounge Table"),
        telebot.types.BotCommand("vibe", "🎚️ Cycle Spoken Vibe (5 Registers)"),
        telebot.types.BotCommand("hosts", "👥 Choose 1 (Solo), 2 (Duo) or 3 (Trio) Hosts"),
        telebot.types.BotCommand("podcast", "🎙️ 3+ Min Trio & Duo Podcast Studio"),
        telebot.types.BotCommand("spark", "🎲 Spark a Fresh Topic or Scenario"),
        telebot.types.BotCommand("devil", "🥊 Devil's Advocate Pushback Sparring"),
        telebot.types.BotCommand("vault", "📓 My Saved Phrases, PDF & Anki Export"),
        telebot.types.BotCommand("guide", "📖 Club Guide & How It Works"),
        telebot.types.BotCommand("fresh", "🔄 Clear Table & Start Fresh Memory")
    ]
    # 1. تحديث الأوامر
    for lang in [None, "en", "ar"]:
        try:
            bot.delete_my_commands(language_code=lang)
            bot.set_my_commands(commands, language_code=lang)
        except Exception as e:
            logger.warning(f"Commands sync warning ({lang}): {e}")

    # 2. مسح أوصاف اللغات الفرعية القديمة أولاً لفك القفل، ثم تعيين الوصف الجديد الموحد
    for lang in ["en", "ar", "fr", "es", "ru", "de"]:
        try:
            bot.set_my_short_description("", language_code=lang)
            bot.set_my_description("", language_code=lang)
        except Exception:
            pass

    for lang in [None, "en", "ar"]:
        try:
            bot.set_my_short_description(short_desc, language_code=lang)
            bot.set_my_description(full_desc, language_code=lang)
            logger.info(f"✅ Bot description & bio successfully updated for lang={lang} (len={len(full_desc)})")
        except Exception as e:
            logger.error(f"❌ Failed to set bot description for lang={lang}: {e}")


def _graceful_shutdown(signum, frame):
    logger.info(f"⏳ Signal {signum} received—saving Vault snapshot before exit...")
    if bot:
        try:
            db.backup_to_telegram(bot, caption_note="Synced Club Vault")
            bot.stop_polling()
        except Exception:
            pass
    os._exit(0)


signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)


def relay_lifecycle_watchdog():
    last_periodic_backup = time.time()
    while True:
        time.sleep(25)
        elapsed = time.time() - START_TIME

        if time.time() - last_periodic_backup >= PERIODIC_BACKUP_SECONDS and bot:
            db.backup_to_telegram(bot, caption_note="Synced Club Vault")
            last_periodic_backup = time.time()

        if elapsed >= RELAY_MAX_RUNTIME_SECONDS:
            logger.info("⏳ Handing over 4.7h club shift seamlessly...")
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

    logger.info("🗽 Starting Musa's NYC Lounge (Marcus, Chloe & Jake)...")

    restored = db.restore_from_telegram_pin(bot)
    sync_bot_profile_metadata()

    watchdog_thread = threading.Thread(target=relay_lifecycle_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        bot.remove_webhook()
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
    while True:
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
        except Exception as poll_err:
            logger.warning(f"Polling loop recovery after error: {poll_err}")
            time.sleep(5)


if __name__ == "__main__":
    main()


def build_contextual_take2_prompt(uid: int, tok: str, msg_text: str = "") -> Tuple[str, Optional[str]]:
    host_question = ""
    natural_flow = ""
    punchy_cut = ""
    chunks: List[str] = []

    try:
        batch = db.get_atomic_batch(uid, tok) if tok else None
        if isinstance(batch, dict):
            natural_flow = (batch.get("audio_clean_text") or "").strip()
            for it in (batch.get("items") or []):
                ch = (it.get("chunk") or "").strip() if isinstance(it, dict) else ""
                if ch and ch not in chunks:
                    chunks.append(ch)
            raw_hosts = str(batch.get("host_reply_text") or "")
            turns_data = json.loads(raw_hosts) if raw_hosts.startswith("[") else []
            if isinstance(turns_data, list) and turns_data:
                last_t = turns_data[-1]
                sp = (last_t.get("speaker") or "jake").capitalize()
                tx = (last_t.get("text") or "").strip()
                if tx:
                    host_question = f"{sp}: \"{tx}\""
    except Exception:
        pass

    if msg_text:
        lines = [ln.strip() for ln in msg_text.splitlines() if ln.strip()]
        if not host_question:
            host_lines = [ln for ln in lines if any(ln.startswith(p) for p in ("🧔‍♂️", "👩🏼‍💼", "🧢", "Marcus:", "Chloe:", "Jake:"))]
            if host_lines:
                host_question = host_lines[-1]
        for ln in lines:
            if "Natural Flow:" in ln and not natural_flow:
                natural_flow = ln.split("Natural Flow:", 1)[1].strip()
            elif "Punchy Cut:" in ln and not punchy_cut:
                punchy_cut = ln.split("Punchy Cut:", 1)[1].strip()
            m_ch = re.match(r"^(?:\u200e)?\([123]\)\s+(.+?)(?:\s+\(instead of:|$)", ln)
            if m_ch and len(chunks) < 3:
                c_val = m_ch.group(1).strip()
                if c_val not in chunks:
                    chunks.append(c_val)

    try:
        meta = db.get_session_meta(uid) if hasattr(db, "get_session_meta") else {}
        if not isinstance(meta, dict):
            meta = {}
        meta["retry_expected"] = True
        meta["take2_active"] = True
        meta["take2_token"] = tok
        meta["take2_host_question"] = host_question
        meta["take2_targets"] = chunks[:3]
        meta["take2_natural_flow"] = natural_flow
        if hasattr(db, "update_session_meta"):
            db.update_session_meta(uid, meta)
    except Exception:
        pass

    esc_q = html.escape(host_question or "Respond to the hosts' last point in your own words.", quote=False)
    esc_nf = html.escape(natural_flow, quote=False)
    esc_pc = html.escape(punchy_cut, quote=False)

    chunks_bullets = ""
    if chunks:
        formatted_chunks = "  •  ".join([f"<code>{html.escape(c, quote=False)}</code>" for c in chunks[:3]])
        chunks_bullets = f"\n🎯 <b>Target Expressions to Weave In (Pick 1 or 2):</b>\n{formatted_chunks}\n"

    recast_block = ""
    if esc_nf:
        recast_block += f"\n🪞 <b>Your Polished Anchor:</b>\n• <i>Natural Flow:</i> <code>{esc_nf}</code>"
    if esc_pc:
        recast_block += f"\n• <i>Punchy Cut:</i> <code>{esc_pc}</code>"

    prompt_html = (
        "🎙️ <b>Take 2 — Active Speaking Challenge (Your Turn!)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Don't just read—step in and deliver your response in your own words.\n\n"
        f"🥊 <b>Current Moment on the Table:</b>\n<i>{esc_q}</i>\n"
        f"{chunks_bullets}"
        f"{recast_block}\n\n"
        "🗣️ <b>Your Speaking Task (Send a Voice Note 🎤 or Text 💬 now):</b>\n"
        "1️⃣ <b>Respond directly to the moment above</b> (give a concrete example, defend your take, or deliver your comeback line).\n"
        "2️⃣ <b>Actively use at least ONE target expression</b> above in your response.\n"
        "3️⃣ Marcus, Chloe, and Jake will react to your move and diagnose your phrasing!"
    )
    return prompt_html, (natural_flow or None)


# === CLAUDE V2 RENDERERS & STATEFUL ROUTER HELPERS ===
import html as _html_mod
def _e(val) -> str:
    return _html_mod.escape(str(val if val is not None else ''))

SPEAKER_BADGES_V2 = {
    "Marcus": "🧔‍♂️ <b>Marcus:</b>",
    "Chloe": "👩🏼‍💼 <b>Chloe:</b>",
    "Jake": "🧢 <b>Jake:</b>",
}

RISK_BADGES_V2 = {
    "low": "🟢 Low Risk",
    "mid": "🟡 Medium Risk",
    "high": "🔴 High Stakes",
}


def format_v2_dialogue_html(dialogue_res) -> str:
    lines = []
    for t in dialogue_res.turns:
        badge = SPEAKER_BADGES_V2.get(t.speaker, f"🗣️ <b>{t.speaker}:</b>")
        lines.append(f"{badge} {_e(t.text)}")
    return "\n\n".join(lines)


def format_v2_coach_html(coach_res) -> str:
    if not coach_res:
        return ""
    lines = []
    if coach_res.mirror and coach_res.mirror.verdict == "fix" and coach_res.mirror.html_render:
        lines.append(coach_res.mirror.html_render)
        lines.append("")
    if coach_res.chunks:
        lines.append("💎 <b>Spoken American Moves:</b>")
        for idx, ch in enumerate(coach_res.chunks, 1):
            lines.append(
                f"({idx}) 🧩 <b>{_e(ch.base_pattern)}</b> <i>[{_e(ch.kind)}]</i> — {_e(ch.ar)}\n"
                f"  └ 💬 <i>\"{_e(ch.expression)}\"</i>\n"
                f"  └ 🎯 <b>Does:</b> {_e(ch.does)}\n"
                f"  └ 🗽 <b>Say it when:</b> {_e(ch.say_it_when)}"
            )
    return "\n".join(lines).strip()


def format_v2_read_the_room_html(rtr, input_text: str) -> str:
    lines = [
        "🎭 <b>Read the Room</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🗣️ <b>Input:</b> \"{_e(input_text)}\"",
        f"📌 <b>Surface / Core Point:</b> {_e(rtr.surface_point)}",
        "",
        "1️⃣ <b>What It Might Mean (Cue → Subtext)</b>",
    ]
    for idx, rd in enumerate(rtr.readings, 1):
        ar_part = f" — <i>{_e(rd.ar)}</i>" if rd.ar else ""
        lines.append(f"• <b>[{idx}] {_e(rd.cue)}</b> → {_e(rd.meaning)}{ar_part}")

    lines.append("\n2️⃣ <b>What You Could Say (Strategic Moves)</b>")
    move_Titles = {
        "ask": "Clarify Without Assuming (Ask)",
        "go_along": "Keep Momentum Smoothly (Go Along)",
        "name_it": "Surface the Subtext / Set Boundary (Name It)",
    }
    for mv in rtr.moves:
        r_badge = RISK_BADGES_V2.get(mv.risk, mv.risk)
        m_title = move_Titles.get(mv.kind, mv.kind)
        lines.append(
            f"• <b>{m_title}</b> ({r_badge})\n"
            f"  💬 <b>Say:</b> \"{_e(mv.line)}\"\n"
            f"  🎯 <b>Fits when:</b> {_e(mv.when_to_use)}"
        )

    lines.append("\n3️⃣ <b>Lounge Takeaway</b>")
    for t in rtr.lounge_take:
        badge = SPEAKER_BADGES_V2.get(t.speaker, f"🗣️ <b>{t.speaker}:</b>")
        lines.append(f"{badge} \"{_e(t.text)}\"")

    if rtr.chunks:
        lines.append("\n💎 <b>Spoken Moves from the Lounge</b>")
        for idx, ch in enumerate(rtr.chunks, 1):
            lines.append(
                f"• 🧩 <b>{_e(ch.base_pattern)}</b> ({_e(ch.ar)}): {_e(ch.does)} — <i>Say it when: {_e(ch.say_it_when)}</i>"
            )

    yt = rtr.your_turn
    c_badge = SPEAKER_BADGES_V2.get(yt.counterpart, f"🗣️ <b>{yt.counterpart}:</b>")
    lines.extend([
        "",
        "4️⃣ <b>Your Turn (Active Roleplay)</b>",
        f"{c_badge} \"{_e(yt.their_line)}\"",
        f"🎯 <b>Goal:</b> {_e(yt.goal)}",
        f"👉 <b>{_e(yt.prompt)}</b> <i>(Reply directly with your spoken line, or send /skip to exit)</i>",
    ])
    return "\n".join(lines)


def format_v2_practice_eval_html(pe, learner_text: str) -> str:
    from validation_layer import Mirror, apply_mirror_guards
    lines = [
        f"👂 <b>How It Lands:</b> {_e(pe.lands_as or '')}"
    ]
    if pe.tweak:
        guarded = apply_mirror_guards(Mirror(verdict="fix", edits=[pe.tweak]), learner_text)
        if guarded.verdict == "fix" and guarded.html_render:
            lines.append(guarded.html_render)
    if pe.reply:
        badge = SPEAKER_BADGES_V2.get(pe.reply.speaker, f"🗣️ <b>{pe.reply.speaker}:</b>")
        lines.append(f"\n{badge} \"{_e(pe.reply.text)}\"")
    return "\n".join(lines)


def handle_claude_v2_turn(bot_inst, ai_inst, db_inst, chat_id: int, user_id: int, user_obj, text: str, reply_to_id: int = None) -> bool:
    try:
        from validation_layer import (
            format_v2_dialogue_html,
            format_v2_coach_html,
            format_v2_read_the_room_html,
            format_v2_practice_eval_html,
        )
        clean_t = (text or "").strip()
        if not clean_t:
            return False

        if clean_t.lower() in ("/skip", "skip", "تخطي"):
            db_inst.clear_pending_practice(user_id)
            bot_inst.send_message(chat_id, "✅ <b>Exited roleplay practice.</b> Back to Lounge Banter!", parse_mode="HTML")
            return True

        if clean_t.startswith("/"):
            return False

        if isinstance(user_obj, dict):
            u_dict = dict(user_obj)
        elif user_obj is not None and hasattr(user_obj, "keys"):
            u_dict = {k: user_obj[k] for k in user_obj.keys()}
        else:
            u_dict = {"user_id": user_id}
        u_dict["user_id"] = user_id
        active_mode = str(u_dict.get("current_mode") or "banter").strip().lower()

        # 1. Check active pending practice (Turn-2 Roleplay Feedback)
        pending = db_inst.get_pending_practice(user_id)
        if pending and active_mode not in ("subtext", "subtext_decoder", "what_to_say", "social_strategy", "read_the_room"):
            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            pe = ai_inst.practice_reply_v2(user=u_dict, learner_reply=clean_t, pending_state=pending)
            if pe.intent == "attempt":
                eval_html = format_v2_practice_eval_html(pe, clean_t)
                bot_inst.send_message(chat_id, eval_html, parse_mode="HTML", reply_to_message_id=reply_to_id)
                return True

        # 2. Read the Room V2 (Unified Subtext & What to Say)
        if active_mode in ("subtext", "subtext_decoder", "what_to_say", "social_strategy", "read_the_room"):
            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            rtr = ai_inst.read_the_room_v2(user=u_dict, user_text=clean_t)
            rtr_html = format_v2_read_the_room_html(rtr, clean_t)
            bot_inst.send_message(chat_id, rtr_html, parse_mode="HTML", reply_to_message_id=reply_to_id)
            try:
                db_inst.update_user_mode(user_id, "spontaneous_chat")
            except Exception:
                pass
            return True

        # 3. Two-Call Lounge Banter & Devil's Advocate
        if active_mode in ("spontaneous_chat", "banter", "nyc_banter", "devils_advocate", "devil", "debate"):
            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            hist_rows = []
            try:
                raw_h = db_inst.get_recent_messages(user_id, limit=6)
                for r in raw_h:
                    r_role = r["role"] if isinstance(r, dict) or hasattr(r, "__getitem__") else "user"
                    r_cont = r["content"] if isinstance(r, dict) or hasattr(r, "__getitem__") else ""
                    role_lbl = "Learner" if r_role == "user" else "Lounge"
                    hist_rows.append(f"{role_lbl}: {str(r_cont)[:160]}")
            except Exception:
                pass

            d_res = ai_inst.generate_dialogue(user=u_dict, user_text=clean_t, mode=active_mode, history_lines=hist_rows)
            msg1_html = format_v2_dialogue_html(d_res)
            sent_msg1 = bot_inst.send_message(chat_id, msg1_html, parse_mode="HTML", reply_to_message_id=reply_to_id)

            try:
                db_inst.add_message(user_id, "user", clean_t)
                db_inst.add_message(user_id, "assistant", "\n".join(f"{t.speaker}: {t.text}" for t in d_res.turns))
            except Exception:
                pass

            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            c_res = ai_inst.coach_turn(user=u_dict, user_text=clean_t, turns=d_res.turns)
            msg2_html = format_v2_coach_html(c_res)
            if msg2_html:
                m1_id = getattr(sent_msg1, "message_id", None) or reply_to_id
                bot_inst.send_message(chat_id, msg2_html, parse_mode="HTML", reply_to_message_id=m1_id)
            return True
    except Exception as e:
        logger.error(f"ClaudeV2 live handler error: {e}", exc_info=True)
    return False

