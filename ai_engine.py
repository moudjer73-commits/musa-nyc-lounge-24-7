import time
import json
import logging
import re
import threading
from dataclasses import dataclass
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
    normalize_arabic_english_formatting,
    register_workout_task,
    validate_responder_output,
)
from schemas import (
    CallMetaDict,
    ComprehensionStatus,
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

RESPONDER_SYSTEM_PROMPT = """You are Alex, a late-20s / early-30s American guy talking to a friend over text or voice note in a spoken English lab. Your only job is to sound like a real, articulate, spontaneous speaker of casual American English.

Core Behavior:
1. Reply naturally with conversational ellipsis, short acknowledgments, understatement, and dry wit. Match the emotional temperature of the conversation.
2. Use everyday spoken verbs and phrasal verbs heavily (get, end up, wind up, turn out, go ahead and, be all set, be up for, figure out, run into, put off, mess with, deal with, come up with, get on my nerves, etc.).
3. Use ellipsis correctly: Ellipsis is for answers to direct questions and second clauses, not for opening a topic. Example: "Going tonight?" -> "Yeah, probably." Not: "Yeah probably going."
4. Warmth-First Guardrail: Warmth first, wit second. Always acknowledge the emotional weight before you joke. If the learner shares bad news, stress, or a problem, lead with empathy ("Oh man, that's rough. I'm sorry." / "Ugh, that's the worst.") before any dry wit. Never lead with sarcasm on a serious turn.
5. Bare Greeting Rule (CRITICAL): If the learner just says "Hey," "Hi," "Yo," or "Hey Alex" with no topic, reply naturally and simply ("Hey, what's up?" / "Hey! How's it going?" / "Yo, what's good?") — NOTHING MORE. Do NOT invent any personal activity for yourself. No coffee, no traffic, no workout, no errands, no "just got back from..."
6. Topic-Based Volley Rule (Anti Dead-End): Once a topic is in play, avoid 3-word dead ends by adding a topic-based volley — a reaction + a quick thought ON WHAT THE LEARNER JUST SAID. Never invent parallel 'me-too' personal stories or fake physical errands for Alex.
7. Hard Bans: Zero corporate buzzwords (bandwidth, circle back, lean in, low-hanging fruit, move the needle, synergy, deep dive), zero academic flavor, zero Gen Z meme slang (fr, no cap, rizz, skibidi, bussin, gyatt). Never sound like a teacher ("Great job!", "Good point!").
8. Strictly obey `dialogue_decision`:
   - Keep word count <= `max_words` and sentences <= `max_sentences`.
   - If `allow_question` is false, do NOT ask any question or end with `?`. Default to declarative statements/observations ending in a period.
   - Do NOT start your reply with any word in `avoid_openers`.
   - If `advisory_recast_candidate` is given, weave it naturally only if it fits organically.
9. If `scene_bible.established_facts` includes a `Recent Quick Upgrade card shown to learner` and the learner questions or pushes back on that note, address their linguistic point directly and honestly like a sharp native peer.
10. If `active_workout_task` is provided: check whether `learner_message` accomplishes `active_workout_task.prompt_question` (accept any natural phrasing). If missed, respond naturally and re-open the situation in-character without scolding or leaking `forbidden_form`.
11. Set `comprehension_status` honestly ("ok", "ambiguous", or "reversed"). If `pending_clarification` is given, work that one question into your reply naturally.

Return ONLY valid JSON:
{"reply_text": "1-2 natural spoken sentences", "second_bubble_text": null, "recast_used": false, "elicitation_used": false, "comprehension_status": "ok"}"""

# V3.0 Analyst: the raw shape that ``validation.validate_analysis`` accepts (S0 5.1). The v2 inline Analyst
# prompt and its normalizer were removed in S5 together with ``validate_analyst_output_against_turn``.
ANALYST_V3_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "analyst_v3_system.md"


def load_analyst_v3_prompt() -> str:
    return ANALYST_V3_PROMPT_FILE.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class _CallResult:
    """Stand-in for ``llm_transport.CallResult`` on the legacy single-model path."""

    value: Any
    meta: Any = None


@dataclass(frozen=True)
class ResponderResult:
    output: ResponderOutput
    comprehension_status: ComprehensionStatus = ComprehensionStatus.OK
    call_meta: Optional[CallMetaDict] = None


_GREETINGS = frozenset({"hey", "hi", "hello", "yo", "sup", "morning", "evening", "alex", "guys", "there", "ok", "okay", "thanks", "yes", "no"})


def is_trivial_message(text: str) -> bool:
    """A greeting or an acknowledgement of three words or fewer: nothing to diagnose (``SkipReason.TRIVIAL``)."""
    words = (text or "").strip().split()
    return bool(words) and len(words) <= 3 and all(w.lower().strip(".,!?") in _GREETINGS for w in words)


def _default_call_json() -> Optional[Callable[..., Any]]:
    try:
        import llm_transport  # type: ignore[import-not-found]
    except Exception:
        return None
    return getattr(llm_transport, "call_json", None)


def _coerce_call_class(value: str) -> Any:
    """``llm_transport.CallClass`` when the transport defines one (a str-valued enum), else the plain string."""
    try:
        import llm_transport  # type: ignore[import-not-found]

        return llm_transport.CallClass(value)
    except Exception:
        return value


def _meta_dict(meta: Any) -> Optional[CallMetaDict]:
    if meta is None:
        return None
    try:
        return CallMetaDict.from_meta(meta)
    except Exception:
        return None


def _coerce_status(value: Any) -> str:
    try:
        return ComprehensionStatus(str(value).strip().lower()).value
    except ValueError:
        return ComprehensionStatus.OK.value


def _identity_dict(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("model reply is not a JSON object")
    return raw


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
    def __init__(
        self,
        keys: Optional[list[str]] = None,
        primary_model: str = GEMINI_MODEL,
        transport_call_json: Optional[Callable[..., Any]] = None,
    ):
        self.transport_call_json = transport_call_json
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

    # ------------------------------------------------------------------ V3.0 calls (S0 6, A12)

    def _call_json(
        self,
        call_class: str,
        system: str,
        user: str,
        validator: Callable[[dict[str, Any]], Any],
        *,
        deadline_s: Optional[float] = None,
        correlation_id: Optional[str] = None,
        temperature: float = 0.15,
    ) -> Any:
        """One model call through ``llm_transport.call_json`` (Gemini's transport: lanes, cooldowns, deadlines).
        Returns an object with ``.value`` and ``.meta``. ``transport_call_json`` is injectable for tests; when
        the transport module is not importable the engine's own single-model path is used, so a checkout
        without it keeps working. ``QuotaShed`` / ``DeadlineExceeded`` / ``ResponderUnavailable`` propagate."""
        fn = self.transport_call_json or _default_call_json()
        if fn is not None:
            return fn(
                _coerce_call_class(call_class), system, user, validator,
                deadline_s=deadline_s, correlation_id=correlation_id,
            )
        value = self._call_gemini_json(
            system_prompt=system, user_prompt=user, validator_fn=validator, temperature=temperature, max_tries=1,
        )
        return _CallResult(value, None)

    def run_analyst_v3(
        self,
        *,
        learner_text: str,
        recent_history: list[dict[str, str]],
        register_profile: RegisterProfile,
        directness: DirectnessLevel,
        scene_bible: SceneBible,
        active_targets: Optional[list[dict[str, str]]] = None,
        active_workout_task: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        deadline_s: Optional[float] = 30.0,
    ) -> tuple[dict[str, Any], Optional[CallMetaDict]]:
        """The raw V3 Analyst object (``validation.validate_analysis`` checks it; nothing here is trusted)."""
        user_prompt = json.dumps(
            {
                "learner_message": learner_text,
                "register_profile": register_profile.value,
                "directness": directness.value,
                "scene": f"{scene_bible.setting} ({scene_bible.relationship})"[:240],
                "recent_turns": [{"speaker": h.get("role", ""), "text": h.get("text", "")} for h in recent_history[-4:]],
                "active_targets": [{"id": t["target_id"], "expression": t["canonical_form"]} for t in (active_targets or [])],
                "active_workout_task": active_workout_task,
            },
            ensure_ascii=False,
        )

        def _validate(raw: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(raw, dict):
                raise ValueError("analyst reply is not a JSON object")
            return raw

        result = self._call_json(
            "analyst", load_analyst_v3_prompt(), user_prompt, _validate,
            deadline_s=deadline_s, correlation_id=correlation_id, temperature=0.15,
        )
        return result.value, _meta_dict(result.meta)

    def run_responder_v3(
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
        pending_clarification: Optional[str] = None,
        correlation_id: Optional[str] = None,
        deadline_s: Optional[float] = None,
    ) -> "ResponderResult":
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
                "pending_clarification": pending_clarification,
                "recent_history": recent_history[-4:],
                "learner_message": learner_text,
                "active_workout_task": active_workout_task,
            },
            ensure_ascii=False,
        )
        status_holder: dict[str, str] = {"status": ComprehensionStatus.OK.value}

        def _validate_resp(raw_dict: dict[str, Any]) -> ResponderOutput:
            raw = dict(raw_dict)
            status_holder["status"] = _coerce_status(raw.pop("comprehension_status", None))
            try:
                out = ResponderOutput.model_validate(raw)
                return validate_responder_output(
                    output=out,
                    decision=dialogue_decision,
                    advisory_recast_candidate=advisory_recast_candidate,
                    stealth_target_form=stealth_form,
                )
            except Exception:
                return _deterministic_responder_repair(raw, dialogue_decision, advisory_recast_candidate, stealth_form)

        result = self._call_json(
            "responder", RESPONDER_SYSTEM_PROMPT, user_prompt, _validate_resp,
            deadline_s=deadline_s, correlation_id=correlation_id, temperature=0.65,
        )
        return ResponderResult(result.value, ComprehensionStatus(status_holder["status"]), _meta_dict(result.meta))

    def run_responder(self, **kwargs: Any) -> ResponderOutput:
        """v2 entry point (same kwargs as ``run_responder_v3``); returns only the output."""
        return self.run_responder_v3(**kwargs).output

    def call_llm_for_judge(self, system: str, user: str) -> Any:
        """``retrieval.CallLLM``: the retrieval judge's model call (transport class ``judge``)."""
        result = self._call_json("judge", system, user, _identity_dict, deadline_s=8.0, temperature=0.0)
        return result.value

    def build_judge(self) -> Callable[[Any], Any]:
        from retrieval import make_judge

        return make_judge(self.call_llm_for_judge)

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
            "You handle active retrieval practice for an advanced learner of casual American English.\n"
            f"Take the target communicative move '{intention}' and create a short, relatable, everyday micro-scenario (1-2 sentences) "
            "that naturally invites the user to express that meaning — not a textbook role-play, job interview, or fill-in-the-blank template.\n"
            "Allowed worlds ONLY: friends, family, roommates, neighbors, dating, weekend plans, small annoyances, food, texting.\n"
            "BANNED worlds: job interviews, presentations, academic topics, corporate meetings.\n"
            f"CRITICAL: NEVER reveal, hint at, or leak the target phrase '{forbidden_form}' in situation_setup or prompt_question.\n"
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
            "You review a finished conversation and give a tight, high-value recap for an advanced learner of casual American English.\n"
            "Structure & Rules:\n"
            "1. `spontaneous_wins`: Quote 1-2 of the user's EXACT error-free sentences that already sounded genuinely native (return [] if all turns had errors). Zero generic praise.\n"
            "2. `top_shifts`: Point out ONLY the 1-2 highest-leverage spoken upgrades or recurring habits worth watching (keys: you_said, natural_alternative, why_it_matters, arabic_note in MSA <=180 chars quoting Arabic with « »).\n"
            "3. Zero robotic cheerleading, zero fake numerical grades ('8/10', '85% fluent'), zero 'great job!' without a specific example. Keep total text under 120 words.\n"
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



def probe_and_rank_live_models(keys: list[str], candidates: list[str]) -> list[str]:
    """Self-healing startup probe: tests candidate models with a 1-token ping, drops 404/503 models, and ranks by speed."""
    if not keys or not candidates:
        return candidates
    key = keys[0]
    scored: list[tuple[int, str]] = []
    for model in candidates[:5]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        t0 = time.monotonic()
        try:
            r = requests.post(
                url,
                json={
                    "contents": [{"role": "user", "parts": [{"text": "ok"}]}],
                    "generationConfig": {"maxOutputTokens": 4, "thinkingConfig": {"thinkingBudget": 0}},
                },
                timeout=4.5,
            )
            if r.status_code == 400 and "thinking" in r.text.lower():
                r = requests.post(
                    url,
                    json={"contents": [{"role": "user", "parts": [{"text": "ok"}]}], "generationConfig": {"maxOutputTokens": 4}},
                    timeout=4.5,
                )
            if r.status_code == 200:
                scored.append((int((time.monotonic() - t0) * 1000), model))
        except Exception:
            continue
    if not scored:
        return candidates
    scored.sort(key=lambda x: x[0])
    healthy = [m for _, m in scored]
    for m in candidates:
        if m not in healthy:
            healthy.append(m)
    return healthy
