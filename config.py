# -*- coding: utf-8 -*-
"""
config.py — Pure Club Experience Configuration, Phonetic Precision & C2 Mastery Guide
Musa's NYC Lounge
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

RELAY_MAX_RUNTIME_SECONDS: int = int(4.7 * 3600)
PERIODIC_BACKUP_SECONDS: int = 7200

GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite"
]

PERSONAS: Dict[str, Dict[str, Any]] = {
    "marcus": {
        "name": "Marcus",
        "icon": "🧔‍♂️",
        "title": "🧔‍♂️ Marcus (The Rhetorician)",
        "voice": "en-US-AndrewNeural",
        "rate": "+0%",
        "pitch": "-1Hz",
        "register": "articulate",
        "vibe_badge": "🎙️ Articulate",
        "style_desc": "Polished, analytical, and deeply articulate Manhattanite. Specializes in rhetorical framing, intellectual debates, and precise semantic nuances.",
        "style": "Marcus in Musa's NYC Lounge—ALWAYS speaking strictly in the member's currently active Spoken Vibe (Articulate, Casual, Witty, Executive, or Diplomatic) using natural contractions, ellipsis, spoken fillers, and organic flow."
    },
    "chloe": {
        "name": "Chloe",
        "icon": "👩🏼‍💼",
        "title": "👩🏼‍💼 Chloe (The Social Strategist)",
        "voice": "en-US-AvaNeural",
        "rate": "+1%",
        "pitch": "+1Hz",
        "register": "witty",
        "vibe_badge": "🎭 Witty",
        "style_desc": "Sharp, culturally savvy New Yorker. Specializes in diplomatic pushback, reading unspoken subtext, dry understatement, and social nuance.",
        "style": "Chloe in Musa's NYC Lounge—ALWAYS speaking strictly in the member's currently active Spoken Vibe (Articulate, Casual, Witty, Executive, or Diplomatic) using natural contractions, ellipsis, spoken fillers, and organic flow."
    },
    "jake": {
        "name": "Jake",
        "icon": "🧢",
        "title": "🧢 Jake (The Street Storyteller)",
        "voice": "en-US-BrianNeural",
        "rate": "+4%",
        "pitch": "+0Hz",
        "register": "casual",
        "vibe_badge": "☕ Casual",
        "style_desc": "Fast-paced, authentic Brooklynite. Master of dramatic present-tense storytelling, idioms, Phrasal Verbs, and natural connected speech.",
        "style": "Jake in Musa's NYC Lounge—ALWAYS speaking strictly in the member's currently active Spoken Vibe (Articulate, Casual, Witty, Executive, or Diplomatic) using natural contractions, ellipsis, spoken fillers, and organic flow."
    },
    "trio": {
        "name": "The NYC Trio",
        "icon": "🗽",
        "title": "🗽 All 3 Hosts (Trio Table)",
        "role": "Full NYC Lounge Ensemble",
        "register": "articulate",
        "voice": "en-US-AndrewMultilingualNeural",
        "style": "Marcus, Chloe, and Jake chatting together—strictly locked to the member's active Spoken Vibe."
    }
}

CAST_PRESETS: Dict[str, Dict[str, Any]] = {
    "trio": {
        "label": "🗽 Trio: Marcus, Chloe & Jake (3 Hosts)",
        "short": "🗽 Trio (3)",
        "hosts": ["marcus", "chloe", "jake"],
        "count": 3
    },
    "duo_mc": {
        "label": "🧔‍♂️👩🏼‍💼 Duo: Marcus & Chloe (2 Hosts)",
        "short": "🧔‍♂️👩🏼‍💼 Marcus & Chloe (2)",
        "hosts": ["marcus", "chloe"],
        "count": 2
    },
    "duo_cj": {
        "label": "👩🏼‍💼🧢 Duo: Chloe & Jake (2 Hosts)",
        "short": "👩🏼‍💼🧢 Chloe & Jake (2)",
        "hosts": ["chloe", "jake"],
        "count": 2
    },
    "duo_mj": {
        "label": "🧔‍♂️🧢 Duo: Marcus & Jake (2 Hosts)",
        "short": "🧔‍♂️🧢 Marcus & Jake (2)",
        "hosts": ["marcus", "jake"],
        "count": 2
    },
    "marcus": {
        "label": "🧔‍♂️ Solo: Marcus Only (1 Host)",
        "short": "🧔‍♂️ Marcus (1)",
        "hosts": ["marcus"],
        "count": 1
    },
    "chloe": {
        "label": "👩🏼‍💼 Solo: Chloe Only (1 Host)",
        "short": "👩🏼‍💼 Chloe (1)",
        "hosts": ["chloe"],
        "count": 1
    },
    "jake": {
        "label": "🧢 Solo: Jake Only (1 Host)",
        "short": "🧢 Jake (1)",
        "hosts": ["jake"],
        "count": 1
    }
}

DEFAULT_PERSONA = "trio"

REGISTERS_MAP: Dict[str, str] = {
    "articulate": "🎙️ Articulate",
    "casual": "☕ Casual",
    "witty": "🎭 Witty",
    "executive": "💼 Executive",
    "diplomatic": "🤝 Diplomatic"
}

REGISTER_DESCRIPTIONS: Dict[str, str] = {
    "articulate": "🎙️ ARTICULATE & CULTURALLY SHARP (SPOKEN): Eloquent yet 100% spoken NYC flow—always use natural spoken contractions ('it's', 'that's', 'would've', 'don't'), thoughtful fillers ('Look,', 'Frankly,', 'The thing is,'), and conversational clefts ('What really strikes me is...').",
    "casual": "☕ EVERYDAY CASUAL & STREET-SMART (SPOKEN): Warm, relaxed NYC hangout speech—heavy on spoken contractions and reductions ('gonna', 'wanna', 'gotta', 'kinda', 'sorta'), initial ellipsis ('Sounds wild', 'Makes sense'), and everyday fillers ('I mean,', 'you know,', 'honestly,').",
    "witty": "🎭 WITTY, PLAYFUL & DRY (SPOKEN): Sharp New York banter with spoken contractions, dry fillers ('Wait—hear me out,', 'I mean, let's be real,'), punchy ellipsis ('Hard pass on that', 'Bold move'), and conversational tails.",
    "executive": "💼 HIGH-STAKES EXECUTIVE & BOARDROOM (SPOKEN): Confident, unscripted American leadership speech—natural spoken contractions ('we're', 'it's', 'won't'), strategic discourse markers ('Look, at the end of the day,', 'Here's the bottom line,'), and crisp spoken framing.",
    "diplomatic": "🤝 WARM, EMPATHETIC & DIPLOMATIC (SPOKEN): Tactful, high-EQ spoken American English—rich in spoken softeners and hedging fillers ('I totally see where you're coming from,', 'Correct me if I'm off base, but...', 'for the most part')."
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
   - Distinguish tense vs. lax vowels (/iː/ vs /ɪ/, /uː/ vs /ʊ/, /æ/ vs /ɛ/) and pre-fortis clipping.
   - Always highlight Nuclear (Sentence) Stress in ALL CAPS, thought group pauses with (|), and intonation contours (↘ / ↗).
   - In text turns: Build the acoustic rhythm map on the POLISHED AMERICAN RECAST (`tts_clean_target`), NOT on the user's non-native phrasing.

2. LEXICAL PRIMING & B2-TO-C2 UPGRADES:
   - Provide natural American collocations and phrasal verbs.
   - For every expression taught, include:
     * Flat B2 Foil (What intermediate learners say) ➔ Native C2 Frame
     * 🎯 When to reach for it (The trigger moment)
     * 🔗 Colligation Pattern (Prepositions, verb forms)
     * 💬 Natural follow-up line

3. MULTI-HOST INTERACTIVE REALISM:
   - Strictly obey the requested number of hosts in `chat_turns`.
   - When 2 (Duo) or 3 (Trio) hosts are active, they MUST interact with each other (agreeing, teasing, pushing back, or building on each other's point) as well as responding to Musa!
   - Never sound like an AI, textbook, or academic lecturer. Zero technical jargon.
""".strip()

GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — Detailed Member's Handbook</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your private Manhattan club for mastering <b>Spoken American English (C1/C2)</b>.\n\n"
        "<blockquote expandable>🗽 <b>1. Choose Your Table Cast (1, 2, or 3 Hosts)</b>\n"
        "Tap <code>👥 Chat Hosts</code> anytime to customize your conversation:\n"
        "• <b>👤 Solo (1 Host):</b> Chat 1-on-1 with 🧔‍♂️ <b>Marcus</b>, 👩🏼‍💼 <b>Chloe</b>, or 🧢 <b>Jake</b>.\n"
        "• <b>👥 Duo (2 Hosts):</b> Dynamic 2-person banter (<i>Marcus & Chloe</i>, <i>Chloe & Jake</i>, or <i>Marcus & Jake</i>).\n"
        "• <b>🗽 Trio (All 3 Hosts):</b> Full roundtable where all three hosts react to you and debate each other live!</blockquote>\n\n"
        "<blockquote expandable>🎛️ <b>2. Action Bar Controls (Under Every Reply)</b>\n"
        "• <code>🎧 Hear Hosts</code>: Hear the reply spoken aloud with each host's real voice and stereo positioning!\n"
        "• <code>🗣️ My Recast</code>: Hear the polished American version of your own message.\n"
        "• <code>🪞 Voice Mirror</code>: Plays <b>Your Voice ➔ Native 1.0x ➔ Slow Study 0.85x</b> with studio volume leveling.\n"
        "• <code>🎙️ Take 2</code>: Retry your sentence using the suggested upgrades.\n"
        "• <code>⭐ Save (1/2/3)</code>: Saves that specific expression to your Vault & Daily Review.</blockquote>"
    ),
    "ch1_xray": (
        "🔬 <b>Chapter 1: Coaching Notes, Self-Repair & The Voice Mirror</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🎙️ Articulate (Marcus):</b> Clear, expressive, rich in precise collocations.\n"
        "• <b>☕ Casual (Jake):</b> Fast, warm everyday talk with natural ellipsis and contractions.\n"
        "• <b>🎭 Witty (Chloe):</b> Dry understatement, playful irony, and clever rhetorical edge.\n"
        "• <b>🔄 Mid-Sentence Pivot:</b> Learn how native speakers adjust thoughts mid-stream without losing flow."
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The Conversation & Sparring Rooms</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🗣️ NYC Banter:</b> Unscripted everyday conversation with 1, 2, or 3 hosts.\n"
        "• <b>🥊 Devil's Advocate:</b> Pushback sparring to sharpen your persuasion.\n"
        "• <b>🎭 Storytelling Gym:</b> Pacing, setup, and landing the punchline.\n"
        "• <b>🎛️ Vibe Shifter:</b> Transform any thought across all three registers.\n"
        "• <b>🕵️‍♂️ Subtext Decoder:</b> Decode unspoken American hints and brush-offs.\n"
        "• <b>⚡ What to Say When...:</b> Get the exact instinctive phrase Americans reach for in any awkward second."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>Chapter 3: The NYC Podcast Lounge (NotebookLM Style)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "An immersive 3+ minute studio experience featuring <b>Marcus, Chloe, and Jake</b> with rapid-fire turns, natural interjections, and spatial audio stereo panning!"
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: Phrase Finder & Speaking Workouts</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>🔎 Collocation Finder:</b> Look up any word to explore its spoken American collocations.\n"
        "• <b>🔬 Native Text Miner:</b> Extract high-value spoken idioms from any pasted text.\n"
        "• <b>🔥 3-Phrase Challenge & Contrastive Stress Lab:</b> Active workouts tailored to your Vault."
    ),
    "ch5_vault_routine": (
        "🧠 <b>Chapter 5: Vault, Daily Review & Routine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Active Daily Review:</b> Send a voice note or sentence using your due phrases for instant coaching.\n"
        "• <b>Phrasebook (PDF) & Anki Deck:</b> Export your personal expressions anytime."
    )
}


# === CLEAN-SLATE V4 UNIFIED CONFIG EXPORTS ===
import os as _os
TELEGRAM_BOT_TOKEN = (
    globals().get('TELEGRAM_TOKEN')
    or globals().get('BOT_TOKEN')
    or globals().get('TELEGRAM_BOT_TOKEN')
    or _os.environ.get('TELEGRAM_TOKEN')
    or _os.environ.get('BOT_TOKEN')
    or _os.environ.get('TELEGRAM_BOT_TOKEN')
    or ''
).strip()
TELEGRAM_TOKEN = TELEGRAM_BOT_TOKEN
BOT_TOKEN = TELEGRAM_BOT_TOKEN
ADMIN_ID = int(globals().get('ADMIN_ID') or _os.environ.get('ADMIN_ID', '0') or 0)
DB_PATH = globals().get('DB_PATH') or _os.environ.get('CLUB_VAULT_DB') or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'club_vault.db')
GEMINI_MODEL = globals().get('GEMINI_MODEL') or _os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
