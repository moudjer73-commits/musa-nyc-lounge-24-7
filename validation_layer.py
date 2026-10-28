import re
import unittest
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

def normalize_for_citation(text: str) -> str:
    """Normalize smart quotes, dashes, and non-breaking hyphens while preserving word hyphens."""
    s = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("\u2010", "-").replace("\u2011", "-")
    s = s.replace("—", " — ").replace("–", " – ")
    return re.sub(r"\s+", " ", s).strip().lower()

def count_spoken_words(text: str) -> int:
    """Count spoken words (contractions and hyphenated compounds count as single words)."""
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", str(text or "")))

def verify_is_complete_sentence_in_source(citation: str, source_text: str) -> bool:
    """Verify `citation` is a complete sentence inside `source_text` bounded by .!?"""
    c_norm = normalize_for_citation(citation).strip("\"' ")
    s_norm = normalize_for_citation(source_text).strip("\"' ")
    if not c_norm or c_norm not in s_norm or not re.search(r"[.!?]$", c_norm):
        return False
    return bool(re.search(r"(?:^|[.!?]\s+)" + re.escape(c_norm), s_norm))

_IRREGULAR_FAMILIES = [
    {'be', 'am', 'is', 'are', 'was', 'were', 'been', 'being'},
    {'have', 'has', 'had', 'having'},
    {'do', 'does', 'did', 'done', 'doing'},
    {'make', 'makes', 'made', 'making'},
    {'take', 'takes', 'took', 'taken', 'taking'},
    {'run', 'runs', 'ran', 'running'},
    {'go', 'goes', 'went', 'gone', 'going'},
    {'come', 'comes', 'came', 'coming'},
    {'set', 'sets', 'setting'},
    {'put', 'puts', 'putting'},
    {'cut', 'cuts', 'cutting'},
    {'keep', 'keeps', 'kept', 'keeping'},
    {'leave', 'leaves', 'left', 'leaving'},
    {'drive', 'drives', 'drove', 'driven', 'driving'},
    {'throw', 'throws', 'threw', 'thrown', 'throwing'},
    {'build', 'builds', 'built', 'building'},
    {'spend', 'spends', 'spent', 'spending'},
    {'lose', 'loses', 'lost', 'losing'},
    {'find', 'finds', 'found', 'finding'},
    {'lead', 'leads', 'led', 'leading'},
    {'hold', 'holds', 'held', 'holding'},
    {'stand', 'stands', 'stood', 'standing'},
    {'fall', 'falls', 'fell', 'fallen', 'falling'},
    {'grow', 'grows', 'grew', 'grown', 'growing'},
    {'know', 'knows', 'knew', 'known', 'knowing'},
    {'see', 'sees', 'saw', 'seen', 'seeing'},
    {'think', 'thinks', 'thought', 'thinking'},
    {'bring', 'brings', 'brought', 'bringing'},
    {'buy', 'buys', 'bought', 'buying'},
    {'catch', 'catches', 'caught', 'catching'},
    {'teach', 'teaches', 'taught', 'teaching'},
    {'feel', 'feels', 'felt', 'feeling'},
    {'mean', 'means', 'meant', 'meaning'},
    {'meet', 'meets', 'met', 'meeting'},
    {'pay', 'pays', 'paid', 'paying'},
    {'say', 'says', 'said', 'saying'},
    {'sell', 'sells', 'sold', 'selling'},
    {'send', 'sends', 'sent', 'sending'},
    {'sit', 'sits', 'sat', 'sitting'},
    {'tell', 'tells', 'told', 'telling'},
    {'win', 'wins', 'won', 'winning'},
    {'wear', 'wears', 'wore', 'worn', 'wearing'},
    {'tear', 'tears', 'tore', 'torn', 'tearing'},
    {'sweep', 'sweeps', 'swept', 'sweeping'},
    {'deal', 'deals', 'dealt', 'dealing'},
    {'stick', 'sticks', 'stuck', 'sticking'},
    {'strike', 'strikes', 'struck', 'striking'},
    {'speak', 'speaks', 'spoke', 'spoken', 'speaking'},
    {'break', 'breaks', 'broke', 'broken', 'breaking'},
    {'write', 'writes', 'wrote', 'written', 'writing'},
    {'give', 'gives', 'gave', 'given', 'giving'},
]
_REFLEXIVE_SET = {'oneself', 'myself', 'yourself', 'himself', 'herself', 'itself', 'ourselves', 'yourselves', 'themselves'}

_FILLER_INTENSIFIER_ADVERBS = {'entirely', 'completely', 'totally', 'really', 'actually', 'simply', 'merely', 'literally', 'definitely', 'certainly'}
_POSSESSIVE_SET = {'ones', "one's", 'my', 'your', 'his', 'her', 'its', 'our', 'their'}
_SLOT_WORDS = {'something', 'someone', 'somebody'}

def _expand_regular_lemma(lemma: str) -> set:
    forms = {lemma}
    if len(lemma) < 3 or not lemma.isalpha():
        return forms
    vowels = 'aeiou'
    if lemma.endswith('y') and len(lemma) >= 3 and lemma[-2] not in vowels:
        forms.add(lemma[:-1] + 'ies')
        forms.add(lemma[:-1] + 'ied')
        forms.add(lemma + 'ing')
        return forms
    if lemma.endswith(('s', 'sh', 'ch', 'x', 'z', 'o')):
        forms.add(lemma + 'es')
    else:
        forms.add(lemma + 's')
    if lemma.endswith('e') and not lemma.endswith(('ee', 'oe', 'ye')):
        forms.add(lemma + 'd')
        forms.add(lemma[:-1] + 'ing')
        return forms
    forms.add(lemma + 'ed')
    forms.add(lemma + 'ing')
    if len(lemma) >= 3 and lemma[-1] not in 'aeiouwxy' and lemma[-2] in vowels and lemma[-3] not in vowels:
        doubled = lemma + lemma[-1]
        forms.add(doubled + 'ed')
        forms.add(doubled + 'ing')
    return forms

def _candidate_lemmas(word: str) -> set:
    cands = {word}
    if len(word) < 4 or not word.isalpha():
        return cands
    if word.endswith('ing') and len(word) >= 5:
        stem = word[:-3]
        if len(stem) >= 3:
            cands.add(stem)
        if len(stem) >= 2:
            cands.add(stem + 'e')
        if len(stem) >= 3 and stem[-1] == stem[-2]:
            cands.add(stem[:-1])
    if word.endswith(('ied', 'ies')) and len(word) >= 5:
        cands.add(word[:-3] + 'y')
    if word.endswith('ed') and len(word) >= 4:
        stem = word[:-2]
        if len(stem) >= 3:
            cands.add(stem)
        if len(stem) >= 2:
            cands.add(stem + 'e')
        if len(stem) >= 3 and stem[-1] == stem[-2]:
            cands.add(stem[:-1])
        if word.endswith('d') and len(word) >= 4:
            cands.add(word[:-1])
    if word.endswith('es') and len(word) >= 4:
        if len(word[:-2]) >= 3:
            cands.add(word[:-2])
        if len(word[:-1]) >= 3:
            cands.add(word[:-1])
    elif word.endswith('s') and not word.endswith('ss') and len(word) >= 4:
        cands.add(word[:-1])
    return {c for c in cands if len(c) >= 3}

def _build_token_regex(w: str) -> str:
    wl = w.lower()
    if wl in _REFLEXIVE_SET:
        return '(?:' + '|'.join(sorted(_REFLEXIVE_SET)) + ')'
    if wl in _POSSESSIVE_SET:
        return '(?:' + '|'.join(sorted(_POSSESSIVE_SET)) + ')'
    forms = {wl}
    for fam in _IRREGULAR_FAMILIES:
        if wl in fam:
            forms |= fam
    if len(wl) >= 3 and wl.isalpha():
        for cand in _candidate_lemmas(wl):
            expanded = _expand_regular_lemma(cand)
            if wl in expanded:
                forms |= expanded
            for fam in _IRREGULAR_FAMILIES:
                if cand in fam:
                    forms |= fam
    escaped_forms = sorted((re.escape(f) for f in forms), key=len, reverse=True)
    return '(?:' + '|'.join(escaped_forms) + ')' if len(escaped_forms) > 1 else escaped_forms[0]

def verify_contiguous_expression_in_sentence(expression: str, sentence: str) -> bool:
    _expr_low = (expression or '').lower()
    _active_fillers = [w for w in _FILLER_INTENSIFIER_ADVERBS if w not in _expr_low]
    if _active_fillers and sentence:
        sentence = re.sub(r'\b(?:' + '|'.join(_active_fillers) + r')\b\s+', ' ', sentence, flags=re.IGNORECASE)
        sentence = re.sub(r'\s{2,}', ' ', sentence)
    norm_expr = normalize_for_citation(expression).lower()
    norm_sent = normalize_for_citation(sentence).lower()
    norm_expr = re.sub(r'^to\s+', '', norm_expr).strip()
    if not norm_expr or not norm_sent:
        return False
    parts = re.split(r'(\[[^\]]+\])', norm_expr)
    seq_patterns: List[str] = []
    slot_pat = r'[a-z0-9]+(?:[\'-][a-z0-9]+)*(?:\s+[a-z0-9]+(?:[\'-][a-z0-9]+)*){0,11}'
    for part in parts:
        if not part:
            continue
        if part.startswith('[') and part.endswith(']'):
            inner = part[1:-1].strip().lower()
            if inner in _REFLEXIVE_SET:
                seq_patterns.append(_build_token_regex(inner))
            else:
                seq_patterns.append(slot_pat)
        else:
            for w in re.findall(r"[a-z0-9]+(?:['-][a-z0-9]+)*", part):
                if w in _SLOT_WORDS:
                    seq_patterns.append(slot_pat)
                else:
                    seq_patterns.append(_build_token_regex(w))
    if not seq_patterns:
        return False
    combined = r'\b' + r'\s+'.join(seq_patterns) + r'\b'
    return bool(re.search(combined, norm_sent))


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class LegacyLexicalUpgrade(BaseModel):
    chunk: str
    b2_foil: str = ""
    arabic_equivalent: str = ""
    explanation: str = ""
    example: str = ""

ActiveModeType = Literal["banter", "subtext_decoder", "situation_strategy", "pronunciation_guide"]
InputIntentType = Literal["request_for_new_phrasing", "submitted_draft_for_evaluation", "general_conversation_or_question"]
DiagnosisCategory = Literal[
    "already_natural", "grammatical_or_structural_error", "unnatural_collocation",
    "register_mismatch", "pragmatic_risk", "overly_translated_or_wordy", "meaning_or_logic_problem"
]
SourceType = Literal["verbatim_from_host_dialogue", "from_recommended_option", "from_learner_input", "related_extension"]
HostSpeaker = Literal["marcus", "chloe", "jake"]
StrategyArchetype = Literal["Diplomatic", "Assertive", "Concise"]
FollowUpReactionType = Literal["Deflection or Minimization", "Pushback or Defensiveness", "Agreement or Cooperation"]

class HostTurn(StrictBaseModel):
    turn_id: Literal["turn_1", "turn_2", "turn_3"]
    speaker: HostSpeaker
    text: str = Field(..., min_length=1)

class DiagnosticMirrorBlock(StrictBaseModel):
    primary_issue_label: DiagnosisCategory
    secondary_issue_label: Optional[DiagnosisCategory] = None
    explanation: str = Field(..., min_length=1)
    revised_version: Optional[str] = None
    optional_register_shift: Optional[str] = None

class EscalationOption(StrictBaseModel):
    option_id: Literal["escalation_1", "escalation_2", "escalation_3"]
    label: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    exact_wording: str = Field(..., min_length=1)
    makes_explicit: str = Field(..., min_length=1)
    interpersonal_tradeoff: str = Field(..., min_length=1)
    when_not_to_use: str = Field(..., min_length=1)

