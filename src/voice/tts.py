import asyncio
from collections import OrderedDict
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import edge_tts

logger = logging.getLogger(__name__)

# صوت مستقل للمدرب والمحاكاة والمرآة لتمييزه سمعياً عن المحاورين الثلاثة
TWIN_VOICE = "en-US-JennyNeural"

# أصوات أمريكية متمايزة لكل شريك وسجل لغوي (Voice, Rate, Pitch)
PARTNER_VOICES: dict[str, tuple[str, str, str]] = {
    "casual_sharp": ("en-US-GuyNeural", "+2%", "+0Hz"),
    "exec_clock": ("en-US-AriaNeural", "+5%", "+0Hz"),
    "seminar_skeptic": ("en-US-ChristopherNeural", "-4%", "-2Hz"),
}

_PHONETIC_TAGS_RE = re.compile(r"\[(?:ɾ|t̚|d̚|pʰ|tʰ|kʰ|ʔ)\]")
_PROSODIC_MARKS_RE = re.compile(r"[ˈˌ↘↗➡️]")


class VoiceClipCache:
    """Bounded RAM-only cache for recent user voice notes (zero DB bloat)."""

    def __init__(self, max_per_user: int = 6, max_bytes: int = 1_600_000) -> None:
        self.max_per_user = max_per_user
        self.max_bytes = max_bytes
        self._store: dict[int, OrderedDict[str, tuple[bytes, str]]] = {}
        self._latest: dict[int, tuple[bytes, str]] = {}

    def stash(self, user_id: int, cid: str, audio_bytes: bytes, target_text: str) -> None:
        if not audio_bytes or len(audio_bytes) > self.max_bytes:
            return
        clean_t = (target_text or "").strip()
        self._latest[user_id] = (audio_bytes, clean_t)
        if len(self._store) > 25 and user_id not in self._store:
            oldest_u = next(iter(self._store))
            self._store.pop(oldest_u, None)
        bucket = self._store.setdefault(user_id, OrderedDict())
        if cid:
            bucket[cid] = (audio_bytes, clean_t)
            bucket.move_to_end(cid)
            while len(bucket) > self.max_per_user:
                bucket.popitem(last=False)

    def fetch(self, user_id: int, cid: str = "") -> tuple[bytes, str] | None:
        bucket = self._store.get(user_id)
        if cid and bucket and cid in bucket:
            return bucket[cid]
        return self._latest.get(user_id)


VOICE_CLIP_CACHE = VoiceClipCache()


def prepare_for_tts(text: str) -> str:
    if not text:
        return ""
    s = _PHONETIC_TAGS_RE.sub("", text)
    s = _PROSODIC_MARKS_RE.sub("", s)
    s = s.replace("‿", " ").replace("|", ", ")
    s = re.sub(r"\[([^\[\]]+)\]", r"\1", s)
    s = re.sub(r"[*_`~<>]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:750]


async def synthesize_mp3(
    text: str,
    voice: str = TWIN_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes | None:
    clean = prepare_for_tts(text)
    if not clean:
        return None
    attempts = [(voice, rate, pitch)]
    if voice != TWIN_VOICE or pitch != "+0Hz":
        attempts.append((TWIN_VOICE, rate, "+0Hz"))

    for v_name, v_rate, v_pitch in attempts:
        try:
            kw = {"voice": v_name, "rate": v_rate}
            if v_pitch != "+0Hz":
                kw["pitch"] = v_pitch
            comm = edge_tts.Communicate(clean, **kw)
            chunks: list[bytes] = []
            async for item in comm.stream():
                if item.get("type") == "audio" and item.get("data"):
                    chunks.append(item["data"])
            if chunks:
                return b"".join(chunks)
        except Exception as exc:
            logger.debug("TTS retry (%s): %s", v_name, exc)
    return None


async def synthesize_partner_voice(text: str, angle: str = "casual_sharp") -> bytes | None:
    voice, rate, pitch = PARTNER_VOICES.get(angle, PARTNER_VOICES["casual_sharp"])
    return await synthesize_mp3(text, voice=voice, rate=rate, pitch=pitch)


async def synthesize_twin_voice(text: str, rate: str = "+0%") -> bytes | None:
    return await synthesize_mp3(text, voice=TWIN_VOICE, rate=rate)


async def synthesize_shadow_drill(phrase: str) -> bytes | None:
    """3-Beat Connected US Drill: [-14% linked] -> [+3% US] -> [+3% shadow]."""
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
    if guided_mp3 and natural_mp3:
        return guided_mp3 + natural_mp3 + natural_mp3
    return natural_mp3 or guided_mp3


async def merge_mirror_clips(learner_bytes: bytes, native_mp3: bytes) -> bytes | None:
    """Normalizes loudness & stitches [1] Learner Voice -> 0.6s gap -> [2] Native US."""
    if not learner_bytes or not native_mp3 or not shutil.which("ffmpeg"):
        return native_mp3
    fd1, p_usr = tempfile.mkstemp(suffix=".ogg")
    fd2, p_nat = tempfile.mkstemp(suffix=".mp3")
    fd3, p_out = tempfile.mkstemp(suffix=".mp3")
    os.close(fd1); os.close(fd2); os.close(fd3)
    try:
        Path(p_usr).write_bytes(learner_bytes)
        Path(p_nat).write_bytes(native_mp3)
        filt = (
            "[0:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,"
            "dynaudnorm=f=150:g=11,apad=pad_dur=0.60[u0];"
            "[1:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,"
            "dynaudnorm=f=150:g=11[n0];"
            "[u0][n0]concat=n=2:v=0:a=1[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", p_usr, "-i", p_nat, "-filter_complex", filt,
            "-map", "[out]", "-codec:a", "libmp3lame", "-q:a", "4", p_out,
        ]
        r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=14)
        if r.returncode == 0 and Path(p_out).stat().st_size > 300:
            return Path(p_out).read_bytes()
    except Exception as exc:
        logger.warning("Audio mirror stitch fallback: %s", exc)
    finally:
        for pf in (p_usr, p_nat, p_out):
            if os.path.exists(pf):
                os.remove(pf)
    return native_mp3


async def synthesize_audio_mirror(learner_bytes: bytes, native_text: str) -> bytes | None:
    native_mp3 = await synthesize_twin_voice(native_text, rate="+2%")
    if not native_mp3:
        return None
    return await merge_mirror_clips(learner_bytes, native_mp3)
