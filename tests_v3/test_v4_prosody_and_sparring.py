"""Tests for V4.0 Sprint 2: Acoustic Prosody Lab & 3-Way Multi-Persona Sparring."""

from __future__ import annotations

import math
import os
import struct
import tempfile
import wave

import prosody
import sparring
from database import EventSourcedDatabase
from flows import V3Flows


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return EventSourcedDatabase(path), path


def _make_synthetic_wav(duration_s: float = 2.8, sr: int = 16000) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    n_samples = int(duration_s * sr)
    samples = []
    for i in range(n_samples):
        t = i / float(sr)
        # Alternate between voiced segments and a 0.35s pause in the middle
        if 1.1 <= t <= 1.45:
            samples.append(0)
        else:
            amp = 12000 if (int(t * 3) % 2 == 0) else 5000
            val = int(amp * math.sin(2.0 * math.pi * 180.0 * t))
            samples.append(val)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def test_physical_acoustic_extraction_and_card_rendering():
    wav_path = _make_synthetic_wav(2.8)
    try:
        metrics = prosody.extract_physical_acoustics(wav_path, transcript_word_count=7)
        assert metrics.duration_s >= 2.5
        assert metrics.pause_count >= 1
        assert metrics.confidence_tier in ("high", "medium")

        report = prosody.build_deterministic_prosody_report(
            "I need to push back on that timeline.", metrics
        )
        html_card = prosody.render_prosody_card_html(report, with_arabic=True)
        assert "Acoustic Prosody Lab" in html_card
        assert "Nuclear Stress Anchor" in html_card
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


def test_three_way_sparring_lifecycle():
    db, path = _make_db()
    try:
        lid = 99005
        flows = V3Flows(db, None)
        open_view = flows.sparring_hub_view(lid, topic_key="ai_decisions")
        assert "3-Way Sparring Table" in open_view.text

        # Learner sends an argument while in sparring mode
        res = flows.handle_text(lid, "We need human oversight whenever an algorithm impacts public services.")
        assert len(res.views) == 1
        assert "Alex:" in res.views[0].text
        assert "Maya:" in res.views[0].text
        assert "Power Pivot Chunk" in res.views[0].text

        # Exit back to 1-on-1 scene
        exit_cb = flows.handle_v4_callback(lid, "spar:exit")
        assert exit_cb.edit is not None
        assert sparring.get_sparring(lid) is None
    finally:
        if os.path.exists(path):
            os.remove(path)
