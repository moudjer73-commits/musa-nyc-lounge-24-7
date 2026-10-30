from typing import Any, Optional
import html
import re
import threading
import html
import threading
import re
from typing import Literal, Optional
from schemas import (
    Analysis,
    ConfidenceBand,
    ConversationalAct,
    Diagnostic,
    DialogueDecision,
    InterventionLevel,
    IssueKind,
    PolicyDecision,
    ProductionEvidence,
    RegisterProfile,
    ResponderOutput,
    Severity,
    SurfaceStyle,
    TargetUseAssessment,
    TrainingMode,
)


def _normalize_text_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def validate_analyst_output_against_turn(
    *,
    analysis: Analysis,
    learner_text: str,
    revealed_target_ids: set[str],
    directly_cued_target_ids: set[str],
    active_training_mode: TrainingMode = TrainingMode.OPEN_SCENE,
) -> Analysis:
    norm_learner = _normalize_text_for_match(learner_text)
    verified_diagnostics: list[Diagnostic] = []

    for d in analysis.diagnostics:
        norm_span = _normalize_text_for_match(d.original_span)
        if not norm_span or norm_span not in norm_learner:
            continue
        verified_diagnostics.append(d)

    verified_targets: list[TargetUseAssessment] = []
    for t in analysis.target_assessments:
        norm_match = _normalize_text_for_match(t.matched_span)
        if not norm_match or norm_match not in norm_learner:
            continue

        was_revealed = t.was_revealed_in_task or (t.target_id in revealed_target_ids)
        was_cued = t.was_directly_cued or (t.target_id in directly_cued_target_ids)
        ev = t.evidence

        if active_training_mode != TrainingMode.OPEN_SCENE and ev == ProductionEvidence.SPONTANEOUS_PRODUCTION:
            ev = ProductionEvidence.INDEPENDENT_PRODUCTION

        revalidated = TargetUseAssessment(
            target_id=t.target_id,
            matched_span=t.matched_span,
            was_revealed_in_task=was_revealed,
            was_directly_cued=was_cued,
            fits_communicative_context=t.fits_communicative_context,
            fits_register=t.fits_register,
            phrase_specific_rubric_passed=t.phrase_specific_rubric_passed,
            evidence=ev,
            confidence_band=t.confidence_band,
        )
        verified_targets.append(revalidated)

    return Analysis(
        communicative_intention=analysis.communicative_intention,
        learner_invites_question=analysis.learner_invites_question,
        communicative_goal_achieved=analysis.communicative_goal_achieved,
        pragmatic_cave_in=analysis.pragmatic_cave_in,
        requires_clarification=analysis.requires_clarification,
        in_emotional_or_story_flow=analysis.in_emotional_or_story_flow,
        diagnostics=verified_diagnostics,
        target_assessments=verified_targets,
    )



def compute_learner_strain(recent_learner_turns: list[str], baseline_words: float = 14.0) -> float:
    if not recent_learner_turns:
        return 0.0
    window = recent_learner_turns[-3:]
    word_counts = [len(t.strip().split()) for t in window if t.strip()]
    if not word_counts:
        return 0.0
    avg_len = sum(word_counts) / len(word_counts)
    len_drop = max(0.0, (baseline_words - avg_len) / max(baseline_words, 1.0))

    joined = " ".join(window).lower()
    hesitation_markers = ("...", "uh", "um", "i don't know how to say", "not sure how", "what do you call")
    hes_hits = sum(1 for m in hesitation_markers if m in joined)
    hes_score = min(1.0, hes_hits * 0.25)

    return round(min(1.0, 0.65 * len_drop + 0.35 * hes_score), 2)


def extract_recent_partner_openers(recent_partner_turns: list[str], max_items: int = 5) -> list[str]:
    openers: list[str] = []
    for txt in recent_partner_turns[-6:]:
        cleaned = re.sub(r"^[^\w]+", "", (txt or "").strip())
        words = cleaned.split()
        if not words:
            continue
        first_tok = words[0].rstrip(",.!?;:—-")
        if first_tok and first_tok not in openers:
            openers.append(first_tok)
    return openers[-max_items:]


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


