import re
from src.config import PROMPTS_DIR
from src.db.scene_repo import SceneRepository
from src.llm.gemini_client import GeminiService
from src.models.register_scene import SceneConfig
from src.quality.guard import QualityAuditResult, QualityGuard


class AlexResponder:
    def __init__(self, scene_repo: SceneRepository, llm: GeminiService, guard: QualityGuard | None = None) -> None:
        self.scene_repo = scene_repo
        self.llm = llm
        self.guard = guard or QualityGuard()

    def _build_system_prompt(
        self, scene: SceneConfig, force_statement: bool, recast_phrase: str | None = None
    ) -> str:
        persona = (PROMPTS_DIR / "alex_persona.md").read_text(encoding="utf-8")
        scene_tpl = (PROMPTS_DIR / "register_scenes.md").read_text(encoding="utf-8")
        cadence_directive = (
            "CRITICAL: Your last turn ended with a question. End this turn with a statement or reaction, NOT a question."
            if force_statement
            else "End with a statement or a natural question only if it fits organically."
        )
        scene_block = scene_tpl.format(
            room_id=scene.room_id,
            setting=scene.setting,
            relationship=scene.relationship,
            function=scene.function,
            directness=scene.directness,
            flavor=scene.flavor,
            topic=scene.topic,
            cadence_directive=cadence_directive,
        )
        prompt = f"{persona}\n\n{scene_block}"
        if recast_phrase:
            prompt += (
                f"\n\nSILENT RECAST DIRECTIVE: Naturally weave the exact phrase \"{recast_phrase}\" "
                "into your response without pointing out the learner's mistake or sounding pedantic."
            )
        return prompt

    def _sanitize_fallback(self, text: str, force_statement: bool) -> str:
        cleaned = text.strip()
        replacements = {
            r"\bmoreover\b": "plus",
            r"\bfurthermore\b": "also",
            r"\bconsequently\b": "so",
            r"\bi would prefer to\b": "I'd rather",
            r"\bcircle back\b": "follow up",
            r"\bsynergy\b": "teamwork",
            r"\bleverage\b": "use",
            r"\bdude\b": "",
            r"\bbro\b": "",
            r"\bno cap\b": "honestly",
        }
        for pattern, repl in replacements.items():
            cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
        if force_statement and cleaned.endswith("?"):
            cleaned = cleaned[:-1].rstrip() + "."
        return cleaned

    async def reply(
        self, user_id: int, learner_text: str, recast_phrase: str | None = None
    ) -> tuple[str, SceneConfig, QualityAuditResult]:
        scene = self.scene_repo.get_active_scene(user_id)
        self.scene_repo.log_turn(user_id, scene.room_id, "learner", learner_text)

        recent_alex = self.scene_repo.get_recent_turns(user_id, role="alex", limit=5)
        force_statement = bool(recent_alex and recent_alex[-1].strip().endswith("?"))
        sys_prompt = self._build_system_prompt(scene, force_statement=force_statement, recast_phrase=recast_phrase)

        draft = await self.llm.generate_text(
            user_id=user_id,
            system_prompt=sys_prompt,
            user_prompt=f"Learner said: {learner_text}\nReply naturally as Alex:",
        )
        audit = self.guard.audit_turns((recent_alex[-3:] + [draft]), scene)

        if not audit.passed:
            repair_prompt = (
                f"{sys_prompt}\nFix these quality issues in your draft ({', '.join(audit.reasons)}): {draft}"
            )
            draft = await self.llm.generate_text(user_id=user_id, system_prompt=repair_prompt, user_prompt=learner_text)
            draft = self._sanitize_fallback(draft, force_statement=force_statement)
            audit = self.guard.audit_turns((recent_alex[-3:] + [draft]), scene)

        self.scene_repo.log_turn(user_id, scene.room_id, "alex", draft)
        return draft, scene, audit
