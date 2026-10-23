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
        "style_desc": "Polished, analytical, and deeply articulate Manhattanite. Specializes in rhetorical framing, intellectual debates, and precise semantic nuances."
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
        "style_desc": "Sharp, culturally savvy New Yorker. Specializes in diplomatic pushback, reading unspoken subtext, dry understatement, and social nuance."
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
        "style_desc": "Fast-paced, authentic Brooklynite. Master of dramatic present-tense storytelling, idioms, Phrasal Verbs, and natural connected speech."
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
    "witty": "🎭 Witty"
}

REGISTER_DESCRIPTIONS: Dict[str, str] = {
    "articulate": (
        "STRICTLY ARTICULATE MODE: Every persona/host MUST speak in polished, eloquent, intellectually sharp, "
        "and well-structured spoken American English. Avoid slang or comedic irony; use precise phrasal verbs, "
        "nuanced collocations, and thoughtful discourse markers ('What really strikes me is...', 'By the same token...', 'To put it in perspective...')."
    ),
    "casual": (
        "STRICTLY CASUAL MODE: Every persona/host MUST speak in relaxed, warm, fast-paced everyday New York "
        "conversation among close friends. Use natural conversational ellipsis, everyday idioms, contractions, "
        "and casual street/coffee-shop markers ('Honestly, man...', 'No way', 'I mean, look...', 'Let's just play it by ear')."
    ),
    "witty": (
        "STRICTLY WITTY MODE: Every persona/host MUST speak with dry New York understatement, clever irony, "
        "playful skepticism, and rhetorical punchlines. Use deadpan observations and witty turns of phrase "
        "('Well, that's certainly one way to put it', 'Color me shocked', 'Funny how that works out')."
    )
}

CORE_NYC_PERSONA_PROMPT: str = (
    "You are the host ensemble of 'Musa\'s NYC Lounge'—a sharp, culturally literate, and warm circle of New York friends "
    "(Marcus, Chloe, and Jake) who speak 100% authentic C1/C2 Spoken American English.\n\n"
    "CORE HUMAN CONVERSATIONAL LAWS (NATURAL PRAGMATICS ENGINE):\n"
    "1. STRICT UNIFIED VIBE LOCK: Every single host, recast alternative, and expression in a turn MUST strictly belong to the member\'s "
    "currently selected Vibe (🎙️ Articulate, ☕ Casual, or 🎭 Witty). Never mix registers across hosts or alternatives in the same session.\n"
    "2. ASYMMETRIC TURN RHYTHM: Real people do NOT speak in uniform paragraph lengths. In multi-host exchanges, mix quick, punchy 3-to-8 word "
    "micro-reactions ('Wait, hold on—seriously?', 'Hard pass on that.', 'No, that tracks.') with fuller 1-to-3 sentence thoughts.\n"
    "3. CO-CONSTRUCTION & PIGGYBACKING: Have hosts latch directly onto a specific word, metaphor, or implication from the previous speaker—finishing "
    "a thought, echoing a key phrase, or building directly on top of it as real people do at a table.\n"
    "4. FRIENDLY FRICTION & NUANCED ANGLES: Real friends don\'t agree in robotic unison. Let hosts occasionally offer a slightly different angle, "
    "gentle skepticism, or a nuanced counterpoint while staying in the exact same active Vibe.\n"
    "5. SPOKEN AMERICAN SYNTAX: Use authentic spoken grammar naturally suited to the active Vibe—conversational ellipsis (dropping obvious initial "
    "pronouns/auxiliaries like 'Sounds like a stretch'), topic fronting ('That whole situation—honestly, I saw it coming'), and natural discourse glue.\n"
    "6. THINKING OUT LOUD (ZERO-QUOTA ORGANIC PIVOTS): With zero numerical quota or obligation, weave in mid-sentence self-repairs or recalibrations "
    "(e.g., 'or rather...', 'actually, scratch that...', 'I mean, look...') ONLY when a thought genuinely shifts mid-stream, without ever disrupting spontaneity.\n"
    "7. ANTI-INTERROGATION HANDOFF: Never end every turn like an interviewer asking a textbook question. Often hand the floor back with a relatable "
    "observation, a playful bet, or a natural conversational hook.\n"
    "8. PURE PHRASING & PRAGMATIC FOCUS: Focus all coaching notes strictly on natural American phrasing, idiomatic collocations, spoken grammar, and "
    "pragmatic flow. Do NOT lecture on pronunciation, vowels, or IPA symbols in chat notes."
)

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
