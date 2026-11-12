from dataclasses import asdict, dataclass
import json, math, shutil, subprocess
import numpy as np

SAMPLE_RATE = 16000
HOP = 160        # 10ms hop for accurate syllable interval timing
F0_WIN = 480     # 30ms window for reliable F0 autocorrelation down to 75Hz


@dataclass
class AcousticMetrics:
    total_s: float = 0.0
    voiced_s: float = 0.0
    syllables: int = 0
    syl_per_sec: float = 0.0
    articulation_wpm: float = 0.0
    npvi: float = 0.0
    pause_ratio: float = 0.0
    mid_pauses: int = 0
    stress_contrast_db: float = 0.0
    pitch_span_st: float = 0.0
    boundary_st: float = 0.0
    boundary_tone: str = "level ➡️"
    prosody_score: int = 0
    rhythm_flag: str = "balanced"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "AcousticMetrics | None":
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None

    def rhythm_label(self) -> str:
        if self.syllables < 3 or self.voiced_s < 0.45:
            return "short clip"
        if self.npvi < 38.0:
            return "syllable-flat ⚠️"
        if self.pause_ratio >= 0.35 or self.mid_pauses >= 3:
            return "choppy pauses ⚠️"
        return "stress-timed US ✅"

    def lab_meter_line(self) -> str:
        if self.voiced_s <= 0.15:
            return ""
        return (
            f"Score {self.prosody_score}/100 · nPVI {self.npvi:.0f} ({self.rhythm_label()}) · "
            f"Contrast {self.stress_contrast_db:.1f}dB · Pitch {self.pitch_span_st:.1f}st ({self.boundary_tone})"
        )

    def compact_badge(self) -> str:
        return self.lab_meter_line()

    def to_prompt_summary(self) -> str:
        if self.voiced_s <= 0.15:
            return "OBJECTIVE DSP TELEMETRY: clip too short for acoustic contour."
        return (
            f"OBJECTIVE PRAAT-STYLE DSP TELEMETRY: "
            f"prosody_score={self.prosody_score}/100 | active_voiced={self.voiced_s:.2f}s/{self.total_s:.2f}s | "
            f"syllables={self.syllables} ({self.syl_per_sec:.1f} syl/s, {self.articulation_wpm:.0f} WPM) | "
            f"vocalic_nPVI={self.npvi:.1f} (US target 50-75; <38 means French/Arabic syllable-timed flat rhythm) | "
            f"energy_stress_contrast={self.stress_contrast_db:.1f} dB (US target >=6.5 dB between content & schwa words) | "
            f"pause_ratio={self.pause_ratio*100:.0f}% ({self.mid_pauses} mid-clause pauses >250ms) | "
            f"pitch_span={self.pitch_span_st:.1f} semitones | final_boundary_tone={self.boundary_tone} ({self.boundary_st:+.1f} st) | "
            f"diagnostic={self.rhythm_flag}"
        )


def compare_metrics(prev: AcousticMetrics | None, curr: AcousticMetrics) -> str:
    if prev is None or prev.voiced_s <= 0.2 or curr.voiced_s <= 0.2:
        return ""
    ds = curr.prosody_score - prev.prosody_score
    dn = curr.npvi - prev.npvi
    dc = curr.stress_contrast_db - prev.stress_contrast_db
    dp = (curr.pause_ratio - prev.pause_ratio) * 100.0
    s_icon = "✅" if ds >= 3 else ("➖" if ds >= -2 else "⚠️")
    n_icon = "✅" if (42 <= curr.npvi <= 130 or abs(curr.npvi - 60) < abs(prev.npvi - 60)) else "🎯"
    c_icon = "✅" if curr.stress_contrast_db >= 6.0 or dc >= 1.0 else "🎯"
    return (
        f"• US Prosody Score: {prev.prosody_score} ➔ {curr.prosody_score}/100 ({ds:+d}) {s_icon}\n"
        f"• Rhythm (nPVI): {prev.npvi:.0f} ➔ {curr.npvi:.0f} ({dn:+.0f}) {n_icon}\n"
        f"• Stress Contrast: {prev.stress_contrast_db:.1f}dB ➔ {curr.stress_contrast_db:.1f}dB ({dc:+.1f}dB) {c_icon}\n"
        f"• Pause Ratio: {prev.pause_ratio*100:.0f}% ➔ {curr.pause_ratio*100:.0f}% ({dp:+.0f}%) · End {curr.boundary_tone}"
    )


def _decode_pcm16(audio_bytes: bytes) -> np.ndarray | None:
    if not audio_bytes or not shutil.which("ffmpeg"):
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
            input=audio_bytes, capture_output=True, timeout=8,
        )
        if proc.returncode != 0 or len(proc.stdout) < HOP * 16:
            return None
        return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None


