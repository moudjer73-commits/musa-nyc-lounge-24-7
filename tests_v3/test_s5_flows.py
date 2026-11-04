"""S5: flows.py, the learner-facing flows on top of interaction.py (S0 5.4, 5.7, 5.8, 5.9, 7)."""

from __future__ import annotations

import pytest

import interaction
import retrieval
from flows import V3Flows, plain_card_text, render_card_html, script_hash
from helpers_s3 import attempt, exposure, expression, tiny_seed
from helpers_s5 import Harness, raw_diag
from schemas import InteractionState

AR_NOTE = "الفعل discuss يتعدى بنفسه فلا يحتاج إلى حرف جر"
PHRASES = ("come_to_think_of_it", "if_you_dont_mind")


def seed_due(db, *ids: str) -> None:
    """Catalog phrases with one success at day 0, three days ago: due and at step 1."""
    exprs = [expression(pid, canonical_form=form) for pid, form in zip(ids, ("come to think of it", "if you don't mind", "for what it's worth"))]
    db.seed_catalog(tiny_seed(*exprs))
    for i, pid in enumerate(ids):
        exposure(db, pid, 0, f"exp{i}")
        attempt(db, pid, 0, f"att{i}", outcome="success", due=False)
    db.clock.travel(days=3)


def buttons(view):
    return [b for row in view.buttons for b in row]


def data(view, prefix):
    return next(b.data for b in buttons(view) if b.data.startswith(prefix + ":"))


def event_count(h, event_type):
    return len(h.events(event_type))


# ------------------------------------------------------------------------------ wire format and stale taps


@pytest.mark.parametrize("op", sorted(interaction.CALLBACK_OPS))
def test_every_callback_fits_in_32_bytes_at_the_largest_ids(op):
    wire = interaction.build_callback(op, interaction.MAX_OBJECT_ID, interaction.MAX_REVISION)
    assert len(wire.encode("ascii")) <= 32
    parsed = interaction.parse_callback(wire)
    assert (parsed.op, parsed.obj_id, parsed.rev) == (op, interaction.MAX_OBJECT_ID, interaction.MAX_REVISION)


@pytest.mark.parametrize("junk", ["", "ug", "ug:1", "ug:a:b", "zz:1:2", "rc:1:2:3", "tts:msg", "setreg:casual"])
def test_malformed_and_foreign_callbacks_expire_quietly(db, junk):
    res = V3Flows(db).handle_callback(1001, junk)
    assert res.toast == interaction.STALE_CALLBACK_TEXT and res.edit is None and not res.send and res.submit_turn is None


def test_a_button_for_a_row_that_is_gone_says_it_expired(db):
    h = Harness(db)
    _, actions = h.reach_card()
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    rc = data(tapped.edit, "rc")
    h.flows.close_active(h.learner)
    assert h.flows.handle_callback(h.learner, rc).toast == interaction.STALE_CALLBACK_TEXT
    assert h.flows.handle_callback(h.learner, data(tapped.edit, "bk")).toast == interaction.STALE_CALLBACK_TEXT


def test_a_ug_tap_after_the_scene_moved_on_is_saved_to_review(db):
    h = Harness(db)
    _, actions = h.reach_card()
    h.say("Anyway, what about you?", analysis={"diagnostics": []})
    res = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert res.toast == interaction.SAVED_TO_REVIEW_TEXT and res.edit is None
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None
    assert event_count(h, "feedback_card_delivered") == 0  # nothing was shown


def test_ug_for_another_learners_reply_is_refused(db):
    h = Harness(db)
    _, actions = h.reach_card()
    db.upsert_learner(2002)
    db.create_session("s2", 2002, started_at_utc="2026-10-01T09:00:00.000000Z")
    res = V3Flows(db).handle_callback(2002, actions.upgrade_callback)
    assert res.toast in (interaction.SAVED_TO_REVIEW_TEXT, interaction.STALE_CALLBACK_TEXT) and res.edit is None


def test_a_second_ug_tap_while_the_card_is_open_changes_nothing(db):
    h = Harness(db)
    _, actions = h.reach_card()
    h.flows.handle_callback(h.learner, actions.upgrade_callback)
    again = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert again.toast == interaction.SAVED_TO_REVIEW_TEXT
    assert [e["payload"]["trigger"] for e in h.events("feedback_card_delivered")] == ["tap"]


