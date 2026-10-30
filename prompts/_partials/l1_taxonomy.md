## L1 ARABIC TRANSFER: DIAGNOSTIC PROTOCOL AND TAXONOMY

Your job is to tell, for each flagged item, whether it is plausibly caused by a specific Arabic construction, and if so which pattern. This matters because L1-driven errors are systematic: they recur, they can be drilled as a family, and the Arabic explanation is most useful exactly there.

### Protocol (apply in order, for every candidate finding)

1. IS IT ACTUALLY MARKED? Would a native speaker, hearing this in this situation, find it odd, unclear, stiff, rude, or "translated"? If a native would simply not notice, do not flag it.
2. CAN YOU RECONSTRUCT A SPECIFIC ARABIC SOURCE? You must be able to name a concrete Arabic word, collocation, preposition, structure or routine that the learner's English plausibly mirrors, and write it in Arabic script in `l1_source_ar`. If you cannot, `l1_transfer` is false. Never guess a source to make the explanation feel richer.
3. PICK THE PATTERN CODE from the list below. If two fit, pick the one that best predicts what the learner will do wrong NEXT time, not the one that sounds more academic.
4. CHECK THE DIALECT. If `learner_dialect` is known, prefer the source construction as that dialect would say it, but only if you are confident of it. If unsure, give the MSA source. Never mix dialects. Never assume a dialect from the learner's name or country.
5. SEPARATE TRANSFER FROM DEVELOPMENTAL AND PERFORMANCE ERRORS. Dropped articles, missing third-person -s, tense slips in fast speech are often performance errors that every learner makes regardless of L1. Do not label these as L1 transfer unless the Arabic source is real (for example the generic definite "the life is hard" mirrors الحياة صعبة).
6. SET severity AND teachable_now (rubric in the main prompt).

### Pattern codes

COLLOC_LIGHT_VERB
Verb + noun combinations where Arabic uses a verb whose closest English equivalent is the wrong light verb.
- "take a decision" (اتّخذ قرارًا) → make a decision / make a call
- "do/make an exam" (أجرى امتحانًا / قدّم امتحانًا) → take an exam (AmE), sit an exam (BrE)
- "open/close the light" (فتح/أغلق النور) → turn on/turn off the light
- "say the truth / say a lie" (قال الحقيقة / قال كذبة) → tell the truth / tell a lie
- "do a mistake" (ارتكب خطأً) → make a mistake
- "see a dream" (رأى حلمًا) → have a dream
- "drink a cigarette" (شرب سيجارة) → smoke a cigarette / have a cigarette
- "make a research" (أجرى بحثًا) → do research
- "do a party" (عمل حفلة) → throw/have a party
- "gain money" (كسب مالًا) → make money

PREP_CALQUE
A preposition or particle copied from the Arabic verb or adjective frame, including transitive/intransitive mismatches.
- "married with" (متزوّج بـ) → married to
- "afraid from" (خائف من) → afraid of
- "good in math" (جيّد في) → good at
- "arrive to / enter to the room / go to home" (وصل إلى / دخل إلى / ذهب إلى البيت) → arrive at/in; enter the room; go home
- "discuss about it" (تناقشوا حول الموضوع) → discuss it
- "wait him / listen him / search a job" (انتظره، استمع إليه، بحث عن عمل) → wait for him; listen to him; look for a job
- "think in it / interested by / responsible of" (فكّر في، مهتم بـ، مسؤول عن) → think about it; interested in; responsible for
- "since two years" (منذ سنتين) → for two years
- "in the weekend" → on the weekend (AmE) / at the weekend (BrE)

IDIOM_CALQUE
Set expressions or formulaic Arabic phrases translated word for word, or used where English has a different routine phrase.
- "May God give you health" (يعطيك العافية) → "Thanks, great work." / "Appreciate it."
- "It's on my head / on my eye" (على راسي / على عيني) → "Consider it done." / "Happy to." / "Absolutely, no problem."
- "Don't tire yourself" (لا تتعب نفسك) → "Please don't go to any trouble." / "Don't go out of your way."
- "God bless you" as generic thanks (بارك الله فيك) → "Thank you, I really appreciate it." (Not wrong; in secular workplace settings it can read as unexpected. Severity 1 at most.)

SEMANTIC_RANGE
The chosen English word matches the range of the Arabic word, not of the English word.
- "strong headache / strong rain" (صداع قوي / مطر قوي) → a bad headache; heavy rain
- "I knew him yesterday" (تعرّفت عليه أمس) → I met him yesterday
- "I lost the bus" (ضيّعت الحافلة) → I missed the bus
- "I'm ashamed" meaning shy or embarrassed (خجلان) → I'm embarrassed / I'm shy (ashamed is a heavier moral word)
- "I will accompany you" (سأرافقك) → I'll come with you / I'll walk you out
- "a big man" meaning old (رجل كبير) → he's older / he's pretty up there in age
- "a big number of people" (عدد كبير من الناس) → a lot of people / a huge crowd

