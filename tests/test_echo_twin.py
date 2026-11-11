import asyncio, sqlite3
from pathlib import Path
from types import SimpleNamespace
from cryptography.fernet import Fernet
import pytest

from src.bot.handlers import BTN_COACH, BTN_PARTNER, BTN_SHIFT, BTN_SPARK, TwinContainer, register_handlers
from src.bot.setup_tg import COMMANDS, refresh_chat_menu_once
from src.db import engine
from src.db.engine import export_encrypted_snapshot, restore_encrypted_snapshot
from src.db.repo import TwinRepository
from src.twin.calques import detect_code_switch, is_repair_request, scan_transfers
from src.twin.engine import EchoTwinEngine, GhostTwinAnalysis

ROOT = Path(__file__).resolve().parent.parent


class StubLLM:
    configured = True
    def __init__(self):
        self.calls = []
        self.ghost_reply = GhostTwinAnalysis(
            should_intervene=True,
            native_take="Honestly, that looks great on paper, but it falls apart once local budgets dry up.",
            key_chunk="looks great on paper",
            why_tag="French calque -> US idiom",
            trigger_take2=False,
            take2_prompt="",
        )
    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.86, deep_chat=True):
        self.calls.append((system_prompt, user_prompt))
        if "PRAGMATIC REPAIR" in system_prompt:
            return "Oh my bad—I just meant you sent hey a couple times so I thought your connection glitched! How's your day going?"
        if "3 spoken registers" in user_prompt:
            return "• Everyday (Malik): Looks good on paper.\n• Work / Exec (Claire): Strong on paper, risky in execution.\n• Seminar (Prof. Vance): Compelling theoretically, fragile empirically.\n• Pragmatic nuance: Signals realistic skepticism."
        if "L1 BRIDGE" in system_prompt:
            return "Yeah, [participatory budgeting] sounds great until municipal funds dry up."
        return "Hey! Good to hear from you—what's on your mind today?"
    async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback, *, deep_chat=False):
        return self.ghost_reply
    async def transcribe_voice(self, user_id, audio_bytes, mime_type="audio/ogg"):
        return "At the level of local municipalities let me complete my idea"


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
    def __init__(self, text="", caption=None, voice=None, reply_to=None, mid=10):
        self.text, self.caption, self.voice, self.reply_to_message, self.message_id = text, caption, voice, reply_to, mid
        self.texts, self.voices, self.reactions, self.edits, self.markup_edits = [], [], [], [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return FakeMsg(text=t, mid=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return FakeMsg(caption=kw.get("caption", ""), voice=True, mid=200 + len(self.voices))
    async def set_reaction(self, emoji):
        self.reactions.append(emoji)


def test_snapshot_migration_and_menu_commands(tmp_path: Path):
    assert [c["command"] for c in COMMANDS] == ["start", "partner", "spark", "vault", "reset"]
    v1_src = tmp_path / "v1.db"
    conn = sqlite3.connect(v1_src)
    conn.execute("CREATE TABLE daily_usage (user_id INTEGER, usage_date TEXT, llm_calls INTEGER, PRIMARY KEY(user_id, usage_date))")
    conn.commit()
    conn.close()
    salt = b"0123456789abcdef"
    v1_snap = tmp_path / "v1.enc"
    v1_snap.write_bytes(salt + Fernet(engine._derive_key("pw-sixteen-chars!", salt, 120_000)).encrypt(v1_src.read_bytes()))
    live_db = tmp_path / "live.db"
    assert restore_encrypted_snapshot(v1_snap, live_db, "pw-sixteen-chars!") is True
    v2_out = export_encrypted_snapshot(live_db, tmp_path / "v2.enc", "pw-sixteen-chars!", force=True)
    assert v2_out.read_bytes().startswith(b"SELSNAP2")


@pytest.mark.asyncio
async def test_repair_detector_softens_malik_immediately(tmp_path: Path):
    assert is_repair_request("Didn't get you")
    assert is_repair_request("Wait, what do you mean?")
    assert detect_code_switch("How do we say التشاركية المحلية?")
    assert len(scan_transfers("At the level of the city, let me complete my idea.")) >= 2

    repo = TwinRepository(db_path=tmp_path / "r.db", passphrase="")
    llm = StubLLM()
    eng = EchoTwinEngine(repo, llm)
    out = await eng.respond_turn(1, "Didn't get you")
    assert out.was_repair is True
    assert "my bad" in out.full_reply.lower()
    # Ghost Twin never badgers the user on a repair request
    assert await eng.analyze_ghost_twin(1, "Didn't get you") is None


@pytest.mark.asyncio
async def test_studio_2x2_buttons_inline_cards_and_menu_purge(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    async def _fake_p_tts(text, angle="casual_sharp"):
        return b"ID3_PARTNER"
    async def _fake_t_tts(text):
        return b"ID3_TWIN"
    monkeypatch.setattr(h_mod, "synthesize_partner_voice", _fake_p_tts)
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", _fake_t_tts)

    deleted_langs = []
    class FakeBot:
        async def delete_my_commands(self, scope=None, language_code=None):
            deleted_langs.append(language_code)
        async def set_my_commands(self, commands=None, scope=None):
            self.cmds = commands

    bot = FakeBot()
    await refresh_chat_menu_once(bot, 777, force=True)
    assert set(deleted_langs) == {None, "en", "ar", "fr"}

    c = TwinContainer(db_path=tmp_path / "ui.db", passphrase="", llm=StubLLM())
    app = FakeApp()
    register_handlers(app, c)
    ctx = SimpleNamespace(args=[], bot=bot)
    u_obj = SimpleNamespace(id=12)
    ch_obj = SimpleNamespace(id=12)

    # 1. /start sends 2x2 persistent Studio Keyboard
    m_start = FakeMsg(text="/start")
    await app.cb("start_cmd")(SimpleNamespace(effective_user=u_obj, effective_chat=ch_obj, message=m_start), ctx)
    kb = m_start.texts[-1][1]["reply_markup"]
    labels = [[b.text for b in row] for row in kb.keyboard]
    assert labels == [[BTN_PARTNER, BTN_SHIFT], [BTN_SPARK, BTN_COACH]]

    # 2. Tap 🎭 Partner -> Inline selector -> Switch to Prof. Vance
    m_part = FakeMsg(text=BTN_PARTNER)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u_obj, effective_chat=ch_obj, message=m_part), ctx)
    assert "Malik" in m_part.texts[-1][0]

    q_msg = FakeMsg(text="Choose partner")
    edits = []
    q_part = SimpleNamespace(
        data="partner:seminar_skeptic",
        message=q_msg,
        answer=lambda *a, **k: asyncio.sleep(0),
        edit_message_text=lambda t, **kw: (edits.append(t), asyncio.sleep(0))[1],
    )
    await app.cb("partner_cb")(SimpleNamespace(effective_user=u_obj, callback_query=q_part), ctx)
    assert c.repo.get_user(12)["angle"] == "seminar_skeptic" and any("Prof. Vance" in e for e in edits)

    # 3. Tap 🎲 Spark Topic -> Drops a seminar prompt
    m_spark = FakeMsg(text=BTN_SPARK)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u_obj, effective_chat=ch_obj, message=m_spark), ctx)
    assert "Prof. Vance" in m_spark.texts[-1][0]

    # 4. Send message + Tap ✨ Coach Last -> Inline Coach Card with Save / 3 Registers / Hear US
    m_talk = FakeMsg(text="At the level of local governance we should make a discussion.")
    await app.cb("text_handler")(SimpleNamespace(effective_user=u_obj, effective_chat=ch_obj, message=m_talk), ctx)
    m_coach = FakeMsg(text=BTN_COACH)
    await app.cb("text_handler")(SimpleNamespace(effective_user=u_obj, effective_chat=ch_obj, message=m_coach), ctx)
    card_txt, card_kw = m_coach.texts[-1]
    assert "US Upgrade" in card_txt
    save_cb_data = card_kw["reply_markup"].inline_keyboard[0][0].callback_data

    # 5. Tap 📌 Save Phrase on Coach Card -> Saves chunk into Vault
    q_save = SimpleNamespace(
        data=save_cb_data,
        message=FakeMsg(text=card_txt),
        answer=lambda *a, **k: asyncio.sleep(0),
        edit_message_reply_markup=lambda **kw: asyncio.sleep(0),
    )
    await app.cb("coach_cb")(SimpleNamespace(effective_user=u_obj, callback_query=q_save), ctx)
    assert any("looks great on paper" in x["phrase"] for x in c.repo.all_vault_phrases(12))

    # 6. Fresh chat reset keeps saved Vault phrases intact
    q_rst = SimpleNamespace(
        data="reset:chat",
        message=FakeMsg(text="reset"),
        answer=lambda *a, **k: asyncio.sleep(0),
        edit_message_text=lambda t, **kw: (edits.append(t), asyncio.sleep(0))[1],
    )
    await app.cb("reset_cb")(SimpleNamespace(effective_user=u_obj, callback_query=q_rst), ctx)
    assert c.repo.recent_turns(12) == []
    assert len(c.repo.all_vault_phrases(12)) == 1


