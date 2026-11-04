"""S5: app.py wiring, driven with a fake bot (no Telegram, no network)."""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

try:  # CI has the real packages; a bare checkout gets minimal stand-ins
    import telebot  # noqa: F401
    import edge_tts  # noqa: F401
except ImportError:  # pragma: no cover
    class _Markup:
        def __init__(self, row_width=3, **kw):
            self.row_width, self.keyboard = row_width, []

        def add(self, *buttons, **kw):
            for i in range(0, len(buttons), self.row_width):
                self.keyboard.append(list(buttons[i:i + self.row_width]))

        def row(self, *buttons):
            self.keyboard.append(list(buttons))

    class _Button:
        def __init__(self, text, callback_data=None, **kw):
            self.text, self.callback_data = text, callback_data

    _tb = types.ModuleType("telebot")
    _tb.TeleBot = lambda *a, **k: None
    _tb.apihelper = SimpleNamespace(SESSION_TIME_TO_LIVE=0)
    _tb.types = SimpleNamespace(ReplyKeyboardMarkup=_Markup, KeyboardButton=lambda t, **k: t, InlineKeyboardMarkup=_Markup, InlineKeyboardButton=_Button)
    sys.modules.update({"telebot": _tb, "telebot.types": _tb.types, "telebot.apihelper": _tb.apihelper})
    _et = types.ModuleType("edge_tts")
    _et.Communicate = object
    sys.modules["edge_tts"] = _et

import app  # noqa: E402
import interaction  # noqa: E402
from flows import V3Flows, script_hash  # noqa: E402
from helpers_s3 import attempt, exposure, expression, tiny_seed  # noqa: E402
from helpers_s5 import GREETING_1, GREETING_2, TEXT, DeferredExecutor, Harness, InlineExecutor, ScriptedAI, raw_diag  # noqa: E402
from orchestrator import TurnOrchestrator  # noqa: E402

LID, CHAT = 1001, 5
REPLY = "Ah, so you finally talked it through."


class AppAI(ScriptedAI):
    model_tier = "flash"

    def get_active_model_chain(self, prefer_pro=False):
        return ["gemini-test"]

    def set_model_tier(self, tier):
        self.model_tier = tier
        return ["gemini-test"]


class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.markup_edits, self.answers, self.voices = [], [], [], [], []
        self.reject_ids: set[str] = set()
        self._id = 100

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self._id += 1
        msg = SimpleNamespace(message_id=self._id, text=text, markup=reply_markup)
        self.sent.append(msg)
        return msg

    def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None, reply_markup=None):
        self.edits.append(SimpleNamespace(text=text, message_id=message_id, markup=reply_markup))

    def edit_message_reply_markup(self, chat_id=None, message_id=None, reply_markup=None):
        self.markup_edits.append(SimpleNamespace(message_id=message_id, markup=reply_markup))

    def answer_callback_query(self, call_id, text=None):
        self.answers.append((call_id, text))

    def send_chat_action(self, chat_id, action):
        pass

    def send_voice(self, chat_id, voice, **kw):
        if isinstance(voice, str) and voice in self.reject_ids:
            raise RuntimeError("wrong file identifier")
        self.voices.append(voice)
        return SimpleNamespace(voice=SimpleNamespace(file_id="F1", file_unique_id="U1", duration=2))


def buttons(markup):
    return [(b.text, b.callback_data) for row in (markup.keyboard if markup else []) for b in row]


def data(markup, prefix):
    return next(cd for _, cd in buttons(markup) if cd.startswith(prefix + ":"))


@pytest.fixture
def env(db, monkeypatch, tmp_path):
    ai = AppAI()
    orch = TurnOrchestrator(db, ai, executor=InlineExecutor())
    flows = V3Flows(db, ai)
    for name, value in (("db", db), ("ai", ai), ("orchestrator", orch), ("flows", flows)):
        monkeypatch.setattr(app, name, value)
    synth = SimpleNamespace(calls=0)

    def fake_synth(text):
        synth.calls += 1
        path = tmp_path / f"clip{synth.calls}.mp3"
        path.write_bytes(b"mp3")
        return str(path)

    monkeypatch.setattr(app, "synthesize_tts_mp3", fake_synth)
    h = Harness(db)
    h.ai, h.orch, h.flows = ai, orch, flows
    return SimpleNamespace(db=db, ai=ai, orch=orch, flows=flows, bot=FakeBot(), h=h, synth=synth)


def reach_card(env):
    env.db.update_learner_preference(LID, feedback_preference="active")
    for i, text in enumerate((GREETING_1, GREETING_2)):
        env.ai.analyses.append({"diagnostics": []})
        app.on_text(env.bot, CHAT, LID, text, 11 + i, background=False)
    env.ai.replies.append(REPLY)
    env.ai.analyses.append({"diagnostics": [raw_diag()]})
    app.on_text(env.bot, CHAT, LID, TEXT, 13, background=False)
    return env.bot.sent[-1], env.bot.markup_edits[-1]


# ------------------------------------------------------------------------------ scene turns


