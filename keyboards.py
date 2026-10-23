# -*- coding: utf-8 -*-
"""
keyboards.py — 100% English Inline Keyboards for Musa's NYC Lounge
Includes Live AI Scenario/Topic Spark button & 6-Chapter Interactive User Guide navigation.
"""
from typing import Dict, Any
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


class LoungeKeyboards:
    @staticmethod
    def main_menu(user: Dict[str, Any], is_admin: bool = False) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        mode = user.get("current_mode", "spontaneous_chat")
        reg = user.get("preferred_register", "articulate")
        lvl = user.get("level", "C1")

        def _m(target: str, label: str) -> str:
            return f"✅ {label}" if mode == target else label

        # Row 1: Spontaneous Banter & Storytelling Gym
        kb.add(
            InlineKeyboardButton(_m("spontaneous_chat", "🗣️ NYC Banter"), callback_data="mode:spontaneous_chat"),
            InlineKeyboardButton(_m("storytelling_gym", "🎭 Storytelling Gym"), callback_data="mode:storytelling_gym")
        )
        # Row 2: Register Shifter Lab & Subtext Decoder
        kb.add(
            InlineKeyboardButton(_m("register_lab", "🎛️ Register Lab"), callback_data="mode:register_lab"),
            InlineKeyboardButton(_m("subtext_decoder", "🕵️‍♂️ Subtext Decoder"), callback_data="mode:subtext_decoder")
        )
        # Row 3: Situation Gambits & Dual-Host Podcast Studio
        kb.add(
            InlineKeyboardButton(_m("situation_gambit", "⚡ Situation Gambits"), callback_data="mode:situation_gambit"),
            InlineKeyboardButton("🎙️ Podcast Studio", callback_data="menu:podcast")
        )
        # Row 4: Live Generative Spark (Generates fresh AI topic/scenario for current mode & level)
        kb.row(
            InlineKeyboardButton(f"🎲 Generate Fresh AI Scenario / Topic ({lvl})", callback_data="action:spark_topic")
        )
        # Row 5: Text Miner/Corpus & Active Drills
        kb.add(
            InlineKeyboardButton("🔬 Text Miner & Corpus", callback_data="menu:miner_lookup"),
            InlineKeyboardButton("🎯 Active & Audio Drills", callback_data="menu:drills")
        )
        # Row 6: Smart SRS & Personal Vault
        kb.add(
            InlineKeyboardButton("🧠 Smart SRS Review", callback_data="srs:next"),
            InlineKeyboardButton("📓 My Vault & Export", callback_data="menu:vault")
        )
        # Row 7: Register Vibe Shifter & Settings
        kb.add(
            InlineKeyboardButton(f"🎚️ Vibe: {reg.capitalize()}", callback_data="action:cycle_register"),
            InlineKeyboardButton(f"⚙️ Level ({lvl}) & Stats", callback_data="menu:settings")
        )
        # Row 8: Detailed Interactive Guide & Session Memory Reset
        kb.add(
            InlineKeyboardButton("📖 Detailed User Guide", callback_data="guide:overview"),
            InlineKeyboardButton("🔄 Reset Memory", callback_data="action:clear_chat")
        )
        if is_admin:
            kb.row(InlineKeyboardButton("🛡️ Founder Admin & Relay Backup", callback_data="menu:admin"))
        return kb

    @staticmethod
    def studio_prompt_bar(mode: str) -> InlineKeyboardMarkup:
        """Attached to newly activated studios or AI-generated sparks so the user can generate endless fresh scenarios."""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🎲 New AI Prompt / Scenario", callback_data="action:spark_topic"),
            InlineKeyboardButton("🎚️ Shift Vibe", callback_data="action:cycle_register")
        )
        kb.row(InlineKeyboardButton("🏠 Back to Lounge Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def turn_action_bar(token_key: str, num_upgrades: int = 3, has_user_voice: bool = False) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)
        audio_btns = [
            InlineKeyboardButton("🔊 US Native Audio", callback_data=f"tts:{token_key}")
        ]
        if has_user_voice:
            audio_btns.append(InlineKeyboardButton("🪞 Acoustic Mirror", callback_data=f"mirror:{token_key}"))
        kb.row(*audio_btns)

        save_btns = [
            InlineKeyboardButton(f"⭐ Save ({i})", callback_data=f"save:{token_key}:{i}")
            for i in range(1, min(num_upgrades, 3) + 1)
        ]
        if save_btns:
            kb.row(*save_btns)

        kb.row(
            InlineKeyboardButton("🎲 New Topic", callback_data="action:spark_topic"),
            InlineKeyboardButton("🎚️ Shift Vibe", callback_data="action:cycle_register"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def guide_navigation_kb(active_chapter: str = "overview") -> InlineKeyboardMarkup:
        """Interactive 6-Chapter Detailed User Guide Keyboard inside Telegram."""
        kb = InlineKeyboardMarkup(row_width=2)

        def _c(key: str, label: str) -> str:
            return f"📍 {label}" if active_chapter == key else label

        kb.add(
            InlineKeyboardButton(_c("overview", "📖 Overview & Registers"), callback_data="guide:overview"),
            InlineKeyboardButton(_c("ch1_xray", "🔬 1. X-Ray & Stealth Flood"), callback_data="guide:ch1_xray")
        )
        kb.add(
            InlineKeyboardButton(_c("ch2_studios", "🗽 2. The 5 NYC Studios"), callback_data="guide:ch2_studios"),
            InlineKeyboardButton(_c("ch3_audio_podcast", "🎙️ 3. Mirror & Podcast"), callback_data="guide:ch3_audio_podcast")
        )
        kb.add(
            InlineKeyboardButton(_c("ch4_miner_drills", "🎯 4. Corpus & AI Drills"), callback_data="guide:ch4_miner_drills"),
            InlineKeyboardButton(_c("ch5_vault_routine", "🧠 5. SRS & Daily Routine"), callback_data="guide:ch5_vault_routine")
        )
        kb.row(InlineKeyboardButton("🏠 Back to Lounge Main Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def podcast_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🎧 Generate AI Episode from My Vault & Level", callback_data="pod:generate_auto"),
            InlineKeyboardButton("✍️ Generate Episode on Any Custom Topic", callback_data="mode:podcast_custom_topic"),
            InlineKeyboardButton("🎙️ 'You Are the Guest' (Live Show Interview)", callback_data="mode:podcast_guest"),
            InlineKeyboardButton("🔙 Back to Lounge Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def miner_lookup_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("⚡ Instant SQLite FTS5 Collocation Lookup", callback_data="mode:fast_lookup"),
            InlineKeyboardButton("🔬 Native Text & Subtext Miner", callback_data="mode:chunk_miner"),
            InlineKeyboardButton("🔙 Back to Lounge Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def drills_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🔥 Generative Forced Output (3 Chunks)", callback_data="drill:forced_output"),
            InlineKeyboardButton("🎙️ Generative US Connected Speech Shadowing", callback_data="drill:shadowing"),
            InlineKeyboardButton("👂 Generative Fast Micro-Dictation Decoder", callback_data="drill:micro_dictation"),
            InlineKeyboardButton("🩺 My Recurring Weakness Profile", callback_data="drill:weakness_list"),
            InlineKeyboardButton("🧭 Adaptive Level Diagnostic Test", callback_data="drill:diagnostic_test"),
            InlineKeyboardButton("🔙 Back to Lounge Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def srs_reveal_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("👁️ Reveal Answer & 3-Part Frame", callback_data=f"srs:reveal:{card_id}"),
            InlineKeyboardButton("🔊 Listen", callback_data=f"srs:tts:{card_id}")
        )
        kb.row(InlineKeyboardButton("🏠 Lounge Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def srs_grade_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(
            InlineKeyboardButton("🔴 Hard (1d)", callback_data=f"srs:grade:{card_id}:1"),
            InlineKeyboardButton("🟡 Good", callback_data=f"srs:grade:{card_id}:3"),
            InlineKeyboardButton("🟢 Easy", callback_data=f"srs:grade:{card_id}:5")
        )
        kb.row(
            InlineKeyboardButton("🔊 Play Example", callback_data=f"srs:tts:{card_id}"),
            InlineKeyboardButton("🏠 Lounge Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def vault_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📄 Export PDF Book", callback_data="export:pdf"),
            InlineKeyboardButton("🗂️ Export Anki CSV Deck", callback_data="export:anki")
        )
        kb.add(
            InlineKeyboardButton("🧠 Start SRS Session", callback_data="srs:next"),
            InlineKeyboardButton("🔙 Lounge Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def settings_menu(user: Dict[str, Any]) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=4)
        cur_lvl = user.get("level", "C1")
        levels = ["B1", "B2", "C1", "C2"]
        kb.row(*[
            InlineKeyboardButton(f"{'✅ ' if cur_lvl == lv else ''}{lv}", callback_data=f"set:level:{lv}")
            for lv in levels
        ])
        eng_only = bool(user.get("english_only", 1))
        eng_label = "🔒 100% English Immersion: ON" if eng_only else "🔓 Brief Arabic Hints: ALLOWED"
        kb.row(InlineKeyboardButton(eng_label, callback_data="set:toggle_eng"))
        kb.row(InlineKeyboardButton("🧭 Run Generative Level Diagnostic", callback_data="drill:diagnostic_test"))
        kb.row(InlineKeyboardButton("🔙 Back to Lounge Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def admin_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("💾 Backup & Pin DB Now", callback_data="admin:backup_now"),
            InlineKeyboardButton("🎟️ Create Friend Invite Code", callback_data="admin:gen_invite")
        )
        kb.add(
            InlineKeyboardButton("👥 Whitelisted Members", callback_data="admin:list_users"),
            InlineKeyboardButton("➕ Add Member by ID", callback_data="mode:admin_add_user")
        )
        kb.add(
            InlineKeyboardButton("➖ Remove Member by ID", callback_data="mode:admin_del_user"),
            InlineKeyboardButton("🔙 Back to Lounge Menu", callback_data="menu:home")
        )
        return kb
