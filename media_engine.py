# -*- coding: utf-8 -*-
"""
media_engine.py — Neural US Speech (Host & User Recast), Voice Mirror (1.0x + 0.85x),
Dual-Host Radio Producer, Printable PDF Booklet & Anki Deck Exporter.
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
    VOICE_FEMALE_HOST
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
        t = re.sub(r"[*_`~#>🗽🎙️☕🎭🔬🎧🩺💎🌊]", "", t)
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
    def _edge_tts_to_file(cls, text: str, out_mp3: str, voice: str = VOICE_MALE_HOST, rate: str = "+0%") -> bool:
        clean_t = cls.clean_text_for_tts(text)
        if not clean_t:
            return False

        async def _synthesize():
            comm = edge_tts.Communicate(clean_t, voice=voice, rate=rate)
            await asyncio.wait_for(comm.save(out_mp3), timeout=16.0)

        for attempt in range(2):
            try:
                cls._run_async(_synthesize())
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 100:
                    return True
            except Exception as e:
                logger.warning(f"Voice synthesis attempt {attempt + 1} note: {e}")
                time.sleep(0.5)
        return False

    @staticmethod
    def _to_standard_wav(in_path: str, out_wav: str) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            out_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _generate_chime_wav(out_wav: str, freq: int = 880, duration: float = 0.22) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=24000",
            "-af", "afade=t=in:st=0:d=0.03,afade=t=out:st=0.15:d=0.07,apad=pad_dur=0.25,volume=0.35",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            out_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        return res.returncode == 0 and os.path.exists(out_wav)

    @staticmethod
    def _generate_silence_wav(out_wav: str, duration: float = 0.28) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={duration}",
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
        Merges: [User Voice] -> [Chime] -> [Native 1.0x] -> [Chime] -> [Slow Study 0.85x]
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
                if not cls._to_standard_wav(user_voice_path, user_wav):
                    return None
                if not cls._generate_chime_wav(chime_wav):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, fast_mp3, voice=voice, rate="+0%"):
                    return None
                if not cls._to_standard_wav(fast_mp3, fast_wav):
                    return None
                if not cls._edge_tts_to_file(target_us_sentence, slow_mp3, voice=voice, rate="-15%"):
                    return None
                if not cls._to_standard_wav(slow_mp3, slow_wav):
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
        cls.sweep_old_temp_files()
        uid = uuid.uuid4().hex[:10]
        temp_files: List[str] = []

        script_lines = getattr(episode, "script", None) or (episode.get("script") if isinstance(episode, dict) else [])
        if not script_lines:
            return None

        pause_wav = os.path.join(TEMP_DIR, f"p_pause_{uid}.wav")
        concat_txt = os.path.join(TEMP_DIR, f"p_list_{uid}.txt")
        final_mp3 = os.path.join(TEMP_DIR, f"nyc_radio_{uid}.mp3")
        temp_files.extend([pause_wav, concat_txt])

        with MEDIA_TASK_SEMAPHORE:
            try:
                cls._generate_silence_wav(pause_wav, duration=0.26)
                wav_segments: List[str] = []

                for idx, turn in enumerate(script_lines):
                    speaker = getattr(turn, "speaker", None) or (turn.get("speaker") if isinstance(turn, dict) else "male_host")
                    text = getattr(turn, "text", None) or (turn.get("text") if isinstance(turn, dict) else "")
                    voice = VOICE_MALE_HOST if speaker == "male_host" else VOICE_FEMALE_HOST

                    seg_mp3 = os.path.join(TEMP_DIR, f"p_seg_{uid}_{idx}.mp3")
                    seg_wav = os.path.join(TEMP_DIR, f"p_seg_{uid}_{idx}.wav")
                    temp_files.extend([seg_mp3, seg_wav])

                    if cls._edge_tts_to_file(text, seg_mp3, voice=voice, rate="+2%"):
                        if cls._to_standard_wav(seg_mp3, seg_wav):
                            wav_segments.append(seg_wav)
                            if os.path.exists(pause_wav):
                                wav_segments.append(pause_wav)

                if not wav_segments:
                    return None

                with open(concat_txt, "w", encoding="utf-8") as f:
                    for w in wav_segments:
                        f.write(f"file '{w}'\n")

                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_txt,
                    "-c:a", "libmp3lame", "-q:a", "2",
                    final_mp3
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
                if res.returncode == 0 and os.path.exists(final_mp3):
                    return final_mp3
                return None
            except Exception as e:
                logger.error(f"Radio episode error: {e}")
                return None
            finally:
                for tf in temp_files:
                    cls.cleanup_file(tf)

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
            cards_html.append(f"""
            <div class="card">
                <div class="card-header">
                    <span class="chunk">{idx}. {html.escape(item.get('chunk', ''))}</span>
                    <span class="badge">{html.escape(item.get('register_tag', ''))}</span>
                </div>
                <p class="meaning"><b>Nuance & Meaning:</b> {html.escape(item.get('explanation', ''))}</p>
                <div class="frame-grid">
                    <div><b>🎯 When to Reach For It:</b> {html.escape(item.get('trigger_context', ''))}</div>
                    <div><b>🔗 Natural Pattern:</b> <code>{html.escape(item.get('co_text_pattern', ''))}</code></div>
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
            </style>
        </head>
        <body>
            <h1>🗽 Musa's NYC Lounge — Personal Phrasebook</h1>
            <div class="subtitle">
                Member: <b>{html.escape(user.get('full_name') or 'Member')}</b> |
                Collected Expressions: <b>{stats.get('total_vault', len(vault_items))}</b> |
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
                    cloze = item.get("cloze_sentence") or item.get("example_sentence", "").replace(chunk, "______")

                    front = f"[{reg}] (When: {trig})<br><br><b>{cloze}</b>"
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
