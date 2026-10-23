# -*- coding: utf-8 -*-
"""
keyboards.py — Clean, Immersive Club Keyboards for Musa's NYC Lounge
Zero technical jargon; supports Host Voice, User Recast Voice, and Voice Mirror.
"""
from typing import Dict, Any
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import REGISTERS_MAP


class LoungeKeyboards:
    @staticmethod
    def main_menu(user: Dict[str, Any], is_admin: bool = False) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        mode = user.get("current_mode", "spontaneous_chat")
        reg_key = user.get("preferred_register", "articulate")
        reg_label = REGISTERS_MAP.get(reg_key, "🎙️ Articulate")

        def _m(target: str, label: str) -> str:
            return f"✅ {label}" if mode == target else label

        # Row 1: Spontaneous Banter & Storytelling Gym
        kb.add(
            InlineKeyboardButton(_m("spontaneous_chat", "🗣️ NYC Banter"), callback_data="mode:spontaneous_chat"),
            InlineKeyboardButton(_m("storytelling_gym", "🎭 Storytelling Gym"), callback_data="mode:storytelling_gym")
        )
        # Row 2: Vibe Shifter & Subtext Decoder
        kb.add(
            InlineKeyboardButton(_m("register_lab", "🎛️ Vibe & Style Shifter"), callback_data="mode:register_lab"),
            InlineKeyboardButton(_m("subtext_decoder", "🕵️‍♂️ Subtext & Nuance"), callback_data="mode:subtext_decoder")
        )
        # Row 3: Situation Gambits & The Radio Lounge
        kb.add(
            InlineKeyboardButton(_m("situation_gambit", "⚡ What to Say When..."), callback_data="mode:situation_gambit"),
            InlineKeyboardButton("🎙️ The Radio Lounge", callback_data="menu:podcast")
        )
        # Row 4: Fresh Conversation / Scenario Starter
        kb.row(
            InlineKeyboardButton("🎲 Spark a Fresh Topic or Scenario", callback_data="action:spark_topic")
        )
        # Row 5: Phrase Finder/Text Miner & Active Workouts
        kb.add(
            InlineKeyboardButton("🔎 Phrase Finder & Miner", callback_data="menu:miner_lookup"),
            InlineKeyboardButton("🎯 Speaking Workouts", callback_data="menu:drills")
        )
        # Row 6: Daily Review & Personal Vault
        kb.add(
            InlineKeyboardButton("🧠 Daily Review", callback_data="srs:next"),
            InlineKeyboardButton("📓 My Vault & Booklet", callback_data="menu:vault")
        )
        # Row 7: Vibe Selector & Preferences
        kb.add(
            InlineKeyboardButton(f"🎚️ Vibe: {reg_label}", callback_data="action:cycle_register"),
            InlineKeyboardButton("⚙️ Preferences & Progress", callback_data="menu:settings")
        )
        # Row 8: Member's Handbook & Fresh Table
        kb.add(
            InlineKeyboardButton("📖 Member's Handbook", callback_data="guide:overview"),
            InlineKeyboardButton("🔄 Fresh Table (Clear Chat)", callback_data="action:clear_chat")
        )
        if is_admin:
            kb.row(InlineKeyboardButton("🛡️ Club Host Controls", callback_data="menu:admin"))
        return kb

    @staticmethod
    def studio_prompt_bar(mode: str) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🎲 Spark Another Topic", callback_data="action:spark_topic"),
            InlineKeyboardButton("🎚️ Switch Vibe", callback_data="action:cycle_register")
        )
        kb.row(InlineKeyboardButton("🏠 Club Main Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def turn_action_bar(
        token_key: str,
        num_upgrades: int = 3,
        has_user_voice: bool = False,
        has_host_voice: bool = True
    ) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)

        audio_btns = []
        if has_host_voice:
            audio_btns.append(InlineKeyboardButton("🎧 Host Voice", callback_data=f"host_tts:{token_key}"))
        audio_btns.append(InlineKeyboardButton("🗣️ My Recast", callback_data=f"tts:{token_key}"))
        if has_user_voice:
            audio_btns.append(InlineKeyboardButton("🪞 Voice Mirror", callback_data=f"mirror:{token_key}"))
        kb.row(*audio_btns)

        save_btns = [
            InlineKeyboardButton(f"⭐ Save ({i})", callback_data=f"save:{token_key}:{i}")
            for i in range(1, min(num_upgrades, 4) + 1)
        ]
        if save_btns:
            kb.row(*save_btns)

        kb.row(
            InlineKeyboardButton("🎲 New Topic", callback_data="action:spark_topic"),
            InlineKeyboardButton("🎚️ Vibe", callback_data="action:cycle_register"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def guide_navigation_kb(active_chapter: str = "overview") -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)

        def _c(key: str, label: str) -> str:
            return f"📍 {label}" if active_chapter == key else label

        kb.add(
            InlineKeyboardButton(_c("overview", "📖 Overview & Controls"), callback_data="guide:overview"),
            InlineKeyboardButton(_c("ch1_xray", "🔬 1. Notes & Voice Mirror"), callback_data="guide:ch1_xray")
        )
        kb.add(
            InlineKeyboardButton(_c("ch2_studios", "🗽 2. Conversation Rooms"), callback_data="guide:ch2_studios"),
            InlineKeyboardButton(_c("ch3_audio_podcast", "🎙️ 3. Accent & Radio Show"), callback_data="guide:ch3_audio_podcast")
        )
        kb.add(
            InlineKeyboardButton(_c("ch4_miner_drills", "🎯 4. Phrase Finder & Workouts"), callback_data="guide:ch4_miner_drills"),
            InlineKeyboardButton(_c("ch5_vault_routine", "🧠 5. Vault & Daily Review"), callback_data="guide:ch5_vault_routine")
        )
        kb.row(InlineKeyboardButton("🏠 Back to Club Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def podcast_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🎧 Record Episode Using My Saved Phrases", callback_data="pod:generate_auto"),
            InlineKeyboardButton("✍️ Record Episode on a Custom Topic", callback_data="mode:podcast_custom_topic"),
            InlineKeyboardButton("🎙️ 'Be the Guest' (Live On-Air Interview)", callback_data="mode:podcast_guest"),
            InlineKeyboardButton("🔙 Back to Club Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def miner_lookup_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🔎 Collocation & Phrase Finder (By Word)", callback_data="mode:fast_lookup"),
            InlineKeyboardButton("🔬 Native Text & Dialogue Miner (Paste Text)", callback_data="mode:chunk_miner"),
            InlineKeyboardButton("🔙 Back to Club Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def drills_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🔥 The 3-Phrase Speaking Challenge", callback_data="drill:forced_output"),
            InlineKeyboardButton("🎙️ American Rhythm & Shadowing Workout", callback_data="drill:shadowing"),
            InlineKeyboardButton("👂 Fast Ear Catcher (Connected Speech)", callback_data="drill:micro_dictation"),
            InlineKeyboardButton("🩺 Habits I'm Polishing", callback_data="drill:weakness_list"),
            InlineKeyboardButton("🧭 Full Spoken Flow & Accent Checkup", callback_data="drill:diagnostic_test"),
            InlineKeyboardButton("🔙 Back to Club Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def srs_reveal_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("👁️ Reveal Phrase & Examples", callback_data=f"srs:reveal:{card_id}"),
            InlineKeyboardButton("🔊 Hear Example", callback_data=f"srs:tts:{card_id}")
        )
        kb.row(InlineKeyboardButton("🏠 Club Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def srs_grade_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(
            InlineKeyboardButton("🔴 Soon (1d)", callback_data=f"srs:grade:{card_id}:1"),
            InlineKeyboardButton("🟡 Good", callback_data=f"srs:grade:{card_id}:3"),
            InlineKeyboardButton("🟢 Effortless", callback_data=f"srs:grade:{card_id}:5")
        )
        kb.row(
            InlineKeyboardButton("🔊 Hear Example", callback_data=f"srs:tts:{card_id}"),
            InlineKeyboardButton("➡️ Next Phrase", callback_data="srs:next"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def vault_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📄 Download Phrasebook (PDF)", callback_data="export:pdf"),
            InlineKeyboardButton("🗂️ Export Flashcards (Anki)", callback_data="export:anki")
        )
        kb.add(
            InlineKeyboardButton("🧠 Practice Due Phrases", callback_data="srs:next"),
            InlineKeyboardButton("🔙 Club Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def settings_menu(user: Dict[str, Any]) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=4)
        cur_lvl = user.get("level", "C1")
        levels = [("B1", "B1"), ("B2", "B2"), ("C1", "C1"), ("C2", "C2")]
        kb.row(*[
            InlineKeyboardButton(f"{'✅ ' if cur_lvl == lv else ''}{label}", callback_data=f"set:level:{lv}")
            for lv, label in levels
        ])
        eng_only = bool(user.get("english_only", 1))
        eng_label = "🔒 100% English Immersion: ON" if eng_only else "🔓 Brief Arabic Notes: ALLOWED"
        kb.row(InlineKeyboardButton(eng_label, callback_data="set:toggle_eng"))
        kb.row(InlineKeyboardButton("🧭 Run Spoken Flow & Accent Checkup", callback_data="drill:diagnostic_test"))
        kb.row(InlineKeyboardButton("🔙 Back to Club Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def admin_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("💾 Save Vault Snapshot Now", callback_data="admin:backup_now"),
            InlineKeyboardButton("🎟️ Create Guest Pass (Invite Code)", callback_data="admin:gen_invite")
        )
        kb.add(
            InlineKeyboardButton("👥 Club Members", callback_data="admin:list_users"),
            InlineKeyboardButton("➕ Add Member by ID", callback_data="mode:admin_add_user")
        )
        kb.add(
            InlineKeyboardButton("➖ Remove Member by ID", callback_data="mode:admin_del_user"),
            InlineKeyboardButton("🔙 Back to Club Menu", callback_data="menu:home")
        )
        return kb
