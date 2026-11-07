from pydantic import BaseModel, Field
from src.config import (
    GoalType,
    ONBOARDING_TURNS_PER_SESSION,
    PROMPTS_DIR,
    get_initial_register_weights,
)
from src.db.profile_repo import ProfileRepository
from src.llm.gemini_client import GeminiService
from src.models.profile import ArabicSupport, CorrectionStyle, LearnerProfile


class CalibrationResult(BaseModel):
    inferred_goal: GoalType = "general"
    interests: list[str] = Field(default_factory=list)
    detected_dialect: str = "unspecified"
    dialect_evidence_span: str = ""
    dialect_supported_by_speech: bool = False
    other_languages: list[str] = Field(default_factory=list)
    correction_style: CorrectionStyle = "selective"
    arabic_support: ArabicSupport = "none"
    retrieval_score: float = Field(default=2.0, ge=0.0, le=4.0)
    retrieval_evidence: str = ""
    linguistic_score: float = Field(default=2.0, ge=0.0, le=4.0)
    linguistic_evidence: str = ""
    interaction_score: float = Field(default=2.0, ge=0.0, le=4.0)
    interaction_evidence: str = ""
    register_score: float = Field(default=2.0, ge=0.0, le=4.0)
    register_evidence: str = ""


class OnboardingDiagnosisEngine:
    def __init__(self, repo: ProfileRepository, llm: GeminiService) -> None:
        self.repo = repo
        self.llm = llm

    def _load_prompt(self, name: str) -> str:
        return (PROMPTS_DIR / name).read_text(encoding="utf-8")

    async def process_turn(self, user_id: int, learner_text: str) -> tuple[str, LearnerProfile]:
        profile = self.repo.get_or_create_profile(user_id)
        session_num = min(profile.onboarding_session, 3)
        self.repo.log_onboarding_turn(user_id, session_num, "learner", learner_text)

        history = self.repo.get_onboarding_transcript(user_id, session_num=session_num)
        formatted_history = "\n".join(f"{r['role'].upper()}: {r['content']}" for r in history[-8:])
        sys_prompt = self._load_prompt(f"onboarding_s{session_num}.md")

        alex_reply = await self.llm.generate_text(
            user_id=user_id,
            system_prompt=sys_prompt,
            user_prompt=f"Conversation so far:\n{formatted_history}\nRespond naturally as Alex:",
        )
        self.repo.log_onboarding_turn(user_id, session_num, "alex", alex_reply)

        profile.onboarding_turns_in_session += 1
        if profile.onboarding_turns_in_session >= ONBOARDING_TURNS_PER_SESSION:
            profile.completed_sessions_count += 1
            profile.onboarding_turns_in_session = 0
            if profile.onboarding_session < 3:
                profile.onboarding_session += 1
            elif not profile.onboarding_completed:
                profile.onboarding_completed = True
                profile = await self.calibrate_profile(profile)

        self.repo.save_profile(profile)
        return alex_reply, profile

    async def calibrate_profile(self, profile: LearnerProfile) -> LearnerProfile:
        transcript = self.repo.get_onboarding_transcript(profile.user_id)
        learner_turns = "\n".join(
            f"[S{r['session_num']}] {r['content']}" for r in transcript if r["role"] == "learner"
        )
        cal_prompt = self._load_prompt("profile_calibration.md")
        fallback = CalibrationResult(inferred_goal=profile.goal)
        res = await self.llm.generate_structured(
            user_id=profile.user_id,
            system_prompt=cal_prompt,
            user_prompt=f"Learner turns across sessions:\n{learner_turns}",
            schema_cls=CalibrationResult,
            fallback_instance=fallback,
        )

        profile.goal = res.inferred_goal
        profile.register_weights = get_initial_register_weights(profile.goal)
        if res.interests:
            profile.interests = list(dict.fromkeys(profile.interests + res.interests))
        if res.other_languages:
            profile.other_languages = list(dict.fromkeys(profile.other_languages + res.other_languages))
        profile.correction_style = res.correction_style
        profile.arabic_support = res.arabic_support

        if res.detected_dialect != "unspecified":
            profile.dialect.dialect_name = res.detected_dialect
        if res.dialect_evidence_span:
            profile.dialect.record_observation(
                res.dialect_evidence_span, res.dialect_supported_by_speech, profile.completed_sessions_count
            )

        sc = max(1, profile.completed_sessions_count)
        profile.mvp_dimensions.retrieval_independence.update_with_evidence(res.retrieval_score, res.retrieval_evidence, sc)
        profile.mvp_dimensions.linguistic_quality.update_with_evidence(res.linguistic_score, res.linguistic_evidence, sc)
        profile.mvp_dimensions.interaction.update_with_evidence(res.interaction_score, res.interaction_evidence, sc)
        profile.mvp_dimensions.register_x_function.update_with_evidence(res.register_score, res.register_evidence, sc)
        profile.mark_calibrated()
        self.repo.save_profile(profile)
        return profile