def test_the_upgrade_row_expires_after_15_minutes(db):
    h = Harness(db)
    _, actions = h.reach_card()
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    db.clock.travel(minutes=16)
    assert h.flows.handle_callback(h.learner, data(tapped.edit, "rc")).toast == interaction.STALE_CALLBACK_TEXT
    assert h.flows.handle_text(h.learner, "hello again").scene_turn is True


# ------------------------------------------------------------------------------ the card and reconstruction


def test_the_card_hides_arabic_for_an_english_only_learner(db):
    h = Harness(db)
    _, actions = h.reach_card(raw_diag(explanation_ar=AR_NOTE))
    db.update_learner_preference(h.learner, arabic_support="english_only")
    plain = h.flows.handle_callback(h.learner, actions.upgrade_callback).edit.text
    assert "tg-spoiler" not in plain and AR_NOTE not in plain


def test_the_card_shows_the_arabic_note_when_bilingual(db):
    h = Harness(db)
    _, actions = h.reach_card(raw_diag(explanation_ar=AR_NOTE))
    assert AR_NOTE in h.flows.handle_callback(h.learner, actions.upgrade_callback).edit.text


def test_card_text_for_the_echo_guard_is_what_the_learner_saw():
    diag = {"alternative": "discussed", "explanation_en": "Discuss takes its object directly.", "secondary_alternatives": [{"phrase": "went over"}], "recast_candidate": "talked over"}
    shown = plain_card_text(diag)
    for piece in ("discussed", "Discuss takes", "went over", "talked over"):
        assert piece in shown
    assert "<" not in render_card_html("Hi", {"original_span": "x", "explanation_en": "y"}).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<s>", "").replace("</s>", "")


def test_a_reconstruction_that_repeats_the_card_is_not_graded(db):
    h = Harness(db)
    _, actions = h.reach_card()
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    h.flows.handle_callback(h.learner, data(tapped.edit, "rc"))
    echoed = h.flows.handle_text(h.learner, "discussed In English, 'discuss' takes its object directly: 'discussed the plan'.")
    assert "own sentence" in echoed.views[0].text
    assert event_count(h, "reconstruction_attempted") == 0
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()).state == InteractionState.RECONSTRUCTION_ACTIVE


def test_a_failed_reconstruction_is_recorded_and_still_offers_send_and_back(db):
    h = Harness(db)
    _, actions = h.reach_card()
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    h.flows.handle_callback(h.learner, data(tapped.edit, "rc"))
    view = h.flows.handle_text(h.learner, "I like coffee in the morning.").views[0]
    assert h.events("reconstruction_attempted")[-1]["payload"]["outcome"] == "failure"
    assert {b.data.split(":")[0] for b in buttons(view)} == {"rs", "bk"}


def test_back_from_the_card_restores_alexs_reply_and_closes_the_row(db):
    h = Harness(db)
    _, actions = h.reach_card()
    tapped = h.flows.handle_callback(h.learner, actions.upgrade_callback)
    back = h.flows.handle_callback(h.learner, data(tapped.edit, "bk"))
    assert "Ah, so you finally talked it through." in back.edit.text
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None


def test_a_mode_button_closes_the_open_row(db):
    h = Harness(db)
    _, actions = h.reach_card()
    h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert h.flows.close_active(h.learner) is True
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None
    assert h.flows.close_active(h.learner) is False


def test_free_text_while_the_card_is_open_is_an_ordinary_scene_turn(db):
    h = Harness(db)
    _, actions = h.reach_card()
    h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert h.flows.handle_text(h.learner, "Anyway, what about you?").scene_turn is True
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None


def test_listen_speaks_the_reply_the_button_belongs_to(db):
    h = Harness(db)
    res, _ = h.reach_card()
    out = h.flows.handle_callback(h.learner, interaction.build_callback("ls", res.handle.reply_event_seq, 0))
    assert out.tts_text == "Ah, so you finally talked it through."
    assert h.flows.handle_callback(h.learner, interaction.build_callback("ls", 999999, 0)).toast == interaction.STALE_CALLBACK_TEXT


def test_the_audio_cache_key_ignores_case_and_spacing():
    assert script_hash("Hello   there") == script_hash(" hello there ")
    assert script_hash("Hello there") != script_hash("Hello there!")


# ------------------------------------------------------------------------------ organic registration


def test_a_shown_high_confidence_alternative_becomes_an_organic_phrase_with_an_exposure(db):
    h = Harness(db)
    _, actions = h.reach_card()
    h.flows.handle_callback(h.learner, actions.upgrade_callback)
    entry = db.get_phrase_entry("organic_discussed")
    assert entry is not None and entry.canonical_form == "discussed"
    assert [e["payload"]["phrase_id"] for e in h.events("phrase_exposure_recorded")] == ["organic_discussed"]


