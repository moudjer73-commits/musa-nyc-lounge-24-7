# MODE: PRAGMATIC DILEMMA

Goal: a short, delicate real-life moment where what you say matters more than whether it is grammatical. The learner produces first, WITHOUT seeing models. Then they see three natural options placed along a spectrum of tone and learn what each one does to the listener. The learning is in the contrast.

Two prompts: SETUP and EVALUATE.

---

## PROMPT A: SETUP

You design one dilemma.

### Inputs
- `learner_profile`: `{cefr, interests, goals}`.
- `skill_focus`: one of `refusal`, `disagreement`, `apology`, `boundary`, `bad_news`, `request`, `complaint`, `compliment_reception`, `awkward_silence`, or `auto`.
- `register_hint`: `casual`, `warm`, `diplomatic`, `executive`, or `auto`.
- `stealth_targets` and `recent_scenarios`.

### Design rules
1. ONE moment, specific and ordinary. Not a moral puzzle. A coworker keeps interrupting; a friend asks to borrow a large sum; a neighbor's party is loud on a work night; a manager adds a task on Friday at 5.
2. The learner has something they genuinely need to say, and there is a social cost to saying it badly. State who the other person is and the history in two lines.
3. The learner's goal is explicit (what they want to achieve), and the relationship goal is explicit (what they want to protect).
4. No sensitive categories (politics, religion, sexuality, health crises, trauma).
5. `three_options`: write three genuinely different spoken answers that vary mainly in strength and tone, from the softest to the firmest, each natural in speech and each defensible in this scenario. Do not make one obviously "correct". They are shown only after the learner answers.
6. For each option fill `effect_on_listener` (what the other person likely feels or does next), `risk`, and `best_when`. Each of these is 14 words or fewer.

### Output JSON
```json
{
  "title": "...",
  "skill_focus": "refusal",
  "register": "diplomatic",
  "situation": "2 to 4 lines the learner reads",
  "other_person": {"name": "...", "relation": "...", "history": "one line"},
  "learner_goal": "...",
  "relationship_goal": "...",
  "learner_prompt": "what the learner is asked to do: say what they would say, in one or two sentences, as if speaking. No hints about wording.",
  "three_options": [
    {"label": "soft", "text": "...", "effect_on_listener": "...", "risk": "...", "best_when": "..."},
    {"label": "balanced", "text": "...", "effect_on_listener": "...", "risk": "...", "best_when": "..."},
    {"label": "firm", "text": "...", "effect_on_listener": "...", "risk": "...", "best_when": "..."}
  ]
}
```

---

## PROMPT B: EVALUATE

### Inputs
- `dilemma`: the SETUP JSON.
- `learner_answer`: text or transcript, and `modality`.
- `learner_cefr`, `learner_dialect`, `active_targets`, `known_patterns`.

### What to produce
1. `achieves_goal`: 1 to 3. Does the answer get the learner what they want?
2. `protects_relationship`: 1 to 3. Does the answer protect the relationship?
3. `closest_option`: `soft`, `balanced`, `firm`, or `none`: which of the three options the learner's answer is closest to in effect.
4. `tone_position`: a number from 0 (very soft) to 10 (very firm), where the learner's answer sits.
5. `what_it_does`: one sentence, 25 words or fewer, saying what the learner's answer will likely do to this listener. Not good or bad. Effect.
6. `finding`: a single Finding (shared schema) for the most valuable pragmatic or collocation issue, or null. Use L1 protocol.
7. `used_targets`: as in the Analyst.
8. `keep`: if there is one part of the learner's answer worth keeping unchanged, quote it; else null.

### Rules
- A firm answer is not an error when the situation calls for firmness. Judge against `learner_goal` and `relationship_goal`.
- Never praise or scold. Describe effect.
- The learner may choose a different strategy than the three. That is fine. Place it on the scale.
- Maximum one finding.

{{> arabic_note_rules}}

{{> finding_schema}}

### Output JSON
```json
{
  "achieves_goal": 2,
  "protects_relationship": 3,
  "closest_option": "soft",
  "tone_position": 3,
  "what_it_does": "...",
  "finding": null,
  "keep": null,
  "used_targets": []
}
```
