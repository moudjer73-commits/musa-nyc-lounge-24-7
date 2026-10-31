"""11-step deterministic validation pipeline for Analyst outputs
(S0 Contracts, Sections 5.1 & 6).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

import config
from interaction import priority_score
from schemas_v3 import (
    Classification,
    DiagnosticV2,
    Domain,
    DropReason,
    DropRecord,
    POSignal,
    RepairScope,
    Timing,
)

__all__ = [
    "AnalystRawV2",
    "ValidationResult",
    "normalize_text",
    "validate_analysis",
]

_CURLY_SINGLE_RE = re.compile(r"[\u2018\u2019\u02bc\u2032\u0060\u00b4]")
_CURLY_DOUBLE_RE = re.compile(r"[\u201c\u201d\u00ab\u00bb\u201e\u201f]")
_WHITESPACE_RE = re.compile(r"\s+")
_WORD_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_PATTERN_ID_RE = re.compile(r"^[a-z0-9_]{2,64}$")
_DIAG_ID_RE = re.compile(r"^dg_[0-9a-f]{8}$")

# Audio/pronunciation claims forbidden when input_modality == 'text' (Step 2)
_AUDIO_CLAIM_KINDS = frozenset(
    {
        "pronunciation",
        "prosody",
        "intonation",
        "stress",
        "word_stress",
        "sentence_stress",
        "rhythm",
        "phonology",
        "phonological",
        "accent",
    }
)
_AUDIO_CLAIM_TEXT_RE = re.compile(
    r"\b(pronounce[ds]?|pronunciation|intonation|prosod(?:y|ic)|vowel\s+sound|"
    r"consonant\s+sound|syllable\s+stress|voice\s+pitch|spoken\s+accent)\b",
    re.IGNORECASE,
)

# Forbidden judgmental phrasing on UPGRADE / ALTERNATIVE notes (Step 7)
_EN_NEGATED_WRONG_RE = re.compile(
    r"\b(not|isn't|is\s+not|wasn't|was\s+not|never|nothing)\s+"
    r"(?:grammatically\s+)?(wrong|incorrect|an\s+error|a\s+mistake|ungrammatical)\b",
    re.IGNORECASE,
)
_EN_FORBIDDEN_TONE_RE = re.compile(
    r"\b(wrong|incorrect|ungrammatical|grammatically\s+incorrect|error|mistake)\b",
    re.IGNORECASE,
)
_AR_NEGATED_WRONG_RE = re.compile(
    r"(ليس|ليست|غير)\s+(خطأ|خاطئ|خاطئة|غير\s+صحيح|غير\s+صحيحة)"
)
_AR_FORBIDDEN_TONE_RE = re.compile(
    r"(خطأ|خاطئ|خاطئة|غير\s+صحيح|غير\s+صحيحة)"
)


class AnalystRawV2(BaseModel):
    """Raw Analyst output before per-diagnostic validation."""

    model_config = ConfigDict(extra="ignore")

    communicative: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    target_assessments: list[dict[str, Any]] = Field(default_factory=list)
    p0: dict[str, Any] | POSignal = Field(default_factory=POSignal)


@dataclass(frozen=True)
class ValidationResult:
    communicative: dict[str, Any]
    diagnostics: list[DiagnosticV2]
    target_assessments: list[dict[str, Any]]
    p0: POSignal
    dropped: list[DropRecord]


def normalize_text(s: str) -> str:
    """NFKC, curly quotes/apostrophes to straight, casefold, whitespace collapsed (Section 5.1 #3)."""
    if not isinstance(s, str):
        return ""
    norm = unicodedata.normalize("NFKC", s)
    norm = _CURLY_SINGLE_RE.sub("'", norm)
    norm = _CURLY_DOUBLE_RE.sub('"', norm)
    norm = norm.casefold()
    return _WHITESPACE_RE.sub(" ", norm).strip()


def _tokenize_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Tokenizes text into (normalized_token, start_char, end_char) preserving exact indices in text."""
    if not text:
        return []
    # 1-to-1 character replacement for curly apostrophes so offsets into `text` remain exact
    quote_aligned = _CURLY_SINGLE_RE.sub("'", text)
    out: list[tuple[str, int, int]] = []
    for m in _WORD_TOKEN_RE.finditer(quote_aligned):
        tok_norm = normalize_text(m.group(0))
        if tok_norm:
            out.append((tok_norm, m.start(), m.end()))
    return out


def _find_contiguous_token_span(
    haystack_tokens: Sequence[tuple[str, int, int]],
    needle_tokens: Sequence[str],
) -> tuple[int, int] | None:
    """Returns (start_char, end_char) of the first contiguous match of needle_tokens in haystack_tokens."""
    n = len(needle_tokens)
    if n == 0 or len(haystack_tokens) < n:
        return None
    target = list(needle_tokens)
    for i in range(len(haystack_tokens) - n + 1):
        if [haystack_tokens[i + j][0] for j in range(n)] == target:
            return haystack_tokens[i][1], haystack_tokens[i + n - 1][2]
    return None


def _has_forbidden_upgrade_tone(explanation_en: str, explanation_ar: str | None) -> bool:
    if explanation_en:
        cleaned_en = _EN_NEGATED_WRONG_RE.sub("", explanation_en)
        if _EN_FORBIDDEN_TONE_RE.search(cleaned_en):
            return True
    if explanation_ar:
        cleaned_ar = _AR_NEGATED_WRONG_RE.sub("", explanation_ar)
        if _AR_FORBIDDEN_TONE_RE.search(cleaned_ar):
            return True
    return False


def _applicable_confidence(
    domain: Domain,
    ling_conf: float | None,
    prag_conf: float | None,
    fallback_conf: float,
) -> float:
    if ling_conf is not None and prag_conf is not None:
        return min(ling_conf, prag_conf)
    if domain in (Domain.LINGUISTIC, Domain.DISCOURSE):
        return ling_conf if ling_conf is not None else fallback_conf
    if domain == Domain.PRAGMATIC:
        return prag_conf if prag_conf is not None else fallback_conf
    return fallback_conf


def _derive_confidence_band(
    ling_conf: float | None,
    prag_conf: float | None,
    fallback_conf: float,
) -> str:
    non_null = [c for c in (ling_conf, prag_conf) if c is not None]
    val = min(non_null) if non_null else fallback_conf
    if val >= 0.85:
        return "high"
    if val >= 0.70:
        return "medium"
    return "low"


def _sanitize_p0(raw_p0: Any) -> POSignal:
    if isinstance(raw_p0, POSignal):
        candidate = raw_p0.candidate
        line = raw_p0.line
    elif isinstance(raw_p0, Mapping):
        candidate = bool(raw_p0.get("candidate", False))
        line = raw_p0.get("line")
    else:
        return POSignal(candidate=False, line=None)

    if not candidate or not isinstance(line, str) or not line.strip():
        return POSignal(candidate=False, line=None)
    words = line.strip().split()
    capped_line = " ".join(words[:20])
    return POSignal(candidate=True, line=capped_line)


def validate_analysis(
    raw: AnalystRawV2 | Mapping[str, Any],
    *,
    learner_text: str,
    input_modality: Literal["text", "voice"] = "text",
    recent_bot_texts: Sequence[str] = (),
    active_target_ids: set[str] | frozenset[str] = frozenset(),
    prior_pattern_counts: Mapping[str, int] | None = None,
    abstain_threshold: float | None = None,
) -> ValidationResult:
    """Runs the 11-step S0 Section 5.1 validation pipeline in strict order."""
    prior_counts = prior_pattern_counts or {}
    threshold = (
        abstain_threshold
        if abstain_threshold is not None
        else float(getattr(config, "ABSTAIN_THRESHOLD", 0.80))
    )

    if isinstance(raw, AnalystRawV2):
        raw_comm = dict(raw.communicative or {})
        raw_diags = list(raw.diagnostics or [])
        raw_targets = list(raw.target_assessments or [])
        raw_p0 = raw.p0
    elif isinstance(raw, Mapping):
        raw_comm = dict(raw.get("communicative") or {})
        raw_diags = list(raw.get("diagnostics") or [])
        raw_targets = list(raw.get("target_assessments") or [])
        raw_p0 = raw.get("p0") or POSignal()
    else:
        raw_comm, raw_diags, raw_targets, raw_p0 = {}, [], [], POSignal()

    dropped: list[DropRecord] = []

    # Step 3 preparation: tokenize learner_text once with character offsets
    learner_tokens = _tokenize_with_offsets(learner_text)
    norm_learner_text = normalize_text(learner_text)

    candidates: list[tuple[int, DiagnosticV2]] = []

    for idx, item in enumerate(raw_diags):
        ref = f"diag_{idx}"
        if isinstance(item, DiagnosticV2):
            d_dict = item.model_dump()
        elif isinstance(item, Mapping):
            d_dict = dict(item)
        else:
            dropped.append(DropRecord(ref=ref, reason=DropReason.SCHEMA))
            continue

        if isinstance(d_dict.get("diagnostic_id"), str) and d_dict["diagnostic_id"]:
            ref = str(d_dict["diagnostic_id"])

        # ------------------------------------------------------------------
        # Step 1: Schema validation
        # ------------------------------------------------------------------
        try:
            kind = str(d_dict.get("kind") or "").strip()
            if not kind:
                raise ValueError("missing kind")
            domain = Domain(str(d_dict.get("domain") or "linguistic"))
            classification = Classification(str(d_dict.get("classification") or "error"))
            repair_scope = RepairScope(str(d_dict.get("repair_scope") or "phrase_span"))

            raw_timing = d_dict.get("timing_hint")
            timing_hint = Timing(str(raw_timing)) if raw_timing is not None else None

            raw_span = d_dict.get("original_span")
            if repair_scope != RepairScope.UTTERANCE_REWRITE and not isinstance(raw_span, str):
                raise ValueError("missing original_span")
            original_span = str(raw_span or "")

            raw_alt = d_dict.get("alternative")
            if not isinstance(raw_alt, str):
                raise ValueError("missing alternative")
            alternative = raw_alt

            imp = d_dict.get("communicative_impact", 2)
            learn = d_dict.get("learning_value", 2)
            if (
                isinstance(imp, bool)
                or isinstance(learn, bool)
                or imp not in (0, 1, 2, 3)
                or learn not in (0, 1, 2, 3)
            ):
                raise ValueError("invalid ordinal")

            severity = str(d_dict.get("severity") or "medium").strip().lower()
            if severity not in ("low", "medium", "high", "critical", "none"):
                raise ValueError("invalid severity")

            alt_status = str(
                d_dict.get("alternative_status")
                or ("optional" if classification != Classification.ERROR else "recommended")
            ).strip().lower()

            ling_conf = d_dict.get("linguistic_confidence")
            if ling_conf is not None:
                ling_conf = float(ling_conf)
                if not (0.0 <= ling_conf <= 1.0):
                    raise ValueError("linguistic_confidence out of bounds")

            prag_conf = d_dict.get("pragmatic_confidence")
            if prag_conf is not None:
                prag_conf = float(prag_conf)
                if not (0.0 <= prag_conf <= 1.0):
                    raise ValueError("pragmatic_confidence out of bounds")

            acoust_conf = d_dict.get("acoustic_confidence")
            if acoust_conf is not None:
                acoust_conf = float(acoust_conf)

            conf_score = float(d_dict.get("confidence_score", 0.85))
            if not (0.0 <= conf_score <= 1.0):
                raise ValueError("confidence_score out of bounds")

            pattern_id = str(d_dict.get("pattern_id") or "general").strip()
            explanation_en = str(d_dict.get("explanation_en") or "")
            explanation_ar = (
                str(d_dict["explanation_ar"])
                if d_dict.get("explanation_ar") is not None
                else None
            )
        except Exception:
            dropped.append(DropRecord(ref=ref, reason=DropReason.SCHEMA))
            continue

        # ------------------------------------------------------------------
        # Step 2: Domain and modality
        # ------------------------------------------------------------------
        if domain == Domain.PHONOLOGICAL or acoust_conf is not None:
            dropped.append(DropRecord(ref=ref, reason=DropReason.DOMAIN_MODALITY))
            continue
        if input_modality == "text":
            if kind.lower() in _AUDIO_CLAIM_KINDS or _AUDIO_CLAIM_TEXT_RE.search(explanation_en):
                dropped.append(DropRecord(ref=ref, reason=DropReason.DOMAIN_MODALITY))
                continue

        # ------------------------------------------------------------------
        # Step 3 & Step 4: Normalize, Tokenize, and Span Alignment
        # ------------------------------------------------------------------
        if repair_scope == RepairScope.UTTERANCE_REWRITE:
            span_start = 0
            span_end = len(learner_text)
            surface_span = learner_text
            if not surface_span.strip():
                dropped.append(DropRecord(ref=ref, reason=DropReason.SPAN_MISSING))
                continue
        else:
            span_tok_objs = _tokenize_with_offsets(original_span)
            span_toks = [t[0] for t in span_tok_objs]
            if not span_toks:
                dropped.append(DropRecord(ref=ref, reason=DropReason.SPAN_MISSING))
                continue
            matched_offsets = _find_contiguous_token_span(learner_tokens, span_toks)
            if matched_offsets is None:
                norm_span_candidate = normalize_text(original_span)
                if norm_span_candidate and norm_span_candidate in norm_learner_text:
                    dropped.append(DropRecord(ref=ref, reason=DropReason.SPAN_BOUNDARY))
                else:
                    dropped.append(DropRecord(ref=ref, reason=DropReason.SPAN_MISSING))
                continue
            span_start, span_end = matched_offsets
            surface_span = learner_text[span_start:span_end]

        # ------------------------------------------------------------------
        # Step 5: No-op check
        # ------------------------------------------------------------------
        norm_span = normalize_text(surface_span)
        norm_alt = normalize_text(alternative)
        if not norm_alt or norm_alt == norm_span:
            dropped.append(DropRecord(ref=ref, reason=DropReason.NOOP))
            continue

        # ------------------------------------------------------------------
        # Step 6: Classification consistency (Section 2)
        # ------------------------------------------------------------------
        if kind.lower() == "insufficient_context":
            dropped.append(DropRecord(ref=ref, reason=DropReason.CLASSIFICATION_MISMATCH))
            continue
        if classification == Classification.ERROR and alt_status not in ("required", "recommended"):
            dropped.append(DropRecord(ref=ref, reason=DropReason.CLASSIFICATION_MISMATCH))
            continue
        if (
            classification in (Classification.UPGRADE, Classification.ALTERNATIVE)
            and alt_status != "optional"
        ):
            dropped.append(DropRecord(ref=ref, reason=DropReason.CLASSIFICATION_MISMATCH))
            continue

        # ------------------------------------------------------------------
        # Step 7: Pattern code & Arabic/English note tone
        # ------------------------------------------------------------------
        if not _PATTERN_ID_RE.fullmatch(pattern_id):
            dropped.append(DropRecord(ref=ref, reason=DropReason.PATTERN_CODE))
            continue
        if classification in (Classification.UPGRADE, Classification.ALTERNATIVE):
            if _has_forbidden_upgrade_tone(explanation_en, explanation_ar):
                dropped.append(DropRecord(ref=ref, reason=DropReason.NOTE_TONE))
                continue

        # ------------------------------------------------------------------
        # Step 8: Abstention
        # ------------------------------------------------------------------
        app_conf = _applicable_confidence(domain, ling_conf, prag_conf, conf_score)
        if app_conf < threshold:
            dropped.append(DropRecord(ref=ref, reason=DropReason.ABSTAIN))
            continue
        conf_band = _derive_confidence_band(ling_conf, prag_conf, app_conf)

        # Build provisional DiagnosticV2 (Step 11 finalizes diagnostic_id)
        prov_id = (
            ref
            if _DIAG_ID_RE.fullmatch(ref)
            else "dg_"
            + hashlib.sha1(
                f"{learner_text}:{idx}:{span_start}:{span_end}:{alternative}".encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:8]
        )
        diag_v2 = DiagnosticV2(
            kind=kind,
            severity=severity,
            original_span=surface_span,
            alternative=alternative,
            alternative_status=alt_status,
            explanation_en=explanation_en,
            explanation_ar=explanation_ar,
            listener_effect=d_dict.get("listener_effect"),
            secondary_register_label=d_dict.get("secondary_register_label"),
            secondary_register_form=d_dict.get("secondary_register_form"),
            pattern_id=pattern_id,
            confidence_score=round(app_conf, 4),
            confidence_band=conf_band,
            diagnostic_id=prov_id,
            domain=domain,
            classification=classification,
            repair_scope=repair_scope,
            timing_hint=timing_hint,
            span_start=span_start,
            span_end=span_end,
            communicative_impact=imp,
            learning_value=learn,
            linguistic_confidence=ling_conf,
            pragmatic_confidence=prag_conf,
            acoustic_confidence=None,
        )
        candidates.append((idx, diag_v2))

    # ----------------------------------------------------------------------
    # Step 9: Priority score & overlap resolution
    # ----------------------------------------------------------------------
    scored_candidates = [
        (
            priority_score(d, prior_counts.get(d.pattern_id, 0)),
            orig_idx,
            d,
        )
        for orig_idx, d in candidates
    ]
    scored_candidates.sort(key=lambda item: (-item[0], item[1]))

    kept_diags: list[DiagnosticV2] = []
    for _score, _orig_idx, cand in scored_candidates:
        overlaps = any(
            max(cand.span_start, k.span_start) < min(cand.span_end, k.span_end)
            for k in kept_diags
        )
        if overlaps:
            dropped.append(DropRecord(ref=cand.diagnostic_id, reason=DropReason.OVERLAP))
        else:
            kept_diags.append(cand)

    # ----------------------------------------------------------------------
    # Step 10: Target assessments & Echo Guard
    # ----------------------------------------------------------------------
    bot_token_lists = [_tokenize_with_offsets(bt) for bt in recent_bot_texts if bt]
    validated_targets: list[dict[str, Any]] = []

    for ta in raw_targets:
        if not isinstance(ta, Mapping):
            continue
        phrase_id = str(ta.get("phrase_id") or "").strip()
        if not phrase_id:
            continue
        if active_target_ids and phrase_id not in active_target_ids:
            continue

        ta_copy = dict(ta)
        used = bool(ta_copy.get("used", False))
        matched_span = str(ta_copy.get("matched_span") or "").strip()

        if used:
            m_toks = [t[0] for t in _tokenize_with_offsets(matched_span)]
            loc = _find_contiguous_token_span(learner_tokens, m_toks) if m_toks else None
            if loc is None:
                ta_copy["used"] = False
                ta_copy["matched_span"] = None
                ta_copy["echo"] = False
            else:
                ta_copy["matched_span"] = learner_text[loc[0] : loc[1]]
                is_echo = bool(ta_copy.get("echo", False)) or any(
                    _find_contiguous_token_span(bt_toks, m_toks) is not None
                    for bt_toks in bot_token_lists
                )
                ta_copy["echo"] = is_echo
                if is_echo:
                    dropped.append(DropRecord(ref=phrase_id, reason=DropReason.ECHO))
        else:
            ta_copy["echo"] = False

        validated_targets.append(ta_copy)

    # ----------------------------------------------------------------------
    # Step 11: Finalize diagnostic_ids & P0 signal
    # ----------------------------------------------------------------------
    final_diags: list[DiagnosticV2] = []
    seen_ids: set[str] = set()
    for final_idx, d in enumerate(kept_diags):
        diag_id = d.diagnostic_id
        if not _DIAG_ID_RE.fullmatch(diag_id) or diag_id in seen_ids:
            diag_id = (
                "dg_"
                + hashlib.sha1(
                    f"{learner_text}:{final_idx}:{d.span_start}:{d.span_end}:{d.alternative}".encode(
                        "utf-8"
                    ),
                    usedforsecurity=False,
                ).hexdigest()[:8]
            )
        seen_ids.add(diag_id)
        if diag_id != d.diagnostic_id:
            d = d.model_copy(update={"diagnostic_id": diag_id})
        final_diags.append(d)

    p0_signal = _sanitize_p0(raw_p0)

    return ValidationResult(
        communicative=raw_comm,
        diagnostics=final_diags,
        target_assessments=validated_targets,
        p0=p0_signal,
        dropped=dropped,
    )
