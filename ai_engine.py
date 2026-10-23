# -*- coding: utf-8 -*-
"""
ai_engine.py — 100% Generative Gemini 2.5 Native Audio & Linguistic Brain
Dynamically generates level-calibrated (B1-C2) topics, scenarios, subtext riddles,
shadowing sentences, micro-dictation audio targets, and forced-output workouts.
"""
import html
import random
import logging
from typing import List, Dict, Optional, Any, Tuple
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from config import (
    GEMINI_KEYS,
    GEMINI_MODELS_CHAIN,
    CORE_NYC_PERSONA_PROMPT,
    REGISTERS_MAP,
    REGISTER_DESCRIPTIONS
)
from database import LoungeDatabase

logger = logging.getLogger("MusaNYCLounge.AI")


# =====================================================================
# 1. Structured Output Schemas (Pydantic)
# =====================================================================
class DiagnosticItem(BaseModel):
    category: str = Field(
        description="Tag such as '[🤖 Stiff / L1 Transfer]', '[🎙️ Prosody / Flap-T]', '[📐 Spoken Grammar]', or '[🎯 Collocation Slip]'."
    )
    user_said: str = Field(description="The exact phrase or phonetic habit from the user.")
    native_fix: str = Field(description="How an articulate American naturally says it.")
    why_note: str = Field(description="Crisp explanation of why the original sounded textbook/foreign and why the fix works.")


class LexicalFrameUpgrade(BaseModel):
    chunk: str = Field(description="High-value American phrasal verb, collocation, idiom, or conversational gambit.")
    register_tag: str = Field(
        description="Strictly one of: '[🎙️ Articulate / Natural]', '[☕ Casual / Everyday]', or '[🎭 Witty / Ironic]'."
    )
    explanation: str = Field(description="Clear pragmatic meaning and tone nuance.")
    trigger: str = Field(description="1. THE TRIGGER: When and why this chunk pops into a native speaker's mind.")
    pattern: str = Field(description="2. CO-TEXT & GRAMMAR PATTERN: Typical verb/preposition collocations and polarity.")
    followup: str = Field(description="3. TYPICAL FOLLOW-UP: The natural next sentence an American says right after this chunk.")
    example: str = Field(description="Authentic, vivid everyday NYC example sentence.")


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(
        default="",
        description="If audio input was provided, the exact verbatim transcription of what the user said. Empty if text input."
    )
    lounge_reply: str = Field(
        description="Your natural, witty, engaging conversational response as a smart NYC friend. Wrap any stealth-injected vault chunks in <i>italics</i>."
    )
    rewrite_articulate: str = Field(
        description="[🎙️ Articulate / Natural] recast of the user's message (smart podcast / conversational flow)."
    )
    rewrite_casual: str = Field(
        description="[☕ Casual / Everyday] recast of the user's message (relaxed close-friends NYC vernacular)."
    )
    rewrite_witty: str = Field(
        description="[🎭 Witty / Ironic] recast of the user's message (dry humor, clever understatement, or rhetorical punch)."
    )
    acoustic_prosody_xray: str = Field(
        description="US Connected Speech breakdown: Flap-T, Schwa reductions, linking, primary stress in CAPS, intonation arrows (↘/↗), and phonetic respelling."
    )
    tts_clean_target: str = Field(
        description="Clean, plain-text version of the best American rewrite (no brackets or phonetic symbols) ready for neural TTS & Acoustic Mirror."
    )
    diagnostics: List[DiagnosticItem] = Field(
        default_factory=list,
        description="Up to 3 surgical diagnostics catching unnatural phrasing, L1 transfer, spoken grammar, or pronunciation habits."
    )
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(
        default_factory=list,
        description="Exactly 3 numbered 3-Part Lexical Frames tailored to this turn."
    )


class PodcastTurnLine(BaseModel):
    speaker: str = Field(description="Strictly 'male_host' (Andrew) or 'female_host' (Ava).")
    text: str = Field(description="Natural spoken line with American discourse markers, reactions, and pragmatic commentary.")


class PodcastEpisodeSchema(BaseModel):
    title: str = Field(description="Catchy NYC Lounge episode title.")
    target_chunks_covered: List[str] = Field(description="List of target chunks woven into the episode.")
    script: List[PodcastTurnLine] = Field(description="8 to 12 back-and-forth conversational turns between male_host and female_host.")
    key_takeaways: List[LexicalFrameUpgrade] = Field(description="3 core 3-Part Lexical Frames featured in the episode.")


