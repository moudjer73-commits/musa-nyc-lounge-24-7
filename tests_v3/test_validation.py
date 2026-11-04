"""S2: validation.py (S0 5.1). One test per step and per gate, plus the L1 accuracy rule."""

from __future__ import annotations

import copy
import re

import pytest

import database
import validation as V
from schemas import AnalysisV2, DIAGNOSTIC_ID_PATTERN, DropReason, Severity

TEXT = "We discussed about the plan yesterday and I did an effort."
SPAN_DISCUSS = "discussed about"


def raw_diag(**over):
    base = dict(
        original_span=SPAN_DISCUSS,
        normalized_pattern_id="COLLOC_PREP_VERB:discuss_about",
        relevant_context_family="global",
        intended_meaning="talk about a topic in detail",
        kind="collocation",
        severity="medium",
        alternative="discussed",
        alternative_status="required",
        listener_effect="Clear, but it sounds slightly off.",
        explanation_en="In English, 'discuss' takes its object directly: 'discussed the plan'.",
        preserves_intended_meaning=True,
        context_fit_verified=True,
        repair_scope="phrase_span",
        communicative_impact=2,
        learning_value=2,
        linguistic_confidence=0.9,
        domain="linguistic",
        classification="error",
    )
    base.update(over)
    return {k: v for k, v in base.items() if v is not ...}


EFFORT = dict(
    original_span="did an effort",
    normalized_pattern_id="COLLOC_LIGHT_VERB:make_effort",
    kind="collocation",
    alternative="made an effort",
    explanation_en="In English, 'effort' pairs with 'make', not 'do'.",
)


def run(diags, text=TEXT, **kw):
    kw.setdefault("turn_event_id", "ev_turn0001")
    return V.validate_analysis({"diagnostics": diags}, learner_text=text, **kw)


def reasons(result):
    return [(d.ref, d.reason.value) for d in result.dropped]


def only_reason(result):
    assert len(result.dropped) == 1, result.dropped
    return result.dropped[0].reason


# ----------------------------------------------------------------------------- step 3: text


def test_normalize_text_follows_the_contract():
    assert V.normalize_text("  Don’t   STOP” \n") == "don't stop\""
    assert V.normalize_text("‘Hi’") == "'hi'"
    assert V.normalize_text("ﬁne") == "fine"  # NFKC ligature
    assert V.normalize_text("ＡＢ") == "ab"  # NFKC full-width
    assert V.normalize_text("Straße") == "strasse"  # casefold, not lower


def test_tokenize_keeps_contractions_whole_and_offsets_in_the_original():
    text = "He said: “I don’t know, really.”"
    toks = V.tokenize(text)
    assert [t.norm for t in toks] == ["he", "said", "i", "don't", "know", "really"]
    for t in toks:
        assert text[t.start : t.end] == t.text
    assert text[toks[3].start : toks[3].end] == "don’t"


def test_tokenize_handles_arabic_and_marks():
    text = "ناقشَ الموضوعَ hello"
    toks = V.tokenize(text)
    assert [text[t.start : t.end] for t in toks] == ["ناقشَ", "الموضوعَ", "hello"]


def test_tokenize_offsets_survive_casefold_length_changes():
    text = "Straße Plan"
    toks = V.tokenize(text)
    assert toks[0].norm == "strasse"
    assert (toks[0].start, toks[0].end) == (0, 6)
    assert text[toks[1].start : toks[1].end] == "Plan"


# ----------------------------------------------------------------------------- step 4: span


def test_span_offsets_and_exact_casing_come_from_the_learner_text():
    text = "We Discussed About the plan."
    result = run([raw_diag(original_span="discussed about")], text=text, )
    d = result.analysis.diagnostics[0]
    assert text[d.span_start : d.span_end] == "Discussed About"
    assert d.original_span == "Discussed About"


def test_span_in_never_matches_inside_thinking():
    result = run([raw_diag(original_span="in", alternative="on")], text="I was thinking about it.")
    assert result.analysis.diagnostics == []
    assert reasons(result) == [("d0", "span_boundary")]


def test_span_that_is_not_in_the_text_is_missing():
    result = run([raw_diag(original_span="talked about")])
    assert reasons(result) == [("d0", "span_missing")]


def test_span_with_no_word_characters_is_missing():
    result = run([raw_diag(original_span="...")])
    assert reasons(result) == [("d0", "span_missing")]


