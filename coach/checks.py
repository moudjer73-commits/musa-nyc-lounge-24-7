"""Deterministic guards around every LLM output.

* `sanitize_analysis`  : never trust the Analyst blindly (verbatim spans, echo detection,
                         Arabic-note tone rules, known pattern codes).
* `check_reply`        : the Responder must obey the director brief; violations trigger one
                         silent retry.
* `validate_telegram_html`: cards and debriefs must be valid Telegram HTML within length limits.
* text helpers used by the pipeline (normalisation, fuzzy phrase matching, openers).
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from typing import Iterable, Sequence

from .models import Analysis, DirectorBrief, Expression, Finding, ResponderReply, UsedTarget

# --------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")
_STOP = frozenset(
    "a an the to of i you he she it we they me my your his her our their is are am was were be been "
    "and or but so that this there here in on at for with as if do does did have has had will would can could "
    "just really very pretty".split()
)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[^a-z0-9'؀-ۿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def word_count(text: str) -> int:
    return len(words(text))


def contains_phrase(text: str, phrase: str) -> bool:
    p = normalize_text(phrase)
    return bool(p) and p in normalize_text(text)


def _stem(tok: str) -> str:
    """Crude stem so make/makes/making/made-less inflections compare equal (make, making -> mak)."""
    tok = tok.replace("'s", "")
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            tok = tok[: -len(suf)]
            break
    if tok.endswith("e") and len(tok) > 3:
        tok = tok[:-1]
    return tok


def _content_stems(text: str) -> list[str]:
    return [_stem(t) for t in normalize_text(text).split() if t not in _STOP and len(t) > 1]


def fuzzy_contains(text: str, phrase: str) -> bool:
    """Is `phrase` (or a close inflection of it) present in `text`?

    All content words of the phrase must appear in the text, compared by crude stem.
    Phrases with no content words ("I see") fall back to exact normalised substring.
    """
    need = _content_stems(phrase)
    if not need:
        return contains_phrase(text, phrase)
    have = set(_content_stems(text))
    return all(n in have for n in need)


def first_word(text: str) -> str:
    ws = words(text)
    return ws[0].lower() if ws else ""


def ends_with_question(text: str) -> bool:
    return text.rstrip(" \t\n\"'”’)]}…").endswith("?")


def question_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.endswith("?")]


def looks_like_echo(form_used: str, recent_texts: Iterable[str]) -> bool:
    """Did the learner just repeat words that the character or a card showed them?"""
    need = _content_stems(form_used)
    if not need:
        return contains_phrase(" ".join(recent_texts), form_used)
    for t in recent_texts:
        have = set(_content_stems(t))
        if all(n in have for n in need):
            return True
    return False


def strip_tags(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


# --------------------------------------------------------------------------------------
# Arabic note tone rules (prompts/_partials/arabic_note_rules.md)
# --------------------------------------------------------------------------------------

_AR_DIACRITICS = re.compile(r"[ً-ٰٟـ]")
ARABIC_NOTE_MAX_CHARS = 200

# Normalised forms (see _ar_norm). Whole-word matches with common clitic prefixes.
def _ar_norm(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي"), ("ة", "ه")):
        text = text.replace(a, b)
    return text


# Written in natural spelling and normalised at import, so the list stays readable.
_AR_BANNED_WORDS = tuple(_ar_norm(w) for w in ("خطأ", "أخطاء", "غلط", "ركيك", "ضعيف"))
_AR_BANNED_PHRASES = tuple(
    _ar_norm(p)
    for p in (
        "لا يقال أبدًا",
        "لا يُقال",            # "never said" style absolutes; the approved wording is «أقل شيوعًا»
        "العرب يخطئون",
        "تفكير عربي",
        "ترجمة حرفية سيئة",
        "ترجمة سيئة",
    )
)
_AR_PREFIXES = r"(?:ال|وال|بال|لل|ب|و|ل|ف|ك)?"
_AR_BANNED_RE = re.compile(
    r"(?<![ء-ي])" + _AR_PREFIXES + r"(?:" + "|".join(_AR_BANNED_WORDS) + r")(?![ء-ي])"
)
_AR_GENERALISE_RE = re.compile(r"(?<![ء-ي])العرب(?![ء-ي])")
_AR_LETTER_RE = re.compile(r"[ء-ي]")


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


# --------------------------------------------------------------------------------------
# Analyst sanitising
# --------------------------------------------------------------------------------------

L1_PATTERN_CODES = frozenset(
    {
        "COLLOC_LIGHT_VERB", "PREP_CALQUE", "IDIOM_CALQUE", "SEMANTIC_RANGE", "DIRECTNESS",
        "RHETORICAL_INTENSITY", "OVER_FORMAL", "ADDRESS_TERM", "DISCOURSE_MARKER_GAP",
        "SYNTAX_CALQUE", "COUNTABILITY", "TENSE_ASPECT", "SPEECH_ACT_RITUAL",
    }
)


def sanitize_analysis(
    analysis: Analysis,
    learner_text: str,
    *,
    active_target_ids: Iterable[str] = (),
    recent_texts: Sequence[str] = (),
) -> tuple[Analysis, list[str]]:
    """Return a cleaned copy of the Analyst's output and a list of notes about what was changed."""
    notes: list[str] = []
    norm_learner = normalize_text(learner_text)
    target_ids = set(active_target_ids)

    findings: list[Finding] = []
    seen_spans: set[str] = set()
    for f in analysis.findings:
        f = f.model_copy(deep=True)
        # verbatim span (fluency findings come from voice metrics and have no quotable span)
        if f.category != "fluency" and normalize_text(f.span) not in norm_learner:
            notes.append(f"dropped finding {f.pattern_key}: span not in learner text")
            continue
        if normalize_text(f.span) in seen_spans:
            notes.append(f"dropped duplicate finding on span {f.span!r}")
            continue
        if not f.alternatives and f.category != "comprehension":
            notes.append(f"dropped finding {f.pattern_key}: no alternatives to teach")
            continue
        if f.l1_transfer and (f.l1_pattern or "").upper() not in L1_PATTERN_CODES:
            notes.append(f"unknown l1_pattern {f.l1_pattern!r}: treated as no L1 transfer")
            f.l1_transfer, f.l1_pattern, f.l1_source_ar = False, None, None
        problems = arabic_note_problems(f.ar_note)
        if problems:
            notes.append(f"blanked ar_note for {f.pattern_key}: {', '.join(problems)}")
            f.ar_note = ""
        if f.recast_form and not f.recast_meaning:
            f.recast_meaning = f.alternatives[0].text if f.alternatives else ""
        seen_spans.add(normalize_text(f.span))
        findings.append(f)

    used: list[UsedTarget] = []
    for u in analysis.used_targets:
        u = u.model_copy(deep=True)
        if u.target_id not in target_ids:
            notes.append(f"dropped used_target {u.target_id}: not an active target")
            continue
        if normalize_text(u.form_used) not in norm_learner:
            notes.append(f"dropped used_target {u.target_id}: form_used not verbatim in learner text")
            continue
        if not u.echo and looks_like_echo(u.form_used, recent_texts):
            notes.append(f"forced echo=true for {u.target_id}: wording appeared in the last turns")
            u.echo = True
        used.append(u)

    cleaned = analysis.model_copy(update={"findings": findings, "used_targets": used})
    return cleaned, notes


