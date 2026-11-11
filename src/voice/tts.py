import asyncio
import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)

PARTNER_VOICES: dict[str, tuple[str, str]] = {
    "casual_sharp": ("en-US-AndrewMultilingualNeural", "+4%"),
    "seminar_skeptic": ("en-US-ChristopherNeural", "+1%"),
    "exec_clock": ("en-US-EmmaMultilingualNeural", "+5%"),
}
TWIN_VOICE = "en-US-BrianMultilingualNeural"
FALLBACK_VOICE = "en-US-AndrewMultilingualNeural"
TTS_TIMEOUT_S = 20.0
MAX_TTS_CHARS = 950

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
SHORTHAND = (
    (r"\btbh\b", "to be honest"),
    (r"\bidk\b", "I don't know"),
    (r"\brn\b", "right now"),
    (r"\bngl\b", "not gonna lie"),
    (r"\bimo\b", "in my opinion"),
    (r"\blol\b", ""),
)


def prepare_for_tts(text: str) -> str:
    if not text:
        return ""
    s = EMOJI_RE.sub("", text)
    s = re.sub(r"[*`#~>|•]|(?<!\w)_|_(?!\w)", " ", s)
    s = s.replace("—", ", ").replace(" – ", ", ")
    for pat, repl in SHORTHAND:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_TTS_CHARS]


async def _edge_save(text: str, voice: str, rate: str) -> bytes | None:
    import edge_tts

    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        comm = edge_tts.Communicate(text, voice=voice, rate=rate)
        await asyncio.wait_for(comm.save(path), timeout=TTS_TIMEOUT_S)
        data = open(path, "rb").read()
        return data if data else None
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


async def synthesize_mp3(text: str, voice: str = FALLBACK_VOICE, rate: str = "+2%") -> bytes | None:
    cleaned = prepare_for_tts(text)
    if not cleaned or not any(ch.isalnum() for ch in cleaned):
        return None
    for candidate_voice in ([voice, FALLBACK_VOICE] if voice != FALLBACK_VOICE else [voice]):
        try:
            out = await _edge_save(cleaned, candidate_voice, rate)
            if out:
                return out
        except Exception as exc:
            logger.warning("TTS failed on %s: %s", candidate_voice, exc)
    return None


async def synthesize_partner_voice(text: str, angle: str = "casual_sharp") -> bytes | None:
    voice, rate = PARTNER_VOICES.get(angle, (FALLBACK_VOICE, "+3%"))
    return await synthesize_mp3(text, voice=voice, rate=rate)


async def synthesize_twin_voice(text: str) -> bytes | None:
    return await synthesize_mp3(text, voice=TWIN_VOICE, rate="+3%")
