"""S3: retrieval.py (S0 5.7). Python-first grading, echo, judge mapping, shed, prompts and hints."""

from __future__ import annotations

import pytest

import retrieval
from retrieval import (
    ECHO_JACCARD,
    JudgeRequest,
    JudgeShed,
    JudgeVerdict,
    PhraseEntry,
    Target,
    build_hint,
    build_prompt,
    default_shed_exceptions,
    expand_token,
    find_match,
    grade_attempt,
    grade_reconstruction,
    is_echo,
    match_form,
    outcome_from_levels,
    to_attempt_event,
)
from schemas import MatchedBy, PromptKind, RetrievalAttemptedV2, RetrievalOrigin, RetrievalOutcome

CANON = "come to think of it"
TARGET = Target(CANON, ("now that I think about it",))


def entry(**over) -> PhraseEntry:
    base = dict(
        phrase_id="come_to_think_of_it",
        canonical_form=CANON,
        communicative_intention="add_an_afterthought",
        arabic_gloss="بالمناسبة",
        context_families=("workplace", "friendships"),
    )
    base.update(over)
    return PhraseEntry(**base)


# ------------------------------------------------------------------------------ contractions


@pytest.mark.parametrize(
    "token,expanded",
    [
        ("don't", ("do", "not")),
        ("can't", ("can", "not")),
        ("won't", ("will", "not")),
        ("i'm", ("i", "am")),
        ("we're", ("we", "are")),
        ("they've", ("they", "have")),
        ("she'll", ("she", "will")),
        ("it's", ("it", "is")),
        ("gonna", ("going", "to")),
        ("let's", ("let", "us")),
        ("plate", ("plate",)),
        ("n't", ("n't",)),
    ],
)
def test_expand_token(token, expanded):
    assert expand_token(token) == expanded


# ------------------------------------------------------------------------------ matching


def test_a_form_matches_token_aligned_ignoring_case_and_punctuation():
    assert match_form("Well... COME to think of it, I agree!", CANON) == MatchedBy.PYTHON_EXACT


def test_a_form_does_not_match_inside_other_words():
    assert match_form("I welcome to think of it as fun", CANON) is None
    assert match_form("come to think", CANON) is None


def test_contractions_match_only_after_expansion_and_are_then_fuzzy():
    assert match_form("I don't know", "I don't know") == MatchedBy.PYTHON_EXACT
    assert match_form("I do not know", "I don't know") == MatchedBy.PYTHON_FUZZY
    assert match_form("I don't know", "I do not know") == MatchedBy.PYTHON_FUZZY


def test_curly_apostrophes_are_the_same_as_straight_ones():
    assert match_form("I don’t know", "I don't know") == MatchedBy.PYTHON_EXACT


def test_a_slot_matches_one_to_six_words():
    form = "run [something] by [someone]"
    assert match_form("can I run this by you", form) == MatchedBy.PYTHON_EXACT
    assert match_form("let me run the whole plan for the week by the team", form) == MatchedBy.PYTHON_EXACT  # 6 + 2 words
    assert match_form("run by you", form) is None  # an empty slot is not a match
    assert match_form("run one two three four five six seven by you", form) is None  # seven words in a slot


def test_a_pattern_of_only_slots_never_matches():
    assert match_form("anything at all", "[something]") is None


def test_find_match_tries_the_canonical_form_then_the_acceptable_forms():
    assert find_match("now that I think about it, yes", TARGET) == ("now that I think about it", MatchedBy.PYTHON_EXACT)
    assert find_match("come to think of it, yes", TARGET) == (CANON, MatchedBy.PYTHON_EXACT)
    assert find_match("no idea", TARGET) is None


# ------------------------------------------------------------------------------ echo


def test_echo_is_a_token_jaccard_of_at_least_point_eight_against_anything_shown():
    shown = "Say something that adds a late thought to the plan"
    assert is_echo("say something that adds a late thought to the plan", [shown])
    assert not is_echo("Honestly, come to think of it, the plan is fine", [shown])
    assert ECHO_JACCARD == 0.8


def test_echo_boundary_is_inclusive():
    # 4 shared of 5 distinct tokens = 0.8 exactly
    assert is_echo("a b c d", ["a b c d e"])
    assert not is_echo("a b c", ["a b c d e"])


def test_empty_shown_texts_never_make_an_echo():
    assert not is_echo("anything", [])
    assert not is_echo("anything", [""])


def test_an_echo_is_checked_before_the_python_match_and_gets_no_credit():
    prompt = f"Use this: {CANON}"
    result = grade_attempt(f"use this {CANON}", TARGET, shown_texts=[prompt])
    assert result.echo and not result.records_evidence and not result.shed
    assert result.outcome == RetrievalOutcome.FAILURE
    assert result.linguistic_level is None and result.functional_level is None
    assert result.matched_by == MatchedBy.PYTHON_EXACT


# ------------------------------------------------------------------------------ grading: Python match