@pytest.mark.parametrize(
    "over",
    [
        {"repair_scope": "utterance_rewrite", "alternative": "We talked the plan over yesterday."},
        {"linguistic_confidence": 0.7},  # medium band
        {"kind": "acceptable_variation", "alternative_status": "optional"},
    ],
)
def test_organic_registration_needs_a_high_confidence_short_local_repair(db, over):
    h = Harness(db)
    _, actions = h.reach_card(raw_diag(**over))
    if actions.upgrade_callback:
        h.flows.handle_callback(h.learner, actions.upgrade_callback)
    assert h.events("phrase_exposure_recorded") == []


# ------------------------------------------------------------------------------ review and retrieval


def test_review_starts_with_the_most_overdue_item_and_closes_any_open_row(db):
    seed_due(db, *PHRASES)
    h = Harness(db)
    h.say("Hello Alex, quick question about the plan.", analysis={"diagnostics": []})
    views = h.flows.start_review(h.learner)
    assert "Review 1/2" in views[-1].text
    assert {b.data.split(":")[0] for b in buttons(views[-1])} == {"rvh", "rvs"}
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()).state == InteractionState.REVIEW_ACTIVE


def test_review_with_nothing_due_says_so(db):
    views = V3Flows(db).start_review(1001)
    assert "Nothing is due" in views[-1].text


