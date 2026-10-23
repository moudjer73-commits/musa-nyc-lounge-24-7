# -*- coding: utf-8 -*-
"""
config.py — Pure Club Experience Configuration, Phonetic Precision & Pragmatic Calibration
Musa's NYC Lounge
"""
import os
import tempfile
from typing import List, Dict

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

RELAY_MAX_RUNTIME_SECONDS: int = int(5.3 * 3600)
PERIODIC_BACKUP_SECONDS: int = 10800  # Every 3 hours, silent & self-cleaning

GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

VOICE_MALE_HOST: str = "en-US-AndrewNeural"
VOICE_FEMALE_HOST: str = "en-US-AvaNeural"

# =====================================================================
# The 3 Spoken American Registers (Clean User-Facing Labels)
# =====================================================================
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
You are a charismatic, perceptive, and culturally literate New Yorker hosting 'Musa's NYC Lounge'—an intimate Manhattan club for mastering authentic Spoken American English.

YOUR CORE COACHING PILLARS:
1. AUTHENTIC SPOKEN NATURALNESS & CONTEXTUAL FIT:
   - Never sound like a bot, textbook, or academic lecturer.
   - Calibrate every expression to the real human moment—whether it's a casual street chat, a sharp workplace negotiation, or a deep philosophical debate.
   - Actively model Spoken American Grammar: conversational clefts ('What really gets me is...', 'The thing about X is...'), Heads & Tails ('That new spot on Bleecker—have you tried it yet?'), natural hedging ('kind of', 'for the most part', 'more or less'), and discourse markers ('Mind you', 'Funny enough', 'Wait, seriously?', 'Fair enough, though').

2. SEMANTIC PRECISION & PRAGMATIC NUANCE:
   - When upgrading the user's phrasing, highlight subtle semantic distinctions (why one verb or collocation carries the exact shade of meaning over a near-synonym).
   - Point out unspoken pragmatic subtext, register shifts, and idiomatic collocations.

3. STRICT PHONETIC & PROSODIC PRECISION (AMERICAN ACCENT COACH):
   - When the user sends a voice note, act as an uncompromising yet encouraging American pronunciation & rhythm critic:
     * Vowel Precision: Catch tense vs. lax vowel slips (/iː/ vs /ɪ/, /uː/ vs /ʊ/), the American trap-bath /æ/, and diphthongs (/oʊ/, /eɪ/).
     * Word & Phrase Stress: Pinpoint primary stress (CAPS) vs. unstressed syllables reduced to Schwa (/ə/).
     * Connected Speech Mechanics: Show exact Flap-T ([ɾ] between vowels: 'what about it' -> 'whaddaboudit'), Glottal Stop ([ʔ] before -en: 'button', 'certain'), palatalization ('did you' -> 'didja', 'got you' -> 'gotcha'), consonant linking, and pitch contours (↘ / ↗) with thought-group pauses (|).

4. 3-PART CONVERSATIONAL FRAMES:
   - Every expression you teach must include:
     * 🎯 When to use it (The real-life trigger moment)
     * 🔗 How it links (Natural verb/preposition collocations & grammar pattern)
     * 💬 How to follow it up (The natural next line an American says right after it)

5. ZERO TECHNICAL JARGON:
   - Never mention AI, models, databases, algorithms, or linguistic abbreviations like 'L1' or 'CEFR'. Speak strictly as a real human coach and friend in New York.
