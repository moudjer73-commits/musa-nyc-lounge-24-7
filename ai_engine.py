from pathlib import Path
# -*- coding: utf-8 -*-
"""
ai_engine.py — Conversational, Phonetic & Pragmatic Brain for Musa's NYC Lounge
"""
import re
import json
import time
import html
import random
import logging
from typing import List, Dict, Optional, Any, Tuple, Literal
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
        default="🎭 Natural Flow",
        description="Strictly one of: '🎭 Natural Flow', '💎 Lexical Upgrade', '📐 Spoken Structure', or '🗽 Pragmatic Nuance'. Never flag opinions or reasoning as language errors."
    )
    user_said: str = Field(
        default="",
        description="Exact verbatim substring from the member's message that contains a genuine grammatical error, awkward non-native collocation, or clear register mismatch. Leave empty if none."
    )
    native_fix: str = Field(
        default="",
        description="Natural spoken American correction that preserves the member's exact meaning, certainty, and connotation."
    )
    why_note: str = Field(
        default="",
        description="1-sentence explanation distinguishing whether this fixes an error or clarifies register/connotation."
    )


class LexicalFrameUpgrade(BaseModel):
    chunk: str = Field(
        default="",
        description="High-value C1/C2 spoken American phrasal verb, collocation, idiom, or conversational gambit."
    )
    register_tag: str = Field(
        default="🎙️ Articulate",
        description="Strictly one of the 5 supported registers: '🎙️ Articulate', '☕ Casual', '🎭 Witty', '💼 Executive', or '🤝 Diplomatic'."
    )
    b2_foil: str = Field(
        default="",
        description="Simpler everyday phrase for contrast."
    )
    arabic_equivalent: str = Field(
        default="",
        description="REQUIRED: Accurate Arabic equivalent (المكافئ العربي الدقيق) of the expression in context."
    )
    explanation: str = Field(
        default="",
        description="Concise English note on tone nuance, connotation, and usage boundaries."
    )
    trigger: str = Field(
        default="",
        description="When to reach for it: the exact real-life social or conversational moment."
    )
    pattern: str = Field(
        default="",
        description="Typical grammatical frame and collocation pattern."
    )
    followup: str = Field(
        default="",
        description="Natural follow-up line an American might say right after."
    )
    example: str = Field(
        default="",
        description="Authentic 1-sentence spoken example demonstrating the exact meaning in context."
    )
    cloze_sentence: str = Field(
        default="",
        description="The exact `example` sentence above with the target expression replaced by '______'."
    )


class ChatBubbleLine(BaseModel):
    speaker: str = Field(default="", 
        description="Strictly one of the active hosts at the table: 'marcus', 'chloe', or 'jake'."
    )
    pacing: str = Field(
        default="normal",
        description="Strictly 'fast_interjection', 'normal', or 'thoughtful_slow'."
    )
    text: str = Field(default="", 
        description="This host's spoken conversational turn (without speaker name prefix). Wrap any naturally woven Vault phrases in <i>italics</i>."
    )


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(
        default="",
        description="Verbatim transcription if voice input was provided; empty string for text input."
    )
    responding_persona: str = Field(
        default="marcus",
        description="Primary host voice: strictly 'marcus', 'chloe', or 'jake'."
    )
    chat_turns: List[ChatBubbleLine] = Field(
        default_factory=list,
        description="Conversational turns from the active host(s) at the table reacting naturally to the member and to one another."
    )
    lounge_reply: str = Field(
        default="",
        description="Combined spoken reply built from `chat_turns`."
    )
    rewrite_articulate: str = Field(
        default="",
        description="Strategy 1 — Natural Flow (strictly in the member's ACTIVE register): Preserve the full thought, scope, causality, and degree of certainty while optimizing spoken rhythm. In Witty mode, use dry understatement or comic contrast in imagery without distorting the claim."
    )
    rewrite_casual: str = Field(
        default="",
        description="Strategy 2 — Punchy Cut (strictly in the member's ACTIVE register): Compress the member's exact thought concisely without exaggerating certainty or scope. In Witty mode, keep the compression sharp and ironical without turning a moderate claim into an absolute."
    )
    rewrite_witty: str = Field(
        default="",
        description="Strategy 3 — Conversational Fronting / Ellipsis (strictly in the member's ACTIVE register, 1st-person POV): Recast the member's thought using spoken fronting or conversational ellipsis. In Witty mode, land a dry observational twist while keeping the exact meaning."
    )
    self_repair_pivot: str = Field(default="", description="Leave empty string ''.")
    acoustic_prosody_xray: str = Field(default="", description="1-sentence rhythm/stress note for voice input; empty string for text input.")
    tts_clean_target: str = Field(
        default="",
        description="Clean plain-text version of `rewrite_articulate` ready for TTS voice synthesis."
    )
    mirror_clean_target: str = Field(
        default="",
        description="Clean, natural version of the member's sentence for 1-to-1 Voice Mirror comparison."
    )
    diagnostics_status: str = Field(
        default="checked_no_corrections",
        description="Strictly one of: 'corrections_found' (when `diagnostics` has a genuine language error), 'checked_no_corrections' (when the member's English sentence was evaluated and is already natural), or 'not_assessed' (when the input was in Arabic, a short greeting, or a scenario/topic prompt)."
    )
    diagnostics: List[DiagnosticItem] = Field(
        default_factory=list,
        description="At most 1 genuine grammatical, collocational, or register fix. Must be empty `[]` whenever `diagnostics_status` is 'checked_no_corrections' or 'not_assessed'."
    )
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(
        default_factory=list,
        description="3 distinct, reusable spoken expressions suited to the active register and topic, each with a populated `arabic_equivalent` and `explanation`."
    )