def validate_responder_output(
    *,
    output: ResponderOutput,
    decision: DialogueDecision,
    advisory_recast_candidate: Optional[str] = None,
    stealth_target_form: Optional[str] = None,
) -> ResponderOutput:
    combined = output.reply_text.strip()
    if output.second_bubble_text and output.second_bubble_text.strip():
        combined = f"{combined} {output.second_bubble_text.strip()}"

    words = combined.split()
    if len(words) > decision.max_words:
        raise ValueError(f"Responder output exceeded max_words ({len(words)} > {decision.max_words}).")

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", combined) if s.strip()]
    if len(sentences) > decision.max_sentences:
        raise ValueError(f"Responder output exceeded max_sentences ({len(sentences)} > {decision.max_sentences}).")

    q_count = DialogueController.count_genuine_questions(combined)
    if not decision.allow_question and q_count > 0:
        raise ValueError("Responder included a prohibited question when allow_question=False.")
    if decision.allow_question and q_count > decision.max_questions:
        raise ValueError(f"Responder included {q_count} questions; max_questions={decision.max_questions}.")

    if stealth_target_form:
        norm_stealth = _normalize_text_for_match(stealth_target_form)
        if norm_stealth and norm_stealth in _normalize_text_for_match(combined):
            raise ValueError("Responder leaked the stealth target phrase verbatim.")

    actual_recast_used = False
    if advisory_recast_candidate:
        norm_recast = _normalize_text_for_match(advisory_recast_candidate)
        if norm_recast and norm_recast in _normalize_text_for_match(combined):
            actual_recast_used = True

    return ResponderOutput(
        reply_text=output.reply_text.strip(),
        second_bubble_text=output.second_bubble_text.strip() if output.second_bubble_text else None,
        recast_used=actual_recast_used,
        elicitation_used=bool(output.elicitation_used and stealth_target_form),
    )


def should_schedule_micro_workout(
    *,
    distinct_eligible_occurrences: int,
    learner_opted_in: bool,
    already_scheduled: bool,
    confidence_ok: bool,
    target_is_relevant: bool,
    is_voice_stt_artifact: bool = False,
) -> bool:
    return (
        distinct_eligible_occurrences >= 3
        and learner_opted_in
        and not already_scheduled
        and confidence_ok
        and target_is_relevant
        and not is_voice_stt_artifact
    )


def _is_pattern_relevant_to_goals(
    pattern_id: str,
    issue_kind: IssueKind,
    active_learning_goals: list[str],
    goal_to_patterns_map: Optional[dict[str, set[str]]] = None,
) -> bool:
    if not active_learning_goals:
        return True
    if pattern_id in active_learning_goals or issue_kind.value in active_learning_goals:
        return True
    if goal_to_patterns_map:
        for goal in active_learning_goals:
            if pattern_id in goal_to_patterns_map.get(goal, set()):
                return True
    return False


