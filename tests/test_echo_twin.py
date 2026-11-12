import asyncio
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from src.bot.handlers import BTN_LAB, BTN_SHIFT, TwinContainer, register_handlers
from src.twin.corpus_guard import has_contrived_buzz, scrub_contrived_english
from src.twin.engine import GhostTwinAnalysis, ProsodyAuditOutput, RegisterBreakdown, ShiftTurnOutput
from src.voice.acoustics import SAMPLE_RATE, analyze_pcm_samples, compare_metrics


class StubLabLLM:
    configured = True
    def __init__(self):
        self.voice_calls = 0
        self.prosody_audits = 0

    async def audit_voice_prosody(self, user_id, system_prompt, audio_bytes, user_prompt, schema_cls, fallback, *, mime_type="audio/ogg"):
        self.prosody_audits += 1
        is_retake = ("A/B RETAKE MODE ACTIVE" in user_prompt) or ("PREVIOUS TAKE ON FILE:" in user_prompt)
        return ProsodyAuditOutput(
            verbatim_heard="I headed back home and picked up a bunch of fruit.",
            has_findings=(not is_retake),
            habit_key="us_rhythm_linking",
            prosodic_map="I ˈHEAD-əd ˈBACK | ən ˈPICKED‿up‿ə ˈBUNCH‿ə ˈFRUIT ↘",
            segmental_fix="" if is_retake else "Crush 'and' -> /ən/ and 'of' -> /ə/",
            prosody_fix="Link 'picked_up_a' smoothly with zero glottal stops",
            focus_stress="BUNCH of FRUIT ↘",
            shadow_chunk="picked up a bunch of fruit",
            native_recast="I headed back home and picked up a bunch of fruit.",
            grammar_fix="",
            retake_verdict="Much stronger vowel reduction and smoother C-V linking on 'picked_up_a'!" if is_retake else "",
        )

    async def generate_voice_turn(self, user_id, system_prompt, audio_bytes, user_prompt, *, mime_type="audio/ogg", temperature=0.62):
        self.voice_calls += 1
        return "===HEARD===\nI buyed many fruit today\n===REPLY===\nNice, what kind of fruit did you get?\n===COACH===\nNONE"

    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.62, deep_chat=True):
        return "We usually utilize that right prior to the meeting."

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False, temperature=0.25):
        if schema_cls is RegisterBreakdown:
            return RegisterBreakdown(
                is_register_neutral=True,
                casual_us="I just picked up some fruit.",
                work_us="I just picked up some fruit.",
                seminar_us="I just picked up some fruit.",
                pragmatic_note="Plain spoken US stays identical across settings.",
            )
        if schema_cls is ShiftTurnOutput:
            return ShiftTurnOutput(
                reframed_user_point="My main worry is whether this timeline holds up.",
                same_across_registers=False,
                partner_reply="Fair point—what's the biggest holdup?",
                key_chunk="whether this holds up",
            )
        return GhostTwinAnalysis(should_intervene=True, native_take="I just picked up some fruit.", key_chunk="picked up")


class FakeApp:
    def __init__(self):
        self.handlers = []
    def add_handler(self, h, group=0):
        self.handlers.append(h)
    def add_error_handler(self, cb):
        self.err = cb
    def cb(self, name):
        return next(h.callback for h in self.handlers if getattr(h.callback, "__name__", "") == name)


class FakeMsg:
    def __init__(self, text="", voice=None, mid=10):
        self.text, self.caption, self.voice, self.reply_to_message, self.message_id = text, None, voice, None, mid
        self.texts, self.voices = [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(text="", mid=200 + len(self.voices))


def test_v5_acoustics_npvi_and_corpus_scrubber():
    assert scrub_contrived_english("Prior to utilizing it") == "Before using it"
    assert has_contrived_buzz("Let's circle back")
    t = np.linspace(0, 0.20, int(0.20 * SAMPLE_RATE), endpoint=False)
    flat_sig = np.concatenate([val for _ in range(4) for val in (0.2 * np.sin(2 * np.pi * 140 * t), np.zeros(800))]).astype(np.float32)
    m1 = analyze_pcm_samples(flat_sig, 4)
    assert m1.npvi < 20 and "syllable-flat" in m1.lab_meter_line()


@pytest.mark.asyncio
async def test_v5_pure_prosody_lab_mode_drills_and_ab_retake(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod
    from src.voice.acoustics import AcousticMetrics

    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"P"))
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", lambda t: asyncio.sleep(0, result=b"T"))
    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"D3X"))

    # Simulate Take 1 (flat rhythm: score 42, nPVI 28) -> Take 2 Retake (US stress-timed: score 81, nPVI 62)
    takes = [
        AcousticMetrics(total_s=2.4, voiced_s=2.0, syllables=8, articulation_wpm=140, npvi=28.0, pause_ratio=0.22, stress_contrast_db=3.8, pitch_span_st=2.4, boundary_tone="level ➡️", prosody_score=42),
        AcousticMetrics(total_s=2.1, voiced_s=1.8, syllables=8, articulation_wpm=155, npvi=62.0, pause_ratio=0.12, stress_contrast_db=8.5, pitch_span_st=5.8, boundary_tone="falling ↘", prosody_score=81),
    ]
    monkeypatch.setattr(e_mod, "analyze_voice_bytes", lambda b, wc=0: takes.pop(0) if takes else AcousticMetrics(voiced_s=1.5, npvi=60.0, prosody_score=80))

    llm = StubLabLLM()
    c = TwinContainer(db_path=tmp_path / "v5.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=77)
    ctx = SimpleNamespace(args=[], bot=None)

    # 1. Tap 🔬 Prosody Lab button -> Toggle Pure Lab Mode ON
    m_lab = FakeMsg(text=BTN_LAB)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m_lab), ctx)
    assert "US Prosody &amp; Rhythm Lab v5" in m_lab.texts[0][0]

    q_toggle = SimpleNamespace(data="lab:toggle", message=FakeMsg(text="lab"), answer=lambda *a, **k: asyncio.sleep(0), edit_message_text=lambda *a, **k: asyncio.sleep(0))
    await app.cb("lab_cb")(SimpleNamespace(effective_user=u, callback_query=q_toggle), ctx)
    assert c.repo.get_lab_state(77)["lab_mode"] == 1

    # 2. Voice Take #1 in Pure Lab Mode -> 0 partner chit-chat calls, 1 prosody audit with Map & 3-Beat audio!
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))
    vo = SimpleNamespace(duration=3.0, file_size=4000, get_file=lambda: asyncio.sleep(0, result=dl))
    mv1 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv1), ctx)
    assert llm.voice_calls == 0 and llm.prosody_audits == 1
    cap1 = mv1.voices[0]["caption"]
    assert "🗺️ <b>Map:</b>" in cap1 and "PICKED‿up‿ə" in cap1

    # 3. Voice Take #2 (Repeating same sentence) -> Auto-triggers A/B Retake Delta (42 ➔ 81/100)!
    mv2 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv2), ctx)
    cap2 = mv2.voices[0]["caption"]
    assert "A/B Retake" in cap2 and "42 ➔ 81/100 (+39)" in cap2 and "Verdict:" in cap2
