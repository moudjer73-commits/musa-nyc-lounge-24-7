from src.models.pressure_tracking import MonologueChallenge, SelfRepairState
from src.models.profile import LearnerProfile
from src.models.register_scene import LearningStage, SceneConfig, SettingType
from src.register.engine import RegisterEngine

# Blueprint 4.9: Neutral topics only (strictly zero politics, zero religion)
SAFE_NEUTRAL_MONOLOGUE_TOPICS: tuple[str, ...] = (
    "Whether remote work helps or hurts deep creative problem-solving",
    "The most overrated habit in modern productivity culture",
    "Designing a neighborhood that encourages walking instead of driving",
    "Why some technical skills age quickly while others stay relevant for decades",
    "Balancing speed versus thoroughness when learning a complex craft",
    "How public libraries should adapt over the next twenty years",
)

FORBIDDEN_TOPIC_KEYWORDS = ("politic", "election", "government", "president", "religio", "theolog", "sect")

STAGE_MONOLOGUE_THRESHOLDS: dict[LearningStage, tuple[int, float]] = {
    "S1": (90, 0.30),
    "S2": (110, 0.25),
    "S3": (130, 0.20),
    "S4": (150, 0.15),
    "S5": (165, 0.12),
}


class PressureTestEngine:
    def __init__(self, reg_engine: RegisterEngine | None = None) -> None:
        self.reg_engine = reg_engine or RegisterEngine()
        self._monologues: dict[int, MonologueChallenge] = {}
        self._self_repair: dict[int, SelfRepairState] = {}

    def _filter_safe_topics(self, profile: LearnerProfile, skipped: list[str]) -> list[str]:
        avoid_terms = [t.lower() for t in profile.topics_to_avoid] + list(FORBIDDEN_TOPIC_KEYWORDS)
        candidates = []
        for topic in SAFE_NEUTRAL_MONOLOGUE_TOPICS:
            low = topic.lower()
            if topic not in skipped and not any(term in low for term in avoid_terms):
                candidates.append(topic)
        return candidates or [SAFE_NEUTRAL_MONOLOGUE_TOPICS[0]]

    def start_90s_monologue(self, profile: LearnerProfile, stage: LearningStage = "S1") -> MonologueChallenge:
        min_words, max_pause = STAGE_MONOLOGUE_THRESHOLDS.get(stage, (110, 0.25))
        topics = self._filter_safe_topics(profile, skipped=[])
        challenge = MonologueChallenge(
            user_id=profile.user_id,
            topic=topics[0],
            min_words_threshold=min_words,
            max_hesitation_Pause_ratio=max_pause,
        )
        self._monologues[profile.user_id] = challenge
        return challenge

    def skip_monologue_topic(self, profile: LearnerProfile) -> MonologueChallenge:
        current = self._monologues.get(profile.user_id) or self.start_90s_monologue(profile)
        current.skipped_topics.append(current.topic)
        topics = self._filter_safe_topics(profile, skipped=current.skipped_topics)
        current.topic = topics[0]
        return current

    def trigger_live_register_switch(self, current_scene: SceneConfig, target_setting: SettingType) -> SceneConfig:
        new_rel = "hierarchical" if target_setting in ("professional", "intellectual") else "close"
        event = self.reg_engine.switch_scene(
            current=current_scene,
            mode="c_mid_conversation_shift",
            new_setting=target_setting,
            new_relationship=new_rel,
        )
        return event.new_scene

    def handle_self_repair_turn(self, user_id: int, learner_text: str, has_slip_or_stall: bool) -> tuple[bool, str]:
        """Blueprint 4.9 Self-repair test: Alex waits before offering any help."""
        state = self._self_repair.setdefault(user_id, SelfRepairState(user_id=user_id))
        if has_slip_or_stall and not state.waited_once:
            state.waited_once = True
            state.stalled_span = learner_text
            return True, "..."  # Alex waits silently on first stall/slip so learner can self-repair
        state.waited_once = False
        return False, "Got it—let's keep building on that point."
