# -*- coding: utf-8 -*-
"""
ai_engine.py — Conversational, Phonetic & Pragmatic Brain for Musa's NYC Lounge
Includes:
- Natural 2-Layer Banter + Mid-Sentence Self-Repair & Pivot Coaching
- Devil's Advocate Pushback Sparring Room
- Contrastive Stress & Implicature Workout
- Spoken Fluency & Mastery Audit Generator
- Live Collocation Explorer & Active Review Evaluator
"""
import time
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
# 1. Structured Output Schemas
# =====================================================================
class DiagnosticItem(BaseModel):
    category: str = Field(
        description="Clean human coaching badge: '🎭 Natural Flow', '🎙️ Vowels & Rhythm', '📐 Spoken Structure', or '🎯 Word Choice & Nuance'."
    )
    user_said: str = Field(description="The exact phrase or vocal habit noticed from the user.")
    native_fix: str = Field(description="How an articulate American naturally phrases or pronounces it.")
    why_note: str = Field(
        description="Clear, insightful note on why the native phrasing or vowel/stress adjustment works better in this context."
    )


class LexicalFrameUpgrade(BaseModel):
    chunk: str = Field(description="High-value American phrasal verb, collocation, idiom, or conversational gambit.")
    register_tag: str = Field(description="Strictly one of: '🎙️ Articulate', '☕ Casual', or '🎭 Witty'.")
    explanation: str = Field(description="Clear meaning, tone nuance, and semantic distinction from near-synonyms.")
    trigger: str = Field(description="When to reach for it: the exact real-life social or mental moment.")
    pattern: str = Field(description="How it links: typical verb/preposition collocations and spoken pattern.")
    followup: str = Field(description="Natural follow-up: the next sentence an American says right after this.")
    example: str = Field(description="Vivid, authentic everyday New York example sentence.")


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(
        default="",
        description="If voice input was provided, the exact transcription of what the user said. Empty if text input."
    )
    lounge_reply: str = Field(
        description="Your natural, witty, engaging conversational response as a sharp NYC friend. Wrap any woven-in Vault phrases in <i>italics</i>."
    )
    rewrite_articulate: str = Field(
        description="🎙️ Articulate recast of the user's message (smart, expressive spoken flow)."
    )
    rewrite_casual: str = Field(
        description="☕ Casual recast of the user's message (relaxed everyday New York talk among close friends)."
    )
    rewrite_witty: str = Field(
        description="🎭 Witty recast of the user's message (dry humor, clever understatement, or playful punch)."
    )
    self_repair_pivot: str = Field(
        default="",
        description="A natural mid-sentence self-repair or pivot showing how a native speaker would smoothly adjust, soften, or sharpen this thought on the fly (e.g. 'Actually, let me walk that back—what I really mean is...')."
    )
    acoustic_prosody_xray: str = Field(
        description="Detailed American Rhythm & Pronunciation Guide: primary stress in CAPS, thought-group pauses (|), intonation arrows (↘/↗), vowel length notes, Flap-T/Glottal Stop, and phonetic respelling."
    )
    tts_clean_target: str = Field(
        description="Clean plain-text version of the best American recast of the user's message (no brackets or phonetic symbols) ready for voice synthesis."
    )
    diagnostics: List[DiagnosticItem] = Field(
        default_factory=list,
        description="Up to 3 surgical coaching notes on phrasing naturalness, semantic nuance, spoken grammar, or vowel/stress precision."
    )
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(
        default_factory=list,
        description="Exactly 3 numbered Spoken American Expressions tailored to this turn."
    )


class PodcastTurnLine(BaseModel):
    speaker: str = Field(description="Strictly 'male_host' (Andrew) or 'female_host' (Ava).")
    text: str = Field(description="Natural spoken line with American discourse markers, reactions, and mid-show commentary.")


class PodcastEpisodeSchema(BaseModel):
    title: str = Field(description="Catchy NYC Radio Lounge episode title.")
    target_chunks_covered: List[str] = Field(description="List of target expressions woven into the episode.")
    script: List[PodcastTurnLine] = Field(description="8 to 12 back-and-forth conversational turns between male_host and female_host.")
    key_takeaways: List[LexicalFrameUpgrade] = Field(description="3 core Spoken Expressions featured in the episode.")


class DynamicStudioSparkSchema(BaseModel):
    headline: str = Field(description="Engaging conversation starter headline.")
    scenario_or_question: str = Field(
        description="A fresh, vivid scenario, real-life dilemma, subtext puzzle, debate stance, or storytelling hook."
    )
    suggested_chunks_to_try: List[str] = Field(
        description="3 natural American expressions the member can try weaving into their reply."
    )
    pro_tip: str = Field(description="A 1-sentence coaching tip on tone, delivery, or storytelling rhythm.")


