# -*- coding: utf-8 -*-
"""
config.py — Configuration, October 2026 Gemini Models, Trio Personas (Marcus, Chloe, Jake),
Phonetic Precision Laws & C2 Applied Linguistics Guide for Musa's NYC Lounge.
"""
import os
import tempfile
from typing import List, Dict, Any


def _clean_env(keys: List[str], default: str = "") -> str:
    for k in keys:
        val = os.getenv(k, "").strip().strip('"').strip("'")
        if val:
            return val
    return default


def collect_gemini_keys() -> List[str]:
    raw_pool = []
    for k in ["GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        val = os.getenv(k, "")
        if val:
            for part in val.replace("\n", ",").split(","):
                clean = part.strip().strip('"').strip("'")
                if clean and clean not in raw_pool:
                    raw_pool.append(clean)
    return raw_pool


BOT_TOKEN: str = _clean_env(["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"])
GEMINI_KEYS: List[str] = collect_gemini_keys()
GEMINI_API_KEY: str = GEMINI_KEYS[0] if GEMINI_KEYS else ""

_raw_admin = _clean_env(["ADMIN_ID", "CHAT_ID"], "8874707150")
ADMIN_ID: int = int(_raw_admin) if _raw_admin.isdigit() else 8874707150

USER_DB_FILENAME: str = "musa_user_data_v3.db"
CORPUS_DB_FILENAME: str = "musa_corpus_fts5.db"

USER_DB_PATH: str = os.getenv("USER_DB_PATH", USER_DB_FILENAME)
CORPUS_DB_PATH: str = os.getenv("CORPUS_DB_PATH", CORPUS_DB_FILENAME)

TEMP_DIR: str = os.path.join(tempfile.gettempdir(), "musa_nyc_lounge_media")
os.makedirs(TEMP_DIR, exist_ok=True)

# 4.65 hours (16,740s) ensures clean shutdown & DB backup BEFORE the 5.0h GitHub cron triggers
RELAY_MAX_RUNTIME_SECONDS: int = int(4.65 * 3600)
PERIODIC_BACKUP_SECONDS: int = 7200

# Verified October 2026 Official Gemini API Models Chain
GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview"
]

# =====================================================================
# The NYC Lounge Trio Personas (Marcus, Chloe, Jake)
# =====================================================================
VOICE_MARCUS: str = "en-US-AndrewNeural"
VOICE_CHLOE: str = "en-US-AvaNeural"
VOICE_JAKE: str = "en-US-BrianNeural"

# Backward compatibility aliases
VOICE_MALE_HOST: str = VOICE_MARCUS
VOICE_FEMALE_HOST: str = VOICE_CHLOE

PERSONAS_MAP: Dict[str, Dict[str, Any]] = {
    "trio": {
        "label": "🗽 Trio Table",
        "short": "Trio",
        "voice": VOICE_MARCUS,
        "base_rate": "+1%",
        "default_register": "articulate",
        "bio": (
            "All three Manhattan hosts at the table: Marcus (analytical & articulate), "
            "Chloe (witty social strategist & subtext expert), and Jake (casual Brooklyn storyteller)."
        )
    },
    "marcus": {
        "label": "🧔‍♂️ Marcus",
        "short": "Marcus",
        "voice": VOICE_MARCUS,
        "base_rate": "+0%",
        "default_register": "articulate",
        "bio": (
            "Marcus — Upper West Side cultural critic & rhetoric coach. "
            "Tone: 🎙️ Articulate, intellectually sharp, master of semantic nuance, pragmatic hedging "
            "('To put a finer point on it...', 'By the same token...'), and persuasive framing."
        )
    },
    "chloe": {
        "label": "👩🏼‍💼 Chloe",
        "short": "Chloe",
        "voice": VOICE_CHLOE,
        "base_rate": "+2%",
        "default_register": "witty",
        "bio": (
            "Chloe — Soho media strategist & pragmatic subtext specialist. "
            "Tone: 🎭 Witty, perceptive, dry understatement, conversational implicature, "
            "reading between the lines, and razor-sharp workplace/social diplomacy."
        )
    },
    "jake": {
        "label": "🧢 Jake",
        "short": "Jake",
        "voice": VOICE_JAKE,
        "base_rate": "+5%",
        "default_register": "casual",
        "bio": (
            "Jake — Brooklyn native, creative producer & master storyteller. "
            "Tone: ☕ Casual, warm, fast-paced street-smart rhythm, phrasal-verb rich, "
            "dramatic present-tense storytelling, and authentic connected speech."
        )
    }
}

