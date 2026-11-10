"""Regression tests for the audit fixes (persistence, Gemini client, gauge, router, guard, responder, voice)."""
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.alex.responder import AlexResponder
from src.db.engine import (
    export_encrypted_snapshot,
    get_connection,
    restore_encrypted_snapshot,
    restore_failed,
)
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository
from src.llm import gemini_client
from src.llm.gemini_client import GeminiService, LLMUnavailableError
from src.naturalness import NaturalnessGauge, NaturalnessGuard, NaturalnessRouter


# ---------------------------------------------------------------- config / persistence
def test_empty_env_var_falls_back_to_default(monkeypatch):
    from src import config

    monkeypatch.setenv("SEL_SNAPSHOT_PUSH_MIN", "")
    assert config._env_int("SEL_SNAPSHOT_PUSH_MIN", 60) == 60
    monkeypatch.setenv("GEMINI_MODEL_CHAIN", "")
    assert config.gemini_model_chain()[0]  # empty repo variable no longer produces an empty chain


def test_snapshot_roundtrip_is_consistent_and_atomic(tmp_path: Path):
    db, snap = tmp_path / "a.db", tmp_path / "s.enc"
    with get_connection(db) as c:
        c.execute("INSERT INTO nat_heard (user_id, phrase, context_note, ts) VALUES (1, 'that tracks', '', 0)")
        c.commit()
    assert export_encrypted_snapshot(db, snap, "pw") == snap
    assert not (tmp_path / "s.enc.tmp").exists()
    out = tmp_path / "b.db"
    assert restore_encrypted_snapshot(snap, out, "pw") is True
    assert sqlite3.connect(out).execute("SELECT phrase FROM nat_heard").fetchone()[0] == "that tracks"


def test_failed_restore_protects_the_good_snapshot(tmp_path: Path):
    db, snap = tmp_path / "a.db", tmp_path / "s.enc"
    with get_connection(db) as c:
        c.commit()
    export_encrypted_snapshot(db, snap, "right")
    good = snap.read_bytes()
    assert restore_encrypted_snapshot(snap, tmp_path / "x.db", "WRONG") is False
    assert restore_failed(snap)
    assert export_encrypted_snapshot(db, snap, "WRONG") is None  # must not overwrite with an empty DB
    assert snap.read_bytes() == good
    assert restore_encrypted_snapshot(snap, tmp_path / "x.db", "right") is True  # marker cleared on success
    assert not restore_failed(snap)


def test_corrupt_short_snapshot_is_never_overwritten(tmp_path: Path):
    db, snap = tmp_path / "a.db", tmp_path / "s.enc"
    with get_connection(db) as c:
        c.commit()
    snap.write_bytes(b"junk")
    assert restore_encrypted_snapshot(snap, tmp_path / "x.db", "pw") is False
    assert export_encrypted_snapshot(db, snap, "pw") is None
    assert snap.read_bytes() == b"junk"


def test_snapshot_writes_are_coalesced_then_flushed(tmp_path: Path):
    from src.db import engine

    engine.set_snapshot_min_interval(3600.0)
    try:
        repo = ProfileRepository(tmp_path / "p.db", tmp_path / "p.enc", "pw")
        p = repo.get_or_create_profile(5)  # first write exports
        first = (tmp_path / "p.enc").read_bytes()
        p.goal = "changed"
        repo.save_profile(p)  # inside the window: coalesced, not exported
        assert (tmp_path / "p.enc").read_bytes() == first
        assert engine.flush_pending_snapshots() == 1  # shutdown / background flush writes it
        assert (tmp_path / "p.enc").read_bytes() != first
    finally:
        engine.set_snapshot_min_interval(0)


def test_refund_quota_gives_back_one_call(tmp_path: Path):
    repo = ProfileRepository(tmp_path / "p.db", tmp_path / "p.enc", "")
    assert repo.check_and_increment_quota(5) == (True, 1)
    repo.refund_quota(5)
    assert repo.check_and_increment_quota(5) == (True, 1)
    repo.refund_quota(5)
    repo.refund_quota(5)  # never goes below zero
    assert repo.check_and_increment_quota(5) == (True, 1)


def test_publish_snapshot_is_a_noop_outside_ci(monkeypatch):
    from src.db import persist

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("SEL_GIT_PERSIST", raising=False)
    assert persist.publish_snapshot() == "not-ci"


# ---------------------------------------------------------------- Gemini client
class _Resp:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def generate_content(self, model, contents, config=None):
        self.calls.append((model, config))
        return self.fn(model, config)


def _svc(tmp_path, fns):
    svc = GeminiService(ProfileRepository(tmp_path / "g.db", tmp_path / "g.enc", ""), api_key="k")
    svc.clients = [SimpleNamespace(models=_Models(f)) for f in fns]
    return svc


