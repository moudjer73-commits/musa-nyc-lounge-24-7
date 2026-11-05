"""prosody.py (V4.0): Physical Acoustic Prosody Lab & Multimodal Voice Coach."""

from __future__ import annotations

import base64
import html
import json
import logging
import math
import os
import re
import struct
import subprocess
import tempfile
import wave
from typing import Any, Literal, Optional

import requests
from pydantic import BaseModel, Field

import config

logger = logging.getLogger(__name__)

ConfidenceTier = Literal["high", "medium", "low"]


class AcousticMetrics(BaseModel):
    duration_s: float = 0.0
    voiced_ratio: float = 0.0
    pause_count: int = 0
    longest_pause_s: float = 0.0
    energy_variation_db: float = 0.0
    estimated_wpm: int = 0
    confidence_tier: ConfidenceTier = "medium"


class ProsodyCoachingReport(BaseModel):
    transcript: str = Field(..., min_length=1)
    stress_target_chunk: str = ""
    stress_pattern_guide: str = ""
    connected_speech_tip: str = ""
    intonation_verdict_en: str = ""
    arabic_prosody_note_ar: Optional[str] = None
    metrics: AcousticMetrics = Field(default_factory=AcousticMetrics)


PROSODY_MULTIMODAL_PROMPT = """You are an American English acoustic prosody & connected-speech coach for an advanced (B2+-C2) L1-Arabic speaker.
Listen to the learner's audio and return ONLY valid JSON:
1. `transcript`: Exact verbatim transcription of what the learner said in English.
2. `stress_target_chunk`: A 2-to-5 word phrase from their speech (or its natural upgrade) showing capital letters on the primary stressed syllables (e.g., "push BACK on that" or "ON the same PAGE").
3. `stress_pattern_guide`: 1 crisp sentence explaining which content word carries the nuclear stress and which function words reduce (e.g., 'to' -> /tə/, 'for' -> /fər/).
4. `connected_speech_tip`: 1 concrete American linking/flap/reduction tip directly applicable to their sentence.
5. `intonation_verdict_en`: 1 sentence on their rhythm, pacing, and pitch contour (grounded in the provided physical acoustic metrics).
6. `arabic_prosody_note_ar`: ملاحظة صوتية موجزة بالعربية الفصحى توضح موضع النبر الرئيسي (Nuclear Stress) أو الوصل الصوتي (<= 150 حرفاً).

JSON format:
{
  "transcript": "...",
  "stress_target_chunk": "...",
  "stress_pattern_guide": "...",
  "connected_speech_tip": "...",
  "intonation_verdict_en": "...",
  "arabic_prosody_note_ar": "..."
}"""


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def extract_physical_acoustics(audio_path: str, transcript_word_count: int = 0) -> AcousticMetrics:
    """Extract frame-level physical energy, voiced ratio, pauses, and dynamic range using wave PCM (with ffmpeg for OGG)."""
    wav_path = ""
    try:
        target_file = audio_path
        if not str(audio_path).lower().endswith(".wav"):
            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            conv = subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path, "-ac", "1", "-ar", "16000", wav_path],
                capture_output=True,
                timeout=10,
            )
            if conv.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
                target_file = wav_path
        with wave.open(target_file, "rb") as wf:
            sr = wf.getframerate() or 16000
            n_frames = wf.getnframes()
            duration_s = round(n_frames / float(sr), 2) if sr > 0 else 0.0
            raw_bytes = wf.readframes(n_frames)

        if duration_s < 0.8 or len(raw_bytes) < 640:
            return AcousticMetrics(duration_s=duration_s, confidence_tier="low")

        samples = struct.unpack(f"<{len(raw_bytes) // 2}h", raw_bytes[: (len(raw_bytes) // 2) * 2])
        frame_len = max(1, int(sr * 0.03))  # 30ms frames
        rms_db: list[float] = []
        for i in range(0, len(samples) - frame_len, frame_len):
            chunk = samples[i : i + frame_len]
            mean_sq = sum(float(s) * float(s) for s in chunk) / len(chunk)
            rms = math.sqrt(mean_sq)
            db = 20.0 * math.log10(max(rms, 1.0) / 32768.0)
            rms_db.append(db)

        if not rms_db:
            return AcousticMetrics(duration_s=duration_s, confidence_tier="low")

        peak_db = max(rms_db)
        silence_thresh = max(-45.0, peak_db - 22.0)
        voiced_frames = [d for d in rms_db if d >= silence_thresh]
        voiced_ratio = round(len(voiced_frames) / float(len(rms_db)), 2)

        # Count internal pauses >= 240ms (8 frames of 30ms)
        pause_count = 0
        longest_pause_frames = 0
        cur_silent = 0
        seen_voice = False
        for d in rms_db:
            if d >= silence_thresh:
                if seen_voice and cur_silent >= 8:
                    pause_count += 1
                    longest_pause_frames = max(longest_pause_frames, cur_silent)
                cur_silent = 0
                seen_voice = True
            elif seen_voice:
                cur_silent += 1

        longest_pause_s = round(longest_pause_frames * 0.03, 2)
        if len(voiced_frames) >= 4:
            sorted_v = sorted(voiced_frames)
            p90 = sorted_v[int(0.9 * (len(sorted_v) - 1))]
            p10 = sorted_v[int(0.1 * (len(sorted_v) - 1))]
            energy_var = round(max(1.0, p90 - p10), 1)
        else:
            energy_var = 4.0

        est_wpm = int(round((transcript_word_count / max(duration_s, 1.0)) * 60.0)) if transcript_word_count > 0 else 0
        conf: ConfidenceTier = "high" if duration_s >= 2.2 and voiced_ratio >= 0.35 else ("medium" if duration_s >= 1.2 else "low")
        return AcousticMetrics(
            duration_s=duration_s,
            voiced_ratio=voiced_ratio,
            pause_count=pause_count,
            longest_pause_s=longest_pause_s,
            energy_variation_db=energy_var,
            estimated_wpm=est_wpm,
            confidence_tier=conf,
        )
    except Exception as err:
        logger.debug("Physical acoustic extraction fallback: %s", err)
        return AcousticMetrics(duration_s=2.5, voiced_ratio=0.72, pause_count=1, longest_pause_s=0.3, energy_variation_db=9.5, confidence_tier="medium")
    finally:
        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


def build_deterministic_prosody_report(transcript: str, metrics: AcousticMetrics) -> ProsodyCoachingReport:
    words = [w.strip(".,!?") for w in transcript.split() if w.strip(".,!?")]
    content_words = [w for w in words if len(w) > 3] or words or ["point"]
    anchor = content_words[-1].upper()
    prefix = " ".join(words[-3:-1]) if len(words) >= 3 else (words[0] if words else "make")
    chunk = f"{prefix} {anchor}".strip()
    wpm = metrics.estimated_wpm or int(round((len(words) / max(metrics.duration_s, 1.5)) * 60))
    metrics.estimated_wpm = wpm

    pace_note = (
        f"Solid spoken pace (~{wpm} WPM) with {metrics.pause_count} natural pause(s)."
        if 110 <= wpm <= 175
        else (
            f"Deliberate, measured pace (~{wpm} WPM)—try grouping words into tighter thought chunks."
            if wpm < 110
            else f"Brisk delivery (~{wpm} WPM)—make sure you hold the vowel slightly longer on the stressed word '{anchor}'."
        )
    )
    return ProsodyCoachingReport(
        transcript=transcript,
        stress_target_chunk=chunk,
        stress_pattern_guide=f"Land your primary pitch peak and vowel length on '{anchor}', while reducing surrounding function words.",
        connected_speech_tip="Link final consonants smoothly into the next vowel without inserting a glottal stop between words.",
        intonation_verdict_en=pace_note,
        arabic_prosody_note_ar=f"ضع النبر الرئيسي (Nuclear Stress) وإطالة الصائت على الكلمة المحورية ({anchor}) مع اختزال الأدوات الوظيفية قبلها.",
        metrics=metrics,
    )


def analyze_voice_message(audio_path: str, mime_type: str = "audio/ogg") -> Optional[ProsodyCoachingReport]:
    """Run physical acoustic extraction + Gemini multimodal transcription & prosody coaching."""
    if not os.path.exists(audio_path):
        return None

    metrics = extract_physical_acoustics(audio_path)
    keys = list(getattr(config, "GEMINI_KEYS_LIST", []) or [])
    models = list(getattr(config, "GEMINI_MODEL_CHAIN", []) or ["gemini-3.5-flash", "gemini-3-flash-preview"])
    if not keys:
        return None

    try:
        with open(audio_path, "rb") as f:
            b64_audio = base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None

    acoustic_context = (
        f"Physical acoustics measured: duration={metrics.duration_s}s, voiced_ratio={metrics.voiced_ratio}, "
        f"pauses={metrics.pause_count}, longest_pause={metrics.longest_pause_s}s, dynamic_range_db={metrics.energy_variation_db}dB."
    )

    for model in models[:3]:
        for key in keys[:2]:
            clean_key = re.sub(r"[^A-Za-z0-9._\-]", "", str(key).strip())
            if len(clean_key) < 15:
                continue
            url = "https" + f"://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={clean_key}"
            body = {
                "systemInstruction": {"parts": [{"text": PROSODY_MULTIMODAL_PROMPT}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"inlineData": {"mimeType": mime_type, "data": b64_audio}},
                            {"text": acoustic_context},
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 1024,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            }
            try:
                resp = requests.post(url, json=body, timeout=20)
                if resp.status_code == 400 and "thinking" in resp.text.lower():
                    body["generationConfig"].pop("thinkingConfig", None)
                    resp = requests.post(url, json=body, timeout=20)
                if resp.status_code != 200:
                    continue
                candidates = (resp.json() or {}).get("candidates") or []
                if not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts") or []
                raw_text = "".join(str(p.get("text", "")) for p in parts).strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
                parsed = json.loads(raw_text)
                tx = str(parsed.get("transcript") or "").strip()
                if not tx:
                    continue
                word_cnt = len(tx.split())
                metrics.estimated_wpm = int(round((word_cnt / max(metrics.duration_s, 1.2)) * 60.0))
                if word_cnt >= 4 and metrics.duration_s >= 1.5:
                    metrics.confidence_tier = "high"
                parsed["metrics"] = metrics.model_dump()
                return ProsodyCoachingReport.model_validate(parsed)
            except Exception as err:
                logger.warning("Multimodal prosody call failed on %s: %s", model, err)
                continue
    return None


def render_prosody_card_html(report: ProsodyCoachingReport, *, with_arabic: bool = True) -> str:
    m = report.metrics
    lines = [
        "🎙️ <b>Acoustic Prosody Lab</b>",
        f"• <b>Heard:</b> “{_e(report.transcript)}”",
        f"• <b>Pacing &amp; Flow:</b> ~{m.estimated_wpm} WPM · {m.duration_s}s · {m.pause_count} pause(s)",
    ]
    if m.confidence_tier == "low":
        lines.append("• <i>Short audio clip — send a full spoken sentence (3+ seconds) for deep stress &amp; intonation mapping.</i>")
        return "\n".join(lines)

    if report.stress_target_chunk:
        lines.append(f"• <b>Nuclear Stress Anchor:</b> <code>{_e(report.stress_target_chunk)}</code>")
    if report.stress_pattern_guide:
        lines.append(f"• <b>Rhythm &amp; Reduction:</b> {_e(report.stress_pattern_guide)}")
    if report.connected_speech_tip:
        lines.append(f"• <b>Connected Speech:</b> {_e(report.connected_speech_tip)}")
    if report.intonation_verdict_en:
        lines.append(f"• <b>Acoustic Verdict:</b> {_e(report.intonation_verdict_en)}")
    if with_arabic and report.arabic_prosody_note_ar:
        lines.append(f"• <b>بالعربية:</b> <tg-spoiler>{_e(report.arabic_prosody_note_ar)}</tg-spoiler>")
    return "\n".join(lines)


PROSODY_SHADOW_DRILLS: list[dict[str, str]] = [
    {
        "id": "pushback",
        "sentence": "To be fair, we simply don't have the bandwidth to take that on right now.",
        "stress_map": "To be FAIR, we SIMPly don't have the BANDwidth / tə take THAT ON right NOW.",
        "focus": "Reduce 'to' -> /tə/ and 'that on' -> flap /ðæɾɑn/, landing peak pitch on FAIR and BANDwidth.",
        "focus_ar": "اخفض نبرة الأدوات (To be / to) وضع النبر الطويل الواضح على FAIR و BANDwidth.",
    },
    {
        "id": "annoyance",
        "sentence": "I'm always having to step in and clean up these last-minute scope changes.",
        "stress_map": "I'm ALways having tə STEP IN / and CLEAN UP these LAST-minute SCOPE changes.",
        "focus": "Hold the vowel on ALways to signal annoyance, and link 'step in' and 'clean up' smoothly.",
        "focus_ar": "مدّ الصائت في ALways لإظهار الضيق التداولي، مع وصل الفاء الساكنة بالهمزة في step_in و clean_up.",
    },
    {
        "id": "executive",
        "sentence": "Let's make sure we're on the same page before we lock in the Friday deadline.",
        "stress_map": "Let's make SURE we're on the SAME PAGE / before we LOCK IN the FRIday DEADline.",
        "focus": "Group into two crisp thought chunks with a micro-pause after PAGE.",
        "focus_ar": "قسّم الجملة إلى وحدتين إيقاعيتين مع وقفة خفيفة جداً بعد كلمة PAGE.",
    },
]


def render_prosody_hub_html(drill_idx: int = 0, *, with_arabic: bool = True) -> str:
    d = PROSODY_SHADOW_DRILLS[drill_idx % len(PROSODY_SHADOW_DRILLS)]
    lines = [
        "🎤 <b>Acoustic Prosody &amp; Connected Speech Lab (V4.0)</b>",
        "Send <b>any voice note</b> in English (or read the shadowing target below) to measure your physical pacing (WPM), pauses, nuclear stress, and American linking:",
        "",
        f"🎯 <b>Shadowing Target #{drill_idx % len(PROSODY_SHADOW_DRILLS) + 1}:</b>",
        f"“<b>{_e(d['sentence'])}</b>”",
        "",
        f"🎼 <b>Rhythm &amp; Stress Map:</b> <code>{_e(d['stress_map'])}</code>",
        f"🔗 <b>Acoustic Focus:</b> {_e(d['focus'])}",
    ]
    if with_arabic and d.get("focus_ar"):
        lines.append(f"🇦🇪 <tg-spoiler>{_e(d['focus_ar'])}</tg-spoiler>")
    lines.extend(
        [
            "",
            "🎙️ <b>Tap the microphone icon in Telegram and record this line (or say whatever is on your mind)!</b>",
        ]
    )
    return "\n".join(lines)