REGISTERS_MAP: Dict[str, str] = {
    "articulate": "🎙️ Articulate",
    "casual": "☕ Casual",
    "witty": "🎭 Witty"
}

REGISTER_DESCRIPTIONS: Dict[str, str] = {
    "articulate": "Polished, expressive spoken American English—natural for thoughtful discussions, work, or smart social conversations.",
    "casual": "Relaxed, fast-paced everyday New York talk among close friends—idiomatic, warm, and effortless.",
    "witty": "Dry understatement, playful irony, rhetorical sharpness, and clever punchlines."
}

CORE_NYC_PERSONA_PROMPT: str = """
You are the host team of 'Musa's NYC Lounge'—an intimate Manhattan club for mastering C1/C2 Spoken American English, featuring three distinct New York personalities:
• 🧔‍♂️ Marcus (🎙️ Articulate): Analytical, precise with semantic distinctions, diplomatic pushback, and rhetorical flow.
• 👩🏼‍💼 Chloe (🎭 Witty): Perceptive, quick-witted, decodes unspoken social subtext, dry understatement, and pragmatic nuance.
• 🧢 Jake (☕ Casual): Fast-talking Brooklynite, master of phrasal verbs, dramatic storytelling, and relaxed street-smart banter.

YOUR APPLIED LINGUISTICS & SLA COACHING PILLARS:
1. AUTHENTIC SPOKEN NATURALNESS & STANCE-TAKING:
   - Never sound like a textbook, teacher, or AI. Never break character (never say 'don't let X distract you from our English sessions').
   - Model Spoken American Grammar: conversational clefts ('What really gets me is...', 'The thing about X is...'), Heads & Tails, natural hedging ('for the most part', 'more or less'), and vocative/pronoun naturalness ('Have you guys ever been...' instead of '...before, guys?').
   - When the active persona is 'trio' OR when the member addresses multiple people ('guys', 'everyone', 'you all', 'both of you'), format `lounge_reply` as a dynamic 2- or 3-host exchange (e.g. "🧢 <b>Jake:</b> ...\n\n👩🏼‍💼 <b>Chloe:</b> ...") where they react to Musa AND to each other!

2. PRAGMATIC STEALTH WEAVING (ZERO FORCED CRINGE):
   - Only weave a saved Vault phrase into `lounge_reply` if its pragmatic trigger fits the exact moment 100% naturally. If none fit organically, DO NOT force them in.

3. MID-SENTENCE SELF-REPAIR & FLOOR-HOLDING GAMBITS (Levelt's Production Model):
   - In every turn (`self_repair_pivot`), model how an American smoothly adjusts, softens, or sharpens Musa's exact thought mid-sentence ('Actually, let me walk that back—what I really mean is...', 'Or rather, to put a finer point on it...').

4. LEXICAL PRIMING & C2 COLLOCATION DEPTH (Hoey / Dellar & Walkley):
   - Prioritize C1/C2 idiomatic chunks, collocations, and conversational gambits over basic B1/B2 words.
   - Match the `register_tag` honestly to the expression's true feel (do not label a normal phrase '🎭 Witty' unless it actually carries irony, understatement, or playful edge).
   - For every expression, provide a `flat_b2_foil` showing the flat textbook phrasing it replaces (e.g. "Instead of: 'I really want to travel' ➔ 'I've been itching to get away'").
   - For every expression, write a `cloze_sentence` where the exact inflected form of the chunk inside `example` is replaced with `______` (never leave the answer visible in `cloze_sentence`!).

5. UNCOMPROMISING PHONETIC & SUPRASEGMENTAL ACCURACY (Flege's SLM & Brazil's Intonation):
   - `acoustic_prosody_xray` MUST map the rhythm of the **Polished Native Recast (`tts_clean_target`)**, NEVER the unpolished user sentence!
   - STRICT PHONETIC LAWS (Zero Hallucinations):
     * Flap-T [ɾ] ONLY occurs when /t/ or /d/ is strictly intervocalic/sonorant-flanked (e.g., 'what about it' -> 'whaddaboudit', 'go to' -> 'go-duh'). NEVER claim a Flap-T after a voiceless obstruent like 'trip to' (/p/ + /t/) or 'want to' (which uses assimilation 'wanna').
     * Glottal Stop [ʔ] occurs before syllabic /n/ ('certain', 'Manhattan', 'button') or word-final unreleased [t̚] before consonants ('what spots' -> 'wha[t̚] spots').
     * Function-Word Schwa (/ə/) Reduction: Always reduce unstressed 'for' -> 'fer' [fər], 'to' -> 'tuh' [tə], 'of' -> 'uhv' [əv], 'have you' -> 'hæv-yə'.
     * Intonation Contours: Use rising pitch (↗) on genuine Yes/No questions and non-final thought groups (|), and falling pitch (↘) on Wh-questions and completed statements.
     * On Voice Notes (`voice_delivery_audit`): Specifically audit Vowel Length & Pre-Fortis Clipping (vowels shortened before voiceless codas like 'seat' vs 'seed'), Tense vs. Lax vowels (/iː/ vs /ɪ/, /uː/ vs /ʊ/), and Nuclear Stress placement.
""".strip()

GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — C2 Applied Linguistics Handbook</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your private Manhattan roundtable with <b>🧔‍♂️ Marcus</b>, <b>👩🏼‍💼 Chloe</b>, and <b>🧢 Jake</b>.\n\n"
        "<blockquote expandable>🗽 <b>1. Meet Your Three NYC Hosts</b>\n"
        "• <b>🧔‍♂️ Marcus (🎙️ Articulate):</b> Analytical cultural critic. Coaches you on semantic precision, persuasive framing, and diplomatic pushback.\n"
        "• <b>👩🏼‍💼 Chloe (🎭 Witty):</b> Soho strategist. Decodes unspoken American subtext, dry understatement, and social nuance.\n"
        "• <b>🧢 Jake (☕ Casual):</b> Brooklyn storyteller. Masters fast phrasal verbs, dramatic present tense, and street-smart connected speech.\n"
        "• <b>🗽 Trio Table Mode:</b> Tap the Host button on the menu (or say <i>'hey guys'</i> in chat) to have Marcus, Chloe, and Jake bounce ideas off each other live!</blockquote>\n\n"
        "<blockquote expandable>🎛️ <b>2. Interactive Action Bar Controls</b>\n"
        "• <code>🎧 Host Voice</code>: Hear Marcus, Chloe, or Jake speak their reply aloud.\n"
        "• <code>🗣️ My Recast</code>: Hear the polished American version of your own thought.\n"
        "• <code>🪞 Voice Mirror</code>: Plays <b>Your Voice ➔ 🔔 ➔ Native Model (1.0x) ➔ 🔔 ➔ Slow Study (0.85x)</b> with studio loudness matching.\n"
        "• <code>🎙️ Take 2</code>: Immediately record a 10-second retry using the upgraded phrasing & rhythm for instant comparison!\n"
        "• <code>⭐ Save (1/2/3)</code>: Saves that expression to your Vault with Lexical Priming frames.</blockquote>"
    ),
    "ch1_xray": (
        "🔬 <b>Chapter 1: Lexical Priming, Uptake Loop & Voice Mirror</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. Flat B2 Foil ➔ Native C2 Upgrade:</b>\n"
        "Every expression in your Coaching Notes shows the exact textbook phrasing it replaces so your brain rewires its automatic reflexes.\n\n"
        "<b>2. 🔥 Active Uptake Detection:</b>\n"
        "Listening isn't enough—true acquisition happens when you <i>produce</i> an expression yourself. Whenever you naturally use a phrase from your Vault in chat or voice notes, the Lounge detects it automatically and celebrates your <b>Active Mastery</b>!\n\n"
        "<b>3. 🎙️ The 'Take 2' Pushed-Output Loop:</b>\n"
        "After reading your Coaching Notes, tap <code>🎙️ Take 2</code> and send a quick voice note trying out the recast sentence. Your host will compare your Take 2 against your first attempt!"
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The 6 Conversation & Sparring Rooms</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🗣️ NYC Banter:</b> Unscripted everyday table talk with Marcus, Chloe, and Jake.\n"
        "• <b>🥊 Devil's Advocate:</b> High-level pushback sparring. Defend your premise using American hedging and concession-and-counter gambits.\n"
        "• <b>🎭 Storytelling Gym:</b> Master dramatic present-tense pacing and punchlines.\n"
        "• <b>🎛️ Vibe & Style Shifter:</b> Transform any thought across Articulate, Casual, and Witty registers.\n"
        "• <b>🕵️‍♂️ Subtext & Nuance Decoder:</b> Unpack conversational implicature and polite American brush-offs.\n"
        "• <b>⚡ What to Say When...:</b> Describe any tricky social or workplace moment to get instinctive native gambits."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>Chapter 3: NotebookLM-Style Trio Podcast & Phonetic Audit</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. 🎙️ The Trio Podcast Lounge (Marcus, Chloe & Jake):</b>\n"
        "• Rapid micro-turns, natural interruptions, stereo panning, and zero dead silence.\n"
        "• Choose between <b>🎧 Trio Banter on My Vault</b>, <b>🥊 Trio Heated Debate</b>, or <b>🎙️ Join the Table</b>.\n"
        "• Includes <b>Timestamped Noticing Markers</b> (e.g. <code>[00:24]</code>) showing the exact second each target phrase is used!\n\n"
        "<b>2. 🔬 Sub-Segmental Phonetic Precision:</b>\n"
        "• Audits Tense vs. Lax vowels, Pre-Fortis Clipping, Nuclear Stress, true Intervocalic Flap-T <code>[ɾ]</code>, Glottal Stops <code>[ʔ]</code>, and Schwa <code>/ə/</code> reductions."
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: Collocation Finder, Text Miner & Workouts</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🔎 Collocation & Phrase Finder:</b> Type any word to get its C1/C2 Spoken Collocation Map.\n"
        "• <b>🔬 Native Text Miner:</b> Paste any article or transcript to extract high-value idioms and decode its tone.\n"
        "• <b>🎯 Speaking Workouts:</b> Launch the <b>3-Phrase Challenge</b>, <b>Contrastive Stress Lab</b>, <b>B1-to-C2 Reflex Gym</b>, <b>Shadowing Workout</b>, or <b>Fast Ear Catcher</b>."
    ),
    "ch5_vault_routine": (
        "🧠 <b>Chapter 5: Productive Vault Review & Daily Routine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🧠 Two-Way Daily Review:</b> Reveal & self-grade cards OR send a live voice note/sentence using the target phrase from memory for instant grading.\n"
        "• <b>📄 Printable PDF Booklet & Anki Deck:</b> Export your entire collection anytime from <code>📓 My Vault & Booklet</code>."
    )
}
