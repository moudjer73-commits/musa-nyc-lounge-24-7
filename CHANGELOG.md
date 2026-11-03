# Changelog

## V3.0, Sprint S4: catalog v1.0.0, Zero-Bias, V3 Analyst prompt, retrieval judge (Claude's files)

Baseline after S3 (CI): 996 tests (737 `tests_v3`, 239 `tests_v2`, 20 `tests.py`); the same suite run locally on Python 3.13 gave 992 (235 `tests_v2`).
After S4, run locally on Python 3.13: 1071 tests (816 `tests_v3`, 235 `tests_v2`, 20 `tests.py`), so CI should show 1075.
`tests_v3` grows by 79: `test_catalog_release.py` 18, `test_analyst_v3_prompt.py` 17, `test_judge.py` 40, `test_policy_engine_shrink.py` 4.
No existing test was edited. All 992 earlier tests pass unchanged.

### New
* `data/catalog_v1.0.0.json`: the release catalog (S0 4.4). 7 competencies, 42 subskills (6 each), 56 expressions (8 per competency), all 8 live phrase ids with their v2 canonical forms, register profiles and applicability, Arabic glosses, and the three retrieval prompts (`meaning_prompt`, `situation_prompt`, `function_prompt`) for each. Every expression has at least 2 non-general context families, so the Stable gate can be reached for all of them. Seed it with `python -m database seed-catalog --file data/catalog_v1.0.0.json` after `migrate` (idempotent; no M004).
* `prompts/analyst_v3_system.md`: the V3 Analyst prompt and the raw JSON it must return (nested `communicative`, `p0`, `diagnostics[]` with `repair_scope`, `communicative_impact`, `learning_value`, `linguistic_confidence` / `pragmatic_confidence`, `target_assessments`). Ids, offsets, band, priority and acoustic fields are code-owned and never requested. Three worked examples; the tests run them through `validation.validate_analysis` and require nothing dropped. `ai_engine.load_analyst_v3_prompt()` loads it. The v2 inline prompt stays wired to the v2 pipeline until the orchestrator rewire.
* `prompts/retrieval_judge_system.md` and, in `retrieval.py`: `load_judge_prompt`, `judge_user_payload`, `parse_judge_response` (strict, raises `JudgeParseError`), `make_judge(call_llm)` (an unusable reply becomes `JudgeShed`, so nothing is recorded and the item stays due). The judge returns two levels and a flag, never an outcome; code owns the flag (it survives only with functional >= 2 and linguistic <= 1).

### Behaviour changes in existing code
* `catalog.zero_bias_violations` reports every offending file (it used to stop at the first hit per expression, which hid a second overlap in `card_writer_system.md`).
* Zero-Bias cleanup, 5 prompt files, examples rewritten so none of the 56 canonical or acceptable forms appears in `prompts/` or `eval/`: `_partials/l1_taxonomy.md`, `_partials/registers.md`, `analyst_system.md`, `responder_system.md`, `card_writer_system.md`. `RENDERED_PROMPTS.md` regenerated. Python files with inline v2 prompts (`ai_engine.py`, `orchestrator.py`) were checked too: no hits.
* `policy_engine.py`: only `get_updated_user_guide_html` removed (no caller in any file). `decide`, `validate_analyst_output_against_turn`, `compute_learner_strain`, the workout helpers `decide` calls, and the session registries stay because `orchestrator.py`, `ai_engine.py`, `app.py` and `tests.py` still import them. The module docstring now says what V3 replaced and where new rules go.

### Not done in S4, on purpose
Orchestrator rewiring, the scripted-LLM end-to-end test and the canary (S0 "integration"); deleting `decide` and the other superseded v2 policy code (goes with that rewire); calibration of `ABSTAIN_THRESHOLD` and the score weights (needs the eval harness and a gold set).

## V3.0, Sprint S3: retrieval, mastery, scheduler, queue, catalog (Claude's files)

