from validation_layer import LoungeTurnResponse, count_spoken_words, verify_is_complete_sentence_in_source, verify_contiguous_expression_in_sentence
def _universal_null_sanitizer(val):
    if isinstance(val, dict):
        return {k: ([] if any(k.endswith(s) for s in ('turns','diagnostics','upgrades','items','lines','options','gambits','examples','points','cards')) else (False if k.startswith(('is_','has_')) else '')) if v is None else _universal_null_sanitizer(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_universal_null_sanitizer(x) for x in val if x is not None]
    return val

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
from pydantic import BaseModel, Field, field_validator, model_validator

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
    category: str = Field(
        default="🎭 Natural Flow",
        description="One of: '🎭 Natural Flow', '💎 Lexical Upgrade', '📐 Spoken Structure', or '🗽 Pragmatic Nuance'."
    )
    user_said: str = Field(default="")
    native_fix: str = Field(default="")
    why_note: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: ("" if v is None else v) for k, v in data.items()}
        return data

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
    register_tag: str = Field(default="🎙️ Articulate")
    b2_foil: str = Field(default="")
    arabic_equivalent: str = Field(default="")
    explanation: str = Field(default="")
    trigger: str = Field(default="")
    pattern: str = Field(default="")
    followup: str = Field(default="")
    example: str = Field(default="")
    cloze_sentence: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: ("" if v is None else v) for k, v in data.items()}
        return data

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
    speaker: str = Field(default="marcus", description="Must be 'marcus', 'chloe', or 'jake'.")
    pacing: str = Field(default="normal", description="One of: 'fast_interjection', 'normal', or 'thoughtful_slow'.")
    text: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: ("" if v is None else v) for k, v in data.items()}
        return data

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
    responding_persona: str = Field(default="marcus", description="Must be 'marcus', 'chloe', or 'jake'.")
    chat_turns: List[ChatBubbleLine] = Field(default_factory=list)
    lounge_reply: str = Field(default="")
    rewrite_articulate: str = Field(
        default="",
        description="Natural Flow: Smooth, idiomatic spoken American recast of the member's own message preserving every claim and exact epistemic strength."
    )
    rewrite_casual: str = Field(
        default="",
        description="Punchy Cut: Concise, natural spoken version of the member's own message retaining all claims and exact degree of certainty."
    )
    rewrite_witty: str = Field(default="")
    self_repair_pivot: str = Field(default="")
    acoustic_prosody_xray: str = Field(default="")
    tts_clean_target: str = Field(default="")
    mirror_clean_target: str = Field(default="")
    diagnostics_status: str = Field(default="checked_no_corrections")
    diagnostics: List[DiagnosticItem] = Field(default_factory=list)
    lexical_upgrades: List[LexicalFrameUpgrade] = Field(default_factory=list)
    is_fallback: bool = Field(default=False)

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        list_keys = {"chat_turns", "diagnostics", "lexical_upgrades"}
        bool_keys = {"is_fallback"}
        for k, v in list(cleaned.items()):
            if v is None:
                if k in list_keys:
                    cleaned[k] = []
                elif k in bool_keys:
                    cleaned[k] = False
                else:
                    cleaned[k] = ""
        return cleaned

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
1. GENUINE SPOKEN TEXTURE (NO LITERARY CLICHÉS):
   - Avoid writerly or overly crafted metaphors (like "the siren call of..."). Use natural spoken American idioms, discourse markers ("Look,", "I mean,", "Fair enough, though—", "Here's the thing:"), and realistic conversational rhythm.
   - Never lecture or moralize! Treat the member as an equal peer in a sharp discussion.

2. STRICT SEMANTIC, EPISTEMIC & PROPOSITIONAL FIDELITY IN THE MIRROR (`rewrite_articulate` & `rewrite_casual`):
   - Recast ONLY the member's own statement—never summarize the hosts' replies.
   - PRESERVE EVERY CLAIM: If the member's input contains three linked claims (e.g., [1] people are too sensitive these days, [2] you can't say anything without someone getting offended, and [3] it's making honest conversations almost impossible), BOTH `rewrite_articulate` (Natural Flow) and `rewrite_casual` (Punchy Cut) MUST retain all three claims! Never drop a clause just to make `Punchy Cut` shorter.
   - PRESERVE EXACT EPISTEMIC STRENGTH (NO EXAGGERATION):
     * Never make the member's claim more extreme or categorical than what they actually said!
     * If the member says "less productive" or "almost impossible", do NOT escalate it to "a total bust", "a massive hit", or "completely impossible". Keep hedges like "almost", "nearly", "tend to", or "making it hard to".

3. STRICT DIAGNOSTIC HONESTY (`diagnostics`):
   - ONLY flag genuine grammatical, verb-tense, morphological, agreement, or unnatural-collocation errors.
   - If the member's message is already grammatically sound and natural American English (such as: "I think people are too sensitive these days. You can't say anything without someone getting offended, and it's making honest conversations almost impossible."), you MUST return `diagnostics = []` and `diagnostics_status = "checked_no_corrections"`.

4. STRICT CONTEXTUAL INTEGRATION & C1/C2 NUANCE IN `lexical_upgrades`:
   - NEVER extract an unanchored expression that did not appear in the turn! Every single `chunk` (all 3 items) MUST physically appear verbatim inside `chat_turns` (spoken by Marcus, Chloe, or Jake) OR inside `rewrite_articulate` / `rewrite_casual`.
   - `explanation` MUST be 100% pure English and MUST state:
     (1) its exact meaning and how it was used in this turn (e.g., by Marcus/Chloe/Jake or in the Mirror), AND
     (2) WHY a C1/C2 speaker chooses it over the simpler `b2_foil` (its exact register, social tone, and usage boundary).
"""



def _auto_clean_base_patterns(payload_dict: dict) -> dict:
    fillers = {'entirely', 'completely', 'totally', 'really', 'actually', 'simply', 'merely', 'literally', 'definitely', 'certainly'}
    keep_ing = {'nothing', 'something', 'anything', 'everything', 'bring', 'spring', 'sing', 'ring', 'cling', 'swing', 'sting', 'wing'}
    items = payload_dict.get('lexical_items') if isinstance(payload_dict, dict) else None
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and isinstance(it.get('expression'), str):
                words = [w for w in it['expression'].split() if w.lower().strip('.,!?') not in fillers]
                if words:
                    w0 = words[0].lower()
                    if w0.endswith('ing') and w0 not in keep_ing and len(w0) > 4:
                        stem = w0[:-3]
                        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in 'aeioulsz':
                            words[0] = stem[:-1]
                        elif stem.endswith(('at', 'iz', 'us', 'iv', 'ag', 'ak', 'am', 'ap', 'av', 'id', 'it', 'od', 'ok', 'on', 'op', 'ot', 'ov', 'ul', 'um', 'un', 'up', 'ur', 'ut')):
                            words[0] = stem + 'e'
                        else:
                            words[0] = stem
                    it['expression'] = ' '.join(words)
    return payload_dict


# === CLAUDE V2 TWO-CALL ENGINE & STATEFUL PRACTICE ===
CLAUDE_BEHAVIORAL_PERSONAS = """
BEHAVIORAL RULES FOR THE NYC TRIO (NO ADJECTIVE CLICHES, NO META-LANGUAGE):
- Marcus (<= 35 words): Picks up on trade-offs and concrete details. Concedes a fair point before pushing back ("Fair point, though..."). Uses complete, measured sentences. Asks about the specific condition, cost, or number. Never uses sarcasm.
- Chloe (<= 28 words): Picks up on what is left unsaid (awkwardness, motive, social friction). Reframes what others said sharply. Uses crisp sentences and pointed questions. Never lectures or explains.
- Jake (<= 18 words): Cuts straight to the practical bottom line (time, effort, what we actually do next). Uses punchy spoken fragments, interrupts lightly, and avoids abstract words.

HARD DIALOGUE RULES (ENFORCED BY VALIDATOR):
1. Return 2 to 4 turns in whatever speaker order fits the moment—NEVER write three parallel speeches of equal length.
2. At most ONE turn in the entire response may contain a question mark (`?`), directed naturally at the learner.
3. NEVER talk about language, phrases, idioms, or linguistics in the characters' dialogue (banned: "the ultimate linguistic...", "shots fired", "lost the plot"). Talk about the people and the situation.
4. At most ONE figurative metaphor across the entire turn (and zero unless the learner asked about idioms/proverbs).
""".strip()

CLAUDE_COACH_SYSTEM_PROMPT = """
You are the precision C1-C2 Spoken American English Coach analyzing a learner's message and the NYC Trio's dialogue.
Return ONLY valid JSON matching `CoachResponse`:
{
  "mirror": {
    "verdict": "natural" | "fix",
    "edits": [
      {
        "span": "<exact contiguous verbatim substring from learner's message, <= 8 words>",
        "replacement": "<natural spoken American replacement, <= span words + 2>",
        "trigger": "register" | "calque" | "wordy" | "reference" | "pragmatic" | "error",
        "why": "<concise spoken-ear reason in <= 14 words>"
      }
    ]
  },
  "chunks": [
    {
      "turn": 0,
      "expression": "<exact contiguous verbatim phrase used in turns[turn].text>",
      "base_pattern": "<canonical reusable frame with [something]/[someone]>",
      "kind": "move" | "phrasal" | "chunk",
      "does": "<what it achieves in conversation, <= 12 words>",
      "say_it_when": "<when an American says this out loud, <= 14 words>",
      "ar": "<natural idiomatic Arabic equivalent>"
    }
  ]
}

