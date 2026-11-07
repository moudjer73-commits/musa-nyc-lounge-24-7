from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from src.config import get_initial_register_weights, normalize_register_weights
from src.db.engine import restore_encrypted_snapshot
from src.db.profile_repo import ProfileRepository
from src.models.profile import LearnerProfile, compute_confidence_label
from src.onboarding.diagnosis import CalibrationResult, OnboardingDiagnosisEngine


class DummyLLM:
    async def generate_text(self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
        return "That sounds really interesting. Tell me more about how you handle that at work."

    async def generate_structured(self, user_id: int, system_prompt: str, user_prompt: str, schema_cls, fallback_instance):
        return CalibrationResult(
            inferred_goal="study",
            interests=["political science", "AI"],
            detected_dialect="Maghrebi",
            dialect_evidence_span="I am focusing on smart local governance",
            dialect_supported_by_speech=False,
            other_languages=["French"],
            correction_style="selective",
            arabic_support="none",
            retrieval_score=3.1,
            retrieval_evidence="sustained spontaneous explanation",
            linguistic_score=2.9,
            linguistic_evidence="accurate collocations",
            interaction_score=3.2,
            interaction_evidence="natural pushback",
            register_score=2.8,
            register_evidence="shifted cleanly to academic framing",
        )


def test_register_weights_and_minimum_floor():
    assert get_initial_register_weights("work") == {"casual": 20, "social": 15, "professional": 45, "intellectual": 20}
    assert get_initial_register_weights("study") == {"casual": 15, "social": 15, "professional": 20, "intellectual": 50}
    tuned = normalize_register_weights({"casual": 0, "social": 5, "professional": 90, "intellectual": 5})
    assert sum(tuned.values()) == 100
    assert all(v >= 10 for v in tuned.values())


def test_confidence_progression_and_dialect_hypothesis():
    assert compute_confidence_label(1, 1) == "provisional"
    assert compute_confidence_label(3, 1) == "supported"
    assert compute_confidence_label(6, 2) == "well_established"

    p = LearnerProfile(user_id=101)
    assert p.dialect.status == "hypothesis"
    p.dialect.record_observation("arrive to the university", supports=True, sessions_count=1)
    assert p.dialect.status == "hypothesis"
    p.dialect.record_observation("make a discussion", supports=True, sessions_count=2)
    assert p.dialect.status == "supported"


@pytest.mark.asyncio
async def test_three_session_onboarding_snapshot_and_privacy(tmp_path: Path):
    db_file = tmp_path / "test.db"
    snap_file = tmp_path / "snap.enc"
    repo = ProfileRepository(db_path=db_file, snapshot_path=snap_file, passphrase="test-secret-passphrase")
    engine = OnboardingDiagnosisEngine(repo=repo, llm=DummyLLM())

    for _ in range(12):
        reply, profile = await engine.process_turn(42, "I'm working on my doctoral research.")
        assert "interesting" in reply

    assert profile.onboarding_completed is True
    assert profile.goal == "study"
    assert profile.register_weights == {"casual": 15, "social": 15, "professional": 20, "intellectual": 50}
    assert profile.dialect.dialect_name == "Maghrebi"
    assert profile.dialect.status == "hypothesis"
    assert not repo.needs_recalibration(profile)
    assert repo.needs_recalibration(profile, now=datetime.now(timezone.utc) + timedelta(days=29))

    assert snap_file.exists()
    restored_db = tmp_path / "restored.db"
    assert restore_encrypted_snapshot(snap_file, restored_db, "test-secret-passphrase")
    repo2 = ProfileRepository(db_path=restored_db)
    assert repo2.get_or_create_profile(42).goal == "study"

    repo.get_or_create_profile(99)
    assert repo.delete_user_data(42) is True
    assert repo.get_onboarding_transcript(42) == []
    assert repo.get_or_create_profile(99).user_id == 99
