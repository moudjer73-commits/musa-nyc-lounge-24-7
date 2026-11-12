import asyncio, subprocess, tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import TwinContainer, register_handlers
from src.voice.acoustics import AcousticMetrics
from src.voice.tts import PARTNER_VOICES, TWIN_VOICE, merge_mirror_clips


class StubFastV61LLM:
    configured = True
    def __init__(self):
        self.fast_calls = 0

    async def generate_fast_multimodal(
        self, user_id, system_prompt, audio_bytes, user_prompt,
        *, mime_type="audio/ogg", temperature=0.3
    ):
        self.fast_calls += 1
        return (
            "===HEARD===\nI want to participate in local governance.\n"
            "===REPLY===\nThat's a huge topic—where are you starting first?\n"
            "===SCORE===\n76\n"
            "===HABIT===\nword_stress\n"
            "===MAP===\nI ˈWAN-ə pɑr-ˈTI-sə-peɪt̚ | ɪn ˈLO-kəl ˈGʌ-vərn-mənt̚ ↘\n"
            "===SOUNDS===\nparticipate: [pɑrtɪsɪˈpeɪt] -> [pɑrˈtɪsəpeɪt̚] (2nd-syllable stress)\n"
            "===STRESS===\nCrush unstressed vowels in 'participate' & 'governance' to /ə/.\n"
            "===RHYTHM===\nLink 'participate_in' smoothly with an unreleased Stop-T.\n"
            "===PEAK===\nˈGʌ-vərn-mənt̚ ↘\n"
            "===GRAMMAR===\nNONE\n"
            "===RECAST===\nNONE\n"
            "===VERDICT===\nCleaner schwa reduction on Take 2!\n"
        )

    async def generate_text(self, *args, **kwargs):
        return "Sounds good."

    async def generate_structured(self, user_id, sys_p, usr_p, schema_cls, fallback, **kw):
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
    def __init__(self, text="", voice=None, audio=None, mid=10):
        self.text, self.caption = text, None
        self.voice, self.audio = voice, audio
        self.reply_to_message, self.message_id = None, mid
        self.texts, self.voices, self.edits = [], [], []

    async def reply_text(self, t, **kw):
        child = FakeMsg(text=t, mid=100 + len(self.texts))
        self.texts.append((t, kw, child))
        return child

    async def edit_text(self, t, **kw):
        self.text = t
        self.edits.append((t, kw))
        return self

    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(text="", mid=200 + len(self.voices))


@pytest.mark.asyncio
async def test_instant_ack_and_guaranteed_pronunciation_card(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod

    monkeypatch.setattr(
        h_mod, "synthesize_partner_voice",
        lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"PARTNER_MP3"),
    )
    monkeypatch.setattr(
        h_mod, "synthesize_shadow_drill",
        lambda t: asyncio.sleep(0, result=b"SHADOW_MP3"),
    )
    monkeypatch.setattr(
        h_mod, "synthesize_audio_mirror",
        lambda lb, nt: asyncio.sleep(0, result=b"MIRROR_MP3"),
    )
    monkeypatch.setattr(
        e_mod, "analyze_voice_bytes",
        lambda b, wc=0: AcousticMetrics(
            total_s=2.5, voiced_s=2.1, syllables=11, npvi=52.0,
            pitch_span_st=4.9, boundary_tone="falling ↘", prosody_score=74,
        ),
    )

    llm = StubFastV61LLM()
    c = TwinContainer(db_path=tmp_path / "v61.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=202)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(
        download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS_CLIP"))
    )
    vo = SimpleNamespace(
        duration=2.5, file_size=4000, mime_type="audio/ogg",
        get_file=lambda: asyncio.sleep(0, result=dl),
    )

    # 1. Send voice message -> Instant status ack + edited in-place into full Pronunciation Card!
    mv = FakeMsg(voice=vo)
    await app.cb("voice_handler")(
        SimpleNamespace(effective_user=u, effective_chat=u, message=mv), ctx
    )
    assert llm.fast_calls == 1
    assert len(mv.texts) == 1
    status_bubble = mv.texts[0][2]
    assert len(status_bubble.edits) == 1
    edited_card, edited_kw = status_bubble.edits[0]
    assert "US Pronunciation &amp; Prosody Lab" in edited_card
    assert "Sounds &amp; Words:" in edited_card
    assert "Stress &amp; Schwa:" in edited_card
    assert "Overall 75/100" in edited_card
    btns = [b.text for row in edited_kw["reply_markup"].inline_keyboard for b in row]
    assert "🪞 Audio Mirror" in btns and "🎯 Shadow 3x" in btns
    assert len(mv.voices) == 1  # Partner voice delivered right after!

    # 2. Even if LLM throws an exception, physical DSP pronunciation report STILL delivers!
    async def broken_mm(*a, **kw):
        raise RuntimeError("API glitch")
    llm.generate_fast_multimodal = broken_mm
    mv_fb = FakeMsg(audio=vo)  # Also verifies filters.AUDIO support!
    await app.cb("voice_handler")(
        SimpleNamespace(effective_user=u, effective_chat=u, message=mv_fb), ctx
    )
    fb_card = mv_fb.texts[0][2].edits[0][0]
    assert "US Pronunciation &amp; Prosody Lab" in fb_card and "nPVI 52" in fb_card
