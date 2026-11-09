import inspect
import logging
from pydantic import BaseModel
from src.alex._orig_responder import _OrigAlexResponder
from src.db.scene_repo import SceneRepository
from src.models.register_scene import SceneConfig
from src.naturalness import NaturalnessGauge, NaturalnessGuard, NaturalnessRouter
from src.quality.guard import QualityGuard

logger = logging.getLogger(__name__)


class AlexLLMOutput(BaseModel):
    reply: str
    used_recast: bool = False


def _extract_bundle_prompt(bundle) -> str:
    for attr in ("system_prompt", "prompt_block", "system_block", "prompt_addition", "rendered_prompt", "prompt", "instructions"):
        val = getattr(bundle, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    if hasattr(bundle, "__dict__"):
        for k, v in vars(bundle).items():
            if isinstance(v, str) and k not in ("cast_name", "situation_name", "profile_id", "room") and len(v) > 20:
                return v
    return "You are a natural American English conversation partner."


class AlexResponder:
    def __init__(
        self,
        scene_repo: SceneRepository,
        llm,
        guard: QualityGuard | None = None,
    ) -> None:
        self.scene_repo = scene_repo
        self.llm = llm
        self.guard = guard or QualityGuard()
        self.nat_router = NaturalnessRouter()
        self.nat_guard = NaturalnessGuard()
        self.nat_gauge = NaturalnessGauge()
        self._user_turn_counts: dict[int, int] = {}
        self.last_segments: dict[int, list[str]] = {}
        self._orig = _OrigAlexResponder(scene_repo=scene_repo, llm=llm, guard=self.guard)

    def build_system_prompt(self, scene: SceneConfig, recast_phrase: str | None = None, user_id: int = 0, user_text: str = "") -> str:
        is_live_gemini = (self.llm.__class__.__name__ == "GeminiService" and bool(getattr(self.llm, "clients", None)))
        if not is_live_gemini:
            return self._orig.build_system_prompt(scene, recast_phrase=recast_phrase)
        turn_no = self._user_turn_counts.get(user_id, 0) + 1
        bundle = self.nat_router.route(
            user_id=user_id,
            turn_no=turn_no,
            user_text=user_text,
            room=scene.room_id,
        )
        base = _extract_bundle_prompt(bundle)
        meta = (
            f"\n\n[Scene Metadata: room={scene.room_id} | setting={scene.setting} | "
            f"relationship={scene.relationship} | function={scene.function} | "
            f"directness={scene.directness} | flavor={scene.flavor} | topic={scene.topic}]"
        )
        if recast_phrase:
            meta += f'\n[Silent Recast Target: naturally model "{recast_phrase}" in your own reply without pointing out any error.]'
        return base + meta

    async def reply(
        self,
        user_id: int,
        learner_text: str,
        recast_phrase: str | None = None,
    ) -> tuple[str, bool, list[str]]:
        is_live_gemini = (self.llm.__class__.__name__ == "GeminiService" and bool(getattr(self.llm, "clients", None)))
        if not is_live_gemini:
            return await self._orig.reply(user_id=user_id, learner_text=learner_text, recast_phrase=recast_phrase)

        scene = self.scene_repo.get_active_scene(user_id)
        turn_no = self._user_turn_counts.get(user_id, 0) + 1
        self._user_turn_counts[user_id] = turn_no

        bundle = self.nat_router.route(
            user_id=user_id,
            turn_no=turn_no,
            user_text=learner_text,
            room=scene.room_id,
        )
        sys_prompt = self.build_system_prompt(scene, recast_phrase=recast_phrase, user_id=user_id, user_text=learner_text)

        recent_turns: list[str] = []
        if hasattr(self.scene_repo, "get_recent_turns"):
            try:
                recent_turns = self.scene_repo.get_recent_turns(user_id, role=None, limit=12)
            except Exception:
                recent_turns = []

        history_block = ""
        if recent_turns:
            history_block = "Recent conversation context:\n" + "\n".join(recent_turns[-12:]) + "\n\n"

        full_user_prompt = f"{history_block}Partner says: {learner_text}" if history_block else learner_text

        raw_reply = await self.llm.generate_text(
            user_id=user_id,
            system_prompt=sys_prompt,
            user_prompt=full_user_prompt,
        )
        used_recast = bool(recast_phrase and recast_phrase.lower() in raw_reply.lower())

        cleaned_nat = raw_reply
        guard_hits: list[str] = []
        try:
            for mname in ("apply", "clean", "sanitize", "process"):
                fn = getattr(self.nat_guard, mname, None)
                if callable(fn):
                    sig = inspect.signature(fn)
                    if "forbid_questions" in sig.parameters:
                        res = fn(raw_reply, forbid_questions=(getattr(bundle.dice, "question_budget", 1) == 0))
                    elif len(sig.parameters) >= 2:
                        res = fn(raw_reply, getattr(bundle, "dice", None))
                    else:
                        res = fn(raw_reply)
                    if isinstance(res, tuple) and len(res) >= 2:
                        cleaned_nat, guard_hits = str(res[0]), list(res[1])
                    elif isinstance(res, str):
                        cleaned_nat = res
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("nat_guard warning: %s", exc)

        final_text = cleaned_nat.strip() or raw_reply.strip() or "Yeah, totally makes sense."
        segments = [s.strip() for s in final_text.split("|||") if s.strip()] or [final_text]
        self.last_segments[user_id] = segments
        joined_reply = " ".join(segments)

        try:
            if hasattr(self.scene_repo, "log_turn"):
                self.scene_repo.log_turn(user_id, scene.room_id, "learner", learner_text)
                self.scene_repo.log_turn(user_id, scene.room_id, "alex", joined_reply)
            if hasattr(self.nat_gauge, "log_turn"):
                self.nat_gauge.log_turn(
                    user_id=user_id,
                    msg_id=f"{user_id}:{turn_no}",
                    situation=getattr(bundle, "situation_name", scene.room_id),
                    cast_name=getattr(bundle, "cast_name", "Sam"),
                    dice_roll=getattr(bundle.dice, "__dict__", {}),
                    reply_text=joined_reply,
                    guard_hits=guard_hits,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Non-critical logging warning: %s", exc)

        return joined_reply, used_recast, []
