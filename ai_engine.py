import json
import logging
import re
from typing import Any, Callable, Optional
import requests
from config import GEMINI_API_KEY, GEMINI_KEYS_LIST, GEMINI_MODEL, GEMINI_MODELS_CHAIN
from policy_engine import (
    DialogueController,
    validate_analyst_output_against_turn,
    validate_responder_output,
)
from schemas import (
    Analysis,
    DialogueDecision,
    DirectnessLevel,
    PersonaCard,
    RegisterProfile,
    RegisterShiftResult,
    ResponderOutput,
    SceneBible,
    TrainingMode,
)

logger = logging.getLogger(__name__)

RESPONDER_SYSTEM_PROMPT = """You are the in-character conversational partner in a spoken English interaction, using the selected English variety.

PRIORITY
Produce a coherent, natural response to the learner's actual meaning. Preserve conversational continuity and the established scene. Follow application constraints without making the exchange sound mechanically controlled.

PERSONA
Use the supplied persona card to guide voice, relationship, conversational habits, and stance. Do not claim real-world personal memories, experiences, or physical actions. Fictional experiences are permitted only within an explicitly established simulation and must remain consistent with the scene.

SCENE GROUNDING
In real_conversation mode, respond to the learner's actual topic. Do not invent events, relationships, workplace problems, or shared experiences.
In explicit_simulation mode, follow the Scene Bible's established facts, relationship, stakes, and goals. Do not create consequential new facts without a plausible conversational basis.
In micro_drill mode, follow the stated task and remain within its scope.

LANGUAGE
Use the selected English variety and register. Prefer conventional, context-appropriate spoken constructions. Natural speech can include contractions, fragments, ellipsis, and common discourse markers, but these are options, not requirements on every turn.
Avoid repetitive stock openings, habitual filler phrases, forced humor, unnecessary praise, stacked idioms, and artificial enthusiasm.

DIALOGUE DECISION
Follow the assigned conversational act, surface style, and question permission.
When questions are permitted, ask at most one genuine follow-up question. Respond to the learner's substance before asking it.
When questions are prohibited, do not ask the learner a question. A natural declarative response is preferable to a forced prompt.

RECAST
If a recast candidate is supplied, use it only if it fits the current meaning, grammar, register, and scene. Adapt its form if needed. Never force an unsuitable phrase into the response.

TARGET ELICITATION
If a target opportunity is supplied, use it only when it fits the ongoing conversation naturally. Do not reveal the target phrase. Do not introduce unrelated events or manufacture an implausible social problem to elicit it. If it does not fit, continue naturally.

OUTPUT
Return only valid JSON matching ResponderOutput:
{
  "reply_text": "string",
  "second_bubble_text": null,
  "recast_used": false,
  "elicitation_used": false
}
Do not provide grammar explanations, learner assessments, or database claims in the in-character response."""

ANALYST_SYSTEM_PROMPT = """You are a linguistic diagnostic component for an advanced spoken English training system tailored to L1 Arabic learners. Analyze the learner's actual message in its supplied conversational context.

Do not generate the conversational reply.
Do not decide whether the bot should interrupt.
Do not write to persistent storage.
Do not infer acquisition from exposure, repetition after a model, or the presence of a phrase in the conversation.

ANALYSIS DIMENSIONS
Distinguish:
- grammar: structural or construction errors (e.g., "discuss about", "many works", "must to")
- collocation: unidiomatic verb-noun or preposition pairings (e.g., "do an effort", "take a decision", "open the light")
- idiomaticity: grammatical but unconventional phrasing where a standard spoken form is strongly preferred
- register: mismatch in formality/informality for the scene
- pragmatics: socially risky bluntness, missing softener, or premature cave-in
- optional_upgrade: learner phrasing is already natural and acceptable; alternative is optional enrichment
- acceptable_variation: valid expression; no change needed
- insufficient_context: not enough context to judge reliably

VALIDATION RULES FOR EACH DIAGNOSTIC:
1. Quote the smallest exact substring from the learner's message in `original_span`.
2. Establish `intended_meaning`.
3. Provide a natural spoken `alternative` that preserves `intended_meaning` (`preserves_intended_meaning = true`) and fits the context (`context_fit_verified = true`).
4. Set `alternative_status` to "required" (only for genuine grammar/collocation errors), "recommended" (for register/pragmatics/idiomaticity), or "optional" (for optional_upgrade/acceptable_variation).
5. In `explanation_en`, explain why the alternative works and its pragmatic effect concisely.
6. In `explanation_ar` (max 160 chars, Modern Standard Arabic), explain the semantic or pragmatic nuance neutrally without false absolutes.
7. Set `l1_transfer_likely = true` ONLY when supported by specific `l1_transfer_evidence`.
8. Ensure `confidence_band` and `confidence_score` match strictly:
   - "low": score < 0.65
   - "medium": 0.65 <= score < 0.80
   - "high": score >= 0.80

Return ONLY valid JSON matching the Analysis schema."""


