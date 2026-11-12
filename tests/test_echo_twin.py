import asyncio, json
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import TwinContainer, register_handlers
from src.config import sanitize_gemini_model
from src.db.repo import TwinRepository
from src.llm.client import GeminiClient
from src.voice.acoustics import AcousticMetrics, MonologueArc


class StubV70LLM:
    configured = True
    def __init__(self):
        self.fast_calls = 0

    async def generate_fast_multimodal(
        self, user_id, system_prompt, audio_bytes, user_prompt,
        *, mime_type="audio/ogg", temperature=0.25
    ):
        self.fast_calls += 1
        if "===US20S===" in system_prompt:
            return (
                "===HEARD===\nDigital platforms fail when cities ignore follow-through.\n"
                "===SCORE===\n84\n"
                "===DISCOURSE===\nStrong point—use 'What it comes down to is...' to stage your thesis.\n"
                "===STAMINA===\nSteady pace early, slight pause spike in closing third.\n"
                "===SOUNDS===\nfollow-through: stretch ˈFɑ-loʊ and keep /θ/ voiceless.\n"
                "===US20S===\nTech portals look great on paper, That said, what it comes down to is whether city hall actually funds what residents vote for.\n"
                "===CHUNK===\nwhat it comes down to is\n"
            )
        if "===LANDED===" in system_prompt:
            return (
                "===LANDED===\nYES\n"
                "===SCORE===\n88\n"
                "===FEEDBACK===\nClean pitch peak on PA-per with a clear Fall-Rise hook signaling reservation.\n"
            )
        if "===CALIBRATION===" in system_prompt or "===RECAST===" in system_prompt and "Move:" in user_prompt:
            return (
                "===HEARD===\nI get the rush, though a one-week pilot saves us headaches.\n"
                "===SCORE===\n90\n"
                "===NOTE===\nWarm concession before pivoting to the pilot.\n"
                "===RECAST===\nI see the urgency—my one worry is launching blind, so what if we run a five-day pilot first?\n"
                "===FRAME===\nmy one worry is\n"
                "===PROSODY===\nCalm pace with diplomatic falling-rising hedge.\n"
            )
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
            "===VERDICT===\nNONE\n"
        )

    async def generate_text(self, user_id, sys_p, usr_p, **kw):
        if "===USED===" in sys_p:
            return (
                "===HEARD===\nIt really comes down to how cities fund it.\n"
                "===USED===\nYES\n"
                "===FEEDBACK===\nNatural spoken clause slot after 'comes down to'.\n"
                "===MODEL===\nIt really comes down to whether cities back it up with real money.\n"
                "===PROSODY===\nNONE\n"
            )
        if "===SENTENCE===" in sys_p:
            return (
                "===SENTENCE===\nWe ended up holding off till local officials figured it out.\n"
                "===MAP===\nwi ˈEND-əd‿ʌp ˈHOLD-ɪŋ‿ˈɔF\n"
                "===FOCUS===\nLinks Flap-T + 2nd-syllable stress on ə-FFI-cials.\n"
            )
        return "Makes total sense."

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

    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"P_MP3"))
    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"S_MP3"))
    monkeypatch.setattr(
        e_mod, "analyze_voice_bytes",
        lambda b, wc=0: AcousticMetrics(
            total_s=2.5, voiced_s=2.1, syllables=11, npvi=52.0,
            pitch_span_st=4.9, boundary_tone="falling ↘", prosody_score=74,
        ),
    )

    llm = StubV70LLM()
    c = TwinContainer(db_path=tmp_path / "v70.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=202)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS_CLIP")))
    vo = SimpleNamespace(duration=2.5, file_size=4000, mime_type="audio/ogg", get_file=lambda: asyncio.sleep(0, result=dl))

    mv = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv), ctx)
    assert llm.fast_calls == 1
    edited_card, _ = mv.texts[0][2].edits[0]
    assert "US Pronunciation &amp; Prosody Lab" in edited_card
    assert "Overall 75/100" in edited_card
    assert len(mv.voices) == 1


