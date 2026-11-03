"""retrieval.py (S0 section 5.7, owner Claude): grading an attempt, Python first.

Order (per attempt):

1. Echo. Token Jaccard of at least 0.8 against anything the learner was just shown (the prompt, the hint,
   a card) means ``echo=True`` and no evidence rows. Checked first: it is cheaper than a judge call and
   an echo cannot be credited whatever it contains.
2. Python match. The attempt contains the canonical form or an entry of ``acceptable_forms``
   (token-aligned; ``[slots]`` match 1 to 6 words; contractions are compared both as written and
   expanded). Outcome ``success`` (hint 0) or ``hinted_success`` (hint 1 or 2), levels ``PY_MATCH_LEVELS``,
   ``matched_by`` python_exact (matched as written) or python_fuzzy (matched only after expanding contractions).
3. The injected judge returns ``linguistic_level``, ``functional_level`` and ``functional_equivalent_credited``
   and never an outcome. Python maps: success if L >= 2 and F >= 2; partial if (L >= 1 and F >= 2) or
   (L >= 2 and F == 1); else failure; a success with hint >= 1 is ``hinted_success``.
4. If the judge is shed (quota) or misses its deadline, nothing is recorded: the result says ``shed`` and
   the item stays due.

Reconstruction uses the same pipeline with the diagnostic's alternative as the target form.

Prompts and hints are built here; a NULL prompt column falls back to the communicative intention. No prompt
ever contains the target form (Zero-Bias).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from schemas import (
    CallMetaDict,
    ContextFamily,
    MatchedBy,
    Ordinal,
    PromptKind,
    RetrievalAttemptedV2,
    RetrievalOrigin,
    RetrievalOutcome,
)
from validation import tokenize

PY_MATCH_LEVELS: tuple[int, int] = (3, 2)  # (linguistic, functional) for a Python match
ECHO_JACCARD = 0.8
MAX_HINT_LEVEL = 2
MAX_SLOT_WORDS = 6
SHED_MESSAGE = "I can't check that right now. It stays in your review, so try again a little later."

_SLOT_RE = re.compile(r"\[[^\]]*\]")


class JudgeShed(RuntimeError):
    """Raised by a judge wrapper when no model call can be made now (quota shed, no judge configured)."""


_SHED_CACHE: Optional[tuple[type[BaseException], ...]] = None


def default_shed_exceptions() -> tuple[type[BaseException], ...]:
    """``QuotaShed`` and ``DeadlineExceeded`` from Gemini's ``llm_transport`` when it is importable, plus
    ``JudgeShed`` and ``TimeoutError`` (which ``DeadlineExceeded`` subclasses)."""
    global _SHED_CACHE
    if _SHED_CACHE is None:
        found: list[type[BaseException]] = [JudgeShed, TimeoutError]
        try:  # pragma: no cover - depends on the deployed repo
            import llm_transport  # type: ignore[import-not-found]

            for name in ("QuotaShed", "DeadlineExceeded"):
                cls = getattr(llm_transport, name, None)
                if isinstance(cls, type) and issubclass(cls, BaseException) and cls not in found:
                    found.append(cls)
        except Exception:
            pass
        _SHED_CACHE = tuple(found)
    return _SHED_CACHE


# ------------------------------------------------------------------------------ data


@dataclass(frozen=True)
class PhraseEntry:
    """A catalog row as retrieval needs it."""

    phrase_id: str
    canonical_form: str
    communicative_intention: str
    arabic_gloss: Optional[str] = None
    acceptable_forms: tuple[str, ...] = ()
    meaning_prompt: Optional[str] = None
    situation_prompt: Optional[str] = None
    function_prompt: Optional[str] = None
    context_families: tuple[str, ...] = ("general",)

    def target(self) -> "Target":
        return Target(self.canonical_form, self.acceptable_forms)


@dataclass(frozen=True)
class Target:
    canonical_form: str
    acceptable_forms: tuple[str, ...] = ()


@dataclass(frozen=True)
class JudgeRequest:
    attempt_text: str
    target_form: str
    acceptable_forms: tuple[str, ...]
    shown_texts: tuple[str, ...]
    prompt_kind: Optional[str]
    context_family: str
    communicative_intention: str
    hint_level: int


@dataclass(frozen=True)
class JudgeVerdict:
    linguistic_level: int
    functional_level: int
    functional_equivalent_credited: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "JudgeVerdict":
        return cls(
            int(data["linguistic_level"]),
            int(data["functional_level"]),
            bool(data.get("functional_equivalent_credited", False)),
        )


Judge = Callable[[JudgeRequest], JudgeVerdict]


@dataclass(frozen=True)
class GradeResult:
    outcome: Optional[RetrievalOutcome]  # None only when the check was shed
    linguistic_level: Optional[int]
    functional_level: Optional[int]
    matched_by: Optional[MatchedBy]
    matched_form: Optional[str] = None
    echo: bool = False
    functional_equivalent_credited: bool = False
    shed: bool = False
    hint_level: int = 0

    @property
    def records_evidence(self) -> bool:
        """Whether this result becomes a ``retrieval_attempted`` event with evidence rows."""
        return not self.shed and not self.echo


# ------------------------------------------------------------------------------ matching

_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "can't": ("can", "not"), "cannot": ("can", "not"), "won't": ("will", "not"), "shan't": ("shall", "not"),
    "ain't": ("is", "not"), "let's": ("let", "us"),
    "gonna": ("going", "to"), "wanna": ("want", "to"), "gotta": ("got", "to"),
}
_SUFFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("n't", ("not",)), ("'re", ("are",)), ("'ve", ("have",)), ("'ll", ("will",)),
    ("'m", ("am",)), ("'d", ("would",)), ("'s", ("is",)),
)


def expand_token(token: str) -> tuple[str, ...]:
    """A contraction as the words it stands for ("don't" -> do not, "I'm" -> i am); other tokens unchanged."""
    if token in _EXPANSIONS:
        return _EXPANSIONS[token]
    for suffix, words in _SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return (token[: -len(suffix)],) + words
    return (token,)


def expand_tokens(tokens: Sequence[str]) -> list[str]:
    out: list[str] = []
    for token in tokens:
        out.extend(expand_token(token))
    return out


def _norm_tokens(text: str) -> list[str]:
    return [t.norm for t in tokenize(text)]


def _pattern(form: str, expand: bool) -> list[Optional[str]]:
    """The form as a token pattern; ``None`` marks a slot."""
    items: list[Optional[str]] = []
    position = 0
    for match in _SLOT_RE.finditer(form):
        literal = _norm_tokens(form[position : match.start()])
        items.extend(expand_tokens(literal) if expand else literal)
        items.append(None)
        position = match.end()
    literal = _norm_tokens(form[position:])
    items.extend(expand_tokens(literal) if expand else literal)
    return items


def _match_at(pattern: Sequence[Optional[str]], tokens: Sequence[str], pi: int, ti: int) -> bool:
    if pi == len(pattern):
        return True
    item = pattern[pi]
    if item is not None:
        return ti < len(tokens) and tokens[ti] == item and _match_at(pattern, tokens, pi + 1, ti + 1)
    for take in range(1, MAX_SLOT_WORDS + 1):
        if ti + take > len(tokens):
            break
        if _match_at(pattern, tokens, pi + 1, ti + take):
            return True
    return False


def _contains(pattern: Sequence[Optional[str]], tokens: Sequence[str]) -> bool:
    if not any(p is not None for p in pattern):
        return False
    return any(_match_at(pattern, tokens, 0, start) for start in range(len(tokens)))


def match_form(attempt_text: str, form: str) -> Optional[MatchedBy]:
    """Is ``form`` in the attempt (token-aligned)? python_exact as written, python_fuzzy only once contractions are expanded."""
    raw_tokens = _norm_tokens(attempt_text)
    if _contains(_pattern(form, expand=False), raw_tokens):
        return MatchedBy.PYTHON_EXACT
    if _contains(_pattern(form, expand=True), expand_tokens(raw_tokens)):
        return MatchedBy.PYTHON_FUZZY
    return None


def find_match(attempt_text: str, target: Target) -> Optional[tuple[str, MatchedBy]]:
    for form in (target.canonical_form, *target.acceptable_forms):
        how = match_form(attempt_text, form)
        if how is not None:
            return form, how
    return None


def token_jaccard(a: str, b: str) -> float:
    left, right = set(_norm_tokens(a)), set(_norm_tokens(b))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def is_echo(attempt_text: str, shown_texts: Sequence[str]) -> bool:
    return any(token_jaccard(attempt_text, shown) >= ECHO_JACCARD for shown in shown_texts if shown)


# ------------------------------------------------------------------------------ grading


def outcome_from_levels(linguistic_level: int, functional_level: int, hint_level: int) -> RetrievalOutcome:
    """success if L >= 2 and F >= 2; partial if (L >= 1 and F >= 2) or (L >= 2 and F == 1); else failure."""
    if linguistic_level >= 2 and functional_level >= 2:
        return RetrievalOutcome.HINTED_SUCCESS if hint_level >= 1 else RetrievalOutcome.SUCCESS
    if (linguistic_level >= 1 and functional_level >= 2) or (linguistic_level >= 2 and functional_level == 1):
        return RetrievalOutcome.PARTIAL
    return RetrievalOutcome.FAILURE


def _check_level(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise ValueError(f"judge {name} must be an integer 0..3, got {value!r}")
    return value


def grade_attempt(
    attempt_text: str,
    target: Target,
    *,
    shown_texts: Sequence[str] = (),
    hint_level: int = 0,
    judge: Optional[Judge] = None,
    prompt_kind: Optional[str] = None,
    context_family: str = "general",
    communicative_intention: str = "",
    shed_exceptions: Optional[tuple[type[BaseException], ...]] = None,
) -> GradeResult:
    if not 0 <= hint_level <= MAX_HINT_LEVEL:
        raise ValueError(f"hint_level must be 0..{MAX_HINT_LEVEL}, got {hint_level}")
    if is_echo(attempt_text, shown_texts):
        return GradeResult(RetrievalOutcome.FAILURE, None, None, MatchedBy.PYTHON_EXACT, echo=True, hint_level=hint_level)

    found = find_match(attempt_text, target)
    if found is not None:
        form, how = found
        outcome = RetrievalOutcome.HINTED_SUCCESS if hint_level >= 1 else RetrievalOutcome.SUCCESS
        return GradeResult(outcome, PY_MATCH_LEVELS[0], PY_MATCH_LEVELS[1], how, matched_form=form, hint_level=hint_level)

    if judge is None:
        return GradeResult(None, None, None, None, shed=True, hint_level=hint_level)
    request = JudgeRequest(
        attempt_text=attempt_text,
        target_form=target.canonical_form,
        acceptable_forms=tuple(target.acceptable_forms),
        shown_texts=tuple(shown_texts),
        prompt_kind=prompt_kind,
        context_family=context_family,
        communicative_intention=communicative_intention,
        hint_level=hint_level,
    )
    try:
        verdict = judge(request)
    except (shed_exceptions or default_shed_exceptions()):
        return GradeResult(None, None, None, None, shed=True, hint_level=hint_level)
    linguistic = _check_level("linguistic_level", verdict.linguistic_level)
    functional = _check_level("functional_level", verdict.functional_level)
    return GradeResult(
        outcome_from_levels(linguistic, functional, hint_level),
        linguistic,
        functional,
        MatchedBy.LLM_JUDGE,
        functional_equivalent_credited=bool(verdict.functional_equivalent_credited),
        hint_level=hint_level,
    )


def grade_reconstruction(
    attempt_text: str,
    alternative: str,
    *,
    shown_texts: Sequence[str] = (),
    judge: Optional[Judge] = None,
    context_family: str = "general",
    communicative_intention: str = "",
    shed_exceptions: Optional[tuple[type[BaseException], ...]] = None,
) -> GradeResult:
    """Same pipeline, the diagnostic's alternative as the target form, never hinted."""
    return grade_attempt(
        attempt_text,
        Target(alternative),
        shown_texts=shown_texts,
        hint_level=0,
        judge=judge,
        context_family=context_family,
        communicative_intention=communicative_intention,
        shed_exceptions=shed_exceptions,
    )