""".strip()

# =====================================================================
# Clean, Immersive Member's Handbook (Zero Technical Jargon)
# =====================================================================
GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — Member's Handbook</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your private Manhattan club for mastering <b>Spoken American English</b>—from effortless everyday banter to razor-sharp pronunciation and cultural nuance.\n\n"
        "<blockquote expandable>🗽 <b>How Every Conversation Works</b>\n"
        "• <b>Natural Conversation First:</b> At the top of every message, your host replies naturally to your ideas—just like talking to a sharp friend over coffee in Greenwich Village.\n"
        "• <b>Subtle Phrase Weaving (<i>Italics</i>):</b> Expressions in <i>italics</i> inside the host's reply are phrases from your personal notebook woven naturally into our conversation so you absorb them effortlessly.\n"
        "• <b>Collapsible Coaching Notes:</b> Tap the expandable box below any reply to see your thought recast across <b>3 American Vibes</b> (<b>🎙️ Articulate</b>, <b>☕ Casual</b>, <b>🎭 Witty</b>), a full <b>Pronunciation & Rhythm Map</b>, and <b>3 Spoken Expressions</b> to save.</blockquote>\n\n"
        "<blockquote expandable>🎧 <b>Your Audio & Saving Controls</b>\n"
        "• <code>🎧 Host Voice</code>: Hear the host's reply spoken aloud with natural New York rhythm.\n"
        "• <code>🗣️ My Recast</code>: Hear the polished American version of your own sentence.\n"
        "• <code>🪞 Voice Mirror</code>: Appears when you send a voice note—plays your voice followed immediately by the native version at normal and slow study speeds.\n"
        "• <code>⭐ Save (1/2/3)</code>: Saves that specific expression directly into your Personal Vault.</blockquote>\n\n"
        "👇 <b>Tap any chapter below to explore the club's rooms:</b>"
    ),
    "ch1_xray": (
        "🔬 <b>1. Inside the Coaching Notes & Voice Mirror</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. The 3 Living American Vibes:</b>\n"
        "Every time you speak or write, see how a native speaker expresses your exact thought in three ways:\n"
        "• <b>🎙️ Articulate:</b> Clear, expressive, and intelligent—ideal for work, podcasts, and deep discussions.\n"
        "• <b>☕ Casual:</b> Warm, fast-paced everyday talk among close friends.\n"
        "• <b>🎭 Witty:</b> Dry humor, clever understatement, and playful pushback.\n\n"
        "<b>2. 🪞 The Voice Mirror (For Voice Notes):</b>\n"
        "Whenever you send a voice message, tap <code>🪞 Voice Mirror</code> under the reply. You will receive a single audio track containing:\n"
        "<code>[Your Original Voice] ➔ 🔔 ➔ [Native Pace 1.0x] ➔ 🔔 ➔ [Slow Study Pace 0.85x]</code>\n"
        "Hearing your own vocal rhythm side-by-side with the native model trains your ear to catch vowel lengths, word stress, and connected speech instantly."
    ),
    "ch2_studios": (
        "🗽 <b>2. The 5 Conversation & Style Rooms</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🗣️ NYC Banter:</b> Unscripted conversation on any topic. Drop a voice note or text, or tap <code>🎲 Spark a Topic</code> for a fresh dilemma.\n\n"
        "• <b>🎭 Storytelling Gym:</b> Practice telling engaging stories using dramatic present tense (<i>'So I walk in, right...'</i>), natural setups, and punchlines.\n\n"
        "• <b>🎛️ Vibe & Style Shifter:</b> Send any sentence to see how its tone shifts across Articulate, Casual, and Witty contexts.\n\n"
        "• <b>🕵️‍♂️ Subtext & Nuance:</b> Decode what Americans <i>really</i> mean between the lines (polite brush-offs, understatement, dry irony) and learn how to reply smoothly.\n\n"
        "• <b>⚡ What to Say When...:</b> Describe any social or workplace moment (in English or Arabic) to get the exact natural phrases Americans reach for in that second."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>3. Pronunciation Coaching & The Radio Lounge</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. Precision Pronunciation Feedback:</b>\n"
        "When you send voice notes, the coach listens closely to your delivery—checking vowel lengths, primary word stress, Flap-T linking, and vowel reductions (Schwa).\n\n"
        "<b>2. 🎙️ The Radio Lounge (Andrew & Ava):</b>\n"
        "• <b>Episode from My Vault:</b> Our two New York hosts record a lively radio conversation weaving in the exact phrases you've saved in your Vault, pausing mid-show to explain why each phrase works.\n"
        "• <b>Custom Topic Show:</b> Pick any topic or debate for Andrew & Ava to discuss on-air.\n"
        "• <b>Be the Guest:</b> Step up to the mic as the featured guest in a live interview."
    ),
    "ch4_miner_drills": (
        "🎯 <b>4. Phrase Finder, Text Miner & Active Workouts</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🔎 Collocation & Phrase Finder:</b> Type any word or verb (e.g., <i>'slack'</i>, <i>'wind up'</i>, <i>'point'</i>) to instantly see its most natural spoken American collocations, patterns, and examples.\n\n"
        "• <b>🔬 Native Text Miner:</b> Paste a paragraph from an article, book, or movie script to extract its best spoken idioms and unpack its tone.\n\n"
        "• <b>🔥 3-Phrase Challenge:</b> We give you 3 target expressions from your Vault and a realistic scenario—try weaving all 3 into a single voice note or reply!\n\n"
        "• <b>🎙️ Rhythm & Shadowing:</b> Listen to a connected-speech sentence with a full stress & Flap-T map, then record yourself matching the exact cadence.\n\n"
        "• <b>👂 Fast Ear Catcher:</b> Listen to a fast, natural American audio clip with no text, and type what you heard to sharpen your listening ear."
    ),
    "ch5_vault_routine": (
        "🧠 <b>5. Your Vault, Daily Review & Exports</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. How Phrases Are Stored:</b>\n"
        "Every saved expression includes <b>When to use it</b>, <b>How it links grammatically</b>, and <b>What to say right after it</b>.\n\n"
        "<b>2. 🧠 Daily Phrase Review (Active & Passive):</b>\n"
        "Open <code>🧠 Daily Review</code> to practice due phrases. You can either tap to reveal and grade yourself, OR <b>send a voice note / sentence using the phrase</b> to get instant feedback!\n\n"
        "<b>3. 📄 Printable Book & Flashcard Exports:</b>\n"
        "From <code>📓 My Vault</code>, download your entire collection as a clean <b>PDF Booklet</b> or an <b>Anki Flashcard Deck</b> anytime."
    )
}
