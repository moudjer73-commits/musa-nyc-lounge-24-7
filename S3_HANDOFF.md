# S3 hand-off (retrieval, mastery, scheduler, queue, catalog)

Baseline 743 tests in CI. Now 992 locally on Python 3.13 (737 `tests_v3`, 235 `tests_v2`, 20 `tests.py`); expect 996 in CI (239 in `tests_v2`). I did not run Python 3.11; the new files parse under the 3.11 grammar.

## Blocking: the catalog content (for Musa)

`data/catalog_v1.0.0.json` does not exist, so there is no M004. The machinery is ready and tested on synthetic content: model, idempotent seeder, release-count check (7 competencies, 42 subskills, 56 expressions), `python -m database seed-catalog --file data/catalog_v1.0.0.json [--check]`.
I did not register M004 as a numbered migration: an applied migration is immutable (checksum), so a placeholder would be wrong forever. Proposal: a deploy step `python -m database seed-catalog --file ...` after `migrate` (idempotent; a later `1.0.1` file refines rows with no new migration). Tell me if you prefer M004 instead.
Options: send me the file, or ask me to draft all 56 (with the 42 subskills, Arabic glosses and the three retrieval prompts). The glosses and prompts need a human read either way (O6). The seed loader rejects a prompt that contains its own expression. Every current live phrase id must appear in the file (`catalog.missing_live_phrases`).

## For Gemini and the orchestrator (S4)

1. Review turn: `db.get_due_phrases(learner_id)` -> plans (phrase, step, family, prompt kind, at most 5). `entry = db.get_phrase_entry(id)`; `retrieval.build_prompt(entry, kind, family)`; `retrieval.build_hint(entry, level)`.
2. Grade: `retrieval.grade_attempt(text, entry.target(), shown_texts=[prompt, hint, any card text], hint_level=..., judge=<wrapper>, ...)`. The judge wrapper (yours, over `llm_transport`) returns `JudgeVerdict(linguistic_level, functional_level, functional_equivalent_credited)`, never an outcome. `QuotaShed` and `DeadlineExceeded` are picked up lazily from `llm_transport`; `JudgeShed` and `TimeoutError` always count as a shed.
3. If `result.records_evidence` is False (shed, or echo) there is nothing to score. A shed writes NOTHING and the item stays due; tell the learner `retrieval.SHED_MESSAGE`. An echo may be stored (`to_attempt_event` supports it) and earns nothing.
4. Otherwise `to_attempt_event(result, phrase_id=..., origin=..., prompt_kind=..., context_family=..., due=<now >= next_review_at>, schedule_step_before=plan.step)` and `append_event_ex(event_type="retrieval_attempted")`. The rebuild does the rest.
5. `validate_analysis(..., taken_ids=db.taken_diagnostic_ids())` (lazy, one indexed lookup per id) or `db.get_taken_diagnostic_ids(learner_id)` for a plain set. The key is global, so the lazy container checks every learner.
6. Debrief: `db.get_review_queue(learner_id)` + `db.get_debrief_counts(session_id)` -> `feedback_queue.render_debrief(entries, counts)`. Scene stealth targets: `db.get_spontaneous_candidates(learner_id, register)`.
7. Show the stage with `db.get_phrase_states(...)["effective_stage"]`, not `current_stage` (see decision 6).

## Decisions I made where S0 is silent (please confirm or overrule)

1. **Hybrid stage.** A phrase with only v1 evidence keeps the v2 ladder (the S1 M003 golden has a v2-Stable phrase that the V3 gate would not call Stable). From its first native `retrieval_attempted` the V3 ladder and gate apply.
2. **Legacy successes enter the schedule** on their own timestamp (S0: "first success from any origin"). The first V3 rebuild can therefore show old phrases as overdue; at most 5 per session are served.
3. **Counters.** `success` = success or hinted_success; `independent` = success with no hint. A delayed success is a due attempt at least 1 day after the last success (else the latest exposure before it).
4. **Echo** = outcome failure, `echo=True`, no levels, no evidence rows, ignored by the tracker, the schedule and the stage.
5. `unresolved_critical_errors` is 0 everywhere (O3 still open).
6. **Stable staleness is read-time.** The stored `current_stage` can still say Stable after 90 idle days (the projection never reads a clock); `effective_stage` and `get_phrase_states` apply the rule. v2 code that reads `current_stage` directly (for example `select_stealth_target_for_session`) sees the stored value.
7. The stage at rebuild time is computed at the learner's last event time (deterministic).
8. A native failure on a phrase with no exposure and no success stays Unseen (the stage ladder counts exposure events only).
9. **Debrief numbers:** turns = `learner_message_received` in the session; cards = `session_state_projection.cards_in_scene`; targets used = distinct phrases with a counted success in the session (v1 independent or spontaneous production, or a non-echo success or hinted success retrieval), retractions excluded.
10. **Spontaneous candidates:** scheduled phrases that are due or at step 1 or higher, and whose catalog register profiles include the scene's register.
11. **Matching:** echo is checked before the Python match; contractions are compared as written (python_exact) and expanded (python_fuzzy); slots match 1 to 6 words; curly and straight apostrophes are the same.
12. **Prompts:** a NULL prompt column falls back to the communicative intention; the seed model forbids a prompt containing a fixed fragment of two or more words of its own expression (single words are not checked).
13. `reconstruction_attempted` gives no phrase evidence (S0 5.7: reported, not scored into the ladder). `grade_reconstruction` is ready for S4.

## Zero-Bias report (not changed in S3)

Four of the eight live phrases already appear in my prompt files: `prompts/_partials/registers.md` ("I see where you're coming from", "it cuts both ways"), `prompts/analyst_system.md` ("my plate's pretty full"), `prompts/responder_system.md` ("push back on [something]"). The final 56 are unknown until the catalog exists; once they are, I will rewrite the examples in the same sprint as the V3 Analyst prompt. The test helper `catalog.zero_bias_violations` is ready to run as a release check.

## Not in this patch

The V3 Analyst prompt and raw JSON shape (S2's changelog listed them for S3; no S3 instruction included them, so I propose doing them with the orchestrator in S4), the retrieval judge prompt, shrinking `policy_engine.py`, M004, orchestrator wiring.
