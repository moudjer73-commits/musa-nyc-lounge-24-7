import asyncio
from collections import OrderedDict
import io
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import edge_tts

logger = logging.getLogger(__name__)

TWIN_VOICE = "en-US-JennyNeural"

PARTNER_VOICES: dict[str, tuple[str, str, str]] = {
    "casual_sharp": ("en-US-GuyNeural", "+2%", "+0Hz"),
    "exec_clock": ("en-US-AriaNeural", "+5%", "+0Hz"),
    "seminar_skeptic": ("en-US-ChristopherNeural", "-4%", "-2Hz"),
}

_PHONETIC_TAGS_RE = re.compile(r"\[(?:ɾ|t̚|d̚|pʰ|tʰ|kʰ|ʔ)\]")
_PROSODIC_MARKS_RE = re.compile(r"[ˈˌ↘↗➡️]")


class VoiceClipCache:
    def __init__(self, max_per_user: int = 6, max_bytes: int = 1_600_000) -> None:
        self.max_per_user = max_per_user
        self.max_bytes = max_bytes
        self._store: dict[int, OrderedDict[str, tuple[bytes, str]]] = {}
        self._latest: dict[int, tuple[bytes, str]] = {}

    def stash(self, user_id: int, cid: str, audio_bytes: bytes, target: str) -> None:
        if not audio_bytes or len(audio_bytes) > self.max_bytes:
            return
        clean_t = (target or "").strip()
        self._latest[user_id] = (audio_bytes, clean_t)
        if len(self._store) > 25 and user_id not in self._store:
            self._store.pop(next(iter(self._store)), None)
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
    return s[:700]


async def _edge_stream_once(
    clean: str, v_name: str, v_rate: str, v_pitch: str
) -> bytes | None:
    kw = {"voice": v_name, "rate": v_rate}
    if v_pitch != "+0Hz":
        kw["pitch"] = v_pitch
    comm = edge_tts.Communicate(clean, **kw)
    chunks: list[bytes] = []
    async for item in comm.stream():
        if item.get("type") == "audio" and item.get("data"):
            chunks.append(item["data"])
    return b"".join(chunks) if chunks else None


def _gtts_sync_fallback(clean: str, rate: str) -> bytes | None:
    try:
        from gtts import gTTS
        buf = io.BytesIO()
        gTTS(text=clean, lang="en", tld="com", slow=False).write_to_fp(buf)
        raw_mp3 = buf.getvalue()
        if not raw_mp3 or not shutil.which("ffmpeg"):
            return raw_mp3 or None
        pct = 0
        m = re.match(r"^([+-]?\d+)%$", rate.strip())
        if m:
            pct = int(m.group(1))
        tempo = max(0.75, min(1.30, 1.0 + (pct / 100.0)))
        if abs(tempo - 1.0) < 0.03:
            return raw_mp3
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0", "-filter:a", f"atempo={tempo:.2f}",
                "-f", "mp3", "pipe:1",
            ],
            input=raw_mp3,
            capture_output=True,
            timeout=7,
        )
        return proc.stdout if (proc.returncode == 0 and proc.stdout) else raw_mp3
    except Exception:
        return None


async def synthesize_mp3(
    text: str,
    voice: str = TWIN_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes | None:
    clean = prepare_for_tts(text)
    if not clean:
        return None
    try:
        res = await asyncio.wait_for(
            _edge_stream_once(clean, voice, rate, pitch), timeout=5.5
        )
        if res:
            return res
    except Exception:
        pass
    return await asyncio.to_thread(_gtts_sync_fallback, clean, rate)


async def synthesize_partner_voice(
    text: str, angle: str = "casual_sharp"
) -> bytes | None:
    voice, rate, pitch = PARTNER_VOICES.get(angle, PARTNER_VOICES["casual_sharp"])
    return await synthesize_mp3(text, voice=voice, rate=rate, pitch=pitch)


async def synthesize_twin_voice(text: str, rate: str = "+0%") -> bytes | None:
    return await synthesize_mp3(text, voice=TWIN_VOICE, rate=rate)


async def synthesize_shadow_drill(phrase: str) -> bytes | None:
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
                "[0:a]aresample=24000,apad=pad_dur=0.60[a0];"
                "[1:a]aresample=24000,asplit=2[n1][n2];"
                "[n1]apad=pad_dur=0.75[a1];"
                "[a0][a1][n2]concat=n=3:v=0:a=1[out]"
            )
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", p1, "-i", p2, "-filter_complex", filt,
                "-map", "[out]", "-codec:a", "libmp3lame", "-q:a", "4", po,
            ]
            r = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, timeout=10
            )
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


async def merge_mirror_clips(
    learner_bytes: bytes, native_mp3: bytes
) -> bytes | None:
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
            "dynaudnorm=f=150:g=11,apad=pad_dur=0.55[u0];"
            "[1:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,"
            "dynaudnorm=f=150:g=11[n0];"
            "[u0][n0]concat=n=2:v=0:a=1[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", p_usr, "-i", p_nat, "-filter_complex", filt,
            "-map", "[out]", "-codec:a", "libmp3lame", "-q:a", "4", p_out,
        ]
        r = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=12
        )
        if r.returncode == 0 and Path(p_out).stat().st_size > 300:
            return Path(p_out).read_bytes()
    except Exception:
        pass
    finally:
        for pf in (p_usr, p_nat, p_out):
            if os.path.exists(pf):
                os.remove(pf)
    return native_mp3


async def synthesize_audio_mirror(
    learner_bytes: bytes, native_text: str
) -> bytes | None:
    native_mp3 = await synthesize_twin_voice(native_text, rate="+2%")
    if not native_mp3:
        return None
    return await merge_mirror_clips(learner_bytes, native_mp3)


async def synthesize_ear_clip(
    text: str, angle: str = "casual_sharp", slow: bool = False
) -> bytes | None:
    voice, _, pitch = PARTNER_VOICES.get(angle, PARTNER_VOICES["casual_sharp"])
    rate = "-15%" if slow else "+14%"
    return await synthesize_mp3(text, voice=voice, rate=rate, pitch=pitch)
