import hashlib
from src.config import DATA_DIR
from src.db.podcast_repo import PodcastRepository
from src.llm.gemini_client import GeminiService
from src.models.listening_podcast import (
    ListeningLevel,
    PodcastEpisodeRecord,
    PodcastScript,
    PodcastTurn,
    ScriptAuditReport,
)
from src.models.register_scene import SceneConfig, SettingType, SpeechFunction
from src.podcast.tts_engine import SwappableTTSManager
from src.quality.guard import QualityGuard

BACKCHANNEL_MARKERS = ("right", "yeah", "totally", "i see", "fair point", "gotcha", "makes sense", "hold on")
REPAIR_MARKERS = ("i mean", "what i meant", "let me rephrase", "sorry, to clarify", "put it this way")

PODCAST_PROMPT = """Generate an AI-generated two-host American English podcast script as schema-validated JSON.
Rules: written for speech (natural contractions like gonna/kinda/I'm), short sentences, varied turn lengths, ONE focus function, embed 3-5 target chunks organically (never list them), include backchannels, and include ONE small misunderstanding and its natural repair. Never use interview cadence."""


class ScriptAuditor:
    def __init__(self, guard: QualityGuard | None = None) -> None:
        self.guard = guard or QualityGuard(max_question_ratio=0.5)

    def audit_script(self, script: PodcastScript) -> ScriptAuditReport:
        turn_texts = [t.text for t in script.turns]
        joined = " ".join(turn_texts).lower()
        scene = SceneConfig(setting=script.register_label, function=script.focus_function)
        q_audit = self.guard.audit_turns(turn_texts, scene)
        reasons = list(q_audit.reasons)

        embedded_count = sum(1 for c in script.target_chunks if c.lower() in joined)
        if embedded_count < min(2, len(script.target_chunks)):
            reasons.append(f"Too few target chunks naturally embedded ({embedded_count})")
        if "target chunks:" in joined or "1." in joined:
            reasons.append("Target chunks must be embedded in dialogue, never listed")

        has_bc = any(b in joined for b in BACKCHANNEL_MARKERS)
        if not has_bc:
            reasons.append("Missing natural spoken backchannels")

        has_rep = script.has_misunderstanding_and_repair and any(r in joined for r in REPAIR_MARKERS)
        if not has_rep:
            reasons.append("Missing misunderstanding and repair sequence")

        return ScriptAuditReport(
            passed=(len(reasons) == 0),
            reasons=reasons,
            embedded_chunks_found=embedded_count,
            has_backchannels=has_bc,
            has_repair=has_rep,
        )


class PodcastPipeline:
    def __init__(
        self,
        repo: PodcastRepository,
        llm: GeminiService,
        tts: SwappableTTSManager | None = None,
        auditor: ScriptAuditor | None = None,
    ) -> None:
        self.repo = repo
        self.llm = llm
        self.tts = tts or SwappableTTSManager()
        self.auditor = auditor or ScriptAuditor()

    def _default_fallback_script(
        self, register: SettingType, function: SpeechFunction, level: ListeningLevel, chunks: list[str]
    ) -> PodcastScript:
        c0 = chunks[0] if chunks else "I see where you're coming from, but"
        c1 = chunks[1] if len(chunks) > 1 else "Sorry to jump in here, but"
        return PodcastScript(
            register_label=register,
            focus_function=function,
            level=level,
            target_chunks=chunks[:3] or [c0, c1],
            turns=[
                PodcastTurn(speaker="Host_A", text="We're gonna roll out the new schedule on Monday."),
                PodcastTurn(speaker="Host_B", text=f"Right, {c0} the testing team still needs two more days."),
                PodcastTurn(speaker="Host_A", text="Wait, you want to cancel the launch completely?"),
                PodcastTurn(
                    speaker="Host_B",
                    text=f"{c1} what I meant was shifting the timeline to Wednesday, not canceling it.",
                ),
            ],
        )

    async def get_or_create_episode(
        self,
        user_id: int,
        register: SettingType,
        function: SpeechFunction,
        level: ListeningLevel,
        target_chunks: list[str],
        personal: bool = False,
        max_retries: int = 2,
    ) -> PodcastEpisodeRecord:
        cache_key = (
            f"personal_{user_id}_{register}_{function}_{'_'.join(sorted(target_chunks))}"
            if personal
            else f"lib_{register}_{function}_{level}"
        )
        cached = self.repo.get_cached_episode(cache_key)
        if cached:
            return cached

        if personal and not self.repo.check_and_consume_personal_budget(user_id):
            raise RuntimeError("Daily personalized podcast budget reached (max 1 per day). Use library episodes.")

        fallback_script = self._default_fallback_script(register, function, level, target_chunks)
        script = fallback_script
        audit = self.auditor.audit_script(script)
        used_fallback = True

        for _attempt in range(max_retries):
            candidate = await self.llm.generate_structured(
                user_id=user_id,
                system_prompt=PODCAST_PROMPT,
                user_prompt=f"Register={register}, Function={function}, Level={level}, Chunks={target_chunks}",
                schema_cls=PodcastScript,
                fallback_instance=fallback_script,
            )
            if candidate is fallback_script:
                break
            cand_audit = self.auditor.audit_script(candidate)
            if cand_audit.passed:
                script, audit = candidate, cand_audit
                used_fallback = False
                break

        if not audit.passed:
            script = fallback_script
            audit = self.auditor.audit_script(script)
            used_fallback = True

        ep_id = hashlib.sha1(cache_key.encode()).hexdigest()[:12]
        audio_out = DATA_DIR / "podcasts" / f"{ep_id}.mp3"
        voice_map = {"Host_A": "en-US-AndrewNeural", "Host_B": "en-US-AvaNeural"}
        rendered_path, _ = await self.tts.render_episode_audio(script.turns, audio_out, voice_map)

        record = PodcastEpisodeRecord(
            episode_id=ep_id,
            cache_key=cache_key,
            user_id=user_id if personal else None,
            script=script,
            audit_report=audit,
            voice_settings=voice_map,
            audio_path=str(rendered_path),
            label="AI-generated",
        )
        if used_fallback:
            if personal:
                self.repo.refund_personal_budget(user_id)
            return record

        self.repo.save_episode(record)
        return record

    def evaluate_post_episode_evidence(self, state_accuracy: float, used_target_chunk_after: bool) -> str:
        if used_target_chunk_after:
            return "E2"
        if state_accuracy >= 0.5:
            return "E1"
        return "E0"