def to_attempt_event(
    result: GradeResult,
    *,
    phrase_id: str,
    origin: RetrievalOrigin,
    prompt_kind: PromptKind,
    context_family: str,
    due: bool,
    schedule_step_before: int,
    interaction_id: Optional[int] = None,
    call_meta: Optional[CallMetaDict] = None,
) -> RetrievalAttemptedV2:
    """The ``retrieval_attempted`` payload. A shed check writes nothing, so it cannot be converted."""
    if result.shed or result.outcome is None or result.matched_by is None:
        raise ValueError("a shed check records no event; the item stays due")
    return RetrievalAttemptedV2(
        phrase_id=phrase_id,
        origin=origin,
        prompt_kind=prompt_kind,
        context_family=ContextFamily(context_family),
        outcome=result.outcome,
        hint_level=result.hint_level,  # type: ignore[arg-type]
        linguistic_level=None if result.linguistic_level is None else Ordinal(result.linguistic_level),
        functional_level=None if result.functional_level is None else Ordinal(result.functional_level),
        matched_form=result.matched_form,
        matched_by=result.matched_by,
        echo=result.echo,
        due=due,
        schedule_step_before=schedule_step_before,
        interaction_id=interaction_id,
        call_meta=call_meta,
    )


# ------------------------------------------------------------------------------ prompts and hints


