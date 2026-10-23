# -*- coding: utf-8 -*-
"""
config.py — Configuration, Linguistic Registers & Contextual Naturalness Calibration
Musa's NYC Lounge (24/7 Relay Architecture)
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
    """Supports multiple comma-separated Gemini keys or fallback env vars for high availability."""
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

# Dual-DB Architecture Paths (Lightweight User DB for Telegram Pin + Read/FTS5 Corpus DB)
USER_DB_FILENAME: str = "musa_user_data_v3.db"
CORPUS_DB_FILENAME: str = "musa_corpus_fts5.db"

USER_DB_PATH: str = os.getenv("USER_DB_PATH", USER_DB_FILENAME)
CORPUS_DB_PATH: str = os.getenv("CORPUS_DB_PATH", CORPUS_DB_FILENAME)

TEMP_DIR: str = os.path.join(tempfile.gettempdir(), "musa_nyc_lounge_media")
os.makedirs(TEMP_DIR, exist_ok=True)

# 5.3-Hour Relay Cycle (19,080 seconds)
RELAY_MAX_RUNTIME_SECONDS: int = int(5.3 * 3600)
PERIODIC_BACKUP_SECONDS: int = 7200  # Every 2 hours

# Gemini Model Fallback Chain (100% Gemini, zero Groq)
GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

# Edge-TTS US Neural Voices
VOICE_MALE_HOST: str = "en-US-AndrewNeural"
VOICE_FEMALE_HOST: str = "en-US-AvaNeural"

# =====================================================================
# The 3 Living Spoken Registers
# =====================================================================
REGISTERS_MAP: Dict[str, str] = {
    "articulate": "[🎙️ Articulate / Natural]",
    "casual": "[☕ Casual / Everyday]",
    "witty": "[🎭 Witty / Ironic]"
}

REGISTER_DESCRIPTIONS: Dict[str, str] = {
    "articulate": "Smart, articulate spoken American English—fluid for podcasts, work discussions, deep ideas, or social banter without sounding stiff or robotic.",
    "casual": "Fast everyday NYC street & close-friends vernacular—natural ellipsis, contractions, and idiomatic flow.",
    "witty": "Dry understatement, irony, rhetorical sharpness, and clever punchlines."
}

CORE_NYC_PERSONA_PROMPT: str = """
You are the sharp, culturally literate, witty host of 'Musa's NYC Lounge'—an authentic Manhattan social and intellectual club.
Your mission is to elevate learners into effortless, native-like Spoken American English that feels 100% natural, spontaneous, and contextually calibrated.

CORE LINGUISTIC PRINCIPLES:
1. CONTEXTUAL FIT & SPONTANEITY (NO ARTIFICIAL WORD BANS):
   Do not arbitrarily ban workplace, professional, or elevated vocabulary. Instead, calibrate every word to the exact context and human moment.
   Whether the topic is a casual coffee run, a high-stakes workplace negotiation, or a deep intellectual debate, phrase things the way an articulate American ACTUALLY speaks out loud in that setting—never like a stiff textbook, a literal translation, or a robotic AI essay.
   Favor natural spoken grammar (Heads & Tails, conversational hedging, ellipsis), crisp phrasal verbs, authentic collocations, and living discourse markers ('Mind you', 'The thing is', 'Funny enough', 'Wait, seriously?', 'Fair enough, though...').

2. THE 3 LIVING REGISTERS:
   Show how any thought adapts across 3 natural spoken vibrations:
   - [🎙️ Articulate / Natural]: Polished, smart spoken flow (ideal for articulate discussions, podcasts, and sharp workplace/social communication).
   - [☕ Casual / Everyday]: Relaxed, fast-paced talk among close American friends.
   - [🎭 Witty / Ironic]: Dry humor, understatement, playful pushback, and clever subtext.

