"""Gemini HTTP transport with lanes, deadlines, cooldowns, breaker & capability cache
(S0 Contracts, Amendments A11-A12, Section 6 & Section 8).
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

import requests

import config
from clock import Clock, SystemClock

__all__ = [
    "CallClass",
    "QuotaShed",
    "DeadlineExceeded",
    "ResponderUnavailable",
    "CallMeta",
    "CallResult",
    "LLMTransport",
    "call_json",
    "parse_429_cooldown_seconds",
    "reset_transport_state",
]

T = TypeVar("T")
CallClass = Literal["responder", "analyst", "judge", "generator"]

_RETRY_DELAY_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)s$")
_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


class QuotaShed(RuntimeError):
    """Raised when a sheddable call class is shed due to quota/breaker pressure."""


class DeadlineExceeded(TimeoutError):
    """Raised when a call exceeds its hard deadline."""


class ResponderUnavailable(RuntimeError):
    """Raised when all (project, model) pairs are exhausted even after last-resort retry."""


@dataclass(frozen=True)
class CallMeta:
    model: str
    project_label: str
    attempts: int
    latency_ms: int
    thinking_budget: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class CallResult(Generic[T]):
    value: T
    meta: CallMeta


@dataclass(frozen=True)
class _CallPolicy:
    default_deadline_s: float
    max_attempts: int
    per_attempt_cap_s: float
    thinking_budget: int | None
    sheddable: bool


_POLICIES: dict[str, _CallPolicy] = {
    "responder": _CallPolicy(10.0, 2, 5.5, 0, False),
    "analyst": _CallPolicy(30.0, 2, 18.0, 1024, True),
    "judge": _CallPolicy(12.0, 2, 6.5, 512, True),
    "generator": _CallPolicy(15.0, 2, 8.0, 0, True),
}


def _seconds_until_pacific_midnight(now_utc: datetime) -> float:
    now_pt = now_utc.astimezone(_PACIFIC_TZ)
    next_day = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return max(60.0, (next_day - now_pt).total_seconds())


def parse_429_cooldown_seconds(body: Any, now_utc: datetime | None = None) -> tuple[float, bool]:
    """Defensive parser for Gemini 429 error bodies (returns (cooldown_s, is_daily))."""
    now = now_utc or datetime.now(timezone.utc)
    retry_delay_s: float | None = None
    is_daily = False

    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body.decode("utf-8", errors="ignore"))
        except Exception:
            body = {}
    elif isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {"error": {"message": body}}

    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            msg = str(err.get("message") or "")
            if any(tok in msg for tok in ("PerDay", "per day", "per-day", "RPD", "daily")):
                is_daily = True
            details = err.get("details")
            if isinstance(details, Sequence):
                for item in details:
                    if not isinstance(item, Mapping):
                        continue
                    t_url = str(item.get("@type") or "")
                    if "RetryInfo" in t_url or "retryDelay" in item:
                        raw_delay = str(item.get("retryDelay") or "").strip()
                        m = _RETRY_DELAY_RE.match(raw_delay)
                        if m:
                            retry_delay_s = float(m.group(1))
                    if "QuotaFailure" in t_url or "violations" in item:
                        violations = item.get("violations")
                        if isinstance(violations, Sequence):
                            for v in violations:
                                if isinstance(v, Mapping):
                                    qid = str(v.get("quotaId") or "")
                                    qmetric = str(v.get("quotaMetric") or "")
                                    if "PerDay" in qid or "Day" in qid or "per_day" in qmetric:
                                        is_daily = True

    if is_daily:
        return _seconds_until_pacific_midnight(now), True
    if retry_delay_s is not None:
        return max(retry_delay_s, 1.0), False
    return 15.0, False


def _extract_json_dict(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Model response did not contain a valid JSON object")


class LLMTransport:
    """Thread-safe Gemini transport managing lanes, project cooldowns, breakers, and 400 capability cache."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        http_post: Callable[..., requests.Response] | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._http_post = http_post or requests.post
        self._lock = threading.RLock()
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._capabilities: dict[str, dict[str, bool]] = {}
        self._rr_index: int = 0

    def _now_ts(self) -> float:
        return self._clock.now().timestamp()

    def reset(self) -> None:
        with self._lock:
            self._cooldowns.clear()
            self._failures.clear()
            self._capabilities.clear()
            self._rr_index = 0

    def get_project_keys(self) -> list[tuple[str, str]]:
        """Returns [(project_label, api_key), ...]. Unlabeled keys share 'default' (A11)."""
        fn = getattr(config, "get_labeled_gemini_keys", None)
        if callable(fn):
            pairs = fn()
            if pairs:
                return list(pairs)
        raw_keys = getattr(config, "GEMINI_API_KEYS", None) or []
        if not raw_keys:
            single = getattr(config, "GEMINI_API_KEY", "")
            raw_keys = [single] if single else []
        out: list[tuple[str, str]] = []
        for entry in raw_keys:
            s = str(entry).strip()
            if not s:
                continue
            if ":" in s and not s.startswith("AIza"):
                label, key = s.split(":", 1)
                out.append((label.strip() or "default", key.strip()))
            else:
                out.append(("default", s))
        return out or [("default", "test-key")]

    def get_lane_models(self, call_class: CallClass) -> list[str]:
        """Returns ordered models for the lane; Analyst never starts with a Pro model (A11)."""
        fn = getattr(config, "get_lane_models", None)
        if callable(fn):
            models = list(fn(call_class))
        else:
            chain = list(getattr(config, "GEMINI_MODEL_CHAIN", ["gemini-2.5-flash", "gemini-2.0-flash"]))
            models = chain

        if call_class == "analyst":
            non_pro = [m for m in models if "pro" not in m.lower()]
            pro_only = [m for m in models if "pro" in m.lower()]
            models = non_pro + pro_only
            # Separate primary model from responder when at least 2 non-pro models exist
            resp_models = self.get_lane_models("responder") if call_class != "responder" else []
            if resp_models and len(non_pro) >= 2 and models[0] == resp_models[0]:
                models = [non_pro[1], non_pro[0]] + non_pro[2:] + pro_only

        return models or ["gemini-2.5-flash"]

    def _is_cooled(self, project_label: str, model: str, now_ts: float) -> bool:
        until = self._cooldowns.get((project_label, model), 0.0)
        return until > now_ts

    def _mark_cooldown(self, project_label: str, model: str, duration_s: float) -> None:
        now_ts = self._now_ts()
        pair = (project_label, model)
        self._cooldowns[pair] = max(self._cooldowns.get(pair, 0.0), now_ts + duration_s)

    def _record_transient_failure(self, project_label: str, model: str) -> None:
        now_ts = self._now_ts()
        pair = (project_label, model)
        q = self._failures.setdefault(pair, deque())
        q.append(now_ts)
        while q and (now_ts - q[0]) > 60.0:
            q.popleft()
        if len(q) >= 3:
            # Open 30s circuit breaker after 3 timeouts or 5xx in 60s
            self._cooldowns[pair] = max(self._cooldowns.get(pair, 0.0), now_ts + 30.0)

    def _capability(self, model: str) -> dict[str, bool]:
        return self._capabilities.setdefault(
            model, {"thinking": True, "response_schema": True}
        )

    def _build_candidates(
        self, call_class: CallClass
    ) -> tuple[list[tuple[str, str, str]], tuple[str, str, str] | None]:
        """Returns (available_triples, soonest_recovering_triple)."""
        keys = self.get_project_keys()
        models = self.get_lane_models(call_class)
        now_ts = self._now_ts()

        with self._lock:
            start_idx = self._rr_index % max(1, len(keys))
            self._rr_index += 1
            rotated_keys = keys[start_idx:] + keys[:start_idx]

            available: list[tuple[str, str, str]] = []
            seen_proj_model: set[tuple[str, str]] = set()
            soonest_triple: tuple[str, str, str] | None = None
            soonest_until = float("inf")

            for model in models:
                for proj, api_key in rotated_keys:
                    pm = (proj, model)
                    if not self._is_cooled(proj, model, now_ts):
                        if pm not in seen_proj_model:
                            available.append((proj, api_key, model))
                            seen_proj_model.add(pm)
                    else:
                        until = self._cooldowns.get(pm, 0.0)
                        if until < soonest_until:
                            soonest_until = until
                            soonest_triple = (proj, api_key, model)

            return available, soonest_triple

    def call_json(
        self,
        call_class: CallClass,
        system: str,
        user: str | list[dict[str, Any]],
        validator: Callable[[dict[str, Any]], T],
        *,
        deadline_s: float | None = None,
        response_schema: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> CallResult[T]:
        policy = _POLICIES[call_class]
        total_deadline = deadline_s if deadline_s is not None else policy.default_deadline_s
        start_mono = time.monotonic()

        available, soonest_cooled = self._build_candidates(call_class)
        if not available:
            if policy.sheddable:
                raise QuotaShed(f"All ({call_class}) lanes/projects in cooldown or breaker.")
            # A12: Responder tries the pair that recovers soonest once before raising ResponderUnavailable
            if soonest_cooled is not None:
                available = [soonest_cooled]
            else:
                raise ResponderUnavailable("No Gemini API keys or models configured.")

        attempts_made = 0
        last_error: Exception | None = None

        for proj_label, api_key, model in available:
            if attempts_made >= policy.max_attempts:
                break

            elapsed = time.monotonic() - start_mono
            remaining = total_deadline - elapsed
            if remaining <= 0.05:
                raise DeadlineExceeded(f"{call_class} deadline ({total_deadline}s) exceeded before attempt.")

            attempts_made += 1
            http_timeout = min(policy.per_attempt_cap_s, remaining)

            with self._lock:
                caps = dict(self._capability(model))

            used_thinking: int | None = None
            if policy.thinking_budget is not None and caps.get("thinking", True):
                used_thinking = policy.thinking_budget

            payload = self._build_payload(
                system=system,
                user=user,
                thinking_budget=used_thinking,
                response_schema=response_schema if caps.get("response_schema", True) else None,
            )
            url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model}:generateContent?key={api_key}"

            try:
                resp = self._http_post(url, json=payload, timeout=http_timeout)
            except (requests.Timeout, TimeoutError) as exc:
                with self._lock:
                    self._record_transient_failure(proj_label, model)
                last_error = exc
                if (time.monotonic() - start_mono) >= total_deadline:
                    raise DeadlineExceeded(f"{call_class} timed out after {total_deadline}s") from exc
                continue
            except requests.RequestException as exc:
                with self._lock:
                    self._record_transient_failure(proj_label, model)
                last_error = exc
                continue

            # Handle HTTP 400 capability fallback (thinkingConfig or responseSchema unsupported)
            if resp.status_code == 400 and (used_thinking is not None or response_schema is not None):
                err_txt = resp.text.lower()
                with self._lock:
                    cap_ref = self._capability(model)
                    if "thinking" in err_txt and used_thinking is not None:
                        cap_ref["thinking"] = False
                        used_thinking = None
                    else:
                        cap_ref["thinking"] = False
                        cap_ref["response_schema"] = False
                        used_thinking = None

                elapsed = time.monotonic() - start_mono
                remaining = total_deadline - elapsed
                if remaining <= 0.05:
                    raise DeadlineExceeded(f"{call_class} deadline exceeded during 400 fallback.")

                fallback_payload = self._build_payload(
                    system=system,
                    user=user,
                    thinking_budget=None,
                    response_schema=None,
                )
                try:
                    resp = self._http_post(url, json=fallback_payload, timeout=min(policy.per_attempt_cap_s, remaining))
                except (requests.Timeout, TimeoutError) as exc:
                    raise DeadlineExceeded(f"{call_class} timed out on 400 fallback") from exc

            if resp.status_code == 429:
                try:
                    body_json = resp.json()
                except Exception:
                    body_json = resp.text
                cooldown_s, _ = parse_429_cooldown_seconds(body_json, self._clock.now())
                with self._lock:
                    self._mark_cooldown(proj_label, model, cooldown_s)
                last_error = QuotaShed(f"429 on ({proj_label}, {model})")
                continue

            if resp.status_code >= 500:
                with self._lock:
                    self._record_transient_failure(proj_label, model)
                last_error = RuntimeError(f"HTTP {resp.status_code} on ({proj_label}, {model})")
                continue

            if resp.status_code != 200:
                last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                continue

            try:
                data = resp.json()
                raw_text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                parsed_dict = _extract_json_dict(raw_text)
                validated = validator(parsed_dict)
                usage = data.get("usageMetadata") or {}
                latency_ms = int(round((time.monotonic() - start_mono) * 1000))
                meta = CallMeta(
                    model=model,
                    project_label=proj_label,
                    attempts=attempts_made,
                    latency_ms=latency_ms,
                    thinking_budget=used_thinking,
                    input_tokens=usage.get("promptTokenCount"),
                    output_tokens=usage.get("candidatesTokenCount"),
                )
                return CallResult(value=validated, meta=meta)
            except Exception as exc:
                last_error = exc
                continue

        if (time.monotonic() - start_mono) >= total_deadline:
            raise DeadlineExceeded(f"{call_class} exceeded deadline {total_deadline}s")
        if call_class == "responder":
            raise ResponderUnavailable(f"Responder failed across all candidates: {last_error}")
        if isinstance(last_error, QuotaShed):
            raise last_error
        raise QuotaShed(f"{call_class} exhausted attempts: {last_error}")

    @staticmethod
    def _build_payload(
        *,
        system: str,
        user: str | list[dict[str, Any]],
        thinking_budget: int | None,
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(user, str):
            contents = [{"role": "user", "parts": [{"text": user}]}]
        else:
            contents = user
        gen_cfg: dict[str, Any] = {
            "temperature": 0.35,
            "responseMimeType": "application/json",
        }
        if response_schema is not None:
            gen_cfg["responseSchema"] = response_schema
        if thinking_budget is not None:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": gen_cfg,
        }


_DEFAULT_TRANSPORT = LLMTransport()


def reset_transport_state() -> None:
    _DEFAULT_TRANSPORT.reset()


def call_json(
    call_class: CallClass,
    system: str,
    user: str | list[dict[str, Any]],
    validator: Callable[[dict[str, Any]], T],
    *,
    deadline_s: float | None = None,
    response_schema: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> CallResult[T]:
    return _DEFAULT_TRANSPORT.call_json(
        call_class,
        system,
        user,
        validator,
        deadline_s=deadline_s,
        response_schema=response_schema,
        correlation_id=correlation_id,
    )