def test_a_scene_turn_replies_first_then_attaches_the_upgrade_button(env):
    msg, edit = reach_card(env)
    assert REPLY in msg.text and msg.markup is not None
    assert [cd.split(":")[0] for _, cd in buttons(msg.markup)] == ["ls"]  # only Listen when it was sent
    attached = buttons(edit.markup)
    assert [cd.split(":")[0] for _, cd in attached] == ["ug", "ls"] and edit.message_id == msg.message_id
    assert all(len(cd.encode()) <= 32 for _, cd in attached)
    assert [e["payload"]["trigger"] for e in env.h.events("feedback_card_delivered")] == ["end_of_turn"]


def test_the_background_analysis_also_delivers_the_button(env):
    env.db.update_learner_preference(LID, feedback_preference="active")
    for i, text in enumerate((GREETING_1, GREETING_2)):
        env.ai.analyses.append({"diagnostics": []})
        app.on_text(env.bot, CHAT, LID, text, 11 + i, background=False)
    env.ai.analyses.append({"diagnostics": [raw_diag()]})
    app.on_text(env.bot, CHAT, LID, TEXT, 13)  # background thread
    for _ in range(100):
        if env.bot.markup_edits:
            break
        time.sleep(0.02)
    assert env.bot.markup_edits and data(env.bot.markup_edits[-1].markup, "ug")


def test_a_duplicate_telegram_message_gets_one_reply(env):
    env.ai.analyses.append({"diagnostics": []})
    app.on_text(env.bot, CHAT, LID, GREETING_1, 11, background=False)
    app.on_text(env.bot, CHAT, LID, GREETING_1, 11, background=False)
    assert len(env.ai.briefs) == 1 and len([m for m in env.bot.sent if "Alex:" in m.text]) == 1


def test_a_responder_failure_sends_the_retry_notice_and_does_not_raise(env):
    def boom(**kw):
        raise RuntimeError("down")

    env.ai.run_responder_v3 = boom
    app.on_text(env.bot, CHAT, LID, TEXT, 11, background=False)
    assert "catching his breath" in env.bot.sent[-1].text


def test_a_late_analyst_is_recorded_as_a_deadline_skip(env, monkeypatch):
    monkeypatch.setattr(app, "ANALYSIS_WAIT_S", 0.01)
    orch = TurnOrchestrator(env.db, env.ai, executor=DeferredExecutor())
    monkeypatch.setattr(app, "orchestrator", orch)
    res = orch.process_turn(learner_id=LID, learner_text=TEXT, message_idempotency_key="late1")
    app.finish_turn(env.bot, CHAT, LID, res, None)
    assert env.h.events("analysis_skipped")[-1]["payload"]["reason"] == "deadline"
    assert env.bot.markup_edits == []


# ------------------------------------------------------------------------------ the card and reconstruction


def test_tapping_the_button_turns_the_message_into_the_card(env):
    msg, edit = reach_card(env)
    app.on_callback(env.bot, "cb1", LID, CHAT, msg.message_id, data(edit.markup, "ug"))
    assert "Quick Upgrade" in env.bot.edits[-1].text and env.bot.edits[-1].message_id == msg.message_id
    assert env.bot.answers[-1] == ("cb1", "Upgrade opened")
    assert {cd.split(":")[0] for _, cd in buttons(env.bot.edits[-1].markup)} == {"rc", "bk", "ls"}


def test_reconstruction_through_the_app_ends_as_a_new_scene_turn(env):
    msg, edit = reach_card(env)
    app.on_callback(env.bot, "cb1", LID, CHAT, msg.message_id, data(edit.markup, "ug"))
    app.on_callback(env.bot, "cb2", LID, CHAT, msg.message_id, data(env.bot.edits[-1].markup, "rc"))
    assert "Say it again" in env.bot.edits[-1].text
    before = len(env.ai.briefs)
    app.on_text(env.bot, CHAT, LID, "We discussed the plan yesterday.", 14, background=False)
    assert "That works" in env.bot.sent[-1].text and len(env.ai.briefs) == before  # graded, not sent to Alex
    env.ai.analyses.append({"diagnostics": []})
    app.on_callback(env.bot, "cb3", LID, CHAT, env.bot.sent[-1].message_id, data(env.bot.sent[-1].markup, "rs"), background=False)
    assert len(env.ai.briefs) == before + 1 and "Alex:" in env.bot.sent[-1].text
    assert env.h.events("learner_message_received")[-1]["payload"]["origin"] == "reconstruction"


def test_a_stale_button_only_says_it_expired(env):
    app.on_callback(env.bot, "cb", LID, CHAT, 1, "rc:999:1")
    assert env.bot.answers[-1] == ("cb", interaction.STALE_CALLBACK_TEXT) and env.bot.edits == []


def test_a_mode_button_closes_the_open_row(env):
    msg, edit = reach_card(env)
    app.on_callback(env.bot, "cb1", LID, CHAT, msg.message_id, data(edit.markup, "ug"))
    assert app.begin_command(env.bot, CHAT, LID) is True
    assert interaction.get_active_interaction(env.db, LID, env.db.clock.now()) is None