class DynamicStudioSparkSchema(BaseModel):
    headline: str = Field(description="Engaging NYC studio prompt headline tailored to the active mode and user CEFR level.")
    scenario_or_question: str = Field(
        description="A fresh, vivid, unscripted scenario, dilemma, subtext riddle, or storytelling prompt calibrated to the user's level."
    )
    suggested_chunks_to_try: List[str] = Field(
        description="3 natural American chunks (calibrated to user's level and register) they can try weaving into their response."
    )
    pro_tip: str = Field(description="A 1-sentence pragmatic or delivery tip for tackling this prompt.")


class DynamicDrillSchema(BaseModel):
    drill_title: str = Field(description="Dynamic title of the workout.")
    scenario_prompt: str = Field(description="Freshly generated interactive scenario or instructions tailored to the user's CEFR level.")
    target_chunks: List[str] = Field(default_factory=list, description="3 target chunks for forced output.")
    target_audio_sentence: str = Field(
        default="",
        description="Freshly generated natural American sentence packed with connected speech features (Flap-T, linking, Schwa, modal reductions)."
    )
    phonetic_prosody_map: str = Field(
        default="",
        description="Phonetic respelling + stress CAPS + intonation arrows (↗/↘) showing exactly how Americans blend the target sentence."
    )
    hidden_reductions_explained: str = Field(
        default="",
        description="Explanation of which words merge or reduce in fast speech (used for Micro-Dictation & Shadowing)."
    )


