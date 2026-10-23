# -*- coding: utf-8 -*-
"""
media_engine.py — NotebookLM-Style Trio Podcast Producer (Parallel Async Synthesis,
FFmpeg Silence Trimming & Stereo Spatial Panning), Studio-Normalized Voice Mirror,
Printable PDF Phrasebook & Anki Deck Exporter.
"""
import os
import re
import csv
import uuid
import time
import html
import asyncio
import logging
import threading
import subprocess
from datetime import datetime
from typing import List, Dict, Optional, Any

import edge_tts

from config import (
    TEMP_DIR,
    VOICE_MALE_HOST,
    HOSTS_MAP
)

logger = logging.getLogger("MusaNYCLounge.Media")

MEDIA_TASK_SEMAPHORE = threading.BoundedSemaphore(4)


class LoungeMediaEngine:
    @staticmethod
    def cleanup_file(path: Optional[str]):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    @staticmethod
    def sweep_old_temp_files(max_age_seconds: int = 1800):
        now = time.time()
        if not os.path.exists(TEMP_DIR):
            return
        for fname in os.listdir(TEMP_DIR):
            fpath = os.path.join(TEMP_DIR, fname)
            try:
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
                    os.remove(fpath)
            except Exception:
                pass

    @staticmethod
    def clean_text_for_tts(text: str) -> str:
        if not text:
            return ""
        t = re.sub(r"<[^>]+>", "", text)
        t = re.sub(r"\[[^\]]*\]", "", t)
        # Strip speaker prefix labels if present in multi-host text
        t = re.sub(r"^(?:Marcus|Chloe|Jake)\s*:\s*", "", t, flags=re.IGNORECASE | re.MULTILINE)
        t = re.sub(r"[*_`~#>🗽🎙️☕🎭🔬🎧🩺💎🌊🧔‍♂️👩🏼‍💼🧢🔥✨🔄]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def resolve_host_voice(speaker_key: str) -> str:
        key = (speaker_key or "").lower().strip()
        if "marcus" in key or "male" in key:
            return HOSTS_MAP["marcus"]["voice"]
        if "jake" in key or "casual" in key:
            return HOSTS_MAP["jake"]["voice"]
        if "chloe" in key or "female" in key:
            return HOSTS_MAP["chloe"]["voice"]
        return HOSTS_MAP.get(key, {}).get("voice", VOICE_MALE_HOST)

    @staticmethod
    def _run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @classmethod
    def _edge_tts_to_file(cls, text: str, out_mp3: str, voice: str = VOICE_MALE_HOST, rate: str = "+0%") -> bool:
        clean_t = cls.clean_text_for_tts(text)
        if not clean_t:
            return False

        async def _synthesize():
            comm = edge_tts.Communicate(clean_t, voice=voice, rate=rate)
            await asyncio.wait_for(comm.save(out_mp3), timeout=18.0)

        for attempt in range(2):
            try:
                cls._run_async(_synthesize())
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 100:
                    return True
            except Exception as e:
                logger.warning(f"Voice synthesis attempt {attempt + 1} note: {e}")
                time.sleep(0.4)
        return False

    @staticmethod
    def _to_standard_wav(in_path: str, out_wav: str, normalize_loudness: bool = False) -> bool:
        cmd = ["ffmpeg", "-y", "-i", in_path]
        if normalize_loudness:
            cmd.extend(["-af", "dynaudnorm=f=150:g=15:p=0.92"])
        cmd.extend(["-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_wav])
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _to_podcast_turn_wav(in_path: str, out_wav: str, pan: float = 0.0, pad_after: float = 0.12) -> bool:
        """
        Strips leading/trailing dead air from Edge-TTS output, applies subtle stereo spatial panning
        (Marcus left, Chloe center, Jake right), and adds a tight natural breath gap.
        """
        left_gain = max(0.35, min(1.0, 0.85 - (pan * 0.45)))
        right_gain = max(0.35, min(1.0, 0.85 + (pan * 0.45)))
        af_chain = (
            "silenceremove=start_periods=1:start_duration=0.01:start_threshold=-45dB,"
            "areverse,silenceremove=start_periods=1:start_duration=0.01:start_threshold=-45dB,areverse,"
            f"apad=pad_dur={pad_after:.2f},"
            f"pan=stereo|c0={left_gain:.2f}*c0|c1={right_gain:.2f}*c0"
        )
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-af", af_chain,
            "-ar", "24000", "-ac", "2", "-c:a", "pcm_s16le",
            out_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _generate_chime_wav(out_wav: str, freq: int = 880, duration: float = 0.22) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=24000",
            "-af", "afade=t=in:st=0:d=0.03,afade=t=out:st=0.15:d=0.07,apad=pad_dur=0.22,volume=0.35",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            out_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res.returncode == 0 and os.path.exists(out_wav)

    @classmethod
    def synthesize_us_speech(
        cls,
        text: str,
        voice: str = VOICE_MALE_HOST,
        rate: str = "+0%"
    ) -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        raw_mp3 = os.path.join(TEMP_DIR, f"tts_{uid}.mp3")
        out_ogg = os.path.join(TEMP_DIR, f"tts_{uid}.ogg")

        with MEDIA_TASK_SEMAPHORE:
            try:
                if not cls._edge_tts_to_file(text, raw_mp3, voice=voice, rate=rate):
                    return None
                cmd = [
                    "ffmpeg", "-y", "-i", raw_mp3,
                    "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                    out_ogg
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
                if res.returncode == 0 and os.path.exists(out_ogg):
                    return out_ogg
                return raw_mp3
            finally:
                if os.path.exists(out_ogg) and os.path.exists(raw_mp3):
                    cls.cleanup_file(raw_mp3)

    @classmethod
    def build_acoustic_mirror(
        cls,
        user_voice_path: str,
        target_us_sentence: str,
        voice: str = VOICE_MALE_HOST
    ) -> Optional[str]:
        """
        Studio-Calibrated SLM Voice Mirror:
        Merges [Loudness-Normalized User Voice] -> [Chime] -> [Native 1.0x] -> [Chime] -> [Slow Study 0.85x]
        """
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []

        user_wav = os.path.join(TEMP_DIR, f"m_user_{uid}.wav")
        chime_wav = os.path.join(TEMP_DIR, f"m_chime_{uid}.wav")
        fast_mp3 = os.path.join(TEMP_DIR, f"m_fast_{uid}.mp3")
        fast_wav = os.path.join(TEMP_DIR, f"m_fast_{uid}.wav")
        slow_mp3 = os.path.join(TEMP_DIR, f"m_slow_{uid}.mp3")
        slow_wav = os.path.join(TEMP_DIR, f"m_slow_{uid}.wav")
        concat_txt = os.path.join(TEMP_DIR, f"m_list_{uid}.txt")
        final_ogg = os.path.join(TEMP_DIR, f"mirror_{uid}.ogg")

        temp_files.extend([user_wav, chime_wav, fast_mp3, fast_wav, slow_mp3, slow_wav, concat_txt])

        with MEDIA_TASK_SEMAPHORE:
            try:
                if not cls._to_standard_wav(user_voice_path, user_wav, normalize_loudness=True):
                    return None
                if not cls._generate_chime_wav(chime_wav):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, fast_mp3, voice=voice, rate="+0%"):
                    return None
                if not cls._to_standard_wav(fast_mp3, fast_wav, normalize_loudness=True):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, slow_mp3, voice=voice, rate="-16%"):
                    return None
                if not cls._to_standard_wav(slow_mp3, slow_wav, normalize_loudness=True):
                    return None

                sequence = [user_wav, chime_wav, fast_wav, chime_wav, slow_wav]
                with open(concat_txt, "w", encoding="utf-8") as f:
                    for seg in sequence:
                        f.write(f"file '{seg}'\n")

                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_txt,
                    "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                    final_ogg
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
                if res.returncode == 0 and os.path.exists(final_ogg):
                    return final_ogg
                return None
            except Exception as e:
                logger.error(f"Voice Mirror error: {e}")
                return None
            finally:
                for tf in temp_files:
                    cls.cleanup_file(tf)

    @classmethod
    def produce_dual_host_podcast(cls, episode: Any) -> Optional[str]:
        """
        NotebookLM-Style Multi-Host Audio Producer (Supports Marcus, Chloe & Jake):
        - Synthesizes all turns concurrently via asyncio.gather
        - Trims dead Edge-TTS padding and applies tight turn-taking gaps (0.06s for fast overlaps, 0.14s normal)
        - Applies stereo spatial panning so the 3 hosts sit across a realistic studio soundstage
        """
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []

        script_lines = getattr(episode, "script", None) or (episode.get("script") if isinstance(episode, dict) else [])
        if not script_lines:
            return None

        concat_txt = os.path.join(TEMP_DIR, f"p_list_{uid}.txt")
        final_mp3 = os.path.join(TEMP_DIR, f"nyc_trio_lounge_{uid}.mp3")
        temp_files.append(concat_txt)

        pacing_rate_map = {
            "fast_interjection": "+7%",
            "excited": "+6%",
            "normal": "+2%",
            "thoughtful": "-2%"
        }
        pacing_gap_map = {
            "fast_interjection": 0.05,
            "excited": 0.08,
            "normal": 0.13,
            "thoughtful": 0.18
        }

        with MEDIA_TASK_SEMAPHORE:
            try:
                turn_specs = []
                for idx, turn in enumerate(script_lines):
                    raw_speaker = (getattr(turn, "speaker", None) or (turn.get("speaker") if isinstance(turn, dict) else "marcus") or "marcus").lower()
                    text = getattr(turn, "text", None) or (turn.get("text") if isinstance(turn, dict) else "")
                    pacing = (getattr(turn, "pacing", None) or (turn.get("pacing") if isinstance(turn, dict) else "normal") or "normal").lower()

                    if "chloe" in raw_speaker or "female" in raw_speaker:
