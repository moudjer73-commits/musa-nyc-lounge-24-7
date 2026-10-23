# -*- coding: utf-8 -*-
"""
config.py — Pure Club Experience Configuration, Phonetic Precision & C2 Mastery Guide
Musa's NYC Lounge
Includes Trio Personas (Marcus, Chloe, Jake), Official Gemini Chain & Tuned Relay Cadence.
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

# Safe Relay runtime: 4.7 hours (Leaves safety window before the 5.0h cron workflow run)
RELAY_MAX_RUNTIME_SECONDS: int = int(4.7 * 3600)
PERIODIC_BACKUP_SECONDS: int = 7200  # Sync vault every 2 hours

# Verified Active Gemini Models Chain (Fallbacks for high availability)
GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash"
]

# =====================================================================
# The NYC Trio: Distinct Vocal and Pedagogical Personas
# =====================================================================
PERSONAS: Dict[str, Dict[str, Any]] = {
    "marcus": {
        "name": "Marcus",
        "title": "🧔‍♂️ Marcus (The Rhetorician)",
        "voice": "en-US-AndrewNeural",
        "rate": "+0%",
        "pitch": "-1Hz",
        "register": "articulate",
        "vibe_badge": "🎙️ Articulate",
        "style_desc": "Polished, analytical, and deeply articulate Manhattanite. Specializes in rhetorical framing, intellectual debates, and precise semantic nuances."
    },
    "chloe": {
        "name": "Chloe",
        "title": "👩🏼‍💼 Chloe (The Social Strategist)",
        "voice": "en-US-AvaNeural",
        "rate": "+1%",
        "pitch": "+1Hz",
        "register": "witty",
        "vibe_badge": "🎭 Witty",
        "style_desc": "Sharp, culturally savvy New Yorker. Specializes in diplomatic pushback, reading unspoken subtext, dry understatement, and social nuance."
    },
    "jake": {
        "name": "Jake",
        "title": "🧢 Jake (The Street Storyteller)",
        "voice": "en-US-BrianNeural",
        "rate": "+4%",
        "pitch": "+0Hz",
        "register": "casual",
        "vibe_badge": "☕ Casual",
        "style_desc": "Fast-paced, authentic Brooklynite. Master of dramatic present-tense storytelling, idioms, Phrasal Verbs, and natural connected speech."
    }
}

DEFAULT_PERSONA = "marcus"

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
You are hosting 'Musa's NYC Lounge'—an exclusive Manhattan conversational club for mastering authentic Spoken American English at C1/C2 native depth.

THE TRIO AT THE TABLE:
1. 🧔‍♂️ Marcus: Articulate, analytical, values semantic precision, rhetorical structure, and diplomatic framing.
2. 👩🏼‍💼 Chloe: Witty, socially astute, expert in reading pragmatic subtext, dry understatement, and situational gambits.
3. 🧢 Jake: Casual, fast-paced Brooklynite, lives on connected speech, punchy phrasal verbs, and dramatic present storytelling.

STRICT APPLIED LINGUISTICS & PHONETIC RULES:
1. RIGOROUS PHONETIC TRUTH (NO FLAP-T HALLUCINATION):
   - In `acoustic_prosody_xray`, ONLY mention Flap-T [ɾ] when /t/ or /d/ occurs strictly in intervocalic position between vowels (e.g., 'water', 'meeting', 'what about it') or after /r/ before a vowel. NEVER claim a Flap-T occurs after a consonant like /p/, /k/, /s/ (e.g., 'trip to' has NO flap-T).
   - Distinguish tense vs. lax vowels (/iː/ vs /ɪ/, /uː/ vs /ʊ/, /æ/ vs /ɛ/).
   - Always highlight Nuclear (Sentence) Stress in ALL CAPS, thought group pauses with (|), and intonation contours (↘ / ↗).
   - In text turns: Build the acoustic rhythm map on the POLISHED AMERICAN RECAST (`tts_clean_target`), NOT on the user's non-native phrasing.

2. LEXICAL PRIMING & B2-TO-C2 UPGRADES:
   - Provide natural American collocations and phrasal verbs.
   - For every expression taught, include:
     * Flat B2 Foil (What intermediate learners say) ➔ Native C2 Frame
     * 🎯 When to reach for it (The trigger moment)
     * 🔗 Colligation Pattern (Prepositions, verb forms)
     * 💬 Natural follow-up line

3. NATURAL CONVERSATION FIRST:
   - If the user addresses the room generally (e.g., 'guys', 'everyone'), let the Trio banter naturally with contrasting perspectives.
   - Never sound like an AI, textbook, or academic lecturer. Zero technical jargon.
""".strip()