# --------------------------------------------------------------------------------------
# Responder checks
# --------------------------------------------------------------------------------------

_BANNED_OPENERS = re.compile(
    r"^\s*(great question|absolutely[!,.]?|certainly[!,.]?|of course[!,.]?|i understand|that'?s interesting|sure thing)\b",
    re.I,
)
_ASSISTANT_ISMS = re.compile(r"\b(as an ai|i'?d be happy to help|feel free to|i hope this helps)\b", re.I)
_TEACHING = re.compile(
    r"\b(your english|grammar|pronunciation|word choice|nice phrase|good phrase|you could say|you can say|we say|"
    r"the correct (way|form)|native speakers?)\b",
    re.I,
)
_RECAST_SIGNALS = re.compile(r"\b(you mean|i think you mean|you could say|we say|you can say)\b", re.I)
_MARKDOWN = re.compile(r"(\*\*|__|^\s*[-•*]\s|^\s*#{1,6}\s|`)", re.M)
_STAGE_DIRECTION = re.compile(r"(\*[^*\n]{1,30}\*|\((?:laughs|smiles|sighs|chuckles|grins|nods)[^)]*\))", re.I)
_TAG_QUESTION_RE = re.compile(
    r"(?:(?:,|—|-)\s*|^)(?:right|you know|huh|eh|okay|ok|yeah|no|innit|isn't it|aren't you|don't you|doesn't it|"
    r"didn't it|wasn't it|won't you|can't you|haven't you|hasn't it|shouldn't we)\s*\?$",
    re.I,
)


def is_tag_question(sentence: str) -> bool:
    """A tag question ("..., right?") is speech texture, not an interview question."""
    return bool(_TAG_QUESTION_RE.search(sentence.strip()))


def real_questions(text: str) -> list[str]:
    return [q for q in question_sentences(text) if not is_tag_question(q)]


