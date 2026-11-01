import pytest
from pydantic import ValidationError

import schemas
from schemas import (
    AnalysisSkippedV2,
    AnalysisV2,
    CallMetaDict,
    Classification,
    DiagnosticV2,
    Domain,
    FeedbackCardDeliveredV2,
    LearnerMessageV2,
    Ordinal,
    P0Signal,
    PolicyDecisionV2,
    PriorityClass,
    ReconstructionAttemptedV2,
    ResponderReplyV2,
    RetrievalAttemptedV2,
    TrainingMode,
    classification_from_kind,
    confidence_band_for,
    legacy_diagnostic_id,
)
from helpers import decision_dict, v2_analysis_dict, v2_diagnostic_dict, v2_policy_dict


def values(enum_cls):
    return [m.value for m in enum_cls]


def test_canonical_enums_match_s0_section_2():
    assert values(schemas.Domain) == ["linguistic", "pragmatic", "discourse", "phonological"]
    assert values(schemas.Classification) == ["error", "upgrade", "alternative"]
    assert values(schemas.RepairScope) == ["local", "phrase_span", "utterance_rewrite"]
    assert values(schemas.Timing) == ["immediate", "end_of_turn", "deferred"]
    assert values(schemas.Delivery) == ["immediate_followup", "end_of_turn", "deferred", "suppressed"]
    assert [int(p) for p in PriorityClass] == [0, 1, 2, 3, 4]
    assert values(schemas.ComprehensionStatus) == ["ok", "ambiguous", "reversed"]
    assert values(schemas.FeedbackStatus) == ["pending", "delivered", "retracted", "merged"]
    assert values(schemas.InteractionObject) == ["upgrade", "reconstruction", "review_item", "decode", "workout"]
    assert values(schemas.InteractionState) == [
        "upgrade_view", "reconstruction_active", "reconstruction_feedback", "review_active", "decode_await", "workout_active",
    ]
    assert values(schemas.TurnOrigin) == ["scene", "reconstruction", "review", "drill"]
    assert values(schemas.RetrievalOutcome) == ["success", "partial", "failure", "hinted_success"]
    assert values(schemas.RetrievalOrigin) == ["review", "spontaneous", "reconstruction", "drill"]
    assert values(schemas.PromptKind) == ["meaning", "situation", "function", "spontaneous"]
    assert values(schemas.MatchedBy) == ["python_exact", "python_fuzzy", "llm_judge"]
    assert values(schemas.EvidenceDimension) == ["recognition", "production", "independence", "transfer", "pragmatic"]
    assert [int(o) for o in Ordinal] == [0, 1, 2, 3]
    assert values(schemas.PhraseStage) == [
        "Unseen", "Seen", "Recognized", "Produced-with-prompt", "Produced-independently",
        "Produced-spontaneously", "Stable",
    ]
    assert values(schemas.ContextFamily) == [
        "workplace", "friendships", "negotiation", "academic", "customer_service", "family",
        "social_group", "conflict", "presentation", "general",
    ]
    assert values(schemas.LexicalStructureType) == ["fixed_chunk", "semi_fixed_frame", "discourse_template"]
    assert values(schemas.SkipReason) == ["trivial", "shed", "deadline", "error"]
    assert values(schemas.DropReason) == [
        "schema", "span_missing", "span_boundary", "overlap", "domain_modality",
        "classification_mismatch", "abstain", "echo", "pattern_code", "note_tone", "noop",
    ]
    assert TrainingMode.REVIEW_RETRIEVAL.value == "review_retrieval"
    assert {m.value for m in TrainingMode} >= {
        "open_scene", "register_shift", "pragmatic_dilemma", "think_aloud", "listen_and_decode",
        "phrase_workout", "echo_prosody", "review_retrieval",
    }


def test_storage_values_are_snake_case_except_phrase_stage():
    for name in ("Domain", "Classification", "InteractionState", "Delivery", "TurnOrigin", "ContextFamily"):
        for member in getattr(schemas, name):
            assert member.value == member.value.lower() and " " not in member.value and "-" not in member.value


