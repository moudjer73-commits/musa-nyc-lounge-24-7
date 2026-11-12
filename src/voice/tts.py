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


async def synthesize_shadow_drill(phrase: str) -> bytes | None:
    """3-Beat Prosody Lab Drill: [1] Gentle linked -14% -> [2] Natural US +3% -> [3] Shadow-along +3%."""
    import shutil, subprocess
    from pathlib import Path
    clean = prepare_for_tts(phrase)
    if not clean:
        return None
    guided_mp3, natural_mp3 = await asyncio.gather(
        synthesize_mp3(clean, voice=TWIN_VOICE, rate="-14%"),
        synthesize_mp3(clean, voice=TWIN_VOICE, rate="+3%"),
    )
    if guided_mp3 and natural_mp3 and shutil.which("ffmpeg"):
        fd1, p1 = tempfile.mkstemp(suffix=".mp3")
        fd2, p2 = tempfile.mkstemp(suffix=".mp3")
        fd3, po = tempfile.mkstemp(suffix=".mp3")
        os.close(fd1); os.close(fd2); os.close(fd3)
        try:
            Path(p1).write_bytes(guided_mp3)
            Path(p2).write_bytes(natural_mp3)
            filt = (
                "[0:a]apad=pad_dur=0.65[a0];"
                "[1:a]asplit=2[n1][n2];"
                "[n1]apad=pad_dur=0.80[a1];"
                "[a0][a1][n2]concat=n=3:v=0:a=1[out]"
            )
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", p1, "-i", p2, "-filter_complex", filt,
                "-map", "[out]", "-codec:a", "libmp3lame", "-q:a", "4", po,
            ]
            r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=12)
            if r.returncode == 0 and Path(po).stat().st_size > 300:
                return Path(po).read_bytes()
        except Exception:
            pass
        finally:
            for pf in (p1, p2, po):
                if os.path.exists(pf):
                    os.remove(pf)
    return (guided_mp3 + natural_mp3 + natural_mp3) if (guided_mp3 and natural_mp3) else (natural_mp3 or guided_mp3)
