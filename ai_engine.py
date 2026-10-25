import uuid
import random
import traceback
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
from pydantic import BaseModel, Field, field_validator

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

HostLiteral = Literal["marcus", "chloe", "jake"]
PacingLiteral = Literal["fast_interjection", "normal", "thoughtful_slow"]
DiagnosticsStatusLiteral = Literal["corrections_found", "checked_no_corrections", "not_assessed"]
DiagnosticCategoryLiteral = Literal["🎭 Natural Flow", "💎 Lexical Upgrade", "📐 Spoken Structure", "🗽 Pragmatic Nuance"]
RegisterTagLiteral = Literal["🎙️ Articulate", "☕ Casual", "🎭 Witty", "💼 Executive", "🤝 Diplomatic"]



class DiagnosticItem(BaseModel):
    category: DiagnosticCategoryLiteral = Field(
        default="🎭 Natural Flow",
        description="One of: '🎭 Natural Flow', '💎 Lexical Upgrade', '📐 Spoken Structure', or '🗽 Pragmatic Nuance'."
    )
    user_said: str = Field(default="")
    native_fix: str = Field(default="")
    why_note: str = Field(default="")

    @field_validator("category", mode="before")
    @classmethod
    def _norm_category(cls, value: Any) -> str:
        s = str(value or "").strip()
        allowed = {"🎭 Natural Flow", "💎 Lexical Upgrade", "📐 Spoken Structure", "🗽 Pragmatic Nuance"}
        if s in allowed:
            return s
        low = s.lower()
        if any(w in low for w in ("lex", "vocab", "word", "colloc", "idiom", "choice")):
            return "💎 Lexical Upgrade"
        if any(w in low for w in ("struct", "gram", "syntax", "tense", "verb", "morph", "agree", "form")):
            return "📐 Spoken Structure"
        if any(w in low for w in ("prag", "tone", "reg", "nuance", "politen", "social")):
            return "🗽 Pragmatic Nuance"
        return "🎭 Natural Flow"


class LexicalFrameUpgrade(BaseModel):
    chunk: str = Field(default="")
    register_tag: RegisterTagLiteral = Field(default="🎙️ Articulate")
    b2_foil: str = Field(default="")
    arabic_equivalent: str = Field(default="")
    explanation: str = Field(default="")
    trigger: str = Field(default="")
    pattern: str = Field(default="")
    followup: str = Field(default="")
    example: str = Field(default="")
    cloze_sentence: str = Field(default="")

    @field_validator("register_tag", mode="before")
    @classmethod
    def _norm_reg_tag(cls, value: Any) -> str:
        s = str(value or "").strip()
        allowed = {"🎙️ Articulate", "☕ Casual", "🎭 Witty", "💼 Executive", "🤝 Diplomatic"}
        if s in allowed:
            return s
        low = s.lower()
        if "casual" in low or "☕" in low or "informal" in low or "street" in low:
            return "☕ Casual"
        if "witty" in low or "🎭" in low or "humor" in low or "iron" in low or "playful" in low:
            return "🎭 Witty"
        if "exec" in low or "💼" in low or "business" in low or "pro" in low or "formal" in low:
            return "💼 Executive"
        if "diplo" in low or "🤝" in low or "tact" in low or "polite" in low:
            return "🤝 Diplomatic"
        return "🎙️ Articulate"