def test_event_registry_has_14_live_plus_3_new_types():
    assert len(schemas.LIVE_EVENT_TYPES) == 14 and len(set(schemas.LIVE_EVENT_TYPES)) == 14
    assert set(schemas.V3_EVENT_TYPES) == {"reconstruction_attempted", "retrieval_attempted", "analysis_skipped"}
    assert {t for t, v in schemas.EVENT_TYPE_REGISTRY.items() if v == 0} == set(schemas.LIVE_EVENT_TYPES)
    assert {t for t, v in schemas.EVENT_TYPE_REGISTRY.items() if v == 1} == set(schemas.V3_EVENT_TYPES)


def test_kind_to_classification_and_domain_mappings():
    assert classification_from_kind("optional_upgrade") == Classification.UPGRADE
    assert classification_from_kind("acceptable_variation") == Classification.ALTERNATIVE
    assert classification_from_kind("insufficient_context") is None
    for kind in ("grammar", "collocation", "idiomaticity", "register", "pragmatics", "discourse"):
        assert classification_from_kind(kind) == Classification.ERROR
    assert schemas.DOMAIN_BY_KIND["collocation"] == Domain.LINGUISTIC
    assert schemas.DOMAIN_BY_KIND["idiomaticity"] == Domain.LINGUISTIC
    assert schemas.DOMAIN_BY_KIND["register"] == Domain.PRAGMATIC
    assert schemas.DOMAIN_BY_KIND["pragmatics"] == Domain.PRAGMATIC
    assert schemas.DOMAIN_BY_KIND["discourse"] == Domain.DISCOURSE


def test_confidence_band_cut_points_match_the_live_diagnostic_validator():
    assert confidence_band_for(0.64) == "low" and confidence_band_for(0.65) == "medium"
    assert confidence_band_for(0.79) == "medium" and confidence_band_for(0.80) == "high"


def test_legacy_diagnostic_id_is_dg_plus_8_hex_and_deterministic():
    one = legacy_diagnostic_id("ev_abc", 0)
    assert one == legacy_diagnostic_id("ev_abc", 0)
    assert one != legacy_diagnostic_id("ev_abc", 1) != legacy_diagnostic_id("ev_abd", 0)
    assert len(one) == 11 and one.startswith("dg_")


def test_diagnostic_v2_accepts_a_valid_error():
    d = DiagnosticV2.model_validate(v2_diagnostic_dict())
    assert d.classification == Classification.ERROR and d.communicative_impact == Ordinal.L2


@pytest.mark.parametrize(
    "override",
    [
        {"diagnostic_id": "d1"},                       # not dg_ + 8 hex
        {"diagnostic_id": "dg_ABCDEF12"},              # uppercase hex
        {"acoustic_confidence": 0.9},                  # must be None in V3.0
        {"span_start": 9, "span_end": 3},
        {"classification": "upgrade"},                 # kind grammar is an error
        {"alternative_status": "optional"},            # an error needs required/recommended
        {"communicative_impact": 4},
        {"linguistic_confidence": None, "pragmatic_confidence": None},
        {"kind": "insufficient_context", "alternative_status": "optional"},
        {"unknown_field": 1},                          # extra='forbid'
    ],
)
def test_diagnostic_v2_rejects_invalid_shapes(override):
    with pytest.raises(ValidationError):
        DiagnosticV2.model_validate(v2_diagnostic_dict(**override))


def test_upgrade_and_alternative_must_be_optional():
    ok = v2_diagnostic_dict(kind="optional_upgrade", classification="upgrade", alternative_status="optional")
    DiagnosticV2.model_validate(ok)
    with pytest.raises(ValidationError):
        DiagnosticV2.model_validate({**ok, "alternative_status": "recommended"})