def check_reply(
    reply: ResponderReply,
    brief: DirectorBrief,
    *,
    forbidden_phrases: Sequence[str] = (),
) -> list[str]:
    """Violations of the director brief. An empty list means the reply may be sent."""
    v: list[str] = []
    text = reply.text
    n_words = word_count(text)
    target = brief.length_words

    limit = int(round(target * 1.3)) + 2
    if n_words > limit:
        v.append(f"too long: {n_words} words, the brief asked for about {target} (max {limit})")
    if brief.dialogue_move == "REACT" and n_words > 12:
        v.append("REACT must be 12 words or fewer")
    if brief.dialogue_move == "BACKCHANNEL" and n_words > 5:
        v.append("BACKCHANNEL must be 5 words or fewer")

    # question discipline
    qs = real_questions(text)
    if brief.end_with_question:
        if not ends_with_question(reply.bubbles[-1]):
            v.append("the move requires ending with a question")
        if len(qs) != 1:
            v.append("ask exactly one question")
    else:
        last_is_plain_question = ends_with_question(reply.bubbles[-1]) and not is_tag_question(
            question_sentences(reply.bubbles[-1])[-1] if question_sentences(reply.bubbles[-1]) else ""
        )
        if qs or last_is_plain_question:
            v.append("end_with_question is false: do not ask a question")

    if _BANNED_OPENERS.search(text):
        v.append("starts with a banned assistant opener")
    if _ASSISTANT_ISMS.search(text):
        v.append("contains assistant language")
    if _TEACHING.search(text):
        v.append("talks about English or teaches")
    if _MARKDOWN.search(text):
        v.append("uses markdown or list formatting")
    if _STAGE_DIRECTION.search(text):
        v.append("uses stage directions")
    if first_word(reply.bubbles[0]) in {w.lower() for w in brief.avoid_openers if w}:
        v.append("starts with one of the avoided openers")

    if brief.recast is not None:
        if _RECAST_SIGNALS.search(text):
            v.append("recast must be silent: no 'you mean' / 'we say'")
        if word_count(brief.recast.learner_said) >= 2 and contains_phrase(text, brief.recast.learner_said):
            v.append("repeats the learner's non-native wording")

    for phrase in forbidden_phrases:
        if phrase and contains_phrase(text, phrase):
            v.append(f"uses a stealth-target expression: {phrase!r}")
    return v


# --------------------------------------------------------------------------------------
# Telegram HTML validation (cards and debriefs)
# --------------------------------------------------------------------------------------

ALLOWED_TAGS = frozenset({"b", "i", "u", "code", "blockquote", "tg-spoiler"})
_RAW_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
CARD_MAX_VISIBLE = 600
DEBRIEF_MAX_VISIBLE = 900


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: D401
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}> not allowed")
        if attrs:
            self.problems.append(f"attributes not allowed on <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"unbalanced </{tag}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def validate_telegram_html(html: str, *, max_visible: int, allow_arabic: bool = True) -> list[str]:
    problems: list[str] = []
    if _RAW_AMP.search(html):
        problems.append("unescaped '&'")
    stripped = re.sub(r"<[^>]*>", "", html)
    if "<" in stripped or ">" in stripped:
        problems.append("unescaped '<' or '>'")
    checker = _TagChecker()
    checker.feed(html)
    checker.close()
    problems += checker.problems
    if checker.stack:
        problems.append(f"unclosed tags: {checker.stack}")

    visible = strip_tags(html)
    if len(visible) > max_visible:
        problems.append(f"too long: {len(visible)} visible characters (max {max_visible})")
    emojis = _EMOJI_RE.findall(visible)
    if len(emojis) > 1 or (emojis and not _EMOJI_RE.match(visible.lstrip())):
        problems.append("at most one emoji, only at the very start")
    if re.search(r"(^|\n)\s*([-•*]|\d+[.)])\s", visible):
        problems.append("bullets or numbered lists are not allowed")

    for m in re.finditer(r"<tg-spoiler>(.*?)</tg-spoiler>", html, re.S):
        problems += [f"arabic spoiler: {p}" for p in arabic_note_problems_in_spoiler(m.group(1))]
    if not allow_arabic and _AR_LETTER_RE.search(visible):
        problems.append("Arabic text present but include_arabic is false")
    return problems


def arabic_note_problems_in_spoiler(text: str) -> list[str]:
    # A spoiler may hold longer text than a single ar_note (a debrief can hold several), so only
    # the tone rules apply here, not the 200-character cap.
    norm = _ar_norm(strip_tags(text))
    out: list[str] = []
    if _AR_BANNED_RE.search(norm):
        out.append("stigmatising wording")
    if any(p in norm for p in _AR_BANNED_PHRASES):
        out.append("false absolute or stigmatising phrase")
    if _AR_GENERALISE_RE.search(norm):
        out.append("generalises about Arabs")
    return out


def forbidden_phrases_for(targets: Sequence[Expression]) -> list[str]:
    """Everything the character must not say when it is creating a need for these targets."""
    out: list[str] = []
    for t in targets:
        out.append(t.form)
        out.extend(t.acceptable_forms)
    return [p for p in dict.fromkeys(out) if p]