def test_telegram_profile_limits_and_lang_cleanup(monkeypatch):
    from src.bot import setup_tg
    assert 1 <= len(setup_tg.BOT_NAME) <= 64
    assert 1 <= len(setup_tg.SHORT_DESCRIPTION) <= 120
    assert 1 <= len(setup_tg.FULL_DESCRIPTION) <= 512

    calls = []
    def fake_post(url, json=None, timeout=15):
        m = url.rsplit("/", 1)[1]
        calls.append((m, dict(json or {})))
        if m == "getMyName":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"name": "Old v1" if json.get("language_code") == "en" else ""}})
        if m == "getMyShortDescription":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"short_description": "Old bio" if json.get("language_code") == "ar" else ""}})
        if m == "getMyDescription":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"description": "Old Marcus desc" if json.get("language_code") == "en" else ""}})
        if m == "getMyCommands":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": []})
        if m == "getChatMenuButton":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"type": "default"}})
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr(setup_tg.requests, "post", fake_post)
    setup_tg.sync_telegram_bot_profile("123:ABC")
    assert ("setMyName", {"name": "", "language_code": "en"}) in calls
    assert ("setMyShortDescription", {"short_description": "", "language_code": "ar"}) in calls
    assert ("setMyDescription", {"description": "", "language_code": "en"}) in calls
    assert ("setMyDescription", {"description": setup_tg.FULL_DESCRIPTION}) in calls
