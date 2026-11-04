# S5 handoff: V3.0 live cutover (Claude)

Result of the local run on Python 3.13: 1187 tests, all pass. Expected on CI: 1191.

## Delivered (the six items of the S5 brief)
1. Analyst to policy: V3 Analyst prompt, then `validation.validate_analysis` (recent bot replies, taken diagnostic ids), then `feedback_policy.resolve_interaction_policy` (P0 line under 20 words, P1 single card gated by the budget, P4 queued for review). Orchestrator `process_turn` / `apply_analysis_outcome`.
2. Durable interaction state: `active_interaction` TTL, `session_revision` compare-and-set, scene revision bumped on mode changes, callbacks `<op>:<id>:<rev>` (at most 32 bytes) for `ug rc rs bk rvn rvh rvs ls`, stale taps answer "That option expired." (`ug`: "Saved to Review").
3. Reconstruction: `rc`, graded text, `rs` sends the stored attempt to Alex as a scene turn with `origin='reconstruction'`.
4. Silent spontaneous detection (Python checks the catalog form is in the learner's own text) and organic registration of a shown high-confidence phrase-sized alternative.
5. Due-phrase review: `get_due_phrases`, `retrieval.grade_attempt` with `make_judge`, hint ladder structure then Arabic gloss, a shed judge leaves the item due.
6. Merged debrief (zero LLM calls), 5D `/progress`, `audio_cache`, `learner_settings` auto-voice; `v2_complete` broadcast removed. Dead v2 policy removed (see CHANGELOG).

## For Gemini (your files)
* `app.py` was rewired on request. My baseline was the S4 copy of `app.py` in my working tree; `app_py_s5.diff` (in the zip) is the unified diff against it. If your deployed `app.py` differs from that baseline, apply the diff by hand instead of overwriting.
* `llm_transport.py` and `config.py` were not in my working copy. `ai_engine` calls `call_json(call_class, system, user, validator, *, deadline_s, correlation_id)` and reads `.value` / `.meta` of the result; `call_class` is passed as `llm_transport.CallClass(value)` when that enum exists, else as a plain string. `response_schema` is not passed. `QuotaShed`, `DeadlineExceeded` and `ResponderUnavailable` must propagate. The judge's temperature 0 and roughly 40 output tokens are not passed per call: please set them on the `judge` lane.
* Workflow: ship `data/` and `prompts/`; run `python -m database seed-catalog --file data/catalog_v1.0.0.json` after `migrate`. `tests_v3/test_s5_app.py` imports `app`; CI has the real `telebot` and `edge_tts`, a bare checkout gets stand-ins.
* Canary: set `CANARY_ADMIN_ONLY=1` for the 48 hour admin-only phase (only `ADMIN_ID` is served, others get a short notice); unset it to open to everyone. Operational, not code.
* `guide_and_branding.py` still says `v2_complete` in the guide text. Yours to update. The command list in `app.py` gained `/review` and new descriptions.

## Decisions worth a look
* The 💡 offer writes `feedback_card_delivered(trigger='end_of_turn')` when the button is attached (that is the visible card for pacing); the tap writes `trigger='tap'` per S0 5.4. The projection counts the pair once.
* The Analyst wait is 35 s (`ANALYSIS_WAIT_S`); after that the turn is recorded as `analysis_skipped(deadline)` and a result that arrives later is not applied.
* A P0 follow-up has no Listen button (its event id is not known when the message is sent).

## Not done / known limits
* Decode and workout session state still live in process memory (`policy_engine` registries); S0 wants them durable but they were not in the S5 list.
* A12 retry button: there is no retry op in `CALLBACK_OPS`, so the canned "please resend" line stays.
* Review does not switch the session to `TrainingMode.review_retrieval`; the open interaction row is what routes the text.
* No calibration run against real Gemini output yet; the first canary hours are that check.

## Questions
* Musa: open the canary to everyone after 48 hours with no `analysis_skipped(error)` spike?
* Gemini: confirm the `call_json` contract above and the `judge` lane limits.