def decide(
    *,
    analysis: Analysis,
    learner_id: int,
    turns_since_last_visible_card: int,
    authoritative_occurrence_counts: dict[str, int],
    already_counted_in_message_keys: set[str],
    active_learning_goals: list[str],
    goal_to_patterns_map: Optional[dict[str, set[str]]] = None,
    feedback_preference: Literal["silent", "light", "active"] = "light",
    learner_opted_in_workouts: bool = True,
    workout_already_scheduled: bool = False,
    is_voice_message: bool = False,
) -> PolicyDecision:
    reason_codes: list[str] = []

    if analysis.requires_clarification:
        return PolicyDecision(
            intervention=InterventionLevel.NONE,
            clarification_needed=True,
            reason_codes=["no_teaching_intervention_clarification_needed"],
        )

    eligible_diagnostics: list[Diagnostic] = []
    seen_turn_keys: set[str] = set()

    for d in analysis.diagnostics:
        if d.kind in (IssueKind.ACCEPTABLE_VARIATION, IssueKind.INSUFFICIENT_CONTEXT):
            reason_codes.append(f"excluded:{d.diagnostic_id}:{d.kind.value}")
            continue
        if not d.preserves_intended_meaning or not d.context_fit_verified:
            reason_codes.append(f"excluded:{d.diagnostic_id}:failed_validation_gate")
            continue
        if d.confidence_band == ConfidenceBand.LOW or d.confidence_score < 0.65:
            reason_codes.append(f"excluded:{d.diagnostic_id}:low_confidence")
            continue

        ctx_fam = d.relevant_context_family if d.kind in (IssueKind.PRAGMATICS, IssueKind.REGISTER) else "global"
        comp_key = f"{learner_id}:{d.normalized_pattern_id}:{ctx_fam}"

        if comp_key in seen_turn_keys:
            reason_codes.append(f"deduplicated_in_turn:{comp_key}")
            continue

        seen_turn_keys.add(comp_key)
        eligible_diagnostics.append(d)

    if not eligible_diagnostics:
        return PolicyDecision(
            intervention=InterventionLevel.NONE,
            reason_codes=reason_codes or ["no_eligible_diagnostics"],
        )

    error_diagnostics = [
        d for d in eligible_diagnostics if d.kind != IssueKind.OPTIONAL_UPGRADE
    ]

    def _comp_key(d: Diagnostic) -> str:
        ctx_fam = d.relevant_context_family if d.kind in (IssueKind.PRAGMATICS, IssueKind.REGISTER) else "global"
        return f"{learner_id}:{d.normalized_pattern_id}:{ctx_fam}"

    def _projected_distinct_count(d: Diagnostic) -> int:
        ckey = _comp_key(d)
        base = authoritative_occurrence_counts.get(ckey, 0)
        increment = 0 if ckey in already_counted_in_message_keys else 1
        return base + increment

    eligible_recurrence_keys = [
        _comp_key(d) for d in error_diagnostics if _comp_key(d) not in already_counted_in_message_keys
    ]

    def _intervention_rank(d: Diagnostic) -> tuple[int, int, int, int]:
        is_real_error = 1 if d.kind != IssueKind.OPTIONAL_UPGRADE else 0
        sev_w = {Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}[d.severity]
        prag_w = 2 if d.kind in (IssueKind.PRAGMATICS, IssueKind.COLLOCATION) else 1
        rec_c = _projected_distinct_count(d) if is_real_error else 0
        return (is_real_error, sev_w * prag_w, rec_c, int(d.confidence_score * 100))

    ranked_for_intervention = sorted(eligible_diagnostics, key=_intervention_rank, reverse=True)
    top_intervention = ranked_for_intervention[0]

    workout_due = False
    workout_pattern_id: Optional[str] = None
    workout_context_family: Optional[str] = None

    if error_diagnostics:
        ranked_for_workout = sorted(
            error_diagnostics,
            key=lambda d: (_projected_distinct_count(d), int(d.confidence_score * 100)),
            reverse=True,
        )
        for cand in ranked_for_workout:
            proj_count = _projected_distinct_count(cand)
            relevant = _is_pattern_relevant_to_goals(
                cand.normalized_pattern_id,
                cand.kind,
                active_learning_goals,
                goal_to_patterns_map,
            )
            stt_artifact = is_voice_message and cand.kind == IssueKind.GRAMMAR and cand.severity == Severity.LOW
            if should_schedule_micro_workout(
                distinct_eligible_occurrences=proj_count,
                learner_opted_in=learner_opted_in_workouts,
                already_scheduled=workout_already_scheduled,
                confidence_ok=(cand.confidence_band == ConfidenceBand.HIGH),
                target_is_relevant=relevant,
                is_voice_stt_artifact=stt_artifact,
            ):
                workout_due = True
                workout_pattern_id = cand.normalized_pattern_id
                workout_context_family = (
                    cand.relevant_context_family
                    if cand.kind in (IssueKind.PRAGMATICS, IssueKind.REGISTER)
                    else "global"
                )
                break

    if feedback_preference == "silent":
        return PolicyDecision(
            intervention=InterventionLevel.DEFER_TO_REVIEW,
            reason_codes=reason_codes + ["silent_preference_deferred_to_review"],
            selected_diagnostic_id=top_intervention.diagnostic_id,
            workout_candidate_pattern_id=workout_pattern_id,
            workout_candidate_context_family=workout_context_family,
            eligible_recurrence_keys=eligible_recurrence_keys,
            schedule_micro_workout=workout_due,
            defer_workout_until_scene_end=True,
        )

    if analysis.in_emotional_or_story_flow:
        recast_val = top_intervention.recast_candidate or top_intervention.alternative
        return PolicyDecision(
            intervention=InterventionLevel.IMPLICIT_RECAST if recast_val else InterventionLevel.DEFER_TO_REVIEW,
            reason_codes=reason_codes + ["story_flow_protected"],
            selected_diagnostic_id=top_intervention.diagnostic_id,
            workout_candidate_pattern_id=workout_pattern_id,
            workout_candidate_context_family=workout_context_family,
            advisory_recast_candidate=recast_val,
            eligible_recurrence_keys=eligible_recurrence_keys,
            schedule_micro_workout=workout_due,
            defer_workout_until_scene_end=True,
        )

    if top_intervention.kind == IssueKind.OPTIONAL_UPGRADE and feedback_preference != "active":
        recast_val = top_intervention.recast_candidate or top_intervention.alternative
        return PolicyDecision(
            intervention=InterventionLevel.IMPLICIT_RECAST if recast_val else InterventionLevel.NONE,
            reason_codes=reason_codes + ["optional_upgrade_implicit_only"],
            selected_diagnostic_id=top_intervention.diagnostic_id,
            workout_candidate_pattern_id=workout_pattern_id,
            workout_candidate_context_family=workout_context_family,
            advisory_recast_candidate=recast_val,
            eligible_recurrence_keys=eligible_recurrence_keys,
            schedule_micro_workout=workout_due,
            defer_workout_until_scene_end=not workout_due,
        )

    min_card_gap = 2 if feedback_preference == "active" else 4
    card_budget_available = turns_since_last_visible_card >= min_card_gap
    recast_val = top_intervention.recast_candidate or top_intervention.alternative

    if card_budget_available and (
        top_intervention.severity in (Severity.HIGH, Severity.MEDIUM)
        or feedback_preference == "active"
    ):
        return PolicyDecision(
            intervention=InterventionLevel.BRIEF_CARD,
            reason_codes=reason_codes + ["budget_available_brief_card_selected"],
            selected_diagnostic_id=top_intervention.diagnostic_id,
            workout_candidate_pattern_id=workout_pattern_id,
            workout_candidate_context_family=workout_context_family,
            advisory_recast_candidate=recast_val,
            eligible_recurrence_keys=eligible_recurrence_keys,
            schedule_micro_workout=workout_due,
            defer_workout_until_scene_end=False,
        )

    if recast_val:
        return PolicyDecision(
            intervention=InterventionLevel.IMPLICIT_RECAST,
            reason_codes=reason_codes + ["card_budget_capped_using_advisory_recast"],
            selected_diagnostic_id=top_intervention.diagnostic_id,
            workout_candidate_pattern_id=workout_pattern_id,
            workout_candidate_context_family=workout_context_family,
            advisory_recast_candidate=recast_val,
            eligible_recurrence_keys=eligible_recurrence_keys,
            schedule_micro_workout=workout_due,
            defer_workout_until_scene_end=True,
        )

    return PolicyDecision(
        intervention=InterventionLevel.DEFER_TO_REVIEW,
        reason_codes=reason_codes + ["deferred_to_review"],
        selected_diagnostic_id=top_intervention.diagnostic_id,
        workout_candidate_pattern_id=workout_pattern_id,
        workout_candidate_context_family=workout_context_family,
        eligible_recurrence_keys=eligible_recurrence_keys,
        schedule_micro_workout=workout_due,
        defer_workout_until_scene_end=True,
    )


