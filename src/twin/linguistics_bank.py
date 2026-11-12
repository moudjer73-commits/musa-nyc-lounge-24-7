import re

# البعد 1: مصرف فك الاختزالات الصوتية والإدغام الخفي في الإنجليزية الأمريكية
EAR_DECODER_BANK = [
    {
        "angle": "casual_sharp",
        "full": "What did you end up doing about the apartment?",
        "map": "ˈWʌDʒ-ə ˈEND‿ʌp ˈDU-ɪŋ | əˈBAʊT̚ ðə əˈPɑRT-mənt̚ ↘",
        "rule": "Palatalization: /t/ + /d/ + /j/ in 'What did you' fuses into [ˈwʌdʒə]; Stop-T [t̚] on 'about'.",
        "weak": ["what", "did", "you"],
    },
    {
        "angle": "casual_sharp",
        "full": "I told him he could crash here if he needed to.",
        "map": "aɪ ˈTOʊLD‿ɪm i kəd ˈKRÆʃ ˈHɪR | ɪf‿i ˈNI-ɾəd tə ↘",
        "rule": "Pronoun /h/-deletion: unstressed 'him' & 'he' drop /h/ and link directly ([ˈtoʊld‿ɪm i], [ɪf‿i]).",
        "weak": ["him", "he", "could", "to"],
    },
    {
        "angle": "exec_clock",
        "full": "We should have held off on launching it till Monday.",
        "map": "wi ˈʃʊɾ-ə ˈHELD‿ˈɔF‿ɑn ˈLɔN-tʃɪŋ‿ɪt̚ | tɪl ˈMʌN-deɪ ↘",
        "rule": "Modal erosion: 'should have' compresses to [ˈʃʊɾə] (flap + schwa); C-V chain in 'held_off_on'.",
        "weak": ["should", "have", "on"],
    },
    {
        "angle": "exec_clock",
        "full": "We can wrap this up today, but we can't rush the numbers.",
        "map": "wi kən ˈRÆP ðɪs‿ˈʌP təˈDEɪ ↗ | bət̚ wi ˈKÆʔ ˈRʌʃ ðə ˈNʌM-bərz ↘",
        "rule": "Can vs. Can't contrast: affirmative 'can' crushes to unstressed [kən], while negative 'can't' stays stressed with full [æ] + glottal stop [ˈkæʔ].",
        "weak": ["can", "can't", "but"],
    },
    {
        "angle": "seminar_skeptic",
        "full": "Most of the data we looked at pointed in a different direction.",
        "map": "ˈMOʊS‿ə ðə ˈDEɪ-ɾə wi ˈLʊKT‿æt̚ | ˈPɔɪN-ɪd ɪn‿ə ˈDɪF-rənt dəˈREK-ʃən ↘",
        "rule": "Consonant cluster elision + nasal flap: /t/ drops in 'most of' [ˈmoʊs‿ə] and post-nasal 'pointed' [ˈpɔɪnɪd]; 'different' compresses to 2 syllables [ˈdɪfrənt].",
        "weak": ["of", "the", "at", "a"],
    },
    {
        "angle": "exec_clock",
        "full": "Could you walk me through how that plays out in practice?",
        "map": "ˈKʊDʒ-ə ˈWɔK mi ˈθRU | ˈHAʊ ðæt̚ ˈPLEɪZ‿aʊɾ‿ɪn ˈPRÆK-tɪs ↘",
        "rule": "Coalescent assimilation: 'Could you' -> [ˈkʊdʒə]; intervocalic Flap-T across 'out_in' [aʊɾ‿ɪn].",
        "weak": ["could", "you", "in"],
    },
    {
        "angle": "casual_sharp",
        "full": "I'm going to have to get back to you on that.",
        "map": "aɪm ˈGʌN-ə ˈHÆF-tə ɡɛt̚ ˈBÆK tə jə | ˈɑN ˈðÆT̚ ↘",
        "rule": "Double semi-modal reduction + devoicing: 'going to' -> [ˈɡʌnə], and 'have to' devoices /v/->/f/ before /t/ [ˈhæftə].",
        "weak": ["going", "to", "have"],
    },
    {
        "angle": "seminar_skeptic",
        "full": "It comes down to whether cities can actually fund it on the ground.",
        "map": "ɪt̚ ˈKʌMZ ˈDAʊN tə | ˈWE-ðər ˈSɪ-ɾiz kən ˈÆK-tʃə-li ˈFʌND‿ɪt̚ ɑn ðə ˈGRAʊND ↘",
        "rule": "Function-word compression: 'to' [tə], 'can' [kən], and 'actually' 3-syllable compression [ˈæktʃəli].",
        "weak": ["to", "can", "on", "the"],
    },
    {
        "angle": "casual_sharp",
        "full": "Did you ask her what she wanted to do tonight?",
        "map": "ˈDɪDʒ-ə ˈÆSK‿ər | ˈWʌT̚ ʃi ˈWɑN-ɪd tə ˈDU təˈNAɪT̚ ↘",
        "rule": "'Did you' -> [ˈdɪdʒə]; /h/-drop in 'ask her' [ˈæsk‿ər]; post-nasal /t/-drop in 'wanted' [ˈwɑnɪd].",
        "weak": ["did", "you", "her", "to"],
    },
    {
        "angle": "seminar_skeptic",
        "full": "How are you going to account for that gap in the field?",
        "map": "ˈHAʊ-ər jə ˈGʌN-ə əˈKAʊNT̚ fər ðæt̚ ˈGÆP ɪn ðə ˈFILD ↘",
        "rule": "WH-erosion: 'How are you' glides into [ˈhaʊər jə]; 'for' reduces to r-colored schwa [fər].",
        "weak": ["are", "you", "for"],
    },
]

