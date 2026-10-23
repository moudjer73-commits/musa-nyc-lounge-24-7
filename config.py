# -*- coding: utf-8 -*-
"""
config.py — Pure Club Experience Configuration, Phonetic Precision & C2 Mastery Guide
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
PERIODIC_BACKUP_SECONDS: int = 10800

GEMINI_MODELS_CHAIN: List[str] = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

VOICE_MALE_HOST: str = "en-US-AndrewNeural"
VOICE_FEMALE_HOST: str = "en-US-AvaNeural"

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
   - Calibrate every expression to the real human moment—whether it's a casual street chat, a high-stakes workplace negotiation, or a deep intellectual debate.
   - Actively model Spoken American Grammar: conversational clefts ('What really gets me is...', 'The thing about X is...'), Heads & Tails ('That new spot on Bleecker—have you tried it yet?'), natural hedging ('kind of', 'for the most part', 'more or less'), and discourse markers ('Mind you', 'Funny enough', 'Wait, seriously?', 'Fair enough, though').

2. MID-SENTENCE SELF-REPAIR & PIVOT COACHING:
   - Native speakers constantly adjust thoughts mid-sentence without losing flow.
   - In every turn (`self_repair_pivot`), show how a New Yorker could smoothly pivot, soften, or sharpen the user's thought mid-stream using authentic repair markers ('Actually, let me walk that back...', 'Or rather...', 'Scratch that—what I really mean is...', 'To put a finer point on it...').

3. SEMANTIC PRECISION & PRAGMATIC NUANCE:
   - Highlight subtle semantic distinctions (why one verb or collocation carries the exact shade of meaning over a near-synonym).
   - Point out unspoken pragmatic subtext, contrastive stress shifts, and idiomatic collocations.

4. STRICT PHONETIC & PROSODIC PRECISION (AMERICAN ACCENT COACH):
   - When the user sends a voice note, act as an uncompromising yet encouraging American pronunciation & rhythm critic:
     * Vowel Precision: Catch tense vs. lax vowel slips (/iː/ vs /ɪ/, /uː/ vs /ʊ/), the American trap-bath /æ/, and diphthongs (/oʊ/, /eɪ/).
     * Word, Phrase & Contrastive Stress: Pinpoint primary stress (CAPS) vs. unstressed syllables reduced to Schwa (/ə/), and show how stress placement shifts meaning.
     * Connected Speech Mechanics: Show exact Flap-T ([ɾ] between vowels: 'what about it' -> 'whaddaboudit'), Glottal Stop ([ʔ] before -en: 'button', 'certain'), palatalization ('did you' -> 'didja', 'got you' -> 'gotcha'), consonant linking, and pitch contours (↘ / ↗) with thought-group pauses (|).

5. 3-PART CONVERSATIONAL FRAMES:
   - Every expression you teach must include:
     * 🎯 When to use it (The real-life trigger moment)
     * 🔗 How it links (Natural verb/preposition collocations & grammar pattern)
     * 💬 How to follow it up (The natural next line an American says right after it)

6. ZERO TECHNICAL JARGON:
   - Never mention AI, models, databases, algorithms, or academic codes. Speak strictly as a real human coach and friend in New York.
""".strip()