Baseline after S2 (CI, commit e6b80bf): 743 tests (484 `tests_v3`, 239 `tests_v2`, 20 `tests.py`).
After S3, run locally on Python 3.13: 992 tests (737 `tests_v3`, 235 `tests_v2`, 20 `tests.py`). The same 4-test difference in `tests_v2` was present before S3, so CI should show 996.
`tests_v3` grows by 253: `test_mastery.py` 56, `test_scheduler.py` 47, `test_retrieval.py` 69, `test_feedback_queue.py` 17, `test_catalog.py` 28, `test_rebuild_v3.py` 36.
One existing test helper was edited, for the reason below: `v1_columns()` in `tests_v3/test_migrations.py` no longer compares `phrase_state_projection.next_review_at_utc`. v2 had the column but never wrote it; M003's own list of v1-visible columns never included it; the scheduler now fills it.

### New
* `scheduler.py` (S0 5.6): intervals 1/3/7/14/30/60 days, entry on the first success from any origin, step moves only on scheduled (due) attempts, lateness never penalised, due selection (most overdue first, ties by lower step, at most 5), family rotation, prompt-kind rotation (never `spontaneous` in Review), spontaneous eligibility.
* `mastery.py` (S0 5.5): quality q, evidence rows per attempt (production, independence, transfer, pragmatic), dimension scores, display-only mastery index, `PhraseTracker`, the Stable gate (`stable_gate` says which condition fails), the miss rule, and `effective_stage` (Stable stale after 90 days, read time).
* `retrieval.py` (S0 5.7): Python-first grading. Echo (token Jaccard >= 0.8) is checked first, then token-aligned matching of the canonical and acceptable forms (slots 1 to 6 words, contractions), then the injected judge, which returns levels only; Python maps them to the outcome. A shed or missed deadline records nothing and the item stays due. Reconstruction uses the same pipeline. Prompts and hints are built here and never contain the target form.
* `feedback_queue.py` (S0 5.8, 5.9): deferred queue selection (21-day window, recurrence rule, group by pattern, at most 5), merge rule, templated debrief (zero LLM calls).
* `catalog.py` (S0 4.4): `CatalogSeed` model (a prompt may not contain its own expression), idempotent seeder (a row is written only when its stored version is lower; legacy is lowest; nothing is deleted), release-count check (7/42/56), `missing_live_phrases`, Zero-Bias grep helpers.
* `database.py`: `python -m database seed-catalog --file F [--check]`; helpers `get_taken_diagnostic_ids(learner_id=None)`, `taken_diagnostic_ids()` (lazy container for `validate_analysis`), `get_phrase_entry`, `get_phrase_states`, `get_due_phrases`, `get_spontaneous_candidates`, `get_review_queue`, `get_debrief_counts`, `seed_catalog`.

### Behaviour changes in existing code
* `database.rebuild_projections_in_tx` now replays every (learner, phrase) history through `mastery.PhraseTracker`: `phrase_evidence` rows for `retrieval_attempted`, and the V3 columns of `phrase_state_projection` (counts, dimension scores, mastery index, schedule step, `next_review_at_utc`, last outcome, last success). A phrase with no native V3 attempt keeps the v2 stage ladder exactly (M003 golden unchanged); a phrase with native attempts gets the V3 stage. Still deterministic: no clock reads, two rebuilds byte-identical.
* `validation.py`: `taken_ids` may be any container that supports `in` (ids given out in the same call are tracked locally).

### Not done in S3, on purpose
M004 as a numbered migration (the catalog content file is missing, and an applied migration is immutable), orchestrator wiring (S4), the V3 Analyst prompt and raw JSON shape, shrinking `policy_engine.py`, the retrieval judge prompt.

## V3.0, Sprint S2: validation and the policy resolver (Claude's files)

Baseline after S1: 545 tests (235 `tests_v2` + 20 `tests.py` + 290 `tests_v3`).
After S2: 739 tests. The 255 v2 tests pass unchanged. `tests_v3` grows from 290 to 484 (+194: 133 in `test_validation.py`, 61 in `test_policy_resolver.py`).
One S1 test was edited, and only for the reason below: `test_canonical_enums_match_s0_section_2` now expects `noop` in `DropReason`.
Run on Python 3.13 only; the CI job is the first Python 3.11 run.

