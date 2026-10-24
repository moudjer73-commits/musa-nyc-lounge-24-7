# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import re
"""
ai_engine.py — Conversational, Phonetic & Pragmatic Brain for Musa's NYC Lounge
"""
import re
import json
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
    PERSONAS,
    CAST_PRESETS,
    REGISTERS_MAP,
    REGISTER_DESCRIPTIONS
)
from database import LoungeDatabase

logger = logging.getLogger("MusaNYCLounge.AI")


class DiagnosticItem(BaseModel):
    category: str = Field(
        description="Pick ONE phrasing category: '🎭 Natural Flow', '💎 Lexical Upgrade', '📐 Spoken Structure', or '🗽 Pragmatic Nuance'. NEVER use pronunciation/vowel categories."
    )
    user_said: str = Field(description="Exact segment from the member's message that can be phrased more naturally.")
    native_fix: str = Field(description="Crisp, natural spoken American phrasing upgrade (NEVER put IPA phonetic symbols like /.../ here).")
    why_note: str = Field(description="1-sentence pragmatic or stylistic explanation of why native speakers prefer this phrasing.")


class LexicalFrameUpgrade(BaseModel):
    chunk: str = Field(description="High-value C1/C2 American phrasal verb, collocation, idiom, or conversational gambit.")
    register_tag: str = Field(description="Strictly one of: '🎙️ Articulate', '☕ Casual', or '🎭 Witty'.")
    b2_foil: str = Field(
        default="",
        description="The flat, textbook B2 phrase learners usually overuse instead of this native expression."
    )
    explanation: str = Field(description="Clear meaning, tone nuance, and semantic distinction from near-synonyms.")
    trigger: str = Field(description="When to reach for it: the exact real-life social or mental moment.")
    pattern: str = Field(description="Colligation & pattern: typical verb/preposition collocations and grammatical frame.")
    followup: str = Field(description="Natural follow-up: the next sentence an American says right after this.")
    example: str = Field(description="Vivid, authentic everyday New York example sentence.")
    cloze_sentence: str = Field(
        default="",
        description="The exact `example` sentence above, but with the target expression replaced by '______'."
    )


class ChatBubbleLine(BaseModel):
    speaker: str = Field(
        description="Strictly one of the active hosts at the table: 'marcus', 'chloe', or 'jake'."
    )
    pacing: str = Field(
        default="normal",
        description="Strictly 'fast_interjection', 'normal', or 'thoughtful_slow'."
    )
    text: str = Field(
        description="This host's spoken conversational turn (without speaker name prefix). Wrap any naturally woven Vault phrases in <i>italics</i>."
    )


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(
        default="",
        description="If voice input was provided, the exact verbatim transcription of what the user said. Empty if text input."
    )
    responding_persona: str = Field(
        default="marcus",
        description="Primary host voice: strictly 'marcus', 'chloe', or 'jake'."
    )
    chat_turns: List[ChatBubbleLine] = Field(
        default_factory=list,
        description="REQUIRED: The structured conversational turns from the active host(s) at the table. If 1 host (Solo): 1 turn. If 2 hosts (Duo): 2 to 3 interactive turns between both hosts. If 3 hosts (Trio): 3 to 4 lively turns where ALL THREE hosts ('marcus', 'chloe', AND 'jake') speak and react to the user and each other!"
    )
    lounge_reply: str = Field(
        default="",
        description="Fallback summary; main display is built from `chat_turns`."
    )
    rewrite_articulate: str = Field(description="Alternative 1 (Natural Spoken Flow — 100% MEMBER'S FIRST-PERSON POV): How the MEMBER themselves expresses their message or performs the requested social act (e.g., if member says 'Apologize for being late', write what the late person says: 'Hey, so sorry I'm running late—the trains were backed up'). NEVER write the hosts' response ('Don't worry about it') here!")
    rewrite_casual: str = Field(description="Alternative 2 (Idiomatic & Phrasal — 100% MEMBER'S FIRST-PERSON POV): A second idiomatic way the MEMBER themselves voices their message or performs the requested social act using phrasal verbs (e.g., 'My bad for holding things up, guys—got totally stuck on the L train'). NEVER write the hosts' reply ('No biggie / Don't sweat it') here!")
    rewrite_witty: str = Field(description="Alternative 3 (Ellipsis & Punchy — 100% MEMBER'S FIRST-PERSON POV): A concise, high-impact spoken way the MEMBER themselves says their message using conversational ellipsis or fronting (e.g., 'Barely made it out alive—sorry for the wait, guys'). NEVER write from the hosts' perspective!")
    self_repair_pivot: str = Field(default="", description="Leave empty string ''.")
    acoustic_prosody_xray: str = Field(default="", description="Leave empty string '' in chat mode.")
    tts_clean_target: str = Field(
        description="Clean plain-text version of the recast that matches the member's ACTIVE VIBE (no brackets or phonetic symbols) ready for voice synthesis."
    )
    mirror_clean_target: str = Field(
        default="",
        description="A clean, grammatically natural version of the user's sentence keeping their original word sequence for 1-to-1 Voice Mirror comparison."
    )
    diagnostics: List[DiagnosticItem] = Field(default_factory=list, description="Up to 2 spoken phrasing upgrades on the member's sentence tailored to the active Vibe. CRITICAL: Leave EMPTY [] if the member sent a short greeting ('Hi guys') OR if the member is describing a scenario/topic title ('Apologizing for being late', 'How to disagree').")
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(default_factory=list, description="3 fresh, 100% SPOKEN C1/C2 American expressions FOR THE MEMBER'S OWN ROLE that strictly match BOTH the active Spoken Vibe (Articulate/Casual/Witty/Executive/Diplomatic) AND the active Chat Mode/Room (Spontaneous Chat, Storytelling Gym, Devil's Advocate, or What to Say When). Never repeat Candidate Vault Phrases and never use stiff written corporate-memo phrases.")


