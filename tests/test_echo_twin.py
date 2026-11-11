import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import TwinContainer, register_handlers
from src.twin.engine import GhostTwinAnalysis


class StubV3LLM:
    configured = True
    def __init__(self):
        self.voice_calls = 0
        self.transcribe_calls = 0
        self.last_sys_prompt = ""

    async def generate_voice_turn(self, user_id, system_prompt, audio_bytes, user_prompt, *, mime_type="audio/ogg", temperature=0.82):
        self.voice_calls += 1
        self.last_sys_prompt = system_prompt
        return (
            "===HEARD===\n"
            "I buyed many fruit today before going to work\n"
            "===REPLY===\n"
            "Nice move grabbing fresh fruit before work! What did you get?\n"
            "===COACH===\n"
            "HABIT: uncountable_noun\n"
            "FIX: buyed -> bought · many fruit -> a bunch of fruit\n"
            "RHYTHM: Link 'picked_up_a' smoothly and reduce 'to' to 'tuh' before 'work'\n"
            "US: I picked up a bunch of fruit today before heading to work.\n"
            "CHUNK: picked up a bunch of\n"
            "WHY: Uncountable + connected speech\n"
            "TAKE2: NONE"
        )

    async def transcribe_voice(self, *a, **k):
        self.transcribe_calls += 1
        raise AssertionError("voice_handler must use single-pass generate_voice_turn, not separate transcribe_voice!")

    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.82, deep_chat=True):
        self.last_sys_prompt = system_prompt
        if "Existing memory:" in user_prompt:
            return "• Doctoral researcher studying local participatory governance\n• Practices spoken US English daily"
        latest_msg = user_prompt.split("LATEST SPEAKER MESSAGE:")[-1]
        if "many advice" in latest_msg or "I buyed many fruit now" in latest_msg:
            return (
                "Got it—good advice is hard to come by.\n"
                "===COACH===\n"
                "HABIT: uncountable_noun\n"
                "FIX: many advice -> some solid advice (uncountable)\n"
                "US: He gave me some solid advice.\n"
                "CHUNK: some solid advice\n"
                "WHY: Uncountable noun\n"
                "TAKE2: NONE"
            )
        return "Totally makes sense—how did that play out?"

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False):
        return GhostTwinAnalysis(
            should_intervene=True,
            habit_key="uncountable_noun",
            fix_summary="many fruit -> a bunch of fruit",
            native_take="I picked up a bunch of fruit.",
            key_chunk="a bunch of fruit",
            why_tag="Uncountable",
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
    def __init__(self, text="", voice=None, mid=10):
        self.text, self.caption, self.voice, self.reply_to_message, self.message_id = text, None, voice, None, mid
        self.texts, self.voices, self.reactions = [], [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(text="", mid=200 + len(self.voices))


@pytest.mark.asyncio
async def test_v3_single_pass_voice_ear_rhythm_and_habit_radar(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    monkeypatch.setattr(h_mod, "synthesize_partner_voice", lambda text, angle="casual_sharp": asyncio.sleep(0, result=b"MP3_P"))
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", lambda text: asyncio.sleep(0, result=b"MP3_T"))

    llm = StubV3LLM()
    c = TwinContainer(db_path=tmp_path / "v3.db", passphrase="", llm=llm)
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=21)
    ch = SimpleNamespace(id=21)
    ctx = SimpleNamespace(args=[], bot=None)

    # 1. Voice note uses SINGLE multimodal call (0 transcribe_voice calls), logs WPM, and delivers spoken Rhythm tip!
    v_obj = SimpleNamespace(
        duration=6.0, file_size=5000,
        get_file=lambda: asyncio.sleep(0, result=SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))),
    )
    mv = FakeMsg(voice=v_obj)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=mv), ctx)
    assert llm.voice_calls == 1 and llm.transcribe_calls == 0
    assert len(mv.voices) == 2
    coach_cap = mv.voices[1]["caption"]
    assert "🎧" in coach_cap and "picked_up_a" in coach_cap
    assert c.repo.weekly_pulse(21)["peak_wpm"] > 0

    # 2. Second hit on same habit ('uncountable_noun') triggers 🔁 Habit #2 badge!
    mt = FakeMsg(text="He gave me many advice")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=mt), ctx)
    coach_txt = mt.texts[1][0]
    assert "🔁" in coach_txt and "Habit #2: Uncountable nouns" in coach_txt

    # 3. Clean substantive turn increments clean_streak on Habit Radar
    mc = FakeMsg(text="That actually worked out way better than expected.")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=mc), ctx)
    assert len(mc.texts) == 1
    assert c.repo.top_habits(21)[0]["clean_streak"] == 1
    assert all(t["role"] in ("learner", "partner") for t in c.repo.recent_turns(21))


@pytest.mark.asyncio
async def test_v3_partner_memory_syncs_and_survives_fresh_chat(tmp_path: Path):
    llm = StubV3LLM()
    c = TwinContainer(db_path=tmp_path / "mem.db", passphrase="", llm=llm)
    for i in range(6):
        await c.engine.respond_turn(9, f"I am writing chapter {i+1} on local participatory governance.")
    assert await c.engine.maybe_sync_memory(9) is True
    mem_txt, synced_at = c.repo.get_memory(9)
    assert "participatory governance" in mem_txt and synced_at == 6

    # Fresh chat clears messages but preserves Partner Memory for next conversation!
    c.repo.clear_conversation(9)
    assert c.repo.recent_turns(9) == []
    await c.engine.respond_turn(9, "What should I focus on next week?")
    assert "participatory governance" in llm.last_sys_prompt

    # Full purge wipes memory too
    c.repo.purge_user(9)
    assert c.repo.get_memory(9) == ("", 0)