### New
* `validation.py` (S0 5.1): `validate_analysis(raw, ...) -> ValidationResult`. All eleven steps in contract order, every drop recorded as `DropRecord(ref, reason)`: schema per diagnostic, domain and modality, NFKC/curly-quote normalisation with a tokenizer that keeps offsets in the original string, token-run span resolution (`span_start`/`span_end`, learner's exact casing), no-op, classification consistency, pattern code and Arabic note checks (ported from `coach/checks.py`), abstention (`ValidationConfig.abstain_threshold`, default 0.80), priority score, overlap resolution, target assessments with the echo guard, and `dg_` + 8 hex ids (deterministic per turn and position, collision-safe against `taken_ids`).
* `feedback_policy.py` (S0 5.2 and 5.3): `resolve_interaction_policy(PolicyInputs, scored) -> PolicyDecisionV2`. P0/P1/P4 classes, the queue floor, Path A/Path B for P0, one card per turn, the budget (silent/light/active gaps, strain bonus, warm-up, flow, 6 cards per scene) and the six gates recorded per finding. Re-exported from `interaction.py`.
* `ScoredDiagnostic` carries each survivor's score, family and prior count from validation to the resolver (`DiagnosticV2` has no score field).

### Linguistic accuracy rule (L1/L2)
* Standard Arabic ناقش is directly transitive (ناقشَ الموضوعَ), like English "discuss the topic". Code now (a) removes any note or L1 field that asserts ناقش + في/عن/على/حول/ب (every inflection and clitic prefix), (b) never attributes English "discuss about" to Arabic: the L1 claim is stripped and the correction is kept, because it is an English-internal analogy with "talk about". Valid Arabic is not flagged (ناقشه في الأمر, and the reciprocal form VI تناقشوا في/حول الموضوع).
* The false claim was also removed from the prompts: `ai_engine.py` (live Analyst prompt), `prompts/_partials/l1_taxonomy.md` and the regenerated `RENDERED_PROMPTS.md` (that one line differs from v2).

### Behaviour changes in existing code
* `schemas.py`: `DropReason.NOOP = "noop"`. S0 lists 11 reasons (step 5 produces one); S1 shipped 10. `CONTRACT_VERSION` is `3.0.0-s2`.
* `interaction.py`: re-exports the resolver names. No behaviour change.

### Not done in S2, on purpose
Orchestrator wiring (S4), mastery and retrieval (S3), shrinking `policy_engine.py`, the V3 Analyst prompt and raw JSON shape (S3), calibrating `ABSTAIN_THRESHOLD` and the score weights (eval harness).

## V3.0, Sprint S1: durability and migrations (Claude's files)

Contract: `Spoken English Lab V3.0 — S0 Contracts` with amendments A1-A15 and open items O1-O6 signed.
Baseline: v2 zip `musa_live_repo_latest.zip`, 255 tests green (235 pytest in `tests_v2` + 20 in `tests.py`).
After S1: the same 255 still pass **unchanged** (no existing test was edited), plus 290 new tests in `tests_v3`
(545 in total). `python -m pytest tests_v3 -q` needs no network and no API key.

### New
* `clock.py`: `Clock` protocol, `SystemClock`, `FakeClock` (time travel, thread-safe). Every timestamp is UTC ISO-8601 with a trailing `Z`; nothing in the new code reads the wall clock.
* `schemas.py` (extended, v2 models untouched): all S0 section 2 enums, the 14 + 3 event-type registry, V2 payload models (`extra='forbid'`), and the pure `upcast(row) -> EventV2` (v1 rows are never rewritten; readers upcast).
* `migrations/`: runner + M001 (`event_types` table and the `event_log` rebuild with a foreign key, fingerprint-verified, append-only triggers re-probed), M002 (all V3 tables and columns, `learner_settings` backfill), M003 (projection rebuild through `upcast`, run twice and compared byte for byte).
  Runner rules: one `BEGIN IMMEDIATE` per migration, `foreign_key_check` before COMMIT, a checksum change on an applied version is a hard error, a database ahead of the code is refused.
* `interaction.py`: durable `active_interaction` (lazy TTL from the injected clock, CAS on `interaction_revision`), text routing, and the `<op>:<id>:<rev>` callback wire format (<= 32 bytes; legacy callbacks parse to `None`).
* `database.py`: `python -m database migrate|status [--check]` (exit 0 ok, 1 error, 10 pending), V2 writers (`append_event_ex`, schema 2.0), `accept_scene_turn`, `append_events_if_revision(_ex)` (A14 CAS), pending clarification, auto-voice, audio cache, and a deterministic projection rebuild that also fills `feedback_item_projection` and `phrase_evidence`.

### Behaviour changes in existing code (all covered by tests)
* `EventSourcedDatabase` runs the migration runner on start-up (`auto_migrate=False` opts out).
* `append_event` keeps its signature and return value and still stamps `schema_version='1.0'` (callers still build v1 payloads until S2/S4); `append_event_ex` writes `2.0`.
* `register_phrase` is now an upsert of the v2 columns. v2 used `INSERT OR REPLACE`, which would wipe the V3 catalog columns on every call.
* `phrase_state_projection.updated_at_utc` is the time of the phrase's last event. v2 stamped the wall-clock time of the rebuild, which made two rebuilds differ. It is the only v1-visible column that changed meaning; the golden test excludes it and says so.
* `read()` is read-only (`PRAGMA query_only`).

### Tests added (`tests_v3`, 290)
clock (14), schemas (25), upcast (30), migrations (42, including a golden v2 database with all 14 event types, an injected failure at every point of every migration, and a real `os._exit` kill in a subprocess), database (42), interaction (128), CLI (9).

### Not done in S1, on purpose
M004 (mastery columns), `validation.py`, the policy resolver, mastery/scheduler/retrieval, orchestrator changes. The mastery aggregate columns of `phrase_state_projection` keep their defaults until S3.

### Not verified
Python 3.11 (the CI target): every S1 file compiles under 3.11 and the stdlib-only parts (clock, M001, M002) ran on 3.11.18, but the full suite ran on 3.13 only, because pydantic and pytest could not be installed for 3.11 in the build sandbox. The first full 3.11 run is the CI job.

## v2: fixes from the external review (one group at a time, suite run after each)

v1 is the first zip that was delivered (183 tests). Every group below lists what changed, which
existing tests changed and why, and what is still NOT verified.

### Group 1: registers are profiles, formality is what the code counts
Problem: the six registers are not on one scale (warm/witty are tones, analytical is a discourse mode).
`STABLE` required use in 2 registers, so casual + warm + witty counted as "diverse" while staying
at the same formality.
* New `coach/registers.py`: `formality_of`, `fits`, `required_formalities`. Formality buckets:
  informal (casual, warm, witty), neutral (analytical), formal (diplomatic, executive).
* `mastery`: STABLE needs >= 2 formality buckets (`STABLE_MIN_FORMALITIES`), not 2 labels. An expression
  whose own `register_tags` span only one bucket needs one (`db.stage_of` passes `min_formalities`).
* `db.metrics`: `register_diversity` (labels / 6) replaced by `formality_diversity` (buckets / 3).
* `RegisterShiftSetup` rejects three listeners that do not differ in formality or repeat a register.
* Prompts: `_partials/registers.md` now says the six are performance profiles; `mode_register_shift.md`
  requires target registers to differ in formality.
* Test changes: `test_db.py::test_metrics_are_derived_from_events` (metric renamed, now 2/3 for casual + diplomatic and
  unchanged by adding warm). New `tests/test_registers.py` (+6 tests).
* Not added, on purpose: separate stored dimensions for warmth, humor, assertiveness, emotional intensity and verbosity.
  The Responder already receives those as dials inside each profile; nothing on the learner side consumes them
  (no policy rule, no mastery rule), and each would be one more judgement the Analyst makes without any way to validate it yet.

### Group 2: an expression is only drilled or targeted where it is natural
Problem: Phrase Workout asked for "a different register" each time with no limit, and the vault's
`register_tags` came from the first alternative only. A first fix idea (union of all alternatives' tags)
was rejected in review: it would make a phrase eligible for a context just because a different phrase
in the same finding fits there.
* `Expression.register_tags` = range of THIS form. Evidence is only (a) alternatives that contain the form
  and (b) the scene register where the Analyst judged the recast natural (`recast_form` is defined as fitting it).
* New `Expression.variants` (`[{form, register_tags}]`): every other short alternative keeps its own range.
  `acceptable_forms` stays as the flat superset used only for recognising use and for keeping the character from saying a target.
* `db.upsert_expression` merges evidence (ranges only grow when the same form is shown natural elsewhere). New `variants` column.
* `modes.workout_setup_problems` + `run_workout_setup`: code rejects a setup whose register is outside the range, that lists a variant
  where its own range excludes it, or whose situation contains the expression; one retry with `fix_notes`. Same path for REINFORCE.
* `director.pick_targets` skips a target that does not fit the scene register; `create_scene` passes `register_tags` to the generator.
* Prompts: `mode_phrase_workout.md` (inputs, rules 2 and 5, REINFORCE), `scene_generator_system.md`.
* Test changes: none of the existing tests changed. One of my own existing pipeline tests initially failed after the change
  (the target was no longer offered in a casual scene): it exposed that the scene register is evidence for the form's range,
  so `expression_from_finding` now takes `scene_register`. New `tests/test_workout_range.py` (+9 tests). A mutation check
  (making `fits` always true) fails 4 tests, so they are not vacuous.

### Group 3: the Debrief Writer can no longer invent how the scene went
Problem: with empty `wins` the prompt demanded "a specific, true observation about how the conversation
went" while its inputs were only title, register and a turn count.
* New `SessionFacts` model and `db.session_facts(session_id)`: learner turns, average and longest turn, first/second-half
  averages and `length_trend` (only from 4 turns on), questions asked, voice/text turns, beats reached/total, cards shown.
  All whole numbers. Computed from the turn log, never by the model.
* `end_scene` sends `session_facts` instead of `turn_count`.
* Prompt: the opening observation must be backed by a `session_facts` field and quote its number, or be omitted; never mood,
  confidence, fluency or what the other person felt. Two new examples (with and without a supportable observation). `fix_notes` input.
* Code guard `checks.debrief_unsupported_numbers`: any figure in the debrief that is not in the data given is rejected, retried
  once with `fix_notes`, and otherwise the debrief is dropped (empty string) rather than shown.
* Test changes: none existing changed. `SessionFacts` added to the prompt drift test. New `tests/test_debrief.py` (+8).
* Limit: the guard checks digits only. A fabricated claim written in words ("you asked a lot of questions") is not caught by code;
  that part rests on the prompt rule.

### Group 4: the Analyst is calibrated against false alarms, and there is a way to measure it
Problem: the taxonomy treated the ABSENCE of "so / well / I mean / you know" as a flaw (an absence has no words to quote, and many
fluent native speakers use very few), and nothing in the repository measured how often the Analyst reacts to a sentence a native would not notice.
* `l1_taxonomy.md` DISCOURSE_MARKER_GAP: absence is never a finding; only mismatched or too-formal markers actually said.
* `analyst_system.md`: new section 3.3b "Do not flag" (nine families: direct-but-complete refusals, short natural replies, slang that fits the scene,
  correct-but-uncommon wording, natural American/British variety, proportionate cushioning, single "please/could you", chosen cultural formulae,
  speech-to-text artifacts) and the closing test "would a thoughtful native speaker in THIS scene actually react to this?". C1/C2 line no longer lists discourse markers.
* New `coach/evalset.py`, `eval/analyst_cases.json` (22 negative + 13 positive, hand-written), `tools/eval_analyst.py`. Runs the Analyst through the real
  `_analyse` path and the real `decide` with a wide-open budget, and reports `false_alarm_rate`, `visible_false_alarm_rate`, `recall` and negatives by family.
  An analyst outage is counted as an error, never as a clean pass.
* Tests: new `tests/test_analyst_examples.py` (every worked example passes the same sanitising as a live answer) and `tests/test_evalset.py` (+9).
  Mutation checks: making `visible` always false, making every span overlap, and not treating an outage as an error each fail tests.
* NOT verified: nothing here says how a live model scores. The seed set is small and written by one person.

### Group 5: Think-Aloud uses a persona's voice, not the errand it was written for
Problem: nothing said where the Think-Aloud partner comes from, and a Persona Card mixes idiolect with scene errands (`wants_in_scene`, `role_in_scene`,
speech samples about the deck). The Priya the reviewer saw in the rendered file was the example persona filling `{{persona_card}}`; the prompt itself is persona-agnostic.
* New `prompt_loader.voice_only_persona(card, scene=None)`; `ModeRunner.run(..., scene=)` applies it before building the interlocutor prompt.
  Role, errand and attitude are replaced; speech samples survive only for REACT / BACKCHANNEL / TEASE and only when they share no content word with the scene
  (an ASK line such as "any chance you'd have an hour this afternoon?" shares no keyword with its scene and still carries the errand, so a word filter alone was not enough);
  background sentences that share a content word with the scene are dropped.
* `mode_think_aloud.md` INTERLOCUTOR: "only the voice and manner carry over ... no errand, deadline, favor to ask".
* `tools/render_prompts.py` now renders Think-Aloud with the voice-only persona, i.e. what the model really receives.
* Tests: new `tests/test_think_aloud_persona.py` (+6). Limits: personality traits in `background` are kept (they are not errands); the filter is deliberately conservative and may drop harmless samples.

### Group 6: direct is not rude, softer is not better
Problem: the worked refusal example taught cushioning as the only fix, contradicted the `executive` profile's own "politeness through brevity", and the learner's
sentence in it already contained a reason, so "bare refusal" at severity 2 overstated it.
* `analyst_system.md`: principle 9 ("direct is not rude, and softer is not better"); first alternative stays as direct as the scene tolerates and complete; Example 2 now
  shows `No. I can't do it.` with `I can't take that on this week. I'm at capacity until Friday.` first and the warmer option second; Example 6 (nothing to flag: a direct, complete refusal) and Example 7 (OVER_SOFTENING, severity 1) added.
* `l1_taxonomy.md`: new pattern `OVER_SOFTENING` (the mirror of DIRECTNESS; stacked Arabic courtesy formulae); DIRECTNESS now flags only the bare form that stops after "no".
* `checks.L1_PATTERN_CODES` includes `OVER_SOFTENING`; card and Arabic-note examples updated to match.
* Test changes: none of the older tests changed; `tests/test_analyst_examples.py` checks that the refusal example teaches direct-first and that a restraint example exists.

### Group 7: say what the performer does not see
* `responder_system.md`: one paragraph stating that the plan (later developments, what the learner will be given a reason to say, turns remaining) stays with the director and arrives
  one piece at a time. The code already hid `beats`, `need_slots`, `register_plan` and `max_turns` from the performer (`tests/test_prompts.py::test_performer_does_not_see_future_beats_or_need_slots`); the prompt just never said so.

### Correction to Group 3 (found while verifying, not by the review)
The first debrief guard accepted any digit that appeared anywhere in the payload. Facts are small whole numbers, so coincidences are common: with `avg_words` = 7 and
`questions_asked` = 1, "you asked 7 questions" passed.
* New `Grounding` and `DebriefOut` models: the writer returns `grounding: [{field, value}]` for every number it takes from `session_facts`.
  `checks.debrief_unsupported_numbers(html, payload, grounding)` rejects a declaration that does not equal the fact, and any figure in the text that is not declared.
  Digits inside the learner's own phrases (`items`, `wins`) and the scene title stay free.
* Test changes: `tests/test_debrief.py` number-guard test rewritten (the old one asserted the v2.0 message and behaviour); two tests added; `test_prompts.py` drift list gained `Grounding` and `DebriefOut`.
  Mutation check: not comparing a declaration with the facts fails three tests.
* Still not caught: a true field declared for the wrong noun ("asked 7 questions" declaring `avg_words`=7).

### Repair during this pass
While rewriting `checks.py` for the correction above, the tail of the file (`arabic_note_problems_in_spoiler`, `forbidden_phrases_for`) was cut off by mistake. It was restored from
the v1 copy and the file diffed against v1: the only differences are `OVER_SOFTENING`, the typing import, and the debrief guard. The suite caught it at once (5 modules failed to import).

### Verification status of this version
235 tests pass from a clean extraction of the zip. Groups 1 to 3 were re-checked with independent scenarios (not only by the tests that came with them):
casual + warm + witty over three sessions stays PRODUCED_SPONTANEOUS, casual + diplomatic reaches STABLE, an informal-only expression needs one bucket, `session_facts` averages and halves are right.
NOT verified anywhere: behaviour with a real model, `GeminiClient` against the live API, the eval seed set's numbers.
