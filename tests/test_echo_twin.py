import asyncio, sqlite3
from pathlib import Path
from types import SimpleNamespace
from cryptography.fernet import Fernet
import pytest

from src.bot.handlers import TwinContainer, extract_stealable_chunk, register_handlers
from src.db import engine
from src.db.engine import export_encrypted_snapshot, restore_encrypted_snapshot
from src.db.repo import TwinRepository
from src.twin.calques import detect_code_switch, scan_transfers
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
            trigger_take2=True,
            take2_prompt="Give me that pushback again in one 8-word punch.",
        )
    async def generate_text(self, user_id, system_prompt, user_prompt, *, temperature=0.9, deep_chat=True):
        self.calls.append((system_prompt, user_prompt))
        if "US X-Ray" in user_prompt:
            return "• Subtext: Direct pushback\n• Close friend: Nah, won't work\n• Exec / Work: Let's stress-test the budget\n• Seminar: That mechanism struggles empirically"
        if "L1 BRIDGE" in system_prompt:
            return "Wait, you mean [it looks good on paper]—yeah, municipalities run into that constantly."
        return "Wait—did my signal glitch or did you just hey me twice?"
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
    def __init__(self, text="", voice=None, reply_to=None, mid=10):
        self.text, self.voice, self.reply_to_message, self.message_id = text, voice, reply_to, mid
        self.texts, self.voices, self.reactions = [], [], []
    async def reply_text(self, t, **kw):
        self.texts.append((t, kw))
        return SimpleNamespace(message_id=100 + len(self.texts))
    async def reply_voice(self, voice, **kw):
        self.voices.append(kw)
        return SimpleNamespace(message_id=200 + len(self.voices))
    async def set_reaction(self, emoji):
        self.reactions.append(emoji)


def test_workflow_and_snapshot_v1_to_v2_migration(tmp_path: Path):
    wf = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
    assert "python app.py" in wf and (ROOT / "app.py").exists()

    # Legacy v1 DB without twin_* tables restores cleanly and auto-creates Echo Twin schema
    v1_src = tmp_path / "v1.db"
    conn = sqlite3.connect(v1_src)
    conn.execute("CREATE TABLE daily_usage (user_id INTEGER, usage_date TEXT, llm_calls INTEGER, PRIMARY KEY(user_id, usage_date))")
    conn.execute("INSERT INTO daily_usage VALUES (7, '2026-11-11', 5)")
    conn.commit()
    conn.close()
    salt = b"0123456789abcdef"
    v1_snap = tmp_path / "v1.enc"
    v1_snap.write_bytes(salt + Fernet(engine._derive_key("pw-sixteen-chars!", salt, 120_000)).encrypt(v1_src.read_bytes()))

    restored_db = tmp_path / "live.db"
    assert restore_encrypted_snapshot(v1_snap, restored_db, "pw-sixteen-chars!") is True
    repo = TwinRepository(db_path=restored_db, snapshot_path=tmp_path / "v2.enc", passphrase="pw-sixteen-chars!")
    assert repo.get_user(7)["angle"] == "casual_sharp"
    v2_out = export_encrypted_snapshot(restored_db, tmp_path / "v2.enc", "pw-sixteen-chars!", force=True)
    assert v2_out.read_bytes().startswith(b"SELSNAP2")


def test_calques_and_chunk_extractor():
    assert detect_code_switch("Wait كيفاش نقولوها in a meeting?")
    assert detect_code_switch("It's c'est-à-dire a budget freeze.")
    hits = scan_transfers("At the level of local governance, let me complete my idea.")
    assert len(hits) >= 2
    assert extract_stealable_chunk("Yeah, [it looks good on paper] until budgets dry up.") == "it looks good on paper"