3. 3-PART LEXICAL FRAMES (CONVERSATIONAL FRAMES):
   Never teach isolated words. Every suggested upgrade must include:
   - The Chunk + Register & Tone Tag
   - 🎯 Trigger: The exact psychological, professional, or social moment a native speaker reaches for it.
   - 🔗 Co-text & Pattern: Which verbs, prepositions, or polarity (negative/positive) naturally glue to it.
   - 💬 Typical Follow-up: The natural second sentence an American says right after it to close the thought.

4. US CONNECTED SPEECH & ACOUSTIC PROSODY:
   Break down how sentences actually sound in fast American speech:
   - Flap-T (t/d between vowels sounding like a quick tap 'd', e.g., 'what about it' -> 'whaddaboudit').
   - Schwa reductions (/ə/ in unstressed function words: to -> 'tuh', for -> 'fer', of -> 'uhv').
   - Consonant-to-vowel linking, palatalization ('did you' -> 'did-ja', 'got you' -> 'got-cha'), and primary stress placement (CAPS) with intonation arrows (↘ / ↗).

5. UNNATURALNESS & L1 INTERFERENCE DETECTOR:
   Even if the user's sentence is grammatically correct, if an American wouldn't naturally phrase it that way in that specific context, flag it as [🤖 Stiff / L1 Transfer] or [🎯 Context Mismatch] and show the natural native recast.
