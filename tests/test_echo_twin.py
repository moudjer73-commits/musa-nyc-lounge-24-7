import asyncio, subprocess, tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import BTN_STUDIOS, TwinContainer, register_handlers
from src.twin.engine import HybridVoiceTurnOutput, ProsodyAuditOutput
from src.voice.acoustics import AcousticMetrics
from src.voice.tts import PARTNER_VOICES, TWIN_VOICE, VOICE_CLIP_CACHE, merge_mirror_clips


class StubBatch1LLM:
    configured = True
    def __init__(self):
        self.legacy_voice_calls = 0
        self.audit_calls = 0

    async def audit_voice_prosody(
        self, user_id, system_prompt, audio_bytes, user_prompt,
        schema_cls, fallback, *, mime_type="audio/ogg"
    ):
        self.audit_calls += 1
        if schema_cls is HybridVoiceTurnOutput:
            return HybridVoiceTurnOutput(
                verbatim_heard="Let's hold off on this till Monday.",
                partner_reply="Makes sense—gives us time to double-check the numbers.",
                phonetic_score=74,
                has_findings=True,
                habit_key="us_rhythm_linking",
                prosodic_map="lɛts ˈHOLD‿ˈOFF ɑn ðɪs | tɪl ˈMʌN-deɪ ↘",
                word_pronunciations="hold off: link /d/ smoothly into /ɔf/ without a pause.",
                stress_and_schwa="Reduce 'till' -> /tɪl/ and stretch OFF & MON-day.",
                prosody_fix="Connect 'hold_off_on' inside one breath group.",
                focus_stress="ˈMʌN-deɪ ↘",
                shadow_chunk="hold off on this till Monday",
                native_recast="",
                grammar_fix="",
            )
        return ProsodyAuditOutput(
            verbatim_heard="Let's hold off on this till Monday.",
            phonetic_score=89,
            has_findings=False,
            prosodic_map="lɛts ˈHOLD‿ˈOFF ɑn ðɪs | tɪl ˈMʌN-deɪ ↘",
            word_pronunciations="Crisp dark /ɫ/ in 'hold' and clean linking!",
            stress_and_schwa="Natural schwa timing.",
            prosody_fix="Smooth single breath group.",
            retake_verdict="Much smoother linking on 'hold_off_on'!",
        )

    async def generate_voice_turn(self, *args, **kwargs):
        self.legacy_voice_calls += 1
        return "===HEARD===\ntest\n===REPLY===\nok\n===COACH===\nNONE"

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
    def __init__(self, text="", voice=None, mid=10):
        self.text, self.caption, self.voice = text, None, voice
        self.reply_to_message, self.message_id = None, mid
        self.texts, self.voices = [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(text="", mid=200 + len(self.voices))


@pytest.mark.asyncio
async def test_four_distinct_voices_and_real_ffmpeg_audio_mirror():
    all_v = {v[0] for v in PARTNER_VOICES.values()} | {TWIN_VOICE}
    assert len(all_v) == 4
    with tempfile.TemporaryDirectory() as td:
        u_ogg = Path(td) / "u.ogg"
        n_mp3 = Path(td) / "n.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=190:duration=0.4", str(u_ogg)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=260:duration=0.4", str(n_mp3)],
            check=True,
        )
        merged = await merge_mirror_clips(u_ogg.read_bytes(), n_mp3.read_bytes())
        assert merged and len(merged) > 1500


@pytest.mark.asyncio
async def test_single_call_hybrid_and_audio_mirror_button(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod

    monkeypatch.setattr(
        h_mod, "synthesize_partner_voice",
        lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"PARTNER_MP3"),
    )
    monkeypatch.setattr(
        h_mod, "synthesize_twin_voice",
        lambda t, rate="+0%": asyncio.sleep(0, result=b"TWIN_MP3"),
    )
    monkeypatch.setattr(
        h_mod, "synthesize_shadow_drill",
        lambda t: asyncio.sleep(0, result=b"SHADOW_MP3"),
    )
    monkeypatch.setattr(
        h_mod, "synthesize_audio_mirror",
        lambda lb, nt: asyncio.sleep(0, result=b"MIRROR_STITCHED_MP3"),
    )

    takes = [
        AcousticMetrics(total_s=2.0, voiced_s=1.7, syllables=7, npvi=36.0, pitch_span_st=3.5, prosody_score=58),
        AcousticMetrics(total_s=1.9, voiced_s=1.6, syllables=7, npvi=61.0, pitch_span_st=5.4, boundary_tone="falling ↘", prosody_score=85),
    ]
    monkeypatch.setattr(e_mod, "analyze_voice_bytes", lambda b, wc=0: takes.pop(0))

    llm = StubBatch1LLM()
    c = TwinContainer(db_path=tmp_path / "b1.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=101)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS_USER")))
    vo = SimpleNamespace(duration=2.0, file_size=3200, mime_type="audio/ogg", get_file=lambda: asyncio.sleep(0, result=dl))

    # 1. Hybrid Voice Turn -> EXACTLY 1 Gemini call (50% quota saved!) + Partner Voice + Lab Card
    mv1 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv1), ctx)
    assert llm.audit_calls == 1 and llm.legacy_voice_calls == 0
    assert len(mv1.voices) == 2

    # 2. Verify 🪞 Audio Mirror button is present & triggers stitched Your Voice -> Native US
    kb = mv1.voices[1]["reply_markup"].inline_keyboard
    mirror_btn = next(b for row in kb for b in row if "Audio Mirror" in b.text)
    q_msg = FakeMsg(text="card")
    q_msg.caption = mv1.voices[1]["caption"]
    q_mirror = SimpleNamespace(data=mirror_btn.callback_data, message=q_msg, answer=lambda *a, **k: asyncio.sleep(0))
    await app.cb("coach_cb")(SimpleNamespace(effective_user=u, callback_query=q_mirror), ctx)
    assert len(q_msg.voices) == 1
    assert "Your Voice ➔ Native US" in q_msg.voices[0]["caption"]
    assert c.repo.get_lab_state(101)["pending_retake"] == 1

    # 3. Studios Hub button opens cleanly
    m_st = FakeMsg(text=BTN_STUDIOS)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m_st), ctx)
    assert "Spoken US Training Studios" in m_st.texts[0][0]

    # 4. Next voice note scores A/B Retake automatically
    mv2 = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv2), ctx)
    assert "Before ➔ After:" in mv2.voices[0]["caption"]