class PodcastTurnLine(BaseModel):
    speaker: str = Field(description="Strictly 'marcus', 'chloe', or 'jake'.")
    pacing: str = Field(default="normal", description="Strictly 'fast_interjection', 'normal', or 'thoughtful_slow'.")
    text: str = Field(description="Natural spoken line.")


class PodcastEpisodeSchema(BaseModel):
    title: str = Field(description="Catchy NYC Radio Lounge episode title.")
    format_mode: str = Field(default="trio", description="'trio' or 'duo'.")
    target_chunks_covered: List[str] = Field(description="List of 5 to 6 target expressions woven into the episode.")
    script: List[PodcastTurnLine] = Field(description="28 to 36 dynamic back-and-forth conversational turns (~500–650 words total) building a 3+ minute episode.")
    key_takeaways: List[LexicalFrameUpgrade] = Field(description="4 core C1/C2 Spoken Expressions featured in the episode.")


class DynamicStudioSparkSchema(BaseModel):
    headline: str = Field(description="Engaging conversation starter headline.")
    lead_persona: str = Field(default="marcus", description="'marcus', 'chloe', or 'jake'.")
    scenario_or_question: str = Field(description="A fresh, vivid scenario, real-life dilemma, or debate stance.")
    suggested_chunks_to_try: List[str] = Field(description="3 natural C1/C2 American expressions to try.")
    pro_tip: str = Field(description="A 1-sentence coaching tip.")


class DynamicDrillSchema(BaseModel):
    drill_title: str = Field(description="Engaging title of the workout.")
    scenario_prompt: str = Field(description="Interactive scenario or challenge instructions.")
    target_chunks: List[str] = Field(default_factory=list, description="3 target expressions.")
    target_audio_sentence: str = Field(default="", description="Natural American spoken sentence.")
    phonetic_prosody_map: str = Field(default="", description="Phonetic respelling + Nuclear stress CAPS + pause bars (|).")
    hidden_reductions_explained: str = Field(default="", description="Clear explanation of reductions or stress subtext.")


class CollocationExplorerSchema(BaseModel):
    headword: str = Field(description="The word or phrase being explored.")
    semantic_note: str = Field(description="Crisp explanation of how Americans naturally prime and use this word.")
    collocation_frames: List[LexicalFrameUpgrade] = Field(description="4 high-frequency spoken American collocations.")


class ActiveReviewEvalSchema(BaseModel):
    used_naturally: bool = Field(description="True if the user used the target expression naturally.")
    recommended_grade: int = Field(description="5 if effortless, 3 if minor slip, 1 if misused.")
    coach_feedback: str = Field(description="Warm, sharp coaching feedback.")
    polished_version: str = Field(description="How a New Yorker would say the user's exact sentence.")