def _intention_text(entry: PhraseEntry) -> str:
    text = re.sub(r"[_\s]+", " ", entry.communicative_intention or "").strip()
    return text or "say what you mean"


def build_prompt(entry: PhraseEntry, kind: PromptKind | str, family: str = "general") -> str:
    """The stored prompt for ``kind``, or a fallback built from the communicative intention. Never the target form."""
    kind_value = PromptKind(kind).value
    stored = {
        PromptKind.MEANING.value: entry.meaning_prompt,
        PromptKind.SITUATION.value: entry.situation_prompt,
        PromptKind.FUNCTION.value: entry.function_prompt,
    }.get(kind_value)
    if stored and stored.strip():
        return stored.strip()
    intent = _intention_text(entry)
    place = "everyday" if family in ("", "general") else family.replace("_", " ")
    if kind_value == PromptKind.SITUATION.value:
        return f"Imagine a {place} situation where you need to {intent}. What do you say?"
    if kind_value == PromptKind.FUNCTION.value:
        return f"Say one sentence that would {intent}."
    return f"Think of a natural way to {intent} in spoken English. What would you say?"


def build_hint(entry: PhraseEntry, level: int) -> Optional[str]:
    """Level 0 none; level 1 the Arabic gloss (or the first word); level 2 the first letters of each word."""
    if level <= 0:
        return None
    words = [w for w in _SLOT_RE.sub(" … ", entry.canonical_form).split() if w]
    if level == 1:
        if entry.arabic_gloss and entry.arabic_gloss.strip():
            return entry.arabic_gloss.strip()
        return f"It starts with “{words[0]}”." if words else None
    masked = [w if w == "…" else (w[0] + "_" * (len(w) - 1) if len(w) > 1 else w) for w in words]
    return " ".join(masked)
