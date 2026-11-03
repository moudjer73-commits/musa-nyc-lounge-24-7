"""validation.py (S0 section 5.1, owner Claude): the deterministic gate between the Analyst and the database.

"LLMs propose, code decides." The Analyst's raw JSON goes in; an ``AnalysisV2`` holding only validated
survivors comes out, plus the priority score of each survivor (``ScoredDiagnostic``) for the policy
resolver. Every drop is recorded as a ``DropRecord(ref, reason)``.

``ref`` grammar (stable, so eval and logs can group by it):

* ``d<i>``             a whole diagnostic (``i`` = 0-based position in the raw list) was dropped
* ``d<i>.<field>``     one field of a surviving diagnostic was removed (``explanation_ar``, ``l1``, ...)
* ``t<i>``             a target assessment was dropped or lost credit
* ``analysis`` / ``communicative`` / ``p0`` / ``diagnostics``   a top-level part was unusable

Steps run in the S0 order: 1 schema, 2 domain and modality, 3 normalize and tokenize, 4 span,
5 no-op, 6 classification, 7 pattern code and Arabic notes, 8 abstention, 9 priority and overlap,
10 target assessments, 11 ids. A diagnostic is dropped at the first step it fails.

Linguistic accuracy rule (S2): code never lets a note or an L1 claim assert an ungrammatical Standard
Arabic frame. Standard Arabic ناقش is directly transitive (ناقشَ الموضوعَ), exactly like English
"discuss the topic". "Discuss about" is an English-internal analogy with "talk about"; it is not an
Arabic «ناقش في» transfer, so the L1 claim is stripped and a note asserting that frame is removed.

Python 3.11 compatible. No imports from config.py (Gemini-owned, reads the environment): tunable
values live in ``ValidationConfig`` and the caller passes ``config.ABSTAIN_THRESHOLD`` when it exists.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Collection, Container, Iterable, Mapping, Optional, Sequence

from pydantic import ValidationError

from schemas import (
    DOMAIN_BY_KIND,
    AnalysisV2,
    CallMetaDict,
    Classification,
    CommunicativeAssessment,
    DiagnosticV2,
    Domain,
    DropReason,
    DropRecord,
    IssueKind,
    P0Signal,
    ProductionEvidence,
    RepairScope,
    Severity,
    TargetUseAssessment,
    classification_from_kind,
    confidence_band_for,
)

# ------------------------------------------------------------------------------ configuration

ABSTAIN_THRESHOLD_DEFAULT = 0.80  # S0 5.1 step 8; a placeholder until the eval harness calibrates it
PRIORITY_WEIGHTS: dict[str, float] = {"sev": 0.30, "imp": 0.20, "learn": 0.20, "freq": 0.15, "conf": 0.15}
SEVERITY_WEIGHT: dict[Severity, float] = {Severity.LOW: 0.33, Severity.MEDIUM: 0.67, Severity.HIGH: 1.0}
FREQ_CAP = 4  # S0 5.2: freq = min(prior occurrences, 4) / 4
TEXT_MODALITY = "text"
VOICE_MODALITY = "voice"


@dataclass(frozen=True)
class ValidationConfig:
    abstain_threshold: float = ABSTAIN_THRESHOLD_DEFAULT


DEFAULT_CONFIG = ValidationConfig()

_PLACEHOLDER_ID = "dg_00000000"

# Taxonomy the pattern-code check accepts. The live Analyst prompt (ai_engine.py) and the coach
# taxonomy (prompts/_partials/l1_taxonomy.md) use two overlapping vocabularies; V3.0 accepts both until
# S3 rewrites the prompts around one of them.
L1_PATTERN_CODES = frozenset(
    {
        # coach/checks.py (ported)
        "COLLOC_LIGHT_VERB", "PREP_CALQUE", "IDIOM_CALQUE", "SEMANTIC_RANGE", "DIRECTNESS",
        "RHETORICAL_INTENSITY", "OVER_FORMAL", "OVER_SOFTENING", "ADDRESS_TERM", "DISCOURSE_MARKER_GAP",
        "SYNTAX_CALQUE", "COUNTABILITY", "TENSE_ASPECT", "SPEECH_ACT_RITUAL",
        # live Analyst prompt (ai_engine.py)
        "COLLOC_PREP_VERB", "COLLOC_OPEN_CLOSE", "COLLOC_INTENSIFIER", "GRAM_RESUMPTIVE_PRONOUN",
        "GRAM_DEF_ARTICLE_ABSTRACT", "DISC_WRITTEN_CONNECTORS", "PRAG_DIRECT_IMPERATIVE_REQUEST",
        "PRAG_LITERAL_REFUSAL",
    }
)
PATTERN_ID_MAX_LEN = 80
_PATTERN_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?::[A-Za-z0-9_.\-]+)?$")

# ------------------------------------------------------------------------------ step 3: text

_QUOTE_MAP = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'", "′": "'",
        "´": "'", "`": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    }
)


def normalize_text(text: str) -> str:
    """NFKC, curly quotes and apostrophes to straight, casefold, whitespace collapsed (S0 5.1 step 3)."""
    folded = unicodedata.normalize("NFKC", text.translate(_QUOTE_MAP)).casefold()
    return " ".join(folded.split())


_WORD_CHAR = r"(?:\w|[̀-ͯؐ-ًؚ-ٰٟۖ-ۭ])"
_APOSTROPHE = "['‘’ʼ′]"
_TOKEN_RE = re.compile(rf"{_WORD_CHAR}+(?:{_APOSTROPHE}{_WORD_CHAR}+)*")


@dataclass(frozen=True)
class Token:
    text: str  # slice of the ORIGINAL string
    norm: str  # normalize_text(text)
    start: int  # offsets into the original string
    end: int


def tokenize(text: str) -> list[Token]:
    """Unicode word characters plus apostrophes ("don't" is one token, Arabic works), with offsets
    into the original string. Normalisation is per token so offsets never drift (casefold can change
    a string's length)."""
    return [Token(m.group(0), normalize_text(m.group(0)), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _find_run(haystack: Sequence[Token], needle: Sequence[str]) -> Optional[int]:
    n = len(needle)
    if n == 0 or n > len(haystack):
        return None
    norms = [t.norm for t in haystack]
    for i in range(len(norms) - n + 1):
        if norms[i : i + n] == list(needle):
            return i
    return None


@dataclass(frozen=True)
class SpanMatch:
    start: int
    end: int


def resolve_span(learner_text: str, span_text: str) -> tuple[Optional[SpanMatch], Optional[DropReason]]:
    """Step 4. The span's tokens must equal a contiguous run of the learner's tokens; first match wins.

    ``SPAN_BOUNDARY`` when the characters occur but not on token boundaries ("in" inside "thinking");
    ``SPAN_MISSING`` when they do not occur at all.
    """
    needle = [t.norm for t in tokenize(span_text)]
    if not needle:
        return None, DropReason.SPAN_MISSING
    haystack = tokenize(learner_text)
    first = _find_run(haystack, needle)
    if first is not None:
        return SpanMatch(haystack[first].start, haystack[first + len(needle) - 1].end), None
    wanted = normalize_text(span_text)
    if wanted and wanted in normalize_text(learner_text):
        return None, DropReason.SPAN_BOUNDARY
    return None, DropReason.SPAN_MISSING


# ------------------------------------------------------------------------------ echo guard (ported)

_STOP = frozenset(
    "a an the to of i you he she it we they me my your his her our their is are am was were be been "
    "and or but so that this there here in on at for with as if do does did have has had will would can could "
    "just really very pretty".split()
)


def _stem(tok: str) -> str:
    tok = tok.replace("'s", "")
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            tok = tok[: -len(suf)]
            break
    if tok.endswith("e") and len(tok) > 3:
        tok = tok[:-1]
    return tok


def _content_stems(text: str) -> list[str]:
    return [_stem(t.norm) for t in tokenize(text) if t.norm not in _STOP and len(t.norm) > 1]


def _contains_phrase(text: str, phrase: str) -> bool:
    wanted = [t.norm for t in tokenize(phrase)]
    return bool(wanted) and _find_run(tokenize(text), wanted) is not None


def looks_like_echo(form_used: str, recent_texts: Iterable[str]) -> bool:
    """Did the learner just repeat words that the character or a card showed them? (coach/checks.py)"""
    texts = list(recent_texts)
    need = _content_stems(form_used)
    if not need:
        return any(_contains_phrase(t, form_used) for t in texts)
    for t in texts:
        have = set(_content_stems(t))
        if all(n in have for n in need):
            return True
    return False


# ------------------------------------------------------------------------------ Arabic notes (ported)

_AR_DIACRITICS = re.compile("[ً-ٰٟـ]")
ARABIC_NOTE_MAX_CHARS = 200
_AR_LETTER = "ء-ي"


def _ar_norm(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي"), ("ة", "ه")):
        text = text.replace(a, b)
    return text


_AR_BANNED_WORDS = tuple(_ar_norm(w) for w in ("خطأ", "أخطاء", "غلط", "ركيك", "ضعيف"))
_AR_BANNED_PHRASES = tuple(
    _ar_norm(p)
    for p in (
        "لا يقال أبدًا",
        "لا يُقال",  # absolutes; the approved wording is «أقل شيوعًا»
        "العرب يخطئون",
        "تفكير عربي",
        "ترجمة حرفية سيئة",
        "ترجمة سيئة",
    )
)
_AR_PREFIXES = r"(?:ال|وال|بال|لل|ب|و|ل|ف|ك)?"
_AR_BANNED_RE = re.compile(
    rf"(?<![{_AR_LETTER}])" + _AR_PREFIXES + r"(?:" + "|".join(_AR_BANNED_WORDS) + rf")(?![{_AR_LETTER}])"
)
_AR_GENERALISE_RE = re.compile(rf"(?<![{_AR_LETTER}])العرب(?![{_AR_LETTER}])")
_AR_LETTER_RE = re.compile(f"[{_AR_LETTER}]")


def arabic_note_problems(note: str) -> list[str]:
    """Reasons an Arabic coaching note must not be shown (empty list = fine)."""
    if not note.strip():
        return []
    problems: list[str] = []
    if len(note) > ARABIC_NOTE_MAX_CHARS:
        problems.append("too long")
    if not _AR_LETTER_RE.search(note):
        problems.append("not Arabic")
    norm = _ar_norm(note)
    if _AR_BANNED_RE.search(norm):
        problems.append("stigmatising wording")
    if any(p in norm for p in _AR_BANNED_PHRASES):
        problems.append("false absolute or stigmatising phrase")
    if _AR_GENERALISE_RE.search(norm):
        problems.append("generalises about Arabs")
    return problems


# ------------------------------------------------------------------------------ L1 accuracy rules

# Standard Arabic ناقش (form III) is directly transitive: ناقشَ الموضوعَ. A claimed frame "ناقش + في/عن/
# على/حول/ب + topic" is not Standard Arabic, so no note may assert it. ناقشه في الأمر (person as the
# direct object, topic in a prepositional phrase) and the reciprocal form VI تناقشوا في/حول الموضوع are
# valid and are deliberately not matched: the verb token must be followed by whitespace and then the
# preposition, and a prefixed ت (form VI) never matches.
_AR_NAQASHA_PREPS = "|".join(_ar_norm(p) for p in ("في", "عن", "على", "حول", "ب"))
_AR_NAQASHA_PREP_RE = re.compile(
    rf"(?<![{_AR_LETTER}])[وفس]?[ينا]?ناقش(?:ت|ا|وا|نا|تم|ن|ون|ان)?\s+(?:{_AR_NAQASHA_PREPS})(?![{_AR_LETTER}])"
)

# English patterns that are intralingual (L2-internal analogy), never an Arabic transfer.
# "discuss about" follows "talk about / argue about / think about" (S2 linguistic accuracy rule).
_DISCUSS_ABOUT_RE = re.compile(r"\bdiscuss(?:es|ed|ing)?\s+about\b", re.IGNORECASE)
INTRALINGUAL_ONLY_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (("discuss_about", _DISCUSS_ABOUT_RE),)


def has_false_arabic_frame(text: Optional[str]) -> bool:
    """True when ``text`` asserts a Standard Arabic frame that does not exist (currently ناقش + preposition)."""
    if not text:
        return False
    return bool(_AR_NAQASHA_PREP_RE.search(_ar_norm(text)))


def is_intralingual_only(original_span: str) -> Optional[str]:
    """Id of the first rule saying this English error must not be attributed to Arabic, else None."""
    for rule_id, pattern in INTRALINGUAL_ONLY_RULES:
        if pattern.search(original_span):
            return rule_id
    return None


# ------------------------------------------------------------------------------ priority score (5.2)


def projection_family(kind: str, relevant_context_family: Optional[str]) -> str:
    """Family ``error_pattern_projection`` files a finding under (the v2 rule, unchanged)."""
    if kind in (IssueKind.PRAGMATICS.value, IssueKind.REGISTER.value):
        return str(relevant_context_family or "global")
    return "global"


def applicable_confidence(linguistic: Optional[float], pragmatic: Optional[float]) -> Optional[float]:
    """Linguistic and discourse use ``linguistic_confidence``, pragmatic uses ``pragmatic_confidence``,
    the minimum when both are set. Only the set value counts when one is None."""
    present = [v for v in (linguistic, pragmatic) if v is not None]
    return min(present) if present else None


def priority_score(
    severity: Severity,
    communicative_impact: int,
    learning_value: int,
    prior_occurrences: int,
    confidence: float,
) -> float:
    """100 x (0.30 sev + 0.20 imp + 0.20 learn + 0.15 freq + 0.15 conf), rounded to 2 places."""
    w = PRIORITY_WEIGHTS
    freq = min(max(int(prior_occurrences), 0), FREQ_CAP) / FREQ_CAP
    raw = (
        w["sev"] * SEVERITY_WEIGHT[Severity(severity)]
        + w["imp"] * (int(communicative_impact) / 3.0)
        + w["learn"] * (int(learning_value) / 3.0)
        + w["freq"] * freq
        + w["conf"] * float(confidence)
    )
    return round(min(max(100.0 * raw, 0.0), 100.0), 2)


def prior_occurrence_count(
    prior_counts: Optional[Mapping[str, int]], learner_id: Optional[int], pattern_id: str, family: str
) -> int:
    """Count before this turn. Accepts the ``database.get_authoritative_occurrence_counts`` shape
    (``"<learner>:<pattern>:<family>"``) or ``"<pattern>:<family>"`` keys."""
    if not prior_counts:
        return 0
    keys = []
    if learner_id is not None:
        keys.append(f"{learner_id}:{pattern_id}:{family}")
    keys.append(f"{pattern_id}:{family}")
    for key in keys:
        if key in prior_counts:
            return max(int(prior_counts[key]), 0)
    return 0


@dataclass(frozen=True)
class ScoredDiagnostic:
    diagnostic: DiagnosticV2
    score: float
    family: str  # error_pattern_projection family
    confidence: float  # the applicable confidence
    prior_occurrences: int
    raw_index: int


@dataclass
class ValidationResult:
    analysis: AnalysisV2
    scored: list[ScoredDiagnostic]  # same order as analysis.diagnostics (score descending)
    notes: list[str] = field(default_factory=list)  # "ref: reason: detail" lines for logs and eval

    @property
    def dropped(self) -> list[DropRecord]:
        return self.analysis.dropped


# ------------------------------------------------------------------------------ step 11: ids


def diagnostic_id_for(turn_event_id: str, ordinal: int, attempt: int = 0) -> str:
    """``dg_`` + 8 hex, deterministic in (turn, raw position, attempt) so re-validating the same raw
    analysis yields the same ids."""
    digest = hashlib.sha1(f"{turn_event_id}|{ordinal}|{attempt}".encode("utf-8")).hexdigest()
    return "dg_" + digest[:8]


def assign_diagnostic_id(turn_event_id: str, ordinal: int, taken: Container[str], also_taken: Container[str] = ()) -> str:
    """First free ``dg_`` id. ``taken`` and ``also_taken`` need only support ``in``, so a lazy database-backed
    container (``database.TakenDiagnosticIds``) works as well as a set."""
    attempt = 0
    while True:
        candidate = diagnostic_id_for(turn_event_id, ordinal, attempt)
        if candidate not in taken and candidate not in also_taken:
            return candidate
        attempt += 1


# ------------------------------------------------------------------------------ the pipeline

_AUDIO_CLAIM_RE = re.compile(
    r"\b(pronunciation|pronounce[sd]?|pronouncing|mispronounc\w*|intonation|prosod\w*|accent(?:ed)?|"
    r"stress(?:ed)? on the|syllable|vowel|phoneme\w*|pitch|tone of voice|rising tone|falling tone|enunciat\w*)\b",
    re.IGNORECASE,
)
_AUDIO_FIELDS = ("explanation_en", "listener_effect", "intended_meaning")
_L1_FIELDS = ("l1_transfer_likely", "l1_pattern", "l1_source_ar", "l1_transfer_evidence")
_OPTIONAL_TEXT_FIELDS = ("explanation_ar", "l1_source_ar", "l1_transfer_evidence", "listener_effect")
_PRE_TRIM = (("explanation_ar", 200, DropReason.NOTE_TONE), ("l1_source_ar", 80, DropReason.NOTE_TONE),
             ("l1_pattern", 64, DropReason.PATTERN_CODE))


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


class _Drop(Exception):
    """Internal: a whole diagnostic fails at the current step."""

    def __init__(self, reason: DropReason, detail: str = "") -> None:
        super().__init__(detail or reason.value)
        self.reason = reason
        self.detail = detail


@dataclass
class _Candidate:
    index: int
    diagnostic: DiagnosticV2
    claimed_classification: Classification
    claimed_domain: Optional[Domain]


class _Recorder:
    def __init__(self) -> None:
        self.dropped: list[DropRecord] = []
        self.notes: list[str] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, ref: str, reason: DropReason, detail: str = "") -> None:
        key = (ref, reason.value)
        if key in self._seen:
            return
        self._seen.add(key)
        self.dropped.append(DropRecord(ref=ref, reason=reason))
        self.notes.append(f"{ref}: {reason.value}" + (f": {detail}" if detail else ""))


def _strip_l1(diag: DiagnosticV2) -> DiagnosticV2:
    return diag.model_copy(
        update={"l1_transfer_likely": False, "l1_pattern": None, "l1_source_ar": None, "l1_transfer_evidence": None}
    )


def _step1_schema(index: int, raw: Any, rec: _Recorder) -> _Candidate:
    """Step 1. Parse one diagnostic on its own. The LLM's classification, domain and acoustic confidence
    are held back for steps 2 and 6 (so a wrong classification is classification_mismatch, not schema);
    the id, the offsets and the confidence band are code's to set, so anything the LLM sent is ignored."""
    ref = f"d{index}"
    if not isinstance(raw, Mapping):
        raise _Drop(DropReason.SCHEMA, "diagnostic is not an object")
    data = dict(raw)
    claimed_acoustic = data.pop("acoustic_confidence", None)
    claimed_class_raw = data.pop("classification", None)
    claimed_domain_raw = data.pop("domain", None)
    for key in ("diagnostic_id", "span_start", "span_end", "confidence_score", "confidence_band"):
        data.pop(key, None)

    try:
        kind = IssueKind(data.get("kind"))
    except ValueError:
        raise _Drop(DropReason.SCHEMA, f"unknown kind {data.get('kind')!r}") from None
    derived = classification_from_kind(kind.value)
    if derived is None:
        raise _Drop(DropReason.CLASSIFICATION_MISMATCH, "insufficient_context is never a finding")

    try:
        claimed_class = derived if claimed_class_raw is None else Classification(claimed_class_raw)
    except ValueError:
        raise _Drop(DropReason.SCHEMA, f"unknown classification {claimed_class_raw!r}") from None
    try:
        claimed_domain = None if claimed_domain_raw is None else Domain(claimed_domain_raw)
    except ValueError:
        raise _Drop(DropReason.SCHEMA, f"unknown domain {claimed_domain_raw!r}") from None

    # Over-long optional notes are blanked (the v2 behaviour), not a reason to lose the finding.
    for name, limit, reason in _PRE_TRIM:
        value = data.get(name)
        if isinstance(value, str) and len(value.strip()) > limit:
            data[name] = None
            rec.add(f"{ref}.{name}", reason, f"longer than {limit} characters")

    confidence = applicable_confidence(_as_float(data.get("linguistic_confidence")), _as_float(data.get("pragmatic_confidence")))
    if confidence is not None:
        data["confidence_score"] = confidence
        data["confidence_band"] = confidence_band_for(confidence).value
    if claimed_acoustic is not None:
        data["acoustic_confidence"] = None  # step 2 rejects the claim itself

    domain = claimed_domain or DOMAIN_BY_KIND.get(kind.value, Domain.LINGUISTIC)
    data.update(
        diagnostic_id=_PLACEHOLDER_ID,
        span_start=0,
        span_end=0,
        classification=derived.value,
        domain=domain.value,
    )
    try:
        diag = DiagnosticV2.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _Drop(DropReason.SCHEMA, f"{'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('msg')}") from None
    if claimed_acoustic is not None:
        diag = diag.model_copy(update={"acoustic_confidence": claimed_acoustic})
    return _Candidate(index, diag, claimed_class, claimed_domain)


def _step2_domain_modality(cand: _Candidate, input_modality: str) -> None:
    d = cand.diagnostic
    if d.domain == Domain.PHONOLOGICAL:
        raise _Drop(DropReason.DOMAIN_MODALITY, "phonological findings are reserved for V3.1")
    if d.acoustic_confidence is not None:
        raise _Drop(DropReason.DOMAIN_MODALITY, "acoustic_confidence must be null in V3.0")
    if input_modality == TEXT_MODALITY:
        for name in _AUDIO_FIELDS:
            value = getattr(d, name, None)
            if isinstance(value, str) and _AUDIO_CLAIM_RE.search(value):
                raise _Drop(DropReason.DOMAIN_MODALITY, f"{name} makes a claim that needs audio, but the input was text")


def _step4_span(cand: _Candidate, learner_text: str) -> DiagnosticV2:
    d = cand.diagnostic
    if d.repair_scope == RepairScope.UTTERANCE_REWRITE:
        if not learner_text.strip():
            raise _Drop(DropReason.SPAN_MISSING, "empty learner text")
        return d.model_copy(update={"original_span": learner_text, "span_start": 0, "span_end": len(learner_text)})
    match, reason = resolve_span(learner_text, d.original_span)
    if match is None:
        raise _Drop(reason or DropReason.SPAN_MISSING, f"span {d.original_span!r} is not a run of the learner's tokens")
    return d.model_copy(
        update={"original_span": learner_text[match.start : match.end], "span_start": match.start, "span_end": match.end}
    )


def _step5_noop(d: DiagnosticV2) -> None:
    alternative = normalize_text(d.alternative or "")
    if not alternative:
        # S0 words this for errors; an upgrade or alternative without a replacement has nothing to teach
        # and feedback_item_projection.alternative is NOT NULL, so it is dropped the same way.
        raise _Drop(DropReason.NOOP, "empty alternative")
    if alternative == normalize_text(d.original_span):
        raise _Drop(DropReason.NOOP, "alternative equals the span after normalisation")


def _step6_classification(cand: _Candidate) -> None:
    d = cand.diagnostic
    if cand.claimed_classification != d.classification:
        raise _Drop(
            DropReason.CLASSIFICATION_MISMATCH,
            f"claimed {cand.claimed_classification.value}, kind {d.kind.value} maps to {d.classification.value}",
        )
    mapped = DOMAIN_BY_KIND.get(d.kind.value)
    if cand.claimed_domain is not None and mapped is not None and cand.claimed_domain != mapped:
        raise _Drop(
            DropReason.CLASSIFICATION_MISMATCH,
            f"claimed domain {cand.claimed_domain.value}, kind {d.kind.value} maps to {mapped.value}",
        )


def _step7_pattern_and_notes(index: int, d: DiagnosticV2, rec: _Recorder) -> DiagnosticV2:
    ref = f"d{index}"
    pattern = d.normalized_pattern_id.strip()
    if len(pattern) > PATTERN_ID_MAX_LEN or not _PATTERN_ID_RE.match(pattern):
        raise _Drop(DropReason.PATTERN_CODE, f"malformed normalized_pattern_id {d.normalized_pattern_id!r}")
    if pattern != d.normalized_pattern_id:
        d = d.model_copy(update={"normalized_pattern_id": pattern})

    # A false frame in the mandatory English explanation cannot be blanked: the finding is dropped.
    for name in ("explanation_en", "recast_candidate", "intended_meaning"):
        if has_false_arabic_frame(getattr(d, name, None)):
            raise _Drop(DropReason.NOTE_TONE, f"{name} asserts a Standard Arabic frame that does not exist")

    # Optional notes carrying a false Arabic frame are removed; an L1 claim resting on one is stripped.
    strip_l1 = False
    for name in _OPTIONAL_TEXT_FIELDS:
        if has_false_arabic_frame(getattr(d, name, None)):
            d = d.model_copy(update={name: None})
            rec.add(f"{ref}.{name}", DropReason.NOTE_TONE, "asserts a Standard Arabic frame that does not exist")
            if name in ("l1_source_ar", "l1_transfer_evidence"):
                strip_l1 = True
    if strip_l1:
        d = _strip_l1(d)
        rec.add(f"{ref}.l1", DropReason.PATTERN_CODE, "L1 claim rested on a frame that is not Standard Arabic")

    rule = is_intralingual_only(d.original_span)
    has_l1_claim = d.l1_transfer_likely or bool(d.l1_pattern) or bool(d.l1_source_ar)
    if rule is not None and has_l1_claim:
        d = _strip_l1(d)
        rec.add(f"{ref}.l1", DropReason.PATTERN_CODE, f"{rule}: intralingual analogy, not an Arabic transfer")

    code = (d.l1_pattern or "").strip().upper()
    if (d.l1_transfer_likely or d.l1_pattern) and code not in L1_PATTERN_CODES:
        d = _strip_l1(d)
        rec.add(f"{ref}.l1", DropReason.PATTERN_CODE, f"unknown l1_pattern {code or '(missing)'}")

    if d.explanation_ar is not None:
        problems = arabic_note_problems(d.explanation_ar)
        if problems:
            d = d.model_copy(update={"explanation_ar": None})
            rec.add(f"{ref}.explanation_ar", DropReason.NOTE_TONE, ", ".join(problems))
    return d


def _parse_communicative(raw: Mapping[str, Any], rec: _Recorder) -> CommunicativeAssessment:
    nested = raw.get("communicative")
    source: Any = nested
    if nested is None:  # tolerate the flat live shape: the flags sit at the top level
        source = {k: raw[k] for k in CommunicativeAssessment.model_fields if k in raw}
    try:
        return CommunicativeAssessment.model_validate(source if isinstance(source, Mapping) else {})
    except ValidationError as exc:
        rec.add("communicative", DropReason.SCHEMA, str(exc.errors()[0].get("msg")))
        return CommunicativeAssessment()


def _parse_p0(raw: Mapping[str, Any], rec: _Recorder) -> P0Signal:
    value = raw.get("p0")
    if value is None:
        return P0Signal()
    try:
        return P0Signal.model_validate(value)
    except ValidationError as exc:
        rec.add("p0", DropReason.SCHEMA, str(exc.errors()[0].get("msg")))
        return P0Signal()


def _step10_targets(
    raw_targets: Any,
    learner_text: str,
    recent_bot_texts: Sequence[str],
    active_target_ids: Collection[str],
    rec: _Recorder,
) -> list[TargetUseAssessment]:
    if raw_targets is None:
        return []
    if not isinstance(raw_targets, list):
        rec.add("target_assessments", DropReason.SCHEMA, "not a list")
        return []
    learner_tokens = tokenize(learner_text)
    active = set(active_target_ids)
    kept: list[TargetUseAssessment] = []
    for i, raw in enumerate(raw_targets):
        ref = f"t{i}"
        try:
            target = TargetUseAssessment.model_validate(raw)
        except ValidationError as exc:
            rec.add(ref, DropReason.SCHEMA, str(exc.errors()[0].get("msg")))
            continue
        if target.target_id not in active:
            rec.add(ref, DropReason.SCHEMA, f"{target.target_id} is not an active target")
            continue
        needle = [t.norm for t in tokenize(target.matched_span)]
        if not needle or _find_run(learner_tokens, needle) is None:
            rec.add(ref, DropReason.SPAN_MISSING, "matched_span is not verbatim in the learner text")
            continue
        credited = (ProductionEvidence.INDEPENDENT_PRODUCTION, ProductionEvidence.SPONTANEOUS_PRODUCTION)
        if target.evidence in credited and looks_like_echo(target.matched_span, recent_bot_texts):
            # Echo: no independent or spontaneous credit. Same downgrade the model applies to a cued use.
            target = target.model_copy(update={"evidence": ProductionEvidence.CUED_PRODUCTION})
            rec.add(ref, DropReason.ECHO, "wording appeared in the bot's recent turns or cards")
        kept.append(target)
    return kept


def validate_analysis(
    raw: Any,
    *,
    turn_event_id: str,
    learner_text: str,
    input_modality: str = TEXT_MODALITY,
    recent_bot_texts: Sequence[str] = (),
    active_target_ids: Collection[str] = (),
    prior_counts: Optional[Mapping[str, int]] = None,
    learner_id: Optional[int] = None,
    taken_ids: Container[str] = (),
    call_meta: Optional[CallMetaDict] = None,
    config: ValidationConfig = DEFAULT_CONFIG,
) -> ValidationResult:
    """Run S0 5.1 over the Analyst's raw JSON. Never raises on bad model output."""
    rec = _Recorder()
    if not isinstance(raw, Mapping):
        rec.add("analysis", DropReason.SCHEMA, "analysis is not an object")
        analysis = AnalysisV2(turn_event_id=turn_event_id, dropped=rec.dropped, call_meta=call_meta)
        return ValidationResult(analysis, [], rec.notes)

    communicative = _parse_communicative(raw, rec)
    p0 = _parse_p0(raw, rec)
    raw_diags = raw.get("diagnostics")
    if raw_diags is None:
        raw_diags = []
    elif not isinstance(raw_diags, list):
        rec.add("diagnostics", DropReason.SCHEMA, "not a list")
        raw_diags = []

    survivors: list[_Candidate] = []
    for index, raw_diag in enumerate(raw_diags):
        ref = f"d{index}"
        try:
            cand = _step1_schema(index, raw_diag, rec)
            _step2_domain_modality(cand, input_modality)
            cand.diagnostic = _step4_span(cand, learner_text)  # step 3 (normalise) happens inside the tokenizer
            _step5_noop(cand.diagnostic)
            _step6_classification(cand)
            cand.diagnostic = _step7_pattern_and_notes(index, cand.diagnostic, rec)
            confidence = cand.diagnostic.confidence_score
            if confidence < config.abstain_threshold:
                raise _Drop(DropReason.ABSTAIN, f"confidence {confidence:.2f} < {config.abstain_threshold:.2f}")
        except _Drop as drop:
            rec.add(ref, drop.reason, drop.detail)
            continue
        survivors.append(cand)

    # Step 9: score, then overlap resolution (highest score first; ties keep the earlier diagnostic).
    scored: list[ScoredDiagnostic] = []
    for cand in survivors:
        d = cand.diagnostic
        family = projection_family(d.kind.value, d.relevant_context_family)
        prior = prior_occurrence_count(prior_counts, learner_id, d.normalized_pattern_id, family)
        confidence = applicable_confidence(d.linguistic_confidence, d.pragmatic_confidence)
        assert confidence is not None  # DiagnosticV2 guarantees one is set
        score = priority_score(d.severity, int(d.communicative_impact), int(d.learning_value), prior, confidence)
        scored.append(ScoredDiagnostic(d, score, family, confidence, prior, cand.index))
    scored.sort(key=lambda s: (-s.score, s.raw_index))
    kept: list[ScoredDiagnostic] = []
    for item in scored:
        d = item.diagnostic
        clash = next((k for k in kept if d.span_start < k.diagnostic.span_end and k.diagnostic.span_start < d.span_end), None)
        if clash is not None:
            rec.add(f"d{item.raw_index}", DropReason.OVERLAP, f"overlaps d{clash.raw_index}")
            continue
        kept.append(item)

    # Step 10: target assessments.
    targets = _step10_targets(raw.get("target_assessments"), learner_text, recent_bot_texts, active_target_ids, rec)

    # Step 11: ids, then a final schema pass so nothing invalid leaves this module.
    used: set[str] = set()  # ids given out in this call; ``taken_ids`` (any container with ``in``) holds the stored ones
    final: list[ScoredDiagnostic] = []
    for item in kept:
        dg = assign_diagnostic_id(turn_event_id, item.raw_index, used, taken_ids)
        try:
            diag = DiagnosticV2.model_validate({**item.diagnostic.model_dump(), "diagnostic_id": dg})
        except ValidationError as exc:  # pragma: no cover - defensive; steps 1 to 10 keep the invariants
            rec.add(f"d{item.raw_index}", DropReason.SCHEMA, str(exc.errors()[0].get("msg")))
            continue
        used.add(dg)
        final.append(replace(item, diagnostic=diag))

    analysis = AnalysisV2(
        turn_event_id=turn_event_id,
        communicative=communicative,
        diagnostics=[s.diagnostic for s in final],
        target_assessments=targets,
        p0=p0,
        dropped=rec.dropped,
        call_meta=call_meta,
    )
    return ValidationResult(analysis, final, rec.notes)
