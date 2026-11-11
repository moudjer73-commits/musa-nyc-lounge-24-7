"""Focused regression tests for the Nov 2026 static audit fixes (P0-1 through P1-16)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest

from src.alex.responder import AlexResponder
from src.bot import background, telegram_setup, voice_io
from src.bot.analyst_handlers import handle_analyzed_conversation_turn
from src.bot.app import LabContainer, register_all_modules
from src.bot.podcast_handlers import AUDIO_UNAVAILABLE, handle_podcast_button_action
from src.db import engine
from src.db.engine import export_encrypted_snapshot, get_connection, restore_encrypted_snapshot, restore_failed
from src.db.podcast_repo import PodcastRepository
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository
from src.llm.gemini_client import AUDIO_TIMEOUT_S, GeminiService
from src.models.listening_podcast import PodcastScript, PodcastTurn
from src.naturalness.router import ROOM_TO_PROFILE
from src.podcast.pipeline import PodcastPipeline
from src.podcast import pipeline as pipeline_mod
from src.podcast.tts_engine import BaseTTSProvider, OfflineWaveFallbackTTSProvider, SwappableTTSManager


class _FailPrimaryTTS(BaseTTSProvider):
    name = 'fail_edge'
    async def synthesize_turns(self, turns, output_path: Path, voice_map, slow: bool = False) -> Path:
        raise ConnectionError('edge-tts unreachable')

ROOT = Path(__file__).resolve().parent.parent


def _find_duplicate_env_keys(yml_text: str) -> list[str]:
    dups: list[str] = []
    lines = yml_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "env:":
            env_indent = len(raw) - len(raw.lstrip(" "))
            seen: set[str] = set()
            i += 1
            while i < len(lines):
                sub = lines[i]
                s = sub.strip()
                if not s or s.startswith("#"):
                    i += 1
                    continue
                if len(sub) - len(sub.lstrip(" ")) <= env_indent:
                    break
                if ":" in s and not s.startswith("-"):
                    k = s.split(":", 1)[0].strip()
                    if k in seen:
                        dups.append(k)
                    seen.add(k)
                i += 1
            continue
        i += 1
    return dups


def test_p0_1_p0_2_and_p1_10_workflow_integrity():
    assert _find_duplicate_env_keys("        env:\n          A: 1\n          A: 2\n") == ["A"]
    wf = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
    assert _find_duplicate_env_keys(wf) == []
    assert "ref: main" in wf
    assert "::error::Bot exited after" in wf
    assert "pip install -U edge-tts" not in wf
    reqs = [ln.strip() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert reqs and all("==" in ln and ">=" not in ln for ln in reqs)


class _FakeApp:
    def __init__(self):
        self.handlers = {}
    def add_handler(self, h, group=0):
        self.handlers.setdefault(group, []).append(h)
    def add_error_handler(self, cb):
        pass
    def find_cb(self, name):
        for hs in self.handlers.values():
            for h in hs:
                if getattr(h.callback, "__name__", "") == name:
                    return h.callback
        raise AssertionError(name)


class _FakeMsg:
    def __init__(self, text="", voice=None):
        self.text, self.voice, self.texts, self.markups, self.audios = text, voice, [], [], []
    async def reply_text(self, t, **kw):
        self.texts.append(t)
        self.markups.append(kw.get("reply_markup"))
    async def reply_audio(self, audio, **kw):
        self.audios.append(kw)


@pytest.mark.asyncio
async def test_p0_3_and_p1_14_ui_podcast_callback_and_unknown_action(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_mod, 'DATA_DIR', tmp_path)
    from src.bot import podcast_handlers

    class StubLLM:
        configured = False
        async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback_instance):
            return fallback_instance
        async def generate_text(self, *a, **k):
            return "ok"

    c = LabContainer(db_path=tmp_path / "pod_ui.db", llm=StubLLM())
    c.podcast_pipeline.tts = SwappableTTSManager(
        primary=_FailPrimaryTTS(), fallback=OfflineWaveFallbackTTSProvider()
    )
    app = _FakeApp()
    register_all_modules(app, c)
    ui_cb = app.find_cb("handle_callback")

    msg1 = _FakeMsg()
    q1 = SimpleNamespace(data="ui:menu:podcast", message=msg1, answer=lambda: asyncio.sleep(0))
    upd1 = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=7), callback_query=q1, message=msg1)
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=lambda *a, **k: asyncio.sleep(0)), args=[])
    await ui_cb(upd1, ctx)
    assert any("Generating" in t for t in msg1.texts) and any(AUDIO_UNAVAILABLE in t for t in msg1.texts)

    mp3 = tmp_path / "real.mp3"
    mp3.write_bytes(b"ID3DATA")
    async def _fake_ensure(pipeline, ep, slow=False):
        return mp3
    monkeypatch.setattr(podcast_handlers, "ensure_episode_audio", _fake_ensure)
    msg2 = _FakeMsg()
    q2 = SimpleNamespace(data="ui:menu:podcast", message=msg2, answer=lambda: asyncio.sleep(0))
    upd2 = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=7), callback_query=q2, message=msg2)
    await ui_cb(upd2, ctx)
    assert len(msg2.audios) == 1

    ep = await c.podcast_pipeline.get_or_create_episode(7, "professional", "disagree", "L2", ["a", "b"])
    txt_unknown, st = handle_podcast_button_action(c.podcast_repo, ep, 7, "unknown_action")
    assert txt_unknown == "" and st.voice_reply_submitted is False and st.first_listen_done is False


@pytest.mark.asyncio
async def test_p0_4_podcast_fallback_not_cached_refunds_budget_and_stable_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_mod, 'DATA_DIR', tmp_path)
    repo = PodcastRepository(db_path=tmp_path / "p4.db")
    good_script = PodcastScript(
        register_label="professional",
        focus_function="disagree",
        target_chunks=["I see where you're coming from, but", "Sorry to jump in here, but"],
        turns=[
            PodcastTurn(speaker="Host_A", text="We're gonna launch Monday."),
            PodcastTurn(speaker="Host_B", text="Right, I see where you're coming from, but QA needs a day."),
            PodcastTurn(speaker="Host_A", text="Wait, cancel it?"),
            PodcastTurn(speaker="Host_B", text="Sorry to jump in here, but what I meant was Wednesday."),
        ],
    )
    state = {"down": True}
    class ToggleLLM:
        async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback_instance):
            return fallback_instance if state["down"] else good_script

    tts = SwappableTTSManager(primary=_FailPrimaryTTS(), fallback=OfflineWaveFallbackTTSProvider())
    pipe = PodcastPipeline(repo=repo, llm=ToggleLLM(), tts=tts)
    chunks = ["I see where you're coming from, but", "Sorry to jump in here, but"]

    ep_fb = await pipe.get_or_create_episode(21, "professional", "disagree", "L2", chunks, personal=True)
    assert repo.get_cached_episode(ep_fb.cache_key) is None
    assert re.fullmatch(r"[0-9a-f]{12}", ep_fb.episode_id)

    state["down"] = False
    ep_ok = await pipe.get_or_create_episode(21, "professional", "disagree", "L2", chunks, personal=True)
    assert ep_ok.episode_id == ep_fb.episode_id and repo.get_cached_episode(ep_ok.cache_key) is not None


def test_p0_5_purge_preserves_today_quotas_and_atomic_concurrency(tmp_path: Path):
    db = tmp_path / "q.db"
    c = LabContainer(db_path=db)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    with get_connection(db) as conn:
        conn.execute("INSERT INTO daily_usage (user_id, usage_date, llm_calls) VALUES (55, ?, 40)", (yesterday,))
        conn.execute("INSERT INTO personal_podcast_budget (user_id, budget_date, episodes_generated) VALUES (55, ?, 1)", (yesterday,))
        conn.commit()
    c.profile_repo.get_or_create_profile(55)
    assert c.profile_repo.check_and_increment_quota(55, daily_limit=80) == (True, 1)
    assert c.podcast_repo.check_and_consume_personal_budget(55, max_per_day=1) is True

    assert c.purge_all_user_data(55) is True
    assert c.profile_repo.check_and_increment_quota(55, daily_limit=80) == (True, 2)
    assert c.podcast_repo.check_and_consume_personal_budget(55, max_per_day=1) is False

    repo = ProfileRepository(db_path=tmp_path / "race.db")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: repo.check_and_increment_quota(99, daily_limit=80)[0], range(100)))
    assert sum(1 for ok in results if ok) == 80


@pytest.mark.asyncio
async def test_p0_6_and_p1_9_wal_concurrent_exports_v2_and_legacy_crypto(tmp_path: Path, caplog):
    db = tmp_path / "wal.db"
    with get_connection(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.execute("INSERT INTO nat_heard (user_id, phrase, context_note, ts) VALUES (1, 'v2_phrase', '', 1.0)")
        conn.commit()

    snap = tmp_path / "state.enc"
    pw = "strong-passphrase-with-24-chars"
    with ThreadPoolExecutor(max_workers=8) as pool:
        outs = list(pool.map(lambda _: export_encrypted_snapshot(db, snap, pw, force=True), range(12)))
    assert all(o == snap for o in outs) and snap.read_bytes().startswith(b"SELSNAP2")
    assert list(tmp_path.glob("*.tmp")) == []

    target_db = tmp_path / "restored.db"
    Path(f"{target_db}-wal").write_bytes(b"stale")
    Path(f"{target_db}-shm").write_bytes(b"stale")
    assert restore_encrypted_snapshot(snap, target_db, pw) is True
    assert not Path(f"{target_db}-wal").exists() and not Path(f"{target_db}-shm").exists()

    v1_db = tmp_path / "v1_src.db"
    c1 = sqlite3.connect(v1_db)
    c1.execute("CREATE TABLE t (x TEXT);")
    c1.execute("INSERT INTO t VALUES ('legacy_ok');")
    c1.commit()
    c1.close()
    salt_v1 = b"0123456789abcdef"
    fkey_v1 = engine._derive_fernet_key("shortpw", salt_v1, 120_000)
    v1_snap = tmp_path / "legacy.enc"
    v1_snap.write_bytes(salt_v1 + Fernet(fkey_v1).encrypt(v1_db.read_bytes()))
    v1_out = tmp_path / "v1_out.db"
    with caplog.at_level("WARNING"):
        assert restore_encrypted_snapshot(v1_snap, v1_out, "shortpw") is True
    assert "shorter than 16" in caplog.text
    assert sqlite3.connect(v1_out).execute("SELECT x FROM t").fetchone()[0] == "legacy_ok"

    bad_v2 = tmp_path / "trunc.enc"
    bad_v2.write_bytes(b"SELSNAP2short")
    assert restore_encrypted_snapshot(bad_v2, tmp_path / "o.db", pw) is False and restore_failed(bad_v2)

    app_stub = SimpleNamespace(bot=SimpleNamespace())
    cont_stub = SimpleNamespace(passphrase=pw, db_path=db, snapshot_path=snap, chunk_repo=SimpleNamespace(get_due_reminder_users=lambda: []))
    tasks = background.start_background_tasks(app_stub, cont_stub)
    assert len(tasks) >= 2
    await background.stop_background_tasks(tasks)


def test_p0_7_routing_profiles_cast_and_no_marcus():
    profiles = json.loads((ROOT / "data/naturalness/profiles.json").read_text(encoding="utf-8"))
    cast = json.loads((ROOT / "data/naturalness/cast.json").read_text(encoding="utf-8"))
    assert ROOM_TO_PROFILE["service_counter"] == "service_stranger"
    assert ROOM_TO_PROFILE["phone_call"] == "work_peer"
    for room, pid in ROOM_TO_PROFILE.items():
        assert pid in profiles, f"{room} -> {pid}"
    for pid, pdata in profiles.items():
        if not pid.startswith("_"):
            assert pdata["default_cast"] in cast
    assert "Marcus" not in (ROOT / "src/bot/ui_buttons.py").read_text(encoding="utf-8")
    assert "Marcus" not in (ROOT / "src/bot/telegram_setup.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_p1_8_single_key_cooldown_aio_and_audio_timeout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CHAIN", "m_overloaded,m_healthy")
    calls: list[str] = []
    class AioModels:
        async def generate_content(self, model, contents, config=None):
            calls.append(model)
            if model == "m_overloaded":
                raise RuntimeError("503 UNAVAILABLE overloaded")
            return SimpleNamespace(text="aio_ok")

    svc = GeminiService(ProfileRepository(tmp_path / "g8.db"), api_key="k1")
    svc.clients = [SimpleNamespace(aio=SimpleNamespace(models=AioModels()))]
    assert await svc.generate_text(1, "sys", "hi") == "aio_ok"
    assert await svc.generate_text(1, "sys", "hi again") == "aio_ok"
    assert calls == ["m_overloaded", "m_healthy", "m_healthy"]

    seen_timeouts: list[float] = []
    async def _spy_invoke(client_obj, kwargs, timeout_s):
        seen_timeouts.append(timeout_s)
        return SimpleNamespace(text="verbatim transcript")
    monkeypatch.setattr(svc, "_invoke_generate", _spy_invoke)
    assert await svc.transcribe_voice(1, b"OggS", "audio/ogg") == "verbatim transcript"
    assert seen_timeouts[-1] == AUDIO_TIMEOUT_S and seen_timeouts[-1] >= 60.0


def test_p1_11_telegram_sync_diff_only_429_stop_and_token_redaction(monkeypatch, caplog):
    set_calls: list[str] = []
    def matching_post(url, json=None, timeout=None):
        m = url.rsplit("/", 1)[1]
        if m.startswith("set"):
            set_calls.append(m)
        if m == "getMyCommands":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": telegram_setup.COMMANDS})
        if m == "getChatMenuButton":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"type": "commands"}})
        if m == "getMyName":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"name": telegram_setup.BOT_NAME}})
        if m == "getMyShortDescription":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"short_description": telegram_setup.SHORT_DESCRIPTION}})
        if m == "getMyDescription":
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"description": telegram_setup.FULL_DESCRIPTION}})
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr(telegram_setup.requests, "post", matching_post)
    res = telegram_setup.sync_telegram_bot_profile("111:SECRET_ABC")
    assert set_calls == [] and res["cmd_default_def"] is True

    calls_429 = []
    monkeypatch.setattr(
        telegram_setup.requests, "post",
        lambda url, json=None, timeout=None: (calls_429.append(url), SimpleNamespace(status_code=429, json=lambda: {"ok": False, "error_code": 429}))[1]
    )
    telegram_setup.sync_telegram_bot_profile("111:SECRET_ABC")
    assert len(calls_429) == 1

    def exploding_post(url, json=None, timeout=None):
        raise RuntimeError(f"HTTPSConnectionPool error for {url}")
    monkeypatch.setattr(telegram_setup.requests, "post", exploding_post)
    with caplog.at_level("WARNING"):
        telegram_setup.sync_telegram_bot_profile("111:SUPER_SECRET_TOKEN_999")
    assert "SUPER_SECRET_TOKEN_999" not in caplog.text


@pytest.mark.asyncio
async def test_p1_12_reply_with_segments_locked(tmp_path: Path):
    db = tmp_path / "seg.db"
    class SegLLM:
        configured = True
        def __init__(self):
            self.n = 0
        async def generate_text(self, user_id, system_prompt, user_prompt, temperature=0.7):
            self.n += 1
            idx = self.n
            await asyncio.sleep(0.02)
            return f"first_{idx} --- second_{idx}"

    resp = AlexResponder(SceneRepository(db_path=db), SegLLM())
    out1, out2 = await asyncio.gather(
        handle_analyzed_conversation_turn(9, "s", "hi", ProfileRepository(db), SceneRepository(db), None, resp),
        handle_analyzed_conversation_turn(9, "s", "yo", ProfileRepository(db), SceneRepository(db), None, resp),
    )
    assert {tuple(out1), tuple(out2)} == {("first_1", "second_1"), ("first_2", "second_2")}


@pytest.mark.asyncio
async def test_p1_13_validation_clamping_splitting_and_voice_limits(tmp_path: Path):
    class StubLLM:
        configured = True
        async def generate_text(self, *a, **k):
            return "A" * 4500
        async def transcribe_voice(self, *a, **k):
            raise AssertionError("should not transcribe oversized voice")

    c = LabContainer(db_path=tmp_path / "v13.db", llm=StubLLM())
    app = _FakeApp()
    register_all_modules(app, c)

    m_sw = _FakeMsg()
    await app.find_cb("switch_cmd")(SimpleNamespace(effective_user=SimpleNamespace(id=3), message=m_sw), SimpleNamespace(args=["xyz"]))
    assert "Allowed settings" in m_sw.texts[-1]

    m_rm = _FakeMsg()
    await app.find_cb("room_cmd")(SimpleNamespace(effective_user=SimpleNamespace(id=3), message=m_rm), SimpleNamespace(args=["not_a_room"]))
    assert "Unknown room" in m_rm.texts[-1]

    m_rem = _FakeMsg()
    await app.find_cb("remind_cmd")(SimpleNamespace(effective_user=SimpleNamespace(id=3), message=m_rem), SimpleNamespace(args=["99"]))
    assert "23:00 UTC" in m_rem.texts[-1]

    m_re = _FakeMsg()
    await app.find_cb("reentry_cmd")(SimpleNamespace(effective_user=SimpleNamespace(id=3), message=m_re), SimpleNamespace(args=["999"]))
    assert "365 days away" in m_re.texts[-1]

    m_long = _FakeMsg()
    long_txt = ("Sentence one is here. " * 220) + "\n\n" + ("Second paragraph sentence. " * 100)
    await telegram_setup.safe_reply_text(m_long, long_txt, reply_markup="FINAL_KB")
    assert len(m_long.texts) >= 2 and all(len(x) <= 4000 for x in m_long.texts)
    assert all(mk is None for mk in m_long.markups[:-1]) and m_long.markups[-1] == "FINAL_KB"

    bad_voice = SimpleNamespace(duration=150, file_size=1000, get_file=lambda: (_ for _ in ()).throw(AssertionError("downloaded!")))
    m_v = _FakeMsg(voice=bad_voice)
    upd_v = SimpleNamespace(effective_user=SimpleNamespace(id=44), effective_chat=SimpleNamespace(id=44), message=m_v, effective_message=m_v)
    await app.find_cb("voice_handler")(upd_v, SimpleNamespace(bot=SimpleNamespace(send_chat_action=lambda *a, **k: asyncio.sleep(0)), args=[]))
    assert "too long or large" in m_v.texts[-1] and c.profile_repo.check_and_increment_quota(44) == (True, 1)


@pytest.mark.asyncio
async def test_p1_15_and_p1_16_complete_purge_and_single_tts_function(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_mod, 'DATA_DIR', tmp_path)
    db = tmp_path / "p15.db"
    class StubLLM:
        configured = True
        async def generate_text(self, *a, **k):
            return "sounds good."
        async def generate_structured(self, user_id, system_prompt, user_prompt, schema_cls, fallback_instance):
            return PodcastScript(
                register_label="casual", focus_function="decline",
                target_chunks=["I'm gonna have to pass on", "take a rain check"],
                turns=[
                    PodcastTurn(speaker="Host_A", text="Wanna hang tonight?"),
                    PodcastTurn(speaker="Host_B", text="Right, I'm gonna have to pass on tonight."),
                    PodcastTurn(speaker="Host_A", text="Are you mad at me?"),
                    PodcastTurn(speaker="Host_B", text="What I meant was let's take a rain check tomorrow."),
                ],
            )

    c = LabContainer(db_path=db, snapshot_path=tmp_path / "s.enc", passphrase="strong-passphrase-16ch", llm=StubLLM())
    c.podcast_pipeline.tts = SwappableTTSManager(primary=_FailPrimaryTTS(), fallback=OfflineWaveFallbackTTSProvider())
    await c.responder.reply(66, "hey there")
    c.responder.nat_gauge.record_heard(66, "rain check")
    ep = await c.podcast_pipeline.get_or_create_episode(
        66, "casual", "decline", "L2", ["I'm gonna have to pass on", "take a rain check"], personal=True
    )
    assert Path(ep.audio_path).exists()

    c.purge_all_user_data(66)
    assert not Path(ep.audio_path).exists() and c.podcast_repo.get_episode_by_id(ep.episode_id) is None
    assert 66 not in c.responder._user_turn_counts and 66 not in c.responder.last_segments
    assert all(e.user_id != 66 for e in c.responder.nat_gauge.logs)
    assert all(h.get("user_id") != 66 for h in c.responder.nat_gauge.heard_notebook)

    assert voice_io.TTS_TIMEOUT_S == 20
    assert await voice_io.synthesize_mp3_bytes("") is None
    assert await voice_io.synthesize_mp3_bytes("😂😂😂") is None
    assert await voice_io.synthesize_mp3_bytes("...") is None
    assert "Alex" in voice_io.clean_for_tts("")
