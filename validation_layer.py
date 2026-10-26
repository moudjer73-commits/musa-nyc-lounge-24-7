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