# البعد 2: مصرف القوالب المعجمية التوليدية ونحو المشافهة الأمريكي (COCA Spoken Grammar)
SPOKEN_FRAME_BANK = [
    {
        "angle": "seminar_skeptic",
        "frame": "It comes down to [how / whether + clause]",
        "regex": r"\bcomes?\s+down\s+to\b|\bcame\s+down\s+to\b",
        "example": "It really comes down to whether residents trust the city council.",
        "why": "Spoken US analytical coreizer—replaces stiff 'The fundamental factor is...'.",
        "prompt": "In 2–3 spoken sentences, explain why some local digital platforms succeed while others get ignored—using the frame naturally.",
    },
    {
        "angle": "exec_clock",
        "frame": "The catch with [noun/-ing] is (that) [clause]",
        "regex": r"\bthe\s+(?:only\s+|main\s+)?catch\b",
        "example": "The catch with rushing the rollout is that we'll spend next month fixing bugs.",
        "why": "Spoken topic-fronting frame—flags a practical trade-off without sounding negative.",
        "prompt": "A colleague suggests automating all citizen complaints with AI tomorrow. Point out the practical risk using this frame.",
    },
    {
        "angle": "casual_sharp",
        "frame": "end up [verb-ing]",
        "regex": r"\b(?:end|ends|ended|ending)\s+up\s+\w+ing\b",
        "example": "We planned a quick coffee and ended up talking for two hours.",
        "why": "High-frequency spoken US resultative—replaces formal 'finally/eventually did'.",
        "prompt": "Tell Malik about a time your original plan for the day changed and you did something else instead.",
    },
    {
        "angle": "exec_clock",
        "frame": "hold off on [noun / verb-ing] (until/for now)",
        "regex": r"\b(?:hold|holds|held|holding)\s+off(?:\s+on)?\b",
        "example": "Let's hold off on announcing the date until the budget clears.",
        "why": "Natural workplace US phrasing—replaces stiff 'postpone/defer'.",
        "prompt": "Tell Claire why your team shouldn't publish the draft report this week.",
    },
    {
        "angle": "seminar_skeptic",
        "frame": "[Statement]—or rather, [self-repair refinement]",
        "regex": r"\bor\s+rather\b|\bi\s+mean\b",
        "example": "Participation failed—or rather, it only reached the same retired homeowners.",
        "why": "Authentic spoken C2 self-repair—Sharpens precision mid-thought without restarting.",
        "prompt": "Make a bold claim about urban governance or education, then refine it mid-sentence using 'or rather' or 'I mean'.",
    },
    {
        "angle": "exec_clock",
        "frame": "push back on [noun / that]",
        "regex": r"\b(?:push|pushes|pushed|pushing)\s+back\s+on\b",
        "example": "I had to push back on that timeline because we're short-staffed.",
        "why": "Everyday & executive US idiom for constructive disagreement.",
        "prompt": "Explain a claim, rule, or deadline you recently disagreed with and why.",
    },
    {
        "angle": "seminar_skeptic",
        "frame": "What throws people off (about X) is [clause]",
        "regex": r"\bwhat\s+throws\b|\bthrow(?:s|n|ing)?\s+\w+\s+off\b",
        "example": "What throws researchers off is assuming legal decentralization equals real power.",
        "why": "Spoken pseudo-cleft staging—highlights a counter-intuitive insight naturally.",
        "prompt": "Share a common misconception people have about your field of research or work.",
    },
    {
        "angle": "casual_sharp",
        "frame": "figure out how to [verb]",
        "regex": r"\b(?:figure|figures|figured|figuring)\s+out\b",
        "example": "I'm still trying to figure out how to balance heavy workdays with deep reading.",
        "why": "Core spoken US verb across all 3 registers—replaces 'solve/determine how'.",
        "prompt": "Tell Malik about a practical problem or workflow you're currently trying to solve.",
    },
]