class DynamicDrillSchema(BaseModel):
    drill_title: str = Field(description="Engaging title of the workout.")
    scenario_prompt: str = Field(description="Freshly generated interactive scenario, contrastive stress Variations, or challenge instructions.")
    target_chunks: List[str] = Field(default_factory=list, description="3 target expressions for the challenge.")
    target_audio_sentence: str = Field(
        default="",
        description="Natural American spoken sentence packed with Flap-T, linking, vowel reductions, or contrastive stress."
    )
    phonetic_prosody_map: str = Field(
        default="",
        description="Phonetic respelling + primary stress CAPS + pause bars (|) + intonation arrows (↗/↘)."
    )
    hidden_reductions_explained: str = Field(
        default="",
        description="Clear explanation of how words blend, link, or how shifting stress alters the hidden subtext."
    )


class CollocationExplorerSchema(BaseModel):
    headword: str = Field(description="The word or phrase being explored.")
    semantic_note: str = Field(
        description="Crisp explanation of how Americans naturally use this word in speech vs. common learner habits."
    )
    collocation_frames: List[LexicalFrameUpgrade] = Field(
        description="4 high-frequency spoken American collocations, phrasal verbs, or idiomatic frames built around this word."
    )


class ActiveReviewEvalSchema(BaseModel):
    used_naturally: bool = Field(description="True if the user used the target expression naturally and accurately in context.")
    recommended_grade: int = Field(description="5 if effortless & natural, 3 if minor slip, 1 if misused or missing.")
    coach_feedback: str = Field(description="Warm, sharp coaching feedback on how they used the phrase (and pronunciation if audio).")
    polished_version: str = Field(description="How a New Yorker would say the user's exact sentence with 100% natural rhythm.")


class SpokenFluencyAuditSchema(BaseModel):
    overall_vibe_assessment: str = Field(description="Insightful assessment of the member's current spoken style, strengths, and conversational presence.")
    naturalness_highlights: List[str] = Field(description="2 to 3 specific strengths or expressions the member is using or absorbing well.")
    focus_areas_under_pressure: List[str] = Field(description="2 to 3 specific phrasing, spoken-structure, or phonetic habits to polish next.")
    recommended_next_steps: str = Field(description="Concrete, actionable 3-step coaching plan for their next few sessions in the club.")


