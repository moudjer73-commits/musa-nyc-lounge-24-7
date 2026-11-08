"""Voice output for Alex (free edge-tts). Best-effort by design: a TTS failure must never break a text reply."""
import asyncio
import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)

ALEX_VOICE = "en-US-AndrewMultilingualNeural"
MAYA_VOICE = "en-US-AvaMultilingualNeural"
TTS_TIMEOUT_S = 25
MAX_TTS_CHARS = 900


def clean_for_tts(text: str) -> str:
    """Strip feedback blocks / markdown so Alex only speaks the conversational part."""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("💡", "🎙️ You said:", "📊", "───")):
            continue
        lines.append(s)
    joined = " ".join(lines)
    joined = re.sub(r"[*_`#~>|•]", " ", joined)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined[:MAX_TTS_CHARS] if joined else "Hey, I'm Alex. Let's keep practicing."


async def synthesize_mp3_bytes(text: str, voice: str = ALEX_VOICE, rate: str = "+0%") -> bytes | None:
    """Return MP3 bytes, or None when TTS is unavailable (blocked IP, no network, package missing)."""
    path = ""
    try:
        import edge_tts

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        comm = edge_tts.Communicate(clean_for_tts(text), voice=voice, rate=rate)
        await asyncio.wait_for(comm.save(path), timeout=TTS_TIMEOUT_S)
        with open(path, "rb") as fh:
            data = fh.read()
        return data or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Alex TTS unavailable: %s", exc)
        return None
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)  # audio is never kept on disk
            except OSError:
                pass


async def send_alex_voice(message, text: str) -> bool:
    """Reply with Alex's voice (Telegram accepts MP3 for sendVoice). Returns False if skipped/failed."""
    audio = await synthesize_mp3_bytes(text)
    if not audio:
        return False
    try:
        await message.reply_voice(voice=audio)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sending Alex voice failed: %s", exc)
        return False