# البعد 3: مختبر المناورة التداولية وإدارة الخلاف (Interactional Pragmatics)
PRAGMATIC_SCENARIOS = [
    {
        "angle": "exec_clock",
        "move": "Concede-then-Pivot Pushback",
        "situation": "Claire says: \"Let's roll out the new portal citywide on Monday—we don't have time for a pilot.\" You know launching untested will backfire.",
        "goal": "Validate her urgency first, then pivot diplomatically to propose a 1-week small pilot without sounding blunt or defensive.",
        "target_cues": "I see the urgency / Fair point, though my one worry is... / What if we...",
    },
    {
        "angle": "seminar_skeptic",
        "move": "Hedged Academic Counter-Argument",
        "situation": "Prof. Vance says: \"Honestly, digital town halls are just cosmetic PR—municipalities ignore 90% of citizen input anyway.\"",
        "goal": "Concede where that skepticism holds true, then push back with a specific condition where participatory governance DOES shift budgets.",
        "target_cues": "That's definitely true when..., though where that breaks down is... / That said...",
    },
    {
        "angle": "exec_clock",
        "move": "Polite Floor-Claiming (Interruption)",
        "situation": "In a fast meeting, two colleagues are already moving to the next agenda item before addressing a major budget gap you spotted.",
        "goal": "Jump in smoothly, apologize briefly for interrupting, and put the budget gap back on the table in 2 crisp sentences.",
        "target_cues": "Sorry to jump in here, but before we move off this— / Quick thing before we...",
    },
    {
        "angle": "casual_sharp",
        "move": "Warm Boundary-Setting without Awkwardness",
        "situation": "Malik says: \"Hey, since you're good with tech, could you build a custom bot for my cousin's shop this weekend? Shouldn't take you long!\"",
        "goal": "Decline taking it on this weekend warmly and firmly without over-apologizing or sounding cold.",
        "target_cues": "I'd love to help out, honestly, I'm just swamped right now / Tell you what...",
    },
    {
        "angle": "seminar_skeptic",
        "move": "Buying Thinking Time Under Pressure",
        "situation": "At a Q&A panel, a reviewer asks an unexpected question: \"How do you isolate digital participation effects from preexisting civic trust?\"",
        "goal": "Buy 4 seconds of natural spoken thinking time (zero dead silence, zero stiff cliches), then give a grounded 2-sentence answer.",
        "target_cues": "That's a really sharp question—let me think how to frame that... / Actually, what it comes down to is...",
    },
]

