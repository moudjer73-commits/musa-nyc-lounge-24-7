from datetime import datetime, timezone
from pathlib import Path
import pytest
from pydantic import ValidationError
from src.analyst.engine import AnalystEngine
from src.chunks.scheduler import AcquisitionScheduler, ChunkBank
from src.db.analyst_repo import AnalystRepository
from src.db.chunk_repo import ChunkRepository
from src.models.analyst import AnalystLLMOutput
from src.models.chunk import FunctionalChunk, ProductionAttempt
from src.session.daily_manager import DailySessionManager


class DummySessionLLM:
    async def generate_structured(self, *args, **kwargs):
        return AnalystLLMOutput()

    async def generate_text(self, *args, **kwargs) -> str:
        return "Natural response."


def test_chunk_bank_forbids_arabic_gloss_and_varies_prompts():
    bank = ChunkBank()
    assert len(bank.chunks) >= 6

    idx1, p1, _ = bank.get_varied_prompt("disagree_pro_01", -1)
    idx2, p2, _ = bank.get_varied_prompt("disagree_pro_01", idx1)
    assert idx1 != idx2
    assert p1 != p2

    with pytest.raises(ValidationError):
        FunctionalChunk(
            chunk_id="bad_card",
            phrase="I see where you're coming from",
            function="disagree",
            register="professional",
            structure="ترجمة حرفية ممنوعة",
            collocations=["see where"],
            examples=["Example 1", "Example 2"],
            retrieval_prompts=["Prompt 1", "Prompt 2"],
        )


def test_e_level_promotion_rules_and_review_intervals(tmp_path: Path):
    repo = ChunkRepository(db_path=tmp_path / "chunks.db")
    bank = ChunkBank()
    scheduler = AcquisitionScheduler()
    chunk = bank.get_chunk("disagree_pro_01")
    rec = repo.get_or_create_record(101, chunk)
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Immediate echo of Alex's model -> capped at E2 even if accurate
    rec = scheduler.evaluate_attempt(
        rec,
        ProductionAttempt(
            session_id="s1", context_register="professional", immediate_echo_of_alex=True, speed="effortless"
        ),
        now=now,
    )
    assert rec.e_level == "E2"
    assert rec.interval_days == 7

    # 2. First true unprompted session -> still E2 (E3 requires TWO separate sessions)
    rec = scheduler.evaluate_attempt(
        rec,
        ProductionAttempt(
            session_id="s1", context_register="professional", immediate_echo_of_alex=False, speed="slow"
        ),
        now=now,
    )
    assert rec.e_level == "E2"
    assert rec.interval_days == 3

    # 3. Second separate unprompted session -> promotes to E3!
    rec = scheduler.evaluate_attempt(
        rec,
        ProductionAttempt(
            session_id="s2", context_register="professional", immediate_echo_of_alex=False, speed="effortless"
        ),
        now=now,
    )
    assert rec.e_level == "E3"
    assert rec.interval_days == 7

    # 4. Failed attempt on E3 -> does NOT auto-demote E3, marks weakened + retry_in_same_session + 1d interval
    rec = scheduler.evaluate_attempt(
        rec,
        ProductionAttempt(session_id="s3", context_register="professional", collocation_accurate=False, speed="failed"),
        now=now,
    )
    assert rec.e_level == "E3"
    assert rec.retention_state == "weakened"
    assert rec.retry_in_same_session is True
    assert rec.interval_days == 1

    # 5. Successful transfer to an unfamiliar register (intellectual) -> promotes to E4 + 14d interval
    rec = scheduler.evaluate_attempt(
        rec,
        ProductionAttempt(
            session_id="s4",
            context_register="intellectual",
            transferred_to_new_context=True,
            speed="successful_transfer",
        ),
        now=now,
    )
    assert rec.e_level == "E4"
    assert rec.interval_days == 14


def test_ten_minute_session_flow_weekly_days_and_reminders(tmp_path: Path):
    db_file = tmp_path / "sess.db"
    c_repo = ChunkRepository(db_path=db_file)
    a_repo = AnalystRepository(db_path=db_file)
    bank = ChunkBank()
    scheduler = AcquisitionScheduler()
    analyst = AnalystEngine(repo=a_repo, llm=DummySessionLLM())
    mgr = DailySessionManager(c_repo, bank, scheduler, analyst)

    # D2 hides targets for unprompted retrieval; total planned time is exactly 10 minutes
    state = mgr.start_session(user_id=7, day_number=2)
    assert state.day_code == "D2"
    assert state.show_targets_to_learner is False
    assert state.planned_minutes == 10

    # Log 2 errors in analyst repo, verify feedback stage surfaces ONLY ONE target
    a_repo.log_analysis(
        7, state.session_id,
        AnalystLLMOutput(category="B", error_type="lexis", error_signature="sig1", evidence_span="make a discussion", suggested_alternative="have a discussion", confidence=0.95),
        gated_out=False, action_taken="end_of_session_summary", surfaced_mid_session=False
    )
    a_repo.log_analysis(
        7, state.session_id,
        AnalystLLMOutput(category="A", error_type="grammar", error_signature="sig2", evidence_span="he don't", suggested_alternative="he doesn't", confidence=0.90),
        gated_out=False, action_taken="end_of_session_summary", surfaced_mid_session=False
    )

    phases = []
    last_msg = ""
    for _ in range(6):
        ph, msg = mgr.advance_phase(7)
        phases.append(ph)
        if ph == "feedback_one_target":
            last_msg = msg

    assert phases == [
        "exposure_to_chunks",
        "guided_interaction",
        "spontaneous_production",
        "one_new_situation",
        "feedback_one_target",
        "completed",
    ]
    assert "have a discussion" in last_msg
    assert "he doesn't" not in last_msg

    # Verify daily reminders
    now = datetime(2026, 5, 10, 19, 0, tzinfo=timezone.utc)
    c_repo.configure_reminder(7, hour_utc=18, enabled=True)
    assert 7 in c_repo.get_due_reminder_users(now=now)
    c_repo.mark_reminder_sent(7, now=now)
    assert 7 not in c_repo.get_due_reminder_users(now=now)