def _detect_syllables(inter_env: np.ndarray, inter_v: np.ndarray, p95: float) -> tuple[list[int], list[float]]:
    m = len(inter_env)
    if m < 6:
        return [], []
    # Find local plateau-safe maxima across +/-30ms
    raw_cands: list[int] = []
    k = 0
    while k < m:
        if not inter_v[k] or inter_env[k] < p95 - 22.0:
            k += 1
            continue
        w_lo, w_hi = max(0, k - 3), min(m, k + 4)
        local_top = float(np.max(inter_env[w_lo:w_hi]))
        if inter_env[k] >= local_top - 1e-4:
            j = k
            while j + 1 < m and inter_v[j + 1] and abs(inter_env[j + 1] - inter_env[k]) <= 0.15:
                j += 1
            raw_cands.append((k + j) // 2)
            k = j + 1
        else:
            k += 1

    peaks: list[int] = []
    for p in raw_cands:
        if not peaks:
            peaks.append(p)
            continue
        prev_p = peaks[-1]
        if p - prev_p < 8:  # < 80ms: merge into stronger peak
            if inter_env[p] > inter_env[prev_p]:
                peaks[-1] = p
            continue
        seg_v = inter_v[prev_p : p + 1]
        seg_e = inter_env[prev_p : p + 1]
        has_gap = not bool(np.all(seg_v))
        dip_db = min(float(inter_env[prev_p]), float(inter_env[p])) - float(np.min(seg_e))
        if has_gap or dip_db >= 1.8:
            peaks.append(p)
        elif inter_env[p] > inter_env[prev_p]:
            peaks[-1] = p

    if not peaks:
        return [], []

    durs: list[float] = []
    s_cnt = len(peaks)
    for idx_p, p in enumerate(peaks):
        lb = 0 if idx_p == 0 else (peaks[idx_p - 1] + int(np.argmin(inter_env[peaks[idx_p - 1] : p + 1])))
        rb = (m - 1) if idx_p == s_cnt - 1 else (p + int(np.argmin(inter_env[p : peaks[idx_p + 1] + 1])))
        floor_p = float(inter_env[p]) - 15.0
        l_cur = p
        while l_cur > lb and inter_v[l_cur - 1] and inter_env[l_cur - 1] >= floor_p:
            l_cur -= 1
        r_cur = p
        while r_cur < rb and inter_v[r_cur + 1] and inter_env[r_cur + 1] >= floor_p:
            r_cur += 1
        durs.append(max(1, r_cur - l_cur + 1) * 0.01)
    return peaks, durs


def analyze_pcm_samples(pcm: np.ndarray, word_count: int = 0) -> AcousticMetrics:
    total_s = round(len(pcm) / SAMPLE_RATE, 2)
    nf = len(pcm) // HOP
    if nf < 12:
        return AcousticMetrics(total_s=total_s)

    frames = pcm[: nf * HOP].reshape(nf, HOP)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms)
    padded = np.pad(db, 2, mode="edge")
    env_db = np.convolve(padded, np.array([0.15, 0.20, 0.30, 0.20, 0.15]), mode="valid")

    p95 = float(np.percentile(env_db, 95))
    if p95 < -50.0:
        return AcousticMetrics(total_s=total_s)
    p05 = float(np.percentile(env_db, 5))
    # Clamp threshold to at most p95 - 10dB so continuous 100%-voiced speech is NEVER zeroed out
    thresh = min(max(p05 + 6.0, p95 - 26.0, -48.0), p95 - 10.0)
    voiced = env_db >= thresh

    v_idx = np.where(voiced)[0]
    if len(v_idx) < 6:
        return AcousticMetrics(total_s=total_s)

    i0, i1 = int(v_idx[0]), int(v_idx[-1])
    inter_v = voiced[i0 : i1 + 1]
    inter_env = env_db[i0 : i1 + 1]

    span_s = len(inter_v) * 0.01
    voiced_s = float(np.sum(inter_v)) * 0.01
    pause_ratio = max(0.0, 1.0 - (voiced_s / max(span_s, 0.01)))

    mid_pauses = silent_run = 0
    for is_v in inter_v:
        if not is_v:
            silent_run += 1
        else:
            if silent_run >= 25:  # >= 250ms mid-utterance hesitation
                mid_pauses += 1
            silent_run = 0

    peaks, syl_durs = _detect_syllables(inter_env, inter_v, p95)
    syl_count = len(peaks)
    syl_per_sec = round(syl_count / max(voiced_s, 0.1), 2) if syl_count > 0 else 0.0

    npvi = 0.0
    if len(syl_durs) >= 2:
        diffs = [
            abs(syl_durs[k] - syl_durs[k + 1]) / max((syl_durs[k] + syl_durs[k + 1]) * 0.5, 1e-4)
            for k in range(len(syl_durs) - 1)
        ]
        npvi = round(100.0 * float(np.mean(diffs)), 1)

    v_db = inter_env[inter_v]
    if len(peaks) >= 3:
        pk_vals = inter_env[peaks]
        contrast = float(np.percentile(pk_vals, 85) - np.percentile(v_db, 20))
    else:
        contrast = float(np.percentile(v_db, 85) - np.percentile(v_db, 20)) if len(v_db) >= 4 else 0.0

    # F0 Pitch contour & Boundary Tone (75 Hz - 360 Hz)
    f0_pts: list[tuple[int, float]] = []
    for fi in range(i0, i1 + 1, 2):
        if not voiced[fi] or fi * HOP + F0_WIN > len(pcm):
            continue
        seg = pcm[fi * HOP : fi * HOP + F0_WIN]
        seg = seg - float(np.mean(seg))
        e0 = float(np.dot(seg, seg))
        if e0 < 1e-6:
            continue
        corr = np.correlate(seg, seg, mode="full")[F0_WIN - 1 :]
        win = corr[44 : min(213, len(corr))]
        if len(win) >= 4:
            rel = int(np.argmax(win))
            if float(win[rel]) / e0 >= 0.30:
                f0_pts.append((fi - i0, SAMPLE_RATE / float(44 + rel)))

    pitch_st = 0.0
    boundary_st = 0.0
    boundary_tone = "level ➡️"
    if len(f0_pts) >= 5:
        hz_vals = np.array([p[1] for p in f0_pts])
        flo, fhi = float(np.percentile(hz_vals, 12)), float(np.percentile(hz_vals, 88))
        if flo > 40.0 and fhi > flo:
            pitch_st = 12.0 * math.log2(fhi / flo)
        cut = max(2, int(len(hz_vals) * 0.72))
        body_med = float(np.median(hz_vals[:cut]))
        tail_med = float(np.median(hz_vals[cut:]))
        if body_med > 40.0 and tail_med > 40.0:
            boundary_st = 12.0 * math.log2(tail_med / body_med)
            if boundary_st <= -1.8:
                boundary_tone = "falling ↘"
            elif boundary_st >= 1.8:
                boundary_tone = "rising ↗"

    est_words = word_count if word_count > 0 else max(1, round(syl_count / 1.45))
    wpm = round((est_words / voiced_s) * 60.0, 1) if voiced_s >= 0.4 else 0.0

    # Calibrated 0-100 US Prosody Score across 4 suprasegmental pillars
    r_sc = 35.0 * min(1.0, max(0.0, (npvi - 18.0) / 34.0)) if npvi <= 88 else max(15.0, 35.0 - (npvi - 88) * 0.4)
    c_sc = 25.0 * min(1.0, max(0.0, (contrast - 2.0) / 5.5))
    p_sc = 20.0 * max(0.0, 1.0 - max(0.0, pause_ratio - 0.16) * 2.2 - mid_pauses * 0.18)
    i_sc = 20.0 * min(1.0, max(0.0, (pitch_st - 1.2) / 3.8))
    score = int(round(min(99.0, max(12.0, r_sc + c_sc + p_sc + i_sc))))

    flags: list[str] = []
    if syl_count >= 3 and npvi < 38.0:
        flags.append("syllable_timed_flat_rhythm (crush function words to schwa & stretch stressed vowels)")
    if contrast < 5.0 and voiced_s >= 1.2:
        flags.append("low_energy_stress_contrast (all syllables equally loud)")
    if 0 < pitch_st < 3.0 and voiced_s >= 1.2:
        flags.append("narrow_monotone_pitch (boost nuclear focus word)")
    if pause_ratio >= 0.32 or mid_pauses >= 3:
        flags.append("choppy_mid_clause_hesitations (link words inside thought groups)")

    return AcousticMetrics(
        total_s=total_s,
        voiced_s=round(voiced_s, 2),
        syllables=syl_count,
        syl_per_sec=syl_per_sec,
        articulation_wpm=wpm,
        npvi=npvi,
        pause_ratio=round(pause_ratio, 2),
        mid_pauses=mid_pauses,
        stress_contrast_db=round(contrast, 1),
        pitch_span_st=round(pitch_st, 1),
        boundary_st=round(boundary_st, 1),
        boundary_tone=boundary_tone,
        prosody_score=score,
        rhythm_flag="; ".join(flags) if flags else "natural_stress_timed_us",
    )