def normalize_arabic_english_formatting(text: Optional[str]) -> Optional[str]:
    """Removes guillemets « » or parentheses ( ) wrapping pure Latin/English spans inside Arabic notes."""
    if not text:
        return text
    cleaned = re.sub(r"«\s*([A-Za-z0-9][A-Za-z0-9\s'\-/.,?!]*?)\s*»", r"\1", str(text))
    cleaned = re.sub(r"\(\s*([A-Za-z0-9][A-Za-z0-9\s'\-/.,?!]*?)\s*\)", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


_DECODE_STATES: dict[str, dict[str, Any]] = {}
_DECODE_LOCK = threading.Lock()

_WORKOUT_TASKS: dict[str, dict[str, Any]] = {}
_WORKOUT_LOCK = threading.Lock()


def start_decode_session(learner_key: str, challenge: Any, chat_key: Optional[str] = None) -> dict[str, Any]:
    with _DECODE_LOCK:
        state = {
            "phase": "awaiting_learner",
            "challenge": challenge,
        }
        _DECODE_STATES[str(learner_key)] = state
        if chat_key:
            _DECODE_STATES[str(chat_key)] = state
        return dict(state)


def get_active_decode_session(key: str) -> Optional[dict[str, Any]]:
    with _DECODE_LOCK:
        st = _DECODE_STATES.get(str(key))
        if st and st.get("phase") == "awaiting_learner":
            return dict(st)
        return None


def complete_decode_session(key: str) -> Optional[dict[str, Any]]:
    with _DECODE_LOCK:
        st = _DECODE_STATES.get(str(key))
        if st:
            st["phase"] = "completed"
            return dict(st)
        return None


def format_decode_phase1_message(challenge: Any, register_label: str = "CASUAL") -> str:
    """Phase 1: Sends ONLY context, spoken clip, and comprehension question. Zero answer/IPA leakage."""
    return (
        f"🎧 <b>Listen &amp; Decode</b> (Register: {html.escape(str(register_label).upper())})\n"
        f"• <b>Context:</b> {html.escape(challenge.context_line)}\n"
        f"• <b>Spoken Clip:</b> “{html.escape(challenge.spoken_clip_text)}”\n\n"
        f"👉 <b>{html.escape(challenge.decode_question)}</b>\n\n"
        f"<i>Reply with what you think the speaker really means, or tap the button below to reveal the breakdown.</i>"
    )


def format_decode_phase2_reveal(challenge: Any, eval_res: Optional[Any] = None) -> str:
    """Phase 2: Reveals comprehension feedback + full unpacked form, IPA, and subtext AFTER attempt."""
    subtext_ar = normalize_arabic_english_formatting(challenge.pragmatic_subtext_ar) or challenge.pragmatic_subtext_ar
    header = ""
    if eval_res is not None:
        icon = "✅" if getattr(eval_res, "comprehension_accurate", True) else "💡"
        fb_ar = normalize_arabic_english_formatting(getattr(eval_res, "coach_feedback_ar", "")) or ""
        header = (
            f"{icon} <b>Decoding Feedback:</b> {html.escape(eval_res.coach_feedback_en)}\n"
            + (f"• <b>بالعربية:</b> {html.escape(fb_ar)}\n" if fb_ar else "")
            + "━━━━━━━━━━━━━━━━━━━━\n"
        )
    return (
        f"{header}"
        f"🔍 <b>Sound &amp; Subtext Breakdown:</b>\n"
        f"• <b>Unpacked Form:</b> {html.escape(challenge.full_unreduced_form)} — {html.escape(challenge.acoustic_reduction_note)}\n"
        f"• <b>Hidden Subtext (EN):</b> {html.escape(challenge.pragmatic_subtext_en)}\n"
        f"• <b>المعنى المبطن (AR):</b> {html.escape(subtext_ar)}"
    )


def register_workout_task(scenario: Any, target_info: dict[str, Any], learner_id: Optional[str] = None) -> dict[str, Any]:
    with _WORKOUT_LOCK:
        task = {
            "communicative_intention": getattr(scenario, "communicative_intention", target_info.get("communicative_intention", "")),
            "situation_setup": getattr(scenario, "situation_setup", ""),
            "prompt_question": getattr(scenario, "prompt_question", ""),
            "forbidden_form": target_info.get("canonical_form", ""),
            "target_id": target_info.get("target_id", ""),
            "attempts": 0,
            "max_attempts": 2,
            "status": "ACTIVE",
            "reinforce_required": False,
        }
        if learner_id:
            _WORKOUT_TASKS[str(learner_id)] = task
        _WORKOUT_TASKS["_latest"] = task
        return dict(task)


def get_active_workout_task(learner_id: str) -> Optional[dict[str, Any]]:
    with _WORKOUT_LOCK:
        task = _WORKOUT_TASKS.get(str(learner_id)) or _WORKOUT_TASKS.get("_latest")
        if task and task.get("status") == "ACTIVE":
            _WORKOUT_TASKS[str(learner_id)] = task
            return dict(task)
        return None


def update_workout_task_after_turn(learner_id: str, goal_achieved: bool) -> dict[str, Any]:
    with _WORKOUT_LOCK:
        task = _WORKOUT_TASKS.get(str(learner_id)) or _WORKOUT_TASKS.get("_latest")
        if not task or task.get("status") != "ACTIVE":
            return {"task_status": "NONE", "reinforce_required": False, "attempts": 0}
        task["attempts"] = int(task.get("attempts", 0)) + 1
        if goal_achieved:
            task["status"] = "COMPLETED"
            task["reinforce_required"] = False
            return {"task_status": "FULFILLED", "reinforce_required": False, "attempts": task["attempts"]}
        if task["attempts"] >= int(task.get("max_attempts", 2)):
            task["status"] = "COMPLETED_UNFULFILLED"
            task["reinforce_required"] = False
            return {"task_status": "EXHAUSTED", "reinforce_required": False, "attempts": task["attempts"]}
        task["reinforce_required"] = True
        return {"task_status": "MISSED", "reinforce_required": True, "attempts": task["attempts"]}


BOT_COMMANDS_SPEC: list[tuple[str, str]] = [
    ("scene", "🎬 Start a fresh spoken scene"),
    ("workout", "🏋️ Spontaneous phrase retrieval workout"),
    ("dilemma", "🤝 High-stakes pragmatic roleplay"),
    ("decode", "🎧 Two-phase connected speech & subtext challenge"),
    ("register", "🎛️ Spoken register shift (Casual / Diplomatic / Executive)"),
    ("settings", "⚙️ Live control panel (Register, Directness, Coaching)"),
    ("debrief", "🏁 End-of-scene debrief & top shifts"),
    ("progress", "📈 Mastery lifecycle & cross-register stats"),
    ("guide", "📖 Updated v2 User Guide (EN / AR)"),
    ("start", "🟢 Open lab & restore keyboard"),
]


def get_updated_user_guide_html() -> str:
    return (
        "📖 <b>Spoken English Lab — v2 User Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎙️ <b>1. Instant Dual-Engine Conversation</b>\n"
        "Talk naturally with <b>Alex</b>. Replies arrive in under a second while the silent analyst attaches a "
        "<b>💡 Quick Upgrade</b> button only when a real spoken upgrade exists.\n"
        "• <b>🌤️ Light:</b> Paced cards with cooldown.\n"
        "• <b>🔬 Active:</b> Instant upgrade card on every turn with an issue.\n\n"
        "🏋️ <b>2. Phrase Workout (Task-Aware)</b>\n"
        "Tests spontaneous retrieval in a fresh scenario without leaking the target phrase. Any natural American phrasing "
        "that fulfills the goal counts! If you drift off-task, Alex will naturally re-open the opportunity in-character.\n\n"
        "🎧 <b>3. Listen &amp; Decode (2-Phase Interactive)</b>\n"
        "• <b>Phase 1:</b> Listen to the reduced American clip (<i>Whaddaya, Mighta, Didja</i>) and reply with what the speaker <i>really</i> means.\n"
        "• <b>Phase 2:</b> Get instant comprehension feedback plus the full unpacked form, IPA, and Arabic/English subtext (or tap <b>🔍 Reveal Breakdown</b> anytime).\n\n"
        "🎛️ <b>4. Spoken-First Register Shift</b>\n"
        "Send any thought in <b>🎛️ Register Shift</b> to hear how Americans say it out loud across <b>Casual</b>, "
        "<b>Diplomatic</b>, and crisp <b>Executive</b> meeting speech—never stiff written memo prose.\n\n"
        "📈 <b>5. Cross-Register Mastery Lifecycle</b>\n"
        "Phrases progress from <code>EXPOSED → ATTEMPTED → CONSOLIDATING → STABLE</code>. Reaching <b>STABLE</b> requires "
        "using the phrase naturally across at least <b>2 formality bands</b> (Informal / Neutral / Executive) with 24h spacing.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>ملخص سريع بالعربية:</b>\n"
        "• اضغط على <b>💡 Quick Upgrade</b> أسفل رد Alex لعرض بطاقة التصحيح والأثر السماعي الأمريكي.\n"
        "• في <b>🎧 Listen &amp; Decode</b> أرسل تفسيرك للمعنى المبطن أولاً ليقيمه البوت ويكشف التفكيك الصوتي.\n"
        "• استخدم زر القائمة <b>( / )</b> أسفل الشاشة للوصول السريع لجميع الأوامر في أي وقت."
    )