class ChatBubbleLine(BaseModel):
    speaker: HostLiteral = Field(default="marcus")
    pacing: PacingLiteral = Field(default="normal")
    text: str = Field(default="")

    @field_validator("speaker", mode="before")
    @classmethod
    def _norm_speaker(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        for h in ("marcus", "chloe", "jake"):
            if h in s:
                return h
        return "marcus"

    @field_validator("pacing", mode="before")
    @classmethod
    def _norm_pacing(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        if s in ("fast_interjection", "normal", "thoughtful_slow"):
            return s
        if any(w in s for w in ("fast", "quick", "rapid", "interject", "punchy", "brisk")):
            return "fast_interjection"
        if any(w in s for w in ("slow", "thought", "deliberate", "measured", "calm")):
            return "thoughtful_slow"
        return "normal"


class LoungeTurnResponse(BaseModel):
    transcription: str = Field(default="")
    responding_persona: HostLiteral = Field(default="marcus")
    chat_turns: List[ChatBubbleLine] = Field(default_factory=list)
    lounge_reply: str = Field(default="")
    rewrite_articulate: str = Field(
        default="",
        description="Natural Flow: Smooth, idiomatic spoken American recast of the member's own message/question preserving exact pragmatic intent, volition, and register."
    )
    rewrite_casual: str = Field(
        default="",
        description="Punchy Cut: Concise, natural spoken compression of the member's own message/question without altering connotation or volition."
    )
    rewrite_witty: str = Field(default="")
    self_repair_pivot: str = Field(default="")
    acoustic_prosody_xray: str = Field(default="")
    tts_clean_target: str = Field(default="")
    mirror_clean_target: str = Field(default="")
    diagnostics_status: DiagnosticsStatusLiteral = Field(default="checked_no_corrections")
    diagnostics: List[DiagnosticItem] = Field(
        default_factory=list,
        description="List ALL genuine grammatical, tense, verb-form, agreement, or collocational errors found in the member's message (up to 4 items), ordered with the #1 pedagogical priority first. Empty `[]` if error-free."
    )
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(default_factory=list)
    is_fallback: bool = Field(default=False)

    @field_validator("responding_persona", mode="before")
    @classmethod
    def _norm_resp_persona(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        for h in ("marcus", "chloe", "jake"):
            if h in s:
                return h
        return "marcus"

    @field_validator("diagnostics_status", mode="before")
    @classmethod
    def _norm_diag_status(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        if s in ("corrections_found", "checked_no_corrections", "not_assessed"):
            return s
        if any(w in s for w in ("found", "error", "fix", "correct")) and "no_" not in s and "no " not in s:
            return "corrections_found"
        if any(w in s for w in ("not_", "not ", "skip", "n/a", "scenario")):
            return "not_assessed"
        return "checked_no_corrections"


class SituationGambitResponse(BaseModel):
    situation_summary: str = Field(default="")
    responding_persona: HostLiteral = Field(default="chloe")
    host_commentary: str = Field(default="")
    best_line: str = Field(default="")
    softer_alternative: str = Field(default="")
    firmer_alternative: str = Field(default="")
    why_it_works: str = Field(default="")
    arabic_equivalent: str = Field(default="")
    follow_up_line: str = Field(default="")
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(default_factory=list)

    @field_validator("responding_persona", mode="before")
    @classmethod
    def _norm_gambit_persona(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        for h in ("chloe", "marcus", "jake"):
            if h in s:
                return h
        return "chloe"




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
CRITICAL COACHING, REGISTER COHERENCE & PRAGMATIC MIRROR RULES (MUST OBEY):
1. RAPID CONVERSATIONAL INTERPLAY & ZERO TONAL DISSONANCE:
   - Keep each host's cognitive lens distinct WITHOUT delivering three separate speeches!
   - Keep each host's turn punchy (1 to 2 sentences max per bubble). Have hosts react directly to a specific word or claim the previous host just used—interrupting lightly, challenging a premise, or building on the joke like real friends at a table.
   - In 🎭 Witty or ☕ Casual: use playful teasing, dry irony, and grounded everyday NYC details.
   - In 💼 Executive, 🤝 Diplomatic, or 🎙️ Articulate: do NOT use slapstick jokes or slang; keep the interplay sharp and aligned with the active register.

2. PRAGMATIC & VOLITIONAL FIDELITY IN THE MIRROR (`rewrite_articulate` & `rewrite_casual`):
   - Recast ONLY the member's own message/question—NEVER summarize or echo the host's answer inside `rewrite_articulate` or `rewrite_casual`!
   - If the member includes routing meta-instructions ("Reply only as Jake..."), strip the meta-instruction and recast only the member's substantive scenario or question in the member's own voice.
   - Preserve exact pragmatic intent, volition, and register:
     * NEVER replace an involuntary conflict ("can't attend because something came up") with wording that implies deliberate truancy or carelessness (like "skipping tomorrow's meeting"); use natural American equivalents like "can't make tomorrow's meeting".
     * NEVER inflate a casual question into stiff, overly formal prose ("Setting the market aside for a moment, if I need to inform a colleague..."). Keep it natural, direct, and genuinely spoken (e.g., "How can I casually tell a coworker I can't make tomorrow's meeting because something came up, without sounding rude?").

3. COMPREHENSIVE MULTI-ERROR DIAGNOSTICS (`diagnostics`):
   - Separate natural recasting from diagnostic error detection!
   - If the member's message contains multiple genuine grammatical, tense, morphology, agreement, or collocation errors (e.g., "Yesterday I go to the market and I buyed some vegetables, but the prices was crazy"), you MUST record EVERY substantive error as a separate `DiagnosticItem` in `diagnostics` (up to 4 items), ordered with the highest pedagogical priority first:
     * Item 1 (Priority): e.g., `I buyed` -> `I bought` (irregular past tense morphology)
     * Item 2: e.g., `I go` -> `I went` (past time marker 'Yesterday' requires past tense)
     * Item 3: e.g., `prices was` -> `prices were` (plural subject-verb agreement)
   - Never stop at just 1 error when other clear errors exist in the sentence!
   - Conversely, if the member's English is already natural and error-free, return `diagnostics = []` and `diagnostics_status = "checked_no_corrections"`.

4. STRICT LEXICAL PRECISION & SCENE ANCHORING (`lexical_upgrades`):
   - Every expression MUST fit the exact topic and active register.
   - Clearly state the accurate Arabic equivalent in `arabic_equivalent` and the exact usage boundary/connotation in `explanation`.
"""


class NYCLoungeAI:
    def __init__(self, api_key: str, db: LoungeDatabase):
        self.db = db
        raw_pool = GEMINI_KEYS if GEMINI_KEYS else ([api_key] if api_key else [])
        self.keys_pool = [k.strip() for k in raw_pool if k and k.strip()]
        self._key_idx = 0
        self._dead_keys_until: Dict[int, float] = {}
        self._dead_models_until: Dict[str, float] = {}
        self._discovered_models: List[str] = list(GEMINI_MODELS_CHAIN)
        self._recent_taught_by_user: Dict[int, List[str]] = {}

    def _get_client(self) -> genai.Client:
        if not self.keys_pool:
            raise RuntimeError("API key is not configured.")
        key = self.keys_pool[self._key_idx % len(self.keys_pool)]
        self._key_idx = (self._key_idx + 1) % len(self.keys_pool)
        return genai.Client(api_key=key, http_options={"timeout": 45000})

    def _get_active_models(self, client: Any = None) -> List[str]:
        now = time.time()
        guaranteed_chain = [
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-2.5-flash-lite",
        ]
        for m in getattr(self, "_discovered_models", []):
            if m not in guaranteed_chain:
                guaranteed_chain.append(m)
        active = [m for m in guaranteed_chain if self._dead_models_until.get(m, 0) < now]
        if not active:
            self._dead_models_until.clear()
            return list(guaranteed_chain)
        return active

    def _call_structured(
        self,
        contents: List[Any],
        schema_cls: Any,
        system_instruction: str,
        temperature: float = 0.72,
        max_output_tokens: int = 8192
    ) -> Any:
        """
        Multi-model, multi-key resilient structured caller:
        - Tries `gemini-2.5-flash` -> `gemini-3-flash-preview` -> `gemini-2.5-flash-lite` on each key.
        - Note: 429 RESOURCE_EXHAUSTED in Gemini is per-model! So a 429 or 503 on one model immediately
          switches to the next model in `models_to_try` within the SAME turn before rotating keys.
        """
        if not self.keys_pool:
            raise RuntimeError("API key is not configured.")

        req_id = uuid.uuid4().hex[:8]
        last_err = None
        now = time.time()
        dead_keys = getattr(self, "_dead_keys_until", {})
        healthy_key_indices = [i for i in range(len(self.keys_pool)) if dead_keys.get(i, 0) < now]
        if not healthy_key_indices:
            dead_keys.clear()
            healthy_key_indices = list(range(len(self.keys_pool)))

        start_pos = self._key_idx % len(healthy_key_indices)
        ordered_key_indices = healthy_key_indices[start_pos:] + healthy_key_indices[:start_pos]
        self._key_idx = (self._key_idx + 1) % max(1, len(self.keys_pool))

        eff_tokens = max(8192, int(max_output_tokens or 8192))

        for k_idx in ordered_key_indices:
            key = self.keys_pool[k_idx]
            client = genai.Client(api_key=key, http_options={"timeout": 45000})
            models_to_try = self._get_active_models(client)
            key_invalid = False

            for model_name in models_to_try:
                for attempt in range(1, 3):
                    t0 = time.perf_counter()
                    stage = "api_call"
                    try:
                        cfg_kwargs: Dict[str, Any] = {
                            "system_instruction": system_instruction,
                            "temperature": temperature,
                            "max_output_tokens": eff_tokens,
                            "response_mime_type": "application/json",
                            "response_schema": schema_cls
                        }
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(**cfg_kwargs)
                        )
                        stage = "schema_validation"
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        if getattr(resp, "parsed", None) is not None:
                            logger.info(
                                f"TELEMETRY req_id={req_id} key_idx={k_idx} model={model_name} attempt={attempt} "
                                f"stage=complete latency_ms={latency_ms} status=ok_parsed"
                            )
                            return resp.parsed
                        raw_txt = getattr(resp, "text", "") or ""
                        if raw_txt:
                            raw_txt = raw_txt.strip()
                            if raw_txt.startswith("```"):
                                raw_txt = raw_txt.strip("`").removeprefix("json").strip()
                            parsed_obj = schema_cls.model_validate_json(raw_txt)
                            logger.info(
                                f"TELEMETRY req_id={req_id} key_idx={k_idx} model={model_name} attempt={attempt} "
                                f"stage=complete latency_ms={latency_ms} status=ok_json"
                            )
                            return parsed_obj
                        raise ValueError("Empty response payload from Gemini API")
                    except Exception as e:
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        last_err = e
                        err_str = self.redact_sensitive_log(str(e))
                        exc_type = type(e).__name__
                        code_match = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", err_str)
                        err_code = code_match.group(1) if code_match else "none"
                        logger.warning(
                            f"TELEMETRY req_id={req_id} key_idx={k_idx} model={model_name} attempt={attempt} "
                            f"stage={stage} latency_ms={latency_ms} exc_type={exc_type} code={err_code} "
                            f"detail={err_str[:160]}"
                        )
                        upper_err = err_str.upper()
                        if any(bad_k in upper_err for bad_k in ("API_KEY_INVALID", "PERMISSION_DENIED", "403", "401", "LEAKED")):
                            if hasattr(self, "_dead_keys_until"):
                                self._dead_keys_until[k_idx] = time.time() + 3600
                            key_invalid = True
                            break
                        if "404" in upper_err or "NOT_FOUND" in upper_err or "NO LONGER AVAILABLE" in upper_err:
                            self._dead_models_until[model_name] = time.time() + 86400
                            break
                        # On 429 or 503, immediately move to the next model in `models_to_try` (e.g., gemini-2.5-flash -> gemini-2.5-flash-lite)
                        if any(q_err in upper_err for q_err in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                            break
                        if attempt == 1:
                            time.sleep(0.8)
                            continue
                        break
                if key_invalid:
                    break

        raise RuntimeError(f"req_id={req_id} All keys/models exhausted: {last_err}")

    @staticmethod
    def _level_calibration_guide(level: str) -> str:
        calibrations = {
            "B1": "Intermediate depth: clear, high-frequency everyday American phrasal verbs.",
            "B2": "Upper-Intermediate depth: natural idiomatic collocations and conversational gambits.",
            "C1": "Advanced depth: sophisticated spoken structure (Heads & Tails, clefts, natural hedging), subtle subtext, and high-utility collocations.",
            "C2": "Mastery depth: native-like intuition, dry understatement, fast connected-speech reductions, semantic precision, and razor-sharp wit."
        }
        return calibrations.get(level, calibrations["C1"])

    @classmethod
    def _detect_addressed_host(cls, user_text: str) -> Optional[str]:
        addressed, _, _ = cls._parse_host_directives(user_text)
        return addressed

    @classmethod
    def _persona_routing_directive(cls, active_persona: str, mode: str, user_text: str = "") -> str:
        preset = CAST_PRESETS.get(active_persona, CAST_PRESETS["trio"])
        base_hosts = list(preset["hosts"])
        called_host, excluded_hosts, solo_only = cls._parse_host_directives(user_text)

        if solo_only:
            hosts = [solo_only]
        else:
            hosts = [h for h in base_hosts if h not in excluded_hosts]
            if not hosts:
                hosts = [called_host or "jake"]
            elif called_host and called_host not in hosts:
                hosts = [called_host] + hosts

        count = len(hosts)
        excl_note = (
            f"\n- STRICT EXCLUSION: Do NOT include {', '.join(h.capitalize() for h in excluded_hosts)} in `chat_turns` on this turn!"
            if excluded_hosts else ""
        )

        if count >= 3:
            lead_note = (
                f"- Musa directly addressed `{called_host.capitalize()}`, so `{called_host}` MUST speak first."
                if called_host else
                "- Start with whichever host has the sharpest immediate reaction to Musa's point."
            )
            return (
                "ACTIVE TABLE CAST: 🗽 NYC TRIO (`marcus`, `chloe`, `jake`).\n"
                f"{lead_note}\n"
                "- INTERPLAY RULE (NO SEPARATE SPEECHES!): Keep each turn tight (1 to 2 sentences max). Each host must react directly to what the previous speaker just said—quoting a phrase they used, pushing back on their logic, or jumping in mid-thought so it feels like a real New York table conversation.\n"
                "- Write 3 to 4 brisk conversational turns in `chat_turns`.\n"
                "- Do NOT put speaker names inside `ChatBubbleLine.text`."
                f"{excl_note}"
            )
        elif count == 2:
            if called_host and called_host in hosts and hosts[0] != called_host:
                hosts = [called_host, [h for h in hosts if h != called_host][0]]
            h1, h2 = hosts[0], hosts[1]
            p1, p2 = PERSONAS[h1], PERSONAS[h2]
            return (
                f"ACTIVE TABLE CAST: 👥 2-HOST DUO (`{h1}` and `{h2}` ONLY).\n"
                f"- {p1['title']} & {p2['title']}.\n"
                f"- Populate `chat_turns` with 2 to 3 brisk back-and-forth turns (1-2 sentences each) strictly between `{h1}` and `{h2}`."
                f"{excl_note}"
            )
        else:
            h1 = hosts[0]
            p1 = PERSONAS.get(h1, PERSONAS["jake"])
            return (
                f"ACTIVE TABLE CAST: 👤 1-ON-1 SOLO WITH {p1['title']} (`{h1}` ONLY).\n"
                f"- Populate `chat_turns` with 1 natural spoken turn where `speaker` is strictly '{h1}'."
                f"{excl_note}"
            )

    @classmethod
    def _finalize_chat_turns(cls, res: LoungeTurnResponse, active_persona: str, user_text: str = "") -> LoungeTurnResponse:
        preset_hosts = list(CAST_PRESETS.get(active_persona, CAST_PRESETS["trio"])["hosts"])
        called_host, excluded_hosts, solo_only = cls._parse_host_directives(user_text)
        if solo_only:
            allowed_hosts = [solo_only]
        else:
            allowed_hosts = [h for h in preset_hosts if h not in excluded_hosts]

        # If all 3 hosts were explicitly excluded by the user, fail closed into the neutral studio fallback
        if not allowed_hosts:
            raise ValueError("All table hosts were explicitly excluded for this turn")

        def _strip_injected_excluded_speakers(txt: str) -> str:
            """Removes any inline HTML or speaker-tag dialogue attributed to excluded hosts inside a turn's text."""
            lines_out = []
            for raw_ln in (txt or "").splitlines():
                s_ln = raw_ln.strip()
                if not s_ln:
                    continue
                # Check if a line starts with an excluded host's speaker label (e.g., "🧔‍♂️ <b>Marcus:</b> ...")
                dropped = False
                for ex_h in excluded_hosts:
                    if re.match(rf"^(?:🧔‍♂️|👩🏼‍💼|🧢|🗽)?\s*(?:<b>)?\s*{ex_h}\s*:\s*(?:</b>)?", s_ln, flags=re.IGNORECASE):
                        dropped = True
                        break
                if not dropped:
                    lines_out.append(s_ln)
            cleaned = " ".join(lines_out).strip()
            return re.sub(r"^(?:🧔‍♂️|👩🏼‍💼|🧢|🗽)?\s*(?:<b>)?\s*(?:Marcus|Chloe|Jake)\s*:\s*(?:</b>)?\s*", "", cleaned, flags=re.IGNORECASE).strip()

        if res.chat_turns:
            filtered_turns = []
            formatted_bubbles = []
            for ct in res.chat_turns:
                sp_k = (ct.speaker or allowed_hosts[0]).strip().lower()
                if sp_k not in ("marcus", "chloe", "jake"):
                    sp_k = allowed_hosts[0]
                if sp_k in excluded_hosts or (solo_only and sp_k != solo_only):
                    continue
                ct.speaker = sp_k
                p_meta = PERSONAS[sp_k]
                clean_line = _strip_injected_excluded_speakers(ct.text or "")
                ct.text = clean_line
                if clean_line:
                    filtered_turns.append(ct)
                    formatted_bubbles.append(f"{p_meta.get('icon', '🗽')} <b>{p_meta['name']}:</b> {clean_line}")
            if formatted_bubbles:
                res.chat_turns = filtered_turns
                res.lounge_reply = "\n\n".join(formatted_bubbles)
                res.responding_persona = filtered_turns[0].speaker
                return res

        sp_k = called_host if (called_host and called_host not in excluded_hosts) else allowed_hosts[0]
        p_meta = PERSONAS[sp_k]
        clean_reply = _strip_injected_excluded_speakers(res.lounge_reply or "")
        if not clean_reply:
            raise ValueError("Empty conversational response after host filtering")
        res.responding_persona = sp_k
        res.chat_turns = [ChatBubbleLine(speaker=sp_k, pacing="normal", text=clean_reply)]
        res.lounge_reply = f"{p_meta.get('icon', '🗽')} <b>{p_meta['name']}:</b> {clean_reply}"
        return res

    @staticmethod
    def _build_mode_specific_directive(mode: str, session_meta: Dict[str, Any]) -> str:
        if mode in ("devils_advocate", "hot_take"):
            return (
                "CURRENT ROOM: 🥊 DEVIL'S ADVOCATE (SHARP INTELLECTUAL & PRACTICAL PUSHBACK).\n"
                "- DO NOT simply agree with Musa's opinion, and DO NOT give lazy or robotic contradiction!\n"
                "- Challenge Musa's take with realistic, nuanced, real-world counter-arguments from each host's distinct lens:\n"
                "  * Marcus challenges the hidden economic/time-cost trade-offs or structural assumptions (e.g., opportunity cost of grocery shopping, prep, and cleanup after a 10-hour workday, or third-place urban culture).\n"
                "  * Chloe challenges the social, culinary, and psychological dimension (e.g., culinary craft you can't replicate in a home kitchen, social energy, and breaking out of routine).\n"
                "  * Jake challenges it from a street-level NYC reality (e.g., tiny apartment kitchens, $12 bodega/Queens neighborhood gems that cost less than buying 9 raw ingredients that spoil in the fridge).\n"
                "- Have the hosts bounce off each other's points naturally and end by putting a sharp, specific follow-up question back to Musa!"
            )
        if mode == "cultural_decode":
            return (
                "CURRENT ROOM: 🎯 SAY IT RIGHT (PRAGMATICS & REAL-WORLD SITUATIONS).\n"
                "- Focus on how Americans navigate this exact social/workplace situation with natural phrasing and tone."
            )
        return (
            "CURRENT ROOM: 🗣️ NYC SPONTANEOUS BANTER.\n"
            "- Engage naturally like close NYC friends. IMPORTANT: If Musa shares an opinion or take (e.g., 'I think people spend way too much money eating out...'), do NOT just blindly agree! Push back playfully with realistic counter-perspectives, debate each other across turns, and ask Musa a follow-up question."
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

        known_ing_stems = (
            "work", "get", "stay", "look", "breath", "talk", "walk", "think", "go", "do",
            "mak", "tak", "com", "runn", "hitt", "try", "say", "play", "grind", "slack",
            "spinn", "paint", "clean", "crank", "bugg", "push", "pull", "call", "wait"
        )
        ing_pattern = re.compile(r"\b(" + "|".join(known_ing_stems) + r")in'(?!\w)", flags=re.IGNORECASE)

        def _normalize_orthography(txt: str) -> str:
            if not txt:
                return ""
            return ing_pattern.sub(r"\1ing", txt).strip()

        try:
            res, used_stealth_ids = self._raw_process_lounge_turn(*args, **kwargs)
            if not isinstance(res, LoungeTurnResponse):
                raise ValueError("Invalid LoungeTurnResponse object returned")
            if not isinstance(used_stealth_ids, list):
                used_stealth_ids = []

            if getattr(res, "chat_turns", None):
                for ct in res.chat_turns:
                    ct.text = _normalize_orthography(getattr(ct, "text", ""))
            res.lounge_reply = _normalize_orthography(getattr(res, "lounge_reply", ""))
            res = self._finalize_chat_turns(res, active_persona=_active_persona, user_text=user_text)
        except Exception as exc:
            import traceback as _tb
            raw_log_payload = f"Structured turn fallback triggered [{type(exc).__name__}]: {exc}\n{_tb.format_exc()}"
            safe_log_payload = self.redact_sensitive_log(raw_log_payload, private_user_text=str(user_text or ""))
            logger.error(safe_log_payload)

            preset_hosts = list(CAST_PRESETS.get(_active_persona, CAST_PRESETS["trio"])["hosts"])
            called_h, excluded_h, solo_h = self._parse_host_directives(str(user_text or ""))
            allowed_h = [h for h in preset_hosts if h not in excluded_h]

            if solo_h and solo_h not in excluded_h:
                preferred_host = solo_h
            elif called_h and called_h not in excluded_h:
                preferred_host = called_h
            elif allowed_h:
                preferred_host = allowed_h[0]
            else:
                preferred_host = None

            fb_msg = "I'm having a technical hiccup. Give me a moment and try that again."
            if preferred_host in ("marcus", "chloe", "jake"):
                p_meta = PERSONAS[preferred_host]
                fb_speaker = preferred_host
                fb_formatted = f"{p_meta.get('icon', '🗽')} <b>{p_meta['name']}:</b> {fb_msg}"
            else:
                fb_speaker = "jake"
                fb_formatted = f"🗽 <b>The Lounge:</b> {fb_msg}"

            fallback_res = LoungeTurnResponse(
                is_fallback=True,
                responding_persona=fb_speaker,
                chat_turns=[
                    ChatBubbleLine(
                        speaker=fb_speaker,
                        pacing="normal",
                        text=fb_msg
                    )
                ],
                lounge_reply=fb_formatted,
                rewrite_articulate="",
                rewrite_casual="",
                mirror_clean_target="",
                diagnostics_status="not_assessed",
                diagnostics=[],
                lexical_upgrades=[],
            )
            return fallback_res, []

        res.rewrite_articulate = _normalize_orthography(getattr(res, "rewrite_articulate", ""))
        res.rewrite_casual = _normalize_orthography(getattr(res, "rewrite_casual", ""))
        res.rewrite_witty = _normalize_orthography(getattr(res, "rewrite_witty", ""))
        res.tts_clean_target = _normalize_orthography(getattr(res, "tts_clean_target", "")) or res.rewrite_articulate
        res.mirror_clean_target = _normalize_orthography(getattr(res, "mirror_clean_target", ""))

        u_raw = (user_text or getattr(res, "transcription", "") or "").strip()
        u_lower = u_raw.lower()
        has_latin_letters = bool(re.search(r"[a-zA-Z]{2,}", u_raw))
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

        if not u_raw or not has_latin_letters:
            res.diagnostics = []
            res.diagnostics_status = "not_assessed"
        else:
            valid_diags = []
            for d in (getattr(res, "diagnostics", None) or []):
                u_said_raw = (getattr(d, "user_said", "") or "").strip()
                u_said_norm = u_said_raw.lower().strip(".,!?\"'")
                n_fix_norm = (getattr(d, "native_fix", "") or "").strip().lower().strip(".,!?\"'")
                why_raw = (getattr(d, "why_note", "") or "").strip()
                if not u_said_norm or not n_fix_norm or not why_raw or u_said_norm == n_fix_norm:
                    continue
                if u_said_norm not in u_lower:
                    continue
                if u_said_norm in protected_natural_phrases:
                    continue
                if any(bad in n_fix_norm for bad in ("of the mind", "of the opinion", "remote workforce", "looking over their shoulder")):
                    continue
                d.native_fix = _normalize_orthography(d.native_fix)
                valid_diags.append(d)
            res.diagnostics = valid_diags[:4]
            if res.diagnostics:
                res.diagnostics_status = "corrections_found"
            elif getattr(res, "diagnostics_status", "") == "not_assessed" and len(u_raw.split()) <= 3:
                res.diagnostics_status = "not_assessed"
            else:
                res.diagnostics_status = "checked_no_corrections"

        res.lexical_upgrades = self._validate_and_filter_upgrades(
            getattr(res, "lexical_upgrades", None) or [],
            uid=uid,
            badge_r=_badge_r,
            normalize_fn=_normalize_orthography
        )
        res.is_fallback = False
        return res, used_stealth_ids


    @staticmethod
    def _build_validated_cloze(chunk: str, example: str, model_cloze: str = "") -> str:
        """
        Builds a reliable cloze sentence (`______`) for the Quiz button even when the chunk
        starts with 'to ' (e.g., 'to get up to speed') or has bracket placeholders ('drop [amount] on').
        """
        ex = (example or "").strip()
        ch = (chunk or "").strip()
        if not ex or not ch:
            return ""
        if model_cloze and "______" in model_cloze and model_cloze.strip() != ex:
            return model_cloze.strip()

        # 1. Exact case-insensitive match
        pat_exact = re.compile(re.escape(ch), flags=re.IGNORECASE)
        if pat_exact.search(ex):
            return pat_exact.sub("______", ex, count=1)

        # 2. Strip leading 'to ' or 'a ' / 'the ' and bracketed placeholders like '[amount]' or '[someone]'
        core_ch = re.sub(r"^(?:to|a|an|the)\s+", "", ch, flags=re.IGNORECASE).strip()
        core_ch_no_brackets = re.sub(r"\[[^\]]+\]", r"\\w+(?:\\s+\\w+)?", core_ch).strip()
        try:
            pat_flex = re.compile(core_ch_no_brackets, flags=re.IGNORECASE)
            if pat_flex.search(ex):
                return pat_flex.sub("______", ex, count=1)
        except re.error:
            pass

        # 3. Match anchor words of the chunk (allowing verb inflection on the first word, e.g., drop -> dropped)
        words = [w for w in re.findall(r"[A-Za-z']+", core_ch) if w.lower() not in ("someone", "something", "amount", "one", "ones")]
        if len(words) >= 2:
            w0_stem = words[0][:max(3, len(words[0]) - 1)]
            w_last = words[-1]
            pat_span = re.compile(rf"\b{re.escape(w0_stem)}\w*(?:\s+\S+){{1,4}}\s+{re.escape(w_last)}\b", flags=re.IGNORECASE)
            if pat_span.search(ex):
                return pat_span.sub("______", ex, count=1)

        return ""


    def _validate_and_filter_upgrades(
        self,
        raw_upgrades: List[LexicalFrameUpgrade],
        uid: int,
        badge_r: str,
        normalize_fn: Any = None
    ) -> List[LexicalFrameUpgrade]:
        if normalize_fn is None:
            normalize_fn = lambda s: (s or "").strip()

        exclusion_set = {
            re.sub(r"\s+", " ", c.strip().lower())
            for c in self._get_active_exclusion_chunks(uid)
        }

        seen_in_turn = set()
        fresh_valid: List[LexicalFrameUpgrade] = []
        fallback_valid: List[LexicalFrameUpgrade] = []

        for item in (raw_upgrades or []):
            item.register_tag = badge_r
            item.chunk = normalize_fn(getattr(item, "chunk", ""))
            item.b2_foil = normalize_fn(getattr(item, "b2_foil", ""))
            item.explanation = normalize_fn(getattr(item, "explanation", ""))
            item.example = normalize_fn(getattr(item, "example", ""))
            item.followup = normalize_fn(getattr(item, "followup", ""))
            item.arabic_equivalent = (getattr(item, "arabic_equivalent", "") or "").strip()

            if not item.arabic_equivalent and "—" in item.explanation:
                parts = item.explanation.split("—", 1)
                item.arabic_equivalent = parts[0].strip()
                item.explanation = parts[1].strip()

            c_key = re.sub(r"\s+", " ", item.chunk.lower()).strip()
            if not c_key or not item.example or not (item.arabic_equivalent or item.explanation):
                continue
            if c_key in seen_in_turn:
                continue

            item.cloze_sentence = self._build_validated_cloze(
                item.chunk, item.example, getattr(item, "cloze_sentence", "")
            )
            seen_in_turn.add(c_key)
            fallback_valid.append(item)
            if c_key not in exclusion_set:
                fresh_valid.append(item)

        # إذا توفرت عبارات جديدة نأخذها فوراً؛ أما لو كانت الدردشة طويلة جداً في موضوع واحد وتطابقت الـ 3 كلها مع السابق،
        # نحتفظ بعبارة واحدة صالحة كبديل آمن بدلاً من إظهار بطاقة فارغة للمستخدم!
        if fresh_valid:
            selected = fresh_valid[:3]
        elif len(fallback_valid) >= 3:
            selected = fallback_valid[:1]
        else:
            selected = []

        if uid and hasattr(self, "_recent_taught_by_user"):
            hist_list = self._recent_taught_by_user.setdefault(uid, [])
            for s_it in selected:
                norm_c = re.sub(r"\s+", " ", s_it.chunk.lower()).strip()
                if norm_c and norm_c not in hist_list:
                    hist_list.append(norm_c)
            self._recent_taught_by_user[uid] = hist_list[-9:]

        return selected


    def process_situation_gambit(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> SituationGambitResponse:
        uid = user.get("user_id", 0) if isinstance(user, dict) else 0
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
            f"2. Provide `best_line` (REQUIRED NON-EMPTY): the #1 natural first-person spoken line an American would actually say out loud in {vibe_badge}.\n"
            f"3. Provide `softer_alternative` and `firmer_alternative` (REQUIRED NON-EMPTY and distinct from `best_line`) while preserving the member's intended outcome.\n"
            f"4. Explain the pragmatic nuance in `why_it_works`, provide the accurate Arabic equivalent in `arabic_equivalent` (REQUIRED NON-EMPTY), and give a realistic `follow_up_line` if the other person pushes back.\n"
            f"5. Populate `lexical_upgrades` with 3 fresh, reusable spoken gambits from your recommendations (where `example` explicitly contains `chunk` and `arabic_equivalent` is populated)."
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append("Listen to the member's scenario description and return the complete `SituationGambitResponse`.")
        else:
            contents.append(f"SCENARIO DESCRIPTION:\n{user_text}\n\nReturn the complete `SituationGambitResponse` calibrated to {vibe_badge}.")

        for attempt in range(2):
            try:
                g_res: SituationGambitResponse = self._call_structured(
                    contents=contents,
                    schema_cls=SituationGambitResponse,
                    system_instruction=system_prompt,
                    temperature=0.65 if attempt == 0 else 0.45,
                    max_output_tokens=3072
                )
                if not isinstance(g_res, SituationGambitResponse):
                    raise ValueError("Returned object is not SituationGambitResponse")

                g_res.best_line = (g_res.best_line or "").strip()
                g_res.softer_alternative = (g_res.softer_alternative or "").strip()
                g_res.firmer_alternative = (g_res.firmer_alternative or "").strip()
                g_res.why_it_works = (g_res.why_it_works or "").strip()
                g_res.arabic_equivalent = (g_res.arabic_equivalent or "").strip()

                if not g_res.best_line or not g_res.arabic_equivalent or not g_res.why_it_works:
                    raise ValueError("Missing required non-empty fields in SituationGambitResponse")
                if not g_res.softer_alternative or g_res.softer_alternative.lower() == g_res.best_line.lower():
                    raise ValueError("softer_alternative is missing or identical to best_line")
                if not g_res.firmer_alternative or g_res.firmer_alternative.lower() in (g_res.best_line.lower(), g_res.softer_alternative.lower()):
                    raise ValueError("firmer_alternative is missing or not distinct")
                if g_res.responding_persona not in ("marcus", "chloe", "jake"):
                    g_res.responding_persona = "chloe"

                g_res.lexical_upgrades = self._validate_and_filter_upgrades(
                    getattr(g_res, "lexical_upgrades", None) or [],
                    uid=uid,
                    badge_r=vibe_badge
                )
                return g_res
            except Exception as exc:
                logger.warning(f"Situation gambit validation/attempt {attempt + 1} note: {exc}")

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

        do_not_repeat_list = self._get_active_exclusion_chunks(uid)
        do_not_repeat_str = ", ".join([f"'{c}'" for c in do_not_repeat_list]) if do_not_repeat_list else "None"

        raw_history = self.db.get_history(uid, limit=8)
        clean_history = [
            h for h in raw_history
            if "train of thought just derailed" not in (h.get("content") or "")
            and "having a technical hiccup" not in (h.get("content") or "")
            and "studio connection just glitched" not in (h.get("content") or "")
        ][-6:]
        u_norm_now = re.sub(r"\s+", " ", (user_text or "").strip().lower())
        prev_user_msgs = [re.sub(r"\s+", " ", h["content"].strip().lower()) for h in clean_history if h.get("role") == "user"]
        if u_norm_now and u_norm_now in prev_user_msgs:
            hist_str = "Fresh angle on this topic (do NOT repeat earlier reassurance or recycle previous points)."
        else:
            hist_str = "\n".join([f"{h['role'].upper()}: {h['content']}" for h in clean_history]) if clean_history else "Start of conversation."

        vibe_harmony_guides = {
            "articulate": "HARMONY RULE (🎙️ Articulate): All hosts use crisp, natural spoken syntax to draw conceptual, interpersonal, and practical distinctions—no slang or forced jokes.",
            "casual": "HARMONY RULE (☕ Casual): All hosts sound like sharp New York friends hanging out—relaxed contractions, phrasal verbs, and grounded everyday banter.",
            "witty": "HARMONY RULE (🎭 Witty): All hosts use dry irony, playful exaggeration in imagery, and sharp observational humor rooted in the specific scene.",
            "executive": "HARMONY RULE (💼 Executive): ZERO jokes or roasting! Marcus frames strategic trade-offs, Chloe assesses team/stakeholder alignment, and Jake focuses on operational execution and bottlenecks.",
            "diplomatic": "HARMONY RULE (🤝 Diplomatic): ZERO sarcasm or bluntness! All hosts model tactful acknowledgment, face-saving qualification, and constructive reframing."
        }
        harmony_rule = vibe_harmony_guides.get(pref_reg, vibe_harmony_guides["articulate"])

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"CALIBRATION & REGISTER HARMONY FOR THIS TURN:\n"
            f"- Depth: {self._level_calibration_guide(level)}\n"
            f"- {self._persona_routing_directive(active_persona, mode, user_text)}\n"
            f"- ACTIVE VIBE ({active_vibe_badge}): {vibe_rules}\n"
            f"- {harmony_rule}\n"
            f"- TWO PRIMARY SPOKEN RECASTS IN {active_vibe_badge} (Preserve exact pragmatic intent, volition, and natural spoken length):\n"
            f"  1. `rewrite_articulate` (Natural Flow): Clear, natural spoken American recast of the member's own message/question (never overly formal or wordy, and never summarizing the host's reply).\n"
            f"  2. `rewrite_casual` (Punchy Cut): Concise, natural spoken version that keeps the exact same volition and politeness (e.g., use 'can't make tomorrow's meeting', never 'skipping tomorrow's meeting' when something came up).\n"
            f"- COMPREHENSIVE ERROR DETECTION IN `diagnostics`: Identify ALL genuine grammatical, verb-tense, irregular-verb, or agreement errors in the member's message (up to 4 items), ordered with the #1 priority fix first.\n"
            f"- RECENT EXPRESSIONS TO AVOID REPEATING IN `lexical_upgrades`: {do_not_repeat_str}\n"
            f"- {self._build_mode_specific_directive(mode, session_meta)}\n\n"
            f"ORGANIC PHRASE WEAVING:\n"
            f"- Candidate Vault Phrases: {stealth_chunks_str}\n"
            f"- Only weave a candidate phrase into `chat_turns` if it fits 100% naturally. Wrap woven phrases in `<i>phrase</i>`.\n\n"
            f"RECURRING HABITS TO WATCH FOR: {weakness_str}\n"
            f"RELEVANT COLLOCATIONS:\n{rag_str}\n\n"
            f"RECENT CONVERSATION CONTEXT:\n{hist_str}\n"
            f"{PRIORITY_1_TO_5_BLOCK}"
        )

        contents: List[Any] = []
        if audio_bytes:
            contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
            contents.append(
                f"Listen to the member's voice note. Transcribe verbatim in `transcription`, populate `chat_turns` with brisk interactive banter in {active_vibe_badge}, provide `rewrite_articulate` and `rewrite_casual` exclusively for the member's own message preserving exact pragmatic intent, list ALL genuine errors in `diagnostics` (up to 4, priority first), and provide 3 fresh scene-anchored `lexical_upgrades` (avoiding {do_not_repeat_str}) with `arabic_equivalent`."
            )
        else:
            contents.append(
                f"CURRENT MEMBER MESSAGE — the ONLY text to recast and diagnose:\n"
                f"{user_text}\n\n"
                f"RECASTING & DIAGNOSTIC RULES:\n"
                f"- Base both rewrites exclusively on the member's message above (never rewrite the host's answer, and never use 'skipping' when the member has an unplanned conflict—prefer 'can't make').\n"
                f"- If the member's sentence contains multiple grammar/tense/agreement errors (like 'I go', 'I buyed', 'prices was'), include EVERY genuine error as a separate item in `diagnostics` (up to 4 items, priority first).\n"
                f"- Respond in `chat_turns` with brisk, interactive 1-2 sentence turns in {active_vibe_badge}, and provide 3 fresh scene-anchored `lexical_upgrades` (avoiding {do_not_repeat_str}) with `arabic_equivalent`."
            )

        res: LoungeTurnResponse = self._call_structured(
            contents=contents,
            schema_cls=LoungeTurnResponse,
            system_instruction=system_prompt,
            temperature=0.68,
            max_output_tokens=8192
        )

        if not getattr(res, "chat_turns", None) and not (getattr(res, "lounge_reply", "") or "").strip():
            raise ValueError("Empty conversational response from model")

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

    def _get_active_exclusion_chunks(self, uid: int) -> List[str]:
        """
        Balanced anti-recycling window shared identically between the prompt and post-filter:
        - Last 9 expressions shown in the active session (last ~3 turns)
        - Last 12 expressions saved in the user's Vault
        Prevents back-to-back repetition without causing lexical starvation in long chats.
        """
        if not uid:
            return []
        session_recent = list(self._recent_taught_by_user.get(uid, []))[-9:]
        vault_recent: List[str] = []
        try:
            if self.db:
                vault_items = self.db.get_user_vault(uid, limit=12)
                vault_recent = [it["chunk"] for it in vault_items if it.get("chunk")]
        except Exception:
            pass
        combined: List[str] = []
        seen = set()
        for ch in (session_recent + vault_recent):
            norm = re.sub(r"\s+", " ", (ch or "").strip().lower())
            if norm and norm not in seen:
                seen.add(norm)
                combined.append(ch.strip())
        return combined[:18]

    @staticmethod
    def _parse_host_directives(user_text: str) -> Tuple[Optional[str], List[str], Optional[str]]:
        low = (user_text or "").lower()
        excluded: List[str] = []
        for h in ("marcus", "chloe", "jake"):
            excl_pats = (
                rf"\b(?:no|not|without|exclude|excluding|skip|mute)\s+{h}\b",
                rf"\b{h}\b[^.!?]{{0,30}}\b(?:sit\s+(?:this\s+(?:one|turn)\s+)?out|stay\s+out|be\s+quiet|don't\s+answer|do\s+not\s+answer|stop)\b",
                rf"\bsit\s+(?:this\s+(?:one|turn)\s+)?out\s*,?\s*{h}\b",
            )
            if any(re.search(p, low) for p in excl_pats):
                excluded.append(h)

        solo_only: Optional[str] = None
        for h in ("jake", "chloe", "marcus"):
            if h in excluded:
                continue
            solo_pats = (
                rf"\b(?:only|just)\s+(?:as\s+|from\s+|to\s+)?{h}\b",
                rf"\breply\s+only\s+as\s+{h}\b",
                rf"\banswer\s+only\s+as\s+{h}\b",
                rf"\b{h}\s+only\b",
            )
            if any(re.search(p, low) for p in solo_pats):
                solo_only = h
                break

        # If a solo host is explicitly requested, all other hosts are implicitly excluded from having their own speaker turns
        if solo_only:
            for other_h in ("marcus", "chloe", "jake"):
                if other_h != solo_only and other_h not in excluded:
                    excluded.append(other_h)

        addressed: Optional[str] = solo_only
        if not addressed:
            found_positions = []
            for h in ("jake", "chloe", "marcus"):
                if h in excluded:
                    continue
                # Ignore names that appear solely as the object of "ask/tell/roast <host>"
                for m in re.finditer(rf"\b{h}\b", low):
                    prefix_slice = low[max(0, m.start() - 18) : m.start()]
                    if re.search(r"\b(?:ask|asks|asking|tell|tells|about)\s+$", prefix_slice):
                        continue
                    found_positions.append((m.start(), h))
                    break
            if found_positions:
                found_positions.sort(key=lambda x: x[0])
                addressed = found_positions[0][1]

        return addressed, excluded, solo_only

    @staticmethod
    def redact_sensitive_log(raw_text: str, private_user_text: str = "") -> str:
        if not raw_text:
            return ""
        s = str(raw_text)
        if private_user_text and len(private_user_text.strip()) >= 4:
            s = s.replace(private_user_text.strip(), "[REDACTED_USER_TEXT]")
        # 1. Gemini / Google API keys
        s = re.sub(r"AIza[0-9A-Za-z\-_]{20,}", "[REDACTED_GEMINI_KEY]", s)
        # 2. Telegram Bot tokens (e.g., 1234567890:AAH_xyz...)
        s = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", "[REDACTED_TG_TOKEN]", s)
        # 3. GitHub PATs (classic & fine-grained)
        s = re.sub(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{20,}\b", "[REDACTED_GH_TOKEN]", s)
        s = re.sub(r"\bgithub_pat_[0-9A-Za-z_]{20,}\b", "[REDACTED_GH_TOKEN]", s)
        # 4. Authorization / Bearer headers
        s = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", r"\1[REDACTED_BEARER]", s)
        s = re.sub(r"(?i)(Authorization\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/ ]+", r"\1[REDACTED_AUTH]", s)
        # 5. Credentials embedded in URLs (https://user:pass@host)
        s = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED_URL_CREDS]@", s)
        return s