def analyze_voice_bytes(audio_bytes: bytes, word_count: int = 0) -> AcousticMetrics:
    pcm = _decode_pcm16(audio_bytes)
    return AcousticMetrics() if pcm is None else analyze_pcm_samples(pcm, word_count=word_count)


def transcode_for_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> tuple[bytes, str]:
    if not audio_bytes or not shutil.which("ffmpeg"):
        return audio_bytes, mime_type
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000",
                "-b:a", "32k", "-f", "mp3", "pipe:1",
            ],
            input=audio_bytes,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0 and len(proc.stdout) > 256:
            return proc.stdout, "audio/mp3"
    except Exception:
        pass
    return audio_bytes, mime_type


@dataclass
class MonologueArc:
    overall: AcousticMetrics
    open_third: AcousticMetrics
    mid_third: AcousticMetrics
    close_third: AcousticMetrics
    stamina_flag: str = "steady_stamina"

    def arc_badge(self) -> str:
        s1 = self.open_third.prosody_score
        s2 = self.mid_third.prosody_score
        s3 = self.close_third.prosody_score
        if s1 == 0 or s3 == 0:
            return self.overall.lab_meter_line()
        tag = (
            "⚠️ late fatigue"
            if ("decay" in self.stamina_flag or "spike" in self.stamina_flag)
            else "✅ steady stamina"
        )
        return (
            f"Stamina {s1}➔{s2}➔{s3}/100 ({tag}) · "
            f"nPVI {self.overall.npvi:.0f} · {self.overall.articulation_wpm:.0f} WPM"
        )

    def thirds_breakdown(self) -> str:
        o, m, c = self.open_third, self.mid_third, self.close_third
        if o.voiced_s < 0.3 or c.voiced_s < 0.3:
            return f"• Clip Meter: {self.overall.lab_meter_line()}"
        return (
            f"• Open  1/3: Score {o.prosody_score} · nPVI {o.npvi:.0f} · Pauses {o.pause_ratio*100:.0f}%\n"
            f"• Mid   1/3: Score {m.prosody_score} · nPVI {m.npvi:.0f} · Pauses {m.pause_ratio*100:.0f}%\n"
            f"• Close 1/3: Score {c.prosody_score} · nPVI {c.npvi:.0f} · Pauses {c.pause_ratio*100:.0f}% ({c.boundary_tone})"
        )

    def to_prompt_summary(self) -> str:
        return (
            f"{self.overall.to_prompt_summary()}\n"
            f"STAMINA THIRDS: Open(score={self.open_third.prosody_score}, nPVI={self.open_third.npvi:.0f}, "
            f"pauses={self.open_third.pause_ratio*100:.0f}%) -> "
            f"Mid(score={self.mid_third.prosody_score}, nPVI={self.mid_third.npvi:.0f}, "
            f"pauses={self.mid_third.pause_ratio*100:.0f}%) -> "
            f"Close(score={self.close_third.prosody_score}, nPVI={self.close_third.npvi:.0f}, "
            f"pauses={self.close_third.pause_ratio*100:.0f}%, end={self.close_third.boundary_tone}) | "
            f"stamina_diagnosis={self.stamina_flag}"
        )


