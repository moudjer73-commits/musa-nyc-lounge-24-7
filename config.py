# -*- coding: utf-8 -*-
"""
config.py — Configuration, Linguistic Registers, Anti-Corporate Guardrails & Prompts
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

# Gemini Model Fallback Chain
GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-pro"
]

# Edge-TTS US Neural Voices
VOICE_MALE_HOST: str = "en-US-AndrewNeural"
VOICE_FEMALE_HOST: str = "en-US-AvaNeural"

# =====================================================================
# Linguistic Registers (Strict replacement for formal/professional/casual)
# =====================================================================
REGISTERS_MAP: Dict[str, str] = {
    "articulate": "[🎙️ Articulate / Natural]",
    "casual": "[☕ Casual / Everyday]",
    "witty": "[🎭 Witty / Ironic]"
}

REGISTER_DESCRIPTIONS: Dict[str, str] = {
    "articulate": "Smart podcast & intellectual coffeehouse English — fluid, phrasal-verb rich, zero textbook stiffness.",
    "casual": "Fast everyday NYC street & close-friends vernacular — natural ellipsis, contractions, and idiomatic flow.",
    "witty": "Dry understatement, irony, rhetorical sharpness, and clever punchlines."
}

# Strict Anti-Corporate & Anti-Textbook Negative Constraints
BANNED_CORPORATE_CLICHES: List[str] = [
    "utilize", "delve into", "foster", "furthermore", "moreover", "navigate challenges",
    "in conclusion", "it is important to note", "testament to", "tapestry", "synergy",
    "leverage", "circle back", "touch base", "game-changer", "seamlessly", "paradigm",
    "endeavor", "commence", "pertaining to", "henceforth", "facilitate", "optimize"
]

CORE_NYC_PERSONA_PROMPT: str = f"""
You are the sharp, culturally literate, witty host of 'Musa's NYC Lounge'—an authentic Manhattan social and intellectual club.
Your mission is to elevate advanced learners (B2/C1/C2) into effortless, native-like Spoken American English (General Everyday English).

STRICT LINGUISTIC & REGISTER RULES:
1. ZERO CORPORATE / HR / TEXTBOOK SPEAK:
   Never use or suggest stiff corporate or academic clichés such as: {', '.join(BANNED_CORPORATE_CLICHES)}.
   Always favor crisp phrasal verbs, authentic collocations, spoken discourse markers ('Mind you', 'The thing is', 'Funny enough', 'Wait, seriously?', 'Fair enough, though...'), and spoken grammar (Heads & Tails, natural hedging).

2. THE 3 LIVING REGISTERS (NEVER USE 'Formal' OR 'Professional'):
   Every rewrite and lexical chunk MUST be calibrated to one of these 3 registers:
   - [🎙️ Articulate / Natural]: Smart podcast / articulate social conversation.
   - [☕ Casual / Everyday]: Fast, relaxed everyday talk among close American friends.
   - [🎭 Witty / Ironic]: Dry humor, understatement, playful pushback, and clever subtext.

3. 3-PART LEXICAL FRAMES (CONVERSATIONAL FRAMES):
   Never teach isolated words. Every suggested upgrade must include:
   - The Chunk + Register & Tone Tag
   - 🎯 Trigger: The exact psychological or social moment a native speaker reaches for it.
   - 🔗 Co-text & Pattern: Which verbs, prepositions, or polarity (negative/positive) naturally glue to it.
   - 💬 Typical Follow-up: The natural second sentence an American says right after it to close the thought.

4. US CONNECTED SPEECH & ACOUSTIC PROSODY:
   Break down how sentences actually sound in fast American speech:
   - Flap-T (t/d between vowels sounding like a quick tap 'd', e.g., 'what about it' -> 'whaddaboudit').
   - Schwa reductions (/ə/ in unstressed function words: to -> 'tuh', for -> 'fer', of -> 'uhv').
   - Consonant-to-vowel linking, palatalization ('did you' -> 'did-ja', 'got you' -> 'got-cha'), and primary stress placement (CAPS) with intonation arrows (↘ / ↗).

5. UNNATURALNESS & L1 INTERFERENCE DETECTOR:
   Even if the user's sentence is 100% grammatically correct, if an American wouldn't naturally phrase it that way, flag it as [🤖 Stiff / L1 Transfer] and show the natural native recast.