GATE FOR NATIVE MIRROR (JUDGE BY THE EAR, NOT THE EYE):
1. Would a native American in a casual NYC lounge say the learner's sentence like this (ignoring punctuation, capitalization, contractions, or missing commas)?
   - If YES (e.g., "What's your favorite English expression guys, and why?" or "What have you been up to everybody?") -> set `"verdict": "natural"` and `"edits": []`.
   - If NO because of a real spoken-ear issue—such as shifting person/pronouns mid-sentence (e.g., addressing "each one of you" then saying "makes sense for him or her" instead of "fits you"), a literal translation calque, or stiff written phrasing -> set `"verdict": "fix"` and isolate ONLY the awkward `span` (<= 10 words) and its brisk spoken American `replacement`.
2. NEVER rewrite the full sentence. Only isolate the exact contiguous `span` and replace it with a shorter or equal spoken `replacement`.
3. NEVER inflate a short phrase into formal/academic wording (never use "why is that the case", "resonates with", "personal approach").

RULES FOR SPOKEN CHUNKS (1 TO 2 ITEMS):
1. Extract 1 or 2 genuine spoken American moves, phrasal verbs, or everyday conversational chunks verbatim from `turns`.
2. Prioritize conversational moves (handling pushback, hedging, agreeing with reservations) and everyday phrasal chunks over rare proverbs.
3. Do NOT repeat any `base_pattern` listed in `RECENTLY_SHOWN_CHUNKS`.
""".strip()

CLAUDE_READ_THE_ROOM_PROMPT = """
You are the Read the Room Pragmatic Engine for C1-C2 Spoken American English.
Given either a spoken quote someone said to the learner OR a social/workplace scenario, return ONLY valid JSON matching `ReadTheRoom`:
{
  "surface_point": "<If quote: brief literal surface meaning + Arabic. If scenario: core communicative goal & social stakes + Arabic>",
  "readings": [
    {"cue": "<tone, pause, relationship, or context cue>", "meaning": "<what they likely mean>", "ar": "<المعنى الضمني بالعربية>"}
  ],
  "moves": [
    {"kind": "ask", "line": "<4-14 word non-presumptive clarifying question>", "when_to_use": "<when to use>", "risk": "low"},
    {"kind": "go_along", "line": "<4-14 word low-friction constructive reply>", "when_to_use": "<when to use>", "risk": "low"},
    {"kind": "name_it", "line": "<4-14 word direct boundary or surfacing reply>", "when_to_use": "<when to use>", "risk": "mid"}
  ],
  "lounge_take": [
    {"speaker": "Marcus", "text": "<1-2 plain sentences about the people and social dynamic, <= 35 words>"},
    {"speaker": "Chloe", "text": "<1 crisp sentence about the practical play, <= 25 words>"}
  ],
  "your_turn": {
    "counterpart": "Marcus" | "Chloe" | "Jake",
    "their_line": "<exact spoken line the counterpart says to the learner in roleplay>",
    "hidden_reading": 0,
    "goal": "<what a strong spoken reply from the learner should achieve>",
    "prompt": "<1 direct question inviting the learner to reply in character>"
  },
  "chunks": [
    {
      "turn": 0,
      "expression": "<verbatim contiguous expression from lounge_take[turn].text>",
      "base_pattern": "<canonical base pattern>",
      "kind": "move" | "phrasal" | "chunk",
      "does": "<pragmatic function in <= 12 words>",
      "say_it_when": "<when to use in <= 14 words>",
      "ar": "<المقابل العربي الطبيعي>"
    }
  ]
}
CRITICAL:
1. Provide 3 distinct `readings`: Reading 1 MUST be a sincere/neutral/good-faith reading; Reading 2 a hedged/skeptical reading; Reading 3 a pointed/indirectly critical reading. Keep `surface_point` neutral without assuming hostility.
2. `moves[0].kind` MUST be `"ask"` (clarifying intent without assuming hostility).
3. In `your_turn.their_line`, use the target utterance itself in a natural spoken sentence (or a realistic spoken line if the input was a scenario).
4. In `chunks`, extract ONLY genuine idiomatic phrasal verbs, idioms, or conversational moves from `lounge_take` (e.g., "call [someone's] bluff", "put [someone] on the spot")—NEVER extract literal adverb+verb combinations like "secretly think".
""".strip()

CLAUDE_PRACTICE_EVAL_PROMPT = """
You are evaluating the learner's spoken reply to an active Read the Room roleplay exercise.
Return ONLY valid JSON matching `PracticeEval`:
{
  "intent": "attempt" | "off_topic",
  "lands_as": "<<= 20 words: how the learner's line sounds to an American ear against the goal; no hollow praise>",
  "tweak": null | {
    "span": "<exact verbatim substring from learner's reply, <= 8 words>",
    "replacement": "<sharper/more natural spoken American phrasing, <= span words + 2>",
    "trigger": "pragmatic" | "register" | "wordy" | "calque" | "reference" | "error",
    "why": "<why this lands better in <= 14 words>"
  },
  "reply": {
    "speaker": "<counterpart name>",
    "text": "<in-character spoken reaction consistent with `hidden_reading`, <= 28 words>"
  },
  "done": true | false
}
RULES:
1. If the learner changed the subject or asked an unrelated question, set `"intent": "off_topic"`, `"lands_as": null`, `"tweak": null`, `"reply": null`, `"done": true`.
2. If the learner's phrasing is already natural and achieves the goal, set `"tweak": null`.
3. Make the counterpart's `reply` react realistically based on `hidden_reading`.
""".strip()

class NYCLoungeAI:
    def __init__(self, api_key: str, db: LoungeDatabase):
        self.db = db
        raw_pool = GEMINI_KEYS if GEMINI_KEYS else ([api_key] if api_key else [])
        # Deduplicate keys while preserving order
        seen_k = set()
        self.keys_pool = []
        for k in raw_pool:
            ks = (k or "").strip()
            if ks and ks not in seen_k:
                seen_k.add(ks)
                self.keys_pool.append(ks)
        self._key_idx = 0
        self._dead_keys_until: Dict[int, float] = {}
        self._dead_models_until: Dict[str, float] = {}
        self._dead_key_models_until: Dict[Tuple[int, str], float] = {}
        self._models_discovered_from_api: bool = False
        self._discovered_models: List[str] = list(GEMINI_MODELS_CHAIN)
        self._recent_taught_by_user: Dict[int, List[str]] = {}

    def _get_client(self) -> genai.Client:
        if not self.keys_pool:
            raise RuntimeError("API key is not configured.")
        key = self.keys_pool[self._key_idx % len(self.keys_pool)]
        self._key_idx = (self._key_idx + 1) % len(self.keys_pool)
        return genai.Client(api_key=key, http_options={"timeout": 45000})

    def _get_active_models(self, client: Any = None, k_idx: int = 0) -> List[str]:
        """
        Dynamically discovers all live text/structured-output Gemini models via `client.models.list()`
        (excluding retired 1.5/2.0/2.5 models and non-text models), so when `gemini-3-flash-preview`
        hits its 20 RPD free-tier quota or 503 spike, the bot seamlessly uses other live models
        (`gemini-3.1-flash-lite-preview`, `gemini-flash-lite-latest`, `gemini-flash-latest`, etc.)
        which have their own separate Per-Model quota buckets!
        """
        now = time.time()
        if client is not None and not getattr(self, "_models_discovered_from_api", False):
            try:
                api_models = []
                for m in client.models.list():
                    raw_name = getattr(m, "name", "") or ""
                    short_name = raw_name.removeprefix("models/").strip()
                    low = short_name.lower()
                    if not low.startswith("gemini"):
                        continue
                    # Skip retired generations (1.0, 1.5, 2.0, 2.5) and specialized non-text endpoints
                    if any(bad in low for bad in (
                        "1.0", "1.5", "2.0", "2.5", "embedding", "imagen", "veo",
                        "tts", "audio", "vision", "aqa", "robotics", "computer-use",
                        "learnlm", "gemma", "thinking"
                    )):
                        continue
                    actions = [str(a).lower() for a in (getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or [])]
                    if actions and not any("generatecontent" in a for a in actions):
                        continue
                    api_models.append(short_name)

                if api_models:
                    def _rank_model(name: str) -> int:
                        n = name.lower()
                        if "3.1-flash-lite" in n or "3-flash-lite" in n:
                            return 0
                        if "flash-lite" in n:
                            return 1
                        if "3-flash" in n or "3.1-flash" in n or "3.8-flash" in n:
                            return 2
                        if "flash" in n:
                            return 3
                        if "pro" in n:
                            return 4
                        return 5

                    api_models.sort(key=_rank_model)
                    merged = []
                    for cand in api_models + list(self._discovered_models):
                        if cand not in merged and not any(old in cand for old in ("2.0", "2.5")):
                            merged.append(cand)
                    self._discovered_models = merged
                    self._models_discovered_from_api = True
                    logger.info(f"TELEMETRY DISCOVERED_MODELS count={len(merged)} models={merged}")
            except Exception as e:
                logger.warning(f"TELEMETRY model_discovery_note={self.redact_sensitive_log(str(e))[:120]}")

        seed_chain = [
            m for m in getattr(self, "_discovered_models", [])
            if not any(old in m for old in ("2.0", "2.5"))
        ]
        if not seed_chain:
            seed_chain = [
                "gemini-3.1-flash-lite-preview",
                "gemini-3-flash-preview",
                "gemini-flash-lite-latest",
                "gemini-flash-latest",
            ]

        dead_km = getattr(self, "_dead_key_models_until", {})
        active = [
            m for m in seed_chain
            if self._dead_models_until.get(m, 0) < now and dead_km.get((k_idx, m), 0) < now
        ]
        if not active:
            # Fallback to any model not globally 404'd
            active = [m for m in seed_chain if self._dead_models_until.get(m, 0) < now]
        return active or list(seed_chain)

    def _call_structured(
        self,
        contents: List[Any],
        schema_cls: Any,
        system_instruction: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192
    ) -> Any:
        """
        Null-safe, dynamic multi-model, multi-key structured Gemini caller:
        - Isolates 429 Free-Tier Per-Model quota exhaustion per `(key_idx, model_name)` pair.
        - Retries 503 temporary demand spikes with backoff AND falls back across all discovered Gemini 3 / Flash-Lite models.
        - Automatically extracts replacement model hints if Google returns a 404 deprecation message.
        """
        if not self.keys_pool:
            raise RuntimeError("API key is not configured.")

        req_id = uuid.uuid4().hex[:8]
        last_err = None
        now = time.time()
        dead_keys = getattr(self, "_dead_keys_until", {})
        if not hasattr(self, "_dead_key_models_until"):
            self._dead_key_models_until = {}

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
            client = genai.Client(api_key=key, http_options={"timeout": 28000})
            models_to_try = self._get_active_models(client, k_idx=k_idx)
            key_invalid = False

            for model_name in models_to_try:
                if self._dead_models_until.get(model_name, 0) >= time.time():
                    continue
                if self._dead_key_models_until.get((k_idx, model_name), 0) >= time.time():
                    continue

                for attempt in range(1, 3):
                    t0 = time.perf_counter()
                    stage = "api_call"
                    try:
                        cfg_kwargs: Dict[str, Any] = {
                            "system_instruction": system_instruction,
                            "temperature": temperature,
                            "max_output_tokens": eff_tokens,
                            "response_mime_type": "application/json",
                            "response_schema": schema_cls,
                        }
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(**cfg_kwargs)
                        )
                        stage = "schema_validation"
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        raw_txt = (getattr(resp, "text", "") or "").strip()
                        if raw_txt:
                            if raw_txt.startswith("```"):
                                raw_txt = raw_txt.strip("`").removeprefix("json").strip()
                            parsed_obj = schema_cls.model_validate(_universal_null_sanitizer(json.loads(raw_txt)))
                            logger.info(
                                f"TELEMETRY req_id={req_id} key_idx={k_idx} model={model_name} attempt={attempt} "
                                f"stage=complete latency_ms={latency_ms} status=ok_json"
                            )
                            return parsed_obj
                        if getattr(resp, "parsed", None) is not None:
                            logger.info(
                                f"TELEMETRY req_id={req_id} key_idx={k_idx} model={model_name} attempt={attempt} "
                                f"stage=complete latency_ms={latency_ms} status=ok_parsed"
                            )
                            return resp.parsed
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
                            f"detail={err_str[:220]}"
                        )
                        upper_err = err_str.upper()

                        # 1. Invalid or leaked key -> quarantine whole key
                        if any(bad_k in upper_err for bad_k in ("API_KEY_INVALID", "PERMISSION_DENIED", "403", "401", "LEAKED")):
                            self._dead_keys_until[k_idx] = time.time() + 3600
                            key_invalid = True
                            break

                        # 2. 404 Model deprecated/not found -> quarantine model globally and extract replacement hint if present
                        if "404" in upper_err or "NOT_FOUND" in upper_err or "NO LONGER AVAILABLE" in upper_err:
                            self._dead_models_until[model_name] = time.time() + 86400
                            hint_m = re.search(r"models/([a-zA-Z0-9._-]+)", err_str)
                            if hint_m:
                                suggested = hint_m.group(1).strip(".")
                                if suggested and suggested != model_name and suggested not in self._discovered_models:
                                    self._discovered_models.insert(0, suggested)
                                    models_to_try.append(suggested)
                            break

                        # 3. 429 Quota Exceeded -> quarantine ONLY this (key_idx, model_name) pair and immediately try next model on same key!
                        if "429" in upper_err or "RESOURCE_EXHAUSTED" in upper_err:
                            cooldown = 3600 if "PER_DAY" in upper_err or "PERDAY" in upper_err or "DAY" in upper_err else 65
                            self._dead_key_models_until[(k_idx, model_name)] = time.time() + cooldown
                            break

                        # 4. 503 High Demand / Timeout -> retry once after brief pause, then move to next model/key
                        if any(t_err in upper_err for t_err in ("503", "UNAVAILABLE", "502", "504", "TIMEOUT", "DEADLINE")):
                            if attempt == 1:
                                time.sleep(1.5)
                                continue
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
                "CURRENT ROOM: 🥊 DEVIL'S ADVOCATE (NUANCED SPOKEN PUSHBACK — ZERO PREACHING).\n"
                "- SPOKEN REALISM: Sound like sharp Americans at a coffee table—NO literary clichés.\n"
                "- Challenge generalizations by conceding the kernel of truth first, separating 'being offended' from 'open pushback', and distinguishing candor from bluntness.\n"
                "- FORCE AN ACTIVE NEXT TURN: End by asking Musa for a concrete real-life example or where he draws the line on a specific trade-off."
            )
        if mode in ("subtext_decoder", "subtext", "cultural_decode"):
            return (
                "CURRENT ROOM: 🕵️‍♂️ SUBTEXT & NUANCE DECODER (HIGH-PRECISION PRAGMATIC CALIBRATION).\n"
                "1. PROBABILISTIC INTERPRETATION (NO CYNICAL CERTAINTY OR OVERCONFIDENT PROMISES):\n"
                "   - Distinguish clearly between: (a) literal surface meaning, (b) plausible subtext (polite deferral / hedging vs. genuine constraint), and (c) what follow-up evidence is needed.\n"
                "   - NEVER overstate outcomes: if someone agrees to revisit a topic, call it 'an opening to revisit it'—NEVER call a polite 'sure' a 'green light' or guaranteed approval.\n"
                "   - NATURAL LEXICON: Use natural pragmatic terms (polite deferral, soft brush-off, hedging); avoid contrived metaphors like 'corporate soft-shoe'.\n"
                "2. HOW THE 3 HOSTS RESPOND IN `chat_turns`:\n"
                "   - Marcus maps out the realistic interpretations (weighing polite distance vs. genuine practical constraints based on context and track record).\n"
                "   - Chloe unpacks the exact softening phrases and offers a composed, natural C1/C2 diagnostic reply that tests their true obstacle without sounding pushy.\n"
                "   - Jake explains how to read their reaction to that follow-up (distinguishing a concrete next step from repeated postponement) and asks Musa how he wants to play his next move.\n"
                "3. VOCABULARY SEMANTIC PRECISION (`lexical_upgrades`):\n"
                "   - Extract 3 multi-word expressions verbatim from the hosts' turns.\n"
                "   - SEPARATE LEXICAL MEANING FROM CONTEXTUAL INFERENCE: In `explanation`, first state what the expression conventionally means in English as a general phrase, and only then note how the host used it in this specific scenario. Never define a neutral phrase (like 'bold direction' or 'flag for later') as inherently negative or as guaranteeing an outcome."
            )
        if mode in ("situation_gambit", "say_it_right"):
            return (
                "CURRENT ROOM: ⚡ WHAT TO SAY WHEN... (HIGH-STAKES PRAGMATICS & REAL-WORLD SITUATIONS).\n"
                "- Deliver concise, natural lines optimized for live spoken delivery, with strict separation between conventional phrase meaning and contextual effect."
            )
        return (
            "CURRENT ROOM: 🗣️ NYC SPONTANEOUS BANTER.\n"
            "- Engage naturally like close NYC friends with varied turn lengths, grounded spoken reactions, and a concrete follow-up question that invites Musa to speak."
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
        "people are too sensitive these days",
        "too sensitive these days",
        "without someone getting offended",
        "making honest conversations almost impossible",
        "honest conversations almost impossible",
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
        normalize_fn: Any
    ) -> List[LexicalFrameUpgrade]:
        """
        Validates lexical upgrades, removes any stray foreign-script glitches (e.g. Cyrillic/CJK)
        from English fields, enforces non-empty semantic explanations and examples, and tracks taught chunks.
        """
        def _clean_en(txt: str) -> str:
            if not txt:
                return ""
            # Strip any stray Cyrillic, Greek, or CJK characters/words that could leak into English definitions
            cleaned = re.sub(r"[\u0400-\u04FF\u0370-\u03FF\u4E00-\u9FFF]+/?", "", str(txt))
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            return normalize_fn(cleaned)

        valid_list: List[LexicalFrameUpgrade] = []
        seen_chunks = set()
        recent_list = self._recent_taught_by_user.setdefault(uid, [])

        for up in (raw_upgrades or []):
            ch_clean = _clean_en(getattr(up, "chunk", ""))
            if not ch_clean or ch_clean.lower() in seen_chunks:
                continue
            ex_clean = _clean_en(getattr(up, "example", ""))
            expl_clean = _clean_en(getattr(up, "explanation", ""))
            foil_clean = _clean_en(getattr(up, "b2_foil", ""))
            if not ex_clean or not expl_clean:
                continue

            up.chunk = ch_clean
            up.example = ex_clean
            up.explanation = expl_clean
            up.b2_foil = foil_clean
            up.register_tag = badge_r
            up.cloze_sentence = self._build_validated_cloze(ch_clean, ex_clean, _clean_en(getattr(up, "cloze_sentence", "")))
            seen_chunks.add(ch_clean.lower())
            valid_list.append(up)

        for up in valid_list[:3]:
            low_c = up.chunk.lower()
            if low_c not in recent_list:
                recent_list.append(low_c)
        if len(recent_list) > 30:
            del recent_list[:-30]

        return valid_list[:3]


    def process_situation_gambit(
        self,
        user: Dict[str, Any],
        user_text: str = "",
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = "audio/ogg"
    ) -> Any:
        from validation_layer import LoungeTurnResponse
        try:
            pref_reg = user["preferred_register"] if (user and "preferred_register" in user and user["preferred_register"]) else "articulate"
        except Exception:
            pref_reg = "articulate"
        if pref_reg not in REGISTERS_MAP:
            pref_reg = "articulate"
        vibe_badge = REGISTERS_MAP[pref_reg]
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, "")

        clean_user_text = (user_text or "").strip() or "Voice scenario submitted."
        has_audio = bool(audio_bytes)
        trusted_context = {
            "active_mode": "situation_strategy",
            "learner_input_text": clean_user_text,
            "runtime_has_audio_bytes": has_audio,
        }

        json_template_example = {
            "active_mode": "situation_strategy",
            "learner_input_text": clean_user_text,
            "input_intent_type": "request_for_new_phrasing",
            "learner_diagnosis": "already_natural",
            "show_diagnostic_mirror": False,
            "diagnostic_mirror": None,
            "subtext_analysis": None,
            "pronunciation_guide": None,
            "host_turns": [
                {"turn_id": "turn_1", "speaker": "marcus", "text": "..."},
                {"turn_id": "turn_2", "speaker": "chloe", "text": "..."}
            ],
            "strategy_playbook": {
                "situation_objective": "...",
                "relationship_constraints": "...",
                "strategic_options": [
                    {
                        "option_id": "option_1",
                        "archetype": "Diplomatic",
                        "spoken_line": "12 to 22 words ending with . or ?",
                        "arabic_equivalent": "...",
                        "makes_explicit": "...",
                        "leaves_implicit": "...",
                        "remaining_risk": "...",
                        "best_fit_when": "..."
                    },
                    {
                        "option_id": "option_2",
                        "archetype": "Assertive",
                        "spoken_line": "12 to 22 words ending with . or ?",
                        "arabic_equivalent": "...",
                        "makes_explicit": "...",
                        "leaves_implicit": "...",
                        "remaining_risk": "...",
                        "best_fit_when": "..."
                    },
                    {
                        "option_id": "option_3",
                        "archetype": "Concise",
                        "spoken_line": "12 to 22 words ending with . or ?",
                        "arabic_equivalent": "...",
                        "makes_explicit": "...",
                        "leaves_implicit": "...",
                        "remaining_risk": "...",
                        "best_fit_when": "..."
                    }
                ],
                "follow_up_branches": [
                    {
                        "reaction_type": "Deflection or Minimization",
                        "their_likely_reply": "...",
                        "your_follow_up_line": "...",
                        "tactical_note": "..."
                    },
                    {
                        "reaction_type": "Pushback or Defensiveness",
                        "their_likely_reply": "...",
                        "your_follow_up_line": "...",
                        "tactical_note": "..."
                    },
                    {
                        "reaction_type": "Agreement or Cooperation",
                        "their_likely_reply": "...",
                        "your_follow_up_line": "...",
                        "tactical_note": "..."
                    }
                ],
                "when_to_let_it_go": "..."
            },
            "lexical_items": [
                {
                    "expression": "exact contiguous words copied from option_1",
                    "source_type": "from_recommended_option",
                    "source_ref_id": "option_1",
                    "source_speaker": None,
                    "full_sentence_citation": "EXACT full spoken_line of option_1 copied character-for-character.",
                    "usage_status_label": "Used in Strategic Option 1 (Diplomatic)",
                    "core_lexical_meaning": "...",
                    "contextual_function": "...",
                    "register_and_usage": "...",
                    "arabic_equivalent": "...",
                    "contrastive_note": None
                },
                {
                    "expression": "exact contiguous words copied from option_3",
                    "source_type": "from_recommended_option",
                    "source_ref_id": "option_3",
                    "source_speaker": None,
                    "full_sentence_citation": "EXACT full spoken_line of option_3 copied character-for-character.",
                    "usage_status_label": "Used in Strategic Option 3 (Concise)",
                    "core_lexical_meaning": "...",
                    "contextual_function": "...",
                    "register_and_usage": "...",
                    "arabic_equivalent": "...",
                    "contrastive_note": None
                },
                {
                    "expression": "related contrasting expression",
                    "source_type": "related_extension",
                    "source_ref_id": None,
                    "source_speaker": None,
                    "full_sentence_citation": None,
                    "usage_status_label": "Related extension — not used in the cited response",
                    "core_lexical_meaning": "...",
                    "contextual_function": "...",
                    "register_and_usage": "...",
                    "arabic_equivalent": "...",
                    "contrastive_note": "Explain how this differs from the expression in option_1 or option_3."
                }
            ]
        }

        system_prompt = (
            f"{CORE_NYC_PERSONA_PROMPT}\n\n"
            f"ACTIVE ENGINE: CONVERSATION STRATEGIES & WORKPLACE SOFT SKILLS (`situation_strategy`)\n"
            f"TARGET SPOKEN VIBE: {vibe_badge} — {vibe_rules}\n\n"
            "Return ONLY a valid JSON object matching this exact structure (no extra keys allowed):\n"
            f"{json.dumps(json_template_example, ensure_ascii=False, indent=2)}\n\n"
            "MANDATORY SCHEMA & EPISTEMIC RULES (ENFORCED BY PYDANTIC v2 VALIDATOR):\n"
            f"1. Set `learner_input_text` to the exact string: {json.dumps(clean_user_text)}\n"
            "2. ZERO FABRICATION OF UNSTATED FACTS:\n"
            "   - Do NOT invent unverified technical components (no payment flows, APIs, databases, or specific feature names).\n"
            "   - Do NOT invent unverified durations or release names (no 'two weeks', 'Phase 2', or 'week-two update').\n"
            "   - Do NOT open a boundary-setting line with an unconditional commitment; tie any scope addition conditionally to deferring existing items or adjusting the timeline.\n"
            "   - Do NOT promise that deferring one item will automatically free up enough capacity before engineering checks it.\n"
            "3. WORD COUNT: Each `spoken_line` in `strategic_options` MUST be a single sentence of 12 to 22 words (`12 <= words <= 22`) ending in `.` or `?`.\n"
            "4. CITATIONS: For `from_recommended_option`, `full_sentence_citation` MUST be the exact, complete `spoken_line` of that `option_id`, and `expression` must appear inside it as an unbroken contiguous phrase using the exact same word forms."
        )

        last_err = None
        now = time.time()
        dead_keys = getattr(self, "_dead_keys_until", {})
        if not hasattr(self, "_dead_key_models_until"):
            self._dead_key_models_until = {}

        healthy_key_indices = [i for i in range(len(self.keys_pool)) if dead_keys.get(i, 0) < now]
        if not healthy_key_indices and self.keys_pool:
            healthy_key_indices = list(range(len(self.keys_pool)))

        tries_done = 0
        for k_idx in healthy_key_indices[:2]:
            key = self.keys_pool[k_idx]
            client = genai.Client(api_key=key, http_options={"timeout": 25000})
            models_to_try = self._get_active_models(client, k_idx=k_idx)
            for model_name in models_to_try:
                if self._dead_models_until.get(model_name, 0) >= time.time():
                    continue
                if self._dead_key_models_until.get((k_idx, model_name), 0) >= time.time():
                    continue
                tries_done += 1
                prompt_suffix = ""
                if last_err:
                    prompt_suffix = f"\n\nYOUR PREVIOUS JSON FAILED PYDANTIC VALIDATION WITH ERROR:\n{last_err}\nFix this exact validation error immediately."

                contents: List[Any] = []
                if audio_bytes:
                    contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
                    contents.append(f"Listen to the scenario and return valid JSON matching LoungeTurnResponse.{prompt_suffix}")
                else:
                    contents.append(f"LEARNER SCENARIO:\n{clean_user_text}\n\nReturn valid JSON matching LoungeTurnResponse.{prompt_suffix}")

                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.75 if tries_done == 1 else 0.55,
                            response_mime_type="application/json",
                            max_output_tokens=4096,
                        ),
                    )
                    raw_text = (getattr(resp, "text", "") or "").strip()
                    if raw_text.startswith("```"):
                        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
                    payload_dict = _auto_clean_base_patterns(json.loads(raw_text))
                    validated: LoungeTurnResponse = LoungeTurnResponse.model_validate(
                        payload_dict,
                        context=trusted_context
                    )
                    logger.info(f"✅ LoungeTurnResponse LIVE Validation PASSED on try {tries_done} ({model_name})!")
                    return validated
                except Exception as e:
                    err_str = str(e)
                    last_err = err_str
                    logger.warning(f"LoungeTurnResponse live try {tries_done} ({model_name}) failed: {err_str[:220]}")
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        self._dead_key_models_until[(k_idx, model_name)] = time.time() + 65
                    elif "404" in err_str or "NOT_FOUND" in err_str:
                        self._dead_models_until[model_name] = time.time() + 86400
                if tries_done >= 4:
                    break
            if tries_done >= 4:
                break

        fallback_payload = {
            "active_mode": "situation_strategy",
            "learner_input_text": clean_user_text,
            "input_intent_type": "request_for_new_phrasing",
            "learner_diagnosis": "already_natural",
            "show_diagnostic_mirror": False,
            "diagnostic_mirror": None,
            "subtext_analysis": None,
            "pronunciation_guide": None,
            "host_turns": [
                {"turn_id": "turn_1", "speaker": "marcus", "text": "The cleanest move is to frame this as a scope-versus-schedule trade-off rather than a refusal."},
                {"turn_id": "turn_2", "speaker": "chloe", "text": "Right—when you ask them how they want to prioritize the current scope, you stay helpful while protecting Friday's deadline."}
            ],
            "strategy_playbook": {
                "situation_objective": "Show willingness to help while tying any new additions to either deferring existing work or moving the delivery timeline.",
                "relationship_constraints": "External client requesting three extra features by Friday while holding the launch date fixed.",
                "strategic_options": [
                    {
                        "option_id": "option_1",
                        "archetype": "Diplomatic",
                        "spoken_line": "To keep our launch date intact, which existing items can we defer so we can deliver these by Friday?",
                        "arabic_equivalent": "للحفاظ على موعد الإطلاق كما هو، ما العناصر الحالية التي ينبغي تأجيلها لكي نمنح الأولوية لهذه الميزات بحلول الجمعة؟",
                        "makes_explicit": "Adding new work by Friday requires deferring existing items if the launch date cannot move.",
                        "leaves_implicit": "How much existing work must be deferred until engineering estimates the three features.",
                        "remaining_risk": "The client may insist that all current items and the three new features are equally urgent.",
                        "best_fit_when": "You want to sound collaborative and invite the client to co-own the prioritization trade-off."
                    },
                    {
                        "option_id": "option_2",
                        "archetype": "Assertive",
                        "spoken_line": "Delivering three extra features by Friday would require either adjusting our current scope or moving the launch date.",
                        "arabic_equivalent": "سيتطلب تسليم ثلاث ميزات إضافية بحلول الجمعة إما تعديل نطاق العمل الحالي أو تحريك موعد الإطلاق.",
                        "makes_explicit": "The operational constraint that three additional features cannot be absorbed into Friday's schedule without a trade-off.",
                        "leaves_implicit": "Internal staffing details—it focuses strictly on delivery options.",
                        "remaining_risk": "More direct; a pressured client may ask why the team cannot absorb the extra work.",
                        "best_fit_when": "You need to state a firm, unmistakable boundary without sounding defensive."
                    },
                    {
                        "option_id": "option_3",
                        "archetype": "Concise",
                        "spoken_line": "We can prioritize those for Friday if we swap out current items or push the launch date back.",
                        "arabic_equivalent": "يمكننا منح الأولوية لتلك الميزات ليوم الجمعة إذا استبدلنا عناصر حالية أو أجّلنا موعد الإطلاق.",
                        "makes_explicit": "The conditional trade-off in a single brief sentence.",
                        "leaves_implicit": "Specific swap candidates until the client agrees to the trade-off principle.",
                        "remaining_risk": "Brief enough that a demanding client may test whether the constraint is negotiable.",
                        "best_fit_when": "You need a quick, composed answer in a fast-moving meeting."
                    }
                ],
                "follow_up_branches": [
                    {
                        "reaction_type": "Deflection or Minimization",
                        "their_likely_reply": "Come on, these three features are small—can't you just fit them in by Friday?",
                        "your_follow_up_line": "Even small additions need to be scoped and tested before we commit, so let's confirm the impact before changing Friday's plan.",
                        "tactical_note": "Require an engineering impact check before committing, without inventing technical details."
                    },
                    {
                        "reaction_type": "Pushback or Defensiveness",
                        "their_likely_reply": "We really need all three features by Friday and the launch date cannot move.",
                        "your_follow_up_line": "We can protect the launch date for the core release and consider moving these three features to a later release, subject to confirming the plan and timeline.",
                        "tactical_note": "Protect the core launch date while proposing a later release conditionally."
                    },
                    {
                        "reaction_type": "Agreement or Cooperation",
                        "their_likely_reply": "Fair enough—what if we defer part of the current list?",
                        "your_follow_up_line": "Let's identify which current items can wait, then confirm with engineering whether that frees up enough capacity by Friday.",
                        "tactical_note": "Avoid promising all three features fit by Friday before checking capacity with engineering."
                    }
                ],
                "when_to_let_it_go": "If the client refuses both trade-offs, do not commit on the spot; offer to review capacity with engineering and follow up in writing."
            },
            "lexical_items": [
                {
                    "expression": "keep [something] intact",
                    "source_type": "from_recommended_option",
                    "source_ref_id": "option_1",
                    "source_speaker": None,
                    "full_sentence_citation": "To keep our launch date intact, which existing items can we defer so we can deliver these by Friday?",
                    "usage_status_label": "Used in Strategic Option 1 (Diplomatic)",
                    "core_lexical_meaning": "Preserve something complete and unchanged.",
                    "contextual_function": "Frames protecting the fixed launch date as a shared priority.",
                    "register_and_usage": "Neutral professional and general American English.",
                    "arabic_equivalent": "يحافظ على الشيء كما هو دون تغيير أو مساس"
                },
                {
                    "expression": "push [something] back",
                    "source_type": "from_recommended_option",
                    "source_ref_id": "option_3",
                    "source_speaker": None,
                    "full_sentence_citation": "We can prioritize those for Friday if we swap out current items or push the launch date back.",
                    "usage_status_label": "Used in Strategic Option 3 (Concise)",
                    "core_lexical_meaning": "Move a scheduled event or deadline to a later time.",
                    "contextual_function": "Refers neutrally to moving the launch date later on the calendar.",
                    "register_and_usage": "Everyday workplace and conversational American English.",
                    "arabic_equivalent": "يؤخّر أو يؤجّل موعداً إلى وقت لاحق"
                },
                {
                    "expression": "base_verb + preposition + [something]",
                    "source_type": "related_extension",
                    "source_ref_id": None,
                    "source_speaker": None,
                    "full_sentence_citation": None,
                    "usage_status_label": "Related extension — not used in the cited response",
                    "core_lexical_meaning": "Object to, resist, or ask to reconsider a request, proposal, or assumption (not necessarily a flat refusal).",
                    "contextual_function": "Contrasts objecting to or questioning part of a request with rescheduling a date.",
                    "register_and_usage": "Common workplace English.",
                    "arabic_equivalent": "يعترض على طلب أو يتحفّظ عليه أو يطلب إعادة النظر فيه",
                    "contrastive_note": "Unlike 'push [a date] back' (reschedule to a later time), 'push back on [a request]' means objecting to, resisting, or asking to reconsider a demand."
                }
            ]
        }
        return LoungeTurnResponse.model_validate(fallback_payload, context=trusted_context)

    def process_subtext_decoder(
        self,
        user: Dict[str, Any],
        user_text: str = '',
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = 'audio/ogg'
    ) -> Any:
        from validation_layer import LoungeTurnResponse
        try:
            pref_reg = user['preferred_register'] if (user and 'preferred_register' in user and user['preferred_register']) else 'articulate'
        except Exception:
            pref_reg = 'articulate'
        if pref_reg not in REGISTERS_MAP:
            pref_reg = 'articulate'
        vibe_badge = REGISTERS_MAP[pref_reg]
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, '')

        clean_user_text = (user_text or '').strip() or 'Utterance submitted.'
        has_audio = bool(audio_bytes)
        trusted_context = {
            'active_mode': 'subtext_decoder',
            'learner_input_text': clean_user_text,
            'runtime_has_audio_bytes': has_audio,
        }
        sub_template = json.loads('{"literal_meaning": "<Explain the literal surface meaning of the words clearly + accurate Arabic literal meaning>", "observable_facts": "<State only what is explicitly observable from the words vs. what context remains unknown>", "plausible_implicatures": ["1. [Neutral / Sincere / Good-Faith Reading]: <Explain when this phrase is used sincerely or neutrally + natural spoken American example + المقابل العربي الدقيق للمقصود>", "2. [Hedged / Skeptical / Mildly Guarded Reading]: <Explain when this phrase signals polite doubt, hesitation, or wistful comparison + natural spoken American example + المقابل العربي الدقيق للمقصود>", "3. [Pointed / Dry / Indirectly Critical Reading]: <Explain when this phrase carries dry sarcasm, disapproval, or a subtle dig + natural spoken American example + المقابل العربي الدقيق للمقصود>"], "behavior_confidence": "Moderate", "motive_confidence": "Low", "evidence_to_watch": "<Order clues by reliability: (1) Prior conversational context, (2) Relationship & past history, (3) Vocal delivery cues (stress, pitch, pauses—qualified as probabilistic, never deterministic), (4) Repeated pattern>", "escalation_options": [{"option_id": "escalation_1", "label": "<Short label for Option 1: Easygoing / Good-Faith Reply>", "objective": "<Communicative goal when taking the remark lightly or constructively>", "exact_wording": "<Write a fresh, natural spoken American reply (5 to 16 words, using natural contractions) tailored specifically to the target utterance>", "makes_explicit": "<What this reply signals socially>", "interpersonal_tradeoff": "<Pros and cons of choosing this low-friction reply>", "when_not_to_use": "<When this reply would be the wrong choice>"}, {"option_id": "escalation_2", "label": "<Short label for Option 2: Clarifying / Constructive Invite>", "objective": "<Communicative goal when inviting the other person to share a better idea or clarify their concern without sounding defensive>", "exact_wording": "<Write a fresh, natural spoken American reply (5 to 16 words, using natural contractions) tailored specifically to the target utterance>", "makes_explicit": "<What this reply brings into the conversation>", "interpersonal_tradeoff": "<Pros and cons of this middle-ground reply>", "when_not_to_use": "<When to skip this option>"}, {"option_id": "escalation_3", "label": "<Short label for Option 3: Direct / Lightly Surfacing the Subtext>", "objective": "<Communicative goal when checking directly but casually if they have a real objection or dig>", "exact_wording": "<Write a fresh, natural, crisp spoken American reply (4 to 15 words, conversational—not stiff or overly formal) tailored specifically to the target utterance>", "makes_explicit": "<What unspoken tension this surfaces>", "interpersonal_tradeoff": "<Pros and cons of addressing the subtext directly>", "when_not_to_use": "<When this direct check-in is inappropriate>"}]}')
        json_template_example = {
            'active_mode': 'subtext_decoder',
            'learner_input_text': clean_user_text,
            'input_intent_type': 'general_conversation_or_question',
            'learner_diagnosis': 'already_natural',
            'show_diagnostic_mirror': False,
            'diagnostic_mirror': None,
            'subtext_analysis': sub_template,
            'strategy_playbook': None,
            'pronunciation_guide': None,
            'host_turns': [
                {
                    'turn_id': 'turn_1',
                    'speaker': 'marcus',
                    'text': '<2 natural spoken American sentences (no theatrical metaphors) explaining how Americans actually use and hear this phrase across situations.>'
                },
                {
                    'turn_id': 'turn_2',
                    'speaker': 'chloe',
                    'text': '<2 natural spoken American sentences showing how to respond smoothly in real life, ending with a short situational question for the learner.>'
                }
            ],
            'lexical_items': [
                {
                    'expression': '<canonical base pattern from turn_1: strip optional adverbs like entirely/completely; use base verb + [something]/[someone]>',
                    'source_type': 'verbatim_from_host_dialogue',
                    'source_ref_id': 'turn_1',
                    'source_speaker': 'marcus',
                    'full_sentence_citation': '<EXACT single complete sentence from turn_1 ending in .!? containing expression>',
                    'usage_status_label': 'Verbatim from Marcus (turn_1)',
                    'core_lexical_meaning': '<Accurate, calibrated definition>',
                    'contextual_function': '<Pragmatic function in this conversation>',
                    'register_and_usage': '[Type: phrasal_verb | idiom | conversational_frame | collocation] <When it sounds natural vs. when a simpler everyday alternative is better>',
                    'arabic_equivalent': '<Natural idiomatic Arabic equivalent conveying intent>',
                    'contrastive_note': None
                },
                {
                    'expression': '<canonical base pattern from turn_2: strip optional adverbs; use base verb + [something]/[someone]>',
                    'source_type': 'verbatim_from_host_dialogue',
                    'source_ref_id': 'turn_2',
                    'source_speaker': 'chloe',
                    'full_sentence_citation': '<EXACT single complete sentence from turn_2 ending in .!? containing expression>',
                    'usage_status_label': 'Verbatim from Chloe (turn_2)',
                    'core_lexical_meaning': '<Accurate, calibrated definition>',
                    'contextual_function': '<Pragmatic function in context>',
                    'register_and_usage': '[Type: phrasal_verb | idiom | conversational_frame | collocation] <Register & constraints>',
                    'arabic_equivalent': '<Natural idiomatic Arabic equivalent conveying intent>',
                    'contrastive_note': None
                },
                {
                    'expression': '<genuine fixed idiom or phrasal verb (NOT a free -ing combination) NOT used anywhere in turn_1 or turn_2>',
                    'source_type': 'related_extension',
                    'source_ref_id': None,
                    'source_speaker': None,
                    'full_sentence_citation': None,
                    'usage_status_label': 'Related extension — not used in the cited response',
                    'core_lexical_meaning': '<Accurate definition>',
                    'contextual_function': '<How it relates to indirect speech, tone, or subtext>',
                    'register_and_usage': '[Type: idiom | phrasal_verb | conversational_frame] <Register & constraints>',
                    'arabic_equivalent': '<Natural idiomatic Arabic equivalent ONLY>',
                    'contrastive_note': '<Precise semantic contrast with expression 1 or 2>'
                }
            ]
        }

        rules_list = [
            f'{CORE_NYC_PERSONA_PROMPT}',
            'ACTIVE ENGINE: SUBTEXT & NUANCE DECODER (`active_mode=subtext_decoder`)',
            f'TARGET SPOKEN VIBE: {vibe_badge} — {vibe_rules}',
            'Return ONLY a valid JSON object matching this exact schema (replace all `<...>` instructions with fresh, utterance-specific content):',
            json.dumps(json_template_example, ensure_ascii=False, indent=2),
            'MANDATORY SUBTEXT DECODER RULES (ZERO BIAS & ZERO TEMPLATE LEAKAGE):',
            f'1. Set `learner_input_text` to the exact string: {json.dumps(clean_user_text)}',
            '2. HIERARCHY OF PRAGMATIC EVIDENCE (`evidence_to_watch`): Teach the learner to weigh clues in this order: (1) Situational Context (what happened or was proposed right before the remark), (2) Relationship & History (trust vs. rivalry/frustration), (3) Vocal Delivery (stress, pitch contour, pauses—always frame vocal cues as probabilistic possibilities using "can" or "may", never as fixed deterministic proof), and (4) Repeated Pattern.',
            '3. UTTERANCE-SPECIFIC `escalation_options`: Every `exact_wording` MUST directly respond to the specific phrase in `learner_input_text` in natural, everyday spoken American English (use contractions like "I\'m", "that\'s", "you\'ve"; avoid defensive over-explaining or stiff textbook sentences; keep between 4 and 16 words).',
            '4. NATURAL HOST DIALOGUE: Let Marcus and Chloe speak freely, naturally, and reactively in their authentic voices.',
            '5. STRICT LEXICAL CLASSIFICATION & CANONICAL FRAMES: Strip optional intensifier adverbs (do NOT include words like "entirely", "completely", "really", "actually", "simply" inside `expression`). Never select a bare `-ing` free combination (like "masking resentment") as a target expression; choose a genuine `idiom`, `phrasal_verb`, or `conversational_frame` in base form and label its type in `register_and_usage`.',
        ]
        system_prompt = '\n\n'.join(rules_list)

        last_err = None
        now = time.time()
        dead_keys = getattr(self, '_dead_keys_until', {})
        if not hasattr(self, '_dead_key_models_until'):
            self._dead_key_models_until = {}
        healthy_key_indices = [i for i in range(len(self.keys_pool)) if dead_keys.get(i, 0) < now]
        if not healthy_key_indices and self.keys_pool:
            healthy_key_indices = list(range(len(self.keys_pool)))

        tries_done = 0
        for k_idx in healthy_key_indices[:3]:
            key = self.keys_pool[k_idx]
            client = genai.Client(api_key=key, http_options={'timeout': 30000})
            models_to_try = self._get_active_models(client, k_idx=k_idx)
            for model_name in models_to_try:
                if self._dead_models_until.get(model_name, 0) >= time.time():
                    continue
                if self._dead_key_models_until.get((k_idx, model_name), 0) >= time.time():
                    continue
                tries_done += 1
                prompt_suffix = ''
                if last_err:
                    prompt_suffix = f'\n\nYOUR PREVIOUS JSON FAILED VALIDATION WITH ERROR:\n{last_err}\nFix this exact error immediately.'
                contents: List[Any] = [
                    f'UTTERANCE OR SCENARIO TO DECODE FOR SUBTEXT & NUANCE:\n{clean_user_text}\n\nReturn valid JSON matching LoungeTurnResponse.{prompt_suffix}'
                ]
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.75 if tries_done == 1 else 0.55,
                            response_mime_type='application/json',
                            max_output_tokens=8192,
                        ),
                    )
                    raw_text = (getattr(resp, 'text', '') or '').strip()
                    if raw_text.startswith('```'):
                        raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text, flags=re.DOTALL).strip()
                    payload_dict = _auto_clean_base_patterns(json.loads(raw_text))
                    validated: LoungeTurnResponse = LoungeTurnResponse.model_validate(
                        payload_dict,
                        context=trusted_context
                    )
                    logger.info(f'✅ Subtext Decoder LIVE Validation PASSED on try {tries_done} ({model_name})!')
                    return validated
                except Exception as e:
                    err_str = str(e)
                    last_err = err_str
                    logger.warning(f'Subtext Decoder live try {tries_done} ({model_name}) failed: {err_str[:220]}')
                    if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                        self._dead_key_models_until[(k_idx, model_name)] = time.time() + 65
                    elif '404' in err_str or 'NOT_FOUND' in err_str:
                        self._dead_models_until[model_name] = time.time() + 86400
                if tries_done >= 8:
                    break
            if tries_done >= 8:
                break
        raise RuntimeError(f'Subtext Decoder live generation failed after {tries_done} tries: {last_err}')

    def process_devils_advocate(
        self,
        user: Dict[str, Any],
        user_text: str = '',
        audio_bytes: Optional[bytes] = None,
        audio_mime: str = 'audio/ogg'
    ) -> Any:
        from validation_layer import LoungeTurnResponse
        try:
            pref_reg = user['preferred_register'] if (user and 'preferred_register' in user and user['preferred_register']) else 'articulate'
        except Exception:
            pref_reg = 'articulate'
        if pref_reg not in REGISTERS_MAP:
            pref_reg = 'articulate'
        vibe_badge = REGISTERS_MAP[pref_reg]
        vibe_rules = REGISTER_DESCRIPTIONS.get(pref_reg, '')

        clean_user_text = (user_text or '').strip() or 'Voice opinion submitted.'
        has_audio = bool(audio_bytes)
        trusted_context = {
            'active_mode': 'banter',
            'learner_input_text': clean_user_text,
            'runtime_has_audio_bytes': has_audio,
        }

        json_template_example = {
            'active_mode': 'banter',
            'learner_input_text': clean_user_text,
            'input_intent_type': 'general_conversation_or_question',
            'learner_diagnosis': 'already_natural',
            'show_diagnostic_mirror': False,
            'diagnostic_mirror': None,
            'subtext_analysis': None,
            'strategy_playbook': None,
            'pronunciation_guide': None,
            'host_turns': [
                {
                    'turn_id': 'turn_1',
                    'speaker': 'marcus',
                    'text': '2 natural spoken American sentences acknowledging the valid kernel in the learner premise before challenging its core causal assumption.'
                },
                {
                    'turn_id': 'turn_2',
                    'speaker': 'chloe',
                    'text': '2 natural spoken American sentences exposing a distinct second-order consequence or alternative explanation using everyday idiomatic English.'
                }
            ],
            'lexical_items': [
                {
                    'expression': 'reusable canonical frame from turn_1 (use base verb form; replace topic nouns with [something] or [someone])',
                    'source_type': 'verbatim_from_host_dialogue',
                    'source_ref_id': 'turn_1',
                    'source_speaker': 'marcus',
                    'full_sentence_citation': 'EXACT single complete sentence from turn_1 ending in .!? containing expression.',
                    'usage_status_label': 'Verbatim from Marcus (turn_1)',
                    'core_lexical_meaning': 'Precise, calibrated definition matching the exact sense used in turn_1.',
                    'contextual_function': 'Pragmatic function: what the speaker is doing socially/communicatively with this expression (e.g., softening pushback, signaling skepticism, setting a boundary).',
                    'register_and_usage': 'Register & constraints: when this expression sounds natural vs. when it might sound too formal, sarcastic, or out of place, plus a short Active-Use prompt for the learner.',
                    'arabic_equivalent': 'Natural idiomatic Arabic equivalent (NO literal word-for-word calques).',
                    'contrastive_note': None
                },
                {
                    'expression': 'reusable canonical frame from turn_2 (use base verb form; replace topic nouns with [something] or [someone])',
                    'source_type': 'verbatim_from_host_dialogue',
                    'source_ref_id': 'turn_2',
                    'source_speaker': 'chloe',
                    'full_sentence_citation': 'EXACT single complete sentence from turn_2 ending in .!? containing expression.',
                    'usage_status_label': 'Verbatim from Chloe (turn_2)',
                    'core_lexical_meaning': 'Precise, calibrated definition matching the exact sense used in turn_2.',
                    'contextual_function': 'How this expression supports Chloe perspective.',
                    'register_and_usage': 'Register & constraints: when this expression sounds natural vs. when it might sound too formal, sarcastic, or out of place, plus a short Active-Use prompt for the learner.',
                    'arabic_equivalent': 'Natural idiomatic Arabic equivalent (NO literal word-for-word calques).',
                    'contrastive_note': None
                },
                {
                    'expression': 'topic-relevant idiom NOT used anywhere in turn_1 or turn_2',
                    'source_type': 'related_extension',
                    'source_ref_id': None,
                    'source_speaker': None,
                    'full_sentence_citation': None,
                    'usage_status_label': 'Related extension — not used in the cited response',
                    'core_lexical_meaning': 'Accurate, non-exaggerated definition.',
                    'contextual_function': 'How a learner can use this idiom when discussing this exact subject.',
                    'register_and_usage': 'Register note.',
                    'arabic_equivalent': 'Natural idiomatic Arabic equivalent ONLY (never write literal calques).',
                    'contrastive_note': 'Clear semantic contrast with expression 1 or 2.'
                }
            ]
        }

        rules_list = [
            f'{CORE_NYC_PERSONA_PROMPT}',
            f'ACTIVE ENGINE: DEVIL\'S ADVOCATE INTELLECTUAL CHALLENGE (`active_mode=banter`)',
            f'TARGET SPOKEN VIBE: {vibe_badge} — {vibe_rules}',
            'Return ONLY a valid JSON object matching this exact structure (no extra keys allowed):',
            json.dumps(json_template_example, ensure_ascii=False, indent=2),
            'UNIVERSAL DEVIL\'S ADVOCATE & PEDAGOGICAL RULES (TOPIC-AGNOSTIC):',
            f'1. Set `learner_input_text` to the exact string: {json.dumps(clean_user_text)}',
            '2. NATURAL SPOKEN AMERICAN DIALOGUE (NO STACKED ACADEMIC/CONSULTANT JARGON): Marcus and Chloe must sound like articulate Americans talking naturally in person—not a written consulting memo. Avoid stacking abstract buzzwords (such as "operational trade-off", "distributed networks", "impediment", "false dichotomy", or "systemic structural shifts"). Prefer clear, everyday idiomatic English.',
            '3. FAIRNESS, NO STRAW-MANNING & EPISTEMIC CALIBRATION:',
            '   - Acknowledge the valid point in the learner\'s premise first before pushing back.',
            '   - Challenge the specific causal assumption or generalization the learner actually made (do not attack an exaggerated straw-man version of their view, and do not swing to the opposite extreme).',
            '   - Use calibrated probabilistic language ("can", "often", "tends to", "may") rather than deterministic claims ("always", "dictates", "what really happens"). NEVER invent fake statistics or studies.',
            '4. REUSABLE CANONICAL LEXICAL FRAMES: Extract expressions in their canonical base pattern, replacing context-specific nouns with `[something]` or `[someone]`. Avoid repeating overused debate clichés.',
            '5. TOPIC-COUPLED RELATED EXTENSION & IDIOMATIC ARABIC: Item (3) (`related_extension`) must belong to the semantic domain of the learner\'s topic (not a random filler idiom) and must NOT appear in `turn_1` or `turn_2`. In `arabic_equivalent`, write ONLY natural idiomatic Arabic—never write literal word-for-word calques.',
            '6. DIAGNOSTIC MIRROR: If the learner\'s English is already natural, set `learner_diagnosis="already_natural"`, `show_diagnostic_mirror=False`, and `diagnostic_mirror=None`.'
        ]
        system_prompt = '\n\n'.join(rules_list)

        last_err = None
        now = time.time()
        dead_keys = getattr(self, '_dead_keys_until', {})
        if not hasattr(self, '_dead_key_models_until'):
            self._dead_key_models_until = {}
        healthy_key_indices = [i for i in range(len(self.keys_pool)) if dead_keys.get(i, 0) < now]
        if not healthy_key_indices and self.keys_pool:
            healthy_key_indices = list(range(len(self.keys_pool)))

        tries_done = 0
        for k_idx in healthy_key_indices[:3]:
            key = self.keys_pool[k_idx]
            client = genai.Client(api_key=key, http_options={'timeout': 25000})
            models_to_try = self._get_active_models(client, k_idx=k_idx)
            for model_name in models_to_try:
                if self._dead_models_until.get(model_name, 0) >= time.time():
                    continue
                if self._dead_key_models_until.get((k_idx, model_name), 0) >= time.time():
                    continue
                tries_done += 1
                prompt_suffix = ''
                if last_err:
                    prompt_suffix = f'\n\nYOUR PREVIOUS JSON FAILED PYDANTIC VALIDATION WITH ERROR:\n{last_err}\nFix this exact validation error immediately. Make sure every word in `expression` outside `[something]` appears contiguously in `full_sentence_citation`.'
                contents: List[Any] = [
                    f'LEARNER OPINION TO CHALLENGE AS DEVIL\'S ADVOCATE:\n{clean_user_text}\n\nReturn valid JSON matching LoungeTurnResponse.{prompt_suffix}'
                ]
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.75 if tries_done == 1 else 0.55,
                            response_mime_type='application/json',
                            max_output_tokens=4096,
                        ),
                    )
                    raw_text = (getattr(resp, 'text', '') or '').strip()
                    if raw_text.startswith('```'):
                        raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text, flags=re.DOTALL).strip()
                    payload_dict = _auto_clean_base_patterns(json.loads(raw_text))
                    validated: LoungeTurnResponse = LoungeTurnResponse.model_validate(
                        payload_dict,
                        context=trusted_context
                    )
                    logger.info(f'✅ Devil\'s Advocate LIVE Validation PASSED on try {tries_done} ({model_name})!')
                    return validated
                except Exception as e:
                    err_str = str(e)
                    last_err = err_str
                    logger.warning(f'Devil\'s Advocate live try {tries_done} ({model_name}) failed: {err_str[:220]}')
                    if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                        self._dead_key_models_until[(k_idx, model_name)] = time.time() + 65
                    elif '404' in err_str or 'NOT_FOUND' in err_str:
                        self._dead_models_until[model_name] = time.time() + 86400
                if tries_done >= 6:
                    break
            if tries_done >= 6:
                break
        raise RuntimeError(f'Devil\'s Advocate live generation failed after {tries_done} tries: {last_err}')

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

    # === CLAUDE V2 METHODS INSIDE NYCLoungeAI ===
    def _call_gemini_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tries: int = 6, validator_fn=None):
        last_err = None
        now = time.time()
        dead_keys = getattr(self, "_dead_keys_until", {})
        if not hasattr(self, "_dead_key_models_until"):
            self._dead_key_models_until = {}
        healthy_indices = [i for i in range(len(self.keys_pool)) if dead_keys.get(i, 0) < now]
        if not healthy_indices and self.keys_pool:
            healthy_indices = list(range(len(self.keys_pool)))

        tries_done = 0
        for k_idx in healthy_indices[:3]:
            key = self.keys_pool[k_idx]
            client = genai.Client(api_key=key, http_options={"timeout": 30000})
            models_to_try = self._get_active_models(client, k_idx=k_idx)
            for model_name in models_to_try:
                if self._dead_models_until.get(model_name, 0) >= time.time():
                    continue
                if self._dead_key_models_until.get((k_idx, model_name), 0) >= time.time():
                    continue
                tries_done += 1
                err_feedback = f"\n\nPREVIOUS JSON FAILED VALIDATION:\n{last_err}\nFix this exact issue immediately." if last_err else ""
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=[user_prompt + err_feedback],
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=temperature if tries_done == 1 else max(0.15, temperature - 0.2),
                            response_mime_type="application/json",
                            max_output_tokens=4096,
                        ),
                    )
                    raw_text = (getattr(resp, "text", "") or "").strip()
                    if raw_text.startswith("```"):
                        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
                    data = json.loads(raw_text)
                    return validator_fn(data) if validator_fn else data
                except Exception as e:
                    err_str = str(e)
                    last_err = err_str
                    logger.warning(f"ClaudeV2 call try {tries_done} ({model_name}) failed: {err_str[:200]}")
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        self._dead_key_models_until[(k_idx, model_name)] = time.time() + 65
                    elif "404" in err_str or "NOT_FOUND" in err_str:
                        self._dead_models_until[model_name] = time.time() + 86400
                if tries_done >= max_tries:
                    break
            if tries_done >= max_tries:
                break
        raise RuntimeError(f"ClaudeV2 call failed after {tries_done} tries: {last_err}")

    def generate_dialogue(self, user: Dict[str, Any], user_text: str, mode: str = "banter", history_lines: Optional[List[str]] = None) -> Any:
        from validation_layer import DialogueOnlyResponse
        pref_reg = (user.get("preferred_register") if isinstance(user, dict) else None) or "witty"
        vibe_badge = REGISTERS_MAP.get(pref_reg, "🎭 Witty")
        vibe_desc = REGISTER_DESCRIPTIONS.get(pref_reg, "")

        mode_contract = (
            "MODE CONTRACT (DEVIL'S ADVOCATE): Pick ONE speaker who best fits the topic to challenge the learner's premise (concede one valid point, deliver one sharp counter-argument, end with one pointed question), plus one brief 1-sentence reaction from a second speaker."
            if mode == "devils_advocate"
            else "MODE CONTRACT (LOUNGE BANTER): Natural NYC lounge conversation among peers reacting to the learner and to each other."
        )
        hist_block = "\n".join(history_lines[-8:]) if history_lines else "(Start of conversation)"
        sys_prompt = "\n\n".join([
            CLAUDE_BEHAVIORAL_PERSONAS,
            mode_contract,
            f"SPOKEN VIBE SETTING: {vibe_badge} ({vibe_desc})",
            'Return ONLY valid JSON: {"turns": [{"speaker": "Marcus"|"Chloe"|"Jake", "text": "..."}]}',
        ])
        u_prompt = f"RECENT DIALOGUE CONTEXT:\n{hist_block}\n\nLEARNER MESSAGE:\n{user_text}"
        return self._call_gemini_json(
            system_prompt=sys_prompt,
            user_prompt=u_prompt,
            temperature=0.8,
            max_tries=5,
            validator_fn=lambda d: DialogueOnlyResponse.model_validate(d),
        )

    def coach_turn(self, user: Dict[str, Any], user_text: str, turns: List[Any]) -> Optional[Any]:
        from validation_layer import CoachResponse
        uid = int(user.get("user_id", 0)) if isinstance(user, dict) else 0
        recent_chunks = self.db.get_recent_shown_chunks(uid, limit=40) if (self.db and uid) else []
        turns_json = json.dumps([{"turn": i, "speaker": t.speaker, "text": t.text} for i, t in enumerate(turns)], ensure_ascii=False)
        u_prompt = (
            f"LEARNER MESSAGE (judge mirror ONLY on this text):\n{user_text}\n\n"
            f"GENERATED HOST TURNS (extract 1-2 spoken chunks verbatim from here):\n{turns_json}\n\n"
            f"RECENTLY_SHOWN_CHUNKS (avoid repeating):\n{json.dumps(recent_chunks[:30])}"
        )
        try:
            coach: CoachResponse = self._call_gemini_json(
                system_prompt=CLAUDE_COACH_SYSTEM_PROMPT,
                user_prompt=u_prompt,
                temperature=0.15,
                max_tries=3,
                validator_fn=lambda d: CoachResponse.model_validate(d).validate_and_guard(
                    turns=turns, learner_text=user_text, recent_chunks=recent_chunks
                ),
            )
            if self.db and uid:
                self.db.log_mirror_event(uid, coach.mirror.verdict, coach.mirror.vetoed, coach.mirror.veto_reason)
                self.db.log_shown_chunks(uid, [c.base_pattern for c in coach.chunks])
            return coach
        except Exception as e:
            logger.warning(f"Coach Call B skipped after retries (dialogue unaffected): {e}")
            return None

    def read_the_room_v2(self, user: Dict[str, Any], user_text: str) -> Any:
        from validation_layer import ReadTheRoom
        uid = int(user.get("user_id", 0)) if isinstance(user, dict) else 0
        rtr: ReadTheRoom = self._call_gemini_json(
            system_prompt=CLAUDE_READ_THE_ROOM_PROMPT,
            user_prompt=f"LEARNER INPUT (QUOTE OR SOCIAL SCENARIO):\n{user_text}",
            temperature=0.55,
            max_tries=5,
            validator_fn=lambda d: ReadTheRoom.model_validate(d),
        )
        if self.db and uid:
            scen = {
                "input_text": user_text,
                "surface_point": rtr.surface_point,
                "readings": [r.model_dump() for r in rtr.readings],
                "your_turn": rtr.your_turn.model_dump(),
            }
            self.db.set_pending_practice(uid, "read_the_room", scen, ttl_seconds=1800)
            self.db.log_shown_chunks(uid, [c.base_pattern for c in rtr.chunks])
        return rtr

    def practice_reply_v2(self, user: Dict[str, Any], learner_reply: str, pending_state: Dict[str, Any]) -> Any:
        from validation_layer import PracticeEval
        uid = int(user.get("user_id", 0)) if isinstance(user, dict) else 0
        scen = pending_state.get("scenario", {})
        yt = scen.get("your_turn", {})
        readings = scen.get("readings", [])
        h_idx = yt.get("hidden_reading", 0)
        hidden_meaning = readings[h_idx] if 0 <= h_idx < len(readings) else {}

        u_prompt = (
            f"ORIGINAL SITUATION / QUOTE: {scen.get('input_text', '')}\n"
            f"COUNTERPART ({yt.get('counterpart', 'Chloe')}) SAID: \"{yt.get('their_line', '')}\"\n"
            f"COUNTERPART'S TRUE HIDDEN INTENT (hidden_reading={h_idx}): {json.dumps(hidden_meaning, ensure_ascii=False)}\n"
            f"COMMUNICATIVE GOAL FOR LEARNER: {yt.get('goal', '')}\n"
            f"LEARNER'S SPOKEN REPLY: \"{learner_reply}\""
        )
        pe: PracticeEval = self._call_gemini_json(
            system_prompt=CLAUDE_PRACTICE_EVAL_PROMPT,
            user_prompt=u_prompt,
            temperature=0.35,
            max_tries=4,
            validator_fn=lambda d: PracticeEval.model_validate(d).validate_with_learner_reply(learner_reply),
        )
        if self.db and uid:
            if pe.intent == "off_topic" or pe.done:
                self.db.clear_pending_practice(uid)
            else:
                rounds = self.db.increment_practice_round(uid)
                if rounds >= 2:
                    self.db.clear_pending_practice(uid)
        return pe
