from __future__ import annotations

import pytest

from coach.checks import (
    arabic_note_problems, check_reply, ends_with_question, first_word, forbidden_phrases_for, fuzzy_contains,
    is_tag_question, looks_like_echo, normalize_text, sanitize_analysis, validate_telegram_html, word_count,
)
from coach.models import Analysis, DirectorBrief, Expression, RecastBrief, ResponderReply, StealthTarget, UsedTarget

from conftest import mk_finding


# ------------------------------------------------------------------ helpers


def test_fuzzy_contains_tolerates_inflection_but_not_other_meanings():
    assert fuzzy_contains("Yeah, no, I figured your plate was pretty full.", "my plate's pretty full")
    assert fuzzy_contains("He's making the call tomorrow", "make the call")
    assert not fuzzy_contains("I have many works", "a lot of work")
    assert not fuzzy_contains("Sounds good", "make the call")
    assert fuzzy_contains("Oh I see, okay", "I see")        # no content words: exact phrase fallback


def test_question_helpers():
    assert ends_with_question("Which deck?") and ends_with_question("Really?”")
    assert not ends_with_question("Rough day.")
    assert is_tag_question("Yeah, that's rough, right?") and is_tag_question("You know?")
    assert not is_tag_question("Which deck is it?")
    assert first_word("Honestly, that's rough") == "honestly" and word_count("I'm swamped, honestly") == 3


def test_echo_detection():
    assert looks_like_echo("my plate's pretty full", ["Right, so your plate was pretty full?"])
    assert not looks_like_echo("my plate's pretty full", ["Totally different sentence here."])


# ------------------------------------------------------------------ arabic note tone


@pytest.mark.parametrize(
    "note",
    [
        "في العربية نقول «اتّخذ قرارًا»، أما الإنجليزية فتختار make مع decision.",
        "«معلومات» جمع في العربية، أما information في الإنجليزية فلا تُجمع.",
        "هذا التعبير يبدو مترجمًا وأقل شيوعًا في الكلام اليومي، لذلك الأقرب هو make a decision.",
        "جملة عن الخطاب والكتابة",       # «خطاب» (speech) must not trip the «خطأ» rule
        "",
    ],
)
def test_good_arabic_notes_pass(note):
    assert arabic_note_problems(note) == []


@pytest.mark.parametrize(
    "note,reason",
    [
        ("هذا خطأ شائع عند العرب.", "stigmatising"),
        ("هذه ترجمة حرفية سيئة من العربية.", "stigmatising"),
        ("لا يقال أبدًا في الإنجليزية.", "absolute"),
        ("تعبير غلط", "stigmatising"),
        ("This note is not in Arabic at all.", "not Arabic"),
        ("كلمة " * 60, "too long"),
        ("العرب دائمًا مباشرون", "generalises"),
    ],
)
def test_bad_arabic_notes_are_caught(note, reason):
    problems = arabic_note_problems(note)
    assert problems, note
    assert any(reason.split()[0] in p for p in problems), problems


# ------------------------------------------------------------------ sanitize_analysis


def test_findings_must_quote_the_learner_verbatim():
    good, bad = mk_finding(), mk_finding(span="made a decision", pattern_key="x")
    a, notes = sanitize_analysis(Analysis(findings=[good, bad]), "I think I will take a decision tomorrow")
    assert [f.span for f in a.findings] == ["take a decision"]
    assert any("span not in learner text" in n for n in notes)


def test_duplicate_spans_and_findings_with_nothing_to_teach_are_dropped():
    f1 = mk_finding()
    f2 = mk_finding(pattern_key="other")
    f3 = mk_finding(span="tomorrow", pattern_key="third", alternatives=[])
    a, notes = sanitize_analysis(Analysis(findings=[f1, f2, f3]), "I will take a decision tomorrow")
    assert len(a.findings) == 1 and len(notes) == 2


def test_stigmatising_arabic_note_is_blanked_not_shown():
    f = mk_finding(ar_note="هذا خطأ شائع")
    a, notes = sanitize_analysis(Analysis(findings=[f]), "I take a decision")
    assert a.findings[0].ar_note == "" and any("ar_note" in n for n in notes)


def test_unknown_l1_pattern_means_no_transfer_claim():
    f = mk_finding(l1_pattern="MADE_UP")
    a, _ = sanitize_analysis(Analysis(findings=[f]), "I take a decision")
    assert a.findings[0].l1_transfer is False and a.findings[0].l1_source_ar is None


def test_used_targets_are_verified_and_echoes_forced():
    uts = [
        UsedTarget(target_id="x1", form_used="my plate's pretty full", correct=True, naturalness=3, echo=False),
        UsedTarget(target_id="ghost", form_used="my plate's pretty full", correct=True),
        UsedTarget(target_id="x1", form_used="completely invented words", correct=True),
    ]
    a, notes = sanitize_analysis(
        Analysis(used_targets=uts), "Honestly my plate's pretty full this week",
        active_target_ids=["x1"], recent_texts=["Yeah, I figured your plate was pretty full."],
    )
    assert len(a.used_targets) == 1 and a.used_targets[0].echo is True
    assert len(notes) == 3
    a2, _ = sanitize_analysis(Analysis(used_targets=uts[:1]), "Honestly my plate's pretty full this week", active_target_ids=["x1"], recent_texts=[])
    assert a2.used_targets[0].echo is False


