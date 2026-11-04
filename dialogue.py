"""dialogue.py (V3.0, owner Claude): the per-turn Responder brief.

``DialogueController`` moved here unchanged from ``policy_engine.py`` in S5. It is not policy: it picks the
conversational act, the surface style and the length limits that the Responder is asked to perform, and counts
the genuine questions in a reply. The feedback decision (what the learner is shown, and when) is
``feedback_policy.resolve_interaction_policy``.
"""
import re

from schemas import (
    Analysis,
    ConversationalAct,
    DialogueDecision,
    RegisterProfile,
    SurfaceStyle,
    TrainingMode,
)


class DialogueController:
    @staticmethod
    def strip_quoted_speech(text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r'"[^"]*"|“[^”]*”', " ", text)
        cleaned = re.sub(r"(?:^|(?<=\s))'[^']*'(?=\s|[.,!?;:]|$)", " ", cleaned)
        return cleaned

    @classmethod
    def count_genuine_questions(cls, text: str) -> int:
        cleaned = cls.strip_quoted_speech(text)
        segments = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if seg.strip()]
        q_segments = [seg for seg in segments if seg.endswith("?")]
        return len(q_segments)

    @staticmethod
    def _choose_surface_style(register: RegisterProfile, word_count: int) -> SurfaceStyle:
        if word_count <= 4 and register == RegisterProfile.CASUAL:
            return SurfaceStyle.FRAGMENT
        mapping = {
            RegisterProfile.CASUAL: SurfaceStyle.PLAIN,
            RegisterProfile.WARM: SurfaceStyle.PLAIN,
            RegisterProfile.WITTY: SurfaceStyle.HUMOROUS,
            RegisterProfile.DIPLOMATIC: SurfaceStyle.HEDGED,
            RegisterProfile.EXECUTIVE: SurfaceStyle.CONCISE,
            RegisterProfile.ANALYTICAL: SurfaceStyle.PLAIN,
        }
        return mapping.get(register, SurfaceStyle.PLAIN)

    @classmethod
    def choose_contextually_appropriate_act(
        cls,
        *,
        learner_text: str,
        analysis: Analysis,
        training_mode: TrainingMode,
    ) -> ConversationalAct:
        if analysis.requires_clarification:
            return ConversationalAct.CLARIFY

        if analysis.in_emotional_or_story_flow:
            return ConversationalAct.REACT

        word_count = len((learner_text or "").strip().split())
        if word_count <= 4 and "?" not in (learner_text or ""):
            return ConversationalAct.ACKNOWLEDGE

        if training_mode == TrainingMode.THINK_ALOUD:
            return ConversationalAct.CHALLENGE

        question_inviting_intentions = {
            "seek_advice",
            "ask_opinion",
            "propose_plan",
            "open_inquiry",
            "decision_dilemma",
        }
        if analysis.learner_invites_question or analysis.communicative_intention in question_inviting_intentions:
            return ConversationalAct.ASK

        if analysis.communicative_intention in {"share_story", "express_emotion", "joke", "react"}:
            return ConversationalAct.REACT

        return ConversationalAct.CONTRIBUTE

    @classmethod
    def select_dialogue_decision(
        cls,
        *,
        learner_text: str,
        analysis: Analysis,
        recent_optional_question_flags: list[bool],
        register: RegisterProfile = RegisterProfile.CASUAL,
        training_mode: TrainingMode = TrainingMode.OPEN_SCENE,
    ) -> DialogueDecision:
        word_count = len((learner_text or "").strip().split())
        style = cls._choose_surface_style(register, word_count)
        act = cls.choose_contextually_appropriate_act(
            learner_text=learner_text,
            analysis=analysis,
            training_mode=training_mode,
        )

        if act == ConversationalAct.CLARIFY:
            return DialogueDecision(
                act=ConversationalAct.CLARIFY,
                style=SurfaceStyle.PLAIN,
                allow_question=True,
                max_questions=1,
                max_sentences=2,
                max_words=35,
                reason_code="clarification_required",
            )

        window = recent_optional_question_flags[-10:]
        last_was_question = bool(window[-1]) if window else False
        if len(window) >= 4:
            q_rate = sum(1 for q in window if q) / len(window)
            budget_permits = (q_rate < 0.30) and (not last_was_question)
        else:
            budget_permits = not last_was_question

        if act == ConversationalAct.ASK:
            question_is_useful = analysis.learner_invites_question or (
                analysis.communicative_intention in {"seek_advice", "ask_opinion", "propose_plan", "open_inquiry", "decision_dilemma"}
            )
            if question_is_useful and budget_permits:
                return DialogueDecision(
                    act=ConversationalAct.ASK,
                    style=style,
                    allow_question=True,
                    max_questions=1,
                    max_sentences=2,
                    max_words=42,
                    reason_code="contextual_question_permitted",
                )
            act = ConversationalAct.CONTRIBUTE

        return DialogueDecision(
            act=act,
            style=style,
            allow_question=False,
            max_questions=0,
            max_sentences=2,
            max_words=40,
            reason_code=f"declarative_{act.value}",
        )