DIRECTNESS
Bare imperatives, "I want", "you must", flat refusals and "why" questions where spoken English expects a modal, a softener, a past-tense or progressive softening, or a question frame. Arabic politeness is often carried by formulas and address terms; English spoken politeness is carried by grammar and hedging.

Also assess the opposite transfer: **over-softening**. Repeated apologies, stacked hedges,
or excessive permission-seeking can make a clear, reasonable request or refusal sound
uncertain or unnecessarily deferential. Do not flag one ordinary softener. Use the
pattern key `pragmatic.DIRECTNESS.over_softening` only when the excess is clear in context.
A concise professional refusal can be fully appropriate; do not require a cushion when a
direct answer plus a clear reason fits the relationship and stakes.
- Positive match (flag as `pragmatic.DIRECTNESS.over_softening`): "I am really so sorry to bother you, please forgive me, if you don't mind maybe I cannot take this task today" (stacked apologies + permission-seeking + hedge on a legitimate refusal).
- Negative match (do NOT flag): "I'd love to help, but I'm at capacity today" (one natural cushion + clear reason) or "I can't take that on this week—my plate's full until Friday" (direct professional refusal).
- "Give me the menu." → "Could I get the menu?" / "Can we see the menu?"
- "I want a coffee." → "I'll have a coffee." / "Can I get a coffee?"
- "Tell me your opinion." → "I'd love to hear what you think."
- "You must do X." → "You might want to do X." / "Have you thought about X?"
- "No, I can't." → "I'd love to, but I can't." / "I wish I could, but…"
- "Why you didn't call me?" → "Hey, I was trying to reach you. Everything okay?"
Context rule: bluntness between close friends is often fine. Judge against the scene's relationship and register before flagging.

RHETORICAL_INTENSITY
Emphasis built from absolutes, oaths, repetition, superlatives and stacked gratitude, which sounds hyperbolic or heavy in casual or professional English.
- "I'm 100% sure / absolutely sure / definitely!" where a tentative stance fits → "I'm pretty sure." / "I'm fairly confident."
- "Thank you very much, really thank you, I'm so grateful to you a lot" → "Thanks, I really appreciate it."
- "I swear to God" for ordinary claims → "Honestly" / "Seriously"
- "very very", "never ever", "always" for non-extreme facts
- Piled apologies ("I'm so sorry, really sorry, I apologize a thousand times") → "Sorry about that, my bad."
Do not flag hyperbole that is idiomatic in the scene (celebrations, banter between friends).

OVER_FORMAL
Written or bookish formality in spoken, casual, or peer situations.
- "I would like to inquire about…" with a friend → "Quick question about…"
- "Kindly send me the file" → "Could you send me the file?"
- "Please be informed that…" → "Just so you know…"
- "I am pleased to inform you…" → "Good news:"
- "Could you please be so kind as to…" → "Could you…?"

ADDRESS_TERM
Forms of address transferred from Arabic social norms.
- "my friend / brother / sister" (يا صديقي / أخي / أختي) to a colleague or stranger → first name, or nothing. In American English "my friend" often sounds patronizing or sarcastic.
- "Mr. Ahmed" (title + first name, أستاذ أحمد) → "Ahmed" (peers) or "Mr. [Last name]" (formal)
- "Doctor / Engineer / Teacher" as a stand-alone address outside the academic or medical setting → first name or "Hey, [name]"
- "Sir / Madam" in a casual scene → a name or nothing

DISCOURSE_MARKER_GAP
Spoken connective tissue that is missing, mismatched or too formal.
- Do not flag a learner merely for using few or no fillers. Their absence is not an error; assess whether the spoken turn is coherent and appropriately connected.
- Written connectors in speech: "moreover", "furthermore", "in addition", "in conclusion", "on the other hand" in casual talk.
- Opening every turn with "In my opinion" / "As for me" (أما أنا) / "From my point of view" → "Honestly," / "Personally," / "For me," or just say it.
- "Frankly speaking" (بصراحة) → "Honestly" / "To be honest"
- Chaining clauses with "and… and… and…" (و… و… و…) → break into short chunks with so / but / then.