@pytest.mark.asyncio
async def test_engine_l1_bridge_shift_vault_and_ghost_twin(tmp_path: Path):
    repo = TwinRepository(db_path=tmp_path / "t.db", passphrase="")
    llm = StubLLM()
    eng = EchoTwinEngine(repo, llm)

    # L1 bridge + spontaneous reply
    out = await eng.respond_turn(1, "How do I say التشاركية المحلية here?")
    assert "[it looks good on paper]" in out.full_reply

    # Shift flips angle on exact last point
    new_angle, badge, shifted = await eng.shift_angle_live(1)
    assert new_angle == "seminar_skeptic" and "Seminar" in badge and shifted

    # Ghost Twin + Take 2 lifecycle
    ghost = await eng.analyze_ghost_twin(1, "At the level of the municipality we must make a discussion.", is_voice=True, duration_s=9.0)
    assert ghost is not None and ghost.key_chunk == "looks great on paper"
    assert repo.get_user(1)["pending_take2"] != ""

    # Vault save + organic activation triggers celebration + clears Take 2
    repo.save_vault_phrase(1, "looks great on paper")
    out2 = await eng.respond_turn(1, "Sure, that looks great on paper, but who funds it?")
    assert "looks great on paper" in out2.vault_celebrations
    assert out2.completed_take2 is True and repo.get_user(1)["pending_take2"] == ""


@pytest.mark.asyncio
async def test_telegram_zero_ui_voice_spoiler_swipe_and_reactions(tmp_path: Path, monkeypatch):
    from src.bot import handlers as h_mod
    async def _fake_partner_tts(text, angle="casual_sharp"):
        return b"ID3_PARTNER_MP3"
    async def _fake_twin_tts(text):
        return b"ID3_TWIN_MP3"
    monkeypatch.setattr(h_mod, "synthesize_partner_voice", _fake_partner_tts)
    monkeypatch.setattr(h_mod, "synthesize_twin_voice", _fake_twin_tts)

    c = TwinContainer(db_path=tmp_path / "bot.db", passphrase="", llm=StubLLM())
    app = FakeApp()
    register_handlers(app, c)
    ctx = SimpleNamespace(args=[])

    # 1. Voice note -> Voice reply with <tg-spoiler> + Ghost Twin Native Take + Take 2
    v_obj = SimpleNamespace(duration=8, file_size=4000, get_file=lambda: asyncio.sleep(0, result=SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(b"OggS")))))
    m_voice = FakeMsg(voice=v_obj, mid=50)
    await app.cb("voice_handler")(SimpleNamespace(effective_user=SimpleNamespace(id=9), message=m_voice), ctx)
    await asyncio.sleep(0.05)
    assert len(m_voice.voices) >= 2
    assert "<tg-spoiler>" in m_voice.voices[0]["caption"]
    assert "Native Take" in m_voice.voices[1]["caption"]

    # 2. Reaction 👍 on Ghost Twin bubble steals key_chunk into vault
    ghost_mid = 202
    mr = SimpleNamespace(user=SimpleNamespace(id=9), message_id=ghost_mid, new_reaction=[SimpleNamespace(emoji="👍")])
    await app.cb("reaction_handler")(SimpleNamespace(message_reaction=mr), ctx)
    vault_phrases = [x["phrase"] for x in c.repo.all_vault_phrases(9)]
    assert "looks great on paper" in vault_phrases

    # 3. Using stolen chunk organically triggers 🔥 reaction on user's message
    m_use = FakeMsg(text="That plan looks great on paper honestly.", mid=55)
    await app.cb("text_handler")(SimpleNamespace(effective_user=SimpleNamespace(id=9), message=m_use), ctx)
    assert "🔥" in m_use.reactions

    # 4. Swipe '?' triggers 4-line US X-Ray; '⚡ Shift' flips room
    m_q = FakeMsg(text="?", reply_to=SimpleNamespace(text="Let's stress-test that.", caption=None))
    await app.cb("text_handler")(SimpleNamespace(effective_user=SimpleNamespace(id=9), message=m_q), ctx)
    assert "Subtext:" in m_q.texts[-1][0]

    m_sh = FakeMsg(text="⚡ Shift")
    await app.cb("text_handler")(SimpleNamespace(effective_user=SimpleNamespace(id=9), message=m_sh), ctx)
    assert "Seminar" in m_sh.texts[-1][0]
