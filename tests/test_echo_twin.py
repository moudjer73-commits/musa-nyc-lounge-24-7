import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import BTN_SHIFT, TwinContainer, register_handlers
from src.twin.calques import scan_transfers
from src.twin.corpus_guard import has_contrived_buzz, scrub_contrived_english
from src.twin.engine import GhostTwinAnalysis, RegisterBreakdown, ShiftTurnOutput


class StubCorpusLLM:
    configured = True
    def __init__(self):
        self.voice_calls = 0
        self.structured_calls = 0
        self.last_sys_prompt = ""

    async def generate_voice_turn(self, user_id, system_prompt, audio_bytes, user_prompt, *, mime_type="audio/ogg", temperature=0.62):
        self.voice_calls += 1
        assert temperature <= 0.65
        return (
            "===HEARD===\n"
            "I returned back home and buyed many fruit\n"
            "===REPLY===\n"
            "Nice, nothing beats having fresh fruit around. What did you end up getting?\n"
            "===COACH===\n"
            "HABIT: uncountable_noun\n"
            "FIX: returned back -> headed back · buyed -> bought · many fruit -> a bunch of fruit\n"
            "RHYTHM: Flap the T in 'bought_a' and reduce 'of' to 'uh' in 'bunch_of'\n"
            "US: I headed back home and picked up a bunch of fruit.\n"
            "CHUNK: picked up a bunch of\n"
            "WHY: Plain phrasal verb + uncountable\n"
            "TAKE2: NONE"
        )

    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.62, deep_chat=True):
        self.last_sys_prompt = system_prompt
        if "Existing memory:" in user_prompt:
            return "• Doctoral researcher in local participatory governance\n• Focuses on authentic spoken US English"
        latest_msg = user_prompt.split("LATEST SPEAKER MESSAGE:")[-1]
        if "many advice" in latest_msg:
            return (
                "Yeah, solid advice is rare.\n"
                "===COACH===\n"
                "HABIT: uncountable_noun\n"
                "FIX: many advice -> some solid advice\n"
                "US: He gave me some solid advice.\n"
                "CHUNK: some solid advice\n"
                "WHY: Uncountable noun\n"
                "TAKE2: NONE"
            )
        # Simulate an accidental stiff word leak ('utilize', 'prior to') to verify post-scrubber cleans it
        if "leak test" in latest_msg:
            return "We usually utilize that right prior to the meeting."
        return "Makes total sense—how did that turn out?"

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False, temperature=0.25):
        self.structured_calls += 1
        assert deep_chat is True and temperature <= 0.25
        if schema_cls is RegisterBreakdown:
            # First call simulates buzzword hallucination -> triggers automatic anti-buzzword retry!
            if "CRITICAL: Zero corporate" not in system_prompt and "fruit" not in user_prompt:
                return RegisterBreakdown(
                    is_register_neutral=False,
                    casual_us="Deadass that won't work.",
                    work_us="Let's circle back to move the needle.",
                    seminar_us="One might posit it fails vis-à-vis budgets.",
                    pragmatic_note="Tone shifts.",
                )
            if "fruit" in user_prompt:
                return RegisterBreakdown(
                    is_register_neutral=True,
                    casual_us="I just picked up some fruit.",
                    work_us="I just picked up some fruit.",
                    seminar_us="I just picked up some fruit.",
                    pragmatic_note="Everyday concrete actions stay plain across all settings in the US.",
                )
            return RegisterBreakdown(
                is_register_neutral=False,
                casual_us="Honestly, I don't think that'll hold up.",
                work_us="My main worry is whether that holds up on our timeline.",
                seminar_us="I'm not convinced that holds up once you factor in local budgets.",
                pragmatic_note="Core verb 'hold up' stays plain; only the hedging shifts.",
            )
        if schema_cls is ShiftTurnOutput:
            return ShiftTurnOutput(
                reframed_user_point="I'm not sure this timeline is realistic given our current setup.",
                same_across_registers=False,
                partner_reply="Fair point—what's the biggest holdup right now?",
                key_chunk="not sure this is realistic",
            )
        return GhostTwinAnalysis(
            should_intervene=True,
            habit_key="stiff_register",
            fix_summary="utilize -> use",
            native_take="We just use what we already have.",
            key_chunk="use what we have",
            why_tag="Plain spoken verb",
        )


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
    def __init__(self, text="", voice=None, reply_to=None, mid=10):
        self.text, self.caption, self.voice, self.reply_to_message, self.message_id = text, None, voice, reply_to, mid
        self.texts, self.voices = [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(text="", mid=200 + len(self.voices))


def test_corpus_guard_scrubs_thesaurus_and_detects_fake_buzzwords():
    raw = "Prior to commencing, we utilize data vis-à-vis with regard to budgets."
    cleaned = scrub_contrived_english(raw)
    assert cleaned == "Before starting, we use data compared to when it comes to budgets."
    assert has_contrived_buzz("Let's circle back to move the needle")
    assert has_contrived_buzz("Deadass no cap")
    assert has_contrived_buzz("Picked up produce at the store")
    assert not has_contrived_buzz("My main worry is whether that holds up once budgets get tight.")
    assert len(scan_transfers("I returned back home since three years")) >= 2


@pytest.mark.asyncio
async def test_registers_never_invent_fake_synonyms_and_retry_on_buzzwords(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda text, angle="casual_sharp": asyncio.sleep(0, result=b"MP3_P"))
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", lambda text: asyncio.sleep(0, result=b"MP3_T"))

    llm = StubCorpusLLM()
    c = TwinContainer(db_path=tmp_path / "v32.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=33)
    ch = SimpleNamespace(id=33)
    ctx = SimpleNamespace(args=[], bot=None)

    # 1. Everyday concrete statement ('fruit') -> Register-neutral honesty (NO 'picked up produce'!)
    neutral_out = await c.engine.explain_registers(33, "I just picked up some fruit.")
    assert "Real US usage:" in neutral_out
    assert "produce" not in neutral_out.lower()

    # 2. Substantive pushback -> Buzzword attempt gets caught & retried cleanly into authentic spoken registers
    op_out = await c.engine.explain_registers(33, "This plan will fail.")
    assert "circle back" not in op_out.lower() and "deadass" not in op_out.lower() and "vis-à-vis" not in op_out.lower()
    assert "holds up" in op_out

    # 3. Partner stiff-word leak gets auto-scrubbed in dialogue
    m_leak = FakeMsg(text="run leak test now")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=m_leak), ctx)
    assert "use that right before" in m_leak.texts[0][0]

    # 4. ⚡ Shift Angle produces grounded partner response + authentic reframed user card with audio/save buttons
    m_sh = FakeMsg(text=BTN_SHIFT)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=m_sh), ctx)
    assert len(m_sh.texts) == 2
    assert "Claire" in m_sh.texts[0][0] and "holdup" in m_sh.texts[0][0]
    assert "How you'd say your point here" in m_sh.texts[1][0]
    assert "realistic" in m_sh.texts[1][0]

    # 5. Voice note single-pass + Habit Radar still work seamlessly
    v_obj = SimpleNamespace(
        duration=5.0, file_size=4000,
        get_file=lambda: asyncio.sleep(0, result=SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))),
    )
    mv = FakeMsg(voice=v_obj)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=mv), ctx)
    assert llm.voice_calls == 1 and "Flap the T" in mv.voices[1]["caption"]
