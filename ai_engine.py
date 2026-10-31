import json
import logging
import re
import threading
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
    normalize_arabic_english_formatting,
    register_workout_task,
    validate_analyst_output_against_turn,
    validate_responder_output,
)
from schemas import (
    Analysis,
    DecodeEvaluationResult,
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

RESPONDER_SYSTEM_PROMPT = """You are Alex, a 33-year-old American peer in a spoken English lab.
Rules:
1. React directly to the learner's real topic. NEVER invent parallel 'me-too' personal stories ("I did that with my friend yesterday too!") or fake emergencies.
2. Never sound like a teacher ("Great job!", "Good point!"). Sound like a natural American friend/colleague.
3. Strictly obey `dialogue_decision`:
   - Keep word count <= `max_words` and sentences <= `max_sentences`.
   - If `allow_question` is false, do NOT ask any question or end with `?`.
   - Do NOT start your reply with any word in `avoid_openers`.
   - If `advisory_recast_candidate` is given, weave it naturally only if it fits organically.
4. If `active_workout_task` is provided: check whether `learner_message` accomplishes `active_workout_task.prompt_question` (accept any natural phrasing). If the learner missed or ignored the task goal, respond naturally to what they said AND re-open the situation in-character so they still have an organic reason to fulfill `prompt_question`—never scold, quiz, or leak `forbidden_form`.
Return ONLY valid JSON:
{"reply_text": "1-2 natural spoken sentences", "second_bubble_text": null, "recast_used": false, "elicitation_used": false}"""

ANALYST_SYSTEM_PROMPT = """You are a fast, silent linguistic & pragmatic diagnostician for an L1-Arabic speaker of Spoken American English.
Rules:
1. If `learner_message` is natural and effective, return `"diagnostics": []`.
   If `active_workout_task` is provided, evaluate linguistic quality and task completion independently: set `"communicative_goal_achieved": true` ONLY if `learner_message` fulfills `active_workout_task.prompt_question` (via any natural expression) or logically declines it in-scene; set `"communicative_goal_achieved": false` if the learner ignores or misses the task.
2. Otherwise return up to 3 diagnostics ordered by impact. `original_span` MUST be an exact substring of `learner_message`.
3. False-Alarm Calibration & Directness (Groups 4 & 6):
   - NEVER flag idiomatic, acceptable spoken American English just to sound "fancier" or "softer".
   - Direct is NOT rude, and softer is NOT always better: in `direct` or `executive` contexts, crisp directness is a strength; only flag pragmatics when a bare imperative or blunt refusal violates the active relationship/register or when the learner caves in (`pragmatic_cave_in: true`).
4. Arabic L1 codes (`l1_pattern` & `l1_source_ar`):
   - `COLLOC_LIGHT_VERB`: "did an effort", "took a decision", "do a mistake" («بذل جهدًا»، «اتّخذ قرارًا») -> `made an effort`, `made a decision`.
   - `COLLOC_PREP_VERB`: "discuss about", "explain me", "reply my message", "reach to" («ناقش في»، «شرح لي»، «ردّ على») -> `discuss`, `explain to me`, `reply to`.
   - `COLLOC_OPEN_CLOSE`: "open/close the AC, light, TV" («افتح/أغلق المكيف أو النور») -> `turn on / turn off`.
   - `GRAM_RESUMPTIVE_PRONOUN`: "the room that we are sitting in it" («الغرفة التي نجلس فيها») -> drop resumptive pronoun (`the room we're sitting in`).
   - `COLLOC_INTENSIFIER`: "too much hot" -> `way too hot` / `super hot`.
   - `GRAM_DEF_ARTICLE_ABSTRACT`, `DISC_WRITTEN_CONNECTORS`, `PRAG_DIRECT_IMPERATIVE_REQUEST`, `PRAG_LITERAL_REFUSAL`.
4. `listener_effect`: 1 crisp sentence on how `original_span` sounds to a native American ear.
5. `explanation_ar`: Modern Standard Arabic, <=180 chars, neutral respectful tone, quote Arabic phrases with « », keep English words in Latin script without quotes.

Return ONLY valid JSON matching this exact structure:
{
  "communicative_intention": "share_comment",
  "learner_invites_question": false,
  "communicative_goal_achieved": true,
  "pragmatic_cave_in": false,
  "requires_clarification": false,
  "in_emotional_or_story_flow": false,
  "diagnostics": [
    {
      "diagnostic_id": "d1",
      "original_span": "exact substring from learner_message",
      "normalized_pattern_id": "COLLOC_LIGHT_VERB:make_effort",
      "relevant_context_family": "global",
      "intended_meaning": "put effort into",
      "kind": "collocation",
      "severity": "medium",
      "alternative": "made an effort",
      "secondary_alternatives": [
        {
          "phrase": "went out of my way",
          "register_profile": "casual",
          "directness": "balanced",
          "pragmatic_effect": "Warm and idiomatic when emphasizing extra effort"
        }
      ],
      "alternative_status": "required",
      "recast_candidate": "made a real effort",
      "listener_effect": "Clear in meaning, but 'did an effort' sounds translated word-for-word.",
      "explanation_en": "In American English, 'effort' pairs with 'make' or 'put in', not 'do'.",
      "explanation_ar": "في العربية نقول «بذل جهدًا»، أما الإنجليزية فتجمع effort مع الفعل make فنقول make an effort.",
      "l1_pattern": "COLLOC_LIGHT_VERB",
      "l1_source_ar": "بذل جهدًا",
      "l1_transfer_likely": true,
      "l1_transfer_evidence": "Light-verb calque from Arabic بذل جهدًا",
      "teachable_now": true,
      "preserves_intended_meaning": true,
      "context_fit_verified": true,
      "confidence_band": "high",
      "confidence_score": 0.92
    }
  ],
  "target_assessments": []
}"""


def _fast_normalize_raw_analysis(raw_dict: dict[str, Any], learner_text: str) -> dict[str, Any]:
    """
    Zero-retry deterministic normalizer: repairs minor LLM field slips in <0.1ms
    while preserving valid diagnostics and target_assessments.
    """
    if not isinstance(raw_dict, dict):
        return {"communicative_intention": "share_comment", "diagnostics": [], "target_assessments": []}

    raw_diags = raw_dict.get("diagnostics") or []
    clean_diags = []
    for idx, d in enumerate(raw_diags[:3]):
        if not isinstance(d, dict):
            continue
        span = str(d.get("original_span") or "").strip()
        if not span or span.lower() not in learner_text.lower():
            continue
        m = re.search(re.escape(span), learner_text, flags=re.IGNORECASE)
        if m:
            d["original_span"] = m.group(0)

        d["diagnostic_id"] = str(d.get("diagnostic_id") or f"d{idx+1}")
        pat = str(d.get("normalized_pattern_id") or "").strip()
        if len(pat) < 3:
            pat = f"PAT_{re.sub(r'[^a-zA-Z0-9]+', '_', span.lower()).strip('_')[:24]}"
        d["normalized_pattern_id"] = pat
        d.setdefault("relevant_context_family", "global")

        kind = str(d.get("kind") or "collocation").lower()
        if kind not in {
            "grammar", "collocation", "idiomaticity", "register", "pragmatics",
            "discourse", "optional_upgrade", "acceptable_variation", "insufficient_context",
        }:
            kind = "collocation"
        d["kind"] = kind

        sev = str(d.get("severity") or "medium").lower()
        d["severity"] = sev if sev in {"low", "medium", "high"} else "medium"

        alt = str(d.get("alternative") or "").strip()
        if not alt:
            continue
        d["alternative"] = alt

        status = str(d.get("alternative_status") or "recommended").lower()
        if kind in {"optional_upgrade", "acceptable_variation", "insufficient_context"} and status == "required":
            status = "optional"
        if status not in {"required", "recommended", "optional"}:
            status = "recommended"
        d["alternative_status"] = status

        if not d.get("intended_meaning"):
            d["intended_meaning"] = f"Express '{alt}' naturally"

        d["preserves_intended_meaning"] = True
        d["context_fit_verified"] = True
        d["teachable_now"] = bool(d.get("teachable_now", True))

        try:
            score = float(d.get("confidence_score", 0.90))
        except Exception:
            score = 0.90
        score = max(0.0, min(1.0, score))
        if score >= 0.80:
            band = "high"
        elif score >= 0.65:
            band = "medium"
        else:
            score = 0.88
            band = "high"
        d["confidence_score"] = score
        d["confidence_band"] = band

        if d.get("l1_source_ar") or d.get("l1_pattern"):
            d["l1_transfer_likely"] = True
        if d.get("l1_transfer_likely") and not d.get("l1_transfer_evidence"):
            d["l1_transfer_evidence"] = str(d.get("l1_source_ar") or d.get("l1_pattern") or "Arabic L1 transfer")

        exp_en = str(d.get("explanation_en") or "").strip()
        if len(exp_en) < 5:
            exp_en = f"In spoken American English, '{alt}' is more natural here."
        d["explanation_en"] = exp_en[:235]

        if d.get("explanation_ar"):
            d["explanation_ar"] = normalize_arabic_english_formatting(str(d["explanation_ar"]).strip())[:195]
        if d.get("listener_effect"):
            d["listener_effect"] = str(d["listener_effect"]).strip()[:195]
        if d.get("l1_pattern"):
            d["l1_pattern"] = str(d["l1_pattern"]).strip()[:60]
        if d.get("l1_source_ar"):
            d["l1_source_ar"] = str(d["l1_source_ar"]).strip()[:75]

        sec_alts = []
        for sa in (d.get("secondary_alternatives") or [])[:2]:
            if isinstance(sa, dict) and sa.get("phrase"):
                reg_p = str(sa.get("register_profile") or "casual").lower()
                if reg_p not in {"casual", "warm", "witty", "diplomatic", "executive", "analytical"}:
                    reg_p = "casual"
                dir_p = str(sa.get("directness") or "balanced").lower()
                if dir_p not in {"soft", "balanced", "direct"}:
                    dir_p = "balanced"
                sec_alts.append({
                    "phrase": str(sa["phrase"]).strip(),
                    "register_profile": reg_p,
                    "directness": dir_p,
                    "pragmatic_effect": str(sa.get("pragmatic_effect") or "Natural spoken option")[:155],
                })
        d["secondary_alternatives"] = sec_alts
        clean_diags.append(d)

    raw_dict["diagnostics"] = clean_diags

    # Preserve valid target_assessments if present
    raw_tas = raw_dict.get("target_assessments") or []
    clean_tas = []
    for ta in raw_tas[:3]:
        if isinstance(ta, dict) and ta.get("target_id") and ta.get("matched_span"):
            m_span = str(ta["matched_span"]).strip()
            if m_span and m_span.lower() in learner_text.lower():
                clean_tas.append(ta)
    raw_dict["target_assessments"] = clean_tas

    raw_dict.setdefault("communicative_intention", "share_comment")
    raw_dict.setdefault("learner_invites_question", "?" in learner_text)
    raw_dict.setdefault("communicative_goal_achieved", True)
    raw_dict.setdefault("pragmatic_cave_in", False)
    raw_dict.setdefault("requires_clarification", False)
    raw_dict.setdefault("in_emotional_or_story_flow", False)
    return raw_dict


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
        reply_text=repaired_txt or "Got it, makes total sense.",
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




def _sanitize_discovered_chain(models: list[str]) -> list[str]:
    """Prioritizes stable Gemini models and pushes fragile lite/preview variants to last-resort."""
    preferred_order = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    clean = []
    for p in preferred_order:
        if p in models and p not in clean:
            clean.append(p)
    for m in models:
        if m not in clean and "lite" not in m.lower() and "tts" not in m.lower() and "image" not in m.lower():
            clean.append(m)
    for m in models:
        if m not in clean and "tts" not in m.lower() and "image" not in m.lower():
            clean.append(m)
    return clean or preferred_order[:3]


def _post_gemini_with_400_schema_fallback(url: str, payload: dict, timeout: int = 25) -> requests.Response:
    """If a model rejects strict responseSchema with HTTP 400, automatically retries with schema-free JSON mode."""
    initial_http_resp = requests.post(url, json=payload, timeout=timeout)
    if initial_http_resp.status_code == 400 and "generationConfig" in payload and "responseSchema" in payload["generationConfig"]:
        fallback_payload = dict(payload)
        gen_cfg = dict(payload["generationConfig"])
        schema_obj = gen_cfg.pop("responseSchema", None)
        fallback_payload["generationConfig"] = gen_cfg
        if schema_obj and "contents" in fallback_payload and fallback_payload["contents"]:
            import json as _json
            schema_hint = f"\n\n[CRITICAL: Return ONLY valid JSON matching this schema structure: {_json.dumps(schema_obj)}]"
            last_content = dict(fallback_payload["contents"][-1])
            parts = list(last_content.get("parts", []))
            if parts and "text" in parts[-1]:
                parts[-1] = {"text": parts[-1]["text"] + schema_hint}
                last_content["parts"] = parts
                fallback_payload["contents"] = list(fallback_payload["contents"][:-1]) + [last_content]
        return requests.post(url, json=fallback_payload, timeout=timeout)
    return initial_http_resp


class DualLLMEngine:
    def __init__(self, keys: Optional[list[str]] = None, primary_model: str = GEMINI_MODEL):
        self.keys = list(keys) if keys is not None else (list(GEMINI_KEYS_LIST) if GEMINI_KEYS_LIST else ([GEMINI_API_KEY] if GEMINI_API_KEY else []))
        self.key_idx = 0
        self.primary_model = primary_model
        self.model_tier: str = "flash"
        self._tls = threading.local()
        self._lock = threading.Lock()
        self.available_models_cache: set[str] = set()
        self.dead_models_cache: set[str] = set()
        self.dead_keys_cache: set[str] = set()
        self._discover_available_models()

    def _get_thread_session(self) -> requests.Session:
        sess = getattr(self._tls, "http_session", None)
        if sess is None:
            sess = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
            sess.mount("https://", adapter)
            self._tls.http_session = sess
        return sess

    def _get_live_keys(self) -> list[str]:
        live = [k for k in self.keys if k not in self.dead_keys_cache]
        return live if live else list(self.keys)

    def _discover_available_models(self) -> None:
        if not self.keys or "DummyKey" in self.keys[0]:
            return
        http = self._get_thread_session()
        for k in self.keys:
            try:
                r = http.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={k}",
                    timeout=5,
                )
                if r.status_code == 200:
                    for it in r.json().get("models", []):
                        name = str(it.get("name", "")).replace("models/", "")
                        if "generateContent" in it.get("supportedGenerationMethods", []) and name:
                            self.available_models_cache.add(name)
                    if self.available_models_cache:
                        return
                elif r.status_code in (400, 401, 403):
                    self.dead_keys_cache.add(k)
            except Exception:
                pass

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
        max_tries: int = 1,
        prefer_pro: bool = False,
    ) -> Any:
        if not self.keys:
            raise RuntimeError("No Gemini API keys configured.")

        http = self._get_thread_session()
        models = self.get_active_model_chain(prefer_pro=prefer_pro)
        live_keys = self._get_live_keys()
        key_lane = 1 if 'diagnostician' in system_prompt else 0
        last_err = None

        for m_name in models:
            model_not_found = False
            for k_offset in range(len(live_keys)):
                api_key = live_keys[(self.key_idx + key_lane + k_offset) % len(live_keys)]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"

                for attempt in range(max_tries):
                    gen_cfg: dict[str, Any] = {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                    }
                    if "flash" in m_name and "pro" not in m_name:
                        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}

                    payload = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": gen_cfg,
                    }
                    try:
                        resp = http.post(url, json=payload, timeout=6.5)
                        if resp.status_code == 404:
                            with self._lock:
                                self.dead_models_cache.add(m_name)
                            model_not_found = True
                            last_err = f"Model {m_name} HTTP 404"
                            break
                        if resp.status_code in (401, 403):
                            with self._lock:
                                self.dead_keys_cache.add(api_key)
                            last_err = f"Key HTTP {resp.status_code} on {m_name}"
                            break
                        if resp.status_code == 429:
                            with self._lock:
                                self.key_idx = (self.key_idx + 1) % max(1, len(live_keys))
                            last_err = f"Key HTTP 429 on {m_name}"
                            break
                        if resp.status_code == 400 and "thinkingConfig" in resp.text:
                            payload["generationConfig"].pop("thinkingConfig", None)
                            resp = http.post(url, json=payload, timeout=6.5)

                        resp.raise_for_status()
                        data = resp.json()
                        raw_txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        raw_txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_txt, flags=re.IGNORECASE).strip()
                        parsed = json.loads(raw_txt)
                        return validator_fn(parsed)
                    except Exception as e:
                        last_err = str(e)
                        logger.warning(f"Gemini call notice ({m_name}): {e}")

                if not model_not_found and last_err and not last_err.startswith("Key HTTP"):
                    break
            if model_not_found:
                continue

        raise RuntimeError(f"All Gemini models/keys failed: {last_err}")

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
        active_workout_task: Optional[dict[str, Any]] = None,
    ) -> Analysis:
        words = learner_text.strip().split()
        if len(words) <= 3 and all(w.lower().strip(".,!?") in {"hey", "hi", "hello", "yo", "sup", "morning", "evening", "alex", "guys", "there", "ok", "okay", "thanks", "yes", "no"} for w in words):
            return Analysis(communicative_intention="greeting")

        user_prompt = json.dumps(
            {
                "register": register_profile.value,
                "directness": directness.value,
                "recent_history": recent_history[-3:],
                "learner_message": learner_text,
                "active_workout_task": active_workout_task,
            },
            ensure_ascii=False,
        )

        def _validate_analyst(raw_dict: dict[str, Any]) -> Analysis:
            normalized_dict = _fast_normalize_raw_analysis(raw_dict, learner_text)
            parsed = Analysis.model_validate(normalized_dict)
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
                temperature=0.15,
                max_tries=1,
            )
        except Exception as err:
            logger.warning(f"Fast Analyst fallback: {err}")
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
        active_workout_task: Optional[dict[str, Any]] = None,
    ) -> ResponderOutput:
        stealth_form = stealth_target_info.get("canonical_form") if stealth_target_info else None
        user_prompt = json.dumps(
            {
                "setting": scene_bible.setting,
                "relationship": scene_bible.relationship,
                "register": register_profile.value,
                "directness": directness.value,
                "dialogue_decision": {
                    "act": dialogue_decision.act.value,
                    "style": dialogue_decision.style.value,
                    "allow_question": dialogue_decision.allow_question,
                    "max_words": dialogue_decision.max_words,
                    "avoid_openers": dialogue_decision.avoid_openers,
                },
                "advisory_recast_candidate": advisory_recast_candidate,
                "recent_history": recent_history[-4:],
                "learner_message": learner_text,
                "active_workout_task": active_workout_task,
            },
            ensure_ascii=False,
        )

        def _validate_resp(raw_dict: dict[str, Any]) -> ResponderOutput:
            try:
                out = ResponderOutput.model_validate(raw_dict)
                return validate_responder_output(
                    output=out,
                    decision=dialogue_decision,
                    advisory_recast_candidate=advisory_recast_candidate,
                    stealth_target_form=stealth_form,
                )
            except Exception:
                return _deterministic_responder_repair(
                    raw_dict,
                    dialogue_decision,
                    advisory_recast_candidate,
                    stealth_form,
                )

        return self._call_gemini_json(
            system_prompt=RESPONDER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            validator_fn=_validate_resp,
            temperature=0.65,
            max_tries=1,
        )

    def generate_register_shift_coaching(self, learner_text: str) -> RegisterShiftResult:
        sys_p = (
            "You are an American spoken-register coach. Express the learner's thought across 3 SPOKEN American registers.\n"
            "SPOKEN_FIRST_REGISTER_POLICY:\n"
            "1. Every transformation MUST remain natural spoken American English said out loud in person, on a call, or in a meeting—unless the learner explicitly requests written communication.\n"
            "2. Professional does NOT mean literary or bureaucratic: changing register adjusts interpersonal tone and formality, not the medium.\n"
            "   - `casual_friend_version`: relaxed everyday speech with a close peer.\n"
            "   - `diplomatic_colleague_version`: considerate, collaborative spoken phrasing with a coworker.\n"
            "   - `executive_manager_version`: crisp, concise, decisive spoken phrasing used by an American leader in a live meeting (more direct and economical, never ceremonial written memo prose).\n"
            "3. Follow `ar_note` rules for `key_pragmatic_distinction_ar`: MSA, <=180 chars, quote Arabic with « », Latin script for English without brackets or quotes.\n"
            "Return ONLY JSON with keys: communicative_intention, casual_friend_version, diplomatic_colleague_version, "
            "executive_manager_version, key_pragmatic_distinction_en, key_pragmatic_distinction_ar, practice_prompt."
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=learner_text,
            validator_fn=RegisterShiftResult.model_validate,
            temperature=0.35,
            max_tries=1,
        )

    def generate_dynamic_dilemma_scene(self, register_profile: str = "diplomatic") -> DynamicDilemmaScene:
        sys_p = (
            "Generate a realistic spoken American pragmatic dilemma JSON with keys: "
            "setting, relationship, register_profile ('casual'|'warm'|'diplomatic'|'executive'), "
            "established_facts (list of 2 strings), counterpart_goal, counterpart_pushback_plan, "
            "learner_pragmatic_goal, opening_line."
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Register: {register_profile}",
            validator_fn=DynamicDilemmaScene.model_validate,
            temperature=0.75,
            max_tries=1,
        )

    def generate_dynamic_workout_scenario(self, target_info: dict[str, str], register_profile: str = "casual") -> DynamicWorkoutScenario:
        forbidden_form = target_info.get("canonical_form", "")
        intention = target_info.get("communicative_intention", "express your point naturally")
        sys_p = (
            f"Create a 1-2 sentence scenario for communicative move '{intention}'.\n"
            f"CRITICAL: NEVER use or leak the phrase '{forbidden_form}' in situation_setup or prompt_question.\n"
            "Return JSON with keys: communicative_intention, situation_setup, prompt_question."
        )

        def _validate_workout(raw_d: dict[str, Any]) -> DynamicWorkoutScenario:
            out = DynamicWorkoutScenario.model_validate(raw_d)
            stem = re.sub(r"\[[^\]]+\]", "", forbidden_form).strip().lower()
            if len(stem) >= 5 and stem in (out.situation_setup + " " + out.prompt_question).lower():
                cleaned_setup = re.sub(re.escape(stem), "handle this situation", out.situation_setup, flags=re.IGNORECASE)
                out = out.model_copy(update={"situation_setup": cleaned_setup})
            register_workout_task(out, target_info)
            return out

        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Register: {register_profile}",
            validator_fn=_validate_workout,
            temperature=0.7,
            max_tries=1,
        )

    def generate_listen_decode_challenge(self, register_profile: str = "casual") -> ListenDecodeChallenge:
        sys_p = (
            "Generate a Listen & Decode challenge with fast American connected speech (shoulda, mighta, whaddaya, didja, lemme, kinda) and pragmatic subtext.\n"
            "Return JSON with keys: context_line, spoken_clip_text, full_unreduced_form, acoustic_reduction_note, pragmatic_subtext_en, pragmatic_subtext_ar, decode_question."
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Register: {register_profile}",
            validator_fn=ListenDecodeChallenge.model_validate,
            temperature=0.75,
            max_tries=1,
        )

    def generate_scene_debrief(self, recent_history: list[dict[str, str]], recent_diagnostics: list[dict[str, Any]]) -> SceneDebriefResult:
        sys_p = (
            "Write a concise end-of-scene debrief JSON.\n"
            "1. `spontaneous_wins`: ONLY error-free natural phrases (return [] if all turns had errors).\n"
            "2. `top_shifts`: up to 3 shifts with keys you_said, natural_alternative, why_it_matters, arabic_note (MSA, <=180 chars, quote Arabic with « »).\n"
            "Return JSON with keys: scene_outcome, spontaneous_wins, top_shifts, closing_note."
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=json.dumps({"history": recent_history[-8:], "diagnostics": recent_diagnostics[-5:]}, ensure_ascii=False),
            validator_fn=lambda raw: SceneDebriefResult.model_validate({
                **raw,
                "top_shifts": [
                    {**s, "arabic_note": normalize_arabic_english_formatting(s.get("arabic_note", ""))}
                    for s in (raw.get("top_shifts") or [])
                ],
            }),
            temperature=0.25,
            max_tries=1,
        )

    def evaluate_listen_decode_attempt(
        self,
        challenge: ListenDecodeChallenge,
        learner_attempt: str,
    ) -> DecodeEvaluationResult:
        sys_p = (
            "Evaluate the learner's comprehension of the spoken American clip and its pragmatic subtext.\n"
            "1. Acknowledge what they understood accurately and gently clarify any missed nuance in 1-2 crisp sentences.\n"
            "2. `coach_feedback_ar`: MSA, <=180 chars, quote Arabic with « », keep English words in Latin script without brackets.\n"
            "Return ONLY JSON with keys: comprehension_accurate (bool), coach_feedback_en, coach_feedback_ar."
        )
        user_p = json.dumps(
            {
                "spoken_clip": challenge.spoken_clip_text,
                "unpacked_form": challenge.full_unreduced_form,
                "expected_subtext": challenge.pragmatic_subtext_en,
                "question": challenge.decode_question,
                "learner_attempt": learner_attempt,
            },
            ensure_ascii=False,
        )
        try:
            return self._call_gemini_json(
                system_prompt=sys_p,
                user_prompt=user_p,
                validator_fn=lambda raw: DecodeEvaluationResult.model_validate({
                    **raw,
                    "coach_feedback_ar": normalize_arabic_english_formatting(raw.get("coach_feedback_ar", "")) or "أصبت في فهم المقصود العام للمتحدث.",
                }),
                temperature=0.25,
                max_tries=1,
            )
        except Exception:
            return DecodeEvaluationResult()