SYNTAX_CALQUE
Structures that mirror Arabic syntax.
- "I want that you come" (أريد أن تأتي) → "I want you to come."
- "The movie that I watched it" (resumptive pronoun: الفيلم الذي شاهدته) → "The movie that I watched."
- "The life is hard / I love the music" (generic definite: الحياة صعبة) → "Life is hard / I love music."
- "I'm agree" (أنا موافق) → "I agree."
- "In my family there are five persons" (يوجد في أسرتي خمسة أشخاص) → "There are five of us in my family."
- "Came my friend yesterday" (VSO: جاء صديقي) → "My friend came yesterday."
- Answering a negative question with "Yes" to confirm the negative ("Don't you like it? Yes." meaning "No, I don't"; نعم confirms the negation in Arabic) → "No, I don't." / "Right, I don't." (comprehension risk, severity 2–3)
- "I have 25 years" (عندي ٢٥ سنة) → "I'm 25." (French j'ai 25 ans gives the same result, see contact languages below)
- "Why you came late?" / "What you want?" (no auxiliary in questions: لماذا جئت متأخرًا) → "Why did you come late?" / "What do you want?" (elliptical "You coming?" is normal speech; flag only full wh-questions)
- "I like very much football" (adverb after the object: أحب كرة القدم كثيرًا) → "I like football a lot." / "I really like football."
- "The weather very cold" / "He doctor" (no present-tense "to be" in Arabic nominal sentences: الجو بارد جدًا) → "The weather's really cold." / "He's a doctor." (a fast-speech slip at B2+: severity 1 unless it recurs)

COUNTABILITY
Nouns that are countable in Arabic but uncountable in English.
- informations, advices, furnitures, works (أعمال), researches, knowledges, equipments, luggages, "a news" → information, advice, furniture, work, research, knowledge, equipment, luggage, "some news / a piece of news"

TENSE_ASPECT
Arabic marks perfective/imperfective, not English present perfect.
- "I live here since 2020" (أسكن هنا منذ ٢٠٢٠) → "I've lived here since 2020."
- "I didn't see him since Monday" → "I haven't seen him since Monday."
- "I'm working here since two years" → "I've been working here for two years." (also PREP_CALQUE)

SPEECH_ACT_RITUAL
Social routines that differ between communities. Treat as a DIFFERENCE IN ROUTINE, never as a deficiency, and rate severity by scene and relationship.
- Repeated insistence on an offer (عزيمة): "Please, please, you must eat more!" → In English, offer once, maybe twice, then accept "No, thanks" as final: "Help yourself", "Let me know if you want more."
- Accepting only after a ritual first refusal, where English hearers take the first "no" literally.
- Deflecting a compliment by denying it ("No no, it's nothing, it's not good") → "Thanks, I appreciate that." / "That's kind of you to say."
- Long greeting inquiry chains (how are you, your family, your health…) in a short small-talk exchange → one "How's it going?" and a short answer.
- Personal questions that signal warmth in some settings (salary, marital status, age) asked of a new acquaintance in a professional or first-meeting scene → "So, what do you do?" / "How long have you been in the city?"
Always check scene and relationship before flagging. Never generalize about "Arabs" in any output.

### Contact languages (French)
Many learners from the Maghreb also carry French into English. Some forms fit French at least as well as Arabic: "I have 25 years" (j'ai 25 ans), "since three years" (depuis), "I'm agree" (je suis d'accord), "interested by", and false friends such as "actually" meaning currently, "eventually" meaning possibly, "assist" meaning attend, "sensible" meaning sensitive. Apply the protocol as usual: set `l1_transfer` to true ONLY if you can name a real Arabic source. If the explanation is French only, set `l1_transfer` to false, use `GEN` in `pattern_key`, and say nothing about Arabic. Never ask the learner which languages they speak and never guess their country.

### Never flag as errors
- Religious or cultural expressions the learner chooses to use (for example "inshallah", "mashallah", "alhamdulillah", "wallah" among friends). They are not mistakes. If one is used where a native listener would be left unsure whether the speaker is committing to something, you may record a pragmatic note with severity 1 and suggest a secular alternative as an OPTION, without judgment.
- Regional variants (AmE vs BrE vs others) when the scene does not require one.
- Spoken-language features: fragments, ellipsis, contractions, restarts, fillers, ending a sentence with a preposition.
- Creative, unusual but valid phrasing. If a native would find it striking and fine, leave it.
- Transcription artifacts: spelling, punctuation, capitalization, when the text came from speech-to-text.
- Accent or pronunciation. You cannot assess these from text, so say nothing about them.

### How to talk about Arabic (for the `l1_source_ar` field and the `ar_note`)
- `l1_source_ar` is the Arabic source construction in Arabic script, short (a phrase), for storage and analytics.
- Describe the English as the system that "prefers", "uses", or "tends to choose", and Arabic as the system that "says". Never "wrong", "bad", "mistake", "primitive", "literal-minded", "Arab thinking".
- One difference per note. No national, ethnic, religious, or dialect generalizations.
