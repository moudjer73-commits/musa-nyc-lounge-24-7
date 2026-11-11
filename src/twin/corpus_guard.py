import re

SPOKEN_US_DNA = (
    "AUTHENTIC SPOKEN AMERICAN ENGLISH DNA (ZERO AFFECTATION):\n"
    "- Speak plain, relaxed, everyday 2026 American English—how educated Americans actually talk out loud when nobody is performing.\n"
    "- Prefer core spoken phrasal & delexicalized verbs (get, end up, turn out, figure out, work out, hold off, run into, come down to, make sense, pan out, walk through) over heavy Latinate verbs.\n"
    "- Use natural contractions always (I'm, don't, that's, it'll, wouldn't) and occasional spoken ellipsis ('Makes sense.', 'Depends how they fund it.').\n"
    "- Use discourse markers sparingly (at most ONE 'I mean,', 'Honestly,', 'Fair enough,', or 'The thing is,' when natural—never stack them).\n"
    "- STRICT ANTI-CARICATURE BAN:\n"
    "  * NO forced internet/TikTok slang ('deadass', 'no cap', 'bet', 'fam', 'bruh', 'lowkey', 'spill the tea', 'slay').\n"
    "  * NO corporate LinkedIn clichés ('circle back', 'synergy', 'synergize', 'move the needle', 'boil the ocean', 'low-hanging fruit', 'bandwidth', 'actionable deliverables', 'deep dive').\n"
    "  * NO stiff written-only thesaurus words ('utilize', 'procure', 'consume', 'commence', 'facilitate', 'endeavor', 'posit', 'vis-à-vis', 'heretofore', 'pertaining to', 'thusly').\n"
    "  * NO fake roleplay props (never mention 'Chicago air', 'Manhattan coffee', 'Ann Arbor weather', or 'staring at spreadsheets')."
)

REGISTER_CORPUS_RULES = (
    "STRICT CORPUS-VERIFIED REGISTER RULES (NEVER INVENT FAKE EXPRESSIONS):\n"
    "1. REALITY CHECK FIRST: Every single word and chunk MUST be high-frequency, idiomatic spoken American English actually heard daily in the US.\n"
    "2. HONEST REGISTER OVERLAP: In real life, Americans say concrete everyday things ('I just picked up some fruit', 'I'm exhausted today', 'Traffic was awful') almost identically to a friend, a manager, and a professor. "
    "If the utterance is everyday/concrete, set is_register_neutral=true (or same_across_registers=true) and keep the plain everyday words—NEVER swap 'fruit' for 'produce', 'buy/pick up' for 'procure', or 'eat' for 'consume'!\n"
    "3. HOW REAL US REGISTERS SHIFT (WHEN THEY DO SHIFT): Registers in spoken US English shift through STANCE, HEDGING, and DIRECTNESS—never through fancy thesaurus vocabulary:\n"
    "   - Everyday (Malik): Direct, relaxed, elliptical ('Honestly, I don't buy that.', 'No way that works on time.').\n"
    "   - Workplace (Claire): Plain words + collaborative framing & practical focus ('My main worry is whether that holds up on this timeline.', 'Let's hold off until we check the budget.').\n"
    "   - Seminar (Prof. Vance): Plain spoken grammar + analytical hedging & mechanism focus ('I'm not convinced that holds up once you factor in local budgets.', 'How does that play out on the ground?').\n"
    "4. ZERO HALLUCINATED IDIOMS: Never coin metaphors or combine idioms. When in doubt, choose the simpler, plainer spoken phrasing."
)

CONTRIVED_BUZZ_RE = re.compile(
    r"\b(?:circle\s+back|move\s+the\s+needle|boil\s+the\s+ocean|low-hanging\s+fruit|"
    r"synerg(?:y|ize|ies)|actionable\s+deliverables|ping\s+me\s+offline|deadass|no\s+cap|"
    r"spill\s+the\s+tea|heretofore|aforementioned|notwithstanding|behooves|thusly|"
    r"one\s+might\s+posit|picked\s+up\s+produce|procure(?:d|s)?)\b",
    re.IGNORECASE,
)

_STIFF_REPLACEMENTS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\butilizing\b", re.IGNORECASE), "using"),
    (re.compile(r"\butilized\b", re.IGNORECASE), "used"),
    (re.compile(r"\butilizes\b", re.IGNORECASE), "uses"),
    (re.compile(r"\butilize\b", re.IGNORECASE), "use"),
    (re.compile(r"\bcommencing\b", re.IGNORECASE), "starting"),
    (re.compile(r"\bcommenced\b", re.IGNORECASE), "started"),
    (re.compile(r"\bcommence\b", re.IGNORECASE), "start"),
    (re.compile(r"\bwith\s+regards?\s+to\b", re.IGNORECASE), "when it comes to"),
    (re.compile(r"\bin\s+regard\s+to\b", re.IGNORECASE), "when it comes to"),
    (re.compile(r"\bpertaining\s+to\b", re.IGNORECASE), "around"),
    (re.compile(r"\bvis-[àa]-vis\b", re.IGNORECASE), "compared to"),
    (re.compile(r"\bprior\s+to\b", re.IGNORECASE), "before"),
    (re.compile(r"\bsubsequent\s+to\b", re.IGNORECASE), "after"),
)


def _match_case(repl: str, original: str) -> str:
    if original and original[0].isupper():
        return repl[0].upper() + repl[1:]
    return repl


def scrub_contrived_english(text: str) -> str:
    if not text:
        return ""
    s = text.strip()
    for pat, repl in _STIFF_REPLACEMENTS:
        s = pat.sub(lambda m: _match_case(repl, m.group(0)), s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def has_contrived_buzz(text: str) -> bool:
    return bool(CONTRIVED_BUZZ_RE.search(text or ""))
