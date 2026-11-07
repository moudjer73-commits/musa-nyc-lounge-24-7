from src.analyst.calques import detect_seed_calques
from src.config import PROMPTS_DIR
from src.db.analyst_repo import AnalystRepository
from src.llm.gemini_client import GeminiService
from src.models.analyst import (
    AnalystLLMOutput,
    CONFIDENCE_THRESHOLD,
    CorrectionDecision,
)
from src.models.profile import LearnerProfile
from src.models.register_scene import SceneConfig


class AnalystEngine:
    def __init__(self, repo: AnalystRepository, llm: GeminiService) -> None:
        self.repo = repo
        self.llm = llm

    async def analyze_turn(
        self,
        profile: LearnerProfile,
        scene: SceneConfig,
        session_id: str,
        learner_text: str,
    ) -> CorrectionDecision:
        calque_hits = detect_seed_calques(learner_text)
        calque_hint = (
            "Known seed calques detected: "
            + "; ".join(f"'{c.non_native_phrase}' -> '{c.natural_alternative}' ({c.cause_status})" for c in calque_hits)
            if calque_hits
            else "No seed calques pre-matched."
        )
        sys_prompt = (PROMPTS_DIR / "analyst_system.md").read_text(encoding="utf-8")
        user_prompt = (
            f"Active Scene: {scene.setting} ({scene.relationship}), directness={scene.directness}, function={scene.function}\n"
            f"{calque_hint}\nLearner turn: {learner_text}"
        )
        fallback = AnalystLLMOutput(category="C", error_type="none", confidence=0.0)
        raw = await self.llm.generate_structured(
            user_id=profile.user_id,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            schema_cls=AnalystLLMOutput,
            fallback_instance=fallback,
        )

        # Confidence Gate (Blueprint 1.4 & 4.3): below threshold -> show nothing, log silently
        if raw.confidence < CONFIDENCE_THRESHOLD or raw.category == "C":
            gated = raw.confidence < CONFIDENCE_THRESHOLD and raw.category != "C"
            self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=gated, action_taken="silent_log_only", surfaced_mid_session=False)
            return CorrectionDecision(action="silent_log_only" if gated else "none", visible_to_learner=False, raw_analysis=raw)

        # Recurring grammar errors in natural-sounding speech: logged silently, never surfaced mid-conversation
        if raw.is_recurring_natural_grammar and raw.error_type == "grammar" and not raw.meaning_broken:
            self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=False, action_taken="end_of_session_summary", surfaced_mid_session=False)
            return CorrectionDecision(action="end_of_session_summary", visible_to_learner=False, raw_analysis=raw)

        sig = raw.error_signature if raw.error_signature != "none" else f"{raw.category}:{raw.error_type}:{raw.evidence_span.lower()}"
        raw.error_signature = sig
        prior_count = self.repo.count_signature_occurrences(profile.user_id, session_id, sig)
        occurrence = prior_count + 1

        arabic_note = None
        if raw.arabic_pragmatic_note and profile.arabic_support in ("short_post_turn_notes", "bilingual"):
            arabic_note = raw.arabic_pragmatic_note

        # 1. Immediate repair only if meaning breaks
        if raw.meaning_broken:
            self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=False, action_taken="immediate_meaning_repair", surfaced_mid_session=True)
            msg = f"Quick check so I don't misunderstand—did you mean: \"{raw.suggested_alternative}\"?"
            return CorrectionDecision(
                action="immediate_meaning_repair",
                visible_to_learner=True,
                learner_feedback_message=msg,
                retry_prompt=f"Try saying it with: \"{raw.suggested_alternative}\"",
                arabic_post_turn_note=arabic_note,
                raw_analysis=raw,
                occurrence_count=occurrence,
            )

        # 2. First occurrence of a minor error -> silent recast inside Alex's reply
        if occurrence == 1 and profile.correction_style != "immediate":
            self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=False, action_taken="silent_recast", surfaced_mid_session=False)
            return CorrectionDecision(
                action="silent_recast",
                visible_to_learner=False,
                recast_phrase_for_alex=raw.suggested_alternative,
                arabic_post_turn_note=arabic_note,
                raw_analysis=raw,
                occurrence_count=1,
            )

        # 3. Second occurrence (or immediate preference) -> explicit correction + retry
        if occurrence == 2 or profile.correction_style == "immediate":
            self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=False, action_taken="explicit_correction_retry", surfaced_mid_session=True)
            prefix = "Spoken register upgrade" if raw.category == "D" else "Natural phrasing tweak"
            msg = f"{prefix}: instead of \"{raw.evidence_span}\", go with \"{raw.suggested_alternative}\"."
            return CorrectionDecision(
                action="explicit_correction_retry",
                visible_to_learner=True,
                learner_feedback_message=msg,
                retry_prompt=f"Give that line another shot using \"{raw.suggested_alternative}\":",
                arabic_post_turn_note=arabic_note,
                raw_analysis=raw,
                occurrence_count=occurrence,
            )

        # 4. Everything else -> end-of-session summary
        self.repo.log_analysis(profile.user_id, session_id, raw, gated_out=False, action_taken="end_of_session_summary", surfaced_mid_session=False)
        return CorrectionDecision(action="end_of_session_summary", visible_to_learner=False, arabic_post_turn_note=arabic_note, raw_analysis=raw, occurrence_count=occurrence)

    def format_end_of_session_summary(self, user_id: int, session_id: str) -> str:
        targets = self.repo.get_session_summary_targets(user_id, session_id, max_targets=2)
        if not targets:
            return "End-of-Session Summary: Great flow today—no high-priority correction targets."
        lines = ["End-of-Session Focus (Top Priority Targets):"]
        for idx, t in enumerate(targets, 1):
            label = "Spoken Register Upgrade" if t["category"] == "D" else f"Target ({t['error_type']})"
            lines.append(
                f"{idx}. {label}: \"{t['evidence_span']}\" -> Try: \"{t['suggested_alternative']}\" (Say it once out loud to lock it in!)"
            )
        return "\n".join(lines)

    async def get_next_hint(self, profile: LearnerProfile, target_word: str, ask_arabic: bool = False) -> tuple[str, str]:
        step_num = self.repo.advance_hint_step(profile.user_id, target_word)
        allow_ar = ask_arabic or profile.arabic_support == "bilingual"
        if step_num == 1:
            step_name = "describe_it"
            instruction = f"Step 1 (Describe it in simple English without using the exact word '{target_word}')"
        elif step_num == 2:
            step_name = "first_letter"
            instruction = f"Step 2 (Give the first letter '{target_word[:1].upper()}' and syllable cue for '{target_word}')"
        elif step_num == 3 or not allow_ar:
            step_name = "sentence_starter"
            instruction = f"Step 3 (Give a natural spoken sentence starter where '{target_word}' fits right at the blank)"
        else:
            step_name = "arabic_fallback"
            instruction = f"Step 4 (Provide a short Arabic gloss and the exact English expression '{target_word}')"

        sys_prompt = (PROMPTS_DIR / "hint_ladder.md").read_text(encoding="utf-8")
        reply = await self.llm.generate_text(
            user_id=profile.user_id,
            system_prompt=sys_prompt,
            user_prompt=instruction,
        )
        return step_name, reply
