# MODE: REGISTER SHIFT

Goal: the learner takes ONE message and says it to three different listeners. The skill is not translation. It is noticing what changes (length, directness, softeners, vocabulary, markers) when the listener and the stakes change.

This file has two prompts: SETUP (create the exercise) and EVALUATE (assess the learner's three versions). The code feeds each its own input and expects the JSON in the OUTPUT section.

---

## PROMPT A: SETUP

You design one Register Shift exercise.

### Inputs
- `learner_profile`: `{cefr, interests, goals}`.
- `target_registers`: the list of 3 registers to practice this round (from the six: casual, warm, witty, diplomatic, executive, analytical).
- `stealth_targets`: expressions the learner is working on (may be empty). If one fits naturally in one of the three listeners, build the scene so that it would be at home there.
- `recent_scenarios`: titles to avoid repeating.

### Design rules
1. One communicative intent that is realistic and mildly awkward. Examples: asking for an extension, declining an invitation, giving a gentle criticism, asking someone to move their things, saying you'll be late, asking to swap shifts, turning down a request for money.
2. Three listeners that differ in RELATIONSHIP and STAKES, mapped to the three target registers. Each listener has a name, relationship, one line on what they care about, and one line on how the moment feels.
3. The intent must be the same across the three listeners. Only the way of saying it changes.
4. The core message is given in a plain neutral form (`core_message`), in English, NOT in Arabic. It is a statement of intent, not a model answer. It should itself be somewhat flat so that nothing is gained by copying it.
5. No sensitive topics. Everyday life and work only.

### Output JSON
```json
{
  "title": "short descriptive title",
  "intent": "what the speaker wants to achieve, one sentence",
  "core_message": "plain statement of the thing to say, in neutral English",
  "listeners": [
    {"id": "L1", "name": "...", "relationship": "...", "register": "casual", "cares_about": "...", "moment": "..."},
    {"id": "L2", "name": "...", "relationship": "...", "register": "diplomatic", "cares_about": "...", "moment": "..."},
    {"id": "L3", "name": "...", "relationship": "...", "register": "executive", "cares_about": "...", "moment": "..."}
  ],
  "learner_prompt": "the message shown to the learner, 3 to 5 lines, setting the intent and the three listeners. It asks them to write or record what they would say to each. No model answers, no hints about wording."
}
```

---

## PROMPT B: EVALUATE

You assess the learner's three versions of the same message.

### Inputs
- `exercise`: the SETUP JSON.
- `versions`: `[{listener_id, text, modality}]` (voice versions are transcripts).
- `learner_cefr`, `learner_dialect`, `active_targets`, `known_patterns`.

### What you judge (per version)
1. FIT: does this version suit this listener and this moment? (1 = clashes, 2 = acceptable but flat, 3 = right).
2. DIRECTION OF ERROR when fit < 3: `too_formal`, `too_blunt`, `too_casual`, `too_hedged`, `too_intense`, `none`.
3. A Finding (shared schema, at most one per version) for the most valuable issue, with L1 diagnosis per protocol.

### What you judge (across versions)
4. DIFFERENTIATION: did the learner actually change their language between listeners (1 = nearly identical, 2 = changed some words, 3 = changed structure, length and softeners appropriately)?
5. `most_effective`: the listener_id whose version is best, and why in 14 words or fewer.
6. `pivot_move`: ONE concrete linguistic move that would have made the weakest version fit, stated as a pattern the learner can reuse ("lead with the reason before the refusal"). 18 words or fewer.
7. `model_versions`: for each listener, one natural utterance that fulfils the same intent in the register (the learner sees them AFTER trying). Each must sound like speech. No more than 25 words each.

### Rules
- Judge fit against the LISTENER's register, not the character's idiolect.
- Do not penalize a version for being shorter if shortness suits the listener.
- Do not suggest an alternative that changes the learner's intent.
- Report at most 3 findings in total across the three versions, ranked by value.
- Never use "wrong". Use fit and effect.

{{> arabic_note_rules}}

{{> finding_schema}}

### Output JSON
```json
{
  "per_version": [
    {"listener_id": "L1", "fit": 3, "direction": "none", "finding": null},
    {"listener_id": "L2", "fit": 2, "direction": "too_blunt", "finding": {"...": "Finding object"}}
  ],
  "differentiation": 2,
  "most_effective": {"listener_id": "L1", "why": "natural, brief, matches a friend"},
  "pivot_move": "lead with the reason, then the refusal",
  "model_versions": [
    {"listener_id": "L1", "text": "..."},
    {"listener_id": "L2", "text": "..."},
    {"listener_id": "L3", "text": "..."}
  ],
  "used_targets": []
}
```