# =====================================================================
# Detailed 6-Chapter Member's Handbook (Inside Telegram)
# =====================================================================
GUIDE_CHAPTERS: Dict[str, str] = {
    "overview": (
        "📖 <b>Musa's NYC Lounge — Detailed Member's Handbook</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your private Manhattan club for mastering <b>Spoken American English</b>—from effortless everyday banter and workplace diplomacy to razor-sharp pronunciation and cultural subtext.\n\n"
        "<blockquote expandable>🗽 <b>1. How Every Conversation Turn Works</b>\n"
        "• <b>Natural Flow First (Top Half):</b> Your host responds directly to your ideas like a sharp New York friend—keeping the conversation alive without interrupting your train of thought.\n"
        "• <b>Subtle Phrase Weaving (<i>Italics</i>):</b> Phrases in <i>italics</i> inside the host's reply are expressions pulled from your Personal Vault and woven naturally into live conversation so your brain absorbs them effortlessly.\n"
        "• <b>Collapsible Coaching Notes (Bottom Half):</b> Tap <code>🔬 Coaching Notes, Rhythm & Expressions</code> under any reply to reveal:\n"
        "  1️⃣ <b>3 Living Vibes:</b> Your thought recast in <b>🎙️ Articulate</b>, <b>☕ Casual</b>, and <b>🎭 Witty</b> styles.\n"
        "  2️⃣ <b>🔄 Mid-Sentence Self-Repair:</b> How to pivot or rephrase your thought mid-stream like a native speaker.\n"
        "  3️⃣ <b>🎧 Rhythm & Linking Map:</b> Stress (CAPS), pauses (|), Flap-T, and intonation arrows.\n"
        "  4️⃣ <b>💎 3 Spoken Expressions:</b> Complete with <i>When to use</i>, <i>Pattern</i>, and <i>Follow-up</i>.</blockquote>\n\n"
        "<blockquote expandable>🎛️ <b>2. Your Action Bar Controls (Under Every Reply)</b>\n"
        "• <code>🎧 Host Voice</code>: Hear the host's reply spoken aloud with authentic New York cadence.\n"
        "• <code>🗣️ My Recast</code>: Hear the polished American version of your own message.\n"
        "• <code>🪞 Voice Mirror</code>: Appears on voice notes—plays <b>Your Voice ➔ Native 1.0x ➔ Slow Study 0.85x</b> back-to-back.\n"
        "• <code>⭐ Save (1/2/3)</code>: Saves that specific expression to your Vault & Daily Review.\n"
        "• <code>🎲 New Topic</code>: Sparks a fresh, unscripted scenario or dilemma anytime.</blockquote>\n\n"
        "👇 <b>Tap any chapter button below for the complete step-by-step guide:</b>"
    ),
    "ch1_xray": (
        "🔬 <b>Chapter 1: Coaching Notes, Self-Repair & The Voice Mirror</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. The 3 Living American Vibes:</b>\n"
        "Language isn't about memorizing rigid rules—it's about matching the moment. Every turn shows your exact idea across:\n"
        "• <b>🎙️ Articulate:</b> Clear, expressive, phrasal-verb rich—ideal for work, podcasts, and thoughtful discussions.\n"
        "• <b>☕ Casual:</b> Fast, warm everyday talk among close friends with natural contractions and ellipsis.\n"
        "• <b>🎭 Witty:</b> Dry understatement, playful irony, and clever rhetorical edge.\n\n"
        "<blockquote expandable>🔄 <b>2. Mid-Sentence Self-Repair & Pivot Coaching</b>\n"
        "Even native speakers start sentences they need to adjust halfway through. Instead of freezing up, Americans use <b>Repair & Pivot Gambits</b>:\n"
        "• <i>'Actually, let me walk that back—what I mean is...'</i>\n"
        "• <i>'Or rather, to put a finer point on it...'</i>\n"
        "• <i>'Scratch that—the real issue is...'</i>\n"
        "Every Coaching Note includes a custom <b>🔄 Mid-Sentence Pivot</b> tailored to your message so you learn how to rescue any thought on the fly.</blockquote>\n\n"
        "<blockquote expandable>🪞 <b>3. How to Use the Side-by-Side Voice Mirror</b>\n"
        "1️⃣ Send a voice note in any room.\n"
        "2️⃣ Tap <code>🪞 Voice Mirror</code> below the reply.\n"
        "3️⃣ Listen to the 3-part track: <code>[Your Voice] ➔ 🔔 ➔ [Native 1.0x] ➔ 🔔 ➔ [Slow 0.85x]</code>.\n"
        "4️⃣ Compare your vowel lengths, primary word stress, and Flap-T linking against the native model, then record a quick follow-up!</blockquote>"
    ),
    "ch2_studios": (
        "🗽 <b>Chapter 2: The 6 Conversation & Sparring Rooms</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Switch rooms anytime from the main menu:\n\n"
        "• <b>🗣️ NYC Banter:</b> Unscripted everyday conversation on life, work, culture, or ideas.\n\n"
        "• <b>🥊 Devil's Advocate (Pushback Sparring):</b>\n"
        "True fluency is tested when someone <i>disagrees</i> with you. Share any opinion and the host will challenge your premise with sharp, polite New York pushback—coaching you on diplomatic disagreement, hedging (<i>'I see where you're coming from, but...'</i>, <i>'I wouldn't go that far...'</i>), and holding the floor.\n\n"
        "• <b>🎭 Storytelling Gym:</b>\n"
        "Tell a 45-second story. Learn to shift into dramatic present tense (<i>'So I walk up to the desk, right...'</i>), pace your setup, and land the punchline.\n\n"
        "• <b>🎛️ Vibe & Style Shifter:</b>\n"
        "Send any sentence to see how its tone transforms across Articulate, Casual, and Witty contexts.\n\n"
        "• <b>🕵️‍♂️ Subtext & Nuance Decoder:</b>\n"
        "Send a tricky remark someone said to you (or tap <code>🎲 Spark Another Topic</code> for a subtext riddle) to decode unspoken American hints and polite brush-offs.\n\n"
        "• <b>⚡ What to Say When...:</b>\n"
        "Describe any social or workplace situation (in <b>English or Arabic</b>) to get the exact instinctive phrases Americans reach for in that second."
    ),
    "ch3_audio_podcast": (
        "🎙️ <b>Chapter 3: Accent Precision, Contrastive Stress & Radio Lounge</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote expandable>🎧 <b>1. What the Accent Coach Listens For</b>\n"
        "When you send a voice note, your delivery is analyzed across 4 dimensions:\n"
        "• <b>Vowel Lengths:</b> Tense vs. lax contrasts (<i>seat</i> vs. <i>sit</i>, <i>pool</i> vs. <i>pull</i>) and diphthongs.\n"
        "• <b>Stress & Schwa (/ə/):</b> Highlighting primary stressed syllables (CAPS) while weakening unstressed function words (<i>to ➔ tuh, of ➔ uhv, for ➔ fer</i>).\n"
        "• <b>Flap-T & Glottal Stop:</b> Intervocalic T/D tapped smoothly (<i>'what about it' ➔ 'whaddaboudit'</i>) and glottal stops before <i>-en</i> (<i>certain, Manhattan</i>).\n"
        "• <b>Thought Groups (|):</b> Pausing naturally between meaning chunks instead of rushing word-by-word.</blockquote>\n\n"
        "<blockquote expandable>🎙️ <b>2. The NYC Radio Lounge (Andrew & Ava)</b>\n"
        "Open <code>🎙️ The Radio Lounge</code> for three immersive audio experiences:\n"
        "• <b>🎧 Episode Using My Saved Phrases:</b> Andrew & Ava record a lively multi-turn radio show weaving in your Vault phrases, pausing mid-show to explain why an expression works.\n"
        "• <b>✍️ Custom Topic Show:</b> Give Andrew & Ava any topic or dilemma to debate on-air.\n"
        "• <b>🎙️ 'Be the Guest':</b> Step onto the show yourself for a live interactive interview.</blockquote>"
    ),
    "ch4_miner_drills": (
        "🎯 <b>Chapter 4: Phrase Finder, Text Miner & Speaking Workouts</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. 🔎 Collocation & Phrase Finder:</b>\n"
        "Open <code>🔎 Phrase Finder & Miner</code> ➔ <code>Collocation Finder</code> and type any word or verb (e.g., <i>slack, point, odds, bridge</i>). You'll get a full <b>Spoken Collocation Map</b> with semantic notes and 4 high-utility conversational frames ready to save.\n\n"
        "<b>2. 🔬 Native Text & Dialogue Miner:</b>\n"
        "Paste any paragraph from a book, article, or transcript to extract its 3 best spoken idioms and decode its tone.\n\n"
        "<blockquote expandable>🔥 <b>3. The 6 Active Speaking & Ear Workouts</b>\n"
        "Open <code>🎯 Speaking Workouts</code> to launch fresh, never-repeated workouts:\n"
        "• <b>🔥 The 3-Phrase Speaking Challenge:</b> We pull 3 phrases from your Vault and give you a live scenario—weave all 3 into a single voice note or reply.\n"
        "• <b>🎭 Contrastive Stress & Subtext Lab:</b> See how stressing different words in the exact same sentence shifts its hidden meaning, then record a voice note delivering a target subtext.\n"
        "• <b>🎙️ Rhythm & Shadowing Workout:</b> Listen to a connected-speech model clip with a full Flap-T & stress map, then shadow the exact cadence.\n"
        "• <b>👂 Fast Ear Catcher:</b> Listen to a fast American audio clip with no text and type what you heard to train bottom-up listening.\n"
        "• <b>🩺 Habits I'm Polishing:</b> Review your recurring phrasing or pronunciation habits and check them off as you master them.\n"
        "• <b>📊 My Spoken Fluency Audit:</b> Generate a comprehensive progress report analyzing your recent turns, Vault mastery, and priority focus areas.</blockquote>"
    ),
    "ch5_vault_routine": (
        "🧠 <b>Chapter 5: Your Vault, Active Review & 15-Minute Daily Routine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1. 🧠 Two Ways to Practice in Daily Review:</b>\n"
        "Tap <code>🧠 Daily Review</code> to practice due phrases from your Vault:\n"
        "• <b>Option A (Quick Self-Check):</b> Tap <code>👁️ Reveal Phrase</code>, listen to the example audio, and grade how natural it felt (<code>🔴 Soon</code> / <code>🟡 Good</code> / <code>🟢 Effortless</code>).\n"
        "• <b>Option B (Live Voice/Sentence Practice):</b> Before revealing the card, <b>send a voice note or sentence using the target phrase in your own context!</b> The host will evaluate your usage and pronunciation, polish your sentence, and schedule its next review.\n\n"
        "<b>2. 📄 Printable Phrasebook & Anki Exports:</b>\n"
        "Open <code>📓 My Vault & Booklet</code> to download your collection as a formatted <b>PDF Booklet</b> or an <b>Anki Flashcard Deck</b>.\n\n"
        "<blockquote expandable>⏱️ <b>3. Recommended 15-Minute Daily Routine for C2 Mastery</b>\n"
        "• <b>Morning (4 mins):</b> Open <code>🧠 Daily Review</code> and practice due phrases by sending a quick voice note for each, then listen to an episode in <code>🎙️ The Radio Lounge</code>.\n"
        "• <b>Midday (7 mins):</b> Spend 3 turns in <code>🗣️ NYC Banter</code>, <code>🥊 Devil's Advocate</code>, or <code>🎭 Storytelling Gym</code>. Tap <code>🪞 Voice Mirror</code> on your voice notes and save 2 new expressions via <code>⭐ Save</code>.\n"
        "• <b>Evening (4 mins):</b> Complete one <code>🔥 3-Phrase Challenge</code> or <code>🎭 Contrastive Stress Lab</code>, and run <code>📊 My Spoken Fluency Audit</code> once a week.</blockquote>"
    )
}