class SituationGambitResponse(BaseModel):
    situation_summary: str = Field(
        default="",
        description="1-sentence pragmatic reading of the speech act, relationship dynamics, and social stakes."
    )
    responding_persona: str = Field(
        default="chloe",
        description="Lead coach for this scenario ('chloe', 'marcus', or 'jake')."
    )
    host_commentary: str = Field(
        default="",
        description="1-2 natural spoken sentences from the lead host framing the strategy before giving the lines."
    )
    best_line: str = Field(
        default="",
        description="The #1 immediate, instinctive first-person spoken line an American naturally says in this exact situation (matching the active register)."
    )
    softer_alternative: str = Field(
        default="",
        description="A gentler, face-saving first-person alternative that lowers tension while keeping the same intent."
    )
    firmer_alternative: str = Field(
        default="",
        description="A firmer, direct boundary-setting first-person alternative that holds the line without aggression."
    )
    why_it_works: str = Field(
        default="",
        description="Concise pragmatic explanation of why `best_line` works socially and what tone trap it avoids."
    )
    arabic_equivalent: str = Field(
        default="",
        description="Accurate Arabic translation / pragmatic equivalent (المكافئ العربي الدقيق) of the recommended line."
    )
    follow_up_line: str = Field(
        default="",
        description="What the member should say next if the other person pushes back or gets defensive."
    )
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(
        default_factory=list,
        description="3 savable situational gambits from this scenario (for the member's Vault), each with `chunk`, `arabic_equivalent`, `explanation`, and `example`."
    )


class PodcastTurnLine(BaseModel):
    speaker: str = Field(default="", description="Strictly 'marcus', 'chloe', or 'jake'.")
    pacing: str = Field(default="normal", description="Strictly 'fast_interjection', 'normal', or 'thoughtful_slow'.")
    text: str = Field(default="", description="Natural spoken line.")


class PodcastEpisodeSchema(BaseModel):
    title: str = Field(default="", description="Catchy NYC Radio Lounge episode title.")
    format_mode: str = Field(default="trio", description="'trio' or 'duo'.")
    target_chunks_covered: List[str] = Field(description="List of 5 to 6 target expressions woven into the episode.")
    script: List[PodcastTurnLine] = Field(description="28 to 36 dynamic back-and-forth conversational turns (~500–650 words total) building a 3+ minute episode.")
    key_takeaways: List[LexicalFrameUpgrade] = Field(description="4 core C1/C2 Spoken Expressions featured in the episode.")


class DynamicStudioSparkSchema(BaseModel):
    headline: str = Field(default="", description="Engaging conversation starter headline.")
    lead_persona: str = Field(default="marcus", description="'marcus', 'chloe', or 'jake'.")
    scenario_or_question: str = Field(default="", description="A fresh, vivid scenario, real-life dilemma, or debate stance.")
    suggested_chunks_to_try: List[str] = Field(description="3 natural C1/C2 American expressions to try.")
    pro_tip: str = Field(default="", description="A 1-sentence coaching tip.")


class DynamicDrillSchema(BaseModel):
    drill_title: str = Field(default="", description="Engaging title of the workout.")
    scenario_prompt: str = Field(default="", description="Interactive scenario or challenge instructions.")
    target_chunks: List[str] = Field(default_factory=list, description="3 target expressions.")
    target_audio_sentence: str = Field(default="", description="Natural American spoken sentence.")
    phonetic_prosody_map: str = Field(default="", description="Phonetic respelling + Nuclear stress CAPS + pause bars (|).")
    hidden_reductions_explained: str = Field(default="", description="Clear explanation of reductions or stress subtext.")


class CollocationExplorerSchema(BaseModel):
    headword: str = Field(default="", description="The word or phrase being explored.")
    semantic_note: str = Field(default="", description="Crisp explanation of how Americans naturally prime and use this word.")
    collocation_frames: List[LexicalFrameUpgrade] = Field(description="4 high-frequency spoken American collocations.")


