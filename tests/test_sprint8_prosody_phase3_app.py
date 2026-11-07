from pathlib import Path
from src.bot.app import LabContainer
from src.models.chunk import ChunkMasteryRecord
from src.models.pressure_tracking import BenchmarkRubricScore
from src.models.profile import LearnerProfile
from src.prosody.lab import ProsodyLabEngine

def test_prosody_lab_5min_isolation_french_gating_and_recognition_guard():
    lab = ProsodyLabEngine()
    p1 = LearnerProfile(user_id=201, other_languages=["Arabic"])
    p1.dialect.dialect_name = "Maghrebi"
    p1.dialect.status = "hypothesis"
    b1 = lab.build_5min_prosody_block(p1, include_recognition_items=True)
    assert b1.duration_minutes == 5 and b1.isolated_from_free_talk is True
    assert "practice aid, not a judge" in b1.feedback_disclaimer
    codes_1 = [d.target_code for d in b1.active_drills]
    assert "french_uvular_r_and_final_stress_transfer" not in codes_1
    rec_items = [d for d in b1.active_drills if d.target_code in ("creaky_voice", "wide_pitch_excursions")]
    assert len(rec_items) == 2 and all(not lab.can_train_for_production(d) for d in rec_items)
    assert "creaky_voice" not in lab.get_weekly_free_talk_recheck_targets(b1)

    p2 = LearnerProfile(user_id=202, other_languages=["French"])
    p2.dialect.dialect_name = "Egyptian"
    p2.dialect.status = "supported"
    b2 = lab.build_5min_prosody_block(p2, include_recognition_items=False)
    codes_2 = [d.target_code for d in b2.active_drills]
    assert "french_uvular_r_and_final_stress_transfer" in codes_2
    assert "epenthetic_vowel_clusters" in codes_2

def test_phase3_eight_dimensions_humor_gate_pilot_and_full_purge(tmp_path: Path):
    container = LabContainer(db_path=tmp_path / "lab.db", snapshot_path=tmp_path / "s.enc", passphrase="secret")
    assert container.phase3_engine.get_humor_exercise("S1", "production").mode == "recognition"
    assert container.phase3_engine.get_humor_exercise("S3", "production").mode == "production"

    prof = container.profile_repo.get_or_create_profile(301)
    prof.completed_sessions_count = 4
    prof.mvp_dimensions.retrieval_independence.update_with_evidence(3.4, "spontaneous", 4)
    full = container.phase3_engine.sync_full_eight_dimensions(prof, 3.1, "p vs b", 3.5, "L3")
    assert full.fluency_retrieval.score == 3.4 and full.pronunciation.score == 3.1

    container.chunk_repo.save_record(ChunkMasteryRecord(user_id=301, chunk_id="disagree_pro_01", function="disagree", baseline_register="professional", e_level="E3"))
    container.tracker.record_benchmark_score(BenchmarkRubricScore(user_id=301, checkpoint="day_0", task_prompt="t", fluency_retrieval=2.0, lexical_precision=2.0, register_fit=2.0, pragmatic_control=2.0))
    container.tracker.record_benchmark_score(BenchmarkRubricScore(user_id=301, checkpoint="week_4", task_prompt="t", fluency_retrieval=3.2, lexical_precision=3.0, register_fit=3.2, pragmatic_control=3.0))
    container.phase3_engine.record_correction_rating(301, 5)

    pilot = container.phase3_engine.get_pilot_metrics(301)
    assert pilot.targets_reaching_e3 == 1 and pilot.fixed_task_before_after_delta == 1.1
    milestone = container.phase3_engine.get_group_aggregate_milestone()
    assert milestone.leaderboard_enabled is False and milestone.collective_e3_targets_reached == 1
    assert container.purge_all_user_data(301) is True
    assert container.phase3_engine.get_group_aggregate_milestone().total_active_learners == 0


import pytest
from src.bot.onboarding_handlers import route_learner_turn
from src.models.analyst import AnalystLLMOutput
from src.onboarding.diagnosis import CalibrationResult

class E2EDummyLLM:
    async def generate_text(self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
        return "I'm glad you brought that up. Let's look at how that works in practice."

    async def generate_structured(self, user_id: int, system_prompt: str, user_prompt: str, schema_cls, fallback_instance):
        if schema_cls is CalibrationResult:
            return CalibrationResult(inferred_goal="work")
        return AnalystLLMOutput(
            category="B", error_type="lexis", error_signature="calque_complete_idea",
            evidence_span="complete my idea", suggested_alternative="finish my thought", confidence=0.95
        )

@pytest.mark.asyncio
async def test_end_to_end_onboarding_to_analyzed_alex_handoff(tmp_path: Path):
    container = LabContainer(db_path=tmp_path / "e2e.db", llm=E2EDummyLLM())
    for _ in range(12):
        msgs = await route_learner_turn(container, 501, "I want to improve my spoken English for work.")
    assert any("Onboarding diagnosis complete" in m for m in msgs)

    # Turn 13 automatically routes to Analyst + Alex (1st occurrence -> silent recast)
    post_onboarding_1 = await route_learner_turn(container, 501, "Wait, let me complete my idea.")
    assert len(post_onboarding_1) == 1
    # Turn 14 (2nd occurrence -> Alex reply + explicit correction & retry)
    post_onboarding_2 = await route_learner_turn(container, 501, "Let me complete my idea first.")
    assert len(post_onboarding_2) == 2
    assert "finish my thought" in post_onboarding_2[1]
