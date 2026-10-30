import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional
import requests
from config import (
    GEMINI_API_KEY,
    GEMINI_FLASH_TIER_CHAIN,
    GEMINI_KEYS_LIST,
    GEMINI_MODEL,
    GEMINI_PRO_TIER_CHAIN,
)
from policy_engine import (
    DialogueController,
    validate_analyst_output_against_turn,
    validate_responder_output,
)
from schemas import (
    Analysis,
    DialogueDecision,
    DirectnessLevel,
    DynamicDilemmaScene,
    DynamicWorkoutScenario,
    ListenDecodeChallenge,
    PersonaCard,
    RegisterProfile,
    RegisterShiftResult,
    ResponderOutput,
    SceneBible,
    SceneDebriefResult,
    TrainingMode,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_partial_if_exists(rel_path: str) -> str:
    p = _PROMPTS_DIR / rel_path
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


_REGISTER_PROFILES_MD = _load_partial_if_exists("_partials/register_profiles.md")
_ARABIC_L1_REF_MD = _load_partial_if_exists("_partials/arabic_l1_reference.md")

RESPONDER_SYSTEM_PROMPT = f"""You are the live conversational counterpart in a spoken American English coaching lab (`v2_complete`).

## Non-Negotiable Principles
1. **Be a real interlocutor first.** React to what the learner actually meant — their substance, humor, hesitation, or pushback — before anything else.
2. **NO 'ME-TOO' MIRRORING OR FABRICATED MEMORIES:** In `real_conversation` mode, NEVER claim that the exact same thing happened to you ("I talked with my friend yesterday too!", "I went hiking there too!"). Instead, react directly to *their* experience or plan ("Glad you two finally got it sorted out — it's always a relief when a plan comes together.").
3. **Stay inside the scene.** Never invent urgent work crises, backstories, or off-screen emergencies unless the Scene Bible explicitly defines them.
4. **Never sound like a teacher, therapist, or customer-support script.** No meta-praise ("Great job!", "Good point!"), no lecturing, no stacked idioms.
5. **Obey the Director's Turn Brief (`dialogue_decision`) strictly:**
   - Match the assigned `act` and `style`.
   - Keep total word count within `max_words`.
   - **Question Rule:** If `allow_question` is `false`, do NOT end with `?` or ask any question.
   - **Anti-Repetition (`avoid_openers`):** Do NOT start your reply with any word listed in `avoid_openers`.
   - **Implicit Recast:** If `advisory_recast_candidate` is provided, weave it naturally into your reaction ONLY if it fits organically without faking a parallel 'me-too' story.

{_REGISTER_PROFILES_MD}

Return ONLY valid JSON matching this exact structure:
{{
  "reply_text": "Your natural in-character spoken reply (1-2 sentences)",
  "second_bubble_text": null,
  "recast_used": false,
  "elicitation_used": false
}}"""

ANALYST_SYSTEM_PROMPT = f"""You are a silent linguistic and pragmatic diagnostician (`v2_complete`) for an L1-Arabic learner of Spoken American English. You output structured JSON consumed by the deterministic Policy Engine.

## Core Principles
1. **Judge communicative effectiveness first, form second.** Did the learner achieve their communicative intent in the current scene and register? Did they protect their boundary, or did they cave in (`pragmatic_cave_in: true`)?
2. **Order diagnostics by communicative impact:** If the learner's message contains multiple issues (e.g., `did an effort`, `discuss about`, `took a decision`), include up to 3 distinct diagnostics in `diagnostics`, ordered from highest impact to lowest.
3. **Arabic-L1 Transfer Reference (`l1_pattern` & `l1_source_ar`):**
   Populate `l1_pattern` and `l1_source_ar` whenever span-level evidence matches an Arabic transfer pattern:
   - `COLLOC_LIGHT_VERB`: e.g. "did an effort", "took a decision", "make a party", "do a mistake" (أصلها: «بذل جهدًا»، «اتّخذ قرارًا»، «أقام حفلة»، «ارتكب خطأ») -> `made an effort`, `made a decision` / `made the call`, `threw a party`, `made a mistake`.
   - `COLLOC_PREP_VERB`: e.g. "discuss about", "explain me", "reply my message", "reach to" (أصلها: «ناقش في / حول»، «شرح لي»، «ردّ على»، «وصل إلى») -> `discuss [something]` / `talk [something] over`, `explain to me`, `reply to my message`, `reach`.
   - `COLLOC_OPEN_CLOSE`: e.g. "open/close the light, TV, AC" (أصلها: «افتح / أغلق النور») -> `turn on / turn off`.
   - `GRAM_RESUMPTIVE_PRONOUN`: e.g. "the plan that we talked about it" («الخطة التي تحدثنا عنها»).
   - `GRAM_DEF_ARTICLE_ABSTRACT`: e.g. "the life is hard" (أل التعريف مع المعنويات العامة).
   - `DISC_WRITTEN_CONNECTORS`: "moreover", "furthermore", "therefore" in casual speech instead of `on top of that`, `plus`, `so`.
   - `PRAG_DIRECT_IMPERATIVE_REQUEST`: bare imperative "Send me the file" instead of `Could you send over...`.
   - `PRAG_LITERAL_REFUSAL`: blunt "No, I can't" without a face-saving softener.
4. **Listener Effect (`listener_effect`):**
   REQUIRED for every diagnostic: In 1 crisp sentence, describe how a native American listener perceives `original_span` (e.g., "Clear in meaning, but 'did an effort' sounds like a direct word-for-word translation.").
5. **Rules for the Arabic Coaching Note (`explanation_ar` / `ar_note`):**
   - Form: Modern Standard Arabic only, plain light style, at most 200 characters (1-2 short sentences).
   - Keep English target phrases in Latin script WITHOUT quotation marks (e.g., make an effort, talk it over), and quote Arabic source phrases with guillemets « » (e.g., «بذل جهدًا»، «اتّخذ قرارًا»).
   - Tone: Neutral and respectful for an educated adult. NEVER use: خطأ، غلط، لا يقال أبدًا، العرب يخطئون، تفكير عربي، ترجمة حرفية سيئة، ضعيف، ركيك.
   - Prefer: «في العربية نقول «...»، أما الإنجليزية فتفضّل ... وفي الحديث اليومي ...».
6. **Contrasting Nuance in `secondary_alternatives`:**
   - Make `pragmatic_effect` show how the secondary option differs in register, warmth, or directness (e.g., "Relaxed everyday phrasing with a friend — focuses on talking things through").

{_ARABIC_L1_REF_MD}

Return ONLY valid JSON matching this EXACT structure:
{{
  "communicative_intention": "share_comment",
  "learner_invites_question": false,
  "communicative_goal_achieved": true,
  "pragmatic_cave_in": false,
  "requires_clarification": false,
  "in_emotional_or_story_flow": false,
  "diagnostics": [
    {{
      "diagnostic_id": "d1",
      "original_span": "exact substring from learner_message",
      "normalized_pattern_id": "COLLOC_LIGHT_VERB:make_effort",
      "relevant_context_family": "global",
      "intended_meaning": "tried hard / put effort into",
      "kind": "collocation",
      "severity": "medium",
      "alternative": "made an effort",
      "secondary_alternatives": [
        {{
          "phrase": "went out of my way",
          "register_profile": "casual",
          "directness": "balanced",
          "pragmatic_effect": "Warm and idiomatic when emphasizing extra effort for a friend"
        }}
      ],
      "alternative_status": "required",
      "recast_candidate": "made a real effort",
      "listener_effect": "Meaning is clear, but 'did an effort' sounds translated rather than idiomatic.",
      "explanation_en": "'Effort' pairs with 'make' or 'put in', whereas 'do' doesn't collocate with 'effort'.",
      "explanation_ar": "في العربية نقول «بذل جهدًا»، أما الإنجليزية فتجمع effort مع الفعل make فنقول make an effort أو put in the effort.",
      "l1_pattern": "COLLOC_LIGHT_VERB",
      "l1_source_ar": "بذل جهدًا / عمل مجهود",
      "l1_transfer_likely": true,
      "l1_transfer_evidence": "Light-verb calque from Arabic عمل مجهود / بذل جهدًا",
      "teachable_now": true,
      "preserves_intended_meaning": true,
      "context_fit_verified": true,
      "confidence_band": "high",
      "confidence_score": 0.94
    }}
  ],
  "target_assessments": []
}}"""


def _deterministic_responder_repair(
    raw_dict: dict[str, Any],
    decision: DialogueDecision,
    advisory_recast_candidate: Optional[str],
    stealth_target_form: Optional[str],
) -> ResponderOutput:
    txt = str(raw_dict.get("reply_text") or "").strip()
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]
    if not decision.allow_question:
        non_q = [s for s in sents if not s.endswith("?")]
        if non_q:
            sents = non_q
        elif sents:
            sents = [sents[0].rstrip("?") + "."]
    elif len([s for s in sents if s.endswith("?")]) > 1:
        kept = []
        q_seen = False
        for s in sents:
            if s.endswith("?"):
                if not q_seen:
                    kept.append(s)
                    q_seen = True
            else:
                kept.append(s)
        sents = kept

    sents = sents[: decision.max_sentences]
    repaired_txt = " ".join(sents).strip()
    words = repaired_txt.split()
    if len(words) > decision.max_words:
        repaired_txt = " ".join(words[: decision.max_words - 1]).rstrip(".,;:") + "."

    candidate_out = ResponderOutput(
        reply_text=repaired_txt,
        second_bubble_text=None,
        recast_used=bool(raw_dict.get("recast_used", False)),
        elicitation_used=bool(raw_dict.get("elicitation_used", False)),
    )
    return validate_responder_output(
        output=candidate_out,
        decision=decision,
        advisory_recast_candidate=advisory_recast_candidate,
        stealth_target_form=stealth_target_form,
    )