def test_a_python_match_is_a_success_with_levels_3_and_2():
    result = grade_attempt("Come to think of it, I can help.", TARGET)
    assert result.outcome == RetrievalOutcome.SUCCESS
    assert (result.linguistic_level, result.functional_level) == (3, 2)
    assert result.matched_by == MatchedBy.PYTHON_EXACT and result.matched_form == CANON
    assert result.records_evidence


@pytest.mark.parametrize("hint", [1, 2])
def test_a_python_match_with_a_hint_is_a_hinted_success(hint):
    result = grade_attempt("come to think of it", TARGET, hint_level=hint)
    assert result.outcome == RetrievalOutcome.HINTED_SUCCESS and result.hint_level == hint


def test_a_python_match_never_calls_the_judge():
    def judge(_request):
        raise AssertionError("the judge must not be called")

    assert grade_attempt("come to think of it", TARGET, judge=judge).outcome == RetrievalOutcome.SUCCESS


def test_a_fuzzy_match_is_reported_as_such():
    result = grade_attempt("I do not know", Target("I don't know"))
    assert result.matched_by == MatchedBy.PYTHON_FUZZY and result.outcome == RetrievalOutcome.SUCCESS


def test_a_hint_level_above_2_is_a_programming_error():
    with pytest.raises(ValueError):
        grade_attempt("x", TARGET, hint_level=3)


# ------------------------------------------------------------------------------ grading: the judge


@pytest.mark.parametrize(
    "linguistic,functional,hint,expected",
    [
        (3, 3, 0, "success"),
        (2, 2, 0, "success"),
        (2, 2, 1, "hinted_success"),
        (3, 2, 2, "hinted_success"),
        (1, 2, 0, "partial"),
        (1, 3, 0, "partial"),
        (2, 1, 0, "partial"),
        (3, 1, 0, "partial"),
        (1, 1, 0, "failure"),
        (0, 3, 0, "failure"),
        (3, 0, 0, "failure"),
        (0, 0, 2, "failure"),
    ],
)
def test_the_judge_returns_levels_and_python_maps_them_to_an_outcome(linguistic, functional, hint, expected):
    assert outcome_from_levels(linguistic, functional, hint).value == expected
    result = grade_attempt("it occurs to me now", TARGET, hint_level=hint, judge=lambda r: JudgeVerdict(linguistic, functional))
    assert result.outcome.value == expected
    assert result.matched_by == MatchedBy.LLM_JUDGE and result.matched_form is None
    assert (result.linguistic_level, result.functional_level) == (linguistic, functional)


def test_the_judge_request_carries_what_it_needs_and_never_an_outcome_field():
    seen: list[JudgeRequest] = []

    def judge(request):
        seen.append(request)
        return JudgeVerdict.from_mapping({"linguistic_level": 2, "functional_level": 3, "functional_equivalent_credited": True})

    result = grade_attempt(
        "it occurs to me now",
        TARGET,
        shown_texts=["a prompt"],
        hint_level=1,
        judge=judge,
        prompt_kind="situation",
        context_family="workplace",
        communicative_intention="add_an_afterthought",
    )
    request = seen[0]
    assert (request.target_form, request.acceptable_forms) == (CANON, ("now that I think about it",))
    assert (request.prompt_kind, request.context_family, request.hint_level) == ("situation", "workplace", 1)
    assert request.shown_texts == ("a prompt",) and request.communicative_intention == "add_an_afterthought"
    assert result.functional_equivalent_credited is True
    assert not hasattr(JudgeVerdict(1, 1), "outcome")


@pytest.mark.parametrize("bad", [4, -1, 2.5, "2", True, None])
def test_a_judge_level_outside_0_to_3_is_rejected(bad):
    with pytest.raises(ValueError):
        grade_attempt("nothing", TARGET, judge=lambda r: JudgeVerdict(bad, 2))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        grade_attempt("nothing", TARGET, judge=lambda r: JudgeVerdict(2, bad))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ shed


def test_without_a_judge_a_non_matching_attempt_is_shed_and_records_nothing():
    result = grade_attempt("no idea", TARGET)
    assert result.shed and result.outcome is None and not result.records_evidence


@pytest.mark.parametrize("exc", [JudgeShed("quota"), TimeoutError("deadline")])
def test_a_shed_or_a_missed_deadline_records_nothing(exc):
    def judge(_request):
        raise exc

    result = grade_attempt("no idea", TARGET, judge=judge)
    assert result.shed and result.outcome is None and not result.records_evidence


def test_custom_shed_exceptions_are_honoured_and_other_errors_propagate():
    class Quota(RuntimeError):
        pass

    def judge(_request):
        raise Quota()

    assert grade_attempt("no idea", TARGET, judge=judge, shed_exceptions=(Quota,)).shed
    with pytest.raises(Quota):
        grade_attempt("no idea", TARGET, judge=judge, shed_exceptions=(JudgeShed,))

    def broken(_request):
        raise KeyError("bug")

    with pytest.raises(KeyError):
        grade_attempt("no idea", TARGET, judge=broken)


def test_default_shed_exceptions_cover_the_known_cases():
    excs = default_shed_exceptions()
    assert JudgeShed in excs and TimeoutError in excs