def test_first_match_wins():
    text = "I did an effort. Then I did an effort again."
    result = run([raw_diag(**EFFORT)], text=text)
    d = result.analysis.diagnostics[0]
    assert (d.span_start, d.span_end) == (text.index("did an effort"), text.index("did an effort") + len("did an effort"))


def test_tokens_must_be_contiguous():
    result = run([raw_diag(**{**EFFORT, "original_span": "did effort"})])
    assert reasons(result) == [("d0", "span_missing")]


def test_curly_and_straight_apostrophes_match_each_other():
    text = "I don’t has time."
    result = run([raw_diag(original_span="don't has", alternative="don't have", normalized_pattern_id="GRAM_AGREE:dont_has", kind="grammar")], text=text)
    d = result.analysis.diagnostics[0]
    assert d.original_span == "don’t has"


def test_utterance_rewrite_uses_the_whole_text_and_ignores_the_llm_span():
    result = run(
        [raw_diag(repair_scope="utterance_rewrite", original_span="completely invented span", alternative="We discussed the plan yesterday, and I made an effort.")]
    )
    d = result.analysis.diagnostics[0]
    assert (d.span_start, d.span_end) == (0, len(TEXT))
    assert d.original_span == TEXT
    assert result.dropped == []


def test_utterance_rewrite_on_blank_text_is_span_missing():
    result = run([raw_diag(repair_scope="utterance_rewrite")], text="   ")
    assert reasons(result) == [("d0", "span_missing")]


# ----------------------------------------------------------------------------- step 1: schema


def test_a_bad_diagnostic_is_dropped_not_the_whole_analysis():
    result = run([raw_diag(kind="not_a_kind"), raw_diag(**EFFORT)])
    assert [d.original_span for d in result.analysis.diagnostics] == ["did an effort"]
    assert reasons(result) == [("d0", "schema")]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "text",
        [],
        raw_diag(kind="not_a_kind"),
        raw_diag(severity="catastrophic"),
        raw_diag(extra_field="x"),
        raw_diag(linguistic_confidence=...),  # both confidences missing
        raw_diag(linguistic_confidence=1.7),
        raw_diag(alternative=None, alternative_status="required"),
        raw_diag(alternative_status="optional"),  # an error cannot be optional
        raw_diag(communicative_impact=4),
        raw_diag(classification="sideways"),
        raw_diag(domain="astral"),
        raw_diag(l1_transfer_likely=True, l1_transfer_evidence=None),
    ],
)
def test_schema_failures_are_schema_drops(bad):
    result = run([bad])
    assert result.analysis.diagnostics == []
    assert only_reason(result) == DropReason.SCHEMA


def test_llm_supplied_ids_offsets_and_confidence_are_ignored():
    result = run([raw_diag(diagnostic_id="d1", span_start=40, span_end=44, confidence_score=0.1, confidence_band="low")])
    d = result.analysis.diagnostics[0]
    assert d.diagnostic_id != "d1" and re.match(DIAGNOSTIC_ID_PATTERN, d.diagnostic_id)
    assert d.span_start == TEXT.index(SPAN_DISCUSS)
    assert d.confidence_score == 0.9 and d.confidence_band.value == "high"


@pytest.mark.parametrize("raw", [None, [], "x", 7, {"diagnostics": "x"}, {"diagnostics": [1, None, []]}, {"p0": "x", "communicative": 3}])
def test_garbage_never_raises(raw):
    result = V.validate_analysis(raw, turn_event_id="ev_x", learner_text=TEXT)
    assert isinstance(result.analysis, AnalysisV2)
    assert result.analysis.diagnostics == []


def test_top_level_shapes_communicative_p0_and_call_meta():
    flat = {
        "communicative_intention": "tell_story",
        "in_emotional_or_story_flow": True,
        "p0": {"candidate": True, "line": "Did you mean the Monday meeting?"},
        "diagnostics": [],
    }
    r = V.validate_analysis(flat, turn_event_id="ev_x", learner_text=TEXT)
    assert r.analysis.communicative.in_emotional_or_story_flow is True
    assert r.analysis.communicative.communicative_intention == "tell_story"
    assert r.analysis.p0.candidate is True
    nested = V.validate_analysis({"communicative": {"requires_clarification": True}}, turn_event_id="ev_x", learner_text=TEXT)
    assert nested.analysis.communicative.requires_clarification is True
    bad = V.validate_analysis({"p0": {"candidate": True, "line": " ".join(["w"] * 21)}}, turn_event_id="ev_x", learner_text=TEXT)
    assert reasons(bad) == [("p0", "schema")]
    assert bad.analysis.p0.candidate is False