def test_listen_speaks_and_caches_the_clip(env):
    msg, edit = reach_card(env)
    ls = data(msg.markup, "ls")
    app.on_callback(env.bot, "cb1", LID, CHAT, msg.message_id, ls)
    assert env.synth.calls == 1 and len(env.bot.voices) == 1
    assert env.db.get_audio_file_id(script_hash(REPLY), app.DEFAULT_VOICE) == "F1"
    app.on_callback(env.bot, "cb2", LID, CHAT, msg.message_id, ls)
    assert env.synth.calls == 1 and env.bot.voices[-1] == "F1"  # second time: the stored file id, no synthesis


def test_a_cached_file_id_that_telegram_rejects_is_replaced(env):
    env.db.put_audio_file_id(script_hash("Hello there"), app.DEFAULT_VOICE, "OLD", None, None)
    env.bot.reject_ids.add("OLD")
    assert app.send_alex_voice(env.bot, CHAT, "Hello there") is True
    assert env.synth.calls == 1 and env.db.get_audio_file_id(script_hash("Hello there"), app.DEFAULT_VOICE) == "F1"


def test_auto_voice_speaks_each_reply_and_lives_in_the_database(env):
    app.on_callback(env.bot, "cb", LID, CHAT, 1, "setvoice:auto")
    assert env.db.get_auto_voice(LID) is True and "AUTO" in env.bot.answers[-1][1]
    env.ai.analyses.append({"diagnostics": []})
    app.on_text(env.bot, CHAT, LID, GREETING_1, 11, background=False)
    assert env.synth.calls == 1


# ------------------------------------------------------------------------------ review, progress, debrief


def test_the_review_command_runs_a_due_phrase_through_the_app(env):
    env.db.seed_catalog(tiny_seed(expression("come_to_think_of_it")))
    exposure(env.db, "come_to_think_of_it", 0, "e1")
    attempt(env.db, "come_to_think_of_it", 0, "a1", outcome="success", due=False)
    env.db.clock.travel(days=3)
    app.send_review(env.bot, CHAT, LID)
    shown = env.bot.sent[-1]
    assert "Review 1/1" in shown.text
    app.on_callback(env.bot, "cb", LID, CHAT, shown.message_id, data(shown.markup, "rvh"))
    assert "💡" in env.bot.edits[-1].text
    app.on_text(env.bot, CHAT, LID, "Come to think of it, we never booked the room.", 20, background=False)
    assert "got it" in env.bot.sent[-1].text.lower() and env.h.events("retrieval_attempted")[-1]["payload"]["hint_level"] == 1


def test_progress_and_debrief_are_templated_messages(env):
    app.send_progress(env.bot, CHAT, LID)
    assert "Your progress" in env.bot.sent[-1].text
    app.send_debrief(env.bot, CHAT, LID)
    assert env.bot.sent[-1].text


# ------------------------------------------------------------------------------ canary and legacy


def test_the_canary_serves_only_the_admin(env, monkeypatch):
    monkeypatch.setattr(app, "CANARY_ADMIN_ONLY", True)
    monkeypatch.setattr(app, "ADMIN_ID", 1)
    app.on_text(env.bot, CHAT, LID, TEXT, 11, background=False)
    assert "private test" in env.bot.sent[-1].text and env.ai.briefs == []
    app.on_callback(env.bot, "cb", LID, CHAT, 1, "ls:1:0")
    assert env.bot.answers[-1] == ("cb", "Private test")
    assert app.begin_command(env.bot, CHAT, LID) is False
    env.ai.analyses.append({"diagnostics": []})
    app.on_text(env.bot, CHAT, 1, GREETING_1, 12, background=False)
    assert len(env.ai.briefs) == 1


def test_the_legacy_callbacks_keep_working(env):
    app.on_callback(env.bot, "g", LID, CHAT, 7, "guide:overview")
    assert env.bot.edits[-1].message_id == 7 and env.bot.answers[-1][1] == "Guide chapter loaded"
    app.on_callback(env.bot, "c", LID, CHAT, 7, "card:nope")
    assert env.bot.answers[-1][1] == "Card expired."
    app.on_callback(env.bot, "t", LID, CHAT, 7, "tts:msg", "💬 Alex: Hello there")
    assert env.synth.calls == 1
    app.on_callback(env.bot, "s", LID, CHAT, 7, "setreg:warm")
    assert env.db.get_learner_settings(LID)["default_register"] == "warm"
    app.on_callback(env.bot, "x", LID, CHAT, 7, "decode_reveal")
    assert env.bot.answers[-1][1] == "Breakdown already revealed."


def test_the_source_has_no_v2_broadcast_and_keeps_the_pinned_strings():
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "v2_complete" not in src
    for needle in ('callback_data="tts:msg"', "_get_learner_lock", "force_sync_all_telegram_scopes_and_profile", "extract_alex_spoken_text", "register_workout_task"):
        assert needle in src
    for gone in ("get_auto_voice_pref", "set_auto_voice_pref", "confirm_card_delivered", "InterventionLevel", "orchestrator.process_turn(\n                learner_id=lid,\n                learner_text=text,\n                message_idempotency_key=f\"tg_msg"):
        assert gone not in src