# =====================================================================
# 2. Generative AI Engine
# =====================================================================
class NYCLoungeAI:
    def __init__(self, api_key: str, db: LoungeDatabase):
        self.db = db
        self.keys_pool = GEMINI_KEYS if GEMINI_KEYS else ([api_key] if api_key else [])
        self._key_idx = 0

    def _get_client(self) -> genai.Client:
        if not self.keys_pool:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        key = self.keys_pool[self._key_idx % len(self.keys_pool)]
        self._key_idx = (self._key_idx + 1) % len(self.keys_pool)
        return genai.Client(api_key=key)

    def _call_structured(
        self,
        contents: List[Any],
        schema_cls: Any,
        system_instruction: str,
        temperature: float = 0.72
    ) -> Any:
        last_err = None
        for _ in range(max(1, len(self.keys_pool))):
            client = self._get_client()
            for model_name in GEMINI_MODELS_CHAIN:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=temperature,
                            response_mime_type="application/json",
                            response_schema=schema_cls
                        )
                    )
                    if getattr(resp, "parsed", None) is not None:
                        return resp.parsed
                    if getattr(resp, "text", None):
                        return schema_cls.model_validate_json(resp.text)
                except Exception as e:
                    last_err = e
                    logger.warning(f"Model {model_name} attempt failed: {e}")
                    continue
        raise RuntimeError(f"AI Engine unavailable after fallback attempts: {last_err}")

    @staticmethod
    def _level_calibration_guide(level: str) -> str:
        calibrations = {
            "B1": "CEFR B1 (Intermediate): Use clear, high-frequency everyday American phrasal verbs and relatable daily situations. Keep syntax accessible while avoiding textbook stiffness.",
            "B2": "CEFR B2 (Upper-Intermediate): Use natural idiomatic collocations, conversational gambits, and moderately nuanced social scenarios.",
            "C1": "CEFR C1 (Advanced): Use sophisticated spoken grammar (Heads & Tails, hedging), nuanced pragmatic subtext, sharp phrasal verbs, and intellectually engaging NYC topics.",
            "C2": "CEFR C2 (Mastery / Native-Like Intuition): Push full native subtlety—dry understatement, fast connected-speech reductions, cultural nuance, and razor-sharp rhetorical wit."
        }
        return calibrations.get(level, calibrations["C1"])

    def _build_mode_specific_directive(self, mode: str, session_meta: Dict[str, Any]) -> str:
        if mode == "storytelling_gym":
            return (
                "ACTIVE STUDIO: 🎭 ANECDOTE & STORYTELLING GYM.\n"
                "Coach the user like a New York narrative & comedy mentor:\n"
                "1. React to their story, then show how to make it punchier using the Historical Present ('So I walk up to the counter, right...').\n"
                "2. Highlight dramatic pacing, setup vs. punchline timing, and conversational fillers ('mind you', 'next thing I know').\n"
                "3. Make all 3 Lexical Upgrades high-impact storytelling & narrative gambits."
            )
        elif mode == "register_lab":
            return (
                "ACTIVE STUDIO: 🎛️ THE REGISTER SHIFTER LAB.\n"
                "Focus deeply on contrasting how the user's exact thought shifts across:\n"
                "• [🎙️ Articulate / Natural] (smart podcast/coffeehouse flow)\n"
                "• [☕ Casual / Everyday] (fast street/friends vernacular with ellipsis)\n"
                "• [🎭 Witty / Ironic] (dry understatement & clever edge)\n"
                "Explain the pragmatic temperature shift in your reply."
            )
        elif mode == "subtext_decoder":
            return (
                "ACTIVE STUDIO: 🕵️‍♂️ SUBTEXT, IRONY & PRAGMATIC DECODER.\n"
                "Unpack the hidden meaning between the lines (unspoken subtext, polite brush-offs, passive-aggressive hints, or American understatement).\n"
                "In your `lounge_reply`, decode what the phrase/situation REALLY means in American culture, and give the user 3 razor-sharp, understated ways to respond."
            )
        elif mode == "situation_gambit":
            return (
                "ACTIVE STUDIO: ⚡ SITUATION-TO-GAMBIT TRANSLATOR.\n"
                "The user is describing a specific social moment, awkward spot, or feeling (they may describe it in English or Arabic).\n"
                "Your entire response MUST be in 100% authentic American English: give them the exact native 'Conversational Gambits' Americans instinctively pull out in that exact second, complete with tone and intonation notes."
            )
        elif mode == "podcast_guest":
            return (
                "ACTIVE STUDIO: 🎙️ LIVE NYC PODCAST ('YOU ARE THE GUEST').\n"
                "Treat the user as the featured guest on your Manhattan podcast. React to their point with charisma, challenge or build on their perspective, and ask a compelling follow-up question on-air."
            )
        elif mode == "weakness_drill":
            targets = session_meta.get("forced_targets", [])
            return (
                f"ACTIVE STUDIO: 🔥 FORCED OUTPUT CHALLENGE.\n"
                f"The user was challenged to naturally weave these target chunks into a single reply: {targets}.\n"
                f"Praise what they pulled off naturally, gently recalibrate any forced phrasing, and keep the banter moving."
            )
        elif mode == "shadowing_drill":
            target_sent = session_meta.get("drill_target_sentence", "")
            hidden_notes = session_meta.get("drill_phonetic_hint", "")
            drill_kind = session_meta.get("drill_kind", "shadowing")
            if drill_kind == "micro_dictation":
                return (
                    f"ACTIVE STUDIO: 👂 MICRO-DICTATION DECODER.\n"
                    f"The secret original US sentence was: '{target_sent}'.\n"
                    f"Acoustic notes: {hidden_notes}\n"
                    f"Compare what the user typed against the secret sentence. Highlight which reduced words (Flap-T, Schwa, linking) tricked their ear, and explain how to catch them next time."
                )
            return (
                f"ACTIVE STUDIO: 🎙️ US CONNECTED SPEECH SHADOWING.\n"
                f"The target shadowing sentence was: '{target_sent}'.\n"
                f"Compare the user's audio delivery against native US rhythm, linking, Flap-T, and stress placement."
            )
        return (
            "ACTIVE STUDIO: 🗣️ SPONTANEOUS NYC BANTER.\n"
            "Engage in genuine, unscripted, witty New York conversation. React to the substance of their idea first, share a sharp observation, and keep the momentum rolling with a natural question."
        )

    # =================================================================
    # 3. Dynamic Topic, Scenario & Prompt Generator (100% AI-Generated)
    # =================================================================
    def generate_studio_spark(self, user: Dict[str, Any], mode: Optional[str] = None) -> str:
        """
        Dynamically generates a fresh, non-repetitive scenario, topic, subtext puzzle,
        or storytelling hook tailored to the user's CEFR level, register, vault, and history.
        """
        uid = user["user_id"]
        target_mode = mode or user.get("current_mode", "spontaneous_chat")
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vault = self.db.get_user_vault(uid, limit=4)
        history = self.db.get_history(uid, limit=4)

        vault_list = [v["chunk"] for v in vault]
        recent_context = " | ".join([h["content"][:80] for h in history]) if history else "No prior turns"

        random_themes = [
            "modern urban etiquette & unspoken social rules",
            "a plan that backfired in an unexpectedly funny way",
            "reading between the lines in a tricky friendship or work conversation",
            "unpopular opinions about daily habits, tech, or coffeehouse culture",
            "navigating last-minute changes, cancellations, or awkward small talk",
            "a moment of sudden realization or changing your mind about something big",
            "dealing with someone who overcomplicates simple things"
        ]
        chosen_seed = random.choice(random_themes)

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Dynamically invent a brand-new, engaging interactive prompt or scenario for studio mode: `{target_mode}`.\n"
            f"- User Level Calibration: {self._level_calibration_guide(level)}\n"
            f"- Preferred Register Vibe: {REGISTERS_MAP.get(pref_reg, '[🎙️ Articulate / Natural]')}\n"
            f"- Thematic Seed (for variety): {chosen_seed}\n"
            f"- Avoid repeating recent topics: {recent_context}\n"
            f"- User's recent Vault chunks (incorporate or complement): {vault_list}\n\n"
            "MODE INSTRUCTIONS:\n"
            "- If `spontaneous_chat`: Pitch a thought-provoking, witty NYC coffeehouse question or real-life dilemma.\n"
            "- If `storytelling_gym`: Give them a vivid story setup or personal prompt to narrate in 45 seconds using Historical Present.\n"
            "- If `subtext_decoder`: Present a realistic ambiguous remark an American might say at a party or meeting, and ask the user what the hidden subtext really means and how they'd reply.\n"
            "- If `situation_gambit`: Describe a tricky social spot and challenge the user to say what they'd say (or invite them to describe their own situation).\n"
            "- If `register_lab`: Give a stiff/textbook sentence and challenge them (or invite them to send any thought) to shift across Articulate, Casual, and Witty.\n"
            "- If `podcast_guest`: Welcome them on-air as the guest on 'Musa's NYC Lounge Podcast' and ask a compelling opening interview question."
        )

        try:
            spark: DynamicStudioSparkSchema = self._call_structured(
                contents=[f"Generate a fresh, level-{level} interactive spark for mode `{target_mode}` now."],
                schema_cls=DynamicStudioSparkSchema,
                system_instruction=system_prompt,
                temperature=0.85
            )
            chunks_fmt = " • ".join([f"<code>{html.escape(c)}</code>" for c in spark.suggested_chunks_to_try[:3]])
            return (
                f"🎲 <b>{html.escape(spark.headline)}</b> <code>[{level} • {pref_reg.capitalize()}]</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🗽 {html.escape(spark.scenario_or_question)}\n\n"
                f"💎 <b>Try Weaving In:</b> {chunks_fmt}\n"
                f"💡 <i>Coach Tip: {html.escape(spark.pro_tip)}</i>\n\n"
                f"🎙️ <i>Reply with a voice note or text message—or tap <code>🎲 New AI Prompt</code> for another angle!</i>"
            )
        except Exception as e:
            logger.warning(f"Dynamic spark fallback: {e}")
            return (
                f"🗽 <b>Studio Ready ({html.escape(target_mode)}) — Level {level}</b>\n"
                f"Drop a voice note or text message on whatever's on your mind, or tap <code>🎲 New AI Prompt</code>!"
            )

    # =================================================================
    # 4. Main Turn Processor (Text & Raw Voice Input)
    # =================================================================
    def process_lounge_turn(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> Tuple[LoungeTurnResponse, List[int]]:
        uid = user["user_id"]
        level = user.get("level", "C1")
        eng_only = bool(user.get("english_only", 1)) or level in ("C1", "C2")
        pref_reg = user.get("preferred_register", "articulate")
        mode = user.get("current_mode", "spontaneous_chat")
        session_meta = self.db.get_session_meta(uid)

        # 1. Pull Stealth Injection Queue (Incidental Input Flood)
        stealth_items, _ = self.db.get_stealth_injection_targets(uid, limit=2)
        stealth_chunks_str = ", ".join([f"'{it['chunk']}'" for it in stealth_items]) if stealth_items else "None"

        # 2. Pull Recurring Weaknesses (Smart Error Profile)
        weaknesses = self.db.get_weaknesses(uid, limit=3)
        weakness_str = "; ".join([f"Avoid '{w['user_said']}' -> Use '{w['native_fix']}'" for w in weaknesses]) if weaknesses else "None"

        # 3. Hybrid FTS5 Corpus RAG Grounding
        rag_hits = self.db.get_rag_collocations_for_context(user_text, limit=3) if user_text else []
        rag_str = "\n".join([f"  - {r}" for r in rag_hits]) if rag_hits else "  - Use authentic Spoken American collocations."

        # 4. Recent Conversation Context
        history = self.db.get_history(uid, limit=6)
        hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history]) if history else "Start of session."

        lang_rule = (
            "STRICT 100% ENGLISH IMMERSION: Every single word of your output—even if the user writes in Arabic in Situation Gambit mode—MUST be in English."
            if eng_only else
            "Keep all UI, rewrites, and conversation in English; you may add a brief Arabic hint ONLY inside `why_note` if helpful for B1/B2."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"USER PROFILE & DYNAMIC LEVEL CALIBRATION:\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- Preferred Vibe: {REGISTERS_MAP.get(pref_reg, '[🎙️ Articulate / Natural]')} ({REGISTER_DESCRIPTIONS.get(pref_reg, '')})\n"
            f"- Language Rule: {lang_rule}\n"
            f"- {self._build_mode_specific_directive(mode, session_meta)}\n\n"
            f"INCIDENTAL INPUT FLOOD (STEALTH INJECTION QUEUE):\n"
            f"- Target Vault Chunks to weave naturally into your `lounge_reply`: {stealth_chunks_str}\n"
            f"- If you weave one in, wrap that exact chunk in HTML italics `<i>chunk</i>` inside `lounge_reply` so the user's eye notices it in context.\n\n"
            f"USER'S RECURRING WEAKNESS PROFILE (WATCH FOR THESE):\n"
            f"- {weakness_str}\n\n"
            f"VERIFIED FTS5 CORPUS COLLOCATIONS (RAG CONTEXT):\n"
            f"{rag_str}\n\n"
            f"RECENT CONVERSATION MEMORY:\n{hist_str}"
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                f"Listen directly to the user's raw voice note (User CEFR Level: {level}). "
                "1) Transcribe it verbatim in `transcription`. "
                "2) Reply naturally as their NYC Lounge host in `lounge_reply`. "
                "3) Provide the 3 Register Shifter rewrites ([🎙️ Articulate], [☕ Casual], [🎭 Witty]). "
                "4) Analyze their actual vocal delivery (Flap-T, vowel reductions, linking, stress, intonation) in `acoustic_prosody_xray` and `diagnostics`. "
                f"5) Provide 3 high-value 3-Part Lexical Frames calibrated to level {level}."
            )
        else:
            contents.append(f"USER MESSAGE (Calibrate upgrades & diagnostics to CEFR {level}):\n{user_text}")

        res: LoungeTurnResponse = self._call_structured(
            contents=contents,
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.70
        )

        allowed_tags = list(REGISTERS_MAP.values())
        for idx, up in enumerate(res.lexical_upgrades):
            if not any(k in up.register_tag for k in ("Articulate", "Casual", "Witty")):
                up.register_tag = allowed_tags[idx % len(allowed_tags)]

        used_stealth_ids: List[int] = []
        reply_lower = (res.lounge_reply or "").lower()
        for it in stealth_items:
            if it["chunk"].lower() in reply_lower:
                used_stealth_ids.append(it["id"])

        return res, used_stealth_ids

    # =================================================================
    # 5. Native Text & Subtext Miner (Chunk Miner)
    # =================================================================
    def mine_text_and_subtext(self, user: Dict[str, Any], source_text: str) -> LoungeTurnResponse:
        level = user.get("level", "C1")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"ACTIVE STUDIO: 🔬 NATIVE TEXT & SUBTEXT MINER (Calibrated to User Level: {level}).\n"
            "The user pasted an English passage, quote, or dialogue transcript.\n"
            "1. In `lounge_reply`, unpack the pragmatic tone, unspoken subtext, and stylistic vibe of the passage.\n"
            "2. Show how the core message of the passage sounds across `[🎙️ Articulate]`, `[☕ Casual]`, and `[🎭 Witty]` registers.\n"
            "3. Extract the 3 most valuable native lexical chunks, phrasal verbs, or discourse markers from (or inspired by) the text as full 3-Part Lexical Frames."
        )
        return self._call_structured(
            contents=[f"MINE THIS TEXT FOR LEXICAL FRAMES & PRAGMATIC SUBTEXT:\n{source_text}"],
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.60
        )

    # =================================================================
    # 6. Dual-Host NYC Podcast Studio Generator (Andrew & Ava)
    # =================================================================
    def generate_podcast_episode(self, user: Dict[str, Any], custom_topic: str = "") -> PodcastEpisodeSchema:
        uid = user["user_id"]
        level = user.get("level", "C1")
        vault = self.db.get_user_vault(uid, limit=5)
        weaknesses = self.db.get_weaknesses(uid, limit=3)

        vault_chunks = [v["chunk"] for v in vault]
        weak_fixes = [w["native_fix"] for w in weaknesses]

        topic_prompt = (
            f"Custom Topic requested by listener: '{custom_topic}'."
            if custom_topic else
            f"Dynamically invent a fresh, captivating NYC cultural observation, social dilemma, or funny real-life story suitable for a {level} listener."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            "ACTIVE STUDIO: 🎙️ DUAL-HOST NYC PODCAST GENERATOR (Andrew & Ava).\n"
            f"- Listener Level Calibration: {self._level_calibration_guide(level)}\n"
            f"- {topic_prompt}\n"
            f"- User's Saved Vault Chunks to weave in (if empty, dynamically choose 3 fresh {level} American chunks): {vault_chunks}\n"
            f"- Target Recurring Fixes to reinforce: {weak_fixes}\n"
            "- Write an 8 to 10 turn dialogue between `male_host` (Andrew) and `female_host` (Ava).\n"
            "- Make it sound like an authentic, witty New York podcast. Twice during the conversation, have the hosts briefly pause ('Wait, I love how you phrased that...') to highlight why a target chunk works so well in that exact register before continuing the story."
        )

        ep: PodcastEpisodeSchema = self._call_structured(
            contents=["Generate the complete Dual-Host NYC Lounge Podcast episode now."],
            schema_cls=PodcastEpisodeSchema,
            system_instruction=system_prompt,
            temperature=0.78
        )
        return ep

    # =================================================================
    # 7. 100% Generative Active Drills (Adapted to User Level & Profile)
    # =================================================================
    def build_active_drill(self, user: Dict[str, Any], drill_type: str) -> Dict[str, Any]:
        """
        Dynamically generates fresh, never-repeated Forced Output challenges,
        Connected Speech Shadowing scripts, Micro-Dictation audio puzzles, and Diagnostic tests
        calibrated to the user's CEFR level (B1/B2/C1/C2), Vault, and Error Profile.
        """
        uid = user["user_id"]
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vault = self.db.get_user_vault(uid, limit=8)
        weaknesses = self.db.get_weaknesses(uid, limit=5)

        vault_pool = [v["chunk"] for v in vault]
        weak_pool = [w["native_fix"] for w in weaknesses]

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Dynamically generate a brand-new `{drill_type}` workout.\n"
            f"- User Level: {self._level_calibration_guide(level)}\n"
            f"- Preferred Register: {REGISTERS_MAP.get(pref_reg, '[🎙️ Articulate / Natural]')}\n"
            f"- User's Vault Chunks: {vault_pool}\n"
            f"- User's Recurring Fixes: {weak_pool}\n\n"
            "DRILL TYPE SPECIFICATIONS:\n"
            "1. If `forced_output`:\n"
            "   - Pick 3 target chunks (prioritize User's Vault & Recurring Fixes; if fewer than 3 exist, dynamically invent fresh level-matched American chunks).\n"
            "   - Invent a vivid, realistic NYC social scenario where all 3 chunks naturally fit together.\n"
            "2. If `shadowing`:\n"
            "   - Generate a fresh, natural 1-2 sentence American line tailored to level " + level + " rich in Flap-T, consonant-to-vowel linking, and Schwa reductions in `target_audio_sentence`.\n"
            "   - Provide its exact acoustic respelling, stress CAPS, and intonation arrows (↗/↘) in `phonetic_prosody_map`.\n"
            "3. If `micro_dictation`:\n"
            "   - Generate a fast everyday American sentence containing natural connected-speech reductions (e.g., 'did you' -> 'did-ja', 'going to' -> 'gonna', 'would have' -> 'woulda', Flap-T) in `target_audio_sentence`.\n"
            "   - Explain the exact hidden phonetic reductions in `hidden_reductions_explained` (stored secretly until the user replies).\n"
            "4. If `diagnostic_test`:\n"
            "   - Generate a sharp, multi-layered dilemma or storytelling prompt in `scenario_prompt` designed to test spoken grammar, register flexibility, and prosody."
        )

        try:
            drill: DynamicDrillSchema = self._call_structured(
                contents=[f"Generate a fresh, non-repetitive `{drill_type}` workout for a {level} learner now."],
                schema_cls=DynamicDrillSchema,
                system_instruction=system_prompt,
                temperature=0.82
            )

            if drill_type == "forced_output":
                targets = drill.target_chunks[:3] if drill.target_chunks else ["play it by ear", "wind up", "let it slide"]
                self.db.update_session_meta(uid, forced_targets=targets, drill_kind="forced_output")
                t_list = "\n".join([f"  {i}. 🎯 <code>{html.escape(t)}</code>" for i, t in enumerate(targets, 1)])
                return {
                    "text": (
                        f"🔥 <b>{html.escape(drill.drill_title)}</b> <code>[Level {level}]</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Reply with <b>ONE voice note or text message</b> reacting to this live scenario while naturally weaving in <b>ALL 3 target chunks</b>:\n\n"
                        f"{t_list}\n\n"
                        f"🗽 <b>Live Scenario:</b>\n<i>{html.escape(drill.scenario_prompt)}</i>"
                    ),
                    "tts_target": ""
                }

            elif drill_type == "micro_dictation":
                clean_sent = drill.target_audio_sentence or "What did you wind up doing after the meeting got canceled?"
                phonetic_hint = drill.hidden_reductions_explained or drill.phonetic_prosody_map
                self.db.update_session_meta(
                    uid,
                    drill_target_sentence=clean_sent,
                    drill_phonetic_hint=phonetic_hint,
                    drill_kind="micro_dictation"
                )
                return {
                    "text": (
                        f"👂 <b>{html.escape(drill.drill_title or 'Fast US Micro-Dictation Decoder')}</b> <code>[Level {level}]</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "1️⃣ Listen closely to the freshly generated US audio clip below (no text spoilers!).\n"
                        "2️⃣ Type out the <b>exact full grammatical sentence</b> you heard.\n"
                        "3️⃣ I'll reveal the hidden connected-speech reductions (Flap-T, Schwa, linking) that happened in the audio!"
                    ),
                    "tts_target": clean_sent
                }

            elif drill_type == "shadowing":
                target_sent = drill.target_audio_sentence or "Honestly, I thought about pushing back, but I wound up just letting it slide."
                phonetic = drill.phonetic_prosody_map or "AA-nist-lee, I thawd-uh-bout pushing BACK ↗, bud-I wound-up just ledding-it SLIDE ↘."
                self.db.update_session_meta(
                    uid,
                    drill_target_sentence=target_sent,
                    drill_phonetic_hint=phonetic,
                    drill_kind="shadowing"
                )
                return {
                    "text": (
                        f"🎙️ <b>{html.escape(drill.drill_title or 'US Connected Speech Shadowing')}</b> <code>[Level {level}]</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>Target Sentence:</b>\n<code>{html.escape(target_sent)}</code>\n\n"
                        f"🎧 <b>Acoustic & Prosody Map:</b>\n<i>{html.escape(phonetic)}</i>\n\n"
                        f"💡 <b>Delivery Note:</b> {html.escape(drill.hidden_reductions_explained or drill.scenario_prompt)}\n\n"
                        "👇 Listen to the native audio clip below, then <b>send a voice note shadowing the exact rhythm</b> (and tap <code>🪞 Acoustic Mirror</code> on my reply!):"
                    ),
                    "tts_target": target_sent
                }

            else:
                return {
                    "text": (
                        f"🧭 <b>{html.escape(drill.drill_title or 'Adaptive Naturalness & Prosody Diagnostic')}</b> <code>[Target: {level}]</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Send a <b>30-to-45 second voice note</b> (or a short paragraph) responding naturally to this live prompt:\n\n"
                        f"🗽 <i>{html.escape(drill.scenario_prompt)}</i>\n\n"
                        "I'll diagnose your spoken grammar, L1 transfer habits, register calibration, and US connected speech!"
                    ),
                    "tts_target": ""
                }
        except Exception as e:
            logger.warning(f"Generative drill fallback: {e}")
            return {
                "text": (
                    f"🎯 <b>Live {html.escape(drill_type)} Workout (Level {level})</b>\n"
                    "Send a voice note or text message describing a recent plan that changed at the last minute, and we'll dissect your phrasing and prosody!"
                ),
                "tts_target": ""
            }