def test_analysis_round_trips_through_json():
    result = run([raw_diag(), raw_diag(**EFFORT)])
    payload = result.analysis.model_dump(mode="json")
    assert AnalysisV2.model_validate(payload) == result.analysis


# ----------------------------------------------------------------------------- step 2: domain and modality


def test_phonological_is_rejected():
    assert only_reason(run([raw_diag(domain="phonological")])) == DropReason.DOMAIN_MODALITY


def test_any_acoustic_confidence_is_rejected_as_domain_modality_not_schema():
    assert only_reason(run([raw_diag(acoustic_confidence=0.0)])) == DropReason.DOMAIN_MODALITY
    assert only_reason(run([raw_diag(acoustic_confidence=0.9)])) == DropReason.DOMAIN_MODALITY


def test_a_claim_that_needs_audio_is_rejected_for_text_input():
    claim = raw_diag(explanation_en="The pronunciation of 'discussed' is off.")
    assert only_reason(run([claim])) == DropReason.DOMAIN_MODALITY
    assert run([claim], input_modality="voice").analysis.diagnostics  # still allowed to be judged on a voice turn


def test_listener_effect_wording_is_not_an_audio_claim():
    ok = raw_diag(listener_effect="It sounds translated word for word.")
    assert run([ok]).analysis.diagnostics


# ----------------------------------------------------------------------------- step 5: no-op


def test_alternative_equal_to_the_span_after_normalisation_is_a_noop():
    text = "I don’t know."
    r = run([raw_diag(original_span="don't know", alternative="Don’t   know", kind="grammar", normalized_pattern_id="GRAM_X:noop")], text=text)
    assert only_reason(r) == DropReason.NOOP


@pytest.mark.parametrize("alt", [None, "", "   "])
def test_empty_alternative_is_a_noop(alt):
    r = run([raw_diag(alternative=alt, alternative_status="recommended")])
    assert only_reason(r) == DropReason.NOOP


# ----------------------------------------------------------------------------- step 6: classification


def test_claimed_classification_must_match_the_kind():
    r = run([raw_diag(classification="upgrade")])
    assert only_reason(r) == DropReason.CLASSIFICATION_MISMATCH


def test_insufficient_context_is_never_a_finding():
    r = run([raw_diag(kind="insufficient_context", alternative_status="optional")])
    assert only_reason(r) == DropReason.CLASSIFICATION_MISMATCH


def test_claimed_domain_must_match_the_kind():
    assert only_reason(run([raw_diag(domain="pragmatic")])) == DropReason.CLASSIFICATION_MISMATCH


def test_missing_classification_and_domain_are_derived():
    r = run([raw_diag(classification=..., domain=...)])
    d = r.analysis.diagnostics[0]
    assert d.classification.value == "error" and d.domain.value == "linguistic"


def test_upgrade_and_alternative_kinds_map_to_their_classes():
    up = raw_diag(kind="optional_upgrade", classification="upgrade", alternative_status="optional", alternative="we talked through the plan",
                  normalized_pattern_id="UPG_X:talk_through", domain="linguistic")
    alt = raw_diag(**{**EFFORT, "kind": "acceptable_variation", "alternative": "put in effort"}, classification="alternative",
                   alternative_status="optional", domain="linguistic")
    r = run([up, alt])
    assert {d.classification.value for d in r.analysis.diagnostics} == {"upgrade", "alternative"}


# ----------------------------------------------------------------------------- step 7: pattern code and notes


@pytest.mark.parametrize("pattern", ["has space", "9starts_with_digit", "x" * 90, "ab", "bad:colon:twice", "a/b"])
def test_malformed_pattern_ids_are_dropped(pattern):
    r = run([raw_diag(normalized_pattern_id=pattern)])
    assert r.analysis.diagnostics == []
    assert only_reason(r) in (DropReason.PATTERN_CODE, DropReason.SCHEMA)  # "ab" fails the model's min length first


