import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.bot.handlers import BTN_COACH, BTN_PARTNER, BTN_SHIFT, BTN_SPARK, TwinContainer, register_handlers
from src.twin.calques import is_repair_request, scan_transfers
from src.twin.engine import EchoTwinEngine, GhostTwinAnalysis, parse_reply_and_coach


class StubLLM:
    configured = True
    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.82, deep_chat=True):
        if "PRAGMATIC REPAIR" in system_prompt:
            return "Oh my bad—I just meant your message came through twice! How's your day going?"
        if "I buyed many fruit now" in user_prompt:
            assert "SILENT COACH CHECK" in system_prompt
            assert "buyed -> bought" in system_prompt
            return (
                "Nice, fresh fruit is the best. What did you end up grabbing?\n"
                "===COACH===\n"
                "FIX: buyed -> bought · many fruit -> a bunch of fruit (uncountable) · now -> just\n"
                "US: I just picked up a bunch of fruit.\n"
                "CHUNK: pick up a bunch of\n"
                "WHY: Irregular verb + uncountable\n"
                "TAKE2: NONE"
            )
        if "3 spoken registers" in user_prompt:
            return "• Everyday (Malik): Picked up some fruit.\n• Work / Exec (Claire): Grabbed fresh fruit.\n• Seminar (Prof. Vance): Picked up produce.\n• Pragmatic nuance: Casual phrasal verb."
        return "Love that—rare midweek win. Anything fun lined up?"

    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False):
        return GhostTwinAnalysis(
            should_intervene=True,
            fix_summary="buyed -> bought · many fruit -> a bunch of fruit",
            native_take="I just picked up a bunch of fruit.",
            key_chunk="pick up a bunch of",
            why_tag="Grammar & US chunk",
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
    def __init__(self, text="", mid=10):
        self.text, self.caption, self.voice, self.reply_to_message, self.message_id = text, None, None, None, mid
        self.texts = []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))


@pytest.mark.asyncio
async def test_short_5_word_mistake_gets_corrected_immediately(tmp_path: Path):
    # 1. Pre-scanner catches both 'buyed' and 'many fruit'
    hits = scan_transfers("I buyed many fruit now")
    assert len(hits) >= 2

    c = TwinContainer(db_path=tmp_path / "fix.db", passphrase="", llm=StubLLM())
    app = FakeApp()
    register_handlers(app, c)
    u = SimpleNamespace(id=5)
    ch = SimpleNamespace(id=5)
    ctx = SimpleNamespace(args=[], bot=None)

    # 2. Clean greeting "Great thank you" -> Partner reply ONLY, zero petty comma correction
    m1 = FakeMsg(text="Great thank you")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=m1), ctx)
    assert len(m1.texts) == 1

    # 3. 5-word mistake "I buyed many fruit now" -> Natural Partner reply + Instant 🎯 US Coach card with exact FIX & US take!
    m2 = FakeMsg(text="I buyed many fruit now")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u, effective_chat=ch, message=m2), ctx)
    assert len(m2.texts) == 2
    partner_bubble = m2.texts[0][0]
    coach_bubble = m2.texts[1][0]
    assert "===COACH===" not in partner_bubble
    assert "US Coach" in coach_bubble
    assert "buyed -&gt; bought" in coach_bubble
    assert "I just picked up a bunch of fruit." in coach_bubble


@pytest.mark.asyncio
async def test_repair_and_parser(tmp_path: Path):
    segs, coach = parse_reply_and_coach("Sounds good!\n===COACH===\nNONE")
    assert segs == ["Sounds good!"] and coach is None
    assert is_repair_request("Didn't get you")
