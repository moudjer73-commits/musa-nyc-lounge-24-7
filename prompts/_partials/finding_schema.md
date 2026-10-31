## THE FINDING OBJECT (shared by the Analyst and all mode evaluators)

A finding is one diagnosed, teachable issue in the learner's utterance. Its fields:

- `span`: the exact words from the learner's message that carry the issue (verbatim, as short as possible while still being the unit a native would replace).
- `category`: one of `comprehension`, `pragmatic`, `register`, `collocation`, `grammar`, `fluency`.
  - comprehension: a native listener might misunderstand or would have to ask.
  - pragmatic: the meaning is clear but the effect on the listener is not what the learner intends (too blunt, too cold, too intense, too intimate, too ritualistic, misread as commitment/refusal).
  - register: the formality or style does not fit the scene (too formal, too casual, written-style in speech).
  - collocation: words that do not go together in English, or a wrong light verb/preposition frame, but the meaning is clear.
  - grammar: a structural slip that is marked in speech (use sparingly: see level calibration).
  - fluency: voice only. Restarts, long gaps, filler density, from `voice_metrics`.
- `l1_transfer`: true only if you completed the protocol and can name a concrete Arabic source.
- `l1_pattern`: a code from the taxonomy when `l1_transfer` is true, otherwise null.
- `l1_source_ar`: the Arabic source construction in Arabic script (short), or null.
- `severity`: 1, 2 or 3. Rubric:
  - 3: a listener is likely to misunderstand, be offended, or read the speaker as rude, cold, aggressive or inappropriate for the situation.
  - 2: clearly marked: the listener understands, but it sounds translated, stiff, or noticeably off for the scene.
  - 1: slight. A native would register it only on reflection.
- `teachable_now`: true if the learner can adopt the better form immediately in one short chunk (a phrase or a clause). False if fixing it needs restructuring or a longer lesson.
- `pattern_key`: stable identifier of the ISSUE FAMILY, built as `category.PATTERN.frame`, all frame words lowercase with underscores. Examples: `collocation.COLLOC_LIGHT_VERB.take_decision`, `pragmatic.DIRECTNESS.bare_refusal`, `grammar.COUNTABILITY.work_plural`, `grammar.GEN.article_omission`. Use `GEN` as the PATTERN when `l1_transfer` is false. When `known_patterns` contains the same issue, REUSE its exact key so recurrence can be counted.
- `listener_effect`: how a native listener would likely perceive the original, in 12 words or fewer ("understood, but sounds translated", "reads as a curt refusal to a manager").
- `why_en`: the mechanism in one sentence of 25 words or fewer: the constraint, the register mismatch, or the pragmatic effect. Never say "wrong" or "incorrect".
- `ar_note`: the Arabic coaching note per the rules, or an empty string.
- `recast_form`: the shortest natural spoken form that carries the learner's meaning and fits the scene's register: a phrase or clause that another speaker could say back in conversation without sounding like a correction. Not a full lecture sentence.
- `recast_meaning`: what `recast_form` means in this exact context, 10 words or fewer ("too busy to take on more"). The director uses it so another speaker can find a natural slot for the recast. Null if `recast_form` is null.
- `alternatives`: 1 to 3 complete spoken utterances a native would actually say in this exact situation, each with:
  - `text`: the utterance, replacing the learner's span in context.
  - `register`: one of the six register names, or `neutral`.
  - `nuance`: 14 words or fewer, the connotation or what distinguishes it from the others.
  - `best_for`: 10 words or fewer, the situation or relationship it fits.
  Alternatives must differ in something real (register, strength, relationship fit), never be paraphrases of each other.