class ActiveReviewEvalSchema(BaseModel):
    used_naturally: bool = Field(description="True if the user used the target expression naturally.")
    recommended_grade: int = Field(description="5 if effortless, 3 if minor slip, 1 if misused.")
    coach_feedback: str = Field(default="", description="Warm, sharp coaching feedback.")
    polished_version: str = Field(default="", description="How a New Yorker would say the user's exact sentence.")


class SpokenFluencyAuditSchema(BaseModel):
    overall_vibe_assessment: str = Field(default="", description="Insightful assessment of the member's spoken style.")
    naturalness_highlights: List[str] = Field(description="2 to 3 specific strengths.")
    focus_areas_under_pressure: List[str] = Field(description="2 to 3 specific habits to polish next.")
    recommended_next_steps: str = Field(default="", description="Concrete 3-step coaching plan from Marcus, Chloe, and Jake.")



PRIORITY_1_TO_5_BLOCK = """
CRITICAL COACHING & DIALOGUE OVERRIDE (PRIORITIES 1–5 — MUST OBEY):
1. PRIORITY 1 — CORRECTION INTEGRITY (`diagnostics`):
   - NEVER invent errors just to fill `diagnostics`! If the user's input is already natural, grammatical American English (e.g., "Honestly, I think people who work from home are less productive. They get distracted all day and don't have anyone checking on them. What do you think?"), you MUST return `diagnostics = []` (EMPTY LIST)!
   - NEVER replace "checking on them" (routine/supportive check) with "looking over their shoulder" (intrusive micromanagement—different meaning!).
   - NEVER replace "people who work from home" with "the remote workforce" or "Honestly, I think" with "I'm of the mind that".
   - ONLY populate `diagnostics` when the user makes a real grammatical, lexical, or non-native collocational mistake.
2. PRIORITY 2 — SEMANTIC FIDELITY IN MIRRORS (`rewrite_articulate` = Natural Flow, `rewrite_casual` = Punchy Cut):
   - Preserve the user's original claim, degree of certainty ("I think" / "can be" / "tend to"), and scope.
   - NEVER escalate "are less productive" into "kills productivity" or "is a productivity killer".
   - NEVER escalate "don't have anyone checking on them" into "zero accountability".
   - `Natural Flow` optimizes for natural spoken rhythm & the active mode's communicative strategy.
   - `Punchy Cut` optimizes for brevity/compression without exaggerating the claim (e.g., "Honestly, I think working from home can hurt productivity. People get distracted, and there's less oversight.").
3. PRIORITY 3 — DISTINCT COMMUNICATIVE STRATEGY PER MODE (IN BOTH BANTER & MIRROR):
   - 🎙️ Articulate: Qualify the claim and develop clear reasoning in plain, natural spoken English (e.g., "I think remote work can undermine productivity when people struggle to separate work from their personal lives."). Ban report-speak ("I'm of the opinion that", "primarily due to", "erosion of boundaries", "physical architecture", "lapse in focus").
   - ☕ Casual: Relaxed, spontaneous flow ("I think working from home can make it harder to stay focused. There's just more stuff competing for your attention."). Use standard `-ing` spelling ALWAYS—never write `paintin'`, `cleanin'`, `crankin'`, `buggin'`, or `workin'`.
   - 🎭 Witty: Both the hosts AND the Mirror (`Natural Flow` & `Punchy Cut`) MUST use a recognizable comic mechanism—hyperbole, irony, comic contrast, or dry understatement (e.g., "Working from home is great until a five-minute break somehow turns into a full kitchen renovation.").
   - 💼 Executive: Frame around management reasoning, measurable targets, and variables (e.g., "I'd focus less on where people work and more on whether their output meets clearly defined targets."). Never misuse "executive presence" for working near a kitchen or make sweeping absolute claims.
   - 🤝 Diplomatic: Demonstrate acknowledgment ("I can see why that would be a concern..."), qualified disagreement ("...although I think the impact probably depends on the person and the kind of work they're doing"), and constructive reframing. Never confuse diplomacy with stiff formality.
4. PRIORITY 4 — NATURAL DIALOGUE REALISM (NO ENGINEERED 3-ESSAY SCRIPTS):
   - Do NOT write three polished mini-essays in a fixed formula (Marcus abstract -> Jake counter -> Chloe synthesis + question).
   - Write a realistic, spontaneous back-and-forth where hosts react directly to one another with varied turn lengths (including short 1-sentence reactions or quick challenges). Do NOT force every turn to end with a tutoring question directed at the user!
5. PRIORITY 5 — FRESH EXPRESSIONS & ACCURATE ARABIC EQUIVALENTS (`lexical_upgrades`):
   - Provide 3 high-value spoken expressions relevant to this specific mode and topic.
   - DO NOT recycle overused defaults like "looking over someone's shoulder" or "phoning it in" across modes.
   - In each item's `explanation`, ALWAYS begin with the accurate Arabic equivalent (المكافئ العربي الدقيق) followed by a 1-sentence note on its exact connotation and when to use it.
"""


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
                f"- Musa directly addressed `{called_host.capitalize()}`, so `{called_host}` MUST speak first."
                if called_host else
                "- Start with whichever host has the sharpest immediate reaction to Musa's point."
            )
            return (
                "ACTIVE TABLE CAST: 🗽 NYC TRIO (`marcus`, `chloe`, `jake`).\n"
                f"{lead_note}\n"
                "- Default cognitive tendencies (allow natural overlap): Marcus clarifies concepts & assumptions; Chloe reads interpersonal/social dynamics; Jake grounds in practical everyday reality.\n"
                "- Write 2 to 4 natural conversational turns in `chat_turns` with varied turn lengths (mix short 1-sentence reactions or challenges with 2-sentence observations).\n"
                "- NEVER use a rigid script ('Marcus abstract -> Jake Wait hold on -> Chloe synthesis + question'). Only end with a question when it genuinely advances the conversation.\n"
                "- Do NOT put speaker names inside `ChatBubbleLine.text`."
            )
        elif count == 2:
            if called_host and called_host not in hosts:
                hosts = [called_host, hosts[0]]
            elif called_host and hosts[0] != called_host:
                hosts = [called_host, [h for h in hosts if h != called_host][0]]
            h1, h2 = hosts[0], hosts[1]
            p1, p2 = PERSONAS[h1], PERSONAS[h2]
            return (
                f"ACTIVE TABLE CAST: 👥 2-HOST DUO (`{h1}` and `{h2}`).\n"
                f"- {p1['title']} & {p2['title']}.\n"
                f"- Populate `chat_turns` with 2 to 3 natural back-and-forth turns between `{h1}` and `{h2}`."
            )
        else:
            h1 = called_host if called_host else hosts[0]
            p1 = PERSONAS.get(h1, PERSONAS["marcus"])
            return (
                f"ACTIVE TABLE CAST: 👤 1-ON-1 SOLO WITH {p1['title']} (`{h1}` ONLY).\n"
                f"- Populate `chat_turns` with 1 natural spoken turn where `speaker` is '{h1}'."
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
        if mode == "devils_advocate":
            return "ACTIVE ROOM: 🥊 DEVIL'S ADVOCATE. Challenge the member's premise with sharp New York pushback."
        elif mode == "storytelling_gym":
            return "ACTIVE ROOM: 🎭 STORYTELLING GYM (Delivered strictly in the member's ACTIVE VIBE). Coach dramatic present-tense storytelling and punchlines."
        elif mode == "register_lab":
            return "ACTIVE ROOM: 🎛️ VIBE & STYLE SHIFTER. Contrast Articulate, Casual, and Witty registers."
        elif mode == "subtext_decoder":
            return "ACTIVE ROOM: 🕵️‍♂️ SUBTEXT & NUANCE DECODER. Decode unspoken American hints and give 3 responses."
        elif mode == "situation_gambit":
            return "ACTIVE ROOM: ⚡ WHAT TO SAY WHEN... Give instinctive phrases Americans reach for in that exact moment."
        elif mode == "podcast_guest":
            return "ACTIVE ROOM: 🎙️ LIVE RADIO INTERVIEW ('YOU ARE THE GUEST' with Marcus, Chloe & Jake)."
        elif mode == "weakness_drill":
            targets = session_meta.get("forced_targets", [])
            return f"ACTIVE ROOM: 🔥 3-PHRASE CHALLENGE. Target phrases: {targets}."
        elif mode == "shadowing_drill":
            target_sent = session_meta.get("drill_target_sentence", "")
            return f"ACTIVE ROOM: 🎙️ RHYTHM & SHADOWING WORKOUT. Target sentence: '{target_sent}'."
        return "ACTIVE ROOM: 🗣️ NYC SPONTANEOUS BANTER. Engage in genuine, unscripted New York conversation strictly locked to the member's ACTIVE VIBE."

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

    def process_lounge_turn(self, *args, **kwargs) -> Tuple[LoungeTurnResponse, List[int]]:
        user = args[0] if len(args) > 0 else kwargs.get("user", {})
        user_text = args[1] if len(args) > 1 else (kwargs.get("user_text") or kwargs.get("text") or "")
        uid = user.get("user_id", 0) if isinstance(user, dict) else 0
        _pref_r = user.get("preferred_register", "articulate") if isinstance(user, dict) else "articulate"
        if _pref_r not in REGISTERS_MAP:
            _pref_r = "articulate"
        _badge_r = REGISTERS_MAP[_pref_r]
        _active_persona = user.get("active_persona", "trio") if isinstance(user, dict) else "trio"
        _mode = user.get("current_mode", "spontaneous_chat") if isinstance(user, dict) else "spontaneous_chat"

        try:
            res, used_stealth_ids = self._raw_process_lounge_turn(*args, **kwargs)
            if not isinstance(res, LoungeTurnResponse):
                raise ValueError("Invalid LoungeTurnResponse object returned")
            if not isinstance(used_stealth_ids, list):
                used_stealth_ids = []
        except Exception as exc:
            logger.warning(f"Structured turn fallback triggered: {exc}")
            fallback_res = LoungeTurnResponse(
                responding_persona="marcus",
                chat_turns=[ChatBubbleLine(speaker="marcus", pacing="normal", text="Give me one second—my train of thought just derailed. Drop that line on me one more time?")],
                lounge_reply="🧔‍♂️ <b>Marcus:</b> Give me one second—my train of thought just derailed. Drop that line on me one more time?",
                rewrite_articulate=str(user_text or ""),
                rewrite_casual=str(user_text or ""),
                diagnostics_status="not_assessed",
                diagnostics=[],
                lexical_upgrades=[]
            )
            return fallback_res, []

        known_ing_stems = (
            "work", "get", "stay", "look", "breath", "talk", "walk", "think", "go", "do",
            "mak", "tak", "com", "runn", "hitt", "try", "say", "play", "grind", "slack",
            "spinn", "paint", "clean", "crank", "bugg", "push", "pull", "call", "wait"
        )
        ing_pattern = re.compile(r"\b(" + "|".join(known_ing_stems) + r")in'(?!\w)", flags=re.IGNORECASE)

        def _clean_spoken_text(txt: str) -> str:
            if not txt:
                return ""
            txt = ing_pattern.sub(r"\1ing", txt)
            safe_replacements = [
                (r"^(?:Frankly|Honestly),?\s+I(?:'m| am) of the (?:mind|opinion) that\s+", "Honestly, I think "),
                (r"\bI(?:'m| am) of the (?:mind|opinion) that\b", "I think"),
                (r"\ba lapse in focus\b", "losing focus"),
                (r"\bthose working remotely\b", "people working from home"),
                (r"\bis a (?:total )?productivity killer\b", "can really hurt productivity"),
                (r"\b(?:just )?kills productivity\b", "can hurt productivity"),
                (r"\bthere(?:'s| is) zero accountability\b", "there's less direct oversight"),
            ]
            for pat, rep in safe_replacements:
                txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)
            return txt.strip()

        if getattr(res, "chat_turns", None):
            for ct in res.chat_turns:
                ct.text = _clean_spoken_text(getattr(ct, "text", ""))
        res.lounge_reply = _clean_spoken_text(getattr(res, "lounge_reply", ""))
        res = self._finalize_chat_turns(res, active_persona=_active_persona, user_text=user_text)

        res.rewrite_articulate = _clean_spoken_text(getattr(res, "rewrite_articulate", ""))
        res.rewrite_casual = _clean_spoken_text(getattr(res, "rewrite_casual", ""))
        res.rewrite_witty = _clean_spoken_text(getattr(res, "rewrite_witty", ""))
        res.tts_clean_target = _clean_spoken_text(getattr(res, "tts_clean_target", "")) or res.rewrite_articulate

        u_raw = (user_text or getattr(res, "transcription", "") or "").strip()
        u_lower = u_raw.lower()
        user_hedged = any(h in u_lower for h in ("i think", "might", "maybe", "can ", "some ", "tend to", "probably"))
        if user_hedged and not any(a in u_lower for a in ("nobody", "no one ever", "zero", "impossible")):
            for attr in ("rewrite_articulate", "rewrite_casual", "rewrite_witty"):
                val = getattr(res, attr, "")
                val = re.sub(r"\bnobody gets anything done\b", "people get less done", val, flags=re.IGNORECASE)
                val = re.sub(r"\bno one gets anything done\b", "people get less done", val, flags=re.IGNORECASE)
                setattr(res, attr, val)

        has_arabic_input = bool(re.search(r"[\u0600-\u06FF]", u_raw))
        is_scenario_prompt = bool(
            re.match(r"^(?:what to say when|how do i |how to |apologizing for |politely )", u_lower)
            or _mode in ("situation_gambit", "subtext_decoder")
        )
        protected_natural_phrases = {
            "people who work from home",
            "don't have anyone checking on them",
            "checking on them",
            "get distracted all day",
            "honestly, i think",
            "honestly i think",
            "work from home",
            "less productive",
            "what do you think",
        }

        if has_arabic_input or is_scenario_prompt or not u_raw:
            res.diagnostics = []
            res.diagnostics_status = "not_assessed"
        else:
            valid_diags = []
            for d in (getattr(res, "diagnostics", None) or []):
                u_said_raw = (getattr(d, "user_said", "") or "").strip()
                u_said_norm = u_said_raw.lower().strip(".,!?\"'")
                n_fix_norm = (getattr(d, "native_fix", "") or "").strip().lower().strip(".,!?\"'")
                if not u_said_norm or not n_fix_norm or u_said_norm == n_fix_norm:
                    continue
                if u_lower and u_said_norm not in u_lower:
                    continue
                if u_said_norm in protected_natural_phrases:
                    continue
                if any(bad in n_fix_norm for bad in ("of the mind", "of the opinion", "remote workforce", "looking over their shoulder")):
                    continue
                d.native_fix = _clean_spoken_text(d.native_fix)
                valid_diags.append(d)
            res.diagnostics = valid_diags[:1]
            res.diagnostics_status = "corrections_found" if res.diagnostics else "checked_no_corrections"

        recent_chunks = set()
        try:
            if self.db and uid:
                vault_items = self.db.get_user_vault(uid, limit=25)
                recent_chunks = {(item.get("chunk") or "").strip().lower() for item in vault_items}
        except Exception:
            pass

        if getattr(res, "lexical_upgrades", None):
            seen_in_turn = set()
            filtered_upgrades = []
            for _u_item in res.lexical_upgrades:
                _u_item.register_tag = _badge_r
                _u_item.chunk = _clean_spoken_text(getattr(_u_item, "chunk", ""))
                _u_item.b2_foil = _clean_spoken_text(getattr(_u_item, "b2_foil", ""))
                _u_item.explanation = _clean_spoken_text(getattr(_u_item, "explanation", ""))
                _u_item.example = _clean_spoken_text(getattr(_u_item, "example", ""))
                _u_item.followup = _clean_spoken_text(getattr(_u_item, "followup", ""))
                if not getattr(_u_item, "arabic_equivalent", "").strip() and "—" in _u_item.explanation:
                    parts = _u_item.explanation.split("—", 1)
                    _u_item.arabic_equivalent = parts[0].strip()
                    _u_item.explanation = parts[1].strip()
                if _u_item.chunk and _u_item.example and not getattr(_u_item, "cloze_sentence", "").strip():
                    _u_item.cloze_sentence = re.sub(re.escape(_u_item.chunk), "______", _u_item.example, flags=re.IGNORECASE)
                c_key = _u_item.chunk.lower()
                if not c_key or c_key in seen_in_turn:
                    continue
                seen_in_turn.add(c_key)
                if c_key not in recent_chunks or len(res.lexical_upgrades) <= 1:
                    filtered_upgrades.append(_u_item)
            res.lexical_upgrades = (filtered_upgrades or list(res.lexical_upgrades)[:1])[:3]

        return res, used_stealth_ids


    def process_situation_gambit(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> SituationGambitResponse:
        pref_reg = user.get("preferred_register", "articulate") if isinstance(user, dict) else "articulate"
        if pref_reg not in REGISTERS_MAP:
            pref_reg = "articulate"
        vibe_badge = REGISTERS_MAP[pref_reg]
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"ACTIVE ROOM: ⚡ WHAT TO SAY WHEN... (Dedicated Situational Gambit Engine)\n"
            f"TARGET SPOKEN VIBE: {vibe_badge} — {vibe_rules}\n\n"
            f"TASK:\n"
            f"The member is describing a social, workplace, or interpersonal scenario (in English or Arabic) and needs the exact instinctive spoken English lines to use in that moment.\n"
            f"1. Analyze the speech act, relationship/power dynamic, and interpersonal risk in `situation_summary`.\n"
            f"2. Provide `best_line`: the #1 natural first-person spoken line an American would actually say out loud in {vibe_badge}.\n"
            f"3. Provide `softer_alternative` (more tactful/indirect) and `firmer_alternative` (clearer boundary/directness) while preserving the member's intended outcome.\n"
            f"4. Explain the pragmatic nuance in `why_it_works`, provide the accurate Arabic equivalent in `arabic_equivalent`, and give a realistic `follow_up_line` if the other person pushes back.\n"
            f"5. Populate `lexical_upgrades` with 3 reusable spoken gambits from your recommendations so the member can save them to their Vault."
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append("Listen to the member's scenario description and return the complete `SituationGambitResponse`.")
        else:
            contents.append(f"SCENARIO DESCRIPTION:\n{user_text}\n\nReturn the complete `SituationGambitResponse` calibrated to {vibe_badge}.")

        try:
            g_res: SituationGambitResponse = self._call_structured(
                contents=contents,
                schema_cls=SituationGambitResponse,
                system_instruction=system_prompt,
                temperature=0.65,
                max_output_tokens=3072
            )
            for up in (g_res.lexical_upgrades or []):
                up.register_tag = vibe_badge
            g_res.lexical_upgrades = list(g_res.lexical_upgrades or [])[:3]
            return g_res
        except Exception as exc:
            logger.warning(f"Situation gambit fallback triggered: {exc}")
            return SituationGambitResponse(
                situation_summary="Setting a clear, respectful boundary without escalating tension.",
                responding_persona="chloe",
                host_commentary="Here is the cleanest way to handle that moment on the spot:",
                best_line="I want to make sure we're on the same page here—let's clarify how we're handling this moving forward.",
                softer_alternative="Just so nothing gets lost in translation, could we quickly align on who's leading which part?",
                firmer_alternative="I want to clarify one thing: I led that part of the work, and it's important our contributions are represented accurately.",
                why_it_works="It addresses the issue calmly in the first person without sounding defensive or accusatory.",
                arabic_equivalent="أود توضيح نقطة واحدة لضمان الدقة والاتفاق على سير العمل.",
                follow_up_line="I'm happy to collaborate closely, as long as we're transparent about ownership.",
                lexical_upgrades=[]
            )

    def _raw_process_lounge_turn(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> Tuple[LoungeTurnResponse, List[int]]:
        uid = user["user_id"]
        level = user.get("level", "C1")
        pref_reg = user.get("preferred_register", "articulate")
        if pref_reg not in REGISTERS_MAP:
            pref_reg = "articulate"
        active_vibe_badge = REGISTERS_MAP[pref_reg]
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
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
        rag_str = "\n".join([f" - {r}" for r in rag_hits]) if rag_hits else " - Prioritize high-utility C1/C2 Spoken American collocations."
        history = self.db.get_history(uid, limit=6)
        hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in history]) if history else "Start of conversation."

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"CALIBRATION & REGISTER CONTRACT FOR THIS TURN:\n"
            f"- Depth: {self._level_calibration_guide(level)}\n"
            f"- {self._persona_routing_directive(active_persona, mode, user_text)}\n"
            f"- ACTIVE REGISTER LOCK ({active_vibe_badge}): {vibe_rules}\n"
            f"- THREE RECAST STRATEGIES WITHIN {active_vibe_badge}:\n"
            f"  1. `rewrite_articulate` (Natural Flow): Preserve the full thought, certainty, scope, and causality with natural spoken rhythm in {active_vibe_badge}.\n"
            f"  2. `rewrite_casual` (Punchy Cut): Compress the same thought concisely in {active_vibe_badge} without exaggerating the claim.\n"
            f"  3. `rewrite_witty` (Spoken Fronting/Ellipsis): First-person spoken alternative using fronting or ellipsis in {active_vibe_badge}.\n"
            f"- LANGUAGE POLICY: Keep all spoken dialogue, rewrites, and examples in natural American English, while ALWAYS populating `arabic_equivalent` with the accurate Arabic meaning.\n"
            f"- {self._build_mode_specific_directive(mode, session_meta)}\n\n"
            f"ORGANIC PHRASE WEAVING:\n"
            f"- Candidate Vault Phrases: {stealth_chunks_str}\n"
            f"- Only weave a candidate phrase into `chat_turns` if it fits 100% naturally. Wrap woven phrases in `<i>phrase</i>`.\n\n"
            f"RECURRING HABITS TO WATCH FOR: {weakness_str}\n"
            f"RELEVANT COLLOCATIONS:\n{rag_str}\n\n"
            f"RECENT CONVERSATION (For topic continuity ONLY — strictly use {active_vibe_badge} now):\n{hist_str}\n"
            f"{PRIORITY_1_TO_5_BLOCK}"
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                f"Listen to the member's voice note. Transcribe verbatim in `transcription`, populate `chat_turns` in {active_vibe_badge}, provide the 3 recast strategies (`rewrite_articulate`, `rewrite_casual`, `rewrite_witty`), set `diagnostics_status` honestly ('corrections_found', 'checked_no_corrections', or 'not_assessed'), provide at most 1 genuine fix in `diagnostics`, and provide 3 fresh `lexical_upgrades` with `arabic_equivalent`."
            )
        else:
            contents.append(
                f"MEMBER MESSAGE:\n{user_text}\n\n"
                f"Respond in `chat_turns` in {active_vibe_badge}, provide the 3 recast strategies (`rewrite_articulate`, `rewrite_casual`, `rewrite_witty`), set `diagnostics_status` honestly, provide at most 1 genuine fix in `diagnostics`, and provide 3 fresh `lexical_upgrades` with `arabic_equivalent`."
            )

        res: LoungeTurnResponse = self._call_structured(
            contents=contents,
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.70,
            max_output_tokens=4096
        )

        used_stealth_ids: List[int] = []
        combined_raw = " ".join([getattr(ct, "text", "") for ct in (res.chat_turns or [])]) + " " + (res.lounge_reply or "")
        reply_lower = combined_raw.lower()
        for it in stealth_items:
            if it["chunk"].lower() in reply_lower:
                used_stealth_ids.append(it["id"])

        return res, used_stealth_ids

    def explore_collocations_live(self, user: Dict[str, Any], query_word: str) -> CollocationExplorerSchema:
        clean_hw = (query_word or "").strip().strip('"').strip("'")
        pref_reg = user.get("preferred_register", "articulate")
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎙️ Articulate")
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")
        level = user.get("level", "C1")

        has_arabic = bool(re.search(r"[\u0600-\u06FF]", clean_hw))
        word_count = len(clean_hw.split())
        is_concept_query = has_arabic or word_count >= 3

        if not is_concept_query:
            mode_instruction = (
                f"MODE A — PURE LEXICAL COLLOCATION MAP FOR HEADWORD '{clean_hw}' (4 Pure Collocations):\n"
                f"1. Every single one of the 4 `collocation_frames` MUST explicitly keep '{clean_hw}' (or its direct inflection) inside `chunk`!\n"
                f"2. Show the strongest spoken C1/C2 combinations: [Verb + '{clean_hw}'], [Adjective + '{clean_hw}'], or ['{clean_hw}' + Preposition].\n"
                f"   - Example for 'Pushback': (1) 'get some pushback from', (2) 'give pushback on', (3) 'anticipate some pushback', (4) 'zero pushback'.\n"
                f"   - Example for 'Happy': (1) 'strike a happy medium', (2) 'not a happy camper', (3) 'couldn\'t be happier', (4) 'more than happy to'."
            )
        else:
            mode_instruction = (
                f"MODE B — DUAL C1/C2 LEXICAL-IDIOMATIC SPECTRUM FOR '{clean_hw}' (2 True Collocations + 2 Spoken Idioms):\n"
                f"The member entered a multi-word phrase or meaning description ('{clean_hw}'). You MUST structure the 4 items into TWO distinct linguistic tiers:\n"
                f"1. ITEMS (1) AND (2) MUST BE TRUE LEXICAL COLLOCATIONS (Verb + Noun, Adjective + Noun, or Verb + Adverb):\n"
                f"   - Upgrade the core noun/verb of '{clean_hw}' using high-precision C1/C2 collocates (NOT metaphors/slang).\n"
                f"   - Example for 'Change my mind suddenly': (1) 'have a sudden change of heart' or 'make a snap decision', (2) 'reverse course abruptly' or 'backtrack on a decision'.\n"
                f"2. ITEMS (3) AND (4) MUST BE SPOKEN IDIOMATIC CHUNKS / METAPHORICAL EXPRESSIONS:\n"
                f"   - Provide the 2 sharpest spoken American idioms or phrasal chunks that native speakers use for '{clean_hw}'.\n"
                f"   - Example for 'Change my mind suddenly': (3) 'do a complete 180', (4) 'call an audible'."
            )

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"TASK: Build a C1/C2 Spoken American Lexical Map for '{clean_hw}' ({self._level_calibration_guide(level)}).\n"
            f"ACTIVE SPOKEN VIBE: {vibe_badge}\n"
            f"ACTIVE VIBE VOICE RULES: {vibe_rules}\n\n"
            f"{mode_instruction}\n\n"
            "CRITICAL SPOKEN AUTHENTICITY RULES:\n"
            f"- 100% FIRST-PERSON CONVERSATIONAL EXAMPLES: Every `example` and `followup` MUST sound like real spoken dialogue using 'I', 'we', or 'you' with natural contractions ('we\'re', 'I\'ve', 'don\'t', 'gonna'). NEVER write third-person newspaper sentences!\n"
            f"- Strictly match the member's active Vibe ({vibe_badge}) in tone and vocabulary.\n"
            "- Return 4 complete `collocation_frames` with `chunk`, `register_tag`, `b2_foil`, `explanation`, `trigger`, `pattern`, `followup`, and `example`."
        )

        user_prompt = (
            f"Target input: '{clean_hw}' | Active Spoken Vibe: {vibe_badge}\n"
            + (
                f"Generate 4 high-frequency spoken American collocations that ALL explicitly include '{clean_hw}' inside the chunk, with 1st-person spoken examples."
                if not is_concept_query else
                f"Generate exactly 4 items for '{clean_hw}' in {vibe_badge} style: Items 1 & 2 MUST be true C1/C2 lexical collocations (Verb+Noun / Adj+Noun / Verb+Adv), and Items 3 & 4 MUST be spoken American idioms/chunks, all with 1st-person spoken examples."
            )
        )

        last_err = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(1.5 * attempt)
                explorer: CollocationExplorerSchema = self._call_structured(
                    contents=[user_prompt],
                    schema_cls=CollocationExplorerSchema,
                    system_instruction=system_prompt,
                    temperature=0.55 if attempt == 0 else 0.40
                )
                if explorer and getattr(explorer, "collocation_frames", None):
                    for idx, c in enumerate(explorer.collocation_frames):
                        if is_concept_query:
                            tier_label = "🔗 Collocation" if idx < 2 else "🎭 Idiom"
                            c.register_tag = f"{vibe_badge} • {tier_label}"
                        else:
                            c.register_tag = vibe_badge
                        if not getattr(c, "b2_foil", "") and getattr(c, "b2_equivalent", ""):
                            c.b2_foil = c.b2_equivalent
                    try:
                        self.db.ingest_chunks_to_corpus([c.model_dump() for c in explorer.collocation_frames])
                    except Exception:
                        pass
                    return explorer
            except Exception as e:
                last_err = e
                logger.warning(f"explore_collocations_live attempt {attempt+1} failed for '{clean_hw}': {e}")

        raise RuntimeError(f"explore_collocations_live exhausted retries for '{clean_hw}': {last_err}")

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
