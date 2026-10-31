"""Sprint S2 Transport Acceptance Tests (S0 Contracts, Section 8)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from clock import FakeClock
from llm_transport import (
    DeadlineExceeded,
    LLMTransport,
    QuotaShed,
    ResponderUnavailable,
    parse_429_cooldown_seconds,
)


def _mock_resp(status: int, body: Any) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    if isinstance(body, (dict, list)):
        m.json.return_value = body
        m.text = json.dumps(body)
    else:
        m.text = str(body)
        m.json.side_effect = ValueError("not json")
    return m


def test_s2_429_parse_variants() -> None:
    clock = FakeClock("2026-10-31T18:00:00Z")
    # 1. Standard RetryInfo + RPM violation
    rpm_body = {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted.",
            "details": [
                {
                    "@type": "[type.googleapis.com/google.rpc.QuotaFailure](https://type.googleapis.com/google.rpc.QuotaFailure)",
                    "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}],
                },
                {"@type": "[type.googleapis.com/google.rpc.RetryInfo](https://type.googleapis.com/google.rpc.RetryInfo)", "retryDelay": "42s"},
            ],
        }
    }
    sec, is_daily = parse_429_cooldown_seconds(rpm_body, clock.now())
    assert sec == 42.0 and is_daily is False

    # 2. PerDay violation cools down until midnight Pacific
    rpd_body = {
        "error": {
            "code": 429,
            "message": "Quota exceeded.",
            "details": [
                {
                    "@type": "[type.googleapis.com/google.rpc.QuotaFailure](https://type.googleapis.com/google.rpc.QuotaFailure)",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
                }
            ],
        }
    }
    sec_day, is_daily_day = parse_429_cooldown_seconds(rpd_body, clock.now())
    assert is_daily_day is True and sec_day > 3600.0

    # 3. Body with no details at all -> safe 15s default
    empty_body = {"error": {"code": 429, "message": "Too many requests"}}
    sec_def, is_daily_def = parse_429_cooldown_seconds(empty_body, clock.now())
    assert sec_def == 15.0 and is_daily_def is False


def test_s2_project_cooldown_and_shedding(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock("2026-10-31T18:00:00Z")
    # Two unlabeled keys share 'default' project label -> 429 on first cools the whole project+model
    monkeypatch.setattr("config.GEMINI_API_KEYS", ["AIzaKey1", "AIzaKey2"])
    monkeypatch.setattr("config.GEMINI_MODEL_CHAIN", ["gemini-2.5-flash"])

    calls = []

    def fake_post(url: str, json: dict, timeout: float):
        calls.append(url)
        return _mock_resp(429, {"error": {"code": 429, "details": [{"retryDelay": "30s"}]}})

    tr = LLMTransport(clock=clock, http_post=fake_post)
    with pytest.raises(QuotaShed):
        tr.call_json("analyst", "sys", "usr", lambda d: d)
    # Because both keys share 'default' project, the second call is shed immediately without HTTP hit
    assert len(calls) == 1
    with pytest.raises(QuotaShed):
        tr.call_json("analyst", "sys", "usr", lambda d: d)
    assert len(calls) == 1


def test_s2_circuit_breaker_and_responder_last_resort(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock("2026-10-31T18:00:00Z")
    monkeypatch.setattr("config.GEMINI_API_KEYS", ["projA:AIzaKeyA"])
    monkeypatch.setattr("config.GEMINI_MODEL_CHAIN", ["gemini-2.5-flash"])

    def fail_500(url: str, json: dict, timeout: float):
        return _mock_resp(503, "Service Unavailable")

    tr = LLMTransport(clock=clock, http_post=fail_500)
    with pytest.raises(QuotaShed):
        tr.call_json("analyst", "sys", "usr", lambda d: d)
    # 3rd failure within 60s opens the 30s breaker
    with pytest.raises(QuotaShed):
        tr.call_json("analyst", "sys", "usr", lambda d: d)

    # Responder is never shed silently; it tries the soonest-recovering pair once, then raises ResponderUnavailable
    with pytest.raises(ResponderUnavailable):
        tr.call_json("responder", "sys", "usr", lambda d: d)


def test_s2_lane_separation_and_thinking_capability_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock("2026-10-31T18:00:00Z")
    monkeypatch.setattr("config.GEMINI_API_KEYS", ["projA:AIzaKeyA"])
    monkeypatch.setattr(
        "config.GEMINI_MODEL_CHAIN",
        ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
    )

    seen_payloads: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float):
        seen_payloads.append(json)
        if "thinkingConfig" in json.get("generationConfig", {}):
            return _mock_resp(400, {"error": {"message": "thinkingConfig is not supported for this model"}})
        return _mock_resp(
            200,
            {
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    tr = LLMTransport(clock=clock, http_post=fake_post)
    # Analyst lane never starts with a Pro model, and differs from Responder primary
    resp_lane = tr.get_lane_models("responder")
    analyst_lane = tr.get_lane_models("analyst")
    assert "pro" not in analyst_lane[0].lower()
    assert analyst_lane[0] != resp_lane[0]

    # First analyst call hits 400 on thinkingConfig, falls back automatically, and caches capability
    res1 = tr.call_json("analyst", "sys", "usr", lambda d: d["ok"])
    assert res1.value is True
    assert len(seen_payloads) == 2

    # Second analyst call skips thinkingConfig immediately (only 1 HTTP request)
    res2 = tr.call_json("analyst", "sys", "usr", lambda d: d["ok"])
    assert res2.value is True
    assert len(seen_payloads) == 3