def test_well_formed_pattern_ids_pass():
    for pattern in ("COLLOC_LIGHT_VERB:make_effort", "PAT_did_an_effort", "GRAM_TENSE", "X1_y:z-9.a"):
        assert run([raw_diag(**{**EFFORT, "normalized_pattern_id": pattern})]).analysis.diagnostics


def test_a_valid_l1_claim_is_kept():
    r = run([raw_diag(**EFFORT, l1_transfer_likely=True, l1_pattern="COLLOC_LIGHT_VERB", l1_source_ar="بذل جهدًا",
                      l1_transfer_evidence="Arabic uses a 'do/give' verb with effort.")])
    d = r.analysis.diagnostics[0]
    assert d.l1_transfer_likely is True and d.l1_pattern == "COLLOC_LIGHT_VERB"
    assert r.dropped == []


@pytest.mark.parametrize("code", ["NOT_A_CODE", "", None])
def test_unknown_l1_code_strips_the_claim_but_keeps_the_finding(code):
    r = run([raw_diag(**EFFORT, l1_transfer_likely=True, l1_pattern=code, l1_source_ar="بذل جهدًا", l1_transfer_evidence="Arabic uses a verb.")])
    d = r.analysis.diagnostics[0]
    assert d.l1_transfer_likely is False and d.l1_pattern is None and d.l1_source_ar is None and d.l1_transfer_evidence is None
    assert reasons(r) == [("d0.l1", "pattern_code")]


def test_l1_pattern_codes_are_case_insensitive():
    r = run([raw_diag(**EFFORT, l1_pattern="colloc_light_verb")])
    assert r.analysis.diagnostics[0].l1_pattern == "colloc_light_verb"
    assert r.dropped == []


def test_both_taxonomies_are_accepted():
    for code in ("PREP_CALQUE", "COLLOC_PREP_VERB", "COLLOC_OPEN_CLOSE", "SYNTAX_CALQUE", "PRAG_LITERAL_REFUSAL"):
        assert code in V.L1_PATTERN_CODES


# --- the S2 linguistic accuracy rule

DISCUSS_L1 = dict(
    l1_transfer_likely=True,
    l1_pattern="COLLOC_PREP_VERB",
    l1_source_ar="ناقش الموضوعَ",
    l1_transfer_evidence="Arabic prepositions often differ from English ones.",
)


@pytest.mark.parametrize("variant", ["discuss about", "Discussed about", "discusses about", "discussing about"])
def test_discuss_about_is_never_attributed_to_arabic(variant):
    text = f"We {variant} the plan."
    r = run([raw_diag(original_span=variant, alternative=variant.split()[0], **DISCUSS_L1)], text=text)
    d = r.analysis.diagnostics[0]
    assert d.l1_transfer_likely is False and d.l1_pattern is None and d.l1_source_ar is None
    assert ("d0.l1", "pattern_code") in reasons(r)
    assert d.alternative == variant.split()[0]  # the correction itself is kept


def test_discussion_about_is_fine_english_and_keeps_its_claim():
    text = "A discussion about the plan helped."
    r = run([raw_diag(original_span="discussion about", alternative="discussion of", kind="collocation", **DISCUSS_L1)], text=text)
    assert r.analysis.diagnostics[0].l1_transfer_likely is True


@pytest.mark.parametrize(
    "note",
    [
        "في العربية نقول «ناقش في الموضوع»، لذلك نقلتَ حرف الجر.",
        "ناقشوا في الخطة",
        "هم يناقشون عن الخطة",
        "وناقش على الأمر",
        "ناقشَ حولَ الموضوع",
        "نناقش بـ الخطة",
        "ناقشَ في",
    ],
)
def test_notes_asserting_an_ungrammatical_arabic_frame_are_removed(note):
    assert V.has_false_arabic_frame(note)
    r = run([raw_diag(**EFFORT, explanation_ar=note)])
    d = r.analysis.diagnostics[0]
    assert d.explanation_ar is None
    assert ("d0.explanation_ar", "note_tone") in reasons(r)


@pytest.mark.parametrize(
    "note",
    [
        "ناقشَ الموضوعَ",  # directly transitive: the correct Standard Arabic frame
        "في العربية نقول «ناقشنا الخطة» دون حرف جر.",
        "ناقشه في الأمر",  # person as the direct object, topic in a prepositional phrase
        "تناقشوا حول الموضوع",  # reciprocal form VI takes في/حول
        "تناقشنا في المسألة",
        "نقاش حول الموضوع",
        "ناقشوا الخطة في الاجتماع",
        "",
    ],
)
def test_valid_standard_arabic_is_not_flagged(note):
    assert not V.has_false_arabic_frame(note)


