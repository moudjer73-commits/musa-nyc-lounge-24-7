from pathlib import Path
import pytest
from src.alex.responder import AlexResponder
from src.db.scene_repo import SceneRepository
from src.models.register_scene import SceneConfig
from src.quality.guard import QualityGuard
from src.register.engine import RegisterEngine


class ScriptedAlexLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.idx = 0

    async def generate_text(self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
        out = self.responses[min(self.idx, len(self.responses) - 1)]
        self.idx += 1
        return out


def test_scene_calibration_humor_lock_and_switching():
    engine = RegisterEngine()
    # S1 learner requesting humorous flavor gets downgraded to warm (recognition only)
    s1_scene = engine.create_room_scene("casual_friends", function="disagree", flavor="humorous", learner_stage="S1")
    assert s1_scene.flavor == "warm"
    assert s1_scene.allow_humor_recognition_only is True
    assert s1_scene.directness == "firm"

    # S3 learner unlocks humorous production
    s3_scene = engine.create_room_scene("casual_friends", function="disagree", flavor="humorous", learner_stage="S3")
    assert s3_scene.flavor == "humorous"
    assert s3_scene.allow_humor_recognition_only is False

    # Switching evidence caps: (b) -> E2, (a) and (c) -> E3_E4
    ev_b = engine.switch_scene(s3_scene, mode="b_learner_requested", new_setting="professional", new_relationship="hierarchical")
    assert ev_b.max_evidence_level == "E2"
    assert ev_b.new_scene.directness == "diplomatic"

    ev_a = engine.switch_scene(s3_scene, mode="a_bot_scene_change", new_setting="intellectual", new_relationship="equal")
    assert ev_a.max_evidence_level == "E3_E4"


def test_quality_guard_markers_contractions_and_drift():
    guard = QualityGuard()
    scene = SceneConfig(setting="professional", relationship="equal", function="give_opinion")

    # 1. Valid natural turn with "gonna" (explicitly allowed in all registers)
    clean_turns = [
        "I'm gonna push back slightly on that timeline because the backend still needs testing.",
        "What do you think happens if we delay the rollout by a week?",
    ]
    res_clean = guard.audit_turns(clean_turns, scene)
    assert res_clean.passed is True
    assert res_clean.question_ratio == 0.5

    # 2. Fail on false casual, false formal, corporate filler, and interview cadence
    bad_turns = [
        "Dude, no cap, that's a massive risk?",
        "Moreover, I would prefer to circle back and leverage our synergy?",
    ]
    res_bad = guard.audit_turns(bad_turns, scene)
    assert res_bad.passed is False
    assert "dude" in res_bad.false_casual_hits
    assert "moreover" in res_bad.false_formal_hits
    assert "circle back" in res_bad.corporate_buzzwords
    assert res_bad.interview_cadence_violation is True

    # 3. Drift check between minute 2 (clean) and minute 11 (drifted)
    drift = guard.check_drift(clean_turns, bad_turns, scene)
    assert drift.drifted is True


@pytest.mark.asyncio
async def test_alex_responder_auto_repairs_bad_draft(tmp_path: Path):
    repo = SceneRepository(db_path=tmp_path / "scene.db")
    # Turn 1 ends with a question; Turn 2 draft violates cadence & uses 'moreover', then repairs
    llm = ScriptedAlexLLM([
        "I don't think that's realistic right now. How would you handle the budget?",
        "Moreover, dude, why would we circle back on that?",
        "Plus, I'd rather we focus on the core features first before expanding.",
    ])
    responder = AlexResponder(scene_repo=repo, llm=llm)

    r1, _, a1 = await responder.reply(77, "Let's launch next Monday.")
    assert r1.endswith("?")
    assert a1.passed is True

    r2, _, a2 = await responder.reply(77, "We can borrow from next quarter.")
    assert not r2.endswith("?")
    assert "moreover" not in r2.lower()
    assert a2.passed is True
