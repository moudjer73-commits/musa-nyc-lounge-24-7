"""Integration fixes: Gemini failover, never-silent handlers, voice output, podcast audio, privacy confirmation."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.bot.app import LabContainer, register_all_modules
from src.llm.gemini_client import GeminiService, LLMUnavailableError, QuotaExceededError


class FakeApp:
    def __init__(self):
        self.handlers = {}
        self.error_handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)

    def add_error_handler(self, cb):
        self.error_handlers.append(cb)

    def find(self, name):
        for hs in self.handlers.values():
            for h in hs:
                if getattr(h.callback, "__name__", "") == name:
                    return h.callback
        raise AssertionError(f"handler {name} not registered")


class FakeMsg:
    def __init__(self):
        self.texts, self.voices, self.audios = [], [], []

    async def reply_text(self, text, **kw):
        self.texts.append(text)

    async def reply_voice(self, voice, **kw):
        self.voices.append(voice)

    async def reply_audio(self, audio, **kw):
        self.audios.append(kw)


class FakeBot:
    async def send_chat_action(self, *a, **k):
        pass


def make_update(msg, uid=77, text="Hello there"):
    msg.text = text
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid), effective_chat=SimpleNamespace(id=uid), message=msg,
        effective_message=msg, callback_query=None,
    )


CTX = SimpleNamespace(bot=FakeBot(), args=[])


class _Resp:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        return self.behaviour(model)


def _svc(tmp_path, behaviours):
    from src.db.profile_repo import ProfileRepository

    svc = GeminiService(ProfileRepository(tmp_path / "g.db", tmp_path / "s.enc", ""), api_key="k0")
    svc.clients = [SimpleNamespace(models=_Models(b)) for b in behaviours]
    return svc


async def test_gemini_rotates_keys_on_rate_limit(tmp_path):
    def limited(model):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota")

    svc = _svc(tmp_path, [limited, lambda m: _Resp(" hi ")])
    assert await svc.generate_text(1, "sys", "prompt") == "hi"


async def test_gemini_skips_retired_model_and_reports_total_failure(tmp_path):
    def gone(model):
        raise RuntimeError("404 NOT_FOUND model is not found")

    svc = _svc(tmp_path, [gone])
    with pytest.raises(LLMUnavailableError):
        await svc.generate_text(1, "sys", "prompt")
    calls = svc.clients[0].models.calls
    assert len(calls) == len(set(calls))  # a retired model is tried once, never retried


async def test_gemini_without_key_is_a_clear_error(tmp_path):
    svc = _svc(tmp_path, [])
    with pytest.raises(LLMUnavailableError):
        await svc.generate_text(1, "sys", "prompt")


class BrokenLLM:
    def __init__(self, exc):
        self.exc = exc

    async def generate_text(self, *a, **k):
        raise self.exc

    async def generate_structured(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize("exc,expected", [
    (LLMUnavailableError("x"), "try again"),
    (QuotaExceededError("Daily free quota reached (80 calls)."), "quota"),
    (ZeroDivisionError("boom"), "went wrong"),
])
async def test_text_handler_never_silent(tmp_path, exc, expected):
    container = LabContainer(db_path=tmp_path / "t.db", llm=BrokenLLM(exc))
    app = FakeApp()
    register_all_modules(app, container)
    msg = FakeMsg()
    await app.find("text_handler")(make_update(msg), CTX)
    assert msg.texts and expected in msg.texts[-1].lower()


def test_no_shadow_layer_and_aliases_registered(tmp_path):
    container = LabContainer(db_path=tmp_path / "r.db", llm=BrokenLLM(RuntimeError()))
    app = FakeApp()
    register_all_modules(app, container)
    assert -2 not in app.handlers
    cmds = {c for hs in app.handlers.values() for h in hs for c in getattr(h, "commands", ())}
    assert {"privacy", "delete_everything", "delete_my_data", "voice", "room", "switch", "session"} <= cmds
    assert app.error_handlers


async def test_delete_requires_confirmation(tmp_path):
    container = LabContainer(db_path=tmp_path / "d.db", llm=BrokenLLM(RuntimeError()))
    app = FakeApp()
    register_all_modules(app, container)
    container.profile_repo.get_or_create_profile(5)
    msg = FakeMsg()
    await app.find("delete_cmd")(make_update(msg, uid=5), CTX)
    assert "Are you sure" in msg.texts[-1]
    assert container.profile_repo.delete_user_data(5) is True  # still there until confirmed (now gone)


async def test_alex_voice_sent_only_when_tts_works(monkeypatch):
    from src.bot import voice_io

    async def ok(text, voice=voice_io.ALEX_VOICE, rate="+0%"):
        return b"ID3audio"

    async def down(text, voice=voice_io.ALEX_VOICE, rate="+0%"):
        return None

    msg = FakeMsg()
    monkeypatch.setattr(voice_io, "synthesize_mp3_bytes", ok)
    assert await voice_io.send_alex_voice(msg, "Hi") is True and msg.voices
    monkeypatch.setattr(voice_io, "synthesize_mp3_bytes", down)
    assert await voice_io.send_alex_voice(FakeMsg(), "Hi") is False


class _TTS:
    def __init__(self, suffix):
        self.suffix = suffix

    async def render_episode_audio(self, turns, output_path, voice_map, slow=False):
        out = output_path.with_suffix(self.suffix)
        out.write_bytes(b"audio")
        return out, "fake"


async def test_podcast_audio_real_speech_or_none(tmp_path: Path):
    from src.bot.podcast_handlers import ensure_episode_audio

    ep = SimpleNamespace(audio_path=str(tmp_path / "ep1.mp3"), episode_id="ep1",
                         script=SimpleNamespace(turns=[]), voice_settings={})
    good = SimpleNamespace(tts=_TTS(".mp3"))
    normal = await ensure_episode_audio(good, ep)
    slow = await ensure_episode_audio(good, ep, slow=True)
    assert normal.name == "ep1.mp3" and slow.name == "ep1_slow.mp3"
    bad_ep = SimpleNamespace(audio_path=str(tmp_path / "ep2.mp3"), episode_id="ep2",
                             script=SimpleNamespace(turns=[]), voice_settings={})
    assert await ensure_episode_audio(SimpleNamespace(tts=_TTS(".wav")), bad_ep) is None  # beep fallback never sent


async def test_gemini_four_keys_one_old_project_still_serves_legacy_model(tmp_path):
    def behaviour_new_user(model):
        raise RuntimeError("404 NOT_FOUND This model is no longer available to new users")

    def behaviour_old_project(model):
        if model.startswith("gemini-2.5"):
            return _Resp("works")
        raise RuntimeError("404 NOT_FOUND")

    svc = _svc(tmp_path, [behaviour_new_user] * 3 + [behaviour_old_project])
    assert await svc.generate_text(1, "sys", "prompt") == "works"
    # known-dead pairs are skipped on the next call instead of being retried
    before = sum(len(c.models.calls) for c in svc.clients)
    assert await svc.generate_text(1, "sys", "prompt") == "works"
    assert sum(len(c.models.calls) for c in svc.clients) - before == 1


def test_default_model_chain_starts_with_current_models(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL_CHAIN", raising=False)
    from src.config import gemini_model_chain

    chain = gemini_model_chain()
    assert chain[:2] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_profile_sync_sets_commands_first_and_survives_timeouts(monkeypatch):
    from src.bot import telegram_setup

    calls = []

    def fake_post(url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        calls.append(method)
        if method == "setMyName":
            raise TimeoutError("ReadTimeout")
        return SimpleNamespace(json=lambda: {"ok": True})

    monkeypatch.setattr(telegram_setup.requests, "post", fake_post)
    results = telegram_setup.sync_telegram_bot_profile("123:abc")
    assert calls.index("setMyCommands") < calls.index("setMyName")
    assert "setMyDescription" in calls and results["cmd_default_def"] is True


async def test_start_clears_chat_scope_commands_once(monkeypatch):
    from src.bot import telegram_setup

    deleted = []

    class Bot:
        async def delete_my_commands(self, scope=None, language_code=None):
            deleted.append(language_code)

    telegram_setup._CLEARED_CHATS.clear()
    await telegram_setup.clear_stale_chat_commands(Bot(), 42)
    first = len(deleted)
    await telegram_setup.clear_stale_chat_commands(Bot(), 42)
    assert first >= 1 and len(deleted) == first