""".strip()

MASTER_GUIDE_HTML: str = (
    "📖 <b>Musa's NYC Lounge — Official Master Guide</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Welcome to your private Manhattan conversational club for Spoken American Mastery. "
    "Tap any section below to expand:\n\n"
    "<blockquote expandable>🔬 <b>1. Two-Layer Chat & Stealth Input Flood</b>\n"
    "• <b>Top Layer (Natural Flow):</b> Witty, unscripted American conversation. Chunks in <i>italics</i> are secretly pulled from your Vault & Error Profile (<b>Stealth Input Flood</b>) so your brain acquires them naturally in context.\n"
    "• <b>Bottom Layer (Expandable X-Ray):</b> Tap to open your 3-Register Shifter (<b>[🎙️ Articulate]</b>, <b>[☕ Casual]</b>, <b>[🎭 Witty]</b>), Connected Speech Prosody, Surgical Diagnostics, and 3-Part Lexical Frames.\n"
    "• <b>Atomic Action Bar:</b>\n"
    "  └ <code>🔊 US Native Audio</code>: On-demand neural US speech.\n"
    "  └ <code>🪞 Acoustic Mirror</code>: Merges your raw voice + chime + 1.0x US native + 0.85x slow study pace.\n"
    "  └ <code>⭐ Save (1/2/3)</code>: Atomically saves a single 3-Part Lexical Frame to your Vault & SRS.</blockquote>\n\n"
    "<blockquote expandable>🗽 <b>2. Conversation, Storytelling & Pragmatic Studios</b>\n"
    "• <b>🗣️ Spontaneous Chat:</b> Unscripted everyday banter with collapsible X-Ray feedback.\n"
    "• <b>🎭 Storytelling & Wit Gym:</b> Master Historical Present, narrative pacing, setups, and punchlines.\n"
    "• <b>✍️ Register & Style Lab:</b> Transform any sentence across Articulate, Casual, and Witty registers.\n"
    "• <b>🕵️‍♂️ Subtext & Irony Decoder:</b> Decode unspoken American subtext, dry humor, and understatement.\n"
    "• <b>⚡ Situation-to-Gambit Translator:</b> Describe any social situation (in English or Arabic) to get the exact native conversational gambits.</blockquote>\n\n"
    "<blockquote expandable>🎙️ <b>3. Dual-Host Podcast, Text Miner & Active Drills</b>\n"
    "• <b>🎙️ Podcast Studio (Andrew & Ava):</b> Generates a 2-host MP3 episode weaving in your Vault chunks with mid-show commentary, or lets you join as the live guest.\n"
    "• <b>🔬 Text Miner & FTS5 Corpus:</b> Instant (<5ms) SQLite FTS5 collocation lookup + Native Text Chunk Miner.\n"
    "• <b>🎯 Active & Audio Drills:</b> Forced Output (use 3 target chunks in 1 reply), US Connected Speech Shadowing, and Fast Micro-Dictation decoding.</blockquote>\n\n"
    "<blockquote expandable>🧠 <b>4. Dual-DB Vault, SM-2 SRS & Exports</b>\n"
    "• <b>🧠 Smart SRS Review:</b> In-bot SM-2 spaced repetition with Cloze deletion cards and audio.\n"
    "• <b>📓 My Vault & Exports:</b> Export your 3-Part Lexical Frames anytime to a styled <b>PDF Report</b> or <b>Anki CSV Deck</b>.</blockquote>"
)



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
        "<blockquote expandable>🎚️ <b>Chapter 2: The 3 Living Registers (Zero Corporate Speak)</b>\n"
        "We banned stiff textbook/HR clichés (<i>utilize, delve into, foster, furthermore</i>). Instead, every thought is calibrated across:\n"
        "• <b>[🎙️ Articulate / Natural]:</b> Smart podcast & coffeehouse discussion—clear, phrasal-verb rich, effortless.\n"
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
        "• <code>🪞 Acoustic Mirror</code>: Appears whenever you send a voice note (see Chapter 4).\n"
        "• <code>⭐ Save (1)</code> / <code>(2)</code> / <code>(3)</code>: Atomically saves ONLY that specific numbered 3-Part Lexical Frame into your SQLite Vault and schedules it in SM-2 SRS.\n"
        "• <code>🎚️ Shift Vibe</code>: Cycles your default register between Articulate, Casual, and Witty."
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The 5 Conversational & Pragmatic Studios</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. 🗣️ NYC Spontaneous Banter:</b>\n"
        "Unscripted everyday talk. Drop a voice note or text about your day, an opinion, or a random thought. The host pushes the conversation forward like a sharp NYC friend.\n\n"
        "<b>2. 🎭 Storytelling & Wit Gym:</b>\n"
        "80% of real life is telling stories. Tell a 1-minute story (real or made up). The bot coaches you on:\n"
        "• <b>Historical Present:</b> Shifting to present tense for drama (<i>'So I walk into the office, right, and he just stares at me...'</i>).\n"
        "• <b>Setup & Punchline Pacing:</b> Using conversational hooks (<i>'Mind you', 'Next thing I know', 'Here's the kicker'</i>).\n\n"
        "<b>3. 🎛️ Register Shifter Lab:</b>\n"
        "Send any stiff or ordinary sentence to see how the exact same idea transforms across <b>[🎙️ Articulate]</b>, <b>[☕ Casual]</b>, and <b>[🎭 Witty]</b>.\n\n"
        "<b>4. 🕵️‍♂️ Subtext & Irony Decoder:</b>\n"
        "Send something someone said to you or a tricky social line. The bot decodes the unspoken American subtext (polite brush-offs, passive-aggressive hints, dry understatement) and gives you 3 sharp ways to reply.\n\n"
        "<b>5. ⚡ Situation-to-Gambit Translator:</b>\n"
        "Describe any social situation or feeling (in <b>English or Arabic</b>—e.g., <i>'Someone is bragging subtly and I want to change the subject smoothly'</i>). You get the exact instinctive American conversational gambits for that moment."
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
        "Tap <code>🪞 Acoustic Mirror</code> under any voice reply. FFmpeg builds a single 12-second audio track:\n"
        "<code>[1. Your Original Voice] ➔ 🔔 ➔ [2. US Native 1.0x Pace] ➔ 🔔 ➔ [3. US Native 0.85x Slow Study Pace]</code>\n"
        "Hearing your voice immediately followed by the native model exposes rhythm and vowel differences in one second!\n\n"
        "<b>3. 🎙️ Dual-Host NYC Podcast Studio (Andrew & Ava):</b>\n"
        "• <b>Auto Episode from Vault:</b> Writes and mixes a multi-turn radio dialogue between Andrew & Ava weaving in your saved Vault chunks and recurring fixes, pausing mid-show to unpack why an expression works.\n"
        "• <b>Custom Topic Producer:</b> Give Andrew & Ava any topic to debate while injecting your vocabulary.\n"
        "• <b>'You Are the Guest':</b> Jump on-air as the guest in a live NYC interview."
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: FTS5 Corpus Lookup, Text Miner & Active Drills</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. ⚡ Instant SQLite FTS5 Collocation Lookup (<5ms):</b>\n"
        "Open <code>🔬 Text Miner & Corpus</code> ➔ <code>Instant SQLite Lookup</code> and type 1–3 words (e.g., <i>'grain of salt'</i> or <i>'slack'</i>). Searches both the FTS5 Corpus and your personal Vault in milliseconds without waiting for AI generation.\n\n"
        "<b>2. 🔬 Native Text & Subtext Miner:</b>\n"
        "Paste any paragraph from a book, article, movie script, or tweet. The bot extracts the 3 best native chunks as full 3-Part Frames with atomic <code>⭐ Save</code> buttons.\n\n"
        "<b>3. 🔥 Forced Output Challenge (Active Fluency):</b>\n"
        "Pulls 3 target chunks from your Vault & Error Profile and gives you a real-life NYC scenario. Your challenge: reply with ONE voice note or text naturally using all 3 chunks.\n\n"
        "<b>4. 🎙️ US Connected Speech Shadowing:</b>\n"
        "Gives you a target sentence, an acoustic Flap-T map, and a native audio clip. Record yourself shadowing the exact rhythm.\n\n"
        "<b>5. 👂 Fast Micro-Dictation Decoder:</b>\n"
        "Sends you a fast, reduced American audio clip with NO text. Type what you heard to train your bottom-up listening ear."
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
