import copy
import json
import sqlite3
from contextlib import closing

import pytest

from helpers import (
    build_golden_v2_db,
    decision_dict,
    raw_row,
    v1_analysis_payload,
    v1_diagnostic,
    v1_policy_payload,
    v2_analysis_dict,
    v2_diagnostic_dict,
    v2_policy_dict,
)
from schemas import (
    AnalysisV2,
    EVENT_TYPE_REGISTRY,
    EventV2,
    FeedbackCardDeliveredV2,
    InterventionLevel,
    IssueKind,
    LIVE_EVENT_TYPES,
    LearnerMessageV2,
    PAYLOAD_MODELS,
    PolicyDecisionV2,
    ResponderReplyV2,
    UpcastError,
    AlternativeStatus,
    ConfidenceBand,
    legacy_diagnostic_id,
    upcast,
)


def j(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# ------------------------------------------------------------ golden: every live event type reads


def test_every_live_event_type_in_the_golden_db_upcasts(tmp_path):
    path = str(tmp_path / "golden.db")
    facts = build_golden_v2_db(path)
    assert facts["event_types"] == set(LIVE_EVENT_TYPES)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM event_log ORDER BY event_seq").fetchall()
    seen = set()
    for row in rows:
        event = upcast(row)
        seen.add(event.event_type)
        assert isinstance(event, EventV2)
        assert event.is_legacy and event.source_schema_version == "1.0"
        assert event.warnings == (), (event.event_type, event.warnings)
        if event.event_type in PAYLOAD_MODELS:
            assert isinstance(event.payload, PAYLOAD_MODELS[event.event_type])
        else:
            assert isinstance(event.payload, dict)
        # the strict path must agree
        assert upcast(row, strict=True).payload_dict() == event.payload_dict()
    assert seen == set(LIVE_EVENT_TYPES)


def test_unchanged_event_types_pass_through_untouched():
    payload = {"phrase_id": "plate_is_full", "result": "ok"}
    event = upcast(raw_row("review_completed", j(payload)))
    assert event.payload == payload and event.legacy == {}


# ------------------------------------------------------------ analysis_diagnosis_recorded v1


def analysis_row(event_id="ev_000000000001", **kw):
    payload = v1_analysis_payload(
        v1_diagnostic("d1"),
        v1_diagnostic("d2", span="plan", pattern="style:plan", kind=IssueKind.OPTIONAL_UPGRADE,
                      alternative="proposal", status=AlternativeStatus.OPTIONAL, score=0.82),
        v1_diagnostic("d3", span="this", pattern="ctx:this", kind=IssueKind.INSUFFICIENT_CONTEXT,
                      alternative=None, status=AlternativeStatus.OPTIONAL, band=ConfidenceBand.LOW, score=0.4),
        v1_diagnostic("d4", span="I want", pattern="pragmatics:i_want", kind=IssueKind.PRAGMATICS,
                      alternative="I'd like", status=AlternativeStatus.RECOMMENDED, band=ConfidenceBand.MEDIUM, score=0.7),
    )
    return raw_row("analyst_diagnosis_recorded", j(payload), event_id=event_id, **kw)


def test_v1_analysis_gets_deterministic_dg_ids_by_list_position():
    event = upcast(analysis_row())
    ids = [d.diagnostic_id for d in event.payload.diagnostics]
    # d3 (ordinal 2) is dropped, but the ordinal is the position in the ORIGINAL list
    assert ids == [legacy_diagnostic_id("ev_000000000001", n) for n in (0, 1, 3)]
    assert event.legacy_diagnostic_ids == {
        legacy_diagnostic_id("ev_000000000001", 0): "d1",
        legacy_diagnostic_id("ev_000000000001", 1): "d2",
        legacy_diagnostic_id("ev_000000000001", 2): "d3",
        legacy_diagnostic_id("ev_000000000001", 3): "d4",
    }


def test_same_v1_id_in_two_analyses_never_collides():
    a = upcast(analysis_row(event_id="ev_aaaaaaaaaaaa")).payload.diagnostics[0].diagnostic_id
    b = upcast(analysis_row(event_id="ev_bbbbbbbbbbbb")).payload.diagnostics[0].diagnostic_id
    assert a != b


def test_v1_analysis_fields_are_derived_as_the_contract_says():
    d1, d2, d4 = upcast(analysis_row()).payload.diagnostics
    assert (d1.domain.value, d1.classification.value) == ("linguistic", "error")
    assert (d2.domain.value, d2.classification.value, d2.alternative_status.value) == ("linguistic", "upgrade", "optional")
    assert (d4.domain.value, d4.classification.value) == ("pragmatic", "error")
    for d in (d1, d2, d4):
        assert d.repair_scope.value == "phrase_span"
        assert int(d.communicative_impact) == 2 and int(d.learning_value) == 2
        assert d.acoustic_confidence is None and d.timing_hint is None
    assert d1.linguistic_confidence == pytest.approx(0.91) and d1.pragmatic_confidence is None
    assert d4.pragmatic_confidence == pytest.approx(0.7) and d4.linguistic_confidence is None


def test_insufficient_context_is_dropped_with_a_reason():
    payload = upcast(analysis_row()).payload
    assert [(x.ref, x.reason.value) for x in payload.dropped] == [
        (legacy_diagnostic_id("ev_000000000001", 2), "classification_mismatch")
    ]


def test_spans_come_from_learner_text_when_given_else_zero():
    text = "I want to discuss about this plan."
    with_text = upcast(analysis_row(), learner_text=text).payload.diagnostics
    start = text.index("discuss about")
    plan = text.index("plan")
    assert [(d.span_start, d.span_end) for d in with_text] == [(start, start + len("discuss about")), (plan, plan + 4), (0, 6)]
    without = upcast(analysis_row()).payload.diagnostics
    assert all((d.span_start, d.span_end) == (0, 0) for d in without)


def test_a_span_missing_from_the_text_falls_back_to_zero_not_an_error():
    event = upcast(analysis_row(), learner_text="completely different words", strict=True)
    assert all((d.span_start, d.span_end) == (0, 0) for d in event.payload.diagnostics)


def test_v1_analysis_turn_event_id_is_the_causation_and_rubric_is_kept():
    event = upcast(analysis_row())
    assert event.payload.turn_event_id == "ev_msg00000001"
    assert event.payload.rubric_version == "v1.0"
    assert event.payload.call_meta is None and event.payload.p0.candidate is False


def test_an_empty_v1_analysis_is_valid():
    event = upcast(raw_row("analyst_diagnosis_recorded", j(v1_analysis_payload())), strict=True)
    assert event.payload.diagnostics == [] and event.payload.dropped == []


# ------------------------------------------------------------ policy / reply / message / card v1


def test_v1_brief_card_policy_is_class_1_end_of_turn():
    event = upcast(raw_row("policy_decision_recorded", j(v1_policy_payload())))
    p = event.payload
    assert isinstance(p, PolicyDecisionV2)
    assert p.selected_diagnostic_id == "d1"
    assert len(p.decisions) == 1
    d = p.decisions[0]
    assert (int(d.priority_class), d.delivery.value, d.priority_score) == (1, "end_of_turn", 0.0)
    assert p.curveball_turn is False and p.pending_clarification is None
    assert event.legacy["selected_pattern_id"] == "grammar:discuss_about"


def test_v1_non_card_policy_with_a_selection_is_class_4_deferred():
    p = upcast(raw_row("policy_decision_recorded", j(v1_policy_payload(InterventionLevel.NONE, "d1")))).payload
    d = p.decisions[0]
    assert (int(d.priority_class), d.delivery.value) == (4, "deferred")


def test_v1_policy_without_a_selection_has_no_decisions():
    p = upcast(raw_row("policy_decision_recorded", j(v1_policy_payload(InterventionLevel.NONE, None, None)))).payload
    assert p.decisions == [] and p.selected_diagnostic_id is None


def test_v1_reply_becomes_kind_reply_and_keeps_the_dropped_flags_in_legacy():
    payload = {"reply_text": "Sure.", "second_bubble_text": "Go on.", "recast_used": True,
               "asked_optional_question": True, "was_clarification_question": False}
    event = upcast(raw_row("responder_reply_delivered", j(payload)))
    r = event.payload
    assert isinstance(r, ResponderReplyV2)
    assert (r.kind, r.reply_text, r.second_bubble_text, r.recast_used) == ("reply", "Sure.", "Go on.", True)
    assert r.comprehension_status.value == "ok" and r.turn_event_id == "ev_msg00000001"
    assert event.legacy["asked_optional_question"] is True


def test_v1_message_voice_flag_and_defaults():
    voice = upcast(raw_row("learner_message_received", j({"text": "hi", "is_voice": True}))).payload
    text = upcast(raw_row("learner_message_received", j({"text": "hi"}))).payload
    assert isinstance(voice, LearnerMessageV2)
    assert (voice.input_modality, voice.origin.value, voice.session_revision, voice.interaction_id) == ("voice", "scene", 0, None)
    assert text.input_modality == "text"


def test_v1_card_is_a_tap_delivery():
    payload = {"diagnostic_id": "d1", "normalized_pattern_id": "grammar:discuss_about", "relevant_context_family": "global"}
    event = upcast(raw_row("feedback_card_delivered", j(payload)))
    assert isinstance(event.payload, FeedbackCardDeliveredV2)
    assert event.payload.trigger == "tap" and event.payload.diagnostic_id == "d1"


# ------------------------------------------------------------ v2 rows, errors, purity


def test_v2_rows_are_read_as_models_without_reshaping():
    payload = v2_analysis_dict("ev_t", v2_diagnostic_dict())
    event = upcast(raw_row("analyst_diagnosis_recorded", j(payload), schema_version="2.0"))
    assert isinstance(event.payload, AnalysisV2) and not event.is_legacy
    assert event.legacy == {} and event.legacy_diagnostic_ids == {}
    assert event.payload.diagnostics[0].diagnostic_id == "dg_0a1b2c3d"
    assert event.payload_dict() == AnalysisV2.model_validate(payload).model_dump(mode="json")


def test_v2_policy_round_trips():
    payload = v2_policy_dict("ev_t", decision_dict("dg_0a1b2c3d"), selected="dg_0a1b2c3d")
    event = upcast(raw_row("policy_decision_recorded", j(payload), schema_version="2.0"))
    assert event.payload.decisions[0].diagnostic_id == "dg_0a1b2c3d"


def test_new_v3_event_types_read_at_v2():
    payload = {"turn_event_id": "ev_t", "reason": "shed", "detail": None}
    event = upcast(raw_row("analysis_skipped", j(payload), schema_version="2.0"))
    assert event.payload.reason.value == "shed"


@pytest.mark.parametrize("version", ["3.0", "0.9", "x", ""])
def test_unsupported_major_versions_raise(version):
    with pytest.raises(UpcastError):
        upcast(raw_row("session_started", j({}), schema_version=version))


def test_unknown_event_type_raises():
    with pytest.raises(UpcastError):
        upcast(raw_row("something_else", j({})))


def test_a_v3_only_event_type_cannot_exist_at_v1():
    with pytest.raises(UpcastError):
        upcast(raw_row("retrieval_attempted", j({}), schema_version="1.0"))


def test_bad_json_raises_but_not_object_raises_too():
    with pytest.raises(UpcastError):
        upcast(raw_row("session_started", "{nope"))
    with pytest.raises(UpcastError):
        upcast(raw_row("session_started", "[1, 2]"))


def test_a_payload_that_fails_its_model_degrades_with_a_warning_unless_strict():
    bad = v2_analysis_dict("ev_t", v2_diagnostic_dict(acoustic_confidence=0.9))
    row = raw_row("analyst_diagnosis_recorded", j(bad), schema_version="2.0")
    event = upcast(row)
    assert isinstance(event.payload, dict) and event.warnings and "AnalysisV2" in event.warnings[0]
    assert event.payload["diagnostics"][0]["acoustic_confidence"] == 0.9
    with pytest.raises(UpcastError):
        upcast(row, strict=True)


def test_validate_false_shapes_but_builds_no_models():
    event = upcast(analysis_row(), validate=False)
    assert isinstance(event.payload, dict)
    assert [d["diagnostic_id"] for d in event.payload["diagnostics"]][0] == legacy_diagnostic_id("ev_000000000001", 0)
    assert event.warnings == ()


def test_upcast_is_pure_and_repeatable():
    row = analysis_row()
    snapshot = copy.deepcopy(row)
    first, second = upcast(row), upcast(row)
    assert row == snapshot
    assert first.payload_dict() == second.payload_dict()
    assert first.legacy_diagnostic_ids == second.legacy_diagnostic_ids
    with pytest.raises(Exception):
        first.event_id = "other"  # frozen


def test_registry_covers_every_payload_model_type():
    assert set(PAYLOAD_MODELS) <= set(EVENT_TYPE_REGISTRY)
