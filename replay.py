"""replay.py (V4.0): Pragmatic Replay Studio for C1-C2 Strategic Recalibration."""

from __future__ import annotations

import html
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

import retrieval
from database import EventSourcedDatabase

logger = logging.getLogger(__name__)

ReplayAngle = Literal["diplomatic", "executive", "witty"]

ANGLE_META: dict[ReplayAngle, dict[str, str]] = {
    "diplomatic": {
        "label": "🕊️ Diplomatic Pushback",
        "register": "diplomatic",
        "desc": "Hold your ground warmly without caving in or sounding abrasive.",
    },
    "executive": {
        "label": "🎯 Crisp Executive",
        "register": "executive",
        "desc": "High-status, concise, zero fluff—state the boundary or decision cleanly.",
    },
    "witty": {
        "label": "⚡ Warm & Witty",
        "register": "witty",
        "desc": "Disarm tension with natural American levity while keeping your point clear.",
    },
}


class ReplayModelOption(BaseModel):
    angle: ReplayAngle = "diplomatic"
    model_move: str = Field(..., min_length=4, max_length=240)
    key_chunk: str = Field(..., min_length=2, max_length=80)
    listener_effect_en: str = Field(..., min_length=5, max_length=220)
    pragmatic_note_ar: str = Field(..., min_length=5, max_length=200)


@dataclass
class ActiveReplaySession:
    learner_id: int
    session_id: str
    partner_line: str
    original_learner_turn: str
    communicative_goal: str
    selected_angle: Optional[ReplayAngle] = None
    options: dict[str, ReplayModelOption] = field(default_factory=dict)
    last_attempt_text: Optional[str] = None


_REPLAY_LOCK = threading.Lock()
_ACTIVE_REPLAYS: dict[int, ActiveReplaySession] = {}


REPLAY_SYSTEM_PROMPT = """You are an elite American English pragmatic coach (C1-C2).
Given a learner's conversation turn (`original_learner_turn`) and the partner context (`partner_line`), generate a model native American version for the requested `angle` (`diplomatic`, `executive`, or `witty`).
Rules:
1. Preserve the learner's real communicative intent (including annoyance, refusal, or pushback—never flatten their stance!).
2. `key_chunk` MUST be a 2-to-5 word reusable spoken chunk that appears verbatim inside `model_move`.
3. Return ONLY valid JSON:
{
  "angle": "diplomatic|executive|witty",
  "model_move": "1-2 crisp natural American spoken sentences",
  "key_chunk": "reusable chunk from model_move",
  "listener_effect_en": "1 sentence on how this lands on an American ear",
  "pragmatic_note_ar": "شرح تداولي موجز بالعربية الفصحى يوضح أثر هذه الصياغة"
}"""


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def close_replay_session(learner_id: int) -> bool:
    with _REPLAY_LOCK:
        return _ACTIVE_REPLAYS.pop(int(learner_id), None) is not None


def get_replay_session(learner_id: int) -> Optional[ActiveReplaySession]:
    with _REPLAY_LOCK:
        return _ACTIVE_REPLAYS.get(int(learner_id))


def extract_latest_replay_moment(db: EventSourcedDatabase, learner_id: int) -> ActiveReplaySession:
    sess = db.get_or_create_active_session(learner_id)
    sid = str(sess["session_id"])
    history = db.get_recent_dialogue_history(sid, limit=8)

    learner_turn = ""
    partner_line = "Alex: Let's figure out how we want to handle the workload this week."
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "learner" and len(str(history[i].get("text", "")).strip()) > 6:
            learner_turn = str(history[i]["text"]).strip()
            for j in range(i - 1, -1, -1):
                if history[j].get("role") == "partner":
                    partner_line = str(history[j]["text"]).strip()
                    break
            break

    if not learner_turn:
        learner_turn = "Yes I am always having really tough work to do and nobody notices."
        partner_line = "Have you been keeping pretty busy lately too?"

    goal = "Express your stance with natural American pragmatic control"
    with db.read() as conn:
        last_diag = conn.execute(
            """
            SELECT kind, original_span, alternative, explanation_en
            FROM feedback_item_projection
            WHERE learner_id = ?
            ORDER BY delivered_at_utc DESC, diagnostic_id DESC LIMIT 1
            """,
            (learner_id,),
        ).fetchone()
    if last_diag and last_diag["alternative"]:
        goal = f"Pivot from '{last_diag['original_span']}' toward '{last_diag['alternative']}' ({last_diag['kind']})"

    replay_sess = ActiveReplaySession(
        learner_id=int(learner_id),
        session_id=sid,
        partner_line=partner_line,
        original_learner_turn=learner_turn,
        communicative_goal=goal,
    )
    with _REPLAY_LOCK:
        _ACTIVE_REPLAYS[int(learner_id)] = replay_sess
    return replay_sess