def test_missing_recast_meaning_is_backfilled():
    f = mk_finding(recast_meaning=None)
    a, _ = sanitize_analysis(Analysis(findings=[f]), "I take a decision")
    assert a.findings[0].recast_meaning


# ------------------------------------------------------------------ check_reply


def brief(move="SHARE", n=15, **kw) -> DirectorBrief:
    return DirectorBrief(dialogue_move=move, length_words=n, end_with_question=move in ("ASK", "CLARIFY"), **kw)


def reply(*bubbles, **kw) -> ResponderReply:
    return ResponderReply(bubbles=list(bubbles), **kw)


def test_a_clean_reply_passes():
    assert check_reply(reply("Honestly, I'd just skip it. Cheaper, faster."), brief(n=10)) == []


def test_questions_only_where_the_brief_allows():
    assert check_reply(reply("That's rough. Want to grab lunch?"), brief("SHARE")) != []
    assert check_reply(reply("Yeah, that's rough, right?"), brief("SHARE")) == []            # tag question is texture
    assert check_reply(reply("Which deck is it?"), brief("ASK", 6)) == []
    assert any("exactly one" in v for v in check_reply(reply("Which deck? And when is it due?"), brief("ASK", 10)))
    assert any("ending with a question" in v for v in check_reply(reply("Tell me which deck."), brief("ASK", 6)))


def test_assistant_isms_teaching_and_formatting_are_rejected():
    assert check_reply(reply("Great question! It depends."), brief()) != []
    assert check_reply(reply("As an AI I can't say."), brief()) != []
    assert check_reply(reply("Nice phrase, by the way."), brief()) != []
    assert check_reply(reply("Here are two things:\n- one\n- two"), brief(n=20)) != []
    assert check_reply(reply("*laughs* okay then."), brief()) != []


def test_length_limits():
    long = " ".join(["word"] * 40)
    assert any("too long" in v for v in check_reply(reply(long), brief(n=10)))
    assert any("12 words" in v for v in check_reply(reply(" ".join(["ha"] * 14)), brief("REACT", 20)))
    assert any("5 words" in v for v in check_reply(reply("Mm, go on then, I am listening."), brief("BACKCHANNEL", 20)))


def test_avoided_openers():
    assert any("avoided openers" in v for v in check_reply(reply("Honestly, no."), brief(avoid_openers=["honestly"], n=5)))


def test_recast_must_be_silent_and_must_not_copy_the_error():
    rc = RecastBrief(id="rc_1", learner_said="I have many works", natural_form="my plate's pretty full", meaning="busy")
    b = brief(recast=rc, n=15)
    assert check_reply(reply("Yeah, I figured your plate was pretty full."), b) == []
    assert any("silent" in v for v in check_reply(reply("You mean your plate's full? Yeah."), b))
    assert any("repeats" in v for v in check_reply(reply("Right, I have many works too."), b))


def test_the_character_never_says_the_stealth_target():
    expr = Expression(id="t1", form="I'm swamped", acceptable_forms=["my plate's full"])
    forbidden = forbidden_phrases_for([expr])
    assert forbidden == ["I'm swamped", "my plate's full"]
    st = [StealthTarget(id="t1", expression="I'm swamped", opportunity_hint="a request")]
    b = brief("ASK", 15, stealth_targets=st)
    assert check_reply(reply("Any chance you could take the deck today?"), b, forbidden_phrases=forbidden) == []
    assert any("stealth" in v for v in check_reply(reply("I'm swamped, can you take it?"), b, forbidden_phrases=forbidden))


def test_brief_model_enforces_its_own_rules():
    with pytest.raises(ValueError):
        DirectorBrief(dialogue_move="SHARE", length_words=10, end_with_question=True)
    with pytest.raises(ValueError):
        brief(recast=RecastBrief(id="a", learner_said="x", natural_form="y"),
              stealth_targets=[StealthTarget(id="t", expression="e")])


# ------------------------------------------------------------------ telegram html


def test_valid_card():
    html = "💡 <i>take a decision</i> is understood, but English pairs it with make: <b>make the call</b>.\n<tg-spoiler>في العربية نقول «اتّخذ قرارًا».</tg-spoiler>"
    assert validate_telegram_html(html, max_visible=600) == []


@pytest.mark.parametrize(
    "html,fragment",
    [
        ("<p>hello</p>", "not allowed"),
        ("<b>unclosed", "unclosed"),
        ("<b>x</i>", "unbalanced"),
        ("Tom & Jerry", "unescaped '&'"),
        ("a < b", "unescaped"),
        ("x" * 700, "too long"),
        ("💡 one 🎯 two", "emoji"),
        ("hello 💡", "emoji"),
        ("- one\n- two", "bullets"),
        ('<a href="x">x</a>', "not allowed"),
        ("<tg-spoiler>هذا خطأ شائع</tg-spoiler>", "arabic spoiler"),
    ],
)
def test_invalid_html_is_rejected(html, fragment):
    problems = validate_telegram_html(html, max_visible=600)
    assert problems and any(fragment in p for p in problems), problems


def test_arabic_is_rejected_when_the_learner_turned_it_off():
    assert validate_telegram_html("<tg-spoiler>مرحبا</tg-spoiler>", max_visible=600, allow_arabic=False)
    assert validate_telegram_html("<i>hi</i> &amp; bye", max_visible=600, allow_arabic=False) == []
