import math, re, shutil, struct, wave
from abc import ABC, abstractmethod
from pathlib import Path
from src.models.listening_podcast import PodcastTurn

TELEGRAM_ALLOWED_FORMATS = (".ogg", ".mp3", ".m4a", ".wav")
TELEGRAM_MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50 MB Telegram Bot API limit

SPOKEN_TTS_NORMALIZATION = {
    r"\bi dunno\b": "Iuhunno",
    r"\bgonna\b": "gunna",
    r"\bwanna\b": "wunna",
    r"\bkinda\b": "kinda",
    r"\bgotta\b": "godda",
    r"\bhafta\b": "hafta",
}


def normalize_for_natural_tts(text: str) -> str:
    """Adjusts colloquial spellings so TTS engines read reductions smoothly without robotic spelling."""
    out = text
    for pattern, phonetic in SPOKEN_TTS_NORMALIZATION.items():
        out = re.sub(pattern, phonetic, out, flags=re.IGNORECASE)
    return out


def check_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def validate_telegram_audio(file_path: Path) -> tuple[bool, str]:
    if not file_path.exists():
        return False, "Audio file does not exist"
    if file_path.suffix.lower() not in TELEGRAM_ALLOWED_FORMATS:
        return False, f"Unsupported Telegram format: {file_path.suffix}"
    size = file_path.stat().st_size
    if size == 0 or size > TELEGRAM_MAX_AUDIO_BYTES:
        return False, f"Invalid audio file size: {size} bytes"
    return True, "ok"


class BaseTTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize_turns(
        self, turns: list[PodcastTurn], output_path: Path, voice_map: dict[str, str], slow: bool = False
    ) -> Path:
        raise NotImplementedError


class EdgeTTSProvider(BaseTTSProvider):
    name = "edge_tts"

    async def synthesize_turns(
        self, turns: list[PodcastTurn], output_path: Path, voice_map: dict[str, str], slow: bool = False
    ) -> Path:
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)
        rate_str = "-15%" if slow else "+0%"
        chunks_bytes = bytearray()
        for turn in turns:
            voice = voice_map.get(turn.speaker, "en-US-AndrewNeural")
            norm_text = normalize_for_natural_tts(turn.text)
            comm = edge_tts.Communicate(norm_text, voice=voice, rate=rate_str)
            async for packet in comm.stream():
                if packet["type"] == "audio":
                    chunks_bytes.extend(packet["data"])
        if not chunks_bytes:
            raise RuntimeError("edge-tts produced empty audio stream")
        output_path.write_bytes(bytes(chunks_bytes))
        return output_path


class OfflineWaveFallbackTTSProvider(BaseTTSProvider):
    """100% offline deterministic acoustic synthesizer fallback when cloud/unofficial TTS fails."""

    name = "offline_wave_fallback"

    async def synthesize_turns(
        self, turns: list[PodcastTurn], output_path: Path, voice_map: dict[str, str], slow: bool = False
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 16000
        frames = bytearray()
        speed_factor = 1.2 if slow else 1.0
        for turn in turns:
            freq = 190.0 if turn.speaker == "Host_A" else 245.0
            duration_s = max(0.25, min(1.2, len(turn.text.split()) * 0.06 * speed_factor))
            n_samples = int(sample_rate * duration_s)
            for i in range(n_samples):
                val = int(6000 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
                frames.extend(struct.pack("<h", val))
            pause_samples = int(sample_rate * (turn.pause_after_ms / 1000.0))
            frames.extend(b"\x00\x00" * pause_samples)

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(frames))
        return output_path


class SwappableTTSManager:
    def __init__(
        self,
        primary: BaseTTSProvider | None = None,
        fallback: BaseTTSProvider | None = None,
    ) -> None:
        self.primary = primary or EdgeTTSProvider()
        self.fallback = fallback or OfflineWaveFallbackTTSProvider()

    async def render_episode_audio(
        self,
        turns: list[PodcastTurn],
        output_path: Path,
        voice_map: dict[str, str],
        slow: bool = False,
    ) -> tuple[Path, str]:
        try:
            path = await self.primary.synthesize_turns(turns, output_path, voice_map, slow=slow)
            ok, _ = validate_telegram_audio(path)
            if ok:
                return path, self.primary.name
        except Exception:
            pass
        fallback_path = output_path.with_suffix(".wav")
        path = await self.fallback.synthesize_turns(turns, fallback_path, voice_map, slow=slow)
        return path, self.fallback.name
