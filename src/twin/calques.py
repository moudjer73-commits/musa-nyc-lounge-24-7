import re

ARABIC_RE = re.compile(r"[\u0600-\u06FF]+")
FRENCH_CUE_RE = re.compile(
    r"(?:[àâçéèêëîïôûùüÿœæ]|\b(?:c'est|c’est|par rapport|comment dire|en fait|du coup|au niveau|ça)\b)",
    re.IGNORECASE,
)

REPAIR_RE = re.compile(
    r"(?:\bdidn'?t\s+(?:get|catch|follow)\b|\bdon'?t\s+(?:get|understand|follow)\b|"
    r"\bwhat\s+do\s+you\s+mean\b|\bwait\s*,?\s*what\b|\bcome\s+again\b|\bhuh\?*$|"
    r"\bwhat\s+does\s+that\s+mean\b|\blost\s+me\b|ما\s*فهمت|مفهمتش|مش\s*فاهم)",
    re.IGNORECASE,
)

TRANSFER_PATTERNS = (
    (r"\bat the level of\b", "when it comes to / across", "fr_calque"),
    (r"\bin the frame of\b", "as part of / under", "fr_calque"),
    (r"\bput in question\b", "call into question / challenge", "fr_calque"),
    (r"\btake in consideration\b", "take into account / factor in", "fr_calque"),
    (r"\btake a decision\b|\btook a decision\b", "make a call / decide", "fr_calque"),
    (r"\bthis problematic\b|\bthe problematic\b", "this core tension / puzzle", "fr_calque"),
    (r"\bgive importance to\b", "prioritize / put real weight on", "ar_fr_calque"),
    (r"\bfrom another side\b|\bon another side\b", "on the flip side / then again", "ar_fr_calque"),
    (r"\baccording to my opinion\b|\baccording to me\b", "the way I see it / in my view", "ar_fr_calque"),
    (r"\bi have another opinion\b", "I see it a bit differently / here's my take", "ar_calque"),
    (r"\bcomplete my idea\b|\bcontinue my idea\b", "finish my thought", "ar_calque"),
    (r"\bmake a discussion\b|\bdo a discussion\b", "talk it through / hash it out", "ar_calque"),
    (r"\bin the same time\b", "at the same time", "ar_fr_calque"),
    (r"\bdepend from\b|\bdepends from\b", "depend on / hinge on", "transfer"),
    (r"\bafraid from\b", "worried about / afraid of", "ar_calque"),
    (r"\barrive to\b|\barrived to\b", "get to / make it to", "ar_calque"),
    (r"\bdiscuss about\b", "talk about / get into", "transfer"),
    (r"\bexplain me\b", "walk me through / explain to me", "fr_calque"),
    (r"\bopen the light\b|\bclose the light\b", "turn on / turn off the light", "ar_calque"),
    (r"\bpass an exam\b", "take an exam (pass = succeed in US)", "fr_trap"),
    (r"\bassist to\b", "attend / sit in on", "fr_trap"),
    (r"\bactual\b(?=\s+(?:situation|crisis|budget|system|data))", "current / right now", "fr_trap"),
)


def detect_code_switch(text: str) -> bool:
    return bool(ARABIC_RE.search(text) or FRENCH_CUE_RE.search(text))


def is_repair_request(text: str) -> bool:
    return bool(REPAIR_RE.search(text.strip()))


def scan_transfers(text: str) -> list[tuple[str, str, str]]:
    hits = []
    for pat, up, tag in TRANSFER_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            hits.append((m.group(0), up, tag))
    return hits