# البعد 4: مصرف التنغيم الدلالي ونبر المفارقة (Pragmatic Intonation & Contrastive Focus)
INTONATION_SUBTEXT_BANK = [
    {
        "sentence": "It looks great on paper.",
        "intent": "Signal diplomatic reservation (implying: '...but in real life it won't work') WITHOUT adding extra words.",
        "blueprint": "ɪt̚ ˈLʊKS ˈGREɪT ɑn ˈPEɪ-pər ↘↗",
        "coach_rule": "Place main pitch drop on PA-per, then add a slight final pitch hook UP (Fall-Rise ↘↗) to signal an unspoken 'but'.",
    },
    {
        "sentence": "I didn't say the policy failed.",
        "intent": "Imply that the POLICY design was fine—something ELSE (like timing or execution) failed.",
        "blueprint": "aɪ ˈDɪD-ənt seɪ ðə ˈPɑ-lə-si feɪld ↘↗",
        "coach_rule": "Spike pitch & vowel length exclusively on ˈPɑ-lə-si, de-accent 'failed', and finish with a Fall-Rise ↘↗.",
    },
    {
        "sentence": "I didn't say the policy failed.",
        "intent": "Correct someone misquoting you (implying: 'Someone else may have said that, or I merely questioned it—I didn't SAY it').",
        "blueprint": "aɪ ˈDɪD-ənt ˈSEɪ ðə ˈpɑ-lə-si feɪld ↘",
        "coach_rule": "Put the nuclear pitch peak squarely on ˈSEɪ, then compress 'the policy failed' in low, flat post-nuclear pitch.",
    },
    {
        "sentence": "We could try launching it on Friday.",
        "intent": "Sound hesitant and unconvinced about Friday (inviting the listener to suggest a safer day).",
        "blueprint": "wi ˈKʊD traɪ ˈLɔN-tʃɪŋ‿ɪt̚ ɑn ˈFRAɪ-deɪ ↘↗",
        "coach_rule": "Secondary stress on ˈKʊD + Fall-Rise ↘↗ tail on ˈFRAɪ-deɪ to project tentative hedging.",
    },
    {
        "sentence": "My main worry isn't the technology, it's the follow-through.",
        "intent": "Create sharp contrastive focus between 'technology' (dismissed) and 'follow-through' (decisive core issue).",
        "blueprint": "maɪ ˈMEɪN ˈWɝ-i ˈɪZ-ənt ðə tɛk-ˈNɑ-lə-dʒi ↗ | ɪts ðə ˈFɑ-loʊ-θru ↘",
        "coach_rule": "Continuation rise ↗ on tech-NO-lo-gy, tiny pause, then decisive low falling pitch ↘ on ˈFɑ-loʊ-θru.",
    },
]

# البعد 5: موضوعات المداخلة المسترسلة وبناء الحجة الشفهية (60-Second Extended Discourse)
MONOLOGUE_PROMPTS = {
    "casual_sharp": [
        "Speak for 45–60s: Tell Malik about a daily habit or routine you changed recently—what pushed you to change it, what was hardest at first, and did it stick?",
        "Speak for 45–60s: Do voice notes and group chats bring people closer, or do they replace real conversation? Walk through both sides and land on your take.",
    ],
    "exec_clock": [
        "Speak for 45–60s: Pitch one low-cost, practical reform that would make municipal services faster for everyday citizens—and preempt the #1 budget objection.",
        "Speak for 45–60s: When a project hits an unexpected snag two days before deadline, should a team ship a stripped-down version or delay launch? Defend your call.",
    ],
    "seminar_skeptic": [
        "Speak for 45–60s: Why do participatory local governance models thrive in cases like Kerala or Barcelona yet stall when copied elsewhere? Lay out the core causal mechanism.",
        "Speak for 45–60s: Critics claim smart-city platforms deepen inequality by favoring tech-savvy citizens. Concede what's valid in that critique, then argue how local policy can fix it.",
    ],
}

CONTRACTION_EXPANSIONS = {
    "whaddaya": "what do you",
    "waddaya": "what do you",
    "whadja": "what did you",
    "didja": "did you",
    "couldja": "could you",
    "wouldja": "would you",
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
    "hafta": "have to",
    "shoulda": "should have",
    "coulda": "could have",
    "woulda": "would have",
    "musta": "must have",
    "outta": "out of",
    "kinda": "kind of",
    "sorta": "sort of",
    "lemme": "let me",
    "gimme": "give me",
    "tell'em": "tell them",
    "told'em": "told them",
    "i'm": "i am",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "it's": "it is",
    "that's": "that is",
    "let's": "let us",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "won't": "will not",
    "wouldn't": "would not",
    "shouldn't": "should not",
    "couldn't": "could not",
}


def normalize_ear_tokens(text: str) -> list[str]:
    low = text.lower().replace("’", "'")
    for k, v in CONTRACTION_EXPANSIONS.items():
        low = re.sub(r"\b" + re.escape(k) + r"\b", v, low)
    # Keep "can't" distinct from "can"!
    low = re.sub(r"\bcannot\b", "can't", low)
    return re.findall(r"can't|[a-z]+", low)


def derive_frame_pattern(phrase: str) -> tuple[str, str]:
    """Converts a saved phrase into a generative spoken frame + flexible regex."""
    p = phrase.strip()
    low = p.lower()
    for item in SPOKEN_FRAME_BANK:
        if re.search(item["regex"], low):
            return item["frame"], item["regex"]
    toks = re.findall(r"[a-z']+", low)
    if len(toks) >= 2:
        stem0 = re.sub(r"(?:ed|ing|s)$", "", toks[0])
        if len(stem0) >= 3:
            pat = r"\b" + re.escape(stem0) + r"\w*\s+" + re.escape(toks[1])
            return f"{p} + [...]", pat
    return p, re.escape(low)
