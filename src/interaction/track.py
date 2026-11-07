from src.models.interaction_reentry import (
    BackchannelFunction,
    InteractionDimensionScores,
    InteractionSkill,
    SmallTalkTurnEvaluation,
    StorytellingDiagnostic,
)
from src.models.register_scene import SettingType

BACKCHANNELS_BY_FUNCTION: dict[BackchannelFunction, tuple[str, ...]] = {
    "continuer": ("uh-huh", "right", "go on", "yeah"),
    "empathy": ("that's rough", "i can imagine", "man, that's tough", "no wonder"),
    "agreement": ("totally", "spot on", "100%", "fair point", "makes sense"),
    "surprise": ("no way", "seriously?", "you're kidding", "get out of here"),
    "clarification_prompt": ("wait, how so?", "meaning what exactly?", "hold on—which part?"),
}

STORY_MARKERS: dict[str, tuple[str, ...]] = {
    "orientation": ("last week", "a few months ago", "we were at", "when i was working"),
    "complication": ("all of a sudden", "out of nowhere", "the problem was", "unfortunately", "then things went"),
    "development": ("so we tried", "after that", "meanwhile", "ended up calling"),
    "evaluation": ("honestly", "looking back", "the funny thing is", "it was frustrating"),
    "resolution": ("in the end", "finally", "eventually", "worked out", "lesson learned"),
}


class InteractionTrackEngine:
    def __init__(self) -> None:
        self._phrase_history: dict[tuple[int, InteractionSkill], list[tuple[str, SettingType]]] = {}

    def classify_backchannel(self, text: str) -> list[BackchannelFunction]:
        low = text.lower()
        matched: list[BackchannelFunction] = []
        for func, phrases in BACKCHANNELS_BY_FUNCTION.items():
            if any(p in low for p in phrases):
                matched.append(func)
        return matched

    def diagnose_storytelling(self, story_text: str) -> StorytellingDiagnostic:
        """Blueprint 4.5: Diagnostic frame (orientation, complication, development, evaluation, resolution), not a rigid template."""
        low = story_text.lower()
        found = {part: any(m in low for m in markers) for part, markers in STORY_MARKERS.items()}
        missing = [part for part, ok in found.items() if not ok]
        note = (
            "Strong natural narrative arc with personal evaluation."
            if not missing
            else f"Diagnostic observation (not a rigid rule): consider adding {', '.join(missing[:2])} so the listener feels the stakes."
        )
        return StorytellingDiagnostic(
            has_orientation=found["orientation"],
            has_complication=found["complication"],
            has_development=found["development"],
            has_evaluation=found["evaluation"],
            has_resolution=found["resolution"],
            missing_elements=missing,
            diagnostic_note=note,
        )

    def evaluate_small_talk_reciprocity(self, recent_turns: list[str]) -> SmallTalkTurnEvaluation:
        """Blueprint 4.5: Small talk with reciprocal sharing; penalizes interview-style questioning."""
        if not recent_turns:
            return SmallTalkTurnEvaluation()
        latest = recent_turns[-1].strip()
        ends_q = latest.endswith("?")
        low = latest.lower()
        shares_self = any(pron in low for pron in ("i ", "i'm", "i've", "my ", "we ", "for me"))

        consecutive_q_without_sharing = sum(
            1
            for t in recent_turns[-3:]
            if t.strip().endswith("?") and not any(p in t.lower() for p in ("i ", "i'm", "my "))
        )
        penalize = consecutive_q_without_sharing >= 2 or (ends_q and not shares_self and len(recent_turns) >= 2)
        note = (
            "Interview-style questioning detected: share a quick personal detail or reaction before asking your next question."
            if penalize
            else "Good reciprocal balance."
        )
        return SmallTalkTurnEvaluation(
            has_reciprocal_sharing=shares_self,
            ended_with_question=ends_q,
            interview_penalty_applied=penalize,
            feedback_note=note,
        )

    def record_skill_attempt(
        self,
        user_id: int,
        skill: InteractionSkill,
        phrase_used: str,
        register: SettingType,
        scores: InteractionDimensionScores,
    ) -> tuple[bool, str]:
        """Blueprint 4.5: Repeating 'Can I jump in?' is not acquisition. Requires varied phrases + register transfer."""
        norm_phrase = phrase_used.strip().lower().rstrip(".?!")
        history = self._phrase_history.setdefault((user_id, skill), [])
        history.append((norm_phrase, register))

        distinct_phrases = {p for p, _ in history}
        distinct_registers = {r for _, r in history}

        if len(distinct_phrases) == 1 and len(history) >= 2:
            return (
                False,
                f"Formulaic repetition detected ('{phrase_used}'): repeating one fixed phrase is not acquisition. Vary your phrasing for the context.",
            )
        acquired = len(distinct_phrases) >= 2 and len(distinct_registers) >= 2 and scores.average >= 3.0
        status = "Interaction skill acquired across contexts!" if acquired else "Progress recorded—keep varying contexts."
        return acquired, status