class DualLLMEngine:
    def __init__(self, keys: Optional[list[str]] = None, primary_model: str = GEMINI_MODEL):
        self.keys = list(keys) if keys is not None else (list(GEMINI_KEYS_LIST) if GEMINI_KEYS_LIST else ([GEMINI_API_KEY] if GEMINI_API_KEY else []))
        self.key_idx = 0
        self.primary_model = primary_model
        self.model_tier: str = "flash"
        self.http = requests.Session()
        self.available_models_cache: set[str] = set()
        self.dead_models_cache: set[str] = set()
        self._discover_available_models()

    def _discover_available_models(self) -> None:
        if not self.keys or "DummyKey" in self.keys[0]:
            return
        for k in self.keys[:2]:
            try:
                r = self.http.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={k}",
                    timeout=8,
                )
                if r.status_code == 200:
                    items = r.json().get("models", [])
                    for it in items:
                        name = str(it.get("name", "")).replace("models/", "")
                        methods = it.get("supportedGenerationMethods", [])
                        if "generateContent" in methods and name:
                            self.available_models_cache.add(name)
                    if self.available_models_cache:
                        logger.info(f"Discovered {len(self.available_models_cache)} supported Gemini models.")
                        return
            except Exception as e:
                logger.warning(f"Model discovery notice: {e}")

    def get_active_model_chain(self, prefer_pro: bool = False) -> list[str]:
        base_chain = GEMINI_PRO_TIER_CHAIN if (prefer_pro or self.model_tier == "pro") else GEMINI_FLASH_TIER_CHAIN
        filtered = [
            m
            for m in base_chain
            if m not in self.dead_models_cache
            and (not self.available_models_cache or m in self.available_models_cache)
        ]
        if not filtered:
            filtered = [m for m in base_chain if m not in self.dead_models_cache] or ["gemini-2.5-flash"]
        return filtered

    def set_model_tier(self, tier: str) -> list[str]:
        if tier in ("flash", "pro"):
            self.model_tier = tier
        return self.get_active_model_chain(prefer_pro=(self.model_tier == "pro"))

    def _call_gemini_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        validator_fn: Callable[[dict[str, Any]], Any],
        temperature: float,
        max_tries: int = 2,
        prefer_pro: bool = False,
    ) -> Any:
        if not self.keys:
            raise RuntimeError("No Gemini API keys configured in GEMINI_KEYS or GEMINI_API_KEY.")

        models = self.get_active_model_chain(prefer_pro=prefer_pro)
        last_err = None

        for m_name in models:
            model_not_found = False
            for k_offset in range(len(self.keys)):
                api_key = self.keys[(self.key_idx + k_offset) % len(self.keys)]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"

                for attempt in range(max_tries):
                    prompt_body = user_prompt
                    if last_err and attempt > 0:
                        prompt_body += (
                            f"\n\n[VALIDATION REPAIR NOTICE: Your previous JSON failed validation ({last_err}). "
                            "Fix the exact field violation and return valid JSON.]"
                        )
                    gen_cfg: dict[str, Any] = {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                    }
                    if "flash" in m_name and "pro" not in m_name:
                        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}

                    payload = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": prompt_body}]}],
                        "generationConfig": gen_cfg,
                    }
                    try:
                        resp = self.http.post(url, json=payload, timeout=12)
                        if resp.status_code == 404:
                            self.dead_models_cache.add(m_name)
                            model_not_found = True
                            last_err = f"Model {m_name} HTTP 404"
                            break
                        if resp.status_code in (401, 403, 429):
                            last_err = f"Key HTTP {resp.status_code} on {m_name}"
                            self.key_idx = (self.key_idx + k_offset + 1) % len(self.keys)
                            break
                        if resp.status_code == 400 and "thinkingConfig" in resp.text:
                            payload["generationConfig"].pop("thinkingConfig", None)
                            resp = self.http.post(url, json=payload, timeout=12)

                        resp.raise_for_status()
                        data = resp.json()
                        raw_txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        raw_txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_txt, flags=re.IGNORECASE).strip()
                        parsed = json.loads(raw_txt)
                        validated = validator_fn(parsed)
                        self.key_idx = (self.key_idx + k_offset) % len(self.keys)
                        return validated
                    except Exception as e:
                        last_err = str(e)
                        logger.warning(f"Gemini validation/call ({m_name}, attempt {attempt + 1}) notice: {e}")

                if not model_not_found and last_err and not last_err.startswith("Key HTTP"):
                    break
            if model_not_found:
                continue

        raise RuntimeError(f"All Gemini models/keys failed validation: {last_err}")

    def run_analyst(
        self,
        *,
        learner_text: str,
        recent_history: list[dict[str, str]],
        register_profile: RegisterProfile,
        directness: DirectnessLevel,
        training_mode: TrainingMode,
        scene_bible: SceneBible,
        revealed_target_ids: set[str],
        directly_cued_target_ids: set[str],
        catalog_targets: list[dict[str, Any]],
    ) -> Analysis:
        words = learner_text.strip().split()
        if len(words) <= 3 and all(w.lower().strip(".,!?") in {"hey", "hi", "hello", "yo", "sup", "morning", "evening", "alex", "guys", "there"} for w in words):
            return Analysis(
                communicative_intention="greeting",
                learner_invites_question=False,
                communicative_goal_achieved=True,
                pragmatic_cave_in=False,
                requires_clarification=False,
                in_emotional_or_story_flow=False,
                diagnostics=[],
                target_assessments=[],
            )

        user_prompt = json.dumps(
            {
                "scene_mode": scene_bible.scene_mode.value,
                "training_mode": training_mode.value,
                "register_profile": register_profile.value,
                "directness": directness.value,
                "relationship": scene_bible.relationship,
                "counterpart_goal": scene_bible.counterpart_goal,
                "recent_history": recent_history[-6:],
                "active_catalog_targets": catalog_targets,
                "learner_message": learner_text,
            },
            ensure_ascii=False,
            indent=2,
        )

        def _validate_analyst(raw_dict: dict[str, Any]) -> Analysis:
            parsed = Analysis.model_validate(raw_dict)
            return validate_analyst_output_against_turn(
                analysis=parsed,
                learner_text=learner_text,
                revealed_target_ids=revealed_target_ids,
                directly_cued_target_ids=directly_cued_target_ids,
                active_training_mode=training_mode,
            )

        try:
            return self._call_gemini_json(
                system_prompt=ANALYST_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                validator_fn=_validate_analyst,
                temperature=0.2,
                max_tries=2,
            )
        except Exception as err:
            logger.error(f"Analyst fallback to empty safe Analysis due to: {err}")
            return Analysis(communicative_intention="share_comment")

    def run_responder(
        self,
        *,
        learner_text: str,
        recent_history: list[dict[str, str]],
        persona: PersonaCard,
        scene_bible: SceneBible,
        register_profile: RegisterProfile,
        directness: DirectnessLevel,
        training_mode: TrainingMode,
        dialogue_decision: DialogueDecision,
        advisory_recast_candidate: Optional[str] = None,
        stealth_target_info: Optional[dict[str, str]] = None,
    ) -> ResponderOutput:
        stealth_form = stealth_target_info.get("canonical_form") if stealth_target_info else None
        user_prompt = json.dumps(
            {
                "persona_card": persona.model_dump(),
                "scene_bible": scene_bible.model_dump(),
                "training_mode": training_mode.value,
                "english_variety": "general_american",
                "register": register_profile.value,
                "directness": directness.value,
                "dialogue_decision": dialogue_decision.model_dump(),
                "advisory_recast_candidate": advisory_recast_candidate,
                "target_opportunity": (
                    {
                        "communicative_intention": stealth_target_info["communicative_intention"],
                        "elicitation_mode": stealth_target_info.get("mode", "organic"),
                    }
                    if stealth_target_info
                    else None
                ),
                "recent_history": recent_history[-6:],
                "learner_message": learner_text,
            },
            ensure_ascii=False,
            indent=2,
        )

        last_raw_dict: dict[str, Any] = {}

        def _validate_resp(raw_dict: dict[str, Any]) -> ResponderOutput:
            nonlocal last_raw_dict
            last_raw_dict = raw_dict
            out = ResponderOutput.model_validate(raw_dict)
            return validate_responder_output(
                output=out,
                decision=dialogue_decision,
                advisory_recast_candidate=advisory_recast_candidate,
                stealth_target_form=stealth_form,
            )

        try:
            return self._call_gemini_json(
                system_prompt=RESPONDER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                validator_fn=_validate_resp,
                temperature=0.7,
                max_tries=2,
            )
        except Exception as err:
            if last_raw_dict:
                logger.warning(f"Applying deterministic responder repair after validation error: {err}")
                return _deterministic_responder_repair(
                    last_raw_dict,
                    dialogue_decision,
                    advisory_recast_candidate,
                    stealth_target_form,
                )
            raise

    def generate_register_shift_coaching(self, learner_text: str) -> RegisterShiftResult:
        sys_p = (
            "You are an expert Spoken American English Register Coach (`v2_complete`) for L1 Arabic learners.\n"
            "Take the learner's communicative intention or sentence and express it naturally across 3 distinct registers.\n"
            "Follow the `ar_note` protocol for `key_pragmatic_distinction_ar`: Modern Standard Arabic, <=200 chars, quote Arabic phrases with « », keep English phrases in Latin script without quotes.\n"
            "Return ONLY valid JSON with these exact keys:\n"
            "{\n"
            '  "communicative_intention": "short description of intention",\n'
            '  "casual_friend_version": "warm, relaxed everyday spoken phrasing",\n'
            '  "diplomatic_colleague_version": "tactful, collaborative softened phrasing",\n'
            '  "executive_manager_version": "crisp, bottom-line first confident phrasing",\n'
            '  "key_pragmatic_distinction_en": "concise explanation of how tone shifts across the 3 versions",\n'
            '  "key_pragmatic_distinction_ar": "شرح عربي موجز ومحايد للفرق التداولي بين المستويات الثلاثة",\n'
            '  "practice_prompt": "1-sentence situational prompt inviting the learner to try one version"\n'
            "}"
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Learner's input to shift across registers: {learner_text}",
            validator_fn=RegisterShiftResult.model_validate,
            temperature=0.4,
            max_tries=2,
        )

    def generate_dynamic_dilemma_scene(self, register_profile: str = "diplomatic") -> DynamicDilemmaScene:
        sys_p = (
            "You design realistic, unscripted Spoken American English pragmatic simulations (`prompts/modes/pragmatic_dilemma.md`).\n"
            "Generate a fresh, relatable real-life situation where the learner faces two competing social/pragmatic constraints.\n"
            "Include a `counterpart_pushback_plan` so Alex pushes back gently once before accepting a compromise.\n"
            "Return ONLY valid JSON matching this exact structure:\n"
            "{\n"
            '  "setting": "Brief setting description",\n'
            '  "relationship": "e.g., close friend, neighbor, peer colleague, client",\n'
            '  "register_profile": "casual" | "warm" | "diplomatic" | "executive",\n'
            '  "established_facts": ["fact 1", "fact 2"],\n'
            '  "counterpart_goal": "What Alex wants in this interaction",\n'
            '  "counterpart_pushback_plan": "How Alex pushes back once before compromising",\n'
            '  "learner_pragmatic_goal": "What the learner needs to achieve while keeping rapport",\n'
            '  "opening_line": "Natural spoken line from Alex starting the interaction"\n'
            "}"
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Preferred starting register profile: {register_profile}. Generate a fresh, spontaneous pragmatic dilemma scene.",
            validator_fn=DynamicDilemmaScene.model_validate,
            temperature=0.8,
            max_tries=2,
        )

    def generate_dynamic_workout_scenario(self, target_info: dict[str, str], register_profile: str = "casual") -> DynamicWorkoutScenario:
        forbidden_form = target_info.get("canonical_form", "")
        intention = target_info.get("communicative_intention", "express your point naturally")
        sys_p = (
            "You create short, realistic situational prompts for unaided spoken English retrieval (`prompts/modes/phrase_workout.md`).\n"
            f"Target communicative move: '{intention}'.\n"
            f"CRITICAL RULE: NEVER include or reveal the phrase '{forbidden_form}' or its key lexical words in `situation_setup` or `prompt_question`!\n"
            "Return ONLY valid JSON with these exact keys:\n"
            "{\n"
            f'  "communicative_intention": "{intention}",\n'
            '  "situation_setup": "1-2 sentence everyday scenario where the speaker needs to perform this move",\n'
            '  "prompt_question": "What do you say naturally in this moment?"\n'
            "}"
        )

        def _validate_workout(raw_d: dict[str, Any]) -> DynamicWorkoutScenario:
            out = DynamicWorkoutScenario.model_validate(raw_d)
            stem = re.sub(r"\[[^\]]+\]", "", forbidden_form).strip().lower()
            if len(stem) >= 5 and stem in (out.situation_setup + " " + out.prompt_question).lower():
                raise ValueError("Workout prompt leaked the target phrase.")
            return out

        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Register context: {register_profile}. Create a spontaneous scenario for communicative move: {intention}",
            validator_fn=_validate_workout,
            temperature=0.75,
            max_tries=2,
        )

    def generate_listen_decode_challenge(self, register_profile: str = "casual") -> ListenDecodeChallenge:
        sys_p = (
            "You run a Listen & Decode exercise (`prompts/modes/listen_decode.md`) for an L1-Arabic learner of Spoken American English.\n"
            "Generate a realistic 1-2 sentence spoken American clip containing natural connected-speech reductions "
            "(e.g., 'shoulda', 'mighta', 'coulda', 'whaddaya', 'didja', 'wouldja', 'lemme', 'kinda', 'sorta', 'gonna') "
            "AND subtle pragmatic subtext (e.g., a polite brush-off, dry understatement, or hedged refusal).\n"
            "Return ONLY valid JSON matching this exact structure:\n"
            "{\n"
            '  "context_line": "1-line context (who is speaking to whom and where)",\n'
            '  "spoken_clip_text": "The natural fast-spoken line with realistic spoken reductions",\n'
            '  "full_unreduced_form": "The exact grammatical words unpacked",\n'
            '  "acoustic_reduction_note": "Brief explanation of how the sounds link/reduce in fast speech",\n'
            '  "pragmatic_subtext_en": "What the speaker REALLY means underneath the polite/indirect words",\n'
            '  "pragmatic_subtext_ar": "شرح عربي موجز للمعنى المبطن تحت السطح",\n'
            '  "decode_question": "Ask the learner what the speaker is really signaling and how they would reply"\n'
            "}"
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Register: {register_profile}. Generate a fresh Listen & Decode challenge.",
            validator_fn=ListenDecodeChallenge.model_validate,
            temperature=0.8,
            max_tries=2,
        )

    def generate_scene_debrief(self, recent_history: list[dict[str, str]], recent_diagnostics: list[dict[str, Any]]) -> SceneDebriefResult:
        sys_p = (
            "You write a concise, high-signal end-of-scene debrief (`prompts/core/debrief_writer_system.md`) for an L1-Arabic learner.\n"
            "Rules:\n"
            "1. Lead with genuine spontaneous wins from the learner's turns (no generic cheerleading).\n"
            "2. Cap `top_shifts` at 3 concrete spoken upgrades.\n"
            "Return ONLY valid JSON matching this exact structure:\n"
            "{\n"
            '  "scene_outcome": "1-2 sentences on how the conversation landed",\n'
            '  "spontaneous_wins": ["Strong phrase or move the learner used naturally"],\n'
            '  "top_shifts": [\n'
            '    {\n'
            '      "you_said": "exact learner phrase",\n'
            '      "natural_alternative": "spoken American upgrade",\n'
            '      "why_it_matters": "1-line pragmatic/idiomatic reason",\n'
            '      "arabic_note": "ملاحظة عربية موجزة ومحايدة"\n'
            '    }\n'
            '  ],\n'
            '  "closing_note": "1 calm, forward-looking sentence"\n'
            "}"
        )
        user_p = json.dumps(
            {"dialogue_history": recent_history[-10:], "recorded_diagnostics": recent_diagnostics[-5:]},
            ensure_ascii=False,
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=user_p,
            validator_fn=SceneDebriefResult.model_validate,
            temperature=0.3,
            max_tries=2,
        )