def test_the_hint_ladder_is_structure_then_the_arabic_gloss_and_then_no_more(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    view = h.flows.start_review(h.learner)[-1]
    entry = db.get_phrase_entry("come_to_think_of_it")
    first = h.flows.handle_callback(h.learner, data(view, "rvh"))
    assert retrieval.build_hint(entry, 1) in first.edit.text and entry.arabic_gloss not in first.edit.text
    stale = h.flows.handle_callback(h.learner, data(view, "rvh"))  # the old button
    assert stale.toast == interaction.STALE_CALLBACK_TEXT
    second = h.flows.handle_callback(h.learner, data(first.edit, "rvh"))
    assert entry.arabic_gloss in second.edit.text
    assert "rvh" not in {b.data.split(":")[0] for b in buttons(second.edit)}  # two hints at most


def test_a_correct_attempt_is_graded_by_python_and_recorded(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    h.flows.start_review(h.learner)
    view = h.flows.handle_text(h.learner, "Come to think of it, we never booked the room.").views[0]
    event = h.events("retrieval_attempted")[-1]["payload"]
    assert event["outcome"] == "success" and event["origin"] == "review" and event["hint_level"] == 0 and event["due"] is True
    assert "Nice" in view.text and "come to think of it" in view.text
    assert h.ai.judge_calls == 0  # the exact form needs no judge


def test_a_hinted_success_is_marked_as_hinted(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    first = h.flows.start_review(h.learner)[-1]
    hinted = h.flows.handle_callback(h.learner, data(first, "rvh")).edit
    h.flows.handle_text(h.learner, "Come to think of it, we never booked the room.")
    event = h.events("retrieval_attempted")[-1]["payload"]
    assert event["hint_level"] == 1 and event["outcome"] == "hinted_success"
    assert "rvh" in {b.data.split(":")[0] for b in buttons(hinted)}  # one hint left


def test_a_second_attempt_on_the_same_item_asks_for_next(db):
    seed_due(db, *PHRASES)
    h = Harness(db)
    h.flows.start_review(h.learner)
    before = event_count(h, "retrieval_attempted")
    h.flows.handle_text(h.learner, "Come to think of it, we never booked the room.")
    again = h.flows.handle_text(h.learner, "Come to think of it, really.").views[0]
    assert "Next" in again.text and event_count(h, "retrieval_attempted") == before + 1


def test_an_attempt_that_only_repeats_the_prompt_is_not_graded(db):
    import html

    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    view = h.flows.start_review(h.learner)[-1]
    before = event_count(h, "retrieval_attempted")
    prompt = html.unescape(view.text.split("\n")[1])
    out = h.flows.handle_text(h.learner, prompt).views[0]
    assert "repeats" in out.text and event_count(h, "retrieval_attempted") == before


def test_a_natural_alternative_gets_functional_credit_from_the_judge(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    h.ai.judge_answers.append({"linguistic_level": 0, "functional_level": 3, "functional_equivalent_credited": True})
    h.flows.start_review(h.learner)
    view = h.flows.handle_text(h.learner, "Oh, one more thing before I forget: we never booked the room.").views[0]
    event = h.events("retrieval_attempted")[-1]["payload"]
    assert h.ai.judge_calls == 1 and event["matched_by"] == "llm_judge"
    assert "does the same job" in view.text


def test_a_shed_judge_leaves_the_item_due_and_writes_nothing(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    h.ai.judge_answers.append(TimeoutError("deadline"))
    h.flows.start_review(h.learner)
    before = event_count(h, "retrieval_attempted")
    view = h.flows.handle_text(h.learner, "Oh, one more thing before I forget: we never booked the room.").views[0]
    assert view.text == retrieval.SHED_MESSAGE
    assert event_count(h, "retrieval_attempted") == before
    assert [p.phrase_id for p in db.get_due_phrases(h.learner, db.clock.now())] == ["come_to_think_of_it"]


def test_skip_writes_no_evidence_and_the_last_item_finishes_the_review(db):
    seed_due(db, *PHRASES)
    h = Harness(db)
    view = h.flows.start_review(h.learner)[-1]
    before = event_count(h, "retrieval_attempted")
    skipped = h.flows.handle_callback(h.learner, data(view, "rvs"))
    assert "Review 2/2" in skipped.edit.text and event_count(h, "retrieval_attempted") == before
    done = h.flows.handle_callback(h.learner, data(skipped.edit, "rvs"))
    assert "Review done" in done.edit.text
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is None
    assert len(db.get_due_phrases(h.learner, db.clock.now())) == 2  # still due


def test_next_moves_on_only_after_an_answer_has_been_given(db):
    seed_due(db, *PHRASES)
    h = Harness(db)
    h.flows.start_review(h.learner)
    answered = h.flows.handle_text(h.learner, "Come to think of it, we never booked the room.").views[0]
    nxt = h.flows.handle_callback(h.learner, data(answered, "rvn"))
    assert "Review 2/2" in nxt.edit.text
    assert h.flows.handle_callback(h.learner, data(answered, "rvn")).toast == interaction.STALE_CALLBACK_TEXT  # used up


def test_a_review_item_expires_after_30_minutes(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    view = h.flows.start_review(h.learner)[-1]
    db.clock.travel(minutes=29)
    assert interaction.get_active_interaction(db, h.learner, db.clock.now()) is not None
    db.clock.travel(minutes=2)
    assert h.flows.handle_callback(h.learner, data(view, "rvs")).toast == interaction.STALE_CALLBACK_TEXT
    assert h.flows.handle_text(h.learner, "hello").scene_turn is True


def test_review_shows_the_deferred_findings_first(db):
    seed_due(db, "come_to_think_of_it")
    h = Harness(db)
    h.say("We discussed about the plan yesterday.", analysis={"diagnostics": [raw_diag()]})  # warm-up: deferred
    views = h.flows.start_review(h.learner)
    assert "Worth another look" in views[0].text and "discussed about" in views[0].text
    assert "Review 1/1" in views[-1].text
    assert [e["payload"]["trigger"] for e in h.events("feedback_card_delivered")] == ["review"]


# ------------------------------------------------------------------------------ debrief and progress


def test_the_debrief_is_templated_and_delivers_the_top_queue_items(db):
    h = Harness(db)
    h.say("We discussed about the plan yesterday.", analysis={"diagnostics": [raw_diag()]})
    view = h.flows.debrief_view(h.learner)
    assert view.text and "discussed" in view.text
    assert h.ai.analyst_calls == 1  # no generator call
    assert [e["payload"]["trigger"] for e in h.events("feedback_card_delivered")] == ["debrief"]


def test_the_debrief_of_an_empty_scene_does_not_fail(db):
    assert V3Flows(db).debrief_view(1001).text


def test_progress_lists_the_five_dimensions_for_tracked_phrases(db):
    seed_due(db, *PHRASES)
    view = V3Flows(db).progress_view(1001)
    assert "Five dimensions" in view.text and "come to think of it" in view.text and "Due for review:</b> 2" in view.text


def test_progress_with_nothing_tracked_says_so(db):
    assert "No tracked phrases yet" in V3Flows(db).progress_view(1001).text


def test_progress_lists_recurring_patterns(db):
    h = Harness(db)
    h.say("We discussed about the plan yesterday.", analysis={"diagnostics": [raw_diag()]})
    assert "COLLOC_PREP_VERB:discuss_about" in h.flows.progress_view(h.learner).text
