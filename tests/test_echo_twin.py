import asyncio
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from src.bot.handlers import BTN_LAB, TwinContainer, register_handlers
from src.twin.corpus_guard import has_contrived_buzz, scrub_contrived_english
from src.twin.engine import GhostTwinAnalysis, ProsodyAuditOutput, RegisterBreakdown, ShiftTurnOutput
from src.voice.acoustics import SAMPLE_RATE, AcousticMetrics, analyze_pcm_samples


class StubUniversalLLM:
    configured = True
    def __init__(self):
        self.voice_calls = 0
        self.prosody_audits = 0
        self.next_heard = "Wait, what do you mean?"

    async def audit_voice_prosody(self, user_id, system_prompt, audio_bytes, user_prompt, schema_cls, fallback, *, mime_type="audio/ogg"):
        self.prosody_audits += 1
        is_retake = ("A/B RETAKE MODE ACTIVE" in user_prompt) or ("PREVIOUS TAKE ON FILE:" in user_prompt)
        return ProsodyAuditOutput(
            verbatim_heard=self.next_heard,
            phonetic_score=88 if is_retake else 64,
            has_findings=(not is_retake),
            habit_key="word_stress",
            prosodic_map="ˈWAIT | ˈWʌD-ə-yə ˈMEAN ↘",
            word_pronunciations="what do you: [wɑt duː juː] -> [ˈwʌɾəjə] (flap /t/ & reduce 'do you')",
            stress_and_schwa="Hold primary stress on WAIT and MEAN; reduce 'do you' to /dəjə/.",
            prosody_fix="Link 'what_do_you' without word-by-word glottal stops.",
            focus_stress="ˈMEAN ↘",
            shadow_chunk="Wait, what do you mean?",
            native_recast="",
            grammar_fix="",
            retake_verdict="Much smoother Flap-T and schwa reduction on 'what_do_you'!" if is_retake else "",
        )

    async def generate_voice_turn(self, user_id, system_prompt, audio_bytes, user_prompt, *, mime_type="audio/ogg", temperature=0.62):
        self.voice_calls += 1
        return f"===HEARD===\n{self.next_heard}\n===REPLY===\nOh my bad—I just meant let's take our time.\n===COACH===\nNONE"

    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.62, deep_chat=True):
        return "Makes sense."

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False, temperature=0.25):
        return fallback


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


@pytest.mark.asyncio
async def test_universal_pronunciation_on_repair_greeting_and_single_word(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod

    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"P"))
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", lambda t: asyncio.sleep(0, result=b"T"))
    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"D3X"))
    monkeypatch.setattr(
        e_mod, "analyze_voice_bytes",
        lambda b, wc=0: AcousticMetrics(total_s=1.8, voiced_s=1.5, syllables=5, npvi=44.0, pitch_span_st=4.5, boundary_tone="falling ↘", prosody_score=68),
    )

    llm = StubUniversalLLM()
    c = TwinContainer(db_path=tmp_path / "u.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=88)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))
    vo = SimpleNamespace(duration=2.0, file_size=3000, mime_type="audio/ogg", get_file=lambda: asyncio.sleep(0, result=dl))

    # 1. Repair question ("Wait, what do you mean?") previously skipped -> NOW gets full pronunciation assessment!
    mv1 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv1), ctx)
    pend = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    if pend:
        await asyncio.gather(*pend, return_exceptions=True)

    assert llm.prosody_audits == 1
    assert len(mv1.voices) == 2
    lab_cap = mv1.voices[1]["caption"]
    assert "Sounds &amp; Words:" in lab_cap and "Stress &amp; Schwa:" in lab_cap and "Heard:" in lab_cap

    # 2. Single test word ("Comfortable") in Hybrid Mode -> ALSO gets full pronunciation assessment!
    llm.next_heard = "Comfortable"
    mv2 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv2), ctx)
    pend = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    if pend:
        await asyncio.gather(*pend, return_exceptions=True)

    assert llm.prosody_audits == 2
    assert "Comfortable" in mv2.voices[1]["caption"]


@pytest.mark.asyncio
async def test_pure_lab_ab_retake_delta(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod

    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"D3X"))
    takes = [
        AcousticMetrics(total_s=2.2, voiced_s=1.9, syllables=6, npvi=30.0, stress_contrast_db=3.5, pitch_span_st=2.2, prosody_score=45),
        AcousticMetrics(total_s=2.0, voiced_s=1.7, syllables=6, npvi=60.0, stress_contrast_db=8.2, pitch_span_st=5.6, boundary_tone="falling ↘", prosody_score=84),
    ]
    monkeypatch.setattr(e_mod, "analyze_voice_bytes", lambda b, wc=0: takes.pop(0))

    llm = StubUniversalLLM()
    c = TwinContainer(db_path=tmp_path / "ab.db", passphrase="", llm=llm)
    c.repo.set_lab_mode(99, True)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=99)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))
    vo = SimpleNamespace(duration=2.2, file_size=3000, mime_type="audio/ogg", get_file=lambda: asyncio.sleep(0, result=dl))

    m1 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m1), ctx)
    m2 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m2), ctx)
    assert "Before ➔ After:" in m2.voices[0]["caption"] and "Verdict:" in m2.voices[0]["caption"]
