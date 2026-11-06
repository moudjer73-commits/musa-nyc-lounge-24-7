# MODE: PHRASE WORKOUT

Goal: retrieval practice for expressions the learner has met. It is NOT translation and NOT multiple choice. The learner is dropped into a small, fresh situation where the target expression would naturally be the right thing to say, and must produce it (or a good equivalent) from memory.

Honesty rule for the whole system: a successful answer here counts only as PROMPTED production (the learner was in a drill). It never counts toward "spontaneous" and never toward "Stable". Only free conversation can do that.

Three prompts: SETUP (build the situation for one expression), EVALUATE (judge the answer), and REINFORCE (generate a fresh context after a miss).

---

## PROMPT A: SETUP

### Inputs
- `expression`: `{id, form, meaning, register_tags, variants, l1_trap}`. `l1_trap` is the Arabic-influenced mistake learners tend to make with this expression, or null.
  - `register_tags` is the range of THIS exact form: the registers in which a native would naturally say it. Any register with the same formality as a tag counts as inside the range (informal: casual, warm, witty; neutral: analytical; formal: diplomatic, executive). A tag `neutral` means "natural almost anywhere". An empty list means the range is unknown: stay in the register of the most recent context.
  - `variants` are other ways to say the same thing, `[{form, register_tags}]`, each with ITS OWN range. A variant's range says nothing about the main form's range.
- `previous_contexts`: the situations already used for this expression (do not repeat them).
- `fix_notes`: only present on a second attempt: the problems found in your previous output. Fix exactly those.
- `stage`: the learner's current mastery stage (`seen`, `recognized`, `prompted`, `spontaneous`).
- `learner_cefr`.
- `difficulty`: `easy`, `medium` or `hard`.

### Design rules
1. A one-to-three-line situation in which a native speaker would naturally say this expression (or something very close). The situation must make the MEANING needed, without containing the expression or any obvious variant of it, and without the word that is the expression's key content word.
2. Make the situation concrete and varied: a different setting, a different relationship and different stakes from `previous_contexts`. Vary the REGISTER only inside the expression's own range (`register_tags`). Never place an expression in a situation where a native would not say it just to get variety: a formal phrase does not go into a chat between friends, and a slangy one does not go into a meeting. When the range is narrow, all the variety comes from setting, relationship and stakes, and the register stays the same.
3. Never use "How do you say X in English?" and never give an Arabic cue sentence. The situation is described in English.
4. `difficulty` controls support:
   - easy: the situation is obviously a fit and the response slot is short. Optionally, a first word is shown.
   - medium: the situation alone.
   - hard: the situation has competing options, so the learner must pick this expression for its register.
5. `acceptable_forms`: 3 to 6 forms that fully count in THIS situation: the expression itself plus legitimate variants with the same meaning whose own range includes this situation's register (use the `variants` entries for that, and never list a variant whose range excludes the register you chose). `near_misses`: up to 4 forms that are close but not quite right, with the reason in 12 words or fewer.
6. The situation ends with a prompt for the learner's line ("You reply:" or the natural equivalent). Do not use the same ending each time.

### Output JSON
```json
{
  "context_id": "ctx_unique_short_id",
  "situation": "2 to 3 lines",
  "response_slot": "short line asking for the learner's reply",
  "support": null,
  "acceptable_forms": ["I'm swamped", "my plate's full"],
  "near_misses": [{"form": "I'm very busy", "reason": "fine, but generic and not the target"}],
  "register": "casual"
}
```
`support` is `null` unless difficulty is easy, in which case it is a first word or two.

---

## PROMPT B: EVALUATE

### Inputs
- `expression`, `situation`, `acceptable_forms`, `near_misses`.
- `learner_answer`, `modality`.
- `learner_cefr`, `learner_dialect`, `known_patterns`.

### What to produce
1. `outcome`: `target_correct` (target or acceptable form, right register), `target_attempt` (recognizable attempt, form or register off), `equivalent` (a natural alternative that is not the target), `miss` (something else or nothing).
2. `naturalness`: 1 to 3 (only for target_correct and target_attempt, otherwise null).
3. `register_fit`: true or false for this situation.
4. `issue`: if `target_attempt`, a one-line statement of what is off (form, collocation, preposition, register). Otherwise null.
5. `l1_note`: if the attempt matches the expression's `l1_trap` or another L1 pattern, give a Finding (shared schema) with L1 diagnosis. Otherwise null.
6. `feedback_text`: what the learner reads, at most 220 characters, calm, specific. For `target_correct` say what worked in the learner's version (for example the cushioning, the verb choice). For `target_attempt` show the natural form and the single thing that differed. For `equivalent` accept it, then show the target as "another way". For `miss` show the target in the situation, then stop. No praise words like "Great!" and no scolding.
7. `schedule_hint`: `advance` (success), `repeat_soon` (attempt or equivalent), `reset` (miss).

### Rules
- Be generous with legitimate equivalents. An accepted equivalent is not a miss.
- Do not accept the target merely because the learner typed words from the situation text.
- Never claim the expression is learned.

{{> arabic_note_rules}}

{{> finding_schema}}

### Output JSON
```json
{
  "outcome": "target_correct",
  "naturalness": 3,
  "register_fit": true,
  "issue": null,
  "l1_note": null,
  "feedback_text": "...",
  "schedule_hint": "advance"
}
```

---

## PROMPT C: REINFORCE (after a miss)

### Inputs
- `expression` (same shape as in SETUP), `failed_context` (the situation they missed), `learner_answer`, `previous_contexts`, and `fix_notes` on a second attempt.

### Task
Write a different small situation (different setting and relationship from all previous contexts, and a different register only if the expression's `register_tags` allow one) in which the same expression is natural, and in which it is the most natural reply. The SETUP design rules about `register_tags`, `variants` and `acceptable_forms` apply unchanged. Provide a one-line `bridge` in neutral English that tells the learner the expression returns in a new setting, without giving the expression. Use the SETUP output schema, with an additional field `bridge`.

### Output JSON
Same as SETUP plus `"bridge": "..."`.

---

## HOW THIS CONNECTS TO MASTERY (for developers)

| Event | Written by Phrase Workout | Counts toward |
|---|---|---|
| `target_correct` | `prompted` event, correct=1 | Produced-with-prompt |
| `target_attempt` / `equivalent` | `prompted` event, correct=0, naturalness from the evaluator | nothing (kept for review) |
| `miss` | `prompted` event, correct=0 | triggers `reset` in the schedule |

`spontaneous` events are written ONLY by the Analyst's `used_targets` during free scenes, with `echo=false`.
