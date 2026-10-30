# Changelog

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