def test_false_frame_in_the_l1_source_strips_the_claim_and_the_field():
    r = run([raw_diag(**EFFORT, l1_transfer_likely=True, l1_pattern="COLLOC_LIGHT_VERB", l1_source_ar="ناقش في الأمر",
                      l1_transfer_evidence="Arabic uses a verb.")])
    d = r.analysis.diagnostics[0]
    assert d.l1_transfer_likely is False and d.l1_source_ar is None
    got = reasons(r)
    assert ("d0.l1_source_ar", "note_tone") in got and ("d0.l1", "pattern_code") in got


def test_false_frame_in_the_l1_evidence_strips_the_claim():
    r = run([raw_diag(**EFFORT, l1_transfer_likely=True, l1_pattern="COLLOC_LIGHT_VERB", l1_source_ar="بذل جهدًا",
                      l1_transfer_evidence="Arabic says «ناقش في» so the learner copies it.")])
    d = r.analysis.diagnostics[0]
    assert d.l1_transfer_likely is False and d.l1_transfer_evidence is None


def test_false_frame_in_the_english_explanation_drops_the_finding():
    r = run([raw_diag(explanation_en="Arabic «ناقش في الموضوع» is the source of 'discussed about'.")])
    assert r.analysis.diagnostics == []
    assert only_reason(r) == DropReason.NOTE_TONE


def test_false_frame_in_listener_effect_is_removed():
    r = run([raw_diag(listener_effect="Mirrors the Arabic «ناقش عن».")])
    assert r.analysis.diagnostics[0].listener_effect is None
    assert ("d0.listener_effect", "note_tone") in reasons(r)