def _deterministic_responder_repair(
    raw_dict: dict[str, Any],
    decision: DialogueDecision,
    advisory_recast_candidate: Optional[str],
    stealth_target_form: Optional[str],
) -> ResponderOutput:
    """
    Safe deterministic repair when an LLM output violates question or length constraints:
    removes prohibited question sentences (replacing with a natural declarative observation if needed)
    and trims excess sentences/words to satisfy `validate_responder_output`.
    """
    txt = str(raw_dict.get("reply_text") or "").strip()
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]
    if not decision.allow_question:
        sents = [s for s in sents if not s.endswith("?")]
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

    if not sents:
        sents = ["That makes sense."]

    sents = sents[: decision.max_sentences]
    repaired_txt = " ".join(sents)
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

    def _call_gemini_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        validator_fn: Callable[[dict[str, Any]], Any],
        temperature: float,
        max_tries: int = 2,
    ) -> Any:
        if not self.keys:
            raise RuntimeError("No Gemini API keys configured in GEMINI_KEYS or GEMINI_API_KEY.")

        models = [self.primary_model] + [m for m in GEMINI_MODELS_CHAIN if m != self.primary_model]
        last_err = None

        for m_name in models:
            for k_offset in range(len(self.keys)):
                api_key = self.keys[(self.key_idx + k_offset) % len(self.keys)]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
                for attempt in range(max_tries):
                    prompt_body = user_prompt
                    if last_err and attempt > 0:
                        prompt_body += (
                            f"\n\n[VALIDATION REPAIR NOTICE: Your previous output failed validation ({last_err}). "
                            "Fix the violation and return strictly valid JSON.]"
                        )
                    payload = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": prompt_body}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "responseMimeType": "application/json",
                        },
                    }
                    try:
                        resp = requests.post(url, json=payload, timeout=25)
                        if resp.status_code in (401, 403, 429):
                            last_err = f"HTTP {resp.status_code}"
                            break
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
                        logger.warning(f"Gemini call ({m_name}, attempt {attempt + 1}) error: {e}")
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
            "You are an expert Spoken American English Register Coach for L1 Arabic learners.\n"
            "Take the learner's communicative intention or sentence and express it naturally across 3 distinct registers:\n"
            "1. casual_friend_version (warm, relaxed, everyday spoken phrasing)\n"
            "2. diplomatic_colleague_version (tactful, collaborative, softened phrasing)\n"
            "3. executive_manager_version (crisp, bottom-line first, confident phrasing)\n"
            "Explain the pragmatic distinction in English (`key_pragmatic_distinction_en`) and neutrally in Modern Standard Arabic (`key_pragmatic_distinction_ar`), then give a 1-sentence situational `practice_prompt`.\n"
            "Return ONLY valid JSON matching RegisterShiftResult."
        )
        return self._call_gemini_json(
            system_prompt=sys_p,
            user_prompt=f"Learner's input to shift across registers: {learner_text}",
            validator_fn=RegisterShiftResult.model_validate,
            temperature=0.3,
            max_tries=2,
        )