# =====================================================================
# Detailed 6-Chapter Member's Handbook
# =====================================================================
GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — Detailed Member's Handbook</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your private Manhattan club for mastering <b>Spoken American English (C1/C2)</b>—from effortless everyday banter and workplace diplomacy to razor-sharp pronunciation and cultural subtext.\n\n"
        "<blockquote expandable>🗽 <b>1. Meet Your New York Trio</b>\n"
        "• 🧔‍♂️ <b>Marcus (The Rhetorician):</b> Master of polished debate, articulate phrasing, and semantic precision.\n"
        "• 👩🏼‍💼 <b>Chloe (The Social Strategist):</b> Queen of dry wit, reading between the lines, and diplomatic tact.\n"
        "• 🧢 <b>Jake (The Street Storyteller):</b> Fast Brooklyn pace, connected speech, phrasal verbs, and punchy stories.</blockquote>\n\n"
        "<blockquote expandable>🎛️ <b>2. Action Bar Controls (Under Every Reply)</b>\n"
        "• <code>🎧 Host Voice</code>: Hear the host's reply spoken aloud with authentic New York cadence.\n"
        "• <code>🗣️ My Recast</code>: Hear the polished American version of your own message.\n"
        "• <code>🪞 Voice Mirror</code>: Appears on voice notes—plays <b>Your Voice ➔ Native 1.0x ➔ Slow Study 0.85x</b> with studio volume leveling.\n"
        "• <code>⭐ Save (1/2/3)</code>: Saves that specific expression to your Vault & Daily Review.\n"
        "• <code>🎲 New Topic</code>: Sparks a fresh, unscripted scenario or dilemma anytime.</blockquote>\n\n"
        "👇 <b>Tap any chapter button below for the complete step-by-step guide:</b>"
    ),
    "ch1_xray": (
        "🔬 <b>Chapter 1: Coaching Notes, Self-Repair & The Voice Mirror</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. The 3 Living American Vibes:</b>\n"
        "Every turn recasts your exact thought across:\n"
        "• <b>🎙️ Articulate (Marcus):</b> Clear, expressive, rich in precise collocations.\n"
        "• <b>☕ Casual (Jake):</b> Fast, warm everyday talk with natural ellipsis and contractions.\n"
        "• <b>🎭 Witty (Chloe):</b> Dry understatement, playful irony, and clever rhetorical edge.\n\n"
        "<blockquote expandable>🔄 <b>2. Mid-Sentence Self-Repair & Pivot Coaching</b>\n"
        "Native speakers constantly adjust thoughts mid-sentence without losing flow. We coach you on authentic repair markers:\n"
        "• <i>'Actually, let me walk that back—what I mean is...'</i>\n"
        "• <i>'Or rather, to put a finer point on it...'</i>\n"
        "• <i>'Scratch that—the real issue is...'</i></blockquote>\n\n"
        "<blockquote expandable>🪞 <b>3. The Side-by-Side Voice Mirror</b>\n"
        "Send a voice note and tap <code>🪞 Voice Mirror</code> to hear your pronunciation compared directly to native cadence with automatic loudness normalization.</blockquote>"
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The Conversation & Sparring Rooms</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🗣️ NYC Banter:</b> Unscripted everyday conversation with the Trio.\n"
        "• <b>🥊 Devil's Advocate:</b> Marcus challenges your premise to sharpen your persuasion.\n"
        "• <b>🎭 Storytelling Gym:</b> Jake coaches you on pacing, setup, and landing the punchline.\n"
        "• <b>🎛️ Vibe Shifter:</b> Transform any thought across all three registers.\n"
        "• <b>🕵️‍♂️ Subtext Decoder:</b> Chloe decodes unspoken American hints and brush-offs.\n"
        "• <b>⚡ What to Say When...:</b> Get the exact instinctive phrase Americans reach for in any awkward second."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>Chapter 3: The NYC Radio Lounge (NotebookLM Style)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "An immersive studio experience featuring <b>Marcus, Chloe, and Jake</b> with rapid-fire turns, natural interjections, and spatial audio stereo panning!\n\n"
        "• <b>🎧 Trio Banter on My Vault:</b> The Trio weaves your saved phrases into an unscripted debate.\n"
        "• <b>✍️ Custom Topic Debate:</b> Throw any dilemma at the Trio and listen to them dissect it.\n"
        "• <b>🎙️ 'Be the Guest':</b> Step onto the mic as a featured on-air guest."
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: Phrase Finder & Speaking Workouts</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🔎 Collocation Finder:</b> Look up any word to explore its spoken American collocations.\n"
        "• <b>🔬 Native Text Miner:</b> Extract high-value spoken idioms from any pasted text.\n"
        "• <b>🔥 The 3-Phrase Speaking Challenge:</b> Weave 3 Vault phrases into a single live scenario.\n"
        "• <b>🎭 Contrastive Stress Lab:</b> Learn how shifting sentence stress flips the hidden subtext.\n"
        "• <b>🎙️ Rhythm & Shadowing:</b> Master Flap-T, glottal stops, and thought-group pauses."
    ),
    "ch5_vault_routine": (
        "🧠 <b>Chapter 5: Vault, Daily Review & Routine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Active Daily Review:</b> Send a voice note or sentence using your due phrases for instant coaching.\n"
        "• <b>Phrasebook (PDF) & Anki Deck:</b> Export your personal expressions anytime.\n"
        "• <b>15-Minute Daily Routine:</b> Review 3 cards in the morning, do 2 banter turns at lunch, and shadow one rhythm clip in the evening."
    )
}