# =====================================================================
# 2. Conversational & Phonetic Engine
# =====================================================================
class NYCLoungeAI:
    def __init__(self, api_key: str, db: LoungeDatabase):
        self.db = db
        self.keys_pool = GEMINI_KEYS if GEMINI_KEYS else ([api_key] if api_key else [])
        self._key_idx = 0
        self._dead_models_until: Dict[str, float] = {}
        self._discovered_models: List[str] = list(GEMINI_MODELS_CHAIN)

    def _get_client(self) -> genai.Client:
        if not self.keys_pool:
            raise RuntimeError("API key is not configured.")
        key = self.keys_pool[self._key_idx % len(self.keys_pool)]
        self._key_idx = (self._key_idx + 1) % len(self.keys_pool)
        return genai.Client(api_key=key, http_options={"timeout": 25000})

    def _get_active_models(self, client: genai.Client) -> List[str]:
        now = time.time()
        avail = [m for m in self._discovered_models if self._dead_models_until.get(m, 0) < now]
        if avail:
            return avail

        found = []
        try:
            for m in client.models.list():
                sn = (getattr(m, "name", "") or "").replace("models/", "")
                low = sn.lower()
                bad_w = ("image", "imagen", "veo", "embedding", "aqa", "tts", "audio", "learnlm", "robotics", "computer-use", "gemma")
                if "gemini" in low and not any(b in low for b in bad_w):
                    found.append(sn)
        except Exception:
            pass
        if found:
            found.sort(key=lambda x: (0 if "3-flash" in x else (1 if "flash" in x else 2)))
            self._discovered_models = found[:6]
        return self._discovered_models

    def _call_structured(
        self,
        contents: List[Any],
        schema_cls: Any,
        system_instruction: str,
        temperature: float = 0.72
    ) -> Any:
        last_err = None
        for _ in range(max(1, min(2, len(self.keys_pool)))):
            client = self._get_client()
            models_to_try = self._get_active_models(client)
            for model_name in models_to_try:
                try:
                    cfg_kwargs: Dict[str, Any] = {
                        "system_instruction": system_instruction,
                        "temperature": temperature,
                        "response_mime_type": "application/json",
                        "response_schema": schema_cls
                    }
                    if "flash" in model_name.lower() and "thinking" not in model_name.lower():
                        try:
                            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                        except Exception:
                            pass

                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(**cfg_kwargs)
                    )
                    if getattr(resp, "parsed", None) is not None:
                        return resp.parsed
                    if getattr(resp, "text", None):
                        return schema_cls.model_validate_json(resp.text)
                except Exception as e:
                    last_err = e
                    err_str = str(e)
                    if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                        self._dead_models_until[model_name] = time.time() + 86400
                    logger.warning(f"Model {model_name} attempt note: {err_str[:110]}")
                    continue
        raise RuntimeError(f"Service temporarily busy: {last_err}")

    @staticmethod
    def _level_calibration_guide(level: str) -> str:
        calibrations = {
            "B1": "Intermediate depth: clear, high-frequency everyday American phrasal verbs and relatable daily situations.",
            "B2": "Upper-Intermediate depth: natural idiomatic collocations, conversational gambits, and nuanced social situations.",
            "C1": "Advanced depth: sophisticated spoken structure (Heads & Tails, clefts, natural hedging), subtle pragmatic subtext, and sharp collocations.",
            "C2": "Mastery depth: full native-like intuition, dry understatement, fast connected-speech reductions, semantic precision, and razor-sharp wit."
        }
        return calibrations.get(level, calibrations["C1"])

    def _build_mode_specific_directive(self, mode: str, session_meta: Dict[str, Any]) -> str:
        if mode == "devils_advocate":
            return (
                "ACTIVE ROOM: 🥊 DEVIL'S ADVOCATE (PUSHBACK & SPARRING UNDER PRESSURE).\n"
                "Your role: Play a sharp, charismatic New Yorker who respectfully challenges the member's premise or plays devil's advocate.\n"
                "1. In `lounge_reply`, push back on their argument with a clever counter-angle or edge-case question that forces them to defend or nuance their position.\n"
                "2. Coach them on American Pushback & Hedging Gambits ('I see where you're coming from, but...', 'Hold on, I wouldn't go that far...', 'Fair point, though if you look at the flip side...').\n"
                "3. Make all 3 expression upgrades high-impact persuasion, concession-and-counter, or floor-holding gambits."
            )
        elif mode == "storytelling_gym":
            return (
                "ACTIVE ROOM: 🎭 STORYTELLING & WIT GYM.\n"
                "Coach the member like a New York storytelling mentor:\n"
                "1. React genuinely to their story, then show how to make it punchier using dramatic present tense ('So I walk up to the counter, right...').\n"
                "2. Highlight pacing, setup vs. punchline timing, and spoken hooks ('Mind you', 'Next thing I know', 'Here's the kicker').\n"
                "3. Make all 3 expression upgrades high-impact storytelling & narrative gambits."
            )
        elif mode == "register_lab":
            return (
                "ACTIVE ROOM: 🎛️ VIBE & STYLE SHIFTER.\n"
                "Contrast how the member's exact thought transforms across:\n"
                "• 🎙️ Articulate (polished, expressive spoken flow)\n"
                "• ☕ Casual (fast close-friends vernacular with natural ellipsis)\n"
                "• 🎭 Witty (dry understatement & clever edge)\n"
                "Explain the subtle tone shift in your reply."
            )
        elif mode == "subtext_decoder":
            return (
                "ACTIVE ROOM: 🕵️‍♂️ SUBTEXT & NUANCE DECODER.\n"
                "Unpack what is really being communicated between the lines (polite brush-offs, subtle hints, passive-aggressive phrasing, or American understatement).\n"
                "In your `lounge_reply`, decode what the remark REALLY means in American culture, and give 3 smooth, natural ways to respond."
            )
        elif mode == "situation_gambit":
            return (
                "ACTIVE ROOM: ⚡ WHAT TO SAY WHEN... (SITUATION GAMBITS).\n"
                "The member is describing a specific social or workplace moment (they may describe it in English or Arabic).\n"
                "Respond 100% in authentic American English: give them the exact instinctive phrases Americans reach for in that exact second, complete with tone and rhythm notes."
            )
        elif mode == "podcast_guest":
            return (
                "ACTIVE ROOM: 🎙️ LIVE RADIO INTERVIEW ('YOU ARE THE GUEST').\n"
                "Treat the member as the featured guest on your Manhattan radio show. React with charisma, challenge or build on their perspective, and ask a compelling follow-up question on-air."
            )
        elif mode == "weakness_drill":
            targets = session_meta.get("forced_targets", [])
            return (
                f"ACTIVE ROOM: 🔥 3-PHRASE CHALLENGE.\n"
                f"The member was challenged to naturally weave these 3 expressions into a single reply: {targets}.\n"
                f"Praise what sounded effortless, gently polish any phrase that felt forced, and keep the conversation flowing."
            )
        elif mode == "shadowing_drill":
            target_sent = session_meta.get("drill_target_sentence", "")
            hidden_notes = session_meta.get("drill_phonetic_hint", "")
            drill_kind = session_meta.get("drill_kind", "shadowing")
            if drill_kind == "micro_dictation":
                return (
                    f"ACTIVE ROOM: 👂 FAST EAR CATCHER.\n"
                    f"The original spoken sentence was: '{target_sent}'.\n"
                    f"Connected speech notes: {hidden_notes}\n"
                    f"Compare what the member typed against the original sentence. Highlight which linked or reduced sounds (Flap-T, Schwa, dropped consonants) blended together, and show how to catch them."
                )
            elif drill_kind == "contrastive_stress":
                return (
                    f"ACTIVE ROOM: 🎭 CONTRASTIVE STRESS & SUBTEXT LAB.\n"
                    f"The target line was: '{target_sent}'.\n"
                    f"Subtext & Stress Guide: {hidden_notes}\n"
                    f"Listen closely to which word the member stressed in their voice note (or how they explained the subtext), tell them what unspoken meaning their pitch/stress conveyed, and coach them on nailing contrastive pitch."
                )
            return (
                f"ACTIVE ROOM: 🎙️ RHYTHM & SHADOWING WORKOUT.\n"
                f"The target sentence was: '{target_sent}'.\n"
                f"Compare the member's vocal delivery against native American rhythm, vowel lengths, primary word stress, Flap-T linking, and intonation."
            )
        return (
            "ACTIVE ROOM: 🗣️ NYC SPONTANEOUS BANTER.\n"
            "Engage in genuine, unscripted, witty New York conversation. React to the substance of their idea first, share a sharp perspective, and keep the momentum rolling with a natural question."
        )

    # =================================================================
    # 3. Dynamic Topic & Scenario Spark
    # =================================================================
    def generate_studio_spark(self, user: Dict[str, Any], mode: Optional[str] = None) -> str:
        uid = user["user_id"]
        target_mode = mode or user.get("current_mode", "spontaneous_chat")
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vault = self.db.get_user_vault(uid, limit=4)
        history = self.db.get_history(uid, limit=4)

        vault_list = [v["chunk"] for v in vault]
        recent_context = " | ".join([h["content"][:80] for h in history]) if history else "Fresh session"

        random_themes = [
            "unspoken social rules in modern city life",
            "a plan or trip that took a completely unexpected turn",
            "reading between the lines when a colleague or friend is being diplomatic",
            "a controversial take on productivity, ambition, or modern habits",
            "navigating last-minute changes or backing out of a commitment gracefully",
            "a moment that completely shifted your perspective on something",
            "dealing with someone who overcomplicates simple decisions"
        ]
        chosen_seed = random.choice(random_themes)

        mode_hint = (
            "Pitch a bold, slightly provocative New York opinion on this theme and challenge the member to push back and disagree with you!"
            if target_mode == "devils_advocate" else
            f"Create a fresh, engaging conversation starter or scenario for room `{target_mode}`."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: {mode_hint}\n"
            f"- Depth Calibration: {self._level_calibration_guide(level)}\n"
            f"- Preferred Vibe: {REGISTERS_MAP.get(pref_reg, '🎙️ Articulate')}\n"
            f"- Inspiration Angle: {chosen_seed}\n"
            f"- Avoid repeating recent topics: {recent_context}\n"
            f"- Member's saved phrases (complement or weave in): {vault_list}\n"
            "Never mention proficiency codes, AI, or technical terms."
        )

        try:
            spark: DynamicStudioSparkSchema = self._call_structured(
                contents=[f"Create a fresh, engaging conversation spark for `{target_mode}` now."],
                schema_cls=DynamicStudioSparkSchema,
                system_instruction=system_prompt,
                temperature=0.85
            )
            chunks_fmt = " • ".join([f"<code>{html.escape(c)}</code>" for c in spark.suggested_chunks_to_try[:3]])
            vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
            return (
                f"🎲 <b>{html.escape(spark.headline)}</b> <code>[{vibe_badge}]</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🗽 {html.escape(spark.scenario_or_question)}\n\n"
                f"💎 <b>Phrases to Try:</b> {chunks_fmt}\n"
                f"💡 <i>Tip: {html.escape(spark.pro_tip)}</i>\n\n"
                f"🎙️ <i>Drop a voice note or text to jump in—or tap <code>🎲 Spark Another Topic</code>!</i>"
            )
        except Exception as e:
            logger.warning(f"Spark fallback: {e}")
            return (
                "🗽 <b>The Table Is Yours!</b>\n"
                "Drop a voice note or text on whatever is on your mind today, or tap <code>🎲 Spark Another Topic</code>."
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

        stealth_items, _ = self.db.get_stealth_injection_targets(uid, limit=2)
        stealth_chunks_str = ", ".join([f"'{it['chunk']}'" for it in stealth_items]) if stealth_items else "None"

        weaknesses = self.db.get_weaknesses(uid, limit=3)
        weakness_str = "; ".join([f"Watch for '{w['user_said']}' -> Prefer '{w['native_fix']}'" for w in weaknesses]) if weaknesses else "None"

        rag_hits = self.db.get_rag_collocations_for_context(user_text, limit=3) if user_text else []
        rag_str = "\n".join([f"  - {r}" for r in rag_hits]) if rag_hits else "  - Use natural Spoken American collocations."

        history = self.db.get_history(uid, limit=6)
        hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history]) if history else "Start of conversation."

        lang_rule = (
            "100% ENGLISH IMMERSION: Every single word of your output—even if the member describes a situation in Arabic—MUST be in English."
            if eng_only else
            "Keep all conversation and rewrites in English; you may add a brief Arabic hint ONLY inside `why_note` if helpful."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"CALIBRATION FOR THIS TURN:\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- Preferred Vibe: {REGISTERS_MAP.get(pref_reg, '🎙️ Articulate')} ({REGISTER_DESCRIPTIONS.get(pref_reg, '')})\n"
            f"- {lang_rule}\n"
            f"- {self._build_mode_specific_directive(mode, session_meta)}\n\n"
            f"SUBTLE PHRASE WEAVING:\n"
            f"- Member's Saved Phrases to weave naturally into your `lounge_reply`: {stealth_chunks_str}\n"
            f"- If you weave one in, wrap that exact phrase in HTML italics `<i>phrase</i>` inside `lounge_reply`.\n\n"
            f"RECURRING HABITS TO WATCH FOR:\n"
            f"- {weakness_str}\n\n"
            f"RELEVANT PHRASEBOOK COLLOCATIONS:\n"
            f"{rag_str}\n\n"
            f"RECENT CONVERSATION:\n{hist_str}"
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                "Listen carefully to the member's voice note. "
                "1) Transcribe what they said verbatim in `transcription`. "
                "2) Reply naturally as their NYC host in `lounge_reply`. "
                "3) Provide the 3 Vibe rewrites (🎙️ Articulate, ☕ Casual, 🎭 Witty) and a natural `self_repair_pivot`. "
                "4) In `acoustic_prosody_xray` and `diagnostics`, evaluate their vocal delivery: vowel lengths (tense vs. lax), primary word stress (CAPS), thought-group pauses (|), Flap-T, Glottal Stop, linking, and pitch direction (↘/↗). "
                "5) Provide 3 high-value Spoken American Expressions with full 3-part frames."
            )
        else:
            contents.append(f"MEMBER MESSAGE:\n{user_text}")

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
    # 5. Live Collocation Explorer
    # =================================================================
    def explore_collocations_live(self, user: Dict[str, Any], query_word: str) -> CollocationExplorerSchema:
        level = user.get("level", "C1")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: The member looked up the word or expression: '{query_word}'.\n"
            f"- {self._level_calibration_guide(level)}\n"
            "1. In `semantic_note`, explain the core semantic feel of this word in Spoken American English and how native speakers use it vs. stiff/literal translations.\n"
            "2. In `collocation_frames`, provide the 4 most natural, high-utility spoken American collocations, phrasal verbs, or conversational frames built around this word."
        )
        explorer: CollocationExplorerSchema = self._call_structured(
            contents=[f"Build a Spoken American Collocation Map for: {query_word}"],
            schema_cls=CollocationExplorerSchema,
            system_instruction=system_prompt,
            temperature=0.60
        )
        self.db.ingest_chunks_to_corpus([c.model_dump() for c in explorer.collocation_frames])
        return explorer

    # =================================================================
    # 6. Active Review Usage Evaluator
    # =================================================================
    def evaluate_active_srs_usage(
        self,
        user: Dict[str, Any],
        card: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> ActiveReviewEvalSchema:
        target_chunk = card.get("chunk", "")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: The member is practicing the saved expression `{target_chunk}` ({card.get('register_tag', '')}).\n"
            f"- Expected Trigger/Context: {card.get('trigger_context', '')}\n"
            f"- Expected Pattern: {card.get('co_text_pattern', '')}\n"
            "Evaluate whether the member used `{target_chunk}` naturally, with the right collocation pattern and tone (and check pronunciation/rhythm if voice input was sent)."
        )
        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(f"Evaluate how the member used and pronounced '{target_chunk}' in this voice note.")
        else:
            contents.append(f"Member's practice sentence for '{target_chunk}':\n{user_text}")

        return self._call_structured(
            contents=contents,
            schema_cls=ActiveReviewEvalSchema,
            system_instruction=system_prompt,
            temperature=0.55
        )

    # =================================================================
    # 7. Cumulative Spoken Fluency Audit (C2 Progress Report)
    # =================================================================
    def generate_fluency_audit(self, user: Dict[str, Any]) -> str:
        uid = user["user_id"]
        stats = self.db.get_user_stats(uid)
        vault = self.db.get_user_vault(uid, limit=10)
        weaknesses = self.db.get_weaknesses(uid, limit=8)
        history = self.db.get_history(uid, limit=12)

        vault_str = ", ".join([f"{v['chunk']} ({v['register_tag']})" for v in vault]) if vault else "Building initial Vault"
        weak_str = "; ".join([f"'{w['user_said']}' -> '{w['native_fix']}' ({w['category']})" for w in weaknesses]) if weaknesses else "No recurring slips logged yet"
        user_turns = [h["content"] for h in history if h["role"] == "user"]
        turns_str = " | ".join(user_turns[-6:]) if user_turns else "Ready for initial turns"

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            "TASK: Generate a personalized, insightful 'Spoken Fluency & Style Audit' for this club member.\n"
            f"- Collected Vault Phrases ({stats['total_vault']} total, {stats['stealth_mastered']} absorbed in live chat): {vault_str}\n"
            f"- Logged Phrasing & Accent Habits ({stats['active_errors']} active, {stats['mastered_errors']} mastered): {weak_str}\n"
            f"- Recent Member Turns: {turns_str}\n"
            "Be encouraging, specific, and perceptive—like a personal New York rhetoric and accent coach."
        )

        try:
            audit: SpokenFluencyAuditSchema = self._call_structured(
                contents=["Generate the member's Spoken Fluency & Style Audit now."],
                schema_cls=SpokenFluencyAuditSchema,
                system_instruction=system_prompt,
                temperature=0.65
            )
            strengths_html = "\n".join([f"  • ✨ {html.escape(s)}" for s in audit.naturalness_highlights[:3]])
            focus_html = "\n".join([f"  • 🎯 {html.escape(f)}" for f in audit.focus_areas_under_pressure[:3]])
            return (
                "📊 <b>Your Spoken American Fluency & Style Audit</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📓 <b>Vault:</b> <code>{stats['total_vault']}</code> phrases | ✨ <b>Absorbed in Chat:</b> <code>{stats['stealth_mastered']}</code> | ✅ <b>Polished Habits:</b> <code>{stats['mastered_errors']}</code>\n\n"
                f"🗽 <b>Current Style & Presence:</b>\n{html.escape(audit.overall_vibe_assessment)}\n\n"
                f"🌟 <b>What's Sounding Natural:</b>\n{strengths_html}\n\n"
                f"🔬 <b>Priority Focus Areas Under Pressure:</b>\n{focus_html}\n\n"
                f"🧭 <b>Coach's Next-Step Gameplan:</b>\n<i>{html.escape(audit.recommended_next_steps)}</i>"
            )
        except Exception as e:
            logger.warning(f"Fluency audit fallback: {e}")
            return (
                "📊 <b>Your Spoken Fluency Snapshot</b>\n"
                f"• Saved Phrases: <code>{stats['total_vault']}</code> | Absorbed in Chat: <code>{stats['stealth_mastered']}</code>\n"
                f"• Active Habits Being Polished: <code>{stats['active_errors']}</code> | Mastered: <code>{stats['mastered_errors']}</code>\n\n"
                "Keep sending voice notes in <b>🗣️ NYC Banter</b> and <b>🥊 Devil's Advocate</b> to deepen your diagnostic profile!"
            )

    # =================================================================
    # 8. Native Text & Subtext Miner
    # =================================================================
    def mine_text_and_subtext(self, user: Dict[str, Any], source_text: str) -> LoungeTurnResponse:
        level = user.get("level", "C1")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"ACTIVE ROOM: 🔬 NATIVE TEXT & SUBTEXT MINER ({self._level_calibration_guide(level)}).\n"
            "The member pasted an English passage, quote, or dialogue transcript.\n"
            "1. In `lounge_reply`, unpack the tone, unspoken subtext, and stylistic feel of the passage.\n"
            "2. Show how the core message sounds across 🎙️ Articulate, ☕ Casual, and 🎭 Witty vibes.\n"
            "3. Extract the 3 most valuable spoken expressions, phrasal verbs, or discourse markers from (or inspired by) the text."
        )
        return self._call_structured(
            contents=[f"MINE THIS PASSAGE FOR SPOKEN EXPRESSIONS & SUBTEXT:\n{source_text}"],
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.60
        )

    # =================================================================
    # 9. Dual-Host NYC Radio Studio Generator (Andrew & Ava)
    # =================================================================
    def generate_podcast_episode(self, user: Dict[str, Any], custom_topic: str = "") -> PodcastEpisodeSchema:
        uid = user["user_id"]
        level = user.get("level", "C1")
        vault = self.db.get_user_vault(uid, limit=5)
        weaknesses = self.db.get_weaknesses(uid, limit=3)

        vault_chunks = [v["chunk"] for v in vault]
        weak_fixes = [w["native_fix"] for w in weaknesses]

        topic_prompt = (
            f"Topic requested by listener: '{custom_topic}'."
            if custom_topic else
            "Invent a lively, captivating New York cultural observation, everyday dilemma, or funny real-life story."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            "ACTIVE ROOM: 🎙️ THE RADIO LOUNGE (Andrew & Ava).\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- {topic_prompt}\n"
            f"- Member's Saved Vault Phrases to weave in naturally (if empty, choose 3 fresh expressions): {vault_chunks}\n"
            f"- Target Phrasing Upgrades to reinforce: {weak_fixes}\n"
            "- Write an 8 to 10 turn dialogue between `male_host` (Andrew) and `female_host` (Ava).\n"
            "- Make it sound like an authentic, witty New York radio show. Twice during the conversation, have the hosts briefly pause ('Wait, I love how you put that...') to highlight why a target phrase works so well in that exact moment before continuing."
        )

        return self._call_structured(
            contents=["Record the complete Dual-Host NYC Radio episode now."],
            schema_cls=PodcastEpisodeSchema,
            system_instruction=system_prompt,
            temperature=0.78
        )

    # =================================================================
    # 10. Generative Active Workouts (Including Contrastive Stress Lab)
    # =================================================================
    def build_active_drill(self, user: Dict[str, Any], drill_type: str) -> Dict[str, Any]:
        uid = user["user_id"]
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vault = self.db.get_user_vault(uid, limit=8)
        weaknesses = self.db.get_weaknesses(uid, limit=5)

        vault_pool = [v["chunk"] for v in vault]
        weak_pool = [w["native_fix"] for w in weaknesses]

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Create a fresh `{drill_type}` workout.\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- Preferred Vibe: {REGISTERS_MAP.get(pref_reg, '🎙️ Articulate')}\n"
            f"- Member's Saved Phrases: {vault_pool}\n"
            f"- Member's Recent Upgrades: {weak_pool}\n\n"
            "WORKOUT SPECIFICATIONS:\n"
            "1. If `forced_output`:\n"
            "   - Pick 3 target expressions (prioritize Member's Saved Phrases & Recent Upgrades; if fewer than 3 exist, invent fresh natural American expressions).\n"
            "   - Invent a vivid, realistic social or workplace scenario where all 3 expressions naturally fit together.\n"
            "2. If `contrastive_stress`:\n"
            "   - Create 1 natural everyday American sentence (10-14 words) in `target_audio_sentence` where shifting the primary stress to 3 different words completely changes the hidden implication/subtext.\n"
            "   - In `scenario_prompt`, show the 3 stress variations (using CAPS on the stressed word) and explain the exact unspoken subtext of each variation, then challenge the member to record a voice note delivering Variation #2 or #3!\n"
            "   - In `phonetic_prosody_map`, show the connected speech & pitch contour.\n"
            "3. If `shadowing`:\n"
            "   - Create a fresh, natural 1-2 sentence American line rich in Flap-T, consonant-to-vowel linking, tense/lax vowel contrasts, and unstressed Schwa reductions in `target_audio_sentence`.\n"
            "   - Provide its exact rhythm map with primary stress CAPS, thought-group pause bars (|), and intonation arrows (↗/↘) in `phonetic_prosody_map`.\n"
            "4. If `micro_dictation`:\n"
            "   - Create a fast everyday American sentence containing natural connected-speech reductions ('did you' -> 'didja', 'going to' -> 'gonna', 'would have' -> 'woulda', Flap-T) in `target_audio_sentence`.\n"
            "   - Explain the exact linked/reduced sounds in `hidden_reductions_explained`.\n"
            "5. If `diagnostic_test`:\n"
            "   - Create an engaging, multi-layered real-life dilemma or storytelling prompt in `scenario_prompt`."
        )

        try:
            drill: DynamicDrillSchema = self._call_structured(
                contents=[f"Create a fresh `{drill_type}` workout now."],
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
                        f"🔥 <b>{html.escape(drill.drill_title or 'The 3-Phrase Challenge')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Reply with <b>one voice note or text message</b> reacting to this moment while naturally weaving in <b>all 3 expressions</b>:\n\n"
                        f"{t_list}\n\n"
                        f"🗽 <b>The Situation:</b>\n<i>{html.escape(drill.scenario_prompt)}</i>"
                    ),
                    "tts_target": ""
                }

            elif drill_type == "contrastive_stress":
                target_sent = drill.target_audio_sentence or "I never said she stole my money."
                self.db.update_session_meta(
                    uid,
                    drill_target_sentence=target_sent,
                    drill_phonetic_hint=drill.scenario_prompt,
                    drill_kind="contrastive_stress"
                )
                return {
                    "text": (
                        f"🎭 <b>{html.escape(drill.drill_title or 'Contrastive Stress & Subtext Lab')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>Base Line:</b>\n<code>{html.escape(target_sent)}</code>\n\n"
                        f"🔍 <b>How Shifting Stress Changes the Unspoken Subtext:</b>\n{html.escape(drill.scenario_prompt)}\n\n"
                        f"🎧 <b>Rhythm & Pitch Contour:</b>\n<i>{html.escape(drill.phonetic_prosody_map)}</i>\n\n"
                        "👇 <b>Your Challenge:</b> Send a voice note reading the base line while placing heavy pitch stress on ONE specific word to convey that exact hidden meaning!"
                    ),
                    "tts_target": target_sent
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
                        f"👂 <b>{html.escape(drill.drill_title or 'Fast Ear Catcher')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "1️⃣ Listen closely to the natural-speed American audio clip below.\n"
                        "2️⃣ Type out the <b>exact full sentence</b> you heard.\n"
                        "3️⃣ I'll show you how the linked vowels and Flap-T reductions blended together!"
                    ),
                    "tts_target": clean_sent
                }

            elif drill_type == "shadowing":
                target_sent = drill.target_audio_sentence or "Honestly, I thought about pushing back, but I wound up just letting it slide."
                phonetic = drill.phonetic_prosody_map or "AA-nist-lee | I thawd-uh-bout pushing BACK ↗ | bud-I wound-up just ledding-it SLIDE ↘."
                self.db.update_session_meta(
                    uid,
                    drill_target_sentence=target_sent,
                    drill_phonetic_hint=phonetic,
                    drill_kind="shadowing"
                )
                return {
                    "text": (
                        f"🎙️ <b>{html.escape(drill.drill_title or 'Rhythm & Shadowing Workout')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>Target Line:</b>\n<code>{html.escape(target_sent)}</code>\n\n"
                        f"🎧 <b>Rhythm, Stress & Linking Map:</b>\n<i>{html.escape(phonetic)}</i>\n\n"
                        f"💡 <b>Coach Note:</b> {html.escape(drill.hidden_reductions_explained or drill.scenario_prompt)}\n\n"
                        "👇 Listen to the model clip below, then <b>send a voice note matching the exact cadence</b> (and tap <code>🪞 Voice Mirror</code> on my reply!):"
                    ),
                    "tts_target": target_sent
                }

            else:
                return {
                    "text": (
                        f"🧭 <b>{html.escape(drill.drill_title or 'Spoken Flow & Prosody Checkup')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Send a <b>30-to-45 second voice note</b> (or a short paragraph) responding naturally to this prompt:\n\n"
                        f"🗽 <i>{html.escape(drill.scenario_prompt)}</i>\n\n"
                        "I'll listen for your spoken rhythm, vowel precision, word stress, and natural phrasing!"
                    ),
                    "tts_target": ""
                }
        except Exception as e:
            logger.warning(f"Drill fallback: {e}")
            return {
                "text": (
                    "🎯 <b>Live Spoken Workout</b>\n"
                    "Send a voice note or text describing a recent plan that changed at the last minute, and we'll polish your phrasing and rhythm!"
                ),
                "tts_target": ""
            }
