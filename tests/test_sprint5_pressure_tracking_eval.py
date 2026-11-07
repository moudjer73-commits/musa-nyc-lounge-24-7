from pathlib import Path
from src.db.chunk_repo import ChunkRepository
from src.db.profile_repo import ProfileRepository
from src.eval.harness import ReleaseEvalHarness
from src.models.chunk import ChunkMasteryRecord
from src.models.pressure_tracking import BenchmarkRubricScore
from src.models.profile import LearnerProfile
from src.models.register_scene import SceneConfig
from src.pressure.engine import FORBIDDEN_TOPIC_KEYWORDS, PressureTestEngine
from src.tracking.progress import ProgressTracker


def test_pressure_monologue_safety_skip_and_self_repair_wait():
    engine = PressureTestEngine()
    profile = LearnerProfile(user_id=31, topics_to_avoid=["politics", "religion", "remote work"])

    c1 = engine.start_90s_monologue(profile, stage="S2")
    assert "remote work" not in c1.topic.lower()
    assert not any(k in c1.topic.lower() for k in FORBIDDEN_TOPIC_KEYWORDS)
    assert c1.min_words_threshold == 110

    first_topic = c1.topic
    c2 = engine.skip_monologue_topic(profile)
    assert c2.topic != first_topic
    assert first_topic in c2.skipped_topics

    # Self-repair test: first stall/slip -> Alex waits ("..."), second turn -> proceeds
    waited_1, msg_1 = engine.handle_self_repair_turn(31, "I mean—wait, how do I say...", has_slip_or_stall=True)
    assert waited_1 is True
    assert msg_1 == "..."

    waited_2, msg_2 = engine.handle_self_repair_turn(31, "I meant we should prioritize quality.", has_slip_or_stall=True)
    assert waited_2 is False
    assert "keep building" in msg_2

    # Live register switch under pressure
    switched = engine.trigger_live_register_switch(SceneConfig(setting="casual", relationship="close"), "professional")
    assert switched.setting == "professional"
    assert switched.directness == "diplomatic"


def test_three_part_progress_and_benchmark_rubric(tmp_path: Path):
    db_file = tmp_path / "prog.db"
    p_repo = ProfileRepository(db_path=db_file)
    c_repo = ChunkRepository(db_path=db_file)
    tracker = ProgressTracker(p_repo, c_repo, db_path=db_file)

    # Record baseline at 2.0, then improve profile scores
    tracker.ensure_baseline_recorded(50)
    prof = p_repo.get_or_create_profile(50)
    prof.mvp_dimensions.retrieval_independence.update_with_evidence(3.2, "unprompted chunks", 3)
    p_repo.save_profile(prof)

    # Save an E3 chunk record
    c_repo.save_record(
        ChunkMasteryRecord(
            user_id=50, chunk_id="disagree_pro_01", function="disagree",
            baseline_register="professional", e_level="E3", retention_state="retained"
        )
    )

    # Record Day 0 and Week 4 fixed speaking task scores
    tracker.record_benchmark_score(
        BenchmarkRubricScore(
            user_id=50, checkpoint="day_0", task_prompt="",
            fluency_retrieval=2.0, lexical_precision=2.0, register_fit=2.0, pragmatic_control=2.0
        )
    )
    tracker.record_benchmark_score(
        BenchmarkRubricScore(
            user_id=50, checkpoint="week_4", task_prompt="",
            fluency_retrieval=3.0, lexical_precision=2.8, register_fit=3.2, pragmatic_control=3.0
        )
    )

    report = tracker.build_progress_report(50)
    assert report.is_private is True
    assert report.leaderboard_enabled is False
    assert report.growth_vs_baseline["retrieval_independence"] == 1.2
    assert report.e3_plus_total == 1
    assert report.retention_counts["retained"] == 1
    assert report.benchmark_delta == 1.0


def test_12_case_release_battery_and_gold_set():
    harness = ReleaseEvalHarness()
    clean_line = "I'm gonna push back slightly on that estimate because the testing phase isn't done yet."
    battery_inputs = {
        i: {"turns": [clean_line, "How do we want to handle the timeline?"]}
        for i in range(1, 10)
    }
    battery_inputs[10] = {"post_switch_turns": ["I'm calling to review the Q3 contract deliverables."]}
    battery_inputs[11] = {
        "minute_2_turns": [clean_line, "What do you think?"],
        "minute_11_turns": ["We're still on track for Friday's deployment.", "Does that work for your team?"],
    }
    battery_inputs[12] = {
        "versions": {
            "casual": "I'm gonna pass on that—it's way too pricey right now.",
            "executive": "We'll need to hold off on that investment until Q4 budgets clear.",
            "intellectual": "The cost-benefit ratio doesn't really hold up under those assumptions.",
        }
    }

    results = harness.run_12_case_battery(battery_inputs)
    assert len(results) == 12
    assert all(r.passed for r in results)

    # Verify failure detection on Case 10 (register residue) & Case 12 (identical versions)
    fail_10 = harness.evaluate_single_case(10, {"post_switch_turns": ["We're gonna roast that client on the couch."]})
    assert fail_10.passed is False
    fail_12 = harness.evaluate_single_case(12, {"versions": {"casual": clean_line, "executive": clean_line, "intellectual": clean_line}})
    assert fail_12.passed is False

    # Gold set verification
    gold_ok, failures = harness.run_gold_set_eval({
        "gold_01": {"category": "B", "suggested_alternative": "Let me finish my thought before we move on."},
        "gold_02": {"category": "D", "suggested_alternative": "I'm gonna have to pass on tonight's movie."},
        "gold_03": {"category": "C", "suggested_alternative": ""},
    })
    assert gold_ok is True
    assert failures == []
