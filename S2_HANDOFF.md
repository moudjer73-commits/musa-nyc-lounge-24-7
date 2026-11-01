# S2 hand-off (validation and the policy resolver)

Baseline 545 tests, now 739 (484 in `tests_v3`). Python 3.13 locally; CI is the first 3.11 run.

## For Gemini: wiring

1. `config.py`: add `ABSTAIN_THRESHOLD = 0.80`. My modules never import `config.py`; the orchestrator passes `ValidationConfig(abstain_threshold=config.ABSTAIN_THRESHOLD)`.
2. Call order (S4): `validation.validate_analysis` -> store `result.analysis` as `analyst_diagnosis_recorded` -> `feedback_policy.resolve_interaction_policy(PolicyInputs, result.scored)` -> store the `PolicyDecisionV2` as `policy_decision_recorded`.
3. What the orchestrator must supply to validation: `recent_bot_texts` (the bot's last turns AND card text, or echo detection is blind to cards), `active_target_ids`, `input_modality`, `learner_id` plus `prior_counts=db.get_authoritative_occurrence_counts(learner_id)` (read BEFORE the turn's events are appended), and `taken_ids` if you can cheaply list existing `dg_` ids.
4. What it must supply to the resolver: `PolicyInputs` (turn index in the scene is 1-based), `RuntimeState` from `session_state_projection`, `strain` from `policy_engine.compute_learner_strain`, `responder_asked_clarification` (Path A), `has_active_interaction` from `interaction.get_active_interaction`.
5. A revision-changed P0 (A14) is the orchestrator's job: store `PolicyDecisionV2.pending_clarification` or the P0 line via `set_pending_clarification`. The resolver only fills `pending_clarification` for a Path B P0 that cooldown deferred.

## Decisions I made where S0 is silent (please confirm or overrule)

1. **`noop` added to `DropReason`.** S0 lists 11 reasons, S1 had 10. "All 10 gates" in the sprint note = the pipeline steps that drop; `noop` is the 11th reason.
2. **P0 carrier.** S0 gives P0 per diagnostic but "Responder status not ok" is not per diagnostic. Carrier = the highest-scoring `error` when status is not ok; every high-severity diagnostic when `p0.candidate`. With no carrier and status not ok, no card is shown anyway (all deferred `suppressed_by_p0`).
3. **Path A is an input flag** (`responder_asked_clarification`), not inferred from reply text.
4. **P0 ignores** the active-interaction rule, warm-up, silence and the queue floor. **P1 respects** an active interaction (a late analysis must not interrupt a reconstruction).
5. **Pragmatic-high P1 exception:** gap 2, no warm-up, strain bonus not applied; still needs a non-silent preference, no flow, fewer than 6 cards.
6. **Empty alternative is a `noop` for every classification**, not only errors (S0 wording). `feedback_item_projection.alternative` is NOT NULL and a card with nothing to teach is useless.
7. **Echo** on a target assessment: `TargetUseAssessment` has no `echo` field, so independent/spontaneous evidence is downgraded to `cued_production` (the same downgrade the model applies to a cued use) and `DropRecord(tN, echo)` is written. The assessment is kept.
8. **Field-level removals are recorded as DropRecords but the diagnostic survives:** an over-long or tone-failing Arabic note (`dN.explanation_ar`, `note_tone`), an unknown or unsupported L1 claim (`dN.l1`, `pattern_code`). A false Arabic frame in the mandatory English explanation drops the whole diagnostic (`note_tone`). Ref grammar: `dN`, `dN.field`, `tN`, `analysis`, `communicative`, `p0`, `diagnostics`.
9. **LLM-supplied classification, domain and acoustic confidence are held back** so a wrong classification is `classification_mismatch` and any acoustic value is `domain_modality`, not `schema`. `confidence_score` and the band are always recomputed by code (minimum of the set confidences).
10. **Two L1 vocabularies are accepted** (coach codes and the live Analyst's `COLLOC_PREP_VERB` family) until S3 picks one.
11. **Priority score is rounded to 2 decimals** and the P1 threshold (55) compares the rounded value.
12. **Audio claims** (step 2, text input only) use a keyword list over `explanation_en`, `listener_effect` and `intended_meaning`; it can false-positive on a word like "accent". The eval harness should measure it.
13. **`dg_` ids are 8 hex (32 bits)**: a collision in `feedback_item_projection` would break a rebuild, so ids are checked against `taken_ids`. Please make the orchestrator pass the learner's existing ids, or tell me if you prefer a DB helper.

## Question for Musa (Arabic accuracy)

The old taxonomy listed «تناقشوا حول الموضوع» as the source of "discuss about it". That Arabic is valid (form VI, reciprocal), but it is a different verb from ناقش and you ruled the English error is an analogy with "talk about", so I removed it as a PREP_CALQUE source and added a counter-example. If you want a *weak* transfer note for it, it is a one-line rule change in `validation.INTRALINGUAL_ONLY_RULES`.
«شرح لي» and «ردّ على» stay in the live prompt as written (valid Arabic); I only added the correction `reply to my message` and `reach` that the line was missing.
