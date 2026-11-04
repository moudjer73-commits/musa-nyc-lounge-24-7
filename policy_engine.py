"""policy_engine.py (V3.0 status, owner Claude).

S5: the v2 policy engine is gone. ``decide`` and ``validate_analyst_output_against_turn`` were superseded by
``feedback_policy.resolve_interaction_policy`` and ``validation.validate_analysis``; ``DialogueController`` moved
unchanged to ``dialogue.py`` (it builds the Responder brief, it is not policy); the process-memory auto-voice
preference moved to ``learner_settings`` (``EventSourcedDatabase.get_auto_voice`` / ``set_auto_voice``).

What is left are small helpers that the orchestrator and the v2 modes (workout, decode) still use:
``compute_learner_strain`` (the strain input of ``feedback_policy``), ``validate_responder_output``,
``extract_recent_partner_openers``, the decode and workout session registries and ``extract_alex_spoken_text``.

Do not add new policy logic here: new rules go to ``feedback_policy.py`` or ``validation.py``.
"""
import html
import re
import threading
from typing import Any, Optional

from schemas import DialogueDecision, ResponderOutput


def _dialogue_controller():
    """The Responder-brief builder lives in dialogue.py (S5); imported lazily so this module never owns it."""
    from dialogue import DialogueController

    return DialogueController


def _normalize_text_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


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

    q_count = _dialogue_controller().count_genuine_questions(combined)
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


def extract_alex_spoken_text(raw_text: Optional[str]) -> str:
    """Extracts only Alex's spoken English line, stripping prefixes, dilemma headers, and Layer-2 cards."""
    if not raw_text:
        return ""
    top_part = str(raw_text).split("━━━━━━━━━━━━━━━━━━━━", 1)[0].strip()
    if "💬 Alex:" in top_part:
        top_part = top_part.split("💬 Alex:", 1)[1].strip()
    elif "Alex:" in top_part:
        top_part = top_part.split("Alex:", 1)[1].strip()
    return top_part.strip().strip('“”"\'').strip()

