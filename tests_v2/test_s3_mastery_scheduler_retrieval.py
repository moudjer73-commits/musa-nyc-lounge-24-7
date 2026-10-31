"""Sprint S3 Acceptance Tests: M004 Seed Catalog, 5D Mastery, Scheduler & 3-Tier Retrieval."""
from __future__ import annotations

from pathlib import Path

import pytest

from catalog_v3 import CATALOG_VERSION, SEED_PHRASES, SEED_SUBSKILLS
from clock import FakeClock
from database import EventSourcedDatabase
from db_v3_migrations import rebuild_all_projections
from interaction import (
    InteractionObject,
    InteractionState,
    get_active_interaction,
    open_interaction,
)
from retrieval import (
    build_retrieval_hint,
    evaluate_phrase_attempt,
    record_reconstruction_attempt,
    record_retrieval_attempt,
    start_review_interaction,
)
from scheduler import (
    LADDER_DAYS,
    compute_next_schedule,
    select_due_review_items,
    select_scene_targets,
)
from schemas_v3 import (
    ContextFamily,
    MatchedBy,
    PhraseStage,
    PromptKind,
    RetrievalOrigin,
    RetrievalOutcome,
)


def test_s3_m004_seed_catalog_and_subskills(tmp_path: Path) -> None:
    db = EventSourcedDatabase(tmp_path / "s3_seed.db")
    applied = db.migrate()
    assert 4 in applied

    with db._connect() as conn:
        sub_cnt = conn.execute("SELECT COUNT(*) FROM competency_subskills").fetchone()[0]
        assert sub_cnt >= len(SEED_SUBSKILLS)

        rows = conn.execute(
            "SELECT phrase_id, catalog_version, lexical_structure_type, arabic_gloss, meaning_prompt "
            "FROM phrase_catalog WHERE catalog_version = ?",
            (CATALOG_VERSION,),
        ).fetchall()
        assert len(rows) >= len(SEED_PHRASES)
        for r in rows:
            assert r["lexical_structure_type"] in ("fixed_chunk", "semi_fixed_frame", "discourse_template")
            assert r["arabic_gloss"]
            assert r["meaning_prompt"]


def test_s3_three_tier_hybrid_evaluator_and_echo_guard() -> None:
    canonical = "I see where you're coming from, but"
    acceptable = ["I see where you are coming from, but", "I get where you're coming from, but"]

    # Tier 1: Python exact match (with curly apostrophe normalization)
    m_exact = evaluate_phrase_attempt(
        learner_text="Look, I see where you’re coming from, but the timeline is tight.",
        canonical_form=canonical,
        acceptable_forms=acceptable,
    )
    assert m_exact.outcome == RetrievalOutcome.SUCCESS
    assert m_exact.matched_by == MatchedBy.PYTHON_EXACT
    assert m_exact.linguistic_level == 3 and m_exact.functional_level == 3
    assert m_exact.echo is False

    # Echo guard flags when copied from recent bot text
    m_echo = evaluate_phrase_attempt(
        learner_text="I see where you're coming from, but we can't.",
        canonical_form=canonical,
        acceptable_forms=acceptable,
        recent_bot_texts=["Try saying: I see where you're coming from, but we need more data."],
    )
    assert m_echo.echo is True

    # Tier 2: Python fuzzy match (minor variation in semi-fixed frame)
    m_fuzzy = evaluate_phrase_attempt(
        learner_text="I see where you coming from, but we need another week.",
        canonical_form=canonical,
        acceptable_forms=acceptable,
        allow_llm_judge=False,
    )
    assert m_fuzzy.outcome == RetrievalOutcome.SUCCESS
    assert m_fuzzy.matched_by == MatchedBy.PYTHON_FUZZY

    # Tier 3: LLM Judge credits functional equivalent when exact/fuzzy do not match
    m_judge = evaluate_phrase_attempt(
        learner_text="I understand your angle on this, though we still have a bottleneck.",
        canonical_form=canonical,
        acceptable_forms=acceptable,
        judge_fn=lambda _lt, _cf, _ci: {
            "linguistic_level": 3,
            "functional_level": 3,
            "functional_equivalent": True,
        },
    )
    assert m_judge.outcome == RetrievalOutcome.SUCCESS
    assert m_judge.matched_by == MatchedBy.LLM_JUDGE
    assert m_judge.functional_equivalent_credited is True