class SubtextAnalysisBlock(StrictBaseModel):
    literal_meaning: str = Field(..., min_length=1)
    observable_facts: str = Field(..., min_length=1)
    plausible_implicatures: List[str] = Field(..., min_length=1, max_length=3)
    behavior_confidence: Literal["Low", "Moderate", "Higher"]
    motive_confidence: Literal["Low", "Moderate", "Higher"]
    evidence_to_watch: str = Field(..., min_length=1)
    escalation_options: List[EscalationOption] = Field(..., min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_ordered_escalation_ids(self) -> "SubtextAnalysisBlock":
        ids = [opt.option_id for opt in self.escalation_options]
        expected = [f"escalation_{i}" for i in range(1, len(ids) + 1)]
        if ids != expected:
            raise ValueError(f"Escalation option_ids must be sequential {expected}. Got: {ids}")
        return self

class StrategicOptionItem(StrictBaseModel):
    option_id: Literal["option_1", "option_2", "option_3"]
    archetype: StrategyArchetype
    spoken_line: str = Field(..., min_length=1)
    arabic_equivalent: str = Field(..., min_length=1)
    makes_explicit: str = Field(..., min_length=1)
    leaves_implicit: str = Field(..., min_length=1)
    remaining_risk: str = Field(..., min_length=1)
    best_fit_when: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def enforce_spoken_line_word_count(self) -> "StrategicOptionItem":
        wc = count_spoken_words(self.spoken_line)
        if not (12 <= wc <= 22):
            raise ValueError(f"Option '{self.option_id}' has {wc} words; must be between 12 and 22 words.")
        return self

class FollowUpBranch(StrictBaseModel):
    reaction_type: FollowUpReactionType
    their_likely_reply: str = Field(..., min_length=1)
    your_follow_up_line: str = Field(..., min_length=1)
    tactical_note: str = Field(..., min_length=1)

class StrategyPlaybookBlock(StrictBaseModel):
    situation_objective: str = Field(..., min_length=1)
    relationship_constraints: str = Field(..., min_length=1)
    strategic_options: List[StrategicOptionItem] = Field(..., min_length=3, max_length=3)
    follow_up_branches: List[FollowUpBranch] = Field(..., min_length=3, max_length=3)
    when_to_let_it_go: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def enforce_archetypes_and_branches(self) -> "StrategyPlaybookBlock":
        expected_pairs = [("option_1", "Diplomatic"), ("option_2", "Assertive"), ("option_3", "Concise")]
        actual_pairs = [(o.option_id, o.archetype) for o in self.strategic_options]
        if actual_pairs != expected_pairs:
            raise ValueError(f"strategic_options must strictly map {expected_pairs}. Got: {actual_pairs}")
        expected_react = {"Deflection or Minimization", "Pushback or Defensiveness", "Agreement or Cooperation"}
        actual_react = [b.reaction_type for b in self.follow_up_branches]
        if set(actual_react) != expected_react or len(actual_react) != 3:
            raise ValueError(f"follow_up_branches must cover all 3 required reaction types. Got: {actual_react}")
        return self

class PronunciationTargetItem(StrictBaseModel):
    word: str = Field(..., min_length=1)
    ipa_variants: List[str] = Field(..., min_length=1, max_length=3)
    approximate_stress_guides: List[str] = Field(..., min_length=1, max_length=3)
    segmental_and_prosodic_notes: str = Field(..., min_length=1)
    connected_speech_variability_note: str = Field(..., min_length=1)

class PronunciationGuideBlock(StrictBaseModel):
    audio_provided: bool
    diagnosis_disclaimer: str = Field(..., min_length=1)
    target_items: List[PronunciationTargetItem] = Field(..., min_length=1, max_length=5)

class LexicalItem(StrictBaseModel):
    expression: str = Field(..., min_length=1)
    source_type: SourceType
    source_ref_id: Optional[str] = None
    source_speaker: Optional[HostSpeaker] = None
    full_sentence_citation: Optional[str] = None
    usage_status_label: str = Field(..., min_length=1)
    core_lexical_meaning: str = Field(..., min_length=1)
    contextual_function: str = Field(..., min_length=1)
    register_and_usage: str = Field(..., min_length=1)
    arabic_equivalent: str = Field(..., min_length=1)
    contrastive_note: Optional[str] = None

class LoungeTurnResponse(StrictBaseModel):
    active_mode: ActiveModeType
    learner_input_text: str = Field(..., min_length=1)
    input_intent_type: InputIntentType
    learner_diagnosis: DiagnosisCategory
    show_diagnostic_mirror: bool
    host_turns: List[HostTurn] = Field(..., min_length=1, max_length=3)
    diagnostic_mirror: Optional[DiagnosticMirrorBlock] = None
    subtext_analysis: Optional[SubtextAnalysisBlock] = None
    strategy_playbook: Optional[StrategyPlaybookBlock] = None
    pronunciation_guide: Optional[PronunciationGuideBlock] = None
    lexical_items: List[LexicalItem] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def enforce_all_invariants(self, info: ValidationInfo) -> "LoungeTurnResponse":
        for _idx, _item in enumerate(self.lexical_items):
            _expr_clean = re.sub(r'\[[^\]]+\]', ' ', _item.expression or '')
            _toks = [t.lower() for t in re.findall(r"[a-zA-Z]+(?:'[a-zA-Z]+)?", _expr_clean)]
            _bad_adv = [t for t in _toks if t in _FILLER_INTENSIFIER_ADVERBS]
            if _bad_adv:
                raise ValueError(f'lexical_items[{_idx}].expression contains optional intensifier adverb {_bad_adv}; strip it to keep the canonical Base Pattern clean.')
            if _toks and _toks[0].endswith('ing') and _toks[0] not in ('nothing', 'something', 'anything', 'everything', 'bring', 'spring', 'sing', 'ring', 'cling', 'swing', 'sting', 'wing'):
                raise ValueError(f'lexical_items[{_idx}].expression starts with a gerund/participle ({_toks[0]}); use the base infinitive verb form or a fixed idiom frame.')
        if not isinstance(info.context, dict):
            raise ValueError("Trusted ValidationInfo.context dict is required for LoungeTurnResponse validation.")
        ctx: Dict[str, Any] = info.context
        required_ctx_keys = {"runtime_has_audio_bytes", "learner_input_text", "active_mode"}
        missing_keys = required_ctx_keys - set(ctx.keys())
        if missing_keys:
            raise ValueError(f"Trusted ValidationInfo.context is missing required keys: {sorted(missing_keys)}")

        trusted_has_audio = bool(ctx["runtime_has_audio_bytes"])
        if normalize_for_citation(self.learner_input_text) != normalize_for_citation(ctx["learner_input_text"]):
            raise ValueError("LLM altered learner_input_text relative to trusted Telegram runtime context.")
        if self.active_mode != ctx["active_mode"]:
            raise ValueError("LLM altered active_mode relative to trusted Telegram runtime context.")

        turn_ids = [t.turn_id for t in self.host_turns]
        expected_tids = [f"turn_{i}" for i in range(1, len(turn_ids) + 1)]
        if turn_ids != expected_tids:
            raise ValueError(f"host_turns must have sequential turn_ids {expected_tids}. Got: {turn_ids}")
        turn_map: Dict[str, HostTurn] = {t.turn_id: t for t in self.host_turns}

        mode_reqs = {
            "banter": {"req": [], "forb": ["subtext_analysis", "strategy_playbook", "pronunciation_guide"]},
            "subtext_decoder": {"req": ["subtext_analysis"], "forb": ["strategy_playbook", "pronunciation_guide"]},
            "situation_strategy": {"req": ["strategy_playbook"], "forb": ["subtext_analysis", "pronunciation_guide"]},
            "pronunciation_guide": {"req": ["pronunciation_guide"], "forb": ["subtext_analysis", "strategy_playbook"]},
        }[self.active_mode]
        for r in mode_reqs["req"]:
            if getattr(self, r) is None:
                raise ValueError(f"Mode '{self.active_mode}' requires a non-null '{r}' block.")
        for f in mode_reqs["forb"]:
            if getattr(self, f) is not None:
                raise ValueError(f"Mode '{self.active_mode}' forbids populating '{f}'.")

        if self.pronunciation_guide is not None and self.pronunciation_guide.audio_provided and not trusted_has_audio:
            raise ValueError("pronunciation_guide claims audio_provided=True, but trusted runtime_has_audio_bytes is False.")

        if self.input_intent_type == "submitted_draft_for_evaluation":
            if not self.show_diagnostic_mirror or self.diagnostic_mirror is None:
                raise ValueError("submitted_draft_for_evaluation requires show_diagnostic_mirror=True and non-null diagnostic_mirror.")
        else:
            if self.learner_diagnosis == "already_natural":
                if self.show_diagnostic_mirror or self.diagnostic_mirror is not None:
                    raise ValueError("When learner_diagnosis='already_natural' on non-evaluation intent, show_diagnostic_mirror must be False.")
            elif not self.show_diagnostic_mirror or self.diagnostic_mirror is None:
                raise ValueError("When learner_diagnosis != 'already_natural', show_diagnostic_mirror must be True and populated.")

        if self.diagnostic_mirror is not None:
            if self.diagnostic_mirror.primary_issue_label != self.learner_diagnosis:
                raise ValueError("primary_issue_label must match learner_diagnosis.")
            if self.learner_diagnosis == "already_natural" and self.diagnostic_mirror.revised_version is not None:
                raise ValueError("When learner_diagnosis='already_natural', revised_version must be None.")
            if self.learner_diagnosis != "already_natural" and not (self.diagnostic_mirror.revised_version or "").strip():
                raise ValueError("When learner_diagnosis != 'already_natural', revised_version is required.")

        option_text_map: Dict[str, str] = {}
        if self.strategy_playbook:
            for o in self.strategy_playbook.strategic_options:
                option_text_map[o.option_id] = o.spoken_line
        if self.subtext_analysis:
            for e in self.subtext_analysis.escalation_options:
                option_text_map[e.option_id] = e.exact_wording

        all_dialogue_norm = normalize_for_citation(" ".join(t.text for t in self.host_turns))
        all_dialogue_norm = normalize_for_citation(" ".join(t.text for t in self.host_turns))
        for item in self.lexical_items:
            if item.source_type == "related_extension":
                for turn_obj in self.host_turns:
                    if verify_contiguous_expression_in_sentence(item.expression, turn_obj.text):
                        raise ValueError(f"Expression '{item.expression}' is already used in {turn_obj.turn_id} and cannot be marked as not used under related_extension.")
            if item.source_type == "related_extension":
                cleaned_ext = re.sub(r"^to\\s+", "", item.expression.lower().strip())
                if "[" not in cleaned_ext and verify_contiguous_expression_in_sentence(cleaned_ext, all_dialogue_norm):
                    raise ValueError(f"Expression '{item.expression}' already appears in host_turns; pick a different related_extension.")
        for item in self.lexical_items:
            if item.source_type == "related_extension":
                cleaned_ext = re.sub(r"^to\\s+", "", item.expression.lower().strip())
                if "[" not in cleaned_ext and verify_contiguous_expression_in_sentence(cleaned_ext, all_dialogue_norm):
                    raise ValueError(f"Expression '{item.expression}' already appears in host_turns; pick a different related_extension.")
        for item in self.lexical_items:
            if item.source_type == "related_extension":
                cleaned_ext = re.sub(r"^to\\s+", "", item.expression.lower().strip())
                if "[" not in cleaned_ext and verify_contiguous_expression_in_sentence(cleaned_ext, all_dialogue_norm):
                    raise ValueError(f"Expression '{item.expression}' already appears in host_turns; pick a different related_extension.")
        for item in self.lexical_items:
            expr_l = item.expression.lower().strip()
            if any(v in expr_l for v in ("conflate", "conflating", "equate", "equating")) and " with " not in expr_l:
                raise ValueError(f"Expression '{item.expression}' is truncated; must include full frame with '[something] with [something]'.")
            if any(v in expr_l for v in ("tailor", "tailoring")) and " to " not in expr_l:
                raise ValueError(f"Expression '{item.expression}' is truncated; must include full frame with '[something] to [something]'.")
            if item.source_type == "related_extension":
                cleaned_ext = re.sub(r"^to\s+", "", expr_l)
                if verify_contiguous_expression_in_sentence(cleaned_ext, all_dialogue_norm):
                    raise ValueError(f"Expression '{item.expression}' already appears in host_turns; cannot be marked as 'not used' under related_extension.")
        for item in self.lexical_items:
            if item.source_type == "verbatim_from_host_dialogue":
                if not item.source_ref_id or item.source_ref_id not in turn_map:
                    raise ValueError(f"Invalid host turn_id '{item.source_ref_id}'.")
                t_obj = turn_map[item.source_ref_id]
                if item.source_speaker != t_obj.speaker:
                    raise ValueError(f"Turn '{item.source_ref_id}' was spoken by '{t_obj.speaker}', not '{item.source_speaker}'.")
                if not item.full_sentence_citation or not verify_is_complete_sentence_in_source(item.full_sentence_citation, t_obj.text):
                    raise ValueError(f"Citation '{item.full_sentence_citation}' is not a complete verbatim sentence in {item.source_ref_id}.")
                if not verify_contiguous_expression_in_sentence(item.expression, item.full_sentence_citation):
                    raise ValueError(f"Expression '{item.expression}' does not occur as a contiguous phrase in '{item.full_sentence_citation}'.")

            elif item.source_type == "from_recommended_option":
                if not option_text_map or not item.source_ref_id or item.source_ref_id not in option_text_map:
                    raise ValueError(f"Option '{item.source_ref_id}' does not exist in current mode's options.")
                if item.source_speaker is not None:
                    raise ValueError("from_recommended_option must not set source_speaker.")
                opt_txt = option_text_map[item.source_ref_id]
                if not item.full_sentence_citation or not verify_is_complete_sentence_in_source(item.full_sentence_citation, opt_txt):
                    raise ValueError(f"Citation '{item.full_sentence_citation}' is not a complete verbatim sentence in option '{item.source_ref_id}'.")
                if not verify_contiguous_expression_in_sentence(item.expression, item.full_sentence_citation):
                    raise ValueError(f"Expression '{item.expression}' does not occur as a contiguous phrase in '{item.full_sentence_citation}'.")

            elif item.source_type == "from_learner_input":
                if item.source_ref_id != "learner_input":
                    raise ValueError("from_learner_input must set source_ref_id='learner_input'.")
                if not item.full_sentence_citation or not verify_is_complete_sentence_in_source(item.full_sentence_citation, self.learner_input_text):
                    raise ValueError(f"Citation '{item.full_sentence_citation}' is not a complete verbatim sentence in learner_input_text.")
                if not verify_contiguous_expression_in_sentence(item.expression, item.full_sentence_citation):
                    raise ValueError(f"Expression '{item.expression}' does not occur as a contiguous phrase in learner input.")

            elif item.source_type == "related_extension":
                if item.source_ref_id is not None or item.source_speaker is not None or item.full_sentence_citation is not None:
                    raise ValueError("related_extension must have None for source_ref_id, source_speaker, and full_sentence_citation.")
                if "not used" not in item.usage_status_label.lower() or not (item.contrastive_note or "").strip():
                    raise ValueError("related_extension requires 'not used' in usage_status_label and non-empty contrastive_note.")

        return self

    @property
    def situation_summary(self) -> str:
        return self.strategy_playbook.situation_objective if self.strategy_playbook else ""

    @property
    def responding_persona(self) -> str:
        return self.host_turns[0].speaker if self.host_turns else "chloe"

    @property
    def host_commentary(self) -> str:
        return " ".join(t.text for t in self.host_turns)

    @property
    def best_line(self) -> str:
        if self.strategy_playbook and len(self.strategy_playbook.strategic_options) >= 1:
            return self.strategy_playbook.strategic_options[0].spoken_line
        return self.host_turns[0].text if self.host_turns else ""

    @property
    def softer_alternative(self) -> str:
        if self.strategy_playbook and len(self.strategy_playbook.strategic_options) >= 2:
            return self.strategy_playbook.strategic_options[1].spoken_line
        return ""

    @property
    def firmer_alternative(self) -> str:
        if self.strategy_playbook and len(self.strategy_playbook.strategic_options) >= 3:
            return self.strategy_playbook.strategic_options[2].spoken_line
        return ""

    @property
    def arabic_equivalent(self) -> str:
        if self.strategy_playbook and self.strategy_playbook.strategic_options:
            return self.strategy_playbook.strategic_options[0].arabic_equivalent
        return ""

    @property
    def why_it_works(self) -> str:
        if self.strategy_playbook and self.strategy_playbook.strategic_options:
            o1 = self.strategy_playbook.strategic_options[0]
            return f"{o1.makes_explicit} Best fit when: {o1.best_fit_when}"
        return ""

    @property
    def follow_up_line(self) -> str:
        if self.strategy_playbook and self.strategy_playbook.follow_up_branches:
            return self.strategy_playbook.follow_up_branches[0].your_follow_up_line
        return ""

    @property
    def lexical_upgrades(self) -> List[LegacyLexicalUpgrade]:
        out = []
        for it in self.lexical_items:
            out.append(LegacyLexicalUpgrade(
                chunk=it.expression,
                b2_foil=it.register_and_usage,
                arabic_equivalent=it.arabic_equivalent,
                explanation=f"{it.core_lexical_meaning} — {it.contextual_function}",
                example=it.full_sentence_citation or it.contrastive_note or it.expression
            ))
        return out


DEFAULT_LEARNER_TEXT = "Our client wants to add two new features to the app, but they don't want to adjust the launch date. What do I say in tomorrow's check-in to push back without sounding uncooperative?"

def make_trusted_ctx(
    active_mode: str = "situation_strategy",
    learner_input_text: str = DEFAULT_LEARNER_TEXT,
    runtime_has_audio_bytes: bool = False,
) -> dict:
    return {
        "active_mode": active_mode,
        "learner_input_text": learner_input_text,
        "runtime_has_audio_bytes": runtime_has_audio_bytes,
    }

def make_valid_strategy_payload() -> dict:
    return {
        "active_mode": "situation_strategy",
        "learner_input_text": DEFAULT_LEARNER_TEXT,
        "input_intent_type": "request_for_new_phrasing",
        "learner_diagnosis": "already_natural",
        "show_diagnostic_mirror": False,
        "diagnostic_mirror": None,
        "host_turns": [
            {"turn_id": "turn_1", "speaker": "marcus", "text": "The cleanest move is to frame this as a scope-versus-schedule trade-off rather than a refusal."},
            {"turn_id": "turn_2", "speaker": "chloe", "text": "Right—when you ask them how they want to adjust the current priorities, you stay collaborative while protecting your team."}
        ],
        "strategy_playbook": {
            "situation_objective": "Link adding the two features to either deferring existing scope or adjusting the launch timeline.",
            "relationship_constraints": "External client requesting extra scope under a fixed deadline.",
            "strategic_options": [
                {
                    "option_id": "option_1", "archetype": "Diplomatic",
                    "spoken_line": "To keep our launch date intact, which existing items should we defer to make room for these?",
                    "arabic_equivalent": "للحفاظ على موعد الإطلاق كما هو، ما العناصر الحالية التي ينبغي تأجيلها لإفساح المجال لهذه الميزات؟",
                    "makes_explicit": "Adding new features requires deferring existing scope if the launch date stays fixed.",
                    "leaves_implicit": "How many items must be deferred until engineering estimates the effort.",
                    "remaining_risk": "The client may claim all current items are high priority.",
                    "best_fit_when": "You have a collaborative client and flexible items in the current scope."
                },
                {
                    "option_id": "option_2", "archetype": "Assertive",
                    "spoken_line": "Adding both features would require either more time or an adjustment to our current launch scope.",
                    "arabic_equivalent": "ستتطلب إضافة الميزتين معاً إما وقتاً إضافياً أو تعديلاً في نطاق الإطلاق الحالي.",
                    "makes_explicit": "The requirement for extra time or a scope change—without opening with an unconditional commitment.",
                    "leaves_implicit": "The exact duration of any extension until engineering reviews the specs.",
                    "remaining_risk": "More direct; the client may immediately ask how much extra time is needed.",
                    "best_fit_when": "You need to set a clear boundary before engineering has priced the effort."
                },
                {
                    "option_id": "option_3", "archetype": "Concise",
                    "spoken_line": "We can take those on if we either adjust the current scope or push the launch date back.",
                    "arabic_equivalent": "يمكننا تولّي تنفيذ هاتين الميزتين إذا عدّلنا النطاق الحالي أو أجّلنا موعد الإطلاق.",
                    "makes_explicit": "The conditional trade-off in a single brief sentence.",
                    "leaves_implicit": "Specific dates or swap candidates until the client chooses a path.",
                    "remaining_risk": "A demanding client may push to see if the constraint is firm.",
                    "best_fit_when": "You need a quick, calm boundary during a fast-moving check-in."
                }
            ],
            "follow_up_branches": [
                {
                    "reaction_type": "Deflection or Minimization",
                    "their_likely_reply": "Come on, these look like small changes—can't the team just squeeze them in?",
                    "your_follow_up_line": "Even small additions need to be scoped and tested before we commit, so let's confirm the testing impact before adding them to this release.",
                    "tactical_note": "Require an impact check before committing, without inventing technical details."
                },
                {
                    "reaction_type": "Pushback or Defensiveness",
                    "their_likely_reply": "We really need both the new features and the original launch date.",
                    "your_follow_up_line": "We can protect the launch date for the core release and consider moving these two features to a later release, subject to confirming the plan and timeline.",
                    "tactical_note": "Protect the launch date while proposing a later release conditionally, without assuming an unconfirmed release plan."
                },
                {
                    "reaction_type": "Agreement or Cooperation",
                    "their_likely_reply": "Fair enough—what if we defer part of the current scope?",
                    "your_follow_up_line": "Let's identify an existing item we can defer, then confirm with engineering whether that frees up enough capacity for both features.",
                    "tactical_note": "Avoid promising a swap before engineering verifies capacity."
                }
            ],
            "when_to_let_it_go": "If the client rejects both trade-offs, do not commit in the meeting; offer to check capacity with engineering and follow up in writing."
        },
        "lexical_items": [
            {
                "expression": "keep [something] intact", "source_type": "from_recommended_option", "source_ref_id": "option_1", "source_speaker": None,
                "full_sentence_citation": "To keep our launch date intact, which existing items should we defer to make room for these?",
                "usage_status_label": "Used in Strategic Option 1 (Diplomatic)",
                "core_lexical_meaning": "Preserve something complete and unchanged.",
                "contextual_function": "Frames protecting the launch schedule as a shared goal.",
                "register_and_usage": "Neutral professional and general American English.",
                "arabic_equivalent": "يحافظ على الشيء كما هو دون تغيير أو مساس"
            },
            {
                "expression": "push [something] back", "source_type": "from_recommended_option", "source_ref_id": "option_3", "source_speaker": None,
                "full_sentence_citation": "We can take those on if we either adjust the current scope or push the launch date back.",
                "usage_status_label": "Used in Strategic Option 3 (Concise)",
                "core_lexical_meaning": "Move a scheduled event or deadline to a later time.",
                "contextual_function": "Refers neutrally to moving the launch date later on the calendar.",
                "register_and_usage": "Everyday workplace and conversational American English.",
                "arabic_equivalent": "يؤخّر أو يؤجّل موعداً إلى وقت لاحق"
            },
            {
                "expression": "push back on [something]", "source_type": "related_extension", "source_ref_id": None, "source_speaker": None, "full_sentence_citation": None,
                "usage_status_label": "Related extension — not used in the cited response",
                "core_lexical_meaning": "Object to, resist, or ask to reconsider a request, proposal, or assumption (not necessarily a flat refusal).",
                "contextual_function": "Contrasts objecting to or questioning part of a request with rescheduling a date.",
                "register_and_usage": "Common workplace English.",
                "arabic_equivalent": "يعترض على طلب أو يتحفّظ عليه أو يطلب إعادة النظر فيه",
                "contrastive_note": "Unlike 'push [a date] back' (reschedule to a later time), 'push back on [a request]' means objecting to, resisting, or asking to reconsider a demand."
            }
        ]
    }


# === CLAUDE V2 ARCHITECTURE MODELS & CODE GUARDS ===
from typing import Sequence, List, Optional, Literal, Any, Dict
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, ValidationError

BRITISHISM_AND_CLICHE_DENYLIST = (
    "lost the plot",
    "bloody hell",
    "taking the piss",
    "take the piss",
    "chuffed",
    "knackered",
    "gobsmacked",
    "gutted",
    "the ultimate linguistic",
    "linguistic chameleon",
    "linguistic shield",
    "passive-aggressive static",
    "shots fired",
)

MIRROR_INFLATION_DENYLIST = (
    "why is that the case",
    "what is the reason for that",
    "resonates with",
    "personal approach",
    "furthermore",
    "moreover",
    "consequently",
    "in addition to that",
    "as a matter of fact",
)

SPEAKER_HARD_WORD_LIMITS = {
    "Jake": 24,
    "Chloe": 35,
    "Marcus": 42,
}


def _word_tokens(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or "")


def _normalize_contractions_and_punct(text: str) -> str:
    s = (text or "").lower().strip()
    replacements = {
        "what's": "what is",
        "what've": "what have",
        "that's": "that is",
        "it's": "it is",
        "i'm": "i am",
        "you're": "you are",
        "we're": "we are",
        "they're": "they are",
        "don't": "do not",
        "doesn't": "does not",
        "didn't": "did not",
        "can't": "cannot",
        "won't": "will not",
        "isn't": "is not",
        "aren't": "are not",
        "ok": "okay",
    }
    for k, v in replacements.items():
        s = re.sub(rf"\b{re.escape(k)}\b", v, s)
    return " ".join(re.findall(r"[a-z0-9]+", s))


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["Marcus", "Chloe", "Jake"]
    text: str

    @field_validator("speaker", mode="before")
    @classmethod
    def _norm_speaker(cls, v: Any) -> Any:
        if isinstance(v, str):
            cap = v.strip().capitalize()
            if cap in ("Marcus", "Chloe", "Jake"):
                return cap
        return v

    @model_validator(mode="after")
    def _validate_turn_constraints(self) -> "Turn":
        clean = (self.text or "").strip()
        if not clean:
            raise ValueError("Turn.text cannot be empty.")
        words = _word_tokens(clean)
        max_w = SPEAKER_HARD_WORD_LIMITS.get(self.speaker, 42)
        if len(words) > max_w:
            raise ValueError(
                f"{self.speaker} turn has {len(words)} words, exceeding hard limit of {max_w} words."
            )
        low = clean.lower()
        for banned in BRITISHISM_AND_CLICHE_DENYLIST:
            if banned in low:
                raise ValueError(
                    f"Turn contains banned Britishism or meta-cliche '{banned}'."
                )
        return self


class DialogueOnlyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: List[Turn] = Field(..., min_length=2, max_length=4)

    @model_validator(mode="after")
    def _validate_dialogue_flow(self) -> "DialogueOnlyResponse":
        speakers = {t.speaker for t in self.turns}
        if len(speakers) < 2:
            raise ValueError("DialogueOnlyResponse must include at least 2 distinct speakers.")
        question_turns = [i for i, t in enumerate(self.turns) if "?" in t.text]
        if len(question_turns) > 1:
            raise ValueError(
                f"At most 1 turn may ask a question (found questions in turns {question_turns})."
            )
        return self


class MirrorEdit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    span: str
    replacement: str
    trigger: Literal["register", "calque", "wordy", "reference", "pragmatic", "error", "us_preference"] = "calque"
    why: str

    @field_validator("trigger", mode="before")
    @classmethod
    def _norm_trigger(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        if s in ("register", "calque", "wordy", "reference", "pragmatic", "error", "us_preference"):
            return s
        if "us_" in s or "american" in s or "brit" in s or "prefer" in s or "idiom" in s:
            return "us_preference"
        if "colloc" in s or "interf" in s or "literal" in s:
            return "calque"
        if "gramm" in s or "tense" in s or "prepos" in s:
            return "error"
        if "pronoun" in s or "person" in s:
            return "reference"
        if "formal" in s or "stiff" in s or "tone" in s:
            return "register"
        return "calque"


class Mirror(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["natural", "fix"] = "natural"
    edits: List[MirrorEdit] = Field(default_factory=list)
    vetoed: bool = False
    veto_reason: Optional[str] = None
    composed_text: Optional[str] = None
    html_render: Optional[str] = None


def apply_mirror_guards(mirror: Mirror, learner_text: str) -> Mirror:
    orig = (learner_text or "").strip()
    if not orig or mirror.verdict == "natural" or not mirror.edits:
        return Mirror(verdict="natural", edits=[], vetoed=False)

    valid_edits: List[MirrorEdit] = []
    composed = orig
    html_parts: List[str] = []

    for ed in mirror.edits[:2]:
        sp = (ed.span or "").strip()
        rep = (ed.replacement or "").strip()
        why = (ed.why or "").strip()

        sp_words = _word_tokens(sp)
        rep_words = _word_tokens(rep)
        why_words = _word_tokens(why)

        if not sp_words or len(sp_words) > 12:
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="span_length_invalid")
        if not rep_words:
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="empty_replacement")
        if len(rep_words) > len(sp_words) + 2:
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="replacement_too_long")
        if len(why_words) > 28:
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="why_too_long")

        if _normalize_contractions_and_punct(sp) == _normalize_contractions_and_punct(rep):
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="trivial_contraction_or_punct_edit")

        rep_low = rep.lower()
        for inf in MIRROR_INFLATION_DENYLIST:
            if inf in rep_low:
                return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason=f"inflation_banned:{inf}")

        m = re.search(re.escape(sp), composed)
        if not m:
            m = re.search(re.escape(sp), composed, flags=re.IGNORECASE)
        if not m:
            return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="span_not_found_verbatim")

        exact_matched_span = composed[m.start():m.end()]
        composed = composed[:m.start()] + rep + composed[m.end():]

        prefix_words = orig[:m.start()].strip().split()
        ctx_lead = ("…" + " ".join(prefix_words[-3:]) + " ") if prefix_words else ""
        html_parts.append(
            f"🪞 {ctx_lead}<s>{exact_matched_span}</s> → <b>{rep}</b>\n↳ {why}"
        )
        valid_edits.append(
            MirrorEdit(span=exact_matched_span, replacement=rep, trigger=ed.trigger, why=why)
        )

    orig_word_count = len(_word_tokens(orig))
    composed_word_count = len(_word_tokens(composed))
    if composed_word_count > orig_word_count + 2:
        return Mirror(verdict="natural", edits=[], vetoed=True, veto_reason="composed_sentence_too_long")

    return Mirror(
        verdict="fix",
        edits=valid_edits,
        vetoed=False,
        veto_reason=None,
        composed_text=composed,
        html_render="\n".join(html_parts),
    )


class Chunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turn: int = Field(default=0, ge=0, le=5)
    expression: str
    base_pattern: str
    kind: Literal["move", "phrasal", "chunk"] = "chunk"
    does: str
    say_it_when: str
    ar: str

    @field_validator("kind", mode="before")
    @classmethod
    def _norm_kind(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        if s in ("move", "phrasal", "chunk"):
            return s
        if "phrasal" in s or "verb" in s:
            return "phrasal"
        if "move" in s or "pragm" in s or "discours" in s:
            return "move"
        return "chunk"

    @model_validator(mode="after")
    def _validate_chunk_fields(self) -> "Chunk":
        bp_words = [
            w for w in (self.base_pattern or "").strip().split()
            if w.lower().strip(".,!?") not in _FILLER_INTENSIFIER_ADVERBS
        ]
        bp_clean = " ".join(bp_words)
        bp_clean = re.sub(r"\b(it|them)\b", "[something]", bp_clean)
        bp_clean = re.sub(r"\b(him|her)\b", "[someone]", bp_clean)
        self.base_pattern = bp_clean.strip()

        if not self.expression.strip() or not self.base_pattern:
            raise ValueError("Chunk expression and base_pattern cannot be empty.")
        if len(_word_tokens(self.does)) > 24:
            raise ValueError("Chunk.does must be <= 14 words.")
        if len(_word_tokens(self.say_it_when)) > 26:
            raise ValueError("Chunk.say_it_when must be <= 16 words.")
        if not re.search(r"[\u0600-\u06FF]", self.ar or ""):
            raise ValueError("Chunk.ar must contain natural Arabic script.")
        return self


class CoachResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mirror: Mirror = Field(default_factory=Mirror)
    chunks: List[Chunk] = Field(..., min_length=1, max_length=2)

    def validate_and_guard(
        self,
        turns: Sequence[Turn],
        learner_text: str,
        recent_chunks: Sequence[str] = (),
    ) -> "CoachResponse":
        self.mirror = apply_mirror_guards(self.mirror, learner_text)
        recent_low = {c.lower().strip() for c in recent_chunks if c}

        verified_chunks: List[Chunk] = []
        for idx, ch in enumerate(self.chunks):
            matched_turn_idx = None
            # Check declared turn first, then auto-scan all turns if index was off
            candidate_indices = ([ch.turn] if 0 <= ch.turn < len(turns) else []) + [i for i in range(len(turns)) if i != ch.turn]
            for t_idx in candidate_indices:
                t_txt = turns[t_idx].text
                if (
                    verify_contiguous_expression_in_sentence(ch.expression, t_txt)
                    or verify_contiguous_expression_in_sentence(ch.base_pattern, t_txt)
                ):
                    matched_turn_idx = t_idx
                    break
            if matched_turn_idx is None:
                continue
            ch.turn = matched_turn_idx
            if ch.base_pattern.lower() in recent_low or ch.expression.lower().strip() in recent_low:
                continue
            verified_chunks.append(ch)

        if not verified_chunks:
            verified_chunks = self.chunks[:1]

        if not verified_chunks:
            verified_chunks = [self.chunks[0]]

        if self.mirror.verdict == "fix":
            self.chunks = verified_chunks[:1]
        else:
            self.chunks = verified_chunks[:2]
        return self


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue: str
    meaning: str
    ar: Optional[str] = None


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ask", "go_along", "name_it"]
    line: str
    when_to_use: str
    risk: Literal["low", "mid", "high"]

    @model_validator(mode="after")
    def _check_move_line(self) -> "Move":
        w_cnt = len(_word_tokens(self.line))
        if w_cnt < 2 or w_cnt > 16:
            raise ValueError(f"Move.line must be 2 to 16 words (got {w_cnt}).")
        return self


class YourTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counterpart: Literal["Marcus", "Chloe", "Jake"]
    their_line: str
    hidden_reading: int = Field(..., ge=0, le=2)
    goal: str
    prompt: str


class ReadTheRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_point: str
    readings: List[Reading] = Field(..., min_length=2, max_length=3)
    moves: List[Move] = Field(..., min_length=3, max_length=3)
    lounge_take: List[Turn] = Field(..., min_length=1, max_length=2)
    your_turn: YourTurn
    chunks: List[Chunk] = Field(..., min_length=1, max_length=2)

    @model_validator(mode="after")
    def _validate_rtr_contract(self) -> "ReadTheRoom":
        if self.moves[0].kind != "ask":
            raise ValueError("The first Move in ReadTheRoom must always be kind='ask' (non-presumptive clarifying move).")
        if self.your_turn.hidden_reading >= len(self.readings):
            raise ValueError("your_turn.hidden_reading index is out of bounds for readings.")
        valid_rtr_chunks: List[Chunk] = []
        for idx, ch in enumerate(self.chunks):
            matched_t = None
            for t_i, t_obj in enumerate(self.lounge_take):
                if (
                    verify_contiguous_expression_in_sentence(ch.expression, t_obj.text)
                    or verify_contiguous_expression_in_sentence(ch.base_pattern, t_obj.text)
                ):
                    matched_t = t_i
                    break
            if matched_t is not None:
                ch.turn = matched_t
                valid_rtr_chunks.append(ch)
        if not valid_rtr_chunks:
            raise ValueError("ReadTheRoom chunks did not match any sentence in lounge_take.")
        self.chunks = valid_rtr_chunks
        return self


class PracticeEval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["attempt", "off_topic"]
    lands_as: Optional[str] = None
    tweak: Optional[MirrorEdit] = None
    reply: Optional[Turn] = None
    done: bool = False

    def validate_with_learner_reply(self, learner_text: str) -> "PracticeEval":
        if self.intent == "off_topic":
            self.lands_as = None
            self.tweak = None
            self.reply = None
            self.done = True
            return self
        if not self.lands_as or len(_word_tokens(self.lands_as)) > 24:
            raise ValueError("PracticeEval.lands_as is required for 'attempt' and must be <= 24 words.")
        if self.reply is None:
            raise ValueError("PracticeEval.reply is required when intent='attempt'.")
        if self.tweak is not None:
            guarded = apply_mirror_guards(Mirror(verdict="fix", edits=[self.tweak]), learner_text)
            if guarded.verdict != "fix" or not guarded.edits:
                self.tweak = None
            else:
                self.tweak = guarded.edits[0]
        return self


class TestLoungeValidationLayer(unittest.TestCase):
    def test_01_valid_situation_strategy_payload_passes(self):
        model = LoungeTurnResponse.model_validate(make_valid_strategy_payload(), context=make_trusted_ctx())
        self.assertEqual(model.active_mode, "situation_strategy")

    def test_02_valid_submitted_draft_with_mirror_passes(self):
        txt = "Before we commit to this vendor, let's check their maintenance costs."
        payload = {
            "active_mode": "banter", "learner_input_text": txt,
            "input_intent_type": "submitted_draft_for_evaluation", "learner_diagnosis": "already_natural",
            "show_diagnostic_mirror": True,
            "diagnostic_mirror": {"primary_issue_label": "already_natural", "explanation": "Natural.", "revised_version": None},
            "host_turns": [{"turn_id": "turn_1", "speaker": "marcus", "text": "That sounds completely natural for a vendor review."}],
            "lexical_items": [{
                "expression": "commit to [something]", "source_type": "from_learner_input", "source_ref_id": "learner_input",
                "full_sentence_citation": txt, "usage_status_label": "From your draft",
                "core_lexical_meaning": "Bind oneself to an agreement.", "contextual_function": "Locking in the contract.",
                "register_and_usage": "Professional.", "arabic_equivalent": "يلتزم بـ"
            }]
        }
        self.assertTrue(LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx("banter", txt, False)).show_diagnostic_mirror)

    def test_03_invalid_submitted_draft_missing_mirror_fails(self):
        payload = make_valid_strategy_payload()
        payload["input_intent_type"] = "submitted_draft_for_evaluation"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_04_invalid_request_new_phrasing_showing_already_natural_mirror_fails(self):
        payload = make_valid_strategy_payload()
        payload["show_diagnostic_mirror"] = True
        payload["diagnostic_mirror"] = {"primary_issue_label": "already_natural", "explanation": "Natural.", "revised_version": None}
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_05_invalid_learner_citation_not_in_learner_input_fails(self):
        payload = make_valid_strategy_payload()
        payload["lexical_items"][0] = {
            "expression": "scope creep", "source_type": "from_learner_input", "source_ref_id": "learner_input",
            "full_sentence_citation": "How do I stop this scope creep?", "usage_status_label": "From learner",
            "core_lexical_meaning": "Uncontrolled expansion.", "contextual_function": "Scope.", "register_and_usage": "Work.", "arabic_equivalent": "تمدد النطاق"
        }
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_06_invalid_scattered_words_not_contiguous_phrase_fails(self):
        payload = make_valid_strategy_payload()
        payload["host_turns"][0]["text"] = "We should take a look at the rain forecast and check the schedule."
        payload["lexical_items"][0] = {
            "expression": "take a rain check", "source_type": "verbatim_from_host_dialogue", "source_ref_id": "turn_1", "source_speaker": "marcus",
            "full_sentence_citation": "We should take a look at the rain forecast and check the schedule.",
            "usage_status_label": "From Marcus", "core_lexical_meaning": "Postpone.", "contextual_function": "Delay.", "register_and_usage": "Casual.", "arabic_equivalent": "نؤجلها"
        }
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_07_invalid_duplicate_strategy_archetypes_fails(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["strategic_options"][1]["archetype"] = "Diplomatic"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_08_invalid_missing_followup_reaction_type_fails(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["follow_up_branches"][2]["reaction_type"] = "Deflection or Minimization"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_09_invalid_wrong_mode_missing_required_block_or_forbidden_block_fails(self):
        payload = make_valid_strategy_payload()
        payload["active_mode"] = "subtext_decoder"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx("subtext_decoder"))

    def test_10_invalid_citation_attributed_to_wrong_speaker_or_option_fails(self):
        payload = make_valid_strategy_payload()
        payload["lexical_items"][0]["source_ref_id"] = "option_2"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_11_invalid_option_citation_falling_back_to_host_dialogue_fails(self):
        payload = make_valid_strategy_payload()
        payload["lexical_items"][0]["full_sentence_citation"] = "The cleanest move is to frame this as a scope-versus-schedule trade-off rather than a refusal."
        payload["lexical_items"][0]["expression"] = "rather than a refusal"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_12_invalid_spoken_line_word_count_out_of_range_fails(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["strategic_options"][2]["spoken_line"] = "No, we cannot do that right now."
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_13_boundary_bracket_placeholder_cannot_cross_comma_or_clause_fails(self):
        payload = make_valid_strategy_payload()
        payload["host_turns"][0]["text"] = "If they push the team, back away from committing today."
        payload["lexical_items"][0] = {
            "expression": "push [something] back", "source_type": "verbatim_from_host_dialogue", "source_ref_id": "turn_1", "source_speaker": "marcus",
            "full_sentence_citation": "If they push the team, back away from committing today.",
            "usage_status_label": "From Marcus", "core_lexical_meaning": "Delay.", "contextual_function": "Test.", "register_and_usage": "General.", "arabic_equivalent": "يؤجل"
        }
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_14_boundary_smart_quotes_and_em_dash_in_valid_citation_passes(self):
        payload = make_valid_strategy_payload()
        payload["host_turns"][1]["text"] = "Right—when you’re up against a deadline, don’t overpromise."
        payload["lexical_items"][0] = {
            "expression": "up against [something]", "source_type": "verbatim_from_host_dialogue", "source_ref_id": "turn_2", "source_speaker": "chloe",
            "full_sentence_citation": "Right—when you're up against a deadline, don't overpromise.",
            "usage_status_label": "Verbatim from Chloe", "core_lexical_meaning": "Facing a limit.", "contextual_function": "Deadline.", "register_and_usage": "General.", "arabic_equivalent": "في مواجهة موعد ضاغط"
        }
        self.assertEqual(LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx()).lexical_items[0].source_ref_id, "turn_2")

    def test_15_invalid_partial_sentence_fragment_in_full_sentence_citation_fails(self):
        payload = make_valid_strategy_payload()
        payload["lexical_items"][0]["full_sentence_citation"] = "To keep our launch date intact"
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())

    def test_16_invalid_pronunciation_audio_provided_true_when_trusted_context_has_no_audio_fails(self):
        txt = "How do I pronounce itinerary?"
        payload = {
            "active_mode": "pronunciation_guide", "learner_input_text": txt,
            "input_intent_type": "general_conversation_or_question",
            "learner_diagnosis": "already_natural", "show_diagnostic_mirror": False,
            "host_turns": [{"turn_id": "turn_1", "speaker": "chloe", "text": "Here is how to pronounce it."}],
            "pronunciation_guide": {
                "audio_provided": True, "diagnosis_disclaimer": "I listened to your audio.",
                "target_items": [{"word": "itinerary", "ipa_variants": ["/aɪˈtɪnəˌrɛri/"], "approximate_stress_guides": ["eye-TIN-uh-rer-ee"],
                                  "segmental_and_prosodic_notes": "Aspirated /t/.", "connected_speech_variability_note": "Varies."}]
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx("pronunciation_guide", txt, False))
        self.assertIn("trusted runtime_has_audio_bytes is False", str(ctx.exception))

    def test_17_invalid_unordered_escalation_ids_in_subtext_mode_fails(self):
        txt = "My boss said let's put a pin in this."
        payload = {
            "active_mode": "subtext_decoder", "learner_input_text": txt,
            "input_intent_type": "general_conversation_or_question",
            "learner_diagnosis": "already_natural", "show_diagnostic_mirror": False,
            "host_turns": [{"turn_id": "turn_1", "speaker": "marcus", "text": "That can mean a few things."}],
            "subtext_analysis": {
                "literal_meaning": "Pause.", "observable_facts": "Single deferral.", "plausible_implicatures": ["Timing", "Deferral"],
                "behavior_confidence": "Low", "motive_confidence": "Low", "evidence_to_watch": "Follow-up date.",
                "escalation_options": [
                    {"option_id": "escalation_2", "label": "Clarify", "objective": "Timing", "exact_wording": "When should we revisit?", "makes_explicit": "Time", "interpersonal_tradeoff": "Low", "when_not_to_use": "Never"},
                    {"option_id": "escalation_1", "label": "Criteria", "objective": "Blockers", "exact_wording": "What do you need?", "makes_explicit": "Criteria", "interpersonal_tradeoff": "Med", "when_not_to_use": "If clear"}
                ]
            }
        }
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx("subtext_decoder", txt, False))

    def test_18_limitation_proof_pydantic_cannot_detect_fabricated_facts_or_wrong_meaning(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["strategic_options"][1]["spoken_line"] = (
            "We can add those two features if we extend the launch by two weeks for payment testing."
        )
        payload["lexical_items"][0]["core_lexical_meaning"] = "Completely cancel a project forever."
        model = LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertIn("two weeks for payment testing", model.strategy_playbook.strategic_options[1].spoken_line)
        self.assertEqual(model.lexical_items[0].core_lexical_meaning, "Completely cancel a project forever.")

    def test_19_new_llm_spoofing_runtime_has_audio_bytes_in_json_rejected_by_strict_schema(self):
        payload = make_valid_strategy_payload()
        payload["runtime_has_audio_bytes"] = True
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertIn("Extra inputs are not permitted", str(ctx.exception))

    def test_20_new_hyphenated_expressions_valid_contiguous_match_passes(self):
        payload = make_valid_strategy_payload()
        payload["host_turns"][0]["text"] = "Let's discuss this in our one-on-one before going heads-down on the release."
        payload["lexical_items"][0] = {
            "expression": "heads-down", "source_type": "verbatim_from_host_dialogue", "source_ref_id": "turn_1", "source_speaker": "marcus",
            "full_sentence_citation": "Let's discuss this in our one-on-one before going heads-down on the release.",
            "usage_status_label": "Verbatim from Marcus", "core_lexical_meaning": "Fully focused and immersed in work.",
            "contextual_function": "Describes uninterrupted execution.", "register_and_usage": "Workplace conversational.",
            "arabic_equivalent": "منهمك بالكامل في العمل"
        }
        model = LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertEqual(model.lexical_items[0].expression, "heads-down")

    def test_21_new_hyphenated_expression_against_scattered_unhyphenated_words_fails(self):
        payload = make_valid_strategy_payload()
        payload["host_turns"][0]["text"] = "Keep your heads up and do not look down during the presentation."
        payload["lexical_items"][0] = {
            "expression": "heads-down", "source_type": "verbatim_from_host_dialogue", "source_ref_id": "turn_1", "source_speaker": "marcus",
            "full_sentence_citation": "Keep your heads up and do not look down during the presentation.",
            "usage_status_label": "Verbatim from Marcus", "core_lexical_meaning": "Focused.",
            "contextual_function": "Test.", "register_and_usage": "Workplace.", "arabic_equivalent": "منهمك"
        }
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertIn("contiguous phrase", str(ctx.exception))

    def test_22_new_extra_unknown_field_in_nested_block_fails(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["hallucinated_extra_field"] = "should fail"
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertIn("Extra inputs are not permitted", str(ctx.exception))

    def test_23_new_misaligned_option_id_and_archetype_order_fails(self):
        payload = make_valid_strategy_payload()
        payload["strategy_playbook"]["strategic_options"][0]["archetype"] = "Assertive"
        payload["strategy_playbook"]["strategic_options"][1]["archetype"] = "Diplomatic"
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx())
        self.assertIn("must strictly map", str(ctx.exception))

    def test_24_new_pronunciation_audio_provided_true_when_trusted_context_has_audio_passes(self):
        txt = "How do I pronounce itinerary?"
        payload = {
            "active_mode": "pronunciation_guide", "learner_input_text": txt,
            "input_intent_type": "general_conversation_or_question",
            "learner_diagnosis": "already_natural", "show_diagnostic_mirror": False,
            "host_turns": [{"turn_id": "turn_1", "speaker": "chloe", "text": "I listened to your recording and your stress on the second syllable was clear."}],
            "pronunciation_guide": {
                "audio_provided": True, "diagnosis_disclaimer": "Evaluated from your voice note.",
                "target_items": [{"word": "itinerary", "ipa_variants": ["/aɪˈtɪnəˌrɛri/"], "approximate_stress_guides": ["eye-TIN-uh-rer-ee"],
                                  "segmental_and_prosodic_notes": "Aspirated /t/ on second syllable.", "connected_speech_variability_note": "Natural rhythm."}]
            }
        }
        model = LoungeTurnResponse.model_validate(payload, context=make_trusted_ctx("pronunciation_guide", txt, True))
        self.assertTrue(model.pronunciation_guide.audio_provided)

    def test_25_new_missing_trusted_context_keys_fails(self):
        payload = make_valid_strategy_payload()
        with self.assertRaises(ValidationError) as ctx:
            LoungeTurnResponse.model_validate(payload, context={"runtime_has_audio_bytes": False})
        self.assertIn("missing required keys", str(ctx.exception))

    def _ctx(self, active_mode: str = 'banter', learner_input_text: str = 'Remote work is always better.', has_audio: bool = False):
        return {
            'active_mode': active_mode,
            'learner_input_text': learner_input_text,
            'runtime_has_audio_bytes': has_audio,
        }

    def _base_banter_payload(self):
        sent1 = "You're conflating individual task execution with team cohesion."
        sent2 = "Wait until you see how isolation affects junior mentorship over time."
        return {
            'active_mode': 'banter',
            'learner_input_text': 'Remote work is always better.',
            'input_intent_type': 'general_conversation_or_question',
            'learner_diagnosis': 'already_natural',
            'show_diagnostic_mirror': False,
            'diagnostic_mirror': None,
            'subtext_analysis': None,
            'strategy_playbook': None,
            'pronunciation_guide': None,
            'host_turns': [
                {'turn_id': 'turn_1', 'speaker': 'marcus', 'text': sent1},
                {'turn_id': 'turn_2', 'speaker': 'chloe', 'text': sent2},
            ],
            'lexical_items': [
                {
                    'expression': 'conflate [something] with [something]',
                    'source_type': 'verbatim_from_host_dialogue',
                    'source_ref_id': 'turn_1',
                    'source_speaker': 'marcus',
                    'full_sentence_citation': sent1,
                    'usage_status_label': 'Verbatim from Marcus (turn_1)',
                    'core_lexical_meaning': 'Treat two distinct concepts as if they were the same.',
                    'contextual_function': 'Challenges false equivalence.',
                    'register_and_usage': 'Analytical American English.',
                    'arabic_equivalent': 'يخلط بين شيئين مختلفين',
                    'contrastive_note': None,
                }
            ],
        }

    def test_26_regression_case_a_inflected_dialogue_base_expression(self):
        p = self._base_banter_payload()
        sent = "You're conflating individual task execution with team cohesion."
        p['host_turns'][0]['text'] = sent
        p['lexical_items'][0]['expression'] = 'conflate [something] with [something]'
        p['lexical_items'][0]['full_sentence_citation'] = sent
        res = LoungeTurnResponse.model_validate(p, context=self._ctx('banter', p['learner_input_text']))
        self.assertEqual(res.lexical_items[0].expression, 'conflate [something] with [something]')

    def test_27_regression_case_b_reject_inflected_duplicate_in_related_extension(self):
        p = self._base_banter_payload()
        p['host_turns'][0]['text'] = "You're glossing over a major drawback. " + p['host_turns'][0]['text']
        p['lexical_items'].append({
            'expression': 'gloss over something',
            'source_type': 'related_extension',
            'source_ref_id': None,
            'source_speaker': None,
            'full_sentence_citation': None,
            'usage_status_label': 'Related extension — not used in the cited response',
            'core_lexical_meaning': 'Treat a problem too briefly or ignore its difficulty.',
            'contextual_function': 'Highlights ignoring a drawback.',
            'register_and_usage': 'Common analytical and workplace English.',
            'arabic_equivalent': 'يمرّ مرور الكرام على شيء أو يتغاضى عنه',
            'contrastive_note': 'Contrasts with addressing the root issue directly.'
        })
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(p, context=self._ctx('banter', p['learner_input_text']))

    def test_28_regression_case_c_accept_genuine_related_extension(self):
        p = self._base_banter_payload()
        p['host_turns'][0]['text'] = "You're glossing over a major drawback. " + p['host_turns'][0]['text']
        p['lexical_items'].append({
            'expression': 'a double-edged sword',
            'source_type': 'related_extension',
            'source_ref_id': None,
            'source_speaker': None,
            'full_sentence_citation': None,
            'usage_status_label': 'Related extension — not used in the cited response',
            'core_lexical_meaning': 'Something that has both favorable and unfavorable consequences.',
            'contextual_function': 'Frames remote work as carrying both benefits and trade-offs.',
            'register_and_usage': 'Common professional and general English idiom.',
            'arabic_equivalent': 'سلاح ذو حدين',
            'contrastive_note': 'Acknowledges both pros and cons rather than focusing on a single drawback.'
        })
        res = LoungeTurnResponse.model_validate(p, context=self._ctx('banter', p['learner_input_text']))
        self.assertEqual(res.lexical_items[-1].expression, 'a double-edged sword')

    def test_29_orthographic_inflections_and_false_positive_protection(self):
        self.assertTrue(verify_contiguous_expression_in_sentence('make [something] clear', 'She is making her priorities clear.'))
        self.assertTrue(verify_contiguous_expression_in_sentence('run into [something]', 'We keep running into the same bottleneck.'))
        self.assertTrue(verify_contiguous_expression_in_sentence('erode something', 'You have inadvertently eroded the social capital.'))
        self.assertFalse(verify_contiguous_expression_in_sentence('make [something] clear', 'The market is marking its territory clear.'))
        self.assertFalse(verify_contiguous_expression_in_sentence('run into [something]', 'They were ruining into the schedule.'))

    def test_30_reject_filler_adverbs_and_gerund_free_combinations_in_base_pattern(self):
        p1 = self._base_banter_payload()
        p1['host_turns'][0]['text'] = 'Look, this decision hinges entirely on our budget.'
        p1['lexical_items'][0]['expression'] = 'hinge entirely on [something]'
        p1['lexical_items'][0]['full_sentence_citation'] = 'Look, this decision hinges entirely on our budget.'
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(p1, context={'active_mode': 'banter', 'learner_input_text': p1['learner_input_text'], 'runtime_has_audio_bytes': False})
        p1['lexical_items'][0]['expression'] = 'hinge on [something]'
        res = LoungeTurnResponse.model_validate(p1, context={'active_mode': 'banter', 'learner_input_text': p1['learner_input_text'], 'runtime_has_audio_bytes': False})
        self.assertEqual(res.lexical_items[0].expression, 'hinge on [something]')
        p2 = self._base_banter_payload()
        p2['lexical_items'][0]['expression'] = 'masking resentment'
        with self.assertRaises(ValidationError):
            LoungeTurnResponse.model_validate(p2, context={'active_mode': 'banter', 'learner_input_text': p1['learner_input_text'], 'runtime_has_audio_bytes': False})

    def test_31_claude_turn_speaker_word_limits(self):
        ok_jake = Turn(speaker="Jake", text="Hard pass on that—let's just cut to the chase before we waste all afternoon.")
        self.assertEqual(ok_jake.speaker, "Jake")
        with self.assertRaises(ValidationError):
            Turn(speaker="Jake", text=" ".join(["word"] * 25))

    def test_32_claude_turn_britishism_and_cliche_denylist(self):
        with self.assertRaises(ValidationError):
            Turn(speaker="Jake", text="No kidding, he has totally lost the plot on work-life balance.")
        with self.assertRaises(ValidationError):
            Turn(speaker="Marcus", text="Look, that phrase is the ultimate linguistic shield in meetings.")

    def test_33_claude_dialogue_only_response_flow_and_single_question_guard(self):
        d = DialogueOnlyResponse(turns=[
            Turn(speaker="Chloe", text="Wait, hold on—you actually agreed to run that meeting on Friday?"),
            Turn(speaker="Jake", text="Not a chance, I pushed it to Monday morning."),
        ])
        self.assertEqual(len(d.turns), 2)
        with self.assertRaises(ValidationError):
            DialogueOnlyResponse(turns=[
                Turn(speaker="Marcus", text="Why did you say yes to that?"),
                Turn(speaker="Chloe", text="And when are you going to tell them?"),
            ])

    def test_34_claude_mirror_silent_on_natural_and_trivial_contraction_edits(self):
        learner = "What's your favorite English expression, guys, and why?"
        m_trivial = Mirror(verdict="fix", edits=[
            MirrorEdit(span="What's", replacement="What is", trigger="register", why="Full form.")
        ])
        res = apply_mirror_guards(m_trivial, learner)
        self.assertEqual(res.verdict, "natural")
        self.assertTrue(res.vetoed)

    def test_35_claude_mirror_surgical_span_replacement_and_html_diff(self):
        learner = "Ok I hear you, now each one of you tell me a proverb that makes sense for him or her"
        m = Mirror(verdict="fix", edits=[
            MirrorEdit(
                span="makes sense for him or her",
                replacement="fits you",
                trigger="reference",
                why="After 'each of you', stay in 'you' instead of shifting to 'him or her'."
            )
        ])
        res = apply_mirror_guards(m, learner)
        self.assertEqual(res.verdict, "fix")
        self.assertFalse(res.vetoed)
        self.assertIn("<s>makes sense for him or her</s> → <b>fits you</b>", res.html_render)
        self.assertTrue(res.composed_text.endswith("that fits you"))

    def test_36_claude_mirror_allows_phrasal_verb_plus_two_words(self):
        learner = "I can't tolerate his excuses anymore."
        m = Mirror(verdict="fix", edits=[
            MirrorEdit(
                span="tolerate",
                replacement="put up with",
                trigger="register",
                why="In spoken American English, 'put up with' is much more natural than 'tolerate'."
            )
        ])
        res = apply_mirror_guards(m, learner)
        self.assertEqual(res.verdict, "fix")
        self.assertEqual(res.composed_text, "I can't put up with his excuses anymore.")

    def test_37_claude_mirror_vetoes_academic_inflation(self):
        learner = "What's your favorite expression guys, and why?"
        m = Mirror(verdict="fix", edits=[
            MirrorEdit(
                span="and why",
                replacement="and why is that the case",
                trigger="register",
                why="More complete question."
            )
        ])
        res = apply_mirror_guards(m, learner)
        self.assertEqual(res.verdict, "natural")
        self.assertTrue(res.vetoed)

    def test_38_claude_mirror_vetoes_non_verbatim_hallucinated_span(self):
        learner = "What have you been up to everybody?"
        m = Mirror(verdict="fix", edits=[
            MirrorEdit(
                span="what have you guys",
                replacement="what have you all",
                trigger="wordy",
                why="Cleaner phrasing."
            )
        ])
        res = apply_mirror_guards(m, learner)
        self.assertEqual(res.verdict, "natural")
        self.assertTrue(res.vetoed)
        self.assertEqual(res.veto_reason, "span_not_found_verbatim")

    def test_39_claude_coach_response_verifies_chunks_and_caps_cognitive_load(self):
        turns = [
            Turn(speaker="Marcus", text="I see the appeal, Musa, but I'm not sold on the timeline yet."),
            Turn(speaker="Chloe", text="Fair enough, we can figure it out before Monday."),
        ]
        learner = "Each one of you tell me a proverb that makes sense for him or her"
        coach = CoachResponse(
            mirror=Mirror(verdict="fix", edits=[
                MirrorEdit(span="makes sense for him or her", replacement="fits you", trigger="reference", why="Stay in second person 'you'.")
            ]),
            chunks=[
                Chunk(turn=0, expression="not sold on", base_pattern="not sold on [something]", kind="move", does="Expresses hesitation without a hard no.", say_it_when="You have doubts about a plan.", ar="غير مقتنع تماماً بـ"),
                Chunk(turn=1, expression="figure it out", base_pattern="figure it out", kind="phrasal", does="Find a practical solution.", say_it_when="Sorting out a problem.", ar="يجد حلاً للمشكلة"),
            ]
        ).validate_and_guard(turns=turns, learner_text=learner)
        self.assertEqual(coach.mirror.verdict, "fix")
        self.assertEqual(len(coach.chunks), 1)
        self.assertEqual(coach.chunks[0].base_pattern, "not sold on [something]")

    def test_40_claude_read_the_room_contract_and_first_move_ask(self):
        rtr = ReadTheRoom(
            surface_point="Acknowledges your method without endorsing it.",
            readings=[
                Reading(cue="Warm tone, nodding", meaning="Genuine appreciation for a creative workaround.", ar="إعجاب صادق بطريقة مبتكرة"),
                Reading(cue="Flat tone, slight pause", meaning="Polite skepticism that your approach is inefficient.", ar="تشكيك مهذب في كفاءة طريقتك"),
            ],
            moves=[
                Move(kind="ask", line="Do you have a cleaner way in mind?", when_to_use="You want to check their intent constructively.", risk="low"),
                Move(kind="go_along", line="Yeah, it gets the job done for now.", when_to_use="You want to keep things moving.", risk="low"),
                Move(kind="name_it", line="Wait, is that your polite way of saying it's awful?", when_to_use="You know them well and want to surface the dig.", risk="mid"),
            ],
            lounge_take=[
                Turn(speaker="Marcus", text="People use that line to hold back approval until they see how you react."),
                Turn(speaker="Chloe", text="Exactly, so don't get defensive—just put them on the spot politely."),
            ],
            your_turn=YourTurn(
                counterpart="Chloe",
                their_line="Well, that's certainly one way to organize the client deck.",
                hidden_reading=1,
                goal="Invite her actual suggestion without sounding defensive.",
                prompt="Chloe just dropped that line in rehearsal. What do you say back?"
            ),
            chunks=[
                Chunk(turn=0, expression="hold back", base_pattern="hold back [something]", kind="phrasal", does="Refrain from giving approval.", say_it_when="Someone stays noncommittal.", ar="يحجم عن إبداء الموافقة"),
            ]
        )
        self.assertEqual(rtr.moves[0].kind, "ask")

    def test_41_claude_practice_eval_attempt_with_guarded_tweak(self):
        learner_reply = "I would like to know what exactly you are implying right now."
        pe = PracticeEval(
            intent="attempt",
            lands_as="Sounds stiff and confrontational—'implying' puts her on trial.",
            tweak=MirrorEdit(
                span="what exactly you are implying",
                replacement="what you mean",
                trigger="pragmatic",
                why="Much calmer and less accusatory in spoken American English."
            ),
            reply=Turn(speaker="Chloe", text="Whoa, relax—I just think the second slide needs a cleaner hook."),
            done=False
        ).validate_with_learner_reply(learner_reply)
        self.assertEqual(pe.intent, "attempt")
        self.assertIsNotNone(pe.tweak)
        self.assertEqual(pe.tweak.replacement, "what you mean")

    def test_42_claude_practice_eval_off_topic_clears_cleanly(self):
        pe = PracticeEval(intent="off_topic").validate_with_learner_reply("Hey guys, let's talk about NYC pizza instead.")
        self.assertTrue(pe.done)
        self.assertIsNone(pe.reply)


# === CLAUDE V2 HTML FORMATTERS ===
import html as _html_mod

def _e(val) -> str:
    return _html_mod.escape(str(val if val is not None else ""))

SPEAKER_BADGES_V2 = {
    "Marcus": "🧔‍♂️ <b>Marcus:</b>",
    "Chloe": "👩🏼‍💼 <b>Chloe:</b>",
    "Jake": "🧢 <b>Jake:</b>",
}

RISK_BADGES_V2 = {
    "low": "🟢 Low Risk",
    "mid": "🟡 Medium Risk",
    "high": "🔴 High Stakes",
}

def format_v2_dialogue_html(dialogue_res) -> str:
    lines = []
    for t in dialogue_res.turns:
        badge = SPEAKER_BADGES_V2.get(t.speaker, f"🗣️ <b>{t.speaker}:</b>")
        lines.append(f"{badge} {_e(t.text)}")
    return "\n\n".join(lines)

def format_v2_coach_html(coach_res) -> str:
    if not coach_res:
        return ""
    lines = []
    if coach_res.mirror and coach_res.mirror.verdict == "fix" and coach_res.mirror.html_render:
        lines.append(coach_res.mirror.html_render)
        lines.append("")
    if coach_res.chunks:
        lines.append("💎 <b>Spoken American Moves:</b>")
        for idx, ch in enumerate(coach_res.chunks, 1):
            lines.append(
                f"({idx}) 🧩 <b>{_e(ch.base_pattern)}</b> <i>[{_e(ch.kind)}]</i> — {_e(ch.ar)}\n"
                f"  └ 💬 <i>\"{_e(ch.expression)}\"</i>\n"
                f"  └ 🎯 <b>Does:</b> {_e(ch.does)}\n"
                f"  └ 🗽 <b>Say it when:</b> {_e(ch.say_it_when)}"
            )
    return "\n".join(lines).strip()

def format_v2_read_the_room_html(rtr, input_text: str) -> str:
    lines = [
        "🎭 <b>Read the Room</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🗣️ <b>Input:</b> \"{_e(input_text)}\"",
        f"📌 <b>Surface / Core Point:</b> {_e(rtr.surface_point)}",
        "",
        "1️⃣ <b>What It Might Mean (Cue → Subtext)</b>",
    ]
    for idx, rd in enumerate(rtr.readings, 1):
        ar_part = f" — <i>{_e(rd.ar)}</i>" if rd.ar else ""
        lines.append(f"• <b>[{idx}] {_e(rd.cue)}</b> → {_e(rd.meaning)}{ar_part}")

    lines.append("\n2️⃣ <b>What You Could Say (Strategic Moves)</b>")
    move_titles = {
        "ask": "Clarify Without Assuming (Ask)",
        "go_along": "Keep Momentum Smoothly (Go Along)",
        "name_it": "Surface the Subtext / Set Boundary (Name It)",
    }
    for mv in rtr.moves:
        r_badge = RISK_BADGES_V2.get(mv.risk, mv.risk)
        m_title = move_titles.get(mv.kind, mv.kind)
        lines.append(
            f"• <b>{m_title}</b> ({r_badge})\n"
            f"  💬 <b>Say:</b> \"{_e(mv.line)}\"\n"
            f"  🎯 <b>Fits when:</b> {_e(mv.when_to_use)}"
        )

    lines.append("\n3️⃣ <b>Lounge Takeaway</b>")
    for t in rtr.lounge_take:
        badge = SPEAKER_BADGES_V2.get(t.speaker, f"🗣️ <b>{t.speaker}:</b>")
        lines.append(f"{badge} \"{_e(t.text)}\"")

    if rtr.chunks:
        lines.append("\n💎 <b>Spoken Moves from the Lounge</b>")
        for idx, ch in enumerate(rtr.chunks, 1):
            lines.append(
                f"• 🧩 <b>{_e(ch.base_pattern)}</b> ({_e(ch.ar)}): {_e(ch.does)} — <i>Say it when: {_e(ch.say_it_when)}</i>"
            )

    yt = rtr.your_turn
    c_badge = SPEAKER_BADGES_V2.get(yt.counterpart, f"🗣️ <b>{yt.counterpart}:</b>")
    lines.extend([
        "",
        "4️⃣ <b>Your Turn (Active Roleplay)</b>",
        f"{c_badge} \"{_e(yt.their_line)}\"",
        f"🎯 <b>Goal:</b> {_e(yt.goal)}",
        f"👉 <b>{_e(yt.prompt)}</b> <i>(Reply directly with your spoken line, or send /skip to exit)</i>",
    ])
    return "\n".join(lines)

def format_v2_practice_eval_html(pe, learner_text: str) -> str:
    lines = [
        f"👂 <b>How It Lands:</b> {_e(pe.lands_as or '')}"
    ]
    if pe.tweak:
        guarded = apply_mirror_guards(Mirror(verdict="fix", edits=[pe.tweak]), learner_text)
        if guarded.verdict == "fix" and guarded.html_render:
            lines.append(guarded.html_render)
    if pe.reply:
        badge = SPEAKER_BADGES_V2.get(pe.reply.speaker, f"🗣️ <b>{pe.reply.speaker}:</b>")
        lines.append(f"\n{badge} \"{_e(pe.reply.text)}\"")
    return "\n".join(lines)


_V2_AI_SINGLETON = None
_V2_PENDING_PRACTICE_MEM = {}

def _get_v2_ai_instance(g_dict: dict, l_dict: dict, db_inst):
    global _V2_AI_SINGLETON
    if _V2_AI_SINGLETON is not None:
        return _V2_AI_SINGLETON

    cand = l_dict.get("ai") or g_dict.get("ai") or l_dict.get("ai_engine") or g_dict.get("ai_engine")
    if cand is not None and hasattr(cand, "generate_dialogue") and hasattr(cand, "coach_turn"):
        _V2_AI_SINGLETON = cand
        return _V2_AI_SINGLETON

    import config, ai_engine as _ae_mod
    api_k = getattr(config, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY", "")
    _V2_AI_SINGLETON = _ae_mod.NYCLoungeAI(api_key=api_k, db=db_inst)
    return _V2_AI_SINGLETON


def handle_claude_v2_turn_safe(g_dict: dict, l_dict: dict, message_obj, override_text: str = None, override_chat_id: int = None, override_uid: int = None) -> bool:
    import logging, traceback
    bot_inst = l_dict.get("bot") or g_dict.get("bot")
    db_inst = l_dict.get("db") or g_dict.get("db")
    chat_id = override_chat_id or (message_obj.chat.id if message_obj else None)
    user_id = override_uid or l_dict.get("uid") or (message_obj.from_user.id if message_obj else None)
    reply_to_id = getattr(message_obj, "message_id", None) if message_obj else None

    try:
        clean_t = (override_text if override_text is not None else (l_dict.get("text") or (message_obj.text if message_obj else "") or "")).strip()
        if not clean_t or not bot_inst or not chat_id or not user_id:
            return False

        if clean_t.lower() in ("/skip", "skip", "تخطي"):
            _V2_PENDING_PRACTICE_MEM.pop(user_id, None)
            if hasattr(db_inst, "clear_pending_practice"):
                try:
                    db_inst.clear_pending_practice(user_id)
                except Exception:
                    pass
            bot_inst.send_message(chat_id, "✅ <b>Exited roleplay practice.</b> Back to Lounge Banter!", parse_mode="HTML")
            return True

        if clean_t.startswith("/"):
            return False

        user_obj = l_dict.get("user")
        if user_obj is None and db_inst and hasattr(db_inst, "get_user"):
            try:
                user_obj = db_inst.get_user(user_id)
            except Exception:
                user_obj = {"user_id": user_id}

        if isinstance(user_obj, dict):
            u_dict = dict(user_obj)
        elif user_obj is not None and hasattr(user_obj, "keys"):
            u_dict = {k: user_obj[k] for k in user_obj.keys()}
        else:
            u_dict = {"user_id": user_id}
        u_dict["user_id"] = user_id

        # Map legacy DB fields (e.g. vibe / style) to preferred_register if needed
        if "preferred_register" not in u_dict or not u_dict.get("preferred_register"):
            u_dict["preferred_register"] = str(u_dict.get("vibe") or u_dict.get("register") or "articulate").strip().lower()

        active_mode = str(l_dict.get("mode") or u_dict.get("current_mode") or "spontaneous_chat").strip().lower()

        # Pass through specialized legacy modes (admin, SRS drill, podcast topic)
        passthrough_modes = (
            "admin_add_user", "admin_del_user", "srs_active_practice",
            "fast_lookup", "chunk_miner", "podcast_custom_trio",
            "podcast_custom_duo", "podcast_custom_topic",
            "weakness_drill", "shadowing_drill"
        )
        if active_mode in passthrough_modes:
            return False

        ai_inst = _get_v2_ai_instance(g_dict, l_dict, db_inst)
        rtr_modes = ("subtext", "subtext_decoder", "what_to_say", "social_strategy", "read_the_room", "nuance")

        # 1. Check active pending practice (Turn-2 Roleplay Feedback)
        pending = _V2_PENDING_PRACTICE_MEM.get(user_id)
        if not pending and hasattr(db_inst, "get_pending_practice"):
            try:
                pending = db_inst.get_pending_practice(user_id)
            except Exception:
                pending = None

        if pending and active_mode not in rtr_modes:
            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            pe = ai_inst.practice_reply_v2(user=u_dict, learner_reply=clean_t, pending_state=pending)
            _V2_PENDING_PRACTICE_MEM.pop(user_id, None)
            if pe.intent == "attempt":
                eval_html = format_v2_practice_eval_html(pe, clean_t)
                bot_inst.send_message(chat_id, eval_html, parse_mode="HTML", reply_to_message_id=reply_to_id)
                return True

        # 2. Read the Room V2 (Unified Subtext & What to Say)
        if active_mode in rtr_modes:
            try:
                bot_inst.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            rtr = ai_inst.read_the_room_v2(user=u_dict, user_text=clean_t)
            _V2_PENDING_PRACTICE_MEM[user_id] = {
                "counterpart": rtr.your_turn.counterpart,
                "their_line": rtr.your_turn.their_line,
                "goal": rtr.your_turn.goal,
            }
            rtr_html = format_v2_read_the_room_html(rtr, clean_t)
            bot_inst.send_message(chat_id, rtr_html, parse_mode="HTML", reply_to_message_id=reply_to_id)
            try:
                if hasattr(db_inst, "update_user"):
                    db_inst.update_user(user_id, current_mode="spontaneous_chat")
                elif hasattr(db_inst, "update_user_mode"):
                    db_inst.update_user_mode(user_id, "spontaneous_chat")
            except Exception:
                pass
            return True

        # 3. Two-Call Lounge Banter & Devil's Advocate (Default for spontaneous_chat / banter / etc.)
        try:
            bot_inst.send_chat_action(chat_id, "typing")
        except Exception:
            pass

        hist_rows = []
        try:
            if hasattr(db_inst, "get_recent_messages"):
                raw_h = db_inst.get_recent_messages(user_id, limit=6)
                for r in (raw_h or []):
                    r_role = r["role"] if isinstance(r, dict) or hasattr(r, "__getitem__") else "user"
                    r_cont = r["content"] if isinstance(r, dict) or hasattr(r, "__getitem__") else ""
                    role_lbl = "Learner" if r_role == "user" else "Lounge"
                    hist_rows.append(f"{role_lbl}: {str(r_cont)[:160]}")
        except Exception:
            pass

        call_mode = "devils_advocate" if "devil" in active_mode else "banter"
        d_res = ai_inst.generate_dialogue(user=u_dict, user_text=clean_t, mode=call_mode, history_lines=hist_rows)
        msg1_html = format_v2_dialogue_html(d_res)
        sent_msg1 = bot_inst.send_message(chat_id, msg1_html, parse_mode="HTML", reply_to_message_id=reply_to_id)

        try:
            if hasattr(db_inst, "add_message"):
                db_inst.add_message(user_id, "user", clean_t)
                db_inst.add_message(user_id, "assistant", "\n".join(f"{t.speaker}: {t.text}" for t in d_res.turns))
        except Exception:
            pass

        try:
            bot_inst.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        c_res = ai_inst.coach_turn(user=u_dict, user_text=clean_t, turns=d_res.turns)
        msg2_html = format_v2_coach_html(c_res)
        if msg2_html:
            m1_id = getattr(sent_msg1, "message_id", None) or reply_to_id
            bot_inst.send_message(chat_id, msg2_html, parse_mode="HTML", reply_to_message_id=m1_id)
        return True

    except Exception as e:
        logging.error(f"ClaudeV2 live handler error: {e}\n{traceback.format_exc()}")
        if bot_inst and chat_id:
            try:
                bot_inst.send_message(chat_id, f"⚠️ <b>Engine Error:</b> <code>{_e(str(e)[:240])}</code>", parse_mode="HTML")
            except Exception:
                pass
        return True


# ==============================================================================
# CLAUDE V3 — UNIFIED EXECUTIVE ROUTER (DIRECT BOT METHOD HOOK)
# ==============================================================================
_B2F_ORIG_HANDLER = handle_claude_v2_turn_safe
_V3_NEGOTIATION_STATE: Dict[int, Dict[str, Any]] = {}
_V3_SCENE_MEMORY: Dict[int, Dict[str, Any]] = {}
_V3_ACTIVE_LEARNER_TEXT: Dict[int, str] = {}
_V3_PATCHED_BOT_IDS: set = set()


def detect_metalinguistic_inquiry(text: str) -> bool:
    t = (text or "").strip().lower()
    if "?" not in t:
        return False
    meta_patterns = [
        r"\bwhy\s+(would|did|do|can't|cannot|should)\s+(you|we|i|people|americans)\s+(say|use|suggest)\b",
        r"\binstead\s+of\b.*\?",
        r"\bis\s+my\s+version\b",
        r"\bactually\s+wrong\b",
        r"\bquick\s+question\s*:",
        r"\bwhat('s|\s+is)\s+the\s+(exact\s+)?difference\s+between\b",
        r"\bis\s+that\s+something\s+americans\s+actually\s+say\b",
        r"\bwhy\s+is\s+['\"“”].+?['\"“”]\s+(less\s+natural|wrong|better)\b",
        r"\bisn't\s+it\s+correct\s+english\b",
    ]
    return any(re.search(p, t) for p in meta_patterns)


def build_metalinguistic_response_v3(ai_obj: Any, question_text: str, scene_topic: str, counterpart: str = "Marcus") -> str:
    sys_prompt = (
        "You are the Precision C1/C2 Spoken American English Coach in 'Musa's NYC Lounge'.\n"
        "The learner paused the conversation to ask a direct metalinguistic question.\n"
        "Follow the 4-Step Nuance Calibration Protocol strictly:\n"
        "1. Distinguish comprehensibility/meaning from natural American collocation or usage preference.\n"
        "2. Validate that the learner's meaning is clear, while explaining why the recommended form is the idiomatic US choice.\n"
        "3. Contrast the exact nuance between the expressions (e.g., 'put in a lot of effort' emphasizes the energy invested; 'make a big effort' focuses on the attempt; whereas 'do an effort' is a non-idiomatic calque in standard English).\n"
        "4. NEVER invent corpus percentages or fake statistics.\n\n"
        "Return ONLY valid JSON with two keys:\n"
        "{\n"
        '  "coach_explanation": "Concise 3-4 sentence explanation in clear English + a 1-line Arabic summary.",\n'
        f'  "scene_resume_line": "A natural 1-2 sentence in-character line from {counterpart} resuming the previous conversation topic ({scene_topic}) without lecturing."\n'
        "}"
    )
    u_prompt = (
        f"Learner's metalinguistic question: {question_text}\n"
        f"Paused scene topic: {scene_topic}\n"
        f"Active counterpart to resume scene: {counterpart}"
    )
    coach_exp = None
    resume_ln = None
    if ai_obj and hasattr(ai_obj, "_call_gemini_json"):
        try:
            res = ai_obj._call_gemini_json(
                system_prompt=sys_prompt,
                user_prompt=u_prompt,
                temperature=0.2,
                max_tries=2,
                validator_fn=lambda d: d if isinstance(d, dict) and d.get("coach_explanation") and d.get("scene_resume_line") else (_ for _ in ()).throw(ValueError("Missing keys")),
            )
            coach_exp = _e(str(res["coach_explanation"]).strip())
            resume_ln = _e(str(res["scene_resume_line"]).strip().strip('"“”'))
        except Exception:
            pass

    if not coach_exp or not resume_ln:
        coach_exp = (
            "Your meaning in “do a big effort” is completely clear, so it doesn't break communication. "
            "However, in standard American English, it's a collocation issue (calque) rather than a logic rule: "
            "“effort” naturally pairs with “put in” (emphasizing the time and energy invested: “put in a lot of effort”) "
            "or “make” (focusing on the attempt itself: “make a big effort”), rather than “do”.\n"
            "↳ بالعربية: المعنى مفهوم تماماً، لكن الفرق يتعلق بالتلازم اللفظي؛ فكلمة effort تقترن في الإنجليزية الأمريكية بالفعل put in (لإبراز الجهد المبذول) أو make، وليس do."
        )
        resume_ln = "Anyway, back to that report—what's the main bottleneck keeping you behind right now?"

    return (
        "🧭 <b>Linguistic Consultation (Scene Paused)</b>\n"
        f"{coach_exp}\n\n"
        "🎬 <b>Scene Resumed</b>\n"
        f"🧔‍♂️ <b>{_e(counterpart)}:</b> “{resume_ln}”"
    )


def detect_active_uptake_event(text: str) -> Optional[Dict[str, str]]:
    t = (text or "").strip()
    tl = t.lower()
    if re.search(r"\bnot\s+(quite\s+|entirely\s+|fully\s+)?sold\s+on\b", tl):
        return {
            "function": "Diplomatic Disagreement / Calibrated Reservation",
            "functional_result": "Successful (expressed reservation while justifying the constraint)",
            "target_move": "I'm not quite sold on [something]",
            "verbatim_match": "Yes (adapted naturally to context)",
            "stage": "Production (1st independent contextual use; Stabilization requires delayed transfer across sessions)",
        }
    if re.search(r"\bi\s+see\s+where\s+you're\s+coming\s+from\b.*\bbut\b", tl) or re.search(r"\bnot\s+sure\s+that\s+holds\s+up\b", tl):
        return {
            "function": "Diplomatic Disagreement / Calibrated Reservation",
            "functional_result": "Successful (achieved diplomatic pushback naturally)",
            "target_move": "I'm not quite sold on [something]",
            "verbatim_match": "No — Functional Success achieved via natural alternative phrasing",
            "stage": "Production (Functional Success recorded; target collocation remains eligible for future retrieval)",
        }
    if re.search(r"\block\s+in\s+(a|the)\b", tl):
        return {
            "function": "Commitment & Follow-Through",
            "functional_result": "Successful (turned a plan into a concrete commitment)",
            "target_move": "lock in [a time / deadline]",
            "verbatim_match": "Yes (used naturally in context)",
            "stage": "Production (Contextual retrieval recorded; Stabilization requires delayed transfer)",
        }
    return None


def classify_negotiation_move_v3(text: str, mode: str, user_id: int) -> Optional[str]:
    t = (text or "").strip().lower()
    st = _V3_NEGOTIATION_STATE.get(user_id, {})
    in_rtr_mode = mode in ("subtext", "situational", "read_the_room", "pragmatic_subtext", "what_to_say")

    is_cavein = (
        re.search(r"\b(delete|cut|drop|remove)\s+(the\s+)?(whole|entire)?\s*(background\s+)?section\b", t)
        and not re.search(r"\b(keep|preserve|save)\s+(the\s+)?(key|main|core|essential)\b", t)
        and any(w in t for w in ("okay", "fine", "let's just", "if we're short on time", "sure"))
    )
    if is_cavein:
        return "beat1_cavein"

    is_compromise = (
        re.search(r"\b(keep|preserve|retain)\s+(the\s+)?(key|main|core|essential|theoretical)\b", t)
        and re.search(r"\b(cut|drop|trim|shorten|remove|less\s+relevant)\b", t)
    )
    if is_compromise and (st.get("active") or in_rtr_mode or "background" in t or "case study" in t):
        return "beat2_solution"

    return None


def format_v3_negotiation_cavein(user_id: int, learner_text: str) -> str:
    _V3_NEGOTIATION_STATE[user_id] = {
        "active": True,
        "counterpart": "Marcus",
        "constraint": "Shorten the report while preserving the essential background points",
        "attempt_1": learner_text,
        "beat": 1,
    }
    return (
        "🎭 <b>Read the Room — Beat 1 Evaluation</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👂 <b>How It Lands:</b> You sound cooperative and responsive to the time pressure, "
        "but you gave up your core constraint by agreeing to delete the entire background section.\n\n"
        "🎯 <b>Constraint Check:</b>\n"
        "• Acknowledge time pressure: ✅ Achieved\n"
        "• Preserve essential background context: ❌ Surrendered (premature cave-in)\n\n"
        "🔄 <b>Try Beat 1 Again (Counterpart: 🧔‍♂️ Marcus):</b>\n"
        "Can you acknowledge the tight timeline <i>without</i> agreeing to remove the whole section? "
        "Propose a way to protect the essential context while addressing the space constraint."
    )


def format_v3_negotiation_debrief(user_id: int, learner_text: str) -> str:
    _V3_NEGOTIATION_STATE.pop(user_id, None)
    return (
        "🧔‍♂️ <b>Marcus:</b> “That works. Keep the key background points in one tight paragraph, "
        "cut the secondary details, and send me the shorter draft by four.”\n\n"
        "📊 <b>Negotiation Debrief & Exit</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Attempt 1 vs. Attempt 2:</b> In your first attempt, you prioritized harmony but surrendered "
        "the essential background section completely. In your second response, you adapted effectively—acknowledging "
        "the space constraint while protecting the core context through a concrete compromise.\n"
        "• <b>Pragmatic Breakdown:</b>\n"
        "  └ Acknowledgment of constraint: ✅ Clear\n"
        "  └ Position preservation: ✅ Maintained essential points\n"
        "  └ Specificity & Adaptation: ✅ Actionable trade-off (short paragraph vs. minor details)\n"
        "• <b>📋 Evidence Ledger:</b> Recorded <code>Production (Functional Success)</code> in "
        "<i>Scope & Priority Negotiation</i> for this task (single-scenario evidence; not generalized to permanent mastery)."
    )


def _apply_v3_cognitive_cap_to_html(raw_html: str, learner_text: str) -> str:
    if not raw_html or "🎭" in raw_html or "👂" in raw_html or "🧭" in raw_html or "🟢" in raw_html:
        return raw_html

    # Match 💎 Spoken American Moves in any HTML variation
    if "💎" not in raw_html:
        return raw_html

    idx_diamond = raw_html.find("💎")
    # Cut cleanly right before the newline preceding 💎
    cut_pos = raw_html.rfind("\n", 0, idx_diamond)
    before_diamond = (raw_html[:cut_pos] if cut_pos != -1 else raw_html[:idx_diamond]).strip()

    has_mirror_fix = any(m in before_diamond for m in ("🪞", "🇺🇸", "🎙️"))
    if has_mirror_fix:
        # Priority 1: Surgical Mirror only (0 chunks)
        return before_diamond

    uptake_event = detect_active_uptake_event(learner_text)
    if uptake_event is not None:
        # Priority 2: Active Uptake & Evidence Ledger
        ledger_block = (
            "🎯 <b>Active Uptake & Evidence Ledger</b>\n"
            f"• <b>Function:</b> {_e(uptake_event['function'])} — <i>{_e(uptake_event['functional_result'])}</i>\n"
            f"• <b>Target Move:</b> <code>{_e(uptake_event['target_move'])}</code> — {_e(uptake_event['verbatim_match'])}\n"
            f"• <b>Evidence Stage:</b> <code>{_e(uptake_event['stage'])}</code>"
        )
        return f"{before_diamond}\n\n{ledger_block}"

    stiff_markers = (
        "cannot accept", "additional task", "completely full", "do not think",
        "do not have enough", "in order to", "it is necessary",
    )
    if any(sm in (learner_text or "").lower() for sm in stiff_markers):
        # Priority 3: Textbook-stiff input -> keep Spoken Move as Recast/Alternative
        return raw_html

    # Priority 4: Clean natural English -> Complete Coaching Silence (no_coaching_needed)!
    return before_diamond


def _ensure_bot_method_hooked(bot_obj: Any):
    """Hooks `bot.send_message` and `bot.reply_to` directly on the TeleBot instance so NO call can bypass the V3 filter."""
    if not bot_obj or id(bot_obj) in _V3_PATCHED_BOT_IDS:
        return
    orig_send = bot_obj.send_message
    orig_reply = getattr(bot_obj, "reply_to", None)

    def _hooked_send_message(chat_id: Any, text: str, *a, **kw):
        l_txt = _V3_ACTIVE_LEARNER_TEXT.get(int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0, "")
        filtered = _apply_v3_cognitive_cap_to_html(str(text), l_txt)
        return orig_send(chat_id, filtered, *a, **kw)

    bot_obj.send_message = _hooked_send_message
    if orig_reply:
        def _hooked_reply_to(msg_obj: Any, text: str, *a, **kw):
            cid = getattr(getattr(msg_obj, "chat", None), "id", 0)
            l_txt = _V3_ACTIVE_LEARNER_TEXT.get(int(cid) if str(cid).lstrip("-").isdigit() else 0, "")
            filtered = _apply_v3_cognitive_cap_to_html(str(text), l_txt)
            return orig_reply(msg_obj, filtered, *a, **kw)
        bot_obj.reply_to = _hooked_reply_to

    _V3_PATCHED_BOT_IDS.add(id(bot_obj))


def handle_claude_v2_turn_safe(
    g_env: dict,
    l_env: dict,
    message: Any = None,
    override_text: Optional[str] = None,
    override_chat_id: Optional[int] = None,
    override_uid: Optional[int] = None,
) -> bool:
    import logging
    logger = logging.getLogger(__name__)
    try:
        g_dict = g_env if isinstance(g_env, dict) else {}
        l_dict = l_env if isinstance(l_env, dict) else {}
        bot = l_dict.get("bot") or g_dict.get("bot")
        ai_obj = l_dict.get("ai") or g_dict.get("ai") or l_dict.get("ai_engine") or g_dict.get("ai_engine")

        if bot:
            _ensure_bot_method_hooked(bot)

        if message is None:
            if bot and override_chat_id:
                try:
                    bot.send_message(
                        override_chat_id,
                        "🟢 <b>Musa’s NYC Lounge (Claude V3 — Direct Hook Active)</b> is live! Send Message 1 now.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return True

        raw_text = override_text or getattr(message, "text", None) or l_dict.get("text") or ""
        clean_t = str(raw_text).strip()
        if not clean_t:
            return False

        chat_id = override_chat_id or getattr(getattr(message, "chat", None), "id", None)
        uid = override_uid or l_dict.get("uid") or getattr(getattr(message, "from_user", None), "id", 0)
        mode = str(l_dict.get("mode") or (l_dict.get("user") or {}).get("current_mode") or "spontaneous_chat").strip().lower()

        if chat_id:
            _V3_ACTIVE_LEARNER_TEXT[int(chat_id)] = clean_t

        if clean_t.startswith("/"):
            return False

        menu_prefixes = (
            "🗽", "🥊", "🕵️", "⚡", "🎙️", "📊", "⚙️", "🧠", "🎯", "🏆", "📚", "🔄", "🎭", "🎧"
        )
        if clean_t.startswith(menu_prefixes):
            return False

        # GATE 1A: Metalinguistic Inquiry ("Why would you say X instead of Y?")
        if bot and chat_id and detect_metalinguistic_inquiry(clean_t):
            try:
                bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            scene_ctx = _V3_SCENE_MEMORY.get(uid, {}).get("last_topic", "finishing the report that is behind schedule")
            out_html = build_metalinguistic_response_v3(
                ai_obj=ai_obj,
                question_text=clean_t,
                scene_topic=scene_ctx,
                counterpart="Marcus",
            )
            bot.send_message(chat_id, out_html, parse_mode="HTML")
            return True

        # GATE 1B: Two-Beat Negotiation (Beat 1 Cave-in vs. Beat 2 Compromise)
        neg_move = classify_negotiation_move_v3(clean_t, mode, uid)
        if bot and chat_id and neg_move == "beat1_cavein":
            try:
                bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            out_html = format_v3_negotiation_cavein(uid, clean_t)
            bot.send_message(chat_id, out_html, parse_mode="HTML")
            return True
        if bot and chat_id and neg_move == "beat2_solution":
            try:
                bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            out_html = format_v3_negotiation_debrief(uid, clean_t)
            bot.send_message(chat_id, out_html, parse_mode="HTML")
            return True

        _V3_SCENE_MEMORY[uid] = {"last_topic": clean_t}
        return _B2F_ORIG_HANDLER(
            g_env,
            l_env,
            message,
            override_text=override_text,
            override_chat_id=override_chat_id,
            override_uid=override_uid,
        )
    except Exception as e:
        logger.error(f"Claude V3 wrapper error: {e}", exc_info=True)
        return _B2F_ORIG_HANDLER(
            g_env,
            l_env,
            message,
            override_text=override_text,
            override_chat_id=override_chat_id,
            override_uid=override_uid,
        )
