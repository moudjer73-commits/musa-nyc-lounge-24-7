"""Injected time source for Spoken English Lab V3.0 (S0 section 0).

Rules this module exists to enforce:

* Time is always UTC and always timezone-aware. Persisted time is an ISO-8601
  string with a trailing ``Z`` and microseconds (``2026-10-31T18:09:00.000000Z``).
  The fixed width makes the strings sort chronologically, which the SQL in
  ``database.py`` and ``interaction.py`` relies on.
* Nothing outside this module calls ``datetime.now`` directly. Every module that
  needs "now" takes a ``Clock`` (or a ``datetime`` produced by one), so a test
  can travel through 60 days in a few milliseconds.

``SystemClock`` is the production implementation. ``FakeClock`` is the
time-travel implementation used by tests (``advance``, ``travel``, ``set``) and
can optionally tick on every read so that consecutive calls are strictly
increasing.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol, Union, runtime_checkable

ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

_DEFAULT_FAKE_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

Delta = Union[timedelta, int, float]


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time. Always timezone-aware UTC."""
        ...


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` as timezone-aware UTC. A naive datetime is a bug, not a guess."""
    if not isinstance(dt, datetime):
        raise TypeError(f"expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime is not allowed: clocks are timezone-aware UTC")
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Canonical persisted form: UTC, microseconds, trailing Z."""
    return ensure_utc(dt).strftime(ISO_UTC_FORMAT)


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 UTC string (trailing ``Z`` or an explicit offset).

    A string without any offset is read as UTC, because the v1 log stored it that way.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_now(clock: Clock) -> str:
    """``to_iso(clock.now())``: the one-liner every module uses to stamp a row."""
    return to_iso(clock.now())


def _as_timedelta(delta: Delta) -> timedelta:
    if isinstance(delta, timedelta):
        return delta
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        raise TypeError(f"expected timedelta or seconds, got {type(delta).__name__}")
    return timedelta(seconds=float(delta))


class SystemClock:
    """Production clock: the real UTC time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return "SystemClock()"


class FakeClock:
    """Deterministic, mutable clock for tests (time travel).

    ``FakeClock()`` starts at 2026-01-01T00:00:00Z and stands still.
    ``FakeClock(auto_step=1)`` returns a time one second later on every read, which
    gives strictly increasing timestamps without any explicit ``advance`` calls.
    """

    def __init__(
        self,
        start: Union[datetime, str, None] = None,
        *,
        auto_step: Union[Delta, None] = None,
    ) -> None:
        if start is None:
            current = _DEFAULT_FAKE_START
        elif isinstance(start, str):
            current = parse_iso(start)
        else:
            current = ensure_utc(start)
        self._current = current
        self._auto_step = _as_timedelta(auto_step) if auto_step is not None else None
        if self._auto_step is not None and self._auto_step <= timedelta(0):
            raise ValueError("auto_step must be positive")
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            value = self._current
            if self._auto_step is not None:
                self._current = self._current + self._auto_step
            return value

    def peek(self) -> datetime:
        """Current time without ticking ``auto_step``."""
        with self._lock:
            return self._current

    def set(self, value: Union[datetime, str]) -> datetime:
        """Jump to an absolute time (forwards or backwards)."""
        target = parse_iso(value) if isinstance(value, str) else ensure_utc(value)
        with self._lock:
            self._current = target
            return self._current

    def advance(self, delta: Delta) -> datetime:
        """Move forward by a ``timedelta`` or a number of seconds. Returns the new time."""
        step = _as_timedelta(delta)
        if step < timedelta(0):
            raise ValueError("advance() only moves forward; use set() to go back")
        with self._lock:
            self._current = self._current + step
            return self._current

    def travel(
        self,
        *,
        days: float = 0.0,
        hours: float = 0.0,
        minutes: float = 0.0,
        seconds: float = 0.0,
    ) -> datetime:
        """Keyword form of ``advance``: ``clock.travel(days=7, hours=2)``."""
        return self.advance(timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds))

    def __repr__(self) -> str:
        return f"FakeClock({to_iso(self._current)})"