""".strip()

# =====================================================================
# Detailed In-Telegram Interactive User Guide (6 Deep-Dive Chapters)
# =====================================================================
GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — Detailed Interactive User Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to <b>Musa's NYC Lounge</b>, a private Manhattan conversational club engineered to take you from advanced English (B2/C1) to <b>Native-like Spoken American Intuition (C2)</b>.\n\n"
        "🧭 <b>How to Navigate This Guide in Telegram:</b>\n"
        "Use the chapter buttons below to read the detailed breakdown of every tool, or expand the quick summaries right here:\n\n"
        "<blockquote expandable>🔬 <b>Chapter 1: The 2-Layer Response & Expandable X-Ray</b>\n"
        "Every reply in the Lounge separates <b>Conversation Flow</b> from <b>Linguistic Surgery</b>:\n"
        "• <b>Top Half (Visible Immediately):</b> Natural, witty American banter that responds to your ideas without interrupting the vibe.\n"
        "• <b>Stealth Input Flood (<i>Italics</i>):</b> Words in <i>italics</i> inside the host's reply are chunks secretly pulled from your Vault & Error Profile so your brain absorbs them naturally.\n"
        "• <b>Bottom Half (Collapsible X-Ray):</b> Tap <code>🔬 NYC Linguistic & Acoustic X-Ray</code> to reveal your 3-Register rewrites, US Connected Speech map, Surgical Diagnostics, and 3 numbered Lexical Frames.</blockquote>\n\n"
        "<blockquote expandable>🎚️ <b>Chapter 2: Contextual Calibration & The 3 Living Registers</b>\n"
        "Language isn't about banning words—it's about <b>Spontaneity & Contextual Fit</b>. Whether you're discussing work, life, or philosophy, every thought is calibrated across:\n"
        "• <b>[🎙️ Articulate / Natural]:</b> Smart podcast, workplace, & coffeehouse discussion—clear, phrasal-verb rich, effortless.\n"
        "• <b>[☕ Casual / Everyday]:</b> Fast NYC street & close-friends vernacular with ellipsis and contractions.\n"
        "• <b>[🎭 Witty / Ironic]:</b> Dry humor, understatement, and clever rhetorical edge.</blockquote>\n\n"
        "👇 <b>Tap any chapter button below for the full step-by-step guide:</b>"
    ),
    "ch1_xray": (
        "🔬 <b>Chapter 1: Mastering the 2-Layer UI, Atomic Saves & Stealth Flood</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. Why Two Layers?</b>\n"
        "Traditional bots kill conversation by dumping walls of grammar corrections. Here, the host replies naturally at the top while tucking the full diagnostic lab inside an expandable box.\n\n"
        "<b>2. Incidental Input Flood (Stealth Injection Queue):</b>\n"
        "• Whenever you save a chunk or make a recurring mistake, it enters a background queue.\n"
        "• Over your next 4 conversations or podcasts, the bot secretly weaves those exact chunks into its own replies (highlighted in <i>italics</i>).\n"
        "• Once you encounter a chunk 4 times in live context, it is marked as contextually acquired!\n\n"
        "<b>3. The Atomic Action Bar (Under Every Turn):</b>\n"
        "• <code>🔊 US Native Audio</code>: Generates neural American speech for the cleanest rewrite of your message on demand.\n"
        "• <code>🪞 Acoustic Mirror</code>: Appears whenever you send a voice note (see Chapter 3).\n"
        "• <code>⭐ Save (1)</code> / <code>(2)</code> / <code>(3)</code>: Atomically saves ONLY that specific numbered 3-Part Lexical Frame into your SQLite Vault and schedules it in SM-2 SRS.\n"
        "• <code>🎲 New Topic</code>: Generates a fresh AI scenario calibrated to your CEFR level.\n"
        "• <code>🎚️ Shift Vibe</code>: Cycles your default register between Articulate, Casual, and Witty."
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The 5 Conversational & Pragmatic Studios</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. 🗣️ NYC Spontaneous Banter:</b>\n"
        "Unscripted everyday talk on any topic (life, work, culture, ideas). The host pushes the conversation forward like a sharp NYC friend.\n\n"
        "<b>2. 🎭 Storytelling & Wit Gym:</b>\n"
        "80% of real life is telling stories. Tell a 1-minute story (real or made up). The bot coaches you on:\n"
        "• <b>Historical Present:</b> Shifting to present tense for immediacy (<i>'So I walk into the office, right, and he just stares at me...'</i>).\n"
        "• <b>Setup & Punchline Pacing:</b> Using conversational hooks (<i>'Mind you', 'Next thing I know', 'Here's the kicker'</i>).\n\n"
        "<b>3. 🎛️ Register Shifter Lab:</b>\n"
        "Send any sentence or idea to see how it naturally transforms across <b>[🎙️ Articulate]</b>, <b>[☕ Casual]</b>, and <b>[🎭 Witty]</b>.\n\n"
        "<b>4. 🕵️‍♂️ Subtext & Irony Decoder:</b>\n"
        "Send something someone said to you or a tricky social/workplace line. The bot decodes the unspoken American subtext and gives you 3 sharp ways to reply.\n\n"
        "<b>5. ⚡ Situation-to-Gambit Translator:</b>\n"
        "Describe any social or professional situation (in <b>English or Arabic</b>). You get the exact instinctive American conversational gambits for that moment."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>Chapter 3: Acoustic Mirror, Connected Speech & Podcast Studio</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. Raw Voice Analysis (Gemini 2.5 Native Audio):</b>\n"
        "When you send a voice note, the bot listens directly to your raw audio waves—analyzing not just grammar, but:\n"
        "• <b>Flap-T:</b> Pronouncing intervocalic T/D as a quick tap (<i>'water', 'what about it' ➔ 'whaddaboudit'</i>).\n"
        "• <b>Schwa (/ə/) Reductions:</b> Weakening unstressed function words (<i>'to' ➔ 'tuh', 'of' ➔ 'uhv'</i>).\n"
        "• <b>Linking & Intonation:</b> Primary word stress (CAPS) and pitch direction (↘ / ↗).\n\n"
        "<b>2. 🪞 Back-to-Back Acoustic Mirror (FFmpeg):</b>\n"
        "Tap <code>🪞 Acoustic Mirror</code> under any voice reply. FFmpeg builds a single audio track:\n"
        "<code>[1. Your Original Voice] ➔ 🔔 ➔ [2. US Native 1.0x Pace] ➔ 🔔 ➔ [3. US Native 0.85x Slow Study Pace]</code>\n"
        "Hearing your voice immediately followed by the native model exposes rhythm and vowel differences in one second!\n\n"
        "<b>3. 🎙️ Dual-Host NYC Podcast Studio (Andrew & Ava):</b>\n"
        "• <b>Auto Episode from Vault:</b> Writes and mixes a multi-turn radio dialogue between Andrew & Ava weaving in your saved Vault chunks and recurring fixes, pausing mid-show to unpack why an expression works.\n"
        "• <b>Custom Topic Producer:</b> Give Andrew & Ava any topic to debate while injecting your vocabulary.\n"
        "• <b>'You Are the Guest':</b> Jump on-air as the guest in a live NYC interview."
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: FTS5 Corpus Lookup, Text Miner & Generative AI Drills</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. ⚡ Instant SQLite FTS5 Collocation Lookup (under 5ms):</b>\n"
        "Open <code>🔬 Text Miner & Corpus</code> ➔ <code>Instant SQLite Lookup</code> and type 1–3 words. Searches both the FTS5 Corpus and your personal Vault in milliseconds.\n\n"
        "<b>2. 🔬 Native Text & Subtext Miner:</b>\n"
        "Paste any paragraph from a book, article, movie script, or email. The bot extracts the 3 best native chunks as full 3-Part Frames with atomic <code>⭐ Save</code> buttons.\n\n"
        "<b>3. 🔥 Generative Forced Output Challenge:</b>\n"
        "Pulls 3 target chunks from your Vault & Error Profile and invents a fresh NYC scenario tailored to your level. Reply with ONE voice note or text naturally using all 3 chunks.\n\n"
        "<b>4. 🎙️ Generative US Connected Speech Shadowing:</b>\n"
        "Generates a fresh target sentence, an acoustic Flap-T map, and a native audio clip. Record yourself shadowing the exact rhythm.\n\n"
        "<b>5. 👂 Generative Fast Micro-Dictation Decoder:</b>\n"
        "Sends you a freshly generated fast American audio clip with NO text. Type what you heard to train your bottom-up listening ear."
    ),
    "ch5_vault_routine": (
        "🧠 <b>Chapter 5: 3-Part Lexical Frames, SM-2 SRS, Exports & Daily Routine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. What is a 3-Part Lexical Frame?</b>\n"
        "Every chunk saved in your Vault stores 3 horizontal dimensions so you never hesitate in real speech:\n"
        "• 🎯 <b>1. The Trigger:</b> What social/mental cue makes a native speaker reach for it.\n"
        "• 🔗 <b>2. Co-text & Pattern:</b> Which verbs, prepositions, or negative/positive polarity glue to it.\n"
        "• 💬 <b>3. Typical Follow-up:</b> The natural next sentence that closes the thought.\n\n"
        "<b>2. 🧠 In-Bot Smart SRS (SuperMemo-2):</b>\n"
        "Tap <code>🧠 Smart SRS Review</code> for 2-minute daily Cloze-deletion flashcards. Reveal the 3-Part Frame, listen to the audio, and grade yourself (<code>🔴 Hard 1d</code>, <code>🟡 Good</code>, <code>🟢 Easy</code>).\n\n"
        "<b>3. 📄 PDF & 🗂️ Anki CSV Exports:</b>\n"
        "From <code>📓 My Vault & Export</code>, download a beautifully formatted <b>WeasyPrint PDF book</b> of your frames or a semicolon-delimited <b>Anki CSV deck</b>.\n\n"
        "⏱️ <b>Recommended 15-Minute Daily C2 Routine:</b>\n"
        "• <b>Morning (3m):</b> Clear due cards in <code>🧠 Smart SRS Review</code> + listen to an auto-generated <code>🎙️ Podcast Episode</code>.\n"
        "• <b>Midday (7m):</b> Send 2–3 voice notes in <code>🗣️ NYC Banter</code> or <code>🎭 Storytelling Gym</code>, tap <code>🪞 Acoustic Mirror</code>, and save new frames via <code>⭐ Save (1/2/3)</code>.\n"
        "• <b>Evening (5m):</b> Complete one <code>🔥 Forced Output Challenge</code> or <code>👂 Micro-Dictation</code>."
    )
}
