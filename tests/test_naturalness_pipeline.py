from src.naturalness import (
    NaturalnessGauge,
    NaturalnessGuard,
    NaturalnessRouter,
    PromptCompiler,
)


def test_router_selects_profile_moves_anchors_and_seeded_dice():
    router = NaturalnessRouter()
    b1 = router.route(user_id=42, turn_no=1, user_text="Wanna grab coffee tonight?", room="casual_friends")
    b2 = router.route(user_id=42, turn_no=1, user_text="Wanna grab coffee tonight?", room="casual_friends")
    assert b1.profile_id == "friend_text"
    assert b1.cast_name == "Sam"
    assert b1.dice["id"] == b2.dice["id"]
    assert any(m["id"] in ("decline_invitation", "accept_invitation", "service_order_request") for m in b1.moves)
    assert len(b1.anchors) == 3


def test_dice_never_repeats_last_three_and_blocks_early_drift():
    router = NaturalnessRouter()
    for turn in range(1, 6):
        dice, _ = router.roll_dice(user_id=99, turn_no=turn, recent_dice_ids=["short", "react", "plain"])
        assert dice["id"] not in ("short", "react", "plain")
        assert dice["id"] != "drift"


def test_guard_strips_ai_isms_markdown_em_dash_and_caps_emoji():
    guard = NaturalnessGuard()
    raw = "Certainly! **That's a great point** — I'd be happy to delve into that. Honestly, winter is rough!! 😊🔥"
    res = guard.apply(raw, learner_used_emoji=False)
    assert "Certainly" not in res.text
    assert "delve" not in res.text
    assert "**" not in res.text
    assert "—" not in res.text
    assert "😊" not in res.text
    assert res.text.count("!") <= 1
    assert any(h.startswith("hard_opener") for h in res.guard_hits)
    assert any(h.startswith("hard_phrase") for h in res.guard_hits)


def test_guard_double_text_and_fallback_when_all_empty():
    guard = NaturalnessGuard()
    res_double = guard.apply("ha yeah totally --- wait did he actually say that?")
    assert len(res_double.segments) == 2

    res_empty = guard.apply("Certainly! Feel free to ask if you have any questions.")
    assert "fallback_used" in res_empty.guard_hits
    assert len(res_empty.segments) == 1


def test_window_analyzer_triggers_no_question_and_opener_ban():
    msgs = [
        "Yeah, how was your day?",
        "Yeah, what did you eat?",
        "Yeah, did you finish the report?",
        "Yeah, are you going tonight?",
        "Yeah, what time is it?",
    ]
    win = NaturalnessGuard.analyze_window(msgs)
    assert win["force_no_question"] is True
    assert win["avoid_opener"] == "yeah"


def test_compiler_and_gauge_end_to_end():
    router = NaturalnessRouter()
    compiler = PromptCompiler()
    guard = NaturalnessGuard()
    gauge = NaturalnessGauge()

    bundle = router.route(user_id=7, turn_no=3, user_text="How does participatory budgeting hold up?", room="academic_seminar")
    prompt = compiler.compile_system_prompt(bundle, learner_notes=["Researching local governance in Algeria"])
    assert "Prof. Ruiz" in prompt
    assert "HOW YOU SOUND" in prompt
    assert "LEARNER NOTES" in prompt

    cleaned = guard.apply("That's a fair point, honestly. I'd push back a little on the fiscal side.")
    gauge.log_turn(
        msg_id="m1",
        user_id=7,
        profile=bundle.profile_id,
        dice_id=bundle.dice["id"],
        cards=[m["id"] for m in bundle.moves],
        text=cleaned.text,
        n_words=cleaned.n_words,
        ends_with_question=cleaned.ends_with_question,
        guard_hits=cleaned.guard_hits,
    )
    assert gauge.flag_artificial("m1", context_turns=["turn1", "turn2"]) is True
    report = gauge.weekly_report()
    assert report["total_messages"] == 1
    assert report["flagged_rate"] == 1.0


def test_full_catalog_counts_24_moves_and_60_anchors():
    from src.naturalness import NaturalnessRouter
    router = NaturalnessRouter()
    assert len(router.moves) >= 24
    assert len(router.anchors) == 60
    assert len(router.cast) == 6
    # Exclude _note key in profiles.json
    profile_keys = [k for k in router.profiles.keys() if not k.startswith("_")]
    assert len(profile_keys) == 9


def test_podcast_validator_checks_word_count_short_turns_and_watchlist():
    from src.naturalness import PodcastPipelineValidator
    validator = PodcastPipelineValidator()
    turns = []
    # Build 40 turns: 12 short turns (<=6 words) + 28 medium turns (~12 words) => ~360 words
    for i in range(12):
        turns.append({"speaker": "Maya", "text": "Wait, really? No way."})
    for i in range(21):
        turns.append({
            "speaker": "Jordan",
            "text": "Yeah, I mean we tried to push it back, but rain check was impossible that week."
        })
    report = validator.check_script(turns, watchlist=["push it back", "rain check"])
    assert report.passed is True
    assert 330 <= report.total_words <= 420
    assert report.short_turn_share >= 0.25


def test_learner_lab_tools_prompts():
    from src.naturalness import LearnerLabTools
    p1 = LearnerLabTools.build_register_ladder_prompt("I cannot come tonight")
    p2 = LearnerLabTools.build_flip_situation_prompt("ahh i can't tonight, rain check?", "boss_or_client")
    assert "Register Ladder" in p1
    assert "boss_or_client" in p2