def test_thinking_config_is_chosen_per_model():
    from google.genai import types

    if not hasattr(types, "ThinkingConfig"):
        pytest.skip("SDK without ThinkingConfig")
    t25 = gemini_client._thinking_config_for("gemini-2.5-flash-lite")
    assert getattr(t25, "thinking_budget", None) == 0
    t3 = gemini_client._thinking_config_for("gemini-3.5-flash-lite")
    lvl = getattr(t3, "thinking_level", None)
    assert t3 is None or str(getattr(lvl, "value", lvl)).lower() == "low"
    assert gemini_client._thinking_config_for("some-unknown-model") is None


@pytest.mark.asyncio
async def test_thinking_400_is_retried_once_without_thinking(tmp_path):
    seen = []

    def fn(model, cfg):
        seen.append(getattr(cfg, "thinking_config", None))
        if getattr(cfg, "thinking_config", None) is not None:
            raise RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported for this model")
        return _Resp("ok")

    svc = _svc(tmp_path, [fn])
    assert await svc.generate_text(1, "s", "p") == "ok"
    assert seen[-1] is None  # same model, retried without thinking
    assert len({m for m, _ in svc.clients[0].models.calls}) == 1


@pytest.mark.asyncio
async def test_hung_call_times_out_and_fails_over(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(gemini_client, "CALL_TIMEOUT_S", 0.2)

    def hang(model, cfg):
        time.sleep(0.6)
        return _Resp("late")

    svc = _svc(tmp_path, [hang, lambda m, c: _Resp("fast")])
    t0 = time.monotonic()
    assert await svc.generate_text(1, "s", "p") == "fast"
    assert time.monotonic() - t0 < 2.0


@pytest.mark.asyncio
async def test_rate_limited_pair_is_cooled_down_not_hammered(tmp_path):
    def limited(model, cfg):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded PerDay")

    svc = _svc(tmp_path, [limited, lambda m, c: _Resp("fine")])
    assert await svc.generate_text(1, "s", "p") == "fine"
    n = len(svc.clients[0].models.calls)
    assert await svc.generate_text(1, "s", "p") == "fine"
    assert len(svc.clients[0].models.calls) == n  # pair on cooldown: skipped on the second call


@pytest.mark.asyncio
async def test_total_failure_refunds_the_learners_quota(tmp_path):
    def gone(model, cfg):
        raise RuntimeError("503 UNAVAILABLE overloaded")

    svc = _svc(tmp_path, [gone])
    with pytest.raises(LLMUnavailableError):
        await svc.generate_text(9, "s", "p")
    assert svc.repo.check_and_increment_quota(9) == (True, 1)  # the failed call cost nothing


# ---------------------------------------------------------------- gauge
def test_gauge_survives_restart_and_isolates_users(tmp_path: Path):
    db = tmp_path / "g.db"
    g1 = NaturalnessGauge(db_path=db)
    g1.log_turn("7:1", 7, "friend_text", "short", ["x"], "yeah, totally", 2, False, [])
    g1.record_heard(7, "that tracks")
    g2 = NaturalnessGauge(db_path=db)  # new process, same database
    assert g2.count_turns(7) == 1
    assert g2.heard_for(7) == ["that tracks"] and g2.heard_for(8) == []
    assert g2.weekly_report(user_id=7)["total_messages"] == 1
    assert g2.flag_artificial("7:1", ["a", "b"], user_id=8) is False  # someone else's message
    assert g2.flag_artificial("7:1", ["a", "b"], user_id=7) is True
    assert NaturalnessGauge(db_path=db).weekly_report(user_id=7)["flagged_rate"] == 1.0


def test_gauge_without_db_still_works_in_memory():
    g = NaturalnessGauge()
    g.log_turn("1:1", 1, "p", "d", [], "hi there", 2, False, [])
    assert g.weekly_report()["total_messages"] == 1


# ---------------------------------------------------------------- router / guard
def test_short_keywords_do_not_match_inside_other_words():
    from src.naturalness.router import _kw_hit

    assert not _kw_hit("hi", "this is history")
    assert _kw_hit("hi", "hi there")
    assert _kw_hit("thank", "thanks a lot")


def test_route_bundle_has_a_human_situation_name_and_voice_profile():
    r = NaturalnessRouter()
    b = r.route(user_id=1, turn_no=1, user_text="", room="casual_friends", voice_mode=True)
    assert b.profile_id == "friend_voice"
    assert b.situation_name and "_" not in b.situation_name


def test_dice_sequence_differs_between_runs_via_salt():
    r = NaturalnessRouter()
    seqs = {
        tuple(r.roll_dice(user_id=3, turn_no=t, salt=s)[0]["id"] for t in range(1, 9)) for s in ("a", "b", "c", "d")
    }
    assert len(seqs) > 1


def test_guard_keeps_real_refusals_and_only_strips_ai_opener_pattern():
    g = NaturalnessGuard()
    assert g.apply("Absolutely not, I can't make it.").text.startswith("Absolutely not")
    assert not g.apply("Certainly! Sure, come over.").text.startswith("Certainly")


def test_tts_prep_keeps_times_prices_and_decimals():
    out = NaturalnessGuard.prepare_for_tts("tbh meet at 3:30, it costs $5, 2.5 hours, 7 people")
    assert "3:30" in out and "$5" in out and "2.5" in out and "seven people" in out and "to be honest" in out


def test_voice_text_is_speakable():
    from src.bot.voice_io import clean_for_tts

    out = clean_for_tts("lol idk 😂 see you rn")
    assert "😂" not in out and "I don't know" in out and "right now" in out


# ---------------------------------------------------------------- responder
class _LLM:
    configured = True

    def __init__(self, replies):
        self.replies, self.prompts, self.fail = list(replies), [], False

    async def generate_text(self, user_id, system_prompt, user_prompt, temperature=0.7):
        self.prompts.append(system_prompt)
        if self.fail:
            raise LLMUnavailableError("down")
        return self.replies.pop(0) if self.replies else "yeah, fair."


@pytest.mark.asyncio
async def test_logged_dice_is_the_dice_that_was_prompted(tmp_path: Path):
    llm = _LLM(["ok."])
    gauge = NaturalnessGauge(db_path=tmp_path / "r.db")
    resp = AlexResponder(SceneRepository(db_path=tmp_path / "r.db"), llm, nat_gauge=gauge)
    await resp.reply(11, "Wanna grab coffee tonight?")
    logged = gauge.logs[-1].dice_id
    assert f"({logged})" in llm.prompts[-1]  # "THIS TURN'S DICE DIRECTIVE (<id>)"


@pytest.mark.asyncio
async def test_failed_llm_call_leaves_no_half_turn(tmp_path: Path):
    llm = _LLM([])
    repo = SceneRepository(db_path=tmp_path / "r.db")
    resp = AlexResponder(repo, llm, nat_gauge=NaturalnessGauge(db_path=tmp_path / "r.db"))
    llm.fail = True
    with pytest.raises(LLMUnavailableError):
        await resp.reply(12, "hello?")
    assert resp._user_turn_counts.get(12) is None and not repo.get_recent_turns(12, role="learner", limit=5)
    llm.fail = False
    await resp.reply(12, "hello again")
    assert resp._user_turn_counts[12] == 1


@pytest.mark.asyncio
async def test_turn_counter_is_persisted_across_restarts(tmp_path: Path):
    db = tmp_path / "r.db"
    llm = _LLM(["a.", "b."])
    r1 = AlexResponder(SceneRepository(db_path=db), llm, nat_gauge=NaturalnessGauge(db_path=db))
    await r1.reply(13, "one")
    await r1.reply(13, "two")
    r2 = AlexResponder(SceneRepository(db_path=db), llm, nat_gauge=NaturalnessGauge(db_path=db))
    assert r2._next_turn_no(13) == 3


@pytest.mark.asyncio
async def test_voice_note_uses_spoken_profile_and_casual_words_survive(tmp_path: Path):
    llm = _LLM(["dude, that's wild."])
    gauge = NaturalnessGauge(db_path=tmp_path / "r.db")
    resp = AlexResponder(SceneRepository(db_path=tmp_path / "r.db"), llm, nat_gauge=gauge)
    text, _, _ = await resp.reply(14, "you won't believe my day", voice_mode=True)
    assert gauge.logs[-1].profile == "friend_voice"
    assert "dude" in text  # the sanitizer no longer deletes authentic casual words


@pytest.mark.asyncio
async def test_same_user_turns_never_interleave(tmp_path: Path):
    order = []

    class Slow(_LLM):
        async def generate_text(self, user_id, system_prompt, user_prompt, temperature=0.7):
            order.append("start")
            await asyncio.sleep(0.05)
            order.append("end")
            return "ok."

    resp = AlexResponder(SceneRepository(db_path=tmp_path / "r.db"), Slow([]),
                         nat_gauge=NaturalnessGauge(db_path=tmp_path / "r.db"))
    await asyncio.gather(resp.reply(15, "a"), resp.reply(15, "b"))
    assert order == ["start", "end", "start", "end"]


# ---------------------------------------------------------------- background loops
@pytest.mark.asyncio
async def test_reminder_loop_sends_and_marks_users(monkeypatch):
    from src.bot import background

    sent, marked = [], []
    repo = SimpleNamespace(get_due_reminder_users=lambda: [1, 2], mark_reminder_sent=marked.append)

    class Bot:
        async def send_message(self, chat_id, text):
            if chat_id == 2:
                raise RuntimeError("blocked")
            sent.append(chat_id)

    task = asyncio.create_task(
        background.reminder_loop(Bot(), SimpleNamespace(chunk_repo=repo), interval_s=60, first_delay_s=0)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    assert sent == [1] and marked == [1, 2]  # a blocked user is marked too: no retry storm
