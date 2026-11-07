from pathlib import Path
import pytest
from src.bot.podcast_handlers import handle_podcast_button_action
from src.db.podcast_repo import PodcastRepository
from src.listening.ladder import ListeningLadderEngine
from src.models.listening_podcast import PodcastScript, PodcastTurn
from src.podcast.pipeline import PodcastPipeline, ScriptAuditor
from src.podcast.tts_engine import (
    BaseTTSProvider,
    OfflineWaveFallbackTTSProvider,
    SwappableTTSManager,
    normalize_for_natural_tts,
    validate_telegram_audio,
)


class FailingPrimaryTTS(BaseTTSProvider):
    name = "failing_edge"

    async def synthesize_turns(self, turns, output_path: Path, voice_map, slow: bool = False) -> Path:
        raise ConnectionError("Simulated edge-tts 403 block")


class MockPodcastLLM:
    def __init__(self, scripts: list[PodcastScript]) -> None:
        self.scripts = scripts
        self.calls = 0

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback_instance):
        out = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        return out


def test_listening_ladder_tasks_and_level_progression():
    ladder = ListeningLadderEngine()
    assert ladder.get_level_spec("L1")["wpm"] == 150
    assert ladder.get_level_spec("L2")["wpm"] == 185

    t_l1 = ladder.get_tasks_for_level("L1", "reconstruct_full_form")[0]
    passed, score = ladder.evaluate_task_response(t_l1, "I am going to have to pass on that meeting.")
    assert passed is True
    assert score >= 0.8

    assert ladder.compute_next_listening_level("L1", [0.85, 0.90, 0.85]) == "L2"
    assert ladder.compute_next_listening_level("L1", [0.50, 0.60, 0.70]) == "L1"
    assert len(ladder.curated_links) >= 2


def test_script_auditor_catches_bad_scripts_and_passes_clean_ones():
    auditor = ScriptAuditor()
    # Bad script: no backchannels, no repair, uses 'moreover', ends every turn with '?'
    bad_script = PodcastScript(
        register_label="professional",
        focus_function="disagree",
        target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
        turns=[
            PodcastTurn(speaker="Host_A", text="Moreover, why are we delaying the launch?"),
            PodcastTurn(speaker="Host_B", text="Dude, why would we circle back on that?"),
            PodcastTurn(speaker="Host_A", text="Are you sure about the numbers?"),
            PodcastTurn(speaker="Host_B", text="What should we do instead?"),
        ],
    )
    bad_audit = auditor.audit_script(bad_script)
    assert bad_audit.passed is False
    assert bad_audit.has_backchannels is False
    assert bad_audit.has_repair is False

    # Good script: contractions, embedded chunks, backchannel ("Right"), misunderstanding + repair ("what I meant")
    good_script = PodcastScript(
        register_label="professional",
        focus_function="disagree",
        target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
        turns=[
            PodcastTurn(speaker="Host_A", text="We're gonna push the release to Monday morning."),
            PodcastTurn(speaker="Host_B", text="Right, I see where you're coming from, but QA still needs a day."),
            PodcastTurn(speaker="Host_A", text="Wait, are you saying we should scrap the whole feature?"),
            PodcastTurn(speaker="Host_B", text="Sorry to jump in here, but what I meant was shifting it to Wednesday."),
        ],
    )
    good_audit = auditor.audit_script(good_script)
    assert good_audit.passed is True
    assert good_script.ai_generated_label == "AI-generated"


@pytest.mark.asyncio
async def test_swappable_tts_failover_and_phonetic_normalization(tmp_path: Path):
    norm = normalize_for_natural_tts("I dunno if I'm gonna wanna go.")
    assert "Iuhunno" in norm
    assert "gunna" in norm

    mgr = SwappableTTSManager(primary=FailingPrimaryTTS(), fallback=OfflineWaveFallbackTTSProvider())
    turns = [
        PodcastTurn(speaker="Host_A", text="We're gonna test this out."),
        PodcastTurn(speaker="Host_B", text="Right, makes sense to me."),
    ]
    audio_path, provider_used = await mgr.render_episode_audio(
        turns, tmp_path / "ep1.mp3", {"Host_A": "v1", "Host_B": "v2"}, slow=True
    )
    assert provider_used == "offline_wave_fallback"
    ok, reason = validate_telegram_audio(audio_path)
    assert ok is True, reason


@pytest.mark.asyncio
async def test_podcast_caching_budget_flow_and_e2_evidence_cap(tmp_path: Path):
    repo = PodcastRepository(db_path=tmp_path / "pod.db")
    good_script = PodcastScript(
        register_label="professional",
        focus_function="disagree",
        target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
        turns=[
            PodcastTurn(speaker="Host_A", text="We're gonna launch on Monday."),
            PodcastTurn(speaker="Host_B", text="Right, I see where you're coming from, but QA isn't ready."),
            PodcastTurn(speaker="Host_A", text="So you want to cancel it?"),
            PodcastTurn(speaker="Host_B", text="Sorry to jump in here, but what I meant was moving it to Wednesday."),
        ],
    )
    llm = MockPodcastLLM([good_script])
    tts = SwappableTTSManager(primary=FailingPrimaryTTS(), fallback=OfflineWaveFallbackTTSProvider())
    pipeline = PodcastPipeline(repo=repo, llm=llm, tts=tts)

    # 1. Library episode generated once, second call hits cache without calling LLM again
    ep1 = await pipeline.get_or_create_episode(
        99, "professional", "disagree", "L2", ["I see where you're coming from, but", "Sorry to jump in here, but"]
    )
    ep2 = await pipeline.get_or_create_episode(
        99, "professional", "disagree", "L2", ["I see where you're coming from, but", "Sorry to jump in here, but"]
    )
    assert ep1.episode_id == ep2.episode_id
    assert llm.calls == 1
    assert ep1.label == "AI-generated"

    # 2. Personalized episode enforces strict daily budget (1 per learner per day)
    await pipeline.get_or_create_episode(
        99, "casual", "decline", "L2", ["I'm gonna have to pass on", "take a rain check"], personal=True
    )
    with pytest.raises(RuntimeError, match="budget"):
        await pipeline.get_or_create_episode(
            99, "social", "decline", "L2", ["I'm gonna have to pass on", "plate is full"], personal=True
        )

    # 3. Learner flow: transcript is locked before answering Gist & Detail, unlocked after
    msg_locked, st1 = handle_podcast_button_action(repo, ep1, 99, "transcript")
    assert st1.transcript_visible is False
    assert "hidden" in msg_locked

    handle_podcast_button_action(repo, ep1, 99, "questions")
    msg_unlocked, st2 = handle_podcast_button_action(repo, ep1, 99, "transcript")
    assert st2.transcript_visible is True
    assert "Host_A:" in msg_unlocked

    # 4. Evidence cap: post-episode chunk use is E2 at most (never E3)
    assert pipeline.evaluate_post_episode_evidence(1.0, used_target_chunk_after=False) == "E1"
    assert pipeline.evaluate_post_episode_evidence(1.0, used_target_chunk_after=True) == "E2"
