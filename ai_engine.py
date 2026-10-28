import html
import json
import logging
import re
from typing import Any, Callable, List
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL
from schemas import (
    DevilsAdvocateOutput,
    MetalinguisticOutput,
    NegotiationTurnOutput,
    UnifiedBanterOutput,
)

logger = logging.getLogger(__name__)


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def is_metalinguistic_question(text: str) -> bool:
    t = (text or "").strip().lower()
    if "?" not in t:
        return False
    patterns = [
        r"\bwhy\s+(would|did|do|can't|cannot|should)\s+(you|we|i|people|americans|native\s+speakers)\s+(say|use|suggest|prefer)\b",
        r"\binstead\s+of\s+['\"“”]",
        r"\bis\s+my\s+(version|phrasing|sentence)\b",
        r"\bwhat('s|\s+is)\s+the\s+(exact\s+)?difference\s+between\b",
        r"\bis\s+that\s+something\s+americans\s+actually\s+say\b",
        r"\bwhy\s+is\s+['\"“”].+?['\"“”]\s+(less\s+natural|wrong|better|more\s+natural)\b",
    ]
    return any(re.search(p, t) for p in patterns)


def is_short_greeting(text: str) -> bool:
    words = (text or "").strip().lower().split()
    if not words or len(words) > 4:
        return False
    greetings = {"hey", "hi", "hello", "yo", "sup", "morning", "evening", "guys", "there", "all", "everyone"}
    return any(w.strip(".,!?") in greetings for w in words) and words[0].strip(".,!?") in {"hey", "hi", "hello", "yo", "sup", "good"}


from transport_core import NYCLoungeAI as _WorkingTransport


