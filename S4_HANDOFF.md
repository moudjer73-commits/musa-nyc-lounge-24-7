# S4 hand-off (catalog v1.0.0, Zero-Bias, V3 Analyst prompt, retrieval judge)

Local run on Python 3.13: 1071 passed (816 `tests_v3`, 235 `tests_v2`, 20 `tests.py`). Expect 1075 in CI (`tests_v2` has 239 there). 79 new tests, no existing test edited. I did not run Python 3.11.

## 1. Deploy step (Gemini)

After `python -m database migrate`: `python -m database seed-catalog --file data/catalog_v1.0.0.json`. It is idempotent: a second run writes nothing. `--check` validates the file (counts 7/42/56) without touching the database. Please make sure the deployed artifact contains `data/` and `prompts/`.
A row is rewritten only when the file's `catalog_version` is higher than the stored one. So after the first seed, any edit to the content needs a version bump (`1.0.1`) or the seeder will skip it.

## 2. Needs a human read (Musa, O6)

Open `data/catalog_v1.0.0.json`. I could not verify these with a native reader:
* 56 Arabic glosses: functional explanations of what the phrase does, not word-for-word translations. Please check naturalness and tone. The 8 live phrases keep their v2 forms.
* 168 retrieval prompts (3 per expression): English, written so the learner has to produce the phrase; none contains it.
* 42 subskill names and descriptions, and for the 48 new expressions the register profiles, context families and `applicability`.
The tests enforce: no prompt contains its own expression (or any other catalog expression), no gloss uses the banned wording or the false ناقش + preposition frame, every expression has 2+ non-general families, no two expressions share a form.

## 3. Zero-Bias result

0 hits. 56 canonical forms plus all acceptable forms against 17 files in `prompts/` and `eval/` (now including the two new V3 prompts, which use only invented expressions in their examples). Rewritten examples: `_partials/l1_taxonomy.md`, `_partials/registers.md`, `analyst_system.md`, `responder_system.md`, `card_writer_system.md`. Inline v2 prompts in `ai_engine.py` and `orchestrator.py` also checked: clean. `test_catalog_release.py` makes this a release check.

## 4. V3 Analyst prompt (not wired yet)

`prompts/analyst_v3_system.md`, loaded by `ai_engine.load_analyst_v3_prompt()`. The live v2 pipeline still sends the v2 prompt, so nothing in production changes until the orchestrator is rewired. The prompt's three worked examples validate with zero drops, and a drift test checks every enum value and field name against `schemas.py`.
Decision: two L1 code vocabularies exist (coach codes and the live family names). The V3 prompt teaches the live family names (`COLLOC_LIGHT_VERB`, `COLLOC_PREP_VERB`, ...); `validation.py` accepts both.
`explanation_ar` is capped at 180 characters in the prompt (code trims at 200). The "discuss about" rule is kept: ناقش is directly transitive, never «ناقش في».

## 5. Retrieval judge (for Gemini's wrapper)

`retrieval.make_judge(call_llm)` returns a `Judge` for `grade_attempt`. `call_llm(system_prompt, user_prompt)` must return the model's JSON as a string or a dict. Raise `QuotaShed` / `DeadlineExceeded` as before: `grade_attempt` treats them as a shed. A reply that is not the agreed JSON becomes `JudgeShed`: nothing is recorded and the item stays due (learner sees `retrieval.SHED_MESSAGE`). The judge is never called for an echo or a Python match.
Decision: `functional_equivalent_credited` is normalised by code (true only when functional >= 2 and linguistic <= 1). Suggested call settings: temperature 0, short output (about 40 tokens).

## 6. policy_engine.py

Only `get_updated_user_guide_html` was removed (no caller anywhere). Everything the other files import is still there (a test checks every `from policy_engine import ...` in the repo). `decide`, `validate_analyst_output_against_turn` and `DialogueController` go in the orchestrator rewire.

## 7. Not in S4

* Orchestrator rewire to the V3 pipeline (Analyst V3 -> `validate_analysis` -> resolver -> review turn with `get_due_phrases` and the judge), the scripted-LLM end-to-end test and the canary. The S4 list did not include it.
* Deleting the superseded v2 policy code (with that rewire).
* Calibrating `ABSTAIN_THRESHOLD` and the score weights: needs `eval/` gold data and the harness.

## Questions

1. Is the orchestrator rewire plus end-to-end test the next sprint (S5)? If yes, I propose we agree the `call_llm` signature in section 5 first.
2. Gemini: does `main.yml` / the start command ship `data/` and run `seed-catalog` after `migrate`?
3. Musa: send corrections to glosses or prompts as a list; I will apply them as `1.0.1`.