@pytest.mark.asyncio
async def test_five_linguistic_pillars_end_to_end(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    from src.twin import engine as e_mod

    monkeypatch.setattr(h_mod, "synthesize_ear_clip", lambda t, angle="casual_sharp", slow=False: asyncio.sleep(0, result=b"EAR_MP3"))
    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda t, angle="casual_sharp": asyncio.sleep(0, result=b"DISTILL_MP3"))
    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"CURE_MP3"))
    monkeypatch.setattr(
        e_mod, "analyze_voice_bytes",
        lambda b, wc=0: AcousticMetrics(total_s=2.2, voiced_s=1.9, syllables=8, npvi=58.0, pitch_span_st=5.6, stress_contrast_db=5.2, boundary_tone="fall-rise ↘↗", prosody_score=84),
    )
    stub_arc = MonologueArc(
        overall=AcousticMetrics(total_s=48.0, voiced_s=39.0, syllables=110, npvi=54.0, prosody_score=80),
        open_third=AcousticMetrics(voiced_s=13.0, npvi=59.0, pause_ratio=0.12, prosody_score=86),
        mid_third=AcousticMetrics(voiced_s=13.0, npvi=55.0, pause_ratio=0.16, prosody_score=81),
        close_third=AcousticMetrics(voiced_s=13.0, npvi=44.0, pause_ratio=0.28, boundary_tone="falling ↘", prosody_score=71),
        stamina_flag="final_third_rhythm_decay; late_hesitation_spike",
    )
    monkeypatch.setattr(e_mod, "analyze_monologue_bytes", lambda b, wc=0: stub_arc)

    llm = StubV70LLM()
    c = TwinContainer(db_path=tmp_path / "pillars.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=303)
    ctx = SimpleNamespace(args=[], bot=None)
    dl = SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS_CLIP")))
    vo = SimpleNamespace(duration=48.0, file_size=9000, mime_type="audio/ogg", get_file=lambda: asyncio.sleep(0, result=dl))

    async def tap_studio(act: str) -> FakeMsg:
        qm = FakeMsg()
        q = SimpleNamespace(data=f"studio:{act}", message=qm, answer=lambda *a, **k: asyncio.sleep(0))
        await app.cb("studio_cb")(SimpleNamespace(effective_user=u, callback_query=q), ctx)
        return qm

    # Pillar 1: Perceptual Ear Decoder (blind audio -> text decode with contraction normalization)
    m_ear = await tap_studio("ear")
    assert len(m_ear.voices) == 1
    payload = json.loads(c.repo.get_studio_state(303)["mode_payload"])
    m_ans = FakeMsg(text=payload["full"])
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m_ans), ctx)
    assert "Ear Decoder — 100% Match" in m_ans.texts[0][0]
    assert c.repo.get_studio_state(303)["active_mode"] == ""

    # Pillar 2: Generative Lexical Frame + Vault Active Recall Advancement
    c.repo.save_vault_phrase(303, "comes down to", "It comes down to trust.")
    await tap_studio("frame")
    m_fr = FakeMsg(text="It really comes down to how cities fund it.")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=m_fr), ctx)
    assert "Spoken Frame Check" in m_fr.texts[0][0]
    assert "Vault Stage Up!" in m_fr.texts[0][0]

    # Pillar 3: Interactional Pragmatics Lab (voice response)
    await tap_studio("prag")
    mv_pr = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv_pr), ctx)
    assert "Pragmatic Calibration — 90/100" in mv_pr.texts[0][2].edits[0][0]

    # Pillar 4: Contrastive Intonation & Subtext (voice contour evaluation)
    await tap_studio("into")
    mv_in = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv_in), ctx)
    assert "Subtext Landed!" in mv_in.texts[0][2].edits[0][0]

    # Pillar 5: 60s Extended Discourse & 3-Thirds Stamina + 20s Spoken Distillation
    await tap_studio("mono")
    mv_mo = FakeMsg(voice=vo)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=u, message=mv_mo), ctx)
    mono_card = mv_mo.texts[0][2].edits[0][0]
    assert "3-Thirds Waveform Stamina:" in mono_card
    assert "20-Second Native Distillation:" in mono_card
    assert len(mv_mo.voices) == 1  # Spoken 20s distillation delivered!


@pytest.mark.asyncio
async def test_gemini_38_routing_and_real_container_init(tmp_path: Path, monkeypatch):
    from telegram.ext import Application
    assert sanitize_gemini_model("gemini-2.5-flash") == "gemini-3.8-flash"
    monkeypatch.setenv("GEMINI_API_KEYS", "AIzaFake1,AIzaFake2")
    repo = TwinRepository(db_path=tmp_path / "r.db", passphrase="")
    cli = GeminiClient(repo)
    if not cli.clients:
        cli.clients = [object()]
    c = TwinContainer(db_path=tmp_path / "full.db", passphrase="", llm=cli)
    app = Application.builder().token("123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw").concurrent_updates(True).build()
    register_handlers(app, c)
    c.flush()


@pytest.mark.asyncio
async def test_all_studio_buttons_habit_cure_and_expired_callback(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod

    monkeypatch.setattr(h_mod, "synthesize_ear_clip", lambda t, angle="casual_sharp", slow=False: asyncio.sleep(0, result=b"EAR_MP3"))
    monkeypatch.setattr(h_mod, "synthesize_shadow_drill", lambda t: asyncio.sleep(0, result=b"CURE_MP3"))

    llm = StubV70LLM()
    c = TwinContainer(db_path=tmp_path / "cure.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=404)
    ctx = SimpleNamespace(args=[], bot=None)

    # Seed real recurring habits in DB so build_remedial_drill exercises top_habits dict keys!
    c.repo.record_habit_hit(404, "word_stress", "participate", "stress 2nd syllable")
    c.repo.record_habit_hit(404, "us_rhythm_linking", "hold off on", "link C-V smoothly")

    # Simulate an EXPIRED Telegram callback query (raises RuntimeError/BadRequest on q.answer)
    async def expired_answer(*a, **k):
        raise RuntimeError("BadRequest: Query is too old and response timeout expired")

    for act in ("remedial", "ear", "ear_slow", "ear_reveal", "frame", "prag", "into", "mono", "spark", "pulse", "exit"):
        qm = FakeMsg()
        q = SimpleNamespace(data=f"studio:{act}", message=qm, answer=expired_answer)
        await app.cb("studio_cb")(SimpleNamespace(effective_user=u, callback_query=q), ctx)
        assert len(qm.texts) + len(qm.voices) >= 1, f"Button studio:{act} produced no output!"