class CleanAIEngine:
    def __init__(self, *args, **kwargs):
        self._transport = _WorkingTransport()

    def _call_gemini_json(
        self,
        system_prompt: str,
        user_prompt: str,
        validator_fn: Callable[[dict], Any],
        temperature: float = 0.35,
        max_tries: int = 2,
    ) -> Any:
        return self._transport._call_gemini_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tries=max_tries,
            validator_fn=validator_fn,
        )

    def generate_banter_turn(self, user_text: str, history_lines: List[str]) -> UnifiedBanterOutput:
        system_prompt = (
            "You run 'Musa's NYC Lounge', a natural C1/C2 Spoken American English hangout with three everyday NYC friends:\n"
            "- Marcus: Warm, practical, grounded friend. Plain, natural American English.\n"
            "- Chloe: Friendly, observant, down-to-earth friend. Conversational and warm (NO literary clichés and NO psychoanalyzing the user).\n"
            "- Jake: Relaxed, upbeat, punchy friend.\n\n"
            "LAWS OF NATURAL CONVERSATION (CALL A):\n"
            "1. STRICT FACTUAL GROUNDING: Respond ONLY to what the user actually said. NEVER invent fake workplace crises, 'brand strategies', 'decks', 'client syncs', or 'deadlines' unless the user explicitly brought up a work topic.\n"
            "2. NORMAL HUMAN GREETINGS: If the user greets the group ('Hey guys', 'Hello'), greet them back warmly like real friends hanging out at a coffee shop.\n"
            "3. INTERACTIVE GROUP CHEMISTRY: Let the second and third speakers react naturally to the previous friend's comment as well as the user, rather than delivering 3 isolated monologues.\n"
            "4. ANTI-INTERROGATION PACING: Never let all 3 speakers ask questions! Ask AT MOST ONE question total across the entire response (only in the final speaker's turn if appropriate). If the user gives a short undecided comment or shares a casual reaction, feel free to use ZERO questions and just riff naturally with declarative comments.\n"
            "5. NO MECHANICAL OPENERS: Do not start turns with 'Look,' or 'The thing is,'. Always end questions with '?'.\n\n"
            "LAWS OF PEDAGOGICAL PRECISION (CALL B):\n"
            "1. SILENCE POLICY (`coaching_decision = 'silence'`): If the user's English is natural, idiomatic, and grammatically sound, set `coaching_decision` to `'silence'`, `mirror` to `null`, and `spoken_move` to `null`. Do NOT invent nitpicks!\n"
            "2. SURGICAL MIRROR (`coaching_decision = 'surgical_mirror'`): If the user made a genuine collocation calque, grammar error, or unidiomatic phrasing, set `coaching_decision` to `'surgical_mirror'`, populate `mirror` (isolating ONLY the erroneous span in `wrong_span` and its natural replacement in `natural_us`, mentioning main American alternatives calmly without ever saying 'marks you as a non-native speaker'), and set `spoken_move` to `null` (Cognitive Cap = 1).\n"
            "3. RECAST MOVE (`coaching_decision = 'recast_move'`): ONLY if the user's sentence is error-free but sounds overly stiff/textbook-like, set `coaching_decision` to `'recast_move'`, set `mirror` to `null`, and provide 1 natural spoken expression in `spoken_move` that one of the friends used in the dialogue.\n\n"
            "Return ONLY valid JSON matching UnifiedBanterOutput:\n"
            "{\n"
            '  "coaching_decision": "silence",\n'
            '  "turns": [{"speaker": "Marcus", "text": "..."}, {"speaker": "Chloe", "text": "..."}, {"speaker": "Jake", "text": "..."}],\n'
            '  "mirror": null,\n'
            '  "spoken_move": null\n'
            "}"
        )
        ctx_str = "\n".join(history_lines[-6:]) if history_lines else "(Fresh conversation — no prior context)"
        user_prompt = (
            f"Recent conversation in current scene:\n{ctx_str}\n\n"
            f'Learner\'s new message: "{user_text}"\n\n'
            "Generate the unified JSON response:"
        )

        def _validate_and_polish(d: dict) -> UnifiedBanterOutput:
            out = UnifiedBanterOutput.model_validate(d)
            for idx, turn in enumerate(out.turns):
                txt = turn.text.strip()
                for op in ("Look, ", "The thing is, ", "Honestly, "):
                    if txt.startswith(op):
                        txt = txt[len(op):].lstrip()
                        if txt:
                            txt = txt[0].upper() + txt[1:]
                if idx < len(out.turns) - 1 and "?" in txt:
                    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]
                    decl = [s for s in sents if not s.endswith("?")]
                    if decl:
                        txt = " ".join(decl)
                turn.text = txt
            if out.coaching_decision == "silence":
                out.mirror = None
                out.spoken_move = None
            elif out.coaching_decision == "surgical_mirror":
                out.spoken_move = None
            elif out.coaching_decision == "recast_move":
                out.mirror = None
            return out

        return self._call_gemini_json(system_prompt, user_prompt, _validate_and_polish, temperature=0.35)

    def generate_metalinguistic_consultation(self, question_text: str, history_lines: List[str]) -> MetalinguisticOutput:
        system_prompt = (
            "You are the Precision C1/C2 Spoken American English Coach in 'Musa's NYC Lounge'.\n"
            "The learner paused the scene to ask a direct metalinguistic question about English usage.\n"
            "Follow these calibration rules:\n"
            "1. Distinguish comprehensibility/meaning from idiomatic American collocation or usage preference.\n"
            "2. Explain clearly why the recommended phrasing is natural in US English and contrast major natural alternatives as practical tendencies rather than rigid absolutes.\n"
            "3. Never use stigmatizing language ('marks you as a non-native speaker') and never claim in Arabic that an understandable phrase is 'غير مستخدمة' (instead say 'ليست الصياغة الاصطلاحية المعتادة في الإنجليزية الأمريكية لهذا المعنى'). Never invent statistics.\n"
            "4. Provide a natural `scene_resume_line` from `counterpart_speaker` addressing the learner directly as 'you' to resume the paused conversation topic.\n\n"
            'Return ONLY valid JSON: {"coach_explanation_en": "...", "coach_explanation_ar": "...", "counterpart_speaker": "Marcus", "scene_resume_line": "..."}'
        )
        ctx_str = "\n".join(history_lines[-4:]) if history_lines else "Casual conversation in the lounge"
        user_prompt = f'Paused scene context:\n{ctx_str}\n\nLearner\'s metalinguistic question: "{question_text}"'
        return self._call_gemini_json(system_prompt, user_prompt, MetalinguisticOutput.model_validate, temperature=0.25)

    def evaluate_negotiation_turn(self, user_text: str, neg_state: dict, history_lines: List[str]) -> NegotiationTurnOutput:
        system_prompt = (
            "You run the 'Read the Room' 2-Beat Pragmatic Negotiation trainer in Musa's NYC Lounge.\n"
            "Evaluate the learner's pragmatic negotiation strategy (distinct from mere grammatical politeness):\n"
            "- If the learner caves in prematurely and surrenders their core constraint/interest just to be polite or fast, set `evaluation_stage` to `'beat1_cavein'`, explain `how_it_lands`, set `preserved_core_constraint` to `false`, and provide a `retry_prompt` without giving away the exact wording.\n"
            "- If the learner proposes a concrete trade-off or compromise that acknowledges the counterpart's constraint while protecting the core interest, set `evaluation_stage` to `'beat2_compromise'`, provide the counterpart's acceptance in `counterpart_reply`, and summarize the strategic improvement in `attempt1_vs_attempt2_comparison`.\n"
            "- Otherwise (if setting up or continuing a scenario before a clear cave-in or resolution), set `evaluation_stage` to `'setup_or_continue'` and respond in-character in `counterpart_reply` presenting a clear pragmatic constraint.\n\n"
            "Return ONLY valid JSON matching NegotiationTurnOutput."
        )
        user_prompt = (
            f"Active negotiation state: {json.dumps(neg_state, ensure_ascii=False)}\n"
            f"Recent history:\n" + ("\n".join(history_lines[-4:]) if history_lines else "(Start of negotiation)") +
            f'\n\nLearner\'s move: "{user_text}"'
        )
        return self._call_gemini_json(system_prompt, user_prompt, NegotiationTurnOutput.model_validate, temperature=0.25)

    def generate_devils_advocate_turn(self, user_text: str, history_lines: List[str]) -> DevilsAdvocateOutput:
        system_prompt = (
            "You run the 'Devil's Advocate' intellectual sparring mode in Musa's NYC Lounge.\n"
            "Rules:\n"
            "1. FAIR RESTATEMENT: First restate or Steelman the learner's point fairly in 1 sentence so they feel heard.\n"
            "2. SHARP COUNTER-PERSPECTIVE: Present a realistic, intelligent counter-argument in natural Spoken American English.\n"
            "3. PROBING QUESTION: End with 1 thought-provoking question that tests the learner's reasoning.\n"
            "4. Keep the focus on ideas—only populate `mirror` if there is a clear collocation/grammar error that needs a surgical fix.\n\n"
            "Return ONLY valid JSON matching DevilsAdvocateOutput."
        )
        ctx_str = "\n".join(history_lines[-4:]) if history_lines else "(New topic)"
        user_prompt = f'Context:\n{ctx_str}\n\nLearner\'s argument: "{user_text}"'
        return self._call_gemini_json(system_prompt, user_prompt, DevilsAdvocateOutput.model_validate, temperature=0.35)