def test_s3_hint_ladder_and_review_interaction_lifecycle(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-31T10:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s3_hints.db")
    db.migrate()
    session = db.get_or_create_active_session(701)
    session_id = session["session_id"]

    active = start_review_interaction(
        db,
        learner_id=701,
        session_id=session_id,
        phrase_id="flag_bandwidth_limit",
        prompt_kind=PromptKind.SITUATION,
        context_family=ContextFamily.WORKPLACE,
        scene_revision=1,
        now=clock.now(),
    )
    assert active.state == InteractionState.REVIEW_ACTIVE

    # Attempt 1 fails at hint_level=0 -> returns Hint 1 and keeps interaction open at revision 2
    r1 = record_retrieval_attempt(
        db,
        learner_id=701,
        session_id=session_id,
        phrase_id="flag_bandwidth_limit",
        learner_text="I am very busy today.",
        origin=RetrievalOrigin.REVIEW,
        prompt_kind=PromptKind.SITUATION,
        context_family=ContextFamily.WORKPLACE,
        hint_level=0,
        interaction_id=active.interaction_id,
        expected_interaction_revision=1,
        idempotency_key="ret_701_1",
        now=clock.now(),
    )
    assert r1.evaluation.outcome == RetrievalOutcome.FAILURE
    assert r1.interaction_closed is False
    assert r1.next_hint_text is not None and "Hint 1" in r1.next_hint_text

    # Attempt 2 succeeds with Hint 1 -> Hinted Success, closes active_interaction
    r2 = record_retrieval_attempt(
        db,
        learner_id=701,
        session_id=session_id,
        phrase_id="flag_bandwidth_limit",
        learner_text="Honestly, I'm a bit stretched thin right now.",
        origin=RetrievalOrigin.REVIEW,
        prompt_kind=PromptKind.SITUATION,
        context_family=ContextFamily.WORKPLACE,
        hint_level=1,
        interaction_id=active.interaction_id,
        expected_interaction_revision=2,
        idempotency_key="ret_701_2",
        now=clock.now(),
    )
    assert r2.evaluation.outcome == RetrievalOutcome.HINTED_SUCCESS
    assert r2.interaction_closed is True
    assert get_active_interaction(db, 701, clock.now()) is None
    assert r2.mastery.stage == PhraseStage.PRODUCED_WITH_PROMPT


def test_s3_spaced_scheduler_and_five_dimension_mastery_to_stable(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-01T09:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s3_mastery.db")
    db.migrate()
    session = db.get_or_create_active_session(801)
    session_id = session["session_id"]

    # Day 0: First independent success in WORKPLACE -> stage = Produced-independently, step 0 -> 1 (3 days)
    res1 = record_retrieval_attempt(
        db,
        learner_id=801,
        session_id=session_id,
        phrase_id="push_back_softly",
        learner_text="I see where you're coming from, but we need to test this first.",
        origin=RetrievalOrigin.REVIEW,
        prompt_kind=PromptKind.SITUATION,
        context_family=ContextFamily.WORKPLACE,
        hint_level=0,
        due=True,
        idempotency_key="m_801_1",
        now=clock.now(),
    )
    assert res1.mastery.stage == PhraseStage.PRODUCED_INDEPENDENTLY
    assert res1.schedule.step_after == 1
    assert res1.schedule.interval_days == LADDER_DAYS[1]  # 3.0 days

    # Before 3 days have elapsed, item is NOT due
    clock.advance(days=2)
    assert select_due_review_items(db, learner_id=801, now=clock.now()) == []

    # Advance past 3 days -> item becomes due in review queue!
    clock.advance(days=2)  # Total +4 days (> 1 day delayed gap!)
    due_items = select_due_review_items(db, learner_id=801, now=clock.now())
    assert len(due_items) == 1
    assert due_items[0].phrase_id == "push_back_softly"

    # Second delayed independent/spontaneous success in a SECOND context family (NEGOTIATION)
    # -> satisfies all Stable criteria (independent >= 2, delayed >= 1, families >= 2, mastery_index >= 75)
    res2 = record_retrieval_attempt(
        db,
        learner_id=801,
        session_id=session_id,
        phrase_id="push_back_softly",
        learner_text="I see where you're coming from, but our budget has a hard cap.",
        origin=RetrievalOrigin.SPONTANEOUS,
        prompt_kind=PromptKind.SPONTANEOUS,
        context_family=ContextFamily.NEGOTIATION,
        hint_level=0,
        due=True,
        idempotency_key="m_801_2",
        now=clock.now(),
    )
    assert res2.mastery.stage == PhraseStage.STABLE
    assert res2.mastery.delayed_success_count >= 1
    assert res2.mastery.max_delayed_gap_days >= 4.0
    assert set(res2.mastery.context_families) == {"workplace", "negotiation"}
    assert (res2.mastery.mastery_index or 0.0) >= 75.0
    assert res2.schedule.step_after == 2  # 7.0 days

    # Verify M003 deterministic projection rebuild preserves the V3 mastery state
    with db._connect() as conn:
        rebuild_all_projections(conn)
        conn.commit()
        row = conn.execute(
            "SELECT stage, delayed_success_count, mastery_index FROM phrase_state_projection "
            "WHERE learner_id = 801 AND phrase_id = 'push_back_softly'"
        ).fetchone()
        assert row is not None
        assert row["stage"] == PhraseStage.STABLE.value
        assert int(row["delayed_success_count"]) >= 1
        assert float(row["mastery_index"]) >= 75.0


def test_s3_reconstruction_attempt_state_transition(tmp_path: Path) -> None:
    clock = FakeClock("2026-10-31T15:00:00Z")
    db = EventSourcedDatabase(tmp_path / "s3_rc.db")
    db.migrate()
    session = db.get_or_create_active_session(901)
    session_id = session["session_id"]

    active = open_interaction(
        db,
        learner_id=901,
        session_id=session_id,
        object_type=InteractionObject.RECONSTRUCTION,
        object_ref="dg_abcd1234",
        state=InteractionState.RECONSTRUCTION_ACTIVE,
        payload={"alternative": "I discussed the contract with my team."},
        scene_revision=1,
        now=clock.now(),
    )
    rc_res = record_reconstruction_attempt(
        db,
        learner_id=901,
        session_id=session_id,
        interaction_id=active.interaction_id,
        expected_revision=1,
        diagnostic_id="dg_abcd1234",
        target_alternative="I discussed the contract with my team.",
        attempt_text="I discussed the contract with my team yesterday.",
        idempotency_key="rc_901_1",
        now=clock.now(),
    )
    assert rc_res.outcome == "success"
    assert rc_res.next_state == InteractionState.RECONSTRUCTION_FEEDBACK