def test_the_live_prompts_do_not_teach_the_false_frame():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("prompts/analyst_v3_system.md", "prompts/_partials/l1_taxonomy.md", "RENDERED_PROMPTS.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "«ناقش في»، «شرح لي»" not in text  # the old COLLOC_PREP_VERB line
        assert "(تناقشوا حول الموضوع) → discuss it" not in text  # the old PREP_CALQUE example
        assert "ناقش الموضوعَ" in text


# --- ported Arabic note tone rules


@pytest.mark.parametrize(
    "note,problem",
    [
        ("هذا خطأ شائع", "stigmatising wording"),
        ("هذه الجملة ركيكة؟ لا، هذا غلط", "stigmatising wording"),
        ("لا يقال أبدًا في الإنجليزية", "false absolute or stigmatising phrase"),
        ("العرب يخطئون هنا", "generalises about Arabs"),
        ("only english letters", "not Arabic"),
    ],
)
def test_arabic_note_tone_rules_blank_the_note(note, problem):
    assert problem in V.arabic_note_problems(note)
    r = run([raw_diag(**EFFORT, explanation_ar=note)])
    assert r.analysis.diagnostics[0].explanation_ar is None
    assert ("d0.explanation_ar", "note_tone") in reasons(r)


def test_a_neutral_arabic_note_is_kept():
    note = "في الإنجليزية نقول «made an effort» وليس «did an effort»؛ وهو أقل شيوعًا."
    assert V.arabic_note_problems(note) == []
    r = run([raw_diag(**EFFORT, explanation_ar=note)])
    assert r.analysis.diagnostics[0].explanation_ar == note and r.dropped == []


def test_overlong_optional_notes_are_blanked_not_fatal():
    r = run([raw_diag(**EFFORT, explanation_ar="ا" * 201, l1_source_ar="ب" * 81)])
    d = r.analysis.diagnostics[0]
    assert d.explanation_ar is None and d.l1_source_ar is None
    assert ("d0.explanation_ar", "note_tone") in reasons(r)
    r2 = run([raw_diag(**EFFORT, l1_pattern="X" * 65)])
    assert r2.analysis.diagnostics and ("d0.l1_pattern", "pattern_code") in reasons(r2)


# ----------------------------------------------------------------------------- step 8: abstention


@pytest.mark.parametrize("conf,kept", [(0.79, False), (0.7999, False), (0.80, True), (0.95, True), (0.0, False)])
def test_abstain_threshold(conf, kept):
    r = run([raw_diag(linguistic_confidence=conf)])
    assert bool(r.analysis.diagnostics) is kept
    if not kept:
        assert only_reason(r) == DropReason.ABSTAIN


def test_the_applicable_confidence_is_the_minimum_when_both_are_set():
    r = run([raw_diag(linguistic_confidence=0.95, pragmatic_confidence=0.7)])
    assert only_reason(r) == DropReason.ABSTAIN
    r2 = run([raw_diag(kind="pragmatics", domain="pragmatic", linguistic_confidence=..., pragmatic_confidence=0.9, normalized_pattern_id="PRAG_X:blunt",
                       alternative="Could you", original_span="did an effort")])
    assert r2.analysis.diagnostics[0].confidence_score == 0.9


def test_the_threshold_is_configurable():
    cfg = V.ValidationConfig(abstain_threshold=0.5)
    assert run([raw_diag(linguistic_confidence=0.6)], config=cfg).analysis.diagnostics
    assert V.DEFAULT_CONFIG.abstain_threshold == 0.80


def test_confidence_band_is_derived_by_code():
    d = run([raw_diag(linguistic_confidence=0.8)]).analysis.diagnostics[0]
    assert d.confidence_band.value == "high"
    d = run([raw_diag(linguistic_confidence=0.7)], config=V.ValidationConfig(abstain_threshold=0.5)).analysis.diagnostics[0]
    assert d.confidence_band.value == "medium"


# ----------------------------------------------------------------------------- step 9: score and overlap


def test_priority_score_formula():
    assert V.priority_score(Severity.MEDIUM, 2, 2, 0, 0.9) == 60.27
    assert V.priority_score(Severity.MEDIUM, 2, 2, 2, 0.9) == 67.77
    assert V.priority_score(Severity.MEDIUM, 2, 2, 9, 0.9) == 75.27  # frequency caps at 4
    assert V.priority_score(Severity.HIGH, 3, 3, 4, 1.0) == 100.0
    assert V.priority_score(Severity.LOW, 0, 0, 0, 0.0) == 9.9
    assert V.priority_score(Severity.LOW, 0, 0, -3, 0.0) == 9.9  # a negative count is treated as zero


def test_priority_weights_sum_to_one():
    assert sum(V.PRIORITY_WEIGHTS.values()) == pytest.approx(1.0)


def test_prior_counts_feed_the_score_from_either_key_shape():
    key = "COLLOC_PREP_VERB:discuss_about:global"
    base = run([raw_diag()]).scored[0].score
    a = run([raw_diag()], prior_counts={f"1001:{key}": 2}, learner_id=1001).scored[0]
    b = run([raw_diag()], prior_counts={key: 2}).scored[0]
    other = run([raw_diag()], prior_counts={f"2002:{key}": 4}, learner_id=1001).scored[0]
    assert a.score == b.score == round(base + 7.5, 2) and a.prior_occurrences == 2
    assert other.score == base and other.prior_occurrences == 0


def test_database_counts_are_read_in_the_database_key_shape(db):
    counts = db.get_authoritative_occurrence_counts(1001)
    assert counts == {}
    assert V.prior_occurrence_count({"1001:P:global": 3}, 1001, "P", "global") == 3


@pytest.mark.parametrize(
    "kind,family,expected",
    [("grammar", "workplace", "global"), ("collocation", "workplace", "global"), ("pragmatics", "workplace", "workplace"),
     ("register", "negotiation", "negotiation"), ("register", None, "global"), ("discourse", "family", "global")],
)
def test_projection_family_matches_the_database_rule(kind, family, expected):
    assert V.projection_family(kind, family) == expected
    assert database._context_family_for_pattern(kind, {"relevant_context_family": family or "global"}) == expected


def test_pragmatic_findings_look_up_their_context_family():
    d = raw_diag(kind="pragmatics", domain="pragmatic", relevant_context_family="workplace", normalized_pattern_id="PRAG_X:blunt",
                 original_span="did an effort", alternative="put in some effort")
    s = run([d], prior_counts={"PRAG_X:blunt:workplace": 4}).scored[0]
    assert s.family == "workplace" and s.prior_occurrences == 4


def test_overlapping_spans_keep_the_higher_score():
    low = raw_diag(original_span="discussed about the plan", alternative="discussed the plan", severity="low", normalized_pattern_id="A_LOW:x")
    high = raw_diag(original_span="about the plan yesterday", alternative="the plan yesterday", severity="high", normalized_pattern_id="A_HIGH:x")
    r = run([low, high])
    assert [d.normalized_pattern_id for d in r.analysis.diagnostics] == ["A_HIGH:x"]
    assert reasons(r) == [("d0", "overlap")]


def test_touching_spans_do_not_overlap():
    a = raw_diag(original_span="discussed about", normalized_pattern_id="A_ONE:x")
    b = raw_diag(original_span="the plan", alternative="our plan", normalized_pattern_id="A_TWO:x", kind="grammar")
    r = run([a, b])
    assert len(r.analysis.diagnostics) == 2 and r.dropped == []


def test_a_rewrite_that_outscores_local_findings_replaces_them_and_vice_versa():
    rewrite = raw_diag(repair_scope="utterance_rewrite", severity="high", alternative="We discussed the plan yesterday, and I made an effort.", normalized_pattern_id="REWRITE_X:all")
    local = raw_diag(severity="low", normalized_pattern_id="LOCAL_X:one")
    r = run([local, rewrite])
    assert [d.repair_scope.value for d in r.analysis.diagnostics] == ["utterance_rewrite"]
    assert reasons(r) == [("d0", "overlap")]
    rewrite_low = {**rewrite, "severity": "low", "communicative_impact": 0, "learning_value": 0}
    local_high = {**local, "severity": "high"}
    r2 = run([rewrite_low, local_high])
    assert [d.normalized_pattern_id for d in r2.analysis.diagnostics] == ["LOCAL_X:one"]


def test_survivors_are_ordered_by_score_then_position():
    a = raw_diag(**EFFORT, severity="low")
    b = raw_diag(severity="high")
    c = raw_diag(original_span="plan yesterday", alternative="plan", normalized_pattern_id="C_X:y", kind="grammar", severity="low")
    r = run([a, b, c])
    scores = [s.score for s in r.scored]
    assert scores == sorted(scores, reverse=True)
    assert [s.raw_index for s in r.scored][0] == 1
    assert [s.diagnostic for s in r.scored] == r.analysis.diagnostics


# ----------------------------------------------------------------------------- step 10: target assessments

TARGET = dict(
    target_id="ph_make_an_effort",
    matched_span="made an effort",
    was_revealed_in_task=False,
    was_directly_cued=False,
    fits_communicative_context=True,
    fits_register=True,
    phrase_specific_rubric_passed=True,
    evidence="spontaneous_production",
    confidence_band="high",
)


def run_targets(targets, text="Yesterday I made an effort to call her.", **kw):
    kw.setdefault("active_target_ids", {"ph_make_an_effort"})
    return V.validate_analysis({"target_assessments": targets}, turn_event_id="ev_t", learner_text=text, **kw)


def test_a_verified_target_use_is_kept_with_its_evidence():
    r = run_targets([TARGET])
    assert r.analysis.target_assessments[0].evidence.value == "spontaneous_production"
    assert r.dropped == []


def test_a_target_that_is_not_active_is_dropped():
    r = run_targets([TARGET], active_target_ids=set())
    assert r.analysis.target_assessments == [] and reasons(r) == [("t0", "schema")]


def test_matched_span_must_be_verbatim_in_the_learner_text():
    r = run_targets([{**TARGET, "matched_span": "made a big effort"}])
    assert r.analysis.target_assessments == [] and reasons(r) == [("t0", "span_missing")]


def test_matched_span_matches_across_curly_quotes_and_case():
    r = run_targets([{**TARGET, "matched_span": "I'm making an effort"}], text="I’m making an Effort today.")
    assert len(r.analysis.target_assessments) == 1


def test_echo_earns_no_spontaneous_or_independent_credit():
    for evidence in ("spontaneous_production", "independent_production"):
        r = run_targets([{**TARGET, "evidence": evidence}], recent_bot_texts=["Alex: You really made an effort there!"])
        assert r.analysis.target_assessments[0].evidence.value == "cued_production"
        assert reasons(r) == [("t0", "echo")]


def test_echo_in_card_text_counts_and_non_echo_is_untouched():
    r = run_targets([TARGET], recent_bot_texts=["Say it like this: made an effort"])
    assert r.analysis.target_assessments[0].evidence.value == "cued_production"
    clean = run_targets([TARGET], recent_bot_texts=["How was your weekend?"])
    assert clean.analysis.target_assessments[0].evidence.value == "spontaneous_production" and clean.dropped == []


def test_looks_like_echo_ported_behaviour():
    assert V.looks_like_echo("made an effort", ["He made an effort."])
    assert V.looks_like_echo("making efforts", ["He made an effort."]) is False  # 'efforts' stems to 'effort', 'making' to 'mak'
    assert V.looks_like_echo("I see", ["Oh, I see what you mean."])
    assert not V.looks_like_echo("I see", ["Seen it."])
    assert not V.looks_like_echo("anything", [])


def test_invalid_target_shapes_are_dropped_individually():
    r = run_targets([{"target_id": 5}, TARGET, "x"])
    assert len(r.analysis.target_assessments) == 1
    assert [d.reason.value for d in r.dropped] == ["schema", "schema"]
    assert V.validate_analysis({"target_assessments": "x"}, turn_event_id="e", learner_text=TEXT).dropped[0].ref == "target_assessments"


# ----------------------------------------------------------------------------- step 11: ids


def test_ids_have_the_contract_shape_and_are_unique_within_a_turn():
    r = run([raw_diag(), raw_diag(**EFFORT)])
    ids = [d.diagnostic_id for d in r.analysis.diagnostics]
    assert len(set(ids)) == 2 and all(re.match(DIAGNOSTIC_ID_PATTERN, i) for i in ids)


def test_ids_are_deterministic_for_the_same_turn_and_differ_across_turns():
    a = run([raw_diag()], turn_event_id="ev_a")
    again = run([raw_diag()], turn_event_id="ev_a")
    b = run([raw_diag()], turn_event_id="ev_b")
    assert a.analysis.diagnostics[0].diagnostic_id == again.analysis.diagnostics[0].diagnostic_id
    assert a.analysis.diagnostics[0].diagnostic_id != b.analysis.diagnostics[0].diagnostic_id


def test_ids_avoid_taken_ids():
    first = run([raw_diag()]).analysis.diagnostics[0].diagnostic_id
    second = run([raw_diag()], taken_ids={first}).analysis.diagnostics[0].diagnostic_id
    assert second != first and re.match(DIAGNOSTIC_ID_PATTERN, second)
    assert V.assign_diagnostic_id("ev_x", 0, {V.diagnostic_id_for("ev_x", 0), V.diagnostic_id_for("ev_x", 0, 1)}) == V.diagnostic_id_for("ev_x", 0, 2)


def test_ids_do_not_depend_on_which_other_diagnostics_survive():
    full = run([raw_diag(), raw_diag(**EFFORT)])
    partial = run([raw_diag(kind="not_a_kind"), raw_diag(**EFFORT)])
    by_span = {d.original_span: d.diagnostic_id for d in full.analysis.diagnostics}
    assert partial.analysis.diagnostics[0].diagnostic_id == V.diagnostic_id_for("ev_turn0001", 1)
    assert by_span["did an effort"] == V.diagnostic_id_for("ev_turn0001", 1)


def test_validation_does_not_mutate_its_input():
    raw = {"diagnostics": [raw_diag(**EFFORT, explanation_ar="ناقش في الأمر")], "p0": {"candidate": False}}
    before = copy.deepcopy(raw)
    V.validate_analysis(raw, turn_event_id="ev_x", learner_text=TEXT)
    assert raw == before


# ----------------------------------------------------------------------------- the 11 reasons are all reachable


def test_every_drop_reason_is_reachable():
    seen = set()
    cases = [
        run([raw_diag(kind="x")]),
        run([raw_diag(original_span="talked about")]),
        run([raw_diag(original_span="in")], text="thinking"),
        run([raw_diag(original_span="discussed about the plan", severity="low"), raw_diag(original_span="about the plan yesterday", severity="high")]),
        run([raw_diag(domain="phonological")]),
        run([raw_diag(classification="upgrade")]),
        run([raw_diag(linguistic_confidence=0.1)]),
        run_targets([TARGET], recent_bot_texts=["made an effort"]),
        run([raw_diag(normalized_pattern_id="has space")]),
        run([raw_diag(explanation_ar="خطأ")]),
        run([raw_diag(alternative=None, alternative_status="recommended")]),
    ]
    for r in cases:
        seen.update(d.reason for d in r.dropped)
    assert seen == set(DropReason)