def analyze_monologue_pcm(pcm: np.ndarray, word_count: int = 0) -> MonologueArc:
    overall = analyze_pcm_samples(pcm, word_count=word_count)
    if len(pcm) < SAMPLE_RATE * 3:
        e = AcousticMetrics()
        return MonologueArc(overall, e, e, e, "short_clip")

    third = len(pcm) // 3
    wc3 = max(1, word_count // 3) if word_count >= 3 else 0
    t1 = analyze_pcm_samples(pcm[:third], wc3)
    t2 = analyze_pcm_samples(pcm[third : 2 * third], wc3)
    t3 = analyze_pcm_samples(pcm[2 * third :], wc3)

    flags: list[str] = []
    if t1.npvi >= 42 and t3.npvi > 0 and (t1.npvi - t3.npvi) >= 12:
        flags.append("final_third_rhythm_decay")
    if (t3.pause_ratio - t1.pause_ratio) >= 0.14 or t3.mid_pauses >= t1.mid_pauses + 2:
        flags.append("late_hesitation_spike")
    if t1.pitch_span_st >= 4.0 and 0 < t3.pitch_span_st <= 2.4:
        flags.append("closing_pitch_flattening")

    return MonologueArc(
        overall, t1, t2, t3, "; ".join(flags) if flags else "steady_stamina"
    )


def analyze_monologue_bytes(audio_bytes: bytes, word_count: int = 0) -> MonologueArc:
    pcm = _decode_pcm16(audio_bytes)
    if pcm is None:
        e = AcousticMetrics()
        return MonologueArc(e, e, e, e, "unavailable")
    return analyze_monologue_pcm(pcm, word_count=word_count)