def _deterministic_replay_option( moment: ActiveReplaySession, angle: ReplayAngle) -> ReplayModelOption:
    presets: dict[ReplayAngle, ReplayModelOption] = {
        "diplomatic": ReplayModelOption(
            angle="diplomatic",
            model_move="To be fair, I'm always getting pulled into the heavy-lifting tasks, so we really need to rebalance the load.",
            key_chunk="rebalance the load",
            listener_effect_en="Signals firm frustration without sounding combative—invites collaboration on a solution.",
            pragmatic_note_ar="تحافظ على نبرة الضيق من تراكم العمل مع توجيه الحوار دبلوماسياً نحو إعادة توزيع المهام.",
        ),
        "executive": ReplayModelOption(
            angle="executive",
            model_move="Right now I'm consistently taking on the heaviest deliverables, and that setup isn't sustainable going forward.",
            key_chunk="isn't sustainable going forward",
            listener_effect_en="High-status executive framing: frames personal overload as an operational risk.",
            pragmatic_note_ar="تنقل الاعتراض من شكوى شخصية إلى تقييم تنفيذي حازم بأن الوضع الحالي غير قابل للاستمرار.",
        ),
        "witty": ReplayModelOption(
            angle="witty",
            model_move="Let's just say if tough assignments were a sport, I'd be in the hall of fame by now—I'm always getting stuck with the wild ones.",
            key_chunk="getting stuck with",
            listener_effect_en="Uses dry American humor to vent annoyance while keeping the vibe relaxed and relatable.",
            pragmatic_note_ar="تستخدم الدعابة الأمريكية الذكية للتعبير عن التذمر (`always getting stuck with`) دون ثقل.",
        ),
    }
    return presets.get(angle, presets["diplomatic"])


def build_replay_angle_option(ai: Any, moment: ActiveReplaySession, angle: ReplayAngle) -> ReplayModelOption:
    if angle in moment.options:
        return moment.options[angle]

    payload = json.dumps(
        {
            "angle": angle,
            "angle_description": ANGLE_META[angle]["desc"],
            "partner_line": moment.partner_line,
            "original_learner_turn": moment.original_learner_turn,
            "communicative_goal": moment.communicative_goal,
        },
        ensure_ascii=False,
    )

    def _validate(raw: dict[str, Any]) -> ReplayModelOption:
        raw["angle"] = angle
        return ReplayModelOption.model_validate(raw)

    opt: Optional[ReplayModelOption] = None
    if ai is not None and hasattr(ai, "_call_json"):
        try:
            res = ai._call_json(
                "debrief",
                REPLAY_SYSTEM_PROMPT,
                payload,
                _validate,
                deadline_s=14.0,
                temperature=0.5,
            )
            opt = res.parsed
        except Exception as err:
            logger.warning("Replay LLM call fell back to deterministic preset: %s", err)

    if opt is None:
        opt = _deterministic_replay_option(moment, angle)
    moment.options[angle] = opt
    return opt


def render_replay_hub_html(moment: ActiveReplaySession) -> str:
    return "\n".join(
        [
            "🎭 <b>Pragmatic Replay Studio</b>",
            "Recalibrate a real moment from your scene across 3 American strategic registers:",
            "",
            f"💬 <b>Context:</b> “{_e(moment.partner_line)}”",
            f"🗣️ <b>Your Original Move:</b> “{_e(moment.original_learner_turn)}”",
            f"🎯 <b>Communicative Goal:</b> {_e(moment.communicative_goal)}",
            "",
            "👇 <b>Choose a strategic angle to see how a native speaker pivots this exact move:</b>",
        ]
    )


def render_replay_angle_html(moment: ActiveReplaySession, opt: ReplayModelOption, *, with_arabic: bool = True) -> str:
    meta = ANGLE_META[opt.angle]
    lines = [
        f"{meta['label']} — <b>Pragmatic Replay</b>",
        f"• <b>Your Original:</b> “{_e(moment.original_learner_turn)}”",
        f"• <b>Model Native Move:</b> “{_e(opt.model_move)}”",
        f"• <b>Power Chunk:</b> <code>{_e(opt.key_chunk)}</code>",
        f"• <b>Listener Effect:</b> {_e(opt.listener_effect_en)}",
    ]
    if with_arabic and opt.pragmatic_note_ar:
        lines.append(f"• <b>بالعربية:</b> <tg-spoiler>{_e(opt.pragmatic_note_ar)}</tg-spoiler>")
    lines.extend(
        [
            "",
            f"✍️ <b>Your Turn:</b> Type your own {meta['register'].upper()} version in your own words (try weaving in <code>{_e(opt.key_chunk)}</code>):",
        ]
    )
    return "\n".join(lines)


def evaluate_replay_attempt(moment: ActiveReplaySession, learner_text: str, judge: Any = None, *, with_arabic: bool = True) -> tuple[str, bool]:
    angle = moment.selected_angle or "diplomatic"
    opt = moment.options.get(angle) or _deterministic_replay_option(moment, angle)
    attempt = learner_text.strip()

    if attempt.lower() == opt.model_move.strip().lower():
        return (
            "🔄 That is the exact model sentence word-for-word. Put the same stance into your own sentence so it sticks!",
            False,
        )

    res = retrieval.grade_reconstruction(
        attempt,
        opt.key_chunk,
        shown_texts=[moment.original_learner_turn],
        judge=judge,
        communicative_intention=moment.communicative_goal,
    )
    moment.last_attempt_text = attempt
    passed = res.outcome is not None and res.outcome.value in ("success", "partial")
    verdict = "✅ <b>Sharp Pragmatic Pivot!</b>" if passed else "💡 <b>Solid Effort — Here is the Target Anchor:</b>"
    lines = [
        verdict,
        f"• <b>Your Replay:</b> “{_e(attempt)}”",
        f"• <b>Target Power Chunk:</b> <code>{_e(opt.key_chunk)}</code>",
        f"• <b>Reference Move:</b> “{_e(opt.model_move)}”",
        "",
        "Tap <b>📨 Send to Alex</b> to inject your new line directly into the live conversation, or pick another angle:",
    ]
    return "\n".join(lines), True