class SpokenFluencyAuditSchema(BaseModel):
    overall_vibe_assessment: str = Field(description="Insightful assessment of the member's spoken style.")
    naturalness_highlights: List[str] = Field(description="2 to 3 specific strengths.")
    focus_areas_under_pressure: List[str] = Field(description="2 to 3 specific habits to polish next.")
    recommended_next_steps: str = Field(description="Concrete 3-step coaching plan from Marcus, Chloe, and Jake.")


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
        return genai.Client(api_key=key, http_options={"timeout": 45000})

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
            self._dead_models_until.clear()
            return self._discovered_models

        self._dead_models_until.clear()
        return list(GEMINI_MODELS_CHAIN)

    def _call_structured(
        self,
        contents: List[Any],
        schema_cls: Any,
        system_instruction: str,
        temperature: float = 0.72,
        max_output_tokens: int = 4096
    ) -> Any:
        last_err = None
        for _ in range(max(1, min(2, len(self.keys_pool)))):
            client = self._get_client()
            models_to_try = self._get_active_models(client)
            for model_name in models_to_try:
                for attempt in range(2):
                    try:
                        cfg_kwargs: Dict[str, Any] = {
                            "system_instruction": system_instruction,
                            "temperature": temperature,
                            "max_output_tokens": max_output_tokens,
                            "response_mime_type": "application/json",
                            "response_schema": schema_cls
                        }
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(**cfg_kwargs)
                        )
                        if getattr(resp, "parsed", None) is not None:
                            return resp.parsed
                        raw_txt = getattr(resp, "text", "") or ""
                        if raw_txt:
                            raw_txt = raw_txt.strip()
                            if raw_txt.startswith("```"):
                                raw_txt = raw_txt.strip("`").removeprefix("json").strip()
                            return schema_cls.model_validate_json(raw_txt)
                    except Exception as e:
                        last_err = e
                        err_str = str(e)
                        if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                            self._dead_models_until[model_name] = time.time() + 86400
                            break
                        if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str) and attempt == 0:
                            time.sleep(1.5 + random.uniform(0.2, 0.8))
                            continue
                        logger.warning(f"Model {model_name} attempt note: {err_str[:120]}")
                        break
        raise RuntimeError(f"Service temporarily busy: {last_err}")

    @staticmethod
    def _level_calibration_guide(level: str) -> str:
        calibrations = {
            "B1": "Intermediate depth: clear, high-frequency everyday American phrasal verbs.",
            "B2": "Upper-Intermediate depth: natural idiomatic collocations and conversational gambits.",
            "C1": "Advanced depth: sophisticated spoken structure (Heads & Tails, clefts, natural hedging), subtle subtext, and high-utility collocations.",
            "C2": "Mastery depth: native-like intuition, dry understatement, fast connected-speech reductions, semantic precision, and razor-sharp wit."
        }
        return calibrations.get(level, calibrations["C1"])

    @staticmethod
    def _detect_addressed_host(user_text: str) -> Optional[str]:
        low = (user_text or "").lower()
        for h in ("jake", "chloe", "marcus"):
            if re.search(rf"\b{h}\b", low):
                return h
        return None

    @classmethod
    def _persona_routing_directive(cls, active_persona: str, mode: str, user_text: str = "") -> str:
        preset = CAST_PRESETS.get(active_persona, CAST_PRESETS["trio"])
        hosts = list(preset["hosts"])
        count = preset["count"]
        called_host = cls._detect_addressed_host(user_text)

        if count == 3:
            lead_note = (
                f"- IMPORTANT: Musa directly addressed `{called_host.capitalize()}` in his message, so `{called_host}` MUST speak FIRST in Turn 1, and then the other two hosts jump in right after!"
                if called_host else
                "- Turn 1: Have the most fitting host react directly to Musa's message first."
            )
            return (
                "ACTIVE TABLE CAST: 🗽 3-HOST TRIO ROUNDTABLE (`marcus`, `chloe`, AND `jake`).\n"
                "- MANDATORY SCHEMA RULE: Populate `chat_turns` with 3 to 4 conversational turns where ALL THREE hosts (`marcus`, `chloe`, and `jake`) speak!\n"
                f"{lead_note}\n"
                "- Turn 2: A second host jumps in ('Wait, hold on—', 'See, I'm with Jake on this...', etc.) adding a contrasting angle or story.\n"
                "- Turn 3: The third host synthesizes the subtext or teases the others and tosses an engaging follow-up question back to Musa.\n"
                "- Do NOT put speaker names inside `ChatBubbleLine.text` (the `speaker` field handles that)."
            )
        elif count == 2:
            if called_host and called_host not in hosts:
                hosts = [called_host, hosts[0]]
            elif called_host and hosts[0] != called_host:
                hosts = [called_host, [h for h in hosts if h != called_host][0]]
            h1, h2 = hosts[0], hosts[1]
            p1, p2 = PERSONAS[h1], PERSONAS[h2]
            return (
                f"ACTIVE TABLE CAST: 👥 2-HOST DUO BANTER (`{h1}` and `{h2}` ONLY).\n"
                f"- Host 1 (Leads first): {p1['title']} — {p1['style_desc']}\n"
                f"- Host 2: {p2['title']} — {p2['style_desc']}\n"
                f"- MANDATORY SCHEMA RULE: Populate `chat_turns` with 2 to 3 interactive turns strictly alternating between `{h1}` and `{h2}`!\n"
                f"- Have `{h1}` and `{h2}` react both to Musa and to each other naturally, ending with a question for Musa."
            )
        else:
            h1 = called_host if called_host else hosts[0]
            p1 = PERSONAS.get(h1, PERSONAS["marcus"])
            return (
                f"ACTIVE TABLE CAST: 👤 1-ON-1 SOLO CHAT WITH {p1['title']} (`{h1}` ONLY).\n"
                f"- Persona Style: {p1['style_desc']}\n"
                f"- MANDATORY SCHEMA RULE: Populate `chat_turns` with exactly 1 rich, engaging turn where `speaker` is '{h1}'."
            )

    @staticmethod
    def _finalize_chat_turns(res: LoungeTurnResponse, active_persona: str, user_text: str = "") -> LoungeTurnResponse:
        preset_hosts = list(CAST_PRESETS.get(active_persona, CAST_PRESETS["trio"])["hosts"])
        if res.chat_turns:
            formatted_bubbles = []
            for ct in res.chat_turns:
                sp_k = (ct.speaker or preset_hosts[0]).strip().lower()
                if sp_k not in ("marcus", "chloe", "jake"):
                    sp_k = preset_hosts[0]
                ct.speaker = sp_k
                p_meta = PERSONAS[sp_k]
                clean_line = re.sub(r"^(?:Marcus|Chloe|Jake)\s*:\s*", "", (ct.text or "").strip(), flags=re.IGNORECASE)
                ct.text = clean_line
                if clean_line:
                    formatted_bubbles.append(f"{p_meta.get('icon', '🗽')} <b>{p_meta['name']}:</b> {clean_line}")
            if formatted_bubbles:
                res.lounge_reply = "\n\n".join(formatted_bubbles)
                res.responding_persona = res.chat_turns[0].speaker
                return res

        sp_k = preset_hosts[0]
        p_meta = PERSONAS[sp_k]
        raw_reply = (res.lounge_reply or "Hey man! Good to see you—what's on your mind today?").strip()
        clean_reply = re.sub(r"^(?:Marcus|Chloe|Jake)\s*:\s*", "", raw_reply, flags=re.IGNORECASE)
        res.responding_persona = sp_k
        res.chat_turns = [ChatBubbleLine(speaker=sp_k, pacing="normal", text=clean_reply)]
        res.lounge_reply = f"{p_meta.get('icon', '🗽')} <b>{p_meta['name']}:</b> {clean_reply}"
        return res

    def _build_mode_specific_directive(self, mode: str, session_meta: Dict[str, Any]) -> str:
        if mode in ("what_to_say", "situational_playbook", "pragmatic_playbook"):
            return (
                "ACTIVE ROOM: ⚡ WHAT TO SAY WHEN... (SITUATIONAL PRAGMATICS PLAYBOOK).\n"
                "- The member is describing a social or workplace situation/occasion (in English or Arabic), NOT speaking a literal line of dialogue.\n"
                "- 1. In `chat_turns`, have Marcus, Chloe, and Jake warmly coach the member and roleplay what real Americans instinctively say in that exact moment.\n"
                "- 2. In `rewrite_articulate`, `rewrite_casual`, and `rewrite_witty`, provide 3 READY-TO-USE spoken lines that the MEMBER themselves can say in that exact situation (all locked to the active Vibe).\n"
                "- 3. In `lexical_upgrades`, provide 3 high-utility C1/C2 spoken expressions for the PERSON IN THAT SITUATION (e.g., if the situation is apologizing for being late, give phrases for apologizing/explaining a delay like 'running behind', 'held up', 'my bad'—NOT phrases for accepting an apology!).\n"
                "- 4. Leave `diagnostics` empty `[]` unless the member explicitly asked to fix a specific sentence."
            )
        if mode == "storytelling_gym":
            return (
                "ACTIVE ROOM: 🍸 STORYTELLING & NARRATIVE GYM. "
                "Model and coach high-impact American storytelling: vivid setup hooks ('So get this—', 'Picture this:'), "
                "historical present tense for drama, pacing pauses, and punchy payoffs."
            )
        if mode == "devils_advocate":
            return (
                "ACTIVE ROOM: 😈 DEVIL'S ADVOCATE & SMART PUSHBACK LAB. "
                "Challenge the member's premise with charisma and intellectual rigor in the active Vibe. "
                "Model how Americans push back, concede a partial point ('I'll grant you that, but...'), and reframe arguments."
            )
        return (
            "ACTIVE ROOM: 💬 LIVE SPOKEN AMERICAN LOUNGE.\n"
            "- NOTE ON SCENARIO DESCRIPTIONS: If the member's message is a meta-description of a situation (e.g., 'Apologize for being late', 'Politely disagree with a boss'), treat it as a situational coaching request: give 3 ready-to-speak lines for the MEMBER'S role in `rewrite_articulate`/`rewrite_casual`/`rewrite_witty`, and 3 expressions for the MEMBER'S role in `lexical_upgrades`."
        )

    def generate_studio_spark(self, user: Dict[str, Any], mode: Optional[str] = None) -> str:
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        uid = user["user_id"]
        target_mode = mode or user.get("current_mode", "spontaneous_chat")
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        active_persona = user.get("active_persona", "trio")
        vault = self.db.get_user_vault(uid, limit=4)
        history = self.db.get_history(uid, limit=4)

        vault_list = [v["chunk"] for v in vault]
        recent_context = " | ".join([h["content"][:80] for h in history]) if history else "Fresh session"
        random_themes = [
            "unspoken social rules in modern city life",
            "a plan or trip that took a completely unexpected turn",
            "reading between the lines when a colleague or friend is being diplomatic",
            "a controversial take on productivity, ambition, or modern habits",
            "navigating last-minute changes or backing out of a commitment gracefully"
        ]
        chosen_seed = random.choice(random_themes)

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Create a fresh conversation starter for room `{target_mode}`.\n"
            f"- Active Host Setting: {active_persona} | Depth: {self._level_calibration_guide(level)}\n"
            f"- Angle: {chosen_seed} | Avoid: {recent_context} | Vault: {vault_list}"
        )

        try:
            spark: DynamicStudioSparkSchema = self._call_structured(
                contents=[f"Create a fresh conversation spark for `{target_mode}` now."],
                schema_cls=DynamicStudioSparkSchema,
                system_instruction=system_prompt,
                temperature=0.85
            )
            chunks_fmt = " • ".join([f"<code>{html.escape(c)}</code>" for c in spark.suggested_chunks_to_try[:3]])
            lead_key = (spark.lead_persona or "marcus").lower()
            lead_badge = PERSONAS.get(lead_key, PERSONAS["marcus"])["title"]
            return (
                f"🎲 <b>{html.escape(spark.headline)}</b>\n"
                f"🎙️ <i>Pitched by {lead_badge}</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🗽 {html.escape(spark.scenario_or_question)}\n\n"
                f"💎 <b>C1/C2 Expressions to Try:</b> {chunks_fmt}\n"
                f"💡 <i>Coach Tip: {html.escape(spark.pro_tip)}</i>"
            )
        except Exception as e:
            logger.warning(f"Spark fallback: {e}")
            return "🗽 <b>The Table Is Yours!</b>\nDrop a voice note or text on whatever is on your mind today!"

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
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        active_persona = user.get("active_persona", "trio")
        mode = user.get("current_mode", "spontaneous_chat")
        session_meta = self.db.get_session_meta(uid)

        stealth_items, _ = self.db.get_stealth_injection_targets(uid, limit=4)
        stealth_chunks_str = (
            ", ".join([f"'{it['chunk']}' (Use when: {it.get('trigger_context', 'fitting')})" for it in stealth_items])
            if stealth_items else "None"
        )

        weaknesses = self.db.get_weaknesses(uid, limit=3)
        weakness_str = "; ".join([f"Watch for '{w['user_said']}' -> Prefer '{w['native_fix']}'" for w in weaknesses]) if weaknesses else "None"

        rag_hits = self.db.get_rag_collocations_for_context(user_text, limit=3) if user_text else []
        rag_str = "\n".join([f"  - {r}" for r in rag_hits]) if rag_hits else "  - Prioritize high-utility C1/C2 Spoken American collocations."

        history = self.db.get_history(uid, limit=6)
        hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history]) if history else "Start of conversation."

        lang_rule = (
            "100% ENGLISH IMMERSION: Every single word of your output MUST be in English."
            if eng_only else
            "Keep all conversation and rewrites in English; you may add a brief Arabic hint ONLY inside `why_note` if helpful."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"CALIBRATION FOR THIS TURN:\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- {self._persona_routing_directive(active_persona, mode, user_text)}\n"
            f"- STRICT SESSION VIBE LOCK ({active_vibe_badge}): {vibe_rules}\n"
            f"- NO REGISTER SHIFTING: Every host in `chat_turns`, your `lounge_reply`, ALL 3 recast alternatives (`rewrite_articulate`, `rewrite_casual`, `rewrite_witty`), `tts_clean_target`, and ALL 3 `lexical_upgrades` MUST strictly belong to the {active_vibe_badge} register!\n"
            f"- SPONTANEOUS HOST PIVOTS: Use authentic spoken American contractions ('I'm', 'don't', 'should've'), conversational ellipsis ('Sounds like a plan', 'Makes sense'), natural fillers ('I mean,', 'you know,', 'honestly,'), occasional real-time hesitation ('well, let's see...', 'how do I put this—'), and zero-quota mid-sentence pivots ('—or rather,', '—actually, scratch that,') inside `chat_turns` (matching {active_vibe_badge}) whenever a thought naturally shifts mid-stream, as long as it never disrupts the spontaneity of the dialogue.\n"
            f"- {lang_rule}\n"
            f"- {self._build_mode_specific_directive(mode, session_meta)}\n\n"
            f"ORGANIC PHRASE WEAVING (CONTEXTUAL FILTER):\n"
            f"- Candidate Vault Phrases: {stealth_chunks_str}\n"
            f"- Only weave a candidate phrase into `chat_turns` if it fits this topic 100% naturally. Wrap woven phrases in `<i>phrase</i>`.\n\n"
            f"RECURRING HABITS TO WATCH FOR: {weakness_str}\n"
            f"RELEVANT COLLOCATIONS:\n{rag_str}\n\n"
            f"RECENT CONVERSATION (For topic continuity ONLY — if earlier turns used a different tone, IGNORE their tone and strictly use {active_vibe_badge} now!):\n{hist_str}"
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                "Listen carefully to the member's voice note. Transcribe verbatim in `transcription`, populate `chat_turns` matching the active cast (1, 2, or 3 hosts), provide 3 distinct native recast alternatives (`rewrite_articulate`, `rewrite_casual`, `rewrite_witty`) ALL strictly in the member's ACTIVE VIBE, `mirror_clean_target`, phrasing-only `diagnostics` (NO pronunciation/IPA notes), and 3 `lexical_upgrades`. Leave `acoustic_prosody_xray` empty."
            )
        else:
            contents.append(
                f"MEMBER MESSAGE:\n{user_text}\n\n"
                "Populate `chat_turns` with the active cast (1, 2, or 3 hosts) and provide phrasing-only `diagnostics` (NO pronunciation/vowel/IPA comments) and 3 `lexical_upgrades`. Leave `acoustic_prosody_xray` empty."
            )

        res: LoungeTurnResponse = self._call_structured(
            contents=contents,
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.72,
            max_output_tokens=4096
        )

        active_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        for up in res.lexical_upgrades:
            up.register_tag = active_badge

        res = self._finalize_chat_turns(res, active_persona=active_persona, user_text=user_text)

        used_stealth_ids: List[int] = []
        reply_lower = (res.lounge_reply or "").lower()
        for it in stealth_items:
            if it["chunk"].lower() in reply_lower:
                used_stealth_ids.append(it["id"])

        try:
            _u_txt = (user_text or "").strip().lower()
            _u_mode = (user.get("current_mode", "spontaneous_chat") if isinstance(user, dict) else "spontaneous_chat")
            if len(_u_txt.split()) <= 6 and (
                _u_txt.startswith(("apologiz", "how to ", "what to ", "asking ", "declin", "disagree", "politely ", "saying ", "telling "))
                or _u_mode not in ("spontaneous_chat", "", None)
            ):
                res.diagnostics = []
        except Exception:
            pass
        return res, used_stealth_ids

    def explore_collocations_live(self, user: Dict[str, Any], query_word: str) -> CollocationExplorerSchema:
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        level = user.get("level", "C1")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Build a Spoken American Collocation Map for '{query_word}' ({self._level_calibration_guide(level)})."
        )
        explorer: CollocationExplorerSchema = self._call_structured(
            contents=[f"Build a Spoken American Collocation Map for: {query_word}"],
            schema_cls=CollocationExplorerSchema,
            system_instruction=system_prompt,
            temperature=0.60
        )
        for c in explorer.collocation_frames:
            c.register_tag = vibe_badge
        self.db.ingest_chunks_to_corpus([c.model_dump() for c in explorer.collocation_frames])
        return explorer

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
            f"TASK: Evaluate how the member practiced `{target_chunk}` ({card.get('register_tag', '')})."
        )
        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(f"Evaluate how the member used and pronounced '{target_chunk}'.")
        else:
            contents.append(f"Member's practice sentence for '{target_chunk}':\n{user_text}")

        return self._call_structured(
            contents=contents,
            schema_cls=ActiveReviewEvalSchema,
            system_instruction=system_prompt,
            temperature=0.55
        )

    def generate_fluency_audit(self, user: Dict[str, Any]) -> str:
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        uid = user["user_id"]
        stats = self.db.get_user_stats(uid)
        vault = self.db.get_user_vault(uid, limit=10)
        weaknesses = self.db.get_weaknesses(uid, limit=8)
        history = self.db.get_history(uid, limit=12)

        vault_str = ", ".join([f"{v['chunk']} (Produced: {v.get('productive_count', 0)}x)" for v in vault]) if vault else "Building initial Vault"
        weak_str = "; ".join([f"'{w['user_said']}' -> '{w['native_fix']}'" for w in weaknesses]) if weaknesses else "None logged yet"
        user_turns = [h["content"] for h in history if h["role"] == "user"]
        turns_str = " | ".join(user_turns[-6:]) if user_turns else "Ready for initial turns"

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Generate a Spoken Fluency & Style Audit from Marcus, Chloe, and Jake.\n"
            f"Vault: {vault_str} | Habits: {weak_str} | Recent Turns: {turns_str}"
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
                f"📓 <b>Vault:</b> <code>{stats['total_vault']}</code> | 🔥 <b>Actively Produced:</b> <code>{stats.get('productive_mastered', 0)}</code> | ✅ <b>Polished Habits:</b> <code>{stats['mastered_errors']}</code>\n\n"
                f"🗽 <b>Current Style & Presence:</b>\n{html.escape(audit.overall_vibe_assessment)}\n\n"
                f"🌟 <b>What's Sounding Effortless:</b>\n{strengths_html}\n\n"
                f"🔬 <b>Priority Focus Areas Under Pressure:</b>\n{focus_html}\n\n"
                f"🧭 <b>The Trio's Next-Step Gameplan:</b>\n<i>{html.escape(audit.recommended_next_steps)}</i>"
            )
        except Exception as e:
            logger.warning(f"Fluency audit fallback: {e}")
            return "📊 <b>Your Spoken Fluency Snapshot</b>\nKeep sending voice notes in NYC Banter!"

    def mine_text_and_subtext(self, user: Dict[str, Any], source_text: str) -> LoungeTurnResponse:
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        level = user.get("level", "C1")
        active_persona = user.get("active_persona", "trio")
        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"ACTIVE ROOM: 🔬 NATIVE TEXT & SUBTEXT MINER ({self._level_calibration_guide(level)})."
        )
        res: LoungeTurnResponse = self._call_structured(
            contents=[f"MINE THIS PASSAGE FOR SPOKEN EXPRESSIONS & SUBTEXT:\n{source_text}"],
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.60
        )
        return self._finalize_chat_turns(res, active_persona=active_persona, user_text=source_text)

    def generate_podcast_episode(self, user: Dict[str, Any], custom_topic: str = "") -> PodcastEpisodeSchema:
        uid = user["user_id"]
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        vault = self.db.get_user_vault(uid, limit=5)
        weaknesses = self.db.get_weaknesses(uid, limit=3)

        vault_chunks = [v["chunk"] for v in vault]
        weak_fixes = [w["native_fix"] for w in weaknesses]

        topic_prompt = (
            f"Topic requested by listener: '{custom_topic}'."
            if custom_topic else
            "Pick a relatable, captivating New York cultural observation, everyday dilemma, or real-life human moment."
        )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            "ACTIVE ROOM: 🎙️ THE RADIO LOUNGE (Andrew & Ava).\n"
            f"- STRICT VIBE LOCK FOR BOTH HOSTS ({vibe_badge}): {vibe_rules}\n"
            f"- Both Andrew and Ava MUST speak strictly in the {vibe_badge} register throughout the entire show, and all 3 `key_takeaways` MUST match {vibe_badge}.\n"
            f"- {self._level_calibration_guide(level)}\n"
            f"- {topic_prompt}\n"
            f"- Candidate Vault Phrases (weave in ONLY the ones that fit the topic 100% naturally; ignore any that feel forced): {vault_chunks}\n"
            f"- Target Phrasing Upgrades (use organically if relevant): {weak_fixes}\n"
            "- Write an 8 to 10 turn unscripted-feeling dialogue between `male_host` (Andrew) and `female_host` (Ava).\n"
            "- REAL HUMAN FLOW & UNFORCED PIVOTS: Make them sound like two real people thinking out loud in real time. "
            "Without any fixed quota or numerical limit, let the hosts naturally recalibrate or pivot mid-sentence "
            f"(matching the {vibe_badge} vibe) whenever the flow of thought genuinely calls for it, provided it never disrupts the spontaneity of their conversation.\n"
            "- NO DIDACTIC META-PAUSES: Never pause the conversation to lecture or say 'I love how you used that phrase.' Keep the illusion of a real, spontaneous NYC podcast 100% intact."
        )

        ep: PodcastEpisodeSchema = self._call_structured(
            contents=[f"Record the complete Dual-Host NYC Radio episode strictly in {vibe_badge} mode with 100% natural human flow now."],
            schema_cls=PodcastEpisodeSchema,
            system_instruction=system_prompt,
            temperature=0.76
        )
        for u in ep.key_takeaways:
            u.register_tag = vibe_badge
        return ep


    def build_active_drill(self, user: Dict[str, Any], drill_type: str) -> Dict[str, Any]:
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        uid = user["user_id"]
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        active_vibe_badge = vibe_badge
        vault = self.db.get_user_vault(uid, limit=8)
        weaknesses = self.db.get_weaknesses(uid, limit=5)

        vault_pool = [v["chunk"] for v in vault]
        weak_pool = [w["native_fix"] for w in weaknesses]

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Create a fresh `{drill_type}` workout ({self._level_calibration_guide(level)}).\n"
            f"Vault: {vault_pool} | Recent Upgrades: {weak_pool}"
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
                        f"🔥 <b>{html.escape(drill.drill_title or 'The 3-Phrase Speaking Challenge')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"{t_list}\n\n"
                        f"🗽 <b>The Situation:</b>\n<i>{html.escape(drill.scenario_prompt)}</i>"
                    ),
                    "tts_target": ""
                }
            elif drill_type == "contrastive_stress":
                target_sent = drill.target_audio_sentence or "I never said she stole my money."
                self.db.update_session_meta(uid, drill_target_sentence=target_sent, drill_phonetic_hint=drill.scenario_prompt, drill_kind="contrastive_stress")
                return {
                    "text": (
                        f"🎭 <b>{html.escape(drill.drill_title or 'Contrastive Stress & Subtext Lab')}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 <b>Base Line:</b> <code>{html.escape(target_sent)}</code>\n\n"
                        f"🔍 <b>Stress Variations:</b>\n{html.escape(drill.scenario_prompt)}"
                    ),
                    "tts_target": target_sent
                }
            elif drill_type == "micro_dictation":
                clean_sent = drill.target_audio_sentence or "What did you wind up doing after the meeting got canceled?"
                self.db.update_session_meta(uid, drill_target_sentence=clean_sent, drill_phonetic_hint=drill.hidden_reductions_explained, drill_kind="micro_dictation")
                return {
                    "text": "👂 <b>Fast Ear Catcher</b>\nListen closely to the audio clip below and type the exact sentence you heard:",
                    "tts_target": clean_sent
                }
            elif drill_type == "shadowing":
                target_sent = drill.target_audio_sentence or "Honestly, I thought about pushing back, but I wound up just letting it slide."
                phonetic = drill.phonetic_prosody_map or "AA-nist-lee | I thawd-uh-bout pushing BACK ↗ | bud-I wound-up just ledding-it SLIDE ↘."
                self.db.update_session_meta(uid, drill_target_sentence=target_sent, drill_phonetic_hint=phonetic, drill_kind="shadowing")
                return {
                    "text": (
                        f"🎙️ <b>{html.escape(drill.drill_title or 'Rhythm & Shadowing Workout')}</b>\n"
                        f"🎯 <code>{html.escape(target_sent)}</code>\n\n"
                        f"🎧 <i>{html.escape(phonetic)}</i>"
                    ),
                    "tts_target": target_sent
                }
            else:
                return {
                    "text": f"🧭 <b>Spoken Flow Checkup</b>\n🗽 <i>{html.escape(drill.scenario_prompt)}</i>",
                    "tts_target": ""
                }
        except Exception as e:
            logger.warning(f"Drill fallback: {e}")
            return {"text": "🎯 <b>Live Spoken Workout</b>\nSend a voice note describing a recent plan that changed!", "tts_target": ""}
