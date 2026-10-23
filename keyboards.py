# -*- coding: utf-8 -*-
"""
keyboards.py — Interactive Club Keyboards for Musa's NYC Lounge
Includes:
- Full Cast Selector (Choose 1 Solo, 2 Duo, or 3 Trio Hosts anytime)
- Long-Form Duo & Trio Podcast Studio Menu (NotebookLM Style)
- Pushed-Output 'Take 2 (Retry)' Button & Multi-Voice Chat Playback
"""
from typing import Dict, Any
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import REGISTERS_MAP, PERSONAS, CAST_PRESETS


class LoungeKeyboards:
    @staticmethod
    def main_menu(user: Dict[str, Any], is_admin: bool = False) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        mode = user.get("current_mode", "spontaneous_chat")
        reg_key = user.get("preferred_register", "articulate")
        reg_label = REGISTERS_MAP.get(reg_key, "🎙️ Articulate")
        active_cast = user.get("active_persona", "trio")
        cast_short = CAST_PRESETS.get(active_cast, CAST_PRESETS["trio"])["short"]

        def _m(target: str, label: str) -> str:
            return f"✅ {label}" if mode == target else label

        # Top Row: Dedicated Cast Selector Button (Choose 1, 2, or 3 Hosts)
        kb.row(
            InlineKeyboardButton(f"👥 Chat Hosts: {cast_short} (Tap to Change)", callback_data="menu:cast_selector")
        )
        # Quick 1-Tap Switchers for Trio / Duo / Solo
        kb.row(
            InlineKeyboardButton("🗽 Trio (3)" if active_cast != "trio" else "✅ 🗽 Trio (3)", callback_data="persona:trio"),
            InlineKeyboardButton("🧔‍♂️👩🏼‍💼 M&C (2)" if active_cast != "duo_mc" else "✅ M&C (2)", callback_data="persona:duo_mc"),
            InlineKeyboardButton("👩🏼‍💼🧢 C&J (2)" if active_cast != "duo_cj" else "✅ C&J (2)", callback_data="persona:duo_cj")
        )

        # Row 1: Spontaneous Banter & Devil's Advocate Sparring
        kb.add(
            InlineKeyboardButton(_m("spontaneous_chat", "🗣️ NYC Banter"), callback_data="mode:spontaneous_chat"),
            InlineKeyboardButton(_m("devils_advocate", "🥊 Devil's Advocate"), callback_data="mode:devils_advocate")
        )
        # Row 2: Storytelling Gym & Vibe Shifter
        kb.add(
            InlineKeyboardButton(_m("storytelling_gym", "🎭 Storytelling Gym"), callback_data="mode:storytelling_gym"),
            InlineKeyboardButton(_m("register_lab", "🎛️ Vibe & Style Shifter"), callback_data="mode:register_lab")
        )
        # Row 3: Subtext Decoder & Situation Gambits
        kb.add(
            InlineKeyboardButton(_m("subtext_decoder", "🕵️‍♂️ Subtext & Nuance"), callback_data="mode:subtext_decoder"),
            InlineKeyboardButton(_m("situation_gambit", "⚡ What to Say When..."), callback_data="mode:situation_gambit")
        )
        # Row 4: Fresh Conversation / Scenario Starter
        kb.row(
            InlineKeyboardButton("🎲 Spark a Fresh Topic or Scenario", callback_data="action:spark_topic")
        )
        # Row 5: Podcast Lounge (Trio/Duo) & Phrase Finder/Miner
        kb.add(
            InlineKeyboardButton("🎙️ Podcast Lounge (Trio/Duo)", callback_data="menu:podcast"),
            InlineKeyboardButton("🔎 Phrase Finder & Miner", callback_data="menu:miner_lookup")
        )
        # Row 6: Speaking Workouts & Daily Review
        kb.add(
            InlineKeyboardButton("🎯 Speaking Workouts", callback_data="menu:drills"),
            InlineKeyboardButton("🧠 Daily Review", callback_data="srs:next")
        )
        # Row 7: Personal Vault & Fluency Audit
        kb.add(
            InlineKeyboardButton("📓 My Vault & Booklet", callback_data="menu:vault"),
            InlineKeyboardButton("📊 Fluency Audit", callback_data="action:fluency_audit")
        )
        # Row 8: Vibe Selector & Preferences
        kb.add(
            InlineKeyboardButton(f"🎚️ Vibe: {reg_label}", callback_data="action:cycle_register"),
            InlineKeyboardButton("⚙️ Preferences", callback_data="menu:settings")
        )
        # Row 9: Detailed Member's Handbook & Fresh Table
        kb.add(
            InlineKeyboardButton("📖 Member's Handbook", callback_data="guide:overview"),
            InlineKeyboardButton("🔄 Fresh Table", callback_data="action:clear_chat")
        )
        if is_admin:
            kb.row(InlineKeyboardButton("🛡️ Club Host Controls", callback_data="menu:admin"))
        return kb

    @staticmethod
    def cast_selector_menu(active_cast: str = "trio") -> InlineKeyboardMarkup:
        """Lets the member choose 1 (Solo), 2 (Duo), or 3 (Trio) hosts for spontaneous chat."""
        kb = InlineKeyboardMarkup(row_width=1)

        def _c(key: str, label: str) -> str:
            return f"✅ {label}" if active_cast == key else label

        # 3 Hosts (Trio)
        kb.add(InlineKeyboardButton(_c("trio", "🗽 Trio Table: Marcus, Chloe & Jake (All 3 Hosts)"), callback_data="persona:trio"))

        # 2 Hosts (Duo Combinations)
        kb.add(
            InlineKeyboardButton(_c("duo_mc", "🧔‍♂️👩🏼‍💼 Duo: Marcus & Chloe (Articulate + Witty)"), callback_data="persona:duo_mc"),
            InlineKeyboardButton(_c("duo_cj", "👩🏼‍💼🧢 Duo: Chloe & Jake (Witty + Brooklyn Casual)"), callback_data="persona:duo_cj"),
            InlineKeyboardButton(_c("duo_mj", "🧔‍♂️🧢 Duo: Marcus & Jake (Analytical + Street Story)"), callback_data="persona:duo_mj")
        )

        # 1 Host (Solo 1-on-1)
        kb.row(
            InlineKeyboardButton(_c("marcus", "🧔‍♂️ Marcus (1)"), callback_data="persona:marcus"),
            InlineKeyboardButton(_c("chloe", "👩🏼‍💼 Chloe (1)"), callback_data="persona:chloe"),
            InlineKeyboardButton(_c("jake", "🧢 Jake (1)"), callback_data="persona:jake")
        )
        kb.row(InlineKeyboardButton("🔙 Back to Conversation / Main Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def studio_prompt_bar(mode: str, active_persona: str = "trio") -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        cast_short = CAST_PRESETS.get(active_persona, CAST_PRESETS["trio"])["short"]

        kb.add(
            InlineKeyboardButton("🎲 Spark Another Topic", callback_data="action:spark_topic"),
            InlineKeyboardButton(f"👥 Hosts: {cast_short}", callback_data="menu:cast_selector")
        )
        kb.add(
            InlineKeyboardButton("🎚️ Switch Vibe", callback_data="action:cycle_register_room"),
            InlineKeyboardButton("🏠 Club Main Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def turn_action_bar(
        token_key: str,
        num_upgrades: int = 3,
        has_user_voice: bool = False,
        has_host_voice: bool = True,
        persona_key: str = "trio",
        has_recast_voice: bool = True,
        recast_label: str = "🗣️ My Recast (3x)",
        active_reg: str = "articulate",
        **kwargs
    ) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)

        audio_btns = []
        if has_host_voice:
            host_lbl = "🎧 Trio Voice" if persona_key == "trio" else "🎧 Host Voice"
            audio_btns.append(InlineKeyboardButton(host_lbl, callback_data=f"host_tts:{token_key}"))
        if has_recast_voice:
            audio_btns.append(InlineKeyboardButton(recast_label, callback_data=f"tts:{token_key}"))
        if has_user_voice:
            audio_btns.append(InlineKeyboardButton("🪞 Voice Mirror", callback_data=f"mirror:{token_key}"))
        if audio_btns:
            kb.row(*audio_btns)

        if num_upgrades > 0:
            save_btns = [
                InlineKeyboardButton(f"⭐ Save #{i+1}", callback_data=f"save:{token_key}:{i}")
                for i in range(min(num_upgrades, 3))
            ]
            kb.row(*save_btns)

        vibe_short = REGISTERS_MAP.get(active_reg, "🎙️ Articulate")
        kb.row(
            InlineKeyboardButton("🎲 New Topic", callback_data="spark:new"),
            InlineKeyboardButton(f"🎚️ Vibe: {vibe_short}", callback_data=f"vibe_cycle:{token_key}"),
            InlineKeyboardButton("🗂️ SRS Due", callback_data="menu:srs")
        )
        return kb

    @staticmethod
    def guide_navigation_kb(active_chapter: str = "overview") -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)

        def _c(key: str, label: str) -> str:
            return f"📍 {label}" if active_chapter == key else label

        kb.add(
            InlineKeyboardButton(_c("overview", "📖 Overview & The Trio"), callback_data="guide:overview"),
            InlineKeyboardButton(_c("ch1_xray", "🔬 1. Notes, Pivot & Mirror"), callback_data="guide:ch1_xray")
        )
        kb.add(
            InlineKeyboardButton(_c("ch2_studios", "🗽 2. The 6 Club Rooms"), callback_data="guide:ch2_studios"),
            InlineKeyboardButton(_c("ch3_audio_podcast", "🎙️ 3. Trio & Duo Podcast"), callback_data="guide:ch3_audio_podcast")
        )
        kb.add(
            InlineKeyboardButton(_c("ch4_miner_drills", "🎯 4. Finder & Workouts"), callback_data="guide:ch4_miner_drills"),
            InlineKeyboardButton(_c("ch5_vault_routine", "🧠 5. Vault & Daily Routine"), callback_data="guide:ch5_vault_routine")
        )
        kb.row(InlineKeyboardButton("🏠 Back to Club Menu", callback_data="menu:home"))
        return kb

    @staticmethod
    def podcast_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🗽 Trio Roundtable on My Vault (3+ Min: All 3 Hosts)", callback_data="pod:trio_vault"),
            InlineKeyboardButton("🎙️ Duo Deep-Dive on My Vault (3+ Min: Marcus & Chloe)", callback_data="pod:duo_mc"),
            InlineKeyboardButton("☕ Duo Street Banter on My Vault (3+ Min: Chloe & Jake)", callback_data="pod:duo_cj"),
            InlineKeyboardButton("✍️ Custom Topic — Trio Roundtable (3+ Min)", callback_data="mode:podcast_custom_trio"),
            InlineKeyboardButton("✍️ Custom Topic — Duo Deep-Dive (3+ Min)", callback_data="mode:podcast_custom_duo"),
            InlineKeyboardButton("🎤 'Be the Guest' (Live On-Air Interview with the Trio)", callback_data="mode:podcast_guest"),
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
            InlineKeyboardButton("🎭 Contrastive Stress & Subtext Lab", callback_data="drill:contrastive_stress"),
            InlineKeyboardButton("🎙️ American Rhythm & Shadowing Workout", callback_data="drill:shadowing"),
            InlineKeyboardButton("👂 Fast Ear Catcher (Connected Speech)", callback_data="drill:micro_dictation"),
            InlineKeyboardButton("🩺 Habits I'm Polishing", callback_data="drill:weakness_list"),
            InlineKeyboardButton("📊 My Spoken Fluency & Style Audit", callback_data="action:fluency_audit"),
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
            InlineKeyboardButton("📊 Spoken Fluency Audit", callback_data="action:fluency_audit")
        )
        kb.row(InlineKeyboardButton("🔙 Club Menu", callback_data="menu:home"))
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

        active_cast = user.get("active_persona", "trio")
        cast_short = CAST_PRESETS.get(active_cast, CAST_PRESETS["trio"])["short"]
        kb.row(InlineKeyboardButton(f"👥 Choose Hosts (1, 2, or 3): {cast_short}", callback_data="menu:cast_selector"))

        eng_only = bool(user.get("english_only", 1))
        eng_label = "🔒 100% English Immersion: ON" if eng_only else "🔓 Brief Arabic Notes: ALLOWED"
        kb.row(InlineKeyboardButton(eng_label, callback_data="set:toggle_eng"))
        kb.row(InlineKeyboardButton("📊 Generate My Spoken Fluency Audit", callback_data="action:fluency_audit"))
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

    @staticmethod
    def podcast_action_bar(token_key: str, num_takeaways: int = 3) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=3)
        if num_takeaways > 0:
            btns = [
                InlineKeyboardButton(f"⭐ Save #{i+1}", callback_data=f"save:{token_key}:{i}")
                for i in range(min(num_takeaways, 3))
            ]
            kb.row(*btns)
        kb.row(
            InlineKeyboardButton("💬 Discuss with Trio", callback_data=f"pod_discuss:{token_key}"),
            InlineKeyboardButton("🎙️ Another Show", callback_data="podcast:surprise")
        )
        kb.row(
            InlineKeyboardButton("✍️ Custom Topic", callback_data="podcast:custom"),
            InlineKeyboardButton("🏠 Club Menu", callback_data="menu:home")
        )
        return kb

    @staticmethod
    def srs_review_card_kb(card_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(
            InlineKeyboardButton("🔊 Hear Phrase", callback_data=f"srs_tts:{card_id}"),
            InlineKeyboardButton("💡 Show Context Hint", callback_data=f"srs_hint:{card_id}")
        )
        kb.row(
            InlineKeyboardButton("✅ Easy (Mastered)", callback_data=f"srs_grade:{card_id}:5"),
            InlineKeyboardButton("🔁 Review Tomorrow", callback_data=f"srs_grade:{card_id}:3")
        )
        kb.row(
            InlineKeyboardButton("⏭️ Skip Card", callback_data="menu:srs"),
            InlineKeyboardButton("💬 Back to Chat", callback_data="mode:spontaneous_chat")
        )
        return kb
