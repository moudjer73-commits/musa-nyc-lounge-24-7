from dataclasses import dataclass
import math, shutil, subprocess
import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_LEN = 320

@dataclass
class AcousticMetrics:
    total_s: float = 0.0
    voiced_s: float = 0.0
    pause_ratio: float = 0.0
    mid_pauses: int = 0
    articulation_wpm: float = 0.0
    stress_contrast_db: float = 0.0
    pitch_span_st: float = 0.0
    rhythm_flag: str = 'balanced'

    def to_prompt_summary(self) -> str:
        if self.voiced_s <= 0.2:
            return 'Acoustic DSP unavailable (short clip).'
        return (
            f'OBJECTIVE DSP TELEMETRY: active_speech={self.voiced_s:.1f}s/{self.total_s:.1f}s | '
            f'articulation_rate={self.articulation_wpm:.0f} WPM | pause_ratio={self.pause_ratio*100:.0f}% '
            f'({self.mid_pauses} mid-pauses >250ms) | stress_contrast={self.stress_contrast_db:.1f} dB | '
            f'pitch_span={self.pitch_span_st:.1f} semitones | diagnostic={self.rhythm_flag}'
        )

    def compact_badge(self) -> str:
        if self.voiced_s <= 0.2:
            return ''
        w = f'{self.articulation_wpm:.0f} WPM' if self.articulation_wpm > 0 else f'{self.voiced_s:.1f}s'
        return f'{w} · pauses {self.pause_ratio*100:.0f}% · contrast {self.stress_contrast_db:.1f}dB · pitch {self.pitch_span_st:.1f}st'

def _decode_pcm16(audio_bytes: bytes):
    if not audio_bytes or not shutil.which('ffmpeg'):
        return None
    try:
        p = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0', '-f', 's16le', '-ac', '1', '-ar', '16000', 'pipe:1'], input=audio_bytes, capture_output=True, timeout=8)
        if p.returncode != 0 or len(p.stdout) < 1280:
            return None
        return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None

def analyze_pcm_samples(pcm: np.ndarray, word_count: int = 0) -> AcousticMetrics:
    nf = len(pcm) // FRAME_LEN
    if nf < 8:
        return AcousticMetrics()
    frames = pcm[: nf * FRAME_LEN].reshape(nf, FRAME_LEN)
    db = 20.0 * np.log10(np.sqrt(np.mean(frames * frames, axis=1) + 1e-10))
    thresh = max(float(np.percentile(db, 15)) + 6.0, float(np.percentile(db, 90)) - 24.0, -48.0)
    voiced = db >= thresh
    idx = np.where(voiced)[0]
    if len(idx) < 4:
        return AcousticMetrics(total_s=round(len(pcm) / SAMPLE_RATE, 2))
    i0, i1 = int(idx[0]), int(idx[-1])
    inter = voiced[i0 : i1 + 1]
    inter_db = db[i0 : i1 + 1]
    span_s = len(inter) * 0.02
    voiced_s = float(np.sum(inter)) * 0.02
    pause_ratio = max(0.0, 1.0 - (voiced_s / max(span_s, 0.01)))
    mid_pauses = silent_run = 0
    for v in inter:
        if not v:
            silent_run += 1
        else:
            if silent_run >= 13:
                mid_pauses += 1
            silent_run = 0
    v_db = inter_db[inter]
    contrast = float(np.percentile(v_db, 85) - np.percentile(v_db, 25)) if len(v_db) >= 4 else 0.0
    f0s = []
    for fi in range(i0, i1 + 1, 2):
        if not voiced[fi] or fi + 1 >= nf:
            continue
        seg = pcm[fi * FRAME_LEN : (fi + 2) * FRAME_LEN]
        seg = seg - float(np.mean(seg))
        e0 = float(np.dot(seg, seg))
        if e0 < 1e-6:
            continue
        corr = np.correlate(seg, seg, mode='full')[len(seg) - 1 :]
        win = corr[44 : min(213, len(corr))]
        if len(win) >= 4:
            rel = int(np.argmax(win))
            if float(win[rel]) / e0 >= 0.32:
                f0s.append(SAMPLE_RATE / float(44 + rel))
    pitch_st = 0.0
    if len(f0s) >= 5:
        flo, fhi = float(np.percentile(f0s, 12)), float(np.percentile(f0s, 88))
        if flo > 40.0 and fhi > flo:
            pitch_st = 12.0 * math.log2(fhi / flo)
    wpm = round((word_count / voiced_s) * 60.0, 1) if (word_count > 0 and voiced_s >= 0.8) else 0.0
    flags = []
    if contrast < 5.2 and voiced_s >= 2.0:
        flags.append('flat_syllable_energy (under-reduced function words)')
    if 0 < pitch_st < 3.0 and voiced_s >= 2.0:
        flags.append('narrow_flat_pitch (weak focus word peak)')
    if pause_ratio >= 0.34 or mid_pauses >= 3:
        flags.append('choppy_mid_clause_pauses')
    if wpm > 195:
        flags.append('rushed_tempo')
    elif 0 < wpm < 105 and voiced_s >= 2.5:
        flags.append('guarded_word_by_word_tempo')
    return AcousticMetrics(
        total_s=round(len(pcm) / SAMPLE_RATE, 2), voiced_s=round(voiced_s, 2),
        pause_ratio=round(pause_ratio, 2), mid_pauses=mid_pauses, articulation_wpm=wpm,
        stress_contrast_db=round(contrast, 1), pitch_span_st=round(pitch_st, 1),
        rhythm_flag='; '.join(flags) if flags else 'healthy_us_prosody_envelope',
    )

def analyze_voice_bytes(audio_bytes: bytes, word_count: int = 0) -> AcousticMetrics:
    pcm = _decode_pcm16(audio_bytes)
    return AcousticMetrics() if pcm is None else analyze_pcm_samples(pcm, word_count=word_count)
