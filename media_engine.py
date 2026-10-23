# -*- coding: utf-8 -*-
"""
media_engine.py — Studio Audio & Document Engine for Musa's NYC Lounge
"""
import os
import re
import csv
import uuid
import time
import html
import wave
import asyncio
import logging
import threading
import subprocess
from datetime import datetime
from typing import List, Dict, Optional, Any

import edge_tts

from config import (
    TEMP_DIR,
    PERSONAS,
    DEFAULT_PERSONA
)

logger = logging.getLogger("MusaNYCLounge.Media")

MEDIA_TASK_SEMAPHORE = threading.BoundedSemaphore(4)

VOICE_MALE_HOST: str = PERSONAS["marcus"]["voice"]
VOICE_FEMALE_HOST: str = PERSONAS["chloe"]["voice"]
VOICE_JAKE_HOST: str = PERSONAS["jake"]["voice"]


class LoungeMediaEngine:
    @staticmethod
    def cleanup_file(path: Optional[str]):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    @staticmethod
    def sweep_old_temp_files(max_age_seconds: int = 2400):
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
    def resolve_persona_voice(speaker_key: str) -> Dict[str, str]:
        key = (speaker_key or "").strip().lower()
        if key in ("male_host", "marcus", "andrew", "host_1"):
            p = PERSONAS["marcus"]
            return {"key": "marcus", "voice": p["voice"], "rate": p["rate"], "pitch": p["pitch"]}
        if key in ("female_host", "chloe", "ava", "host_2", "duo_cj"):
            p = PERSONAS["chloe"]
            return {"key": "chloe", "voice": p["voice"], "rate": p["rate"], "pitch": p["pitch"]}
        if key in ("jake", "brian", "casual_host", "host_3"):
            p = PERSONAS["jake"]
            return {"key": "jake", "voice": p["voice"], "rate": p["rate"], "pitch": p["pitch"]}
        p = PERSONAS["marcus"]
        return {"key": "marcus", "voice": p["voice"], "rate": p["rate"], "pitch": p["pitch"]}

    @staticmethod
    def clean_text_for_tts(text: str) -> str:
        if not text:
            return ""
        t = re.sub(r"<[^>]+>", "", text)
        t = re.sub(r"\[[^\]]*\]", "", t)
        t = re.sub(r"[*_`~#>🗽🎙️☕🎭🔬🎧🩺💎🌊🧔‍♂️👩🏼‍💼🧢🔥🎯]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def _run_async(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @classmethod
    async def _async_edge_tts_to_file(
        cls,
        text: str,
        out_mp3: str,
        voice: str = VOICE_MALE_HOST,
        rate: str = "+0%",
        pitch: str = "+0Hz"
    ) -> bool:
        clean_t = cls.clean_text_for_tts(text)
        if not clean_t:
            return False

        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(clean_t, voice=voice, rate=rate, pitch=pitch)
                await asyncio.wait_for(comm.save(out_mp3), timeout=30.0)
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 100:
                    return True
            except Exception as e:
                try:
                    comm_fb = edge_tts.Communicate(clean_t, voice=voice, rate=rate)
                    await asyncio.wait_for(comm_fb.save(out_mp3), timeout=30.0)
                    if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 100:
                        return True
                except Exception:
                    pass
                logger.warning(f"Voice synthesis attempt {attempt + 1} note: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
        return False

    @classmethod
    def _edge_tts_to_file(
        cls,
        text: str,
        out_mp3: str,
        voice: str = VOICE_MALE_HOST,
        rate: str = "+0%",
        pitch: str = "+0Hz"
    ) -> bool:
        return cls._run_async(
            cls._async_edge_tts_to_file(text, out_mp3, voice=voice, rate=rate, pitch=pitch)
        )

    @staticmethod
    def _get_wav_duration(wav_path: str) -> float:
        try:
            with wave.open(wav_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate) if rate > 0 else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _to_trimmed_podcast_wav(
        in_path: str,
        out_wav: str,
        pan_filter: str = "pan=stereo|c0=0.50*c0|c1=0.50*c0",
        pad_after: float = 0.15
    ) -> bool:
        af_chain = (
            "silenceremove=start_periods=1:start_silence=0.02:start_threshold=-44dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-44dB,"
            "areverse,"
            f"{pan_filter},"
            f"apad=pad_dur={pad_after:.2f}"
        )
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-af", af_chain,
            "-ar", "24000", "-ac", "2", "-c:a", "pcm_s16le",
            out_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if res.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 200:
            return True

        cmd_fb = [
            "ffmpeg", "-y", "-i", in_path,
            "-af", f"{pan_filter},apad=pad_dur={pad_after:.2f}",
            "-ar", "24000", "-ac", "2", "-c:a", "pcm_s16le",
            out_wav
        ]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return res_fb.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _to_normalized_mono_wav(in_path: str, out_wav: str, normalize_user: bool = False) -> bool:
        if normalize_user:
            af_chain = (
                "highpass=f=80,"
                "dynaudnorm=f=150:g=15,"
                "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-42dB,"
                "areverse,"
                "silenceremove=start_periods=1:start_silence=0.08:start_threshold=-42dB,"
                "areverse"
            )
            cmd = [
                "ffmpeg", "-y", "-i", in_path,
                "-af", af_chain,
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                out_wav
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
            if res.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 200:
                return True

        cmd_std = [
            "ffmpeg", "-y", "-i", in_path,
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            out_wav
        ]
        res_std = subprocess.run(cmd_std, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res_std.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _generate_chime_wav(out_wav: str, freq: int = 880, duration: float = 0.20) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=24000",
            "-af", "afade=t=in:st=0:d=0.03,afade=t=out:st=0.13:d=0.07,apad=pad_dur=0.22,volume=0.32",
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
        rate: str = "+0%",
        persona_key: str = ""
    ) -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        raw_mp3 = os.path.join(TEMP_DIR, f"tts_{uid}.mp3")
        out_ogg = os.path.join(TEMP_DIR, f"tts_{uid}.ogg")

        pitch = "+0Hz"
        if persona_key:
            p_info = cls.resolve_persona_voice(persona_key)
            voice = p_info["voice"]
            if rate == "+0%":
                rate = p_info["rate"]
            pitch = p_info["pitch"]

        with MEDIA_TASK_SEMAPHORE:
            try:
                if not cls._edge_tts_to_file(text, raw_mp3, voice=voice, rate=rate, pitch=pitch):
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
    def synthesize_chat_turns(
        cls,
        chat_turns: List[Dict[str, str]],
        fallback_text: str = "",
        fallback_persona: str = "marcus"
    ) -> Optional[str]:
        if not chat_turns or len(chat_turns) <= 1:
            if chat_turns and len(chat_turns) == 1:
                sp = chat_turns[0].get("speaker", fallback_persona)
                txt = chat_turns[0].get("text", fallback_text)
                return cls.synthesize_us_speech(txt, persona_key=sp)
            return cls.synthesize_us_speech(fallback_text, persona_key=fallback_persona)

        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []
        concat_txt = os.path.join(TEMP_DIR, f"ct_list_{uid}.txt")
        final_ogg = os.path.join(TEMP_DIR, f"ct_multi_{uid}.ogg")
        temp_files.append(concat_txt)

        with MEDIA_TASK_SEMAPHORE:
            try:
                resolved_speakers = [cls.resolve_persona_voice(t.get("speaker", "marcus"))["key"] for t in chat_turns]
                pan_map = cls._build_speaker_pan_map(resolved_speakers, requested_mode="auto")

                tasks_meta = []
                for idx, turn in enumerate(chat_turns):
                    raw_sp = turn.get("speaker", "marcus")
                    text = turn.get("text", "")
                    pacing = turn.get("pacing", "normal")
                    p_info = cls.resolve_persona_voice(raw_sp)
                    sp_key = p_info["key"]
                    final_rate = cls._compute_turn_rate(p_info["rate"], pacing)
                    pad_after = cls._compute_pad_after(text, pacing)
                    pan_filter = pan_map.get(sp_key, "pan=stereo|c0=0.50*c0|c1=0.50*c0")

                    seg_mp3 = os.path.join(TEMP_DIR, f"ct_seg_{uid}_{idx}.mp3")
                    seg_wav = os.path.join(TEMP_DIR, f"ct_seg_{uid}_{idx}.wav")
                    temp_files.extend([seg_mp3, seg_wav])

                    tasks_meta.append({
                        "text": text,
                        "voice": p_info["voice"],
                        "rate": final_rate,
                        "pitch": p_info["pitch"],
                        "mp3": seg_mp3,
                        "wav": seg_wav,
                        "pan_filter": pan_filter,
                        "pad_after": pad_after
                    })

                async def _synth_all():
                    sem = asyncio.Semaphore(4)
                    async def _w(m):
                        async with sem:
                            return await cls._async_edge_tts_to_file(
                                m["text"], m["mp3"], voice=m["voice"], rate=m["rate"], pitch=m["pitch"]
                            )
                    return await asyncio.gather(*[_w(m) for m in tasks_meta])

                results = cls._run_async(_synth_all())
                wav_segments = []
                for m, ok in zip(tasks_meta, results):
                    if ok and cls._to_trimmed_podcast_wav(m["mp3"], m["wav"], pan_filter=m["pan_filter"], pad_after=m["pad_after"]):
                        wav_segments.append(m["wav"])

                if not wav_segments:
                    return cls.synthesize_us_speech(fallback_text, persona_key=fallback_persona)

                with open(concat_txt, "w", encoding="utf-8") as f:
                    for w in wav_segments:
                        f.write(f"file '{w}'\n")

                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_txt,
                    "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                    final_ogg
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
                if res.returncode == 0 and os.path.exists(final_ogg):
                    return final_ogg
                return None
            except Exception as e:
                logger.error(f"Multi-host chat voice error: {e}")
                return None
            finally:
                for tf in temp_files:
                    cls.cleanup_file(tf)

    @classmethod
    def build_acoustic_mirror(
        cls,
        user_voice_path: str,
        target_us_sentence: str,
        voice: str = VOICE_MALE_HOST,
        persona_key: str = ""
    ) -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []

        if persona_key:
            voice = cls.resolve_persona_voice(persona_key)["voice"]

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
                if not cls._to_normalized_mono_wav(user_voice_path, user_wav, normalize_user=True):
                    return None
                if not cls._generate_chime_wav(chime_wav):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, fast_mp3, voice=voice, rate="+0%"):
                    return None
                if not cls._to_normalized_mono_wav(fast_mp3, fast_wav, normalize_user=False):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, slow_mp3, voice=voice, rate="-15%"):
                    return None
                if not cls._to_normalized_mono_wav(slow_mp3, slow_wav, normalize_user=False):
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

    @staticmethod
    def _compute_turn_rate(base_rate: str, pacing: str) -> str:
        try:
            base_val = int(base_rate.replace("%", "").replace("+", ""))
        except Exception:
            base_val = 0

        p = (pacing or "normal").lower()
        if "fast" in p or "interject" in p or "excited" in p:
            base_val += 6
        elif "slow" in p or "thoughtful" in p or "emphasis" in p:
            base_val -= 3
        sign = "+" if base_val >= 0 else ""
        return f"{sign}{base_val}%"

    @staticmethod
    def _compute_pad_after(text: str, pacing: str) -> float:
        stripped = (text or "").strip()
        p = (pacing or "normal").lower()
        word_cnt = len(stripped.split())

        if stripped.endswith("—") or stripped.endswith("--") or "fast" in p or "interject" in p:
            return 0.05
        if word_cnt <= 6:
            return 0.09
        if "slow" in p or "thoughtful" in p or word_cnt >= 28:
            return 0.24
        return 0.16

    @staticmethod
    def _build_speaker_pan_map(speaker_keys_in_episode: List[str], requested_mode: str = "auto") -> Dict[str, str]:
        unique_speakers = []
        for s in speaker_keys_in_episode:
            if s not in unique_speakers:
                unique_speakers.append(s)

        is_duo = (requested_mode == "duo") or (requested_mode == "auto" and len(unique_speakers) == 2)
        if is_duo and len(unique_speakers) >= 2:
            return {
                unique_speakers[0]: "pan=stereo|c0=0.58*c0|c1=0.42*c0",
                unique_speakers[1]: "pan=stereo|c0=0.42*c0|c1=0.58*c0"
            }

        return {
            "marcus": "pan=stereo|c0=0.62*c0|c1=0.38*c0",
            "chloe": "pan=stereo|c0=0.50*c0|c1=0.50*c0",
            "jake": "pan=stereo|c0=0.38*c0|c1=0.62*c0"
        }

    @classmethod
    def produce_podcast(cls, episode: Any, format_mode: str = "auto") -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []

        script_lines = getattr(episode, "script", None) or (episode.get("script") if isinstance(episode, dict) else [])
        if not script_lines:
            return None

        target_chunks = (
            getattr(episode, "target_chunks_covered", None)
            or (episode.get("target_chunks_covered") if isinstance(episode, dict) else [])
            or []
        )

        concat_txt = os.path.join(TEMP_DIR, f"p_list_{uid}.txt")
        final_mp3 = os.path.join(TEMP_DIR, f"nyc_lounge_podcast_{uid}.mp3")
        temp_files.append(concat_txt)

        with MEDIA_TASK_SEMAPHORE:
            try:
                resolved_speakers = []
                for turn in script_lines:
                    raw_sp = getattr(turn, "speaker", None) or (turn.get("speaker") if isinstance(turn, dict) else "marcus")
                    resolved_speakers.append(cls.resolve_persona_voice(raw_sp)["key"])

                pan_map = cls._build_speaker_pan_map(resolved_speakers, requested_mode=format_mode)

                tasks_meta = []
                for idx, turn in enumerate(script_lines):
                    raw_speaker = getattr(turn, "speaker", None) or (turn.get("speaker") if isinstance(turn, dict) else "marcus")
                    text = getattr(turn, "text", None) or (turn.get("text") if isinstance(turn, dict) else "")
                    pacing = getattr(turn, "pacing", None) or (turn.get("pacing") if isinstance(turn, dict) else "normal")

                    p_info = cls.resolve_persona_voice(raw_speaker)
                    sp_key = p_info["key"]
                    final_rate = cls._compute_turn_rate(p_info["rate"], pacing)
                    pad_after = cls._compute_pad_after(text, pacing)
                    pan_filter = pan_map.get(sp_key, "pan=stereo|c0=0.50*c0|c1=0.50*c0")

                    seg_mp3 = os.path.join(TEMP_DIR, f"p_seg_{uid}_{idx}.mp3")
                    seg_wav = os.path.join(TEMP_DIR, f"p_seg_{uid}_{idx}.wav")
                    temp_files.extend([seg_mp3, seg_wav])

                    tasks_meta.append({
                        "idx": idx,
                        "speaker_key": sp_key,
                        "text": text,
                        "voice": p_info["voice"],
                        "rate": final_rate,
                        "pitch": p_info["pitch"],
                        "mp3": seg_mp3,
                        "wav": seg_wav,
                        "pan_filter": pan_filter,
                        "pad_after": pad_after
                    })

                async def _synthesize_all_turns():
                    sem = asyncio.Semaphore(6)
                    async def _worker(meta_item):
                        async with sem:
                            return await cls._async_edge_tts_to_file(
                                meta_item["text"],
                                meta_item["mp3"],
                                voice=meta_item["voice"],
                                rate=meta_item["rate"],
                                pitch=meta_item["pitch"]
                            )
                    return await asyncio.gather(*[_worker(m) for m in tasks_meta])

                synth_results = cls._run_async(_synthesize_all_turns())

                wav_segments: List[str] = []
                cumulative_sec: float = 0.0
                chunk_timestamps: Dict[str, str] = {}
                turn_timestamps: List[str] = []

                for meta_item, ok in zip(tasks_meta, synth_results):
                    mm = int(cumulative_sec // 60)
                    ss = int(cumulative_sec % 60)
                    ts_label = f"{mm:02d}:{ss:02d}"
                    turn_timestamps.append(ts_label)

                    if ok and cls._to_trimmed_podcast_wav(
                        meta_item["mp3"],
                        meta_item["wav"],
                        pan_filter=meta_item["pan_filter"],
                        pad_after=meta_item["pad_after"]
                    ):
                        wav_segments.append(meta_item["wav"])
                        low_txt = meta_item["text"].lower()
                        for ch in target_chunks:
                            ch_clean = ch.strip().lower()
                            if ch_clean and ch not in chunk_timestamps:
                                stem_words = [w for w in ch_clean.split() if len(w) > 3]
                                if ch_clean in low_txt or (stem_words and all(w[:4] in low_txt for w in stem_words)):
                                    chunk_timestamps[ch] = ts_label

                        cumulative_sec += cls._get_wav_duration(meta_item["wav"])

                if not wav_segments:
                    return None

                total_mm = int(cumulative_sec // 60)
                total_ss = int(cumulative_sec % 60)
                duration_label = f"{total_mm:02d}:{total_ss:02d}"

                try:
                    setattr(episode, "chunk_timestamps", chunk_timestamps)
                    setattr(episode, "turn_timestamps", turn_timestamps)
                    setattr(episode, "total_duration_sec", int(cumulative_sec))
                    setattr(episode, "duration_label", duration_label)
                except Exception:
                    if isinstance(episode, dict):
                        episode["chunk_timestamps"] = chunk_timestamps
                        episode["turn_timestamps"] = turn_timestamps
                        episode["total_duration_sec"] = int(cumulative_sec)
                        episode["duration_label"] = duration_label

                with open(concat_txt, "w", encoding="utf-8") as f:
                    for w in wav_segments:
                        f.write(f"file '{w}'\n")

                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_txt,
                    "-c:a", "libmp3lame", "-q:a", "2",
                    final_mp3
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
                if res.returncode == 0 and os.path.exists(final_mp3):
                    return final_mp3
                return None
            except Exception as e:
                logger.error(f"Podcast production error ({format_mode}): {e}")
                return None
            finally:
                for tf in temp_files:
                    cls.cleanup_file(tf)

    @classmethod
    def produce_trio_podcast(cls, episode: Any) -> Optional[str]:
        return cls.produce_podcast(episode, format_mode="trio")

    @classmethod
    def produce_dual_host_podcast(cls, episode: Any) -> Optional[str]:
        return cls.produce_podcast(episode, format_mode="auto")

    @classmethod
    def export_vault_pdf(
        cls,
        user: Dict[str, Any],
        vault_items: List[Dict[str, Any]],
        stats: Dict[str, Any]
    ) -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:8]
        pdf_path = os.path.join(TEMP_DIR, f"Musa_NYC_Lounge_Notebook_{uid}.pdf")

        cards_html = []
        for idx, item in enumerate(vault_items, 1):
            b2_foil = item.get("b2_foil", "")
            foil_line = (
                f"<div><b>🔄 Flat B2 vs. Native C2:</b> <s>{html.escape(b2_foil)}</s> ➔ <b>{html.escape(item.get('chunk', ''))}</b></div>"
                if b2_foil else ""
            )
            prod_cnt = int(item.get("productive_count", 0))
            mastery_badge = f" | 🔥 Used Actively: {prod_cnt}x" if prod_cnt > 0 else ""

            cards_html.append(f"""
            <div class="card">
                <div class="card-header">
                    <span class="chunk">{idx}. {html.escape(item.get('chunk', ''))}</span>
                    <span class="badge">{html.escape(item.get('register_tag', ''))}{mastery_badge}</span>
                </div>
                <p class="meaning"><b>Nuance & Meaning:</b> {html.escape(item.get('explanation', ''))}</p>
                <div class="frame-grid">
                    {foil_line}
                    <div><b>🎯 When to Reach For It:</b> {html.escape(item.get('trigger_context', ''))}</div>
                    <div><b>🔗 Colligation & Pattern:</b> <code>{html.escape(item.get('co_text_pattern', ''))}</code></div>
                    <div><b>💬 Natural Follow-up:</b> <i>"{html.escape(item.get('typical_followup', ''))}"</i></div>
                </div>
                <p class="example"><b>🗽 In Conversation:</b> <i>"{html.escape(item.get('example_sentence', ''))}"</i></p>
            </div>
            """)

        html_doc = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{ size: A4; margin: 18mm; }}
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.5; }}
                h1 {{ color: #0f172a; margin-bottom: 4px; font-size: 22px; }}
                .subtitle {{ color: #475569; font-size: 13px; margin-bottom: 20px; border-bottom: 2px solid #cbd5e1; padding-bottom: 10px; }}
                .card {{ border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px 15px; margin-bottom: 14px; background: #f8fafc; page-break-inside: avoid; }}
                .card-header {{ display: flex; justify-content: space-between; margin-bottom: 6px; }}
                .chunk {{ font-size: 16px; font-weight: 700; color: #0f172a; }}
                .badge {{ font-size: 12px; color: #0369a1; font-weight: 600; margin-left: 10px; }}
                .meaning {{ margin: 4px 0; font-size: 13px; }}
                .frame-grid {{ font-size: 12.5px; background: #ffffff; border-left: 3px solid #0284c7; padding: 6px 10px; margin: 6px 0; }}
                .example {{ margin: 6px 0 0 0; font-size: 13px; color: #334155; }}
                code {{ background: #e2e8f0; padding: 1px 4px; border-radius: 4px; }}
                s {{ color: #94a3b8; }}
            </style>
        </head>
        <body>
            <h1>🗽 Musa's NYC Lounge — Personal C1/C2 Phrasebook</h1>
            <div class="subtitle">
                Member: <b>{html.escape(user.get('full_name') or 'Member')}</b> |
                Collected Expressions: <b>{stats.get('total_vault', len(vault_items))}</b> |
                Actively Produced: <b>{stats.get('productive_mastered', 0)}</b> |
                Date: <b>{datetime.utcnow().strftime('%Y-%m-%d')}</b>
            </div>
            {''.join(cards_html)}
        </body>
        </html>"""

        with MEDIA_TASK_SEMAPHORE:
            try:
                from weasyprint import HTML
                HTML(string=html_doc).write_pdf(pdf_path)
                if os.path.exists(pdf_path):
                    return pdf_path
            except Exception as e:
                logger.error(f"PDF Booklet error: {e}")
            return None

    @classmethod
    def export_vault_anki_csv(cls, vault_items: List[Dict[str, Any]]) -> Optional[str]:
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:8]
        csv_path = os.path.join(TEMP_DIR, f"Musa_NYC_Lounge_Flashcards_{uid}.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                for item in vault_items:
                    chunk = item.get("chunk", "")
                    reg = item.get("register_tag", "")
                    trig = item.get("trigger_context", "")
                    b2_foil = item.get("b2_foil", "")
                    cloze = item.get("cloze_sentence") or item.get("example_sentence", "").replace(chunk, "______")

                    foil_prompt = f"<br><small>🔄 Upgrade from: <i>{b2_foil}</i></small>" if b2_foil else ""
                    front = f"[{reg}] (When: {trig}){foil_prompt}<br><br><b>{cloze}</b>"
                    back = (
                        f"<b>{chunk}</b> ({reg})<br><br>"
                        f"<b>Nuance:</b> {item.get('explanation', '')}<br>"
                        f"<b>🎯 When to use:</b> {trig}<br>"
                        f"<b>🔗 Pattern:</b> <code>{item.get('co_text_pattern', '')}</code><br>"
                        f"<b>💬 Follow-up:</b> <i>{item.get('typical_followup', '')}</i><br><br>"
                        f"<b>🗽 Example:</b> <i>{item.get('example_sentence', '')}</i>"
                    )
                    writer.writerow([front, back, "Musa_NYC_Lounge"])
            return csv_path if os.path.exists(csv_path) else None
        except Exception as e:
            logger.error(f"Flashcard CSV error: {e}")
            return None
