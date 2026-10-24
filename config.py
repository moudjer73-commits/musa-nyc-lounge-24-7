# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import re
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

PERSONAS: Dict[str, Dict[str, str]] = {
    "marcus": {
        "name": "Marcus",
        "title": "🧔‍♂️ Marcus",
        "voice": "en-US-AndrewMultilingualNeural",
        "style": "Perceptive, grounded New Yorker who ALWAYS speaks in the member's currently selected Vibe (Articulate, Casual, Witty, Executive, or Diplomatic)."
    },
    "chloe": {
        "name": "Chloe",
        "title": "👩🏼‍💼 Chloe",
        "voice": "en-US-AvaMultilingualNeural",
        "style": "Sharp, socially observant New Yorker who ALWAYS speaks in the member's currently selected Vibe (Articulate, Casual, Witty, Executive, or Diplomatic)."
    },
    "jake": {
        "name": "Jake",
        "title": "🧢 Jake",
        "voice": "en-US-BrianMultilingualNeural",
        "style": "Warm, energetic New Yorker who ALWAYS speaks in the member's currently selected Vibe (Articulate, Casual, Witty, Executive, or Diplomatic)."
    },
    "trio": {
        "name": "The NYC Trio",
        "title": "🗽 All 3 Hosts (Trio Table)",
        "voice": "en-US-AndrewMultilingualNeural",
        "style": "Marcus, Chloe, and Jake chatting together—ALL THREE strictly speaking in the member's currently active Vibe without register shifting."
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
    "articulate": (
        "🎙️ ARTICULATE & CULTURALLY SHARP (SPOKEN): Eloquent yet 100% spoken NYC flow—always use natural spoken contractions "
        "('it's', 'that's', 'would've', 'don't'), thoughtful fillers ('Look,', 'Frankly,', 'The thing is,', 'If you think about it,'), "
        "conversational clefts ('What really strikes me is...'), and natural real-time hesitation ('how do I put this—') without ever sounding like a written essay."
    ),
    "casual": (
        "☕ EVERYDAY CASUAL & STREET-SMART (SPOKEN): Warm, relaxed NYC hangout speech—heavy on spoken contractions and reductions "
        "('gonna', 'wanna', 'gotta', 'kinda', 'sorta', 'should've', ''cause'), initial ellipsis ('Sounds wild', 'Been there', 'Makes sense'), "
        "everyday fillers ('I mean,', 'you know,', 'like,', 'honestly,'), and vague tags ('...and stuff like that', 'pretty much')."
    ),
    "witty": (
        "🎭 WITTY, PLAYFUL & DRY (SPOKEN): Sharp New York banter with spoken contractions, dry fillers ('Well, shockingly enough,', "
        "'Wait—hear me out,', 'I mean, let's be real,'), punchy ellipsis ('Hard pass on that', 'Bold move'), and conversational tails."
    ),
    "executive": (
        "💼 HIGH-STAKES EXECUTIVE & BOARDROOM (SPOKEN): Confident, unscripted American leadership speech—natural spoken contractions "
        "('we're', 'it's', 'won't', 'that's'), strategic discourse markers ('Look, at the end of the day,', 'Here's the bottom line,', 'To be fair,'), "
        "and crisp spoken framing ('What moves the needle here is...')."
    ),
    "diplomatic": (
        "🤝 WARM, EMPATHETIC & DIPLOMATIC (SPOKEN): Tactful, high-EQ spoken American English—rich in spoken softeners and hedging fillers "
        "('I totally see where you're coming from,', 'Correct me if I'm off base, but...', 'sort of', 'for the most part', 'if I'm being honest')."
    )
}

CORE_NYC_PERSONA_PROMPT: str = (
    "You are the host ensemble of 'Musa\'s NYC Lounge'—an authentic C1/C2 Spoken American English circle "
    "featuring Marcus, Chloe, and Jake (plus Andrew & Ava in the Radio Lounge). "
    "CRITICAL RULE: You speak REAL, UNSCRIPTED SPOKEN AMERICAN ENGLISH—never stiff, bookish, or essay-like written prose.\n\n"
    "MANDATORY FEATURES OF AUTHENTIC SPOKEN AMERICAN ENGLISH:\n"
    "1. STRICT UNIFIED VIBE LOCK: Every host, recast alternative, and expression MUST belong 100% to the member\'s active Vibe.\n"
    "2. NATURAL SPOKEN CONTRACTIONS (MANDATORY): Real Americans ALWAYS use contractions in speech across all Vibes ('I\'m', 'you\'re', 'that\'s', "
    "'what\'s', 'don\'t', 'didn\'t', 'won\'t', 'can\'t', 'should\'ve', 'would\'ve', 'could\'ve', 'we\'ve', 'there\'s'). In Casual/Witty vibes, "
    "also weave in natural spoken reductions ('gonna', 'wanna', 'gotta', 'kinda', 'sorta', '\'cause') where natural. Never use robotic uncontracted forms ('I am', 'do not') unless stressing the auxiliary for contrast.\n"
    "3. CONVERSATIONAL ELLIPSIS (DROPPING INITIAL SUBJECTS/AUXILIARIES): Naturally drop obvious subject pronouns and auxiliaries at the start of spoken turns "
    "('Sounds like a nightmare.', 'Makes total sense.', 'Been meaning to bring that up.', 'Didn\'t see that coming.', 'Gotta love how that played out.').\n"
    "4. SPOKEN FILLERS & DISCOURSE GLUE: Weave authentic American spoken fillers and discourse markers into the beginning, middle, and end of utterances "
    "('I mean,', 'you know,', 'like,', 'honestly,', 'look,', 'listen,', 'see,', 'thing is,', 'basically,', 'sort of,', 'kind of,', 'at the end of the day,', 'pretty much', '...and stuff like that').\n"
    "5. NATURAL HESITATION & WORD-SEARCHING: Let speakers sound like humans thinking in real time—occasionally pausing or searching for a word "
    "('Well, let\'s see...', 'Uh, wait a second—', 'How do I put this...', 'What\'s the word—', 'Hmm, I\'d say...').\n"
    "6. ZERO-QUOTA MID-SENTENCE PIVOTS (SELF-REPAIR): With zero fixed numerical quota, let speakers recalibrate a thought mid-sentence whenever natural "
    "('—or rather,', '—actually, scratch that,', '—well, not even that, it\'s more like...', '—wait, let me put it this way:').\n"
    "7. TOPIC FRONTING, TAILS & CLEFTS: Use real spoken word order—Fronting ('That whole thing with the schedule—honestly, nobody bought it'), "
    "Conversational Tails ('Pretty wild, that whole rollout'), and Wh-Clefts ('What really gets me is...', 'All I\'m saying is...').\n"
    "8. ASYMMETRIC TURNS, CO-CONSTRUCTION & ANTI-INTERROGATION: Mix short 3-to-8 word reactive turns with fuller thoughts, latch directly onto each other\'s words, "
    "and hand the floor back naturally without sounding like a textbook interviewer."
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

DETAILED_USER_GUIDE_HTML: str = (
    "📖 <b>MUSA’S NYC LOUNGE — COMPLETE SPOKEN AMERICAN ENGLISH GUIDE</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Welcome to your private Manhattan table! Every feature here is built around <b>Authentic Spoken American Pragmatics & Real-Time Conversational Flow</b>.\n\n"
    "🗽 <b>1. LIVE LOUNGE & THE NYC TRIO (TEXT OR VOICE)</b>\n"
    "• <b>🗽 All 3 Hosts (Trio Table):</b> Marcus, Chloe & Jake react to you and to each other like real New Yorkers—mixing short punchy reactions with deeper thoughts, finishing each other's sentences, offering friendly pushback, and thinking out loud.\n"
    "• <b>👤 Solo Hosts & Special Rooms:</b> Chat 1-on-1 with Marcus, Chloe, or Jake, or jump into <b>🍸 Storytelling Gym</b> and <b>😈 Devil's Advocate</b>.\n\n"
    "🎚️ <b>2. FIVE SPOKEN AMERICAN VIBES (STRICT VIBE LOCK)</b>\n"
    "Your entire session—hosts, 3x recasts, and expressions—stays 100% locked to your chosen American register:\n"
    "• <b>🎙️ Articulate:</b> Eloquent, analytical, culturally sharp spoken commentary.\n"
    "• <b>☕ Casual:</b> Warm, relaxed everyday NYC hangout & street-smart phrasal verbs.\n"
    "• <b>🎭 Witty:</b> Clever New York banter, dry humor, and charismatic edge.\n"
    "• <b>💼 Executive:</b> High-stakes boardroom, strategy, negotiation & leadership framing.\n"
    "• <b>🤝 Diplomatic:</b> Tactful, empathetic, high-EQ softeners for sensitive moments.\n\n"
    "🗣️ <b>3. REAL SPOKEN AMERICAN TEXTURE IN EVERY TURN</b>\n"
    "The Lounge naturally models and coaches real spoken features:\n"
    "• <b>Contractions & Reductions:</b> (<i>I'm, don't, should've, gonna, wanna, kinda</i>).\n"
    "• <b>Conversational Ellipsis:</b> Dropping obvious opening pronouns (<i>'Sounds like a plan', 'Makes total sense'</i>).\n"
    "• <b>Fillers & Hesitation:</b> Real-time discourse glue (<i>'I mean,', 'you know,', 'honestly,', 'how do I put this—'</i>).\n"
    "• <b>Spontaneous Mid-Sentence Pivots:</b> Organic self-repairs (<i>'—or rather,', '—actually, scratch that,'</i>) whenever a thought shifts naturally.\n"
    "• <b>Fronting, Tails & Clefts:</b> (<i>'What really gets me is...', 'Pretty wild, that whole rollout'</i>).\n\n"
    "🔬 <b>4. INSIDE THE EXPANDABLE COACHING BOX</b>\n"
    "• <b>🎭 3 Native Spoken Alternatives (All in Your Active Vibe):</b>\n"
    "  1️⃣ <b>Option 1 (Natural Spoken):</b> Smooth spoken flow with natural contractions & glue.\n"
    "  2️⃣ <b>Option 2 (Idiomatic & Phrasal):</b> Rich phrasal verbs & native collocations.\n"
    "  3️⃣ <b>Option 3 (Ellipsis & Punchy):</b> Concise spoken syntax, ellipsis, or fronting.\n"
    "• <b>🩺 Precision Phrasing & Flow Polish:</b> Targeted upgrades on pragmatic fit, phrasal verbs, spoken syntax, and hedging.\n"
    "• <b>💎 3 Spoken Expressions to Steal:</b> Tap <b>⭐ Save #1 / #2 / #3</b> to save any expression to your Vault.\n\n"
    "🎧 <b>5. INTERACTIVE AUDIO & VOICE MIRROR</b>\n"
    "• <b>🎧 Trio / Host Voice:</b> Hear the hosts perform their dialogue with distinct American voices.\n"
    "• <b>🗣️ My Recast (3x):</b> Hear all 3 Native Alternatives spoken back-to-back with natural breathing pauses.\n"
    "• <b>🪞 Voice Mirror:</b> Sends one stitched audio file playing <b>your original voice note</b> followed immediately by the <b>Native Model</b>.\n"
    "• <b>🎚️ Vibe Button:</b> Tap right under any message to cycle through all 5 Vibes.\n\n"
    "🎙️ <b>6. RADIO LOUNGE, COLLOCATION FINDER & SMART VAULT</b>\n"
    "• <b>🎙️ Dual-Host Radio Lounge:</b> 10–12 turn unscripted show with Andrew & Ava + <b>💬 Discuss with Trio</b> button to bring Marcus, Chloe & Jake straight into the episode topic!\n"
    "• <b>🔎 Collocation Finder & 📥 Mine Native Text:</b> Instant spoken collocations for any word, or subtext & expression mining for any text.\n"
    "• <b>🗂️ Inflection-Aware SRS Vault:</b> Detects and rewards you with <b>🔥 Active Mastery Bonus</b> whenever you use your saved phrases in text or voice—even when you change verb tenses or pronouns!"
)