def test_a_shed_result_cannot_become_an_event():
    shed = grade_attempt("no idea", TARGET)
    with pytest.raises(ValueError):
        to_attempt_event(
            shed, phrase_id="p", origin=RetrievalOrigin.REVIEW, prompt_kind=PromptKind.MEANING,
            context_family="general", due=True, schedule_step_before=0,
        )


# ------------------------------------------------------------------------------ reconstruction


def test_reconstruction_grades_against_the_diagnostics_alternative_and_is_never_hinted():
    ok = grade_reconstruction("Let's discuss the plan", "discuss the plan")
    assert ok.outcome == RetrievalOutcome.SUCCESS and ok.hint_level == 0 and ok.matched_form == "discuss the plan"
    again = grade_reconstruction("talk about the plan", "discuss the plan", judge=lambda r: JudgeVerdict(1, 3))
    assert again.outcome == RetrievalOutcome.PARTIAL


def test_a_reconstruction_that_repeats_the_card_is_an_echo():
    result = grade_reconstruction("discuss the plan", "discuss the plan", shown_texts=["discuss the plan"])
    assert result.echo and not result.records_evidence


# ------------------------------------------------------------------------------ the event


def test_to_attempt_event_builds_a_valid_v2_payload():
    result = grade_attempt("come to think of it", TARGET, hint_level=1)
    event = to_attempt_event(
        result, phrase_id="come_to_think_of_it", origin=RetrievalOrigin.REVIEW, prompt_kind=PromptKind.SITUATION,
        context_family="workplace", due=True, schedule_step_before=2, interaction_id=7,
    )
    assert isinstance(event, RetrievalAttemptedV2)
    dumped = event.model_dump(mode="json")
    assert dumped["outcome"] == "hinted_success" and dumped["hint_level"] == 1
    assert dumped["linguistic_level"] == 3 and dumped["functional_level"] == 2
    assert dumped["matched_by"] == "python_exact" and dumped["matched_form"] == CANON
    assert dumped["due"] is True and dumped["schedule_step_before"] == 2 and dumped["interaction_id"] == 7
    assert RetrievalAttemptedV2.model_validate(dumped) == event


def test_an_echo_becomes_an_event_with_the_echo_flag_and_no_levels():
    result = grade_attempt(CANON, TARGET, shown_texts=[CANON])
    event = to_attempt_event(
        result, phrase_id="p", origin=RetrievalOrigin.REVIEW, prompt_kind=PromptKind.MEANING,
        context_family="general", due=False, schedule_step_before=0,
    )
    assert event.echo is True and event.linguistic_level is None and event.functional_level is None


# ------------------------------------------------------------------------------ prompts and hints


def test_stored_prompts_are_used_for_their_kind():
    e = entry(meaning_prompt="  Meaning prompt.  ", situation_prompt="Situation prompt.", function_prompt="Function prompt.")
    assert build_prompt(e, PromptKind.MEANING) == "Meaning prompt."
    assert build_prompt(e, "situation") == "Situation prompt."
    assert build_prompt(e, PromptKind.FUNCTION) == "Function prompt."


@pytest.mark.parametrize("kind", ["meaning", "situation", "function"])
def test_a_null_prompt_falls_back_to_the_communicative_intention_and_never_shows_the_target(kind):
    text = build_prompt(entry(), kind, "workplace")
    assert "add an afterthought" in text
    assert CANON not in text.lower()
    assert "come to think" not in text.lower()


def test_the_fallback_uses_the_family_for_the_situation_kind_only():
    assert "workplace" in build_prompt(entry(), "situation", "workplace")
    assert "everyday" in build_prompt(entry(), "situation", "general")
    assert "customer service" in build_prompt(entry(), "situation", "customer_service")


def test_a_blank_stored_prompt_is_treated_as_missing():
    assert "add an afterthought" in build_prompt(entry(meaning_prompt="   "), "meaning")


def test_an_unknown_prompt_kind_is_an_error():
    with pytest.raises(ValueError):
        build_prompt(entry(), "riddle")


def test_hint_level_0_is_nothing_level_1_is_the_structure_level_2_is_the_gloss():
    e = entry()
    assert build_hint(e, 0) is None
    assert build_hint(e, 1) == "c___ t_ t____ o_ i_"
    assert build_hint(e, 2) == "بالمناسبة"


def test_hint_level_2_without_a_gloss_gives_the_first_word_and_slots_stay_visible_at_level_1():
    e = entry(arabic_gloss=None, canonical_form="run [something] by [someone]")
    assert build_hint(e, 1) == "r__ … b_ …"
    assert build_hint(e, 2) == "It starts with “run”."


def test_no_hint_reveals_the_whole_phrase():
    e = entry()
    for level in (1, 2):
        assert CANON not in (build_hint(e, level) or "").lower()


def test_module_constants():
    assert retrieval.PY_MATCH_LEVELS == (3, 2)
    assert retrieval.MAX_HINT_LEVEL == 2 and retrieval.MAX_SLOT_WORDS == 6
