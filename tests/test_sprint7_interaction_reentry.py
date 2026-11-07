from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.db.chunk_repo import ChunkRepository
from src.interaction.track import InteractionTrackEngine
from src.models.chunk import ChunkMasteryRecord
from src.models.interaction_reentry import InteractionDimensionScores
from src.session.reentry import ReEntrySessionManager


def test_interaction_backchannels_storytelling_and_anti_formulaic_rule():
    engine = InteractionTrackEngine()

    # 1. Backchannels classified by function
    funcs = engine.classify_backchannel("Man, that's tough—no wonder you pushed back.")
    assert "empathy" in funcs

    # 2. Storytelling diagnostic frame
    full_story = (
        "Last week we were at the station when all of a sudden the train stalled. "
        "So we tried calling a cab, and honestly looking back it was frustrating, "
        "but in the end we made it on time."
    )
    diag = engine.diagnose_storytelling(full_story)
    assert diag.missing_elements == []

    # 3. Small talk reciprocity penalizes interview-style questioning
    eval_bad = engine.evaluate_small_talk_reciprocity([
        "Where did you grow up?",
        "What do you do for work?",
    ])
    assert eval_bad.interview_penalty_applied is True

    eval_good = engine.evaluate_small_talk_reciprocity([
        "I've been hiking a lot in the mountains lately to clear my head. What do you usually do to unwind?"
    ])
    assert eval_good.has_reciprocal_sharing is True
    assert eval_good.interview_penalty_applied is False

    # 4. Blueprint 4.5: Repeating "Can I jump in?" is NOT acquisition
    scores = InteractionDimensionScores(
        recognition=3.5, timing=3.5, pragmatic_appropriateness=3.5, execution=3.5, transfer=3.5
    )
    acq1, _ = engine.record_skill_attempt(12, "polite_interruption", "Can I jump in?", "professional", scores)
    assert acq1 is False

    acq2, msg2 = engine.record_skill_attempt(12, "polite_interruption", "Can I jump in?", "intellectual", scores)
    assert acq2 is False
    assert "Formulaic repetition" in msg2

    # Varying the expression in a second register achieves acquisition
    acq3, _ = engine.record_skill_attempt(
        12, "polite_interruption", "Sorry to jump in here, but quick point—", "intellectual", scores
    )
    assert acq3 is True


def test_reentry_session_blocks_and_no_automatic_e3_e4_demotion(tmp_path: Path):
    repo = ChunkRepository(db_path=tmp_path / "reentry.db")
    mgr = ReEntrySessionManager(repo)

    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    last_active = (now - timedelta(days=11)).isoformat()
    needed, days = mgr.needs_reentry_session(last_active, now=now)
    assert needed is True
    assert days == 11

    state = mgr.start_reentry_session(user_id=44, days_away=days)
    assert state.total_planned_minutes == 10
    assert state.current_block == "warm_natural_talk_2m"

    blocks = [mgr.advance_block(44)[0] for _ in range(4)]
    assert blocks == [
        "unprompted_retrieval_3m",
        "skill_and_difficult_target_3m",
        "comparison_with_last_evidence_2m",
        "completed",
    ]

    # Verify target classifications and strict non-demotion of E3/E4
    e3_rec = ChunkMasteryRecord(
        user_id=44, chunk_id="disagree_pro_01", function="disagree", baseline_register="professional", e_level="E3"
    )
    e4_rec = ChunkMasteryRecord(
        user_id=44, chunk_id="challenge_intel_01", function="challenge_idea", baseline_register="intellectual", e_level="E4"
    )

    out_inacc = mgr.classify_target_on_return(
        e3_rec, retrieved_unprompted=False, retrieved_after_cue=True, collocation_intact=False
    )
    assert out_inacc.classification == "temporarily_inaccessible"
    assert out_inacc.new_e_level == "E3"  # Never demoted to E2!

    out_weak = mgr.classify_target_on_return(
        e4_rec, retrieved_unprompted=True, retrieved_after_cue=False, collocation_intact=False
    )
    assert out_weak.classification == "weakened"
    assert out_weak.new_e_level == "E4"  # Never demoted!

    out_uncertain = mgr.classify_target_on_return(
        e3_rec, retrieved_unprompted=False, retrieved_after_cue=False, collocation_intact=False, evidence_clear=False
    )
    assert out_uncertain.classification == "uncertain"
    assert out_uncertain.new_e_level == "E3"