def test_phonological_domain_exists_but_is_reserved():
    assert Domain.PHONOLOGICAL.value == "phonological"
    assert DiagnosticV2.model_validate(v2_diagnostic_dict(domain="phonological")).domain == Domain.PHONOLOGICAL  # rejected later by validation.py


def test_analysis_v2_roundtrips_and_forbids_extras():
    payload = v2_analysis_dict("ev_t", v2_diagnostic_dict(), dropped=[{"ref": "x", "reason": "abstain"}])
    model = AnalysisV2.model_validate(payload)
    assert model.model_dump(mode="json")["diagnostics"][0]["diagnostic_id"] == "dg_0a1b2c3d"
    with pytest.raises(ValidationError):
        AnalysisV2.model_validate({**payload, "surprise": 1})


def test_p0_line_is_capped_at_20_words():
    P0Signal(candidate=True, line=" ".join(["word"] * 20))
    with pytest.raises(ValidationError):
        P0Signal(candidate=True, line=" ".join(["word"] * 21))


def test_policy_decision_v2_curveball_is_always_false():
    PolicyDecisionV2.model_validate(v2_policy_dict("ev_t", decision_dict("dg_0a1b2c3d"), selected="dg_0a1b2c3d"))
    with pytest.raises(ValidationError):
        PolicyDecisionV2.model_validate({**v2_policy_dict("ev_t"), "curveball_turn": True})
    with pytest.raises(ValidationError):
        PolicyDecisionV2.model_validate(v2_policy_dict("ev_t", decision_dict("dg_0a1b2c3d", score=101.0)))


def test_message_reply_and_card_shapes():
    LearnerMessageV2(text="hi", input_modality="voice", origin="review", session_revision=3, interaction_id=9)
    with pytest.raises(ValidationError):
        LearnerMessageV2(text="hi", input_modality="video", origin="scene", session_revision=0)
    reply = ResponderReplyV2(kind="p0_followup", reply_text="Sorry, do you mean the report?")
    assert reply.comprehension_status == "ok" and reply.telegram_message_ids == []
    with pytest.raises(ValidationError):
        ResponderReplyV2(kind="goodbye", reply_text="x")
    FeedbackCardDeliveredV2(diagnostic_id="dg_0a1b2c3d", card_id="card_1", trigger="debrief")
    with pytest.raises(ValidationError):
        FeedbackCardDeliveredV2(diagnostic_id="dg_0a1b2c3d", card_id="card_1", trigger="push")


def test_v3_only_event_payloads():
    ReconstructionAttemptedV2(
        interaction_id=4, diagnostic_id="dg_0a1b2c3d", attempt_text="I'd like to discuss it", linguistic_level=3,
        functional_level=2, outcome="success", functional_equivalent_credited=False, matched_by="python_exact",
    )
    RetrievalAttemptedV2(
        phrase_id="plate_is_full", origin="review", prompt_kind="meaning", context_family="workplace",
        outcome="hinted_success", hint_level=1, matched_form="my plate is full", matched_by="python_fuzzy",
        echo=False, due=True, schedule_step_before=2,
    )
    with pytest.raises(ValidationError):
        RetrievalAttemptedV2(
            phrase_id="x", origin="review", prompt_kind="meaning", context_family="workplace", outcome="success",
            hint_level=3, matched_by="python_exact", echo=False, due=True, schedule_step_before=0,
        )
    AnalysisSkippedV2(turn_event_id="ev_t", reason="deadline", detail="35 s")
    with pytest.raises(ValidationError):
        AnalysisSkippedV2(turn_event_id="ev_t", reason="bored")


def test_call_meta_from_transport_object_drops_project_label():
    class Meta:
        model = "gemini-x"
        project_label = "default"
        attempts = 2
        latency_ms = 850
        thinking_budget = None
        input_tokens = 120
        output_tokens = 40

    stored = CallMetaDict.from_meta(Meta())
    assert stored.model_dump() == {
        "model": "gemini-x", "attempts": 2, "latency_ms": 850, "thinking_budget": None,
        "input_tokens": 120, "output_tokens": 40,
    }
