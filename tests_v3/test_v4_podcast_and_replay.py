"""Tests for V4.0 Sprint 1: Personal Podcast Generator & Pragmatic Replay Studio."""

from __future__ import annotations

import tempfile
import os

import podcast
import replay
from database import EventSourcedDatabase
from flows import V3Flows


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return EventSourcedDatabase(path), path


def test_podcast_generation_and_transcript_rendering():
    db, path = _make_db()
    try:
        lid = 99001
        for mode in ("recap", "briefing", "narrative"):
            ep = podcast.generate_podcast_episode(db, None, lid, mode=mode)
            assert ep.mode == mode
            assert len(ep.turns) >= 4
            assert any(t.speaker == "Alex" for t in ep.turns)
            assert any(t.speaker == "Maya" for t in ep.turns)
            card_collapsed = podcast.render_podcast_card_html(ep, show_transcript=False, with_arabic=True)
            assert "Featured Moves" in card_collapsed
            assert "Full Episode Transcript" not in card_collapsed
            card_expanded = podcast.render_podcast_card_html(ep, show_transcript=True, with_arabic=True)
            assert "Full Episode Transcript" in card_expanded
            assert "Alex:" in card_expanded and "Maya:" in card_expanded
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_pragmatic_replay_lifecycle_and_send_to_alex():
    db, path = _make_db()
    try:
        lid = 99002
        flows = V3Flows(db, None)
        hub = flows.replay_hub_view(lid)
        assert "Pragmatic Replay Studio" in hub.text

        # Pick executive angle
        cb = flows.handle_v4_callback(lid, "rep:angle:executive")
        assert cb.edit is not None
        assert "Crisp Executive" in cb.edit.text

        # Submit learner replay attempt via flows.handle_text
        res = flows.handle_text(lid, "Right now I am taking on the heaviest deliverables, and that setup isn't sustainable going forward.")
        assert len(res.views) == 1
        assert "Send to Alex" in str(res.views[0].buttons)

        # Send to Alex closes the active replay session and submits turn
        send_cb = flows.handle_v4_callback(lid, "rep:send")
        assert send_cb.submit_turn is not None
        assert "sustainable going forward" in send_cb.submit_turn.text
        assert replay.get_replay_session(lid) is None
    finally:
        if os.path.exists(path):
            os.remove(path)
