"""Injectable UTC clock (S0 Contracts, section 6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "FakeClock", "to_utc_iso", "from_utc_iso"]

_DEFAULT_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if not isinstance(dt, datetime):
        raise TypeError(f"expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return "SystemClock()"


class FakeClock:
    def __init__(self, start: datetime | str | None = None) -> None:
        if start is None:
            self._now = _DEFAULT_START
        elif isinstance(start, str):
            self._now = from_utc_iso(start)
        else:
            self._now = _as_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = _as_utc(dt)

    def advance(
        self,
        *,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> datetime:
        self._now = self._now + timedelta(
            days=days, hours=hours, minutes=minutes, seconds=seconds
        )
        return self._now

    def __repr__(self) -> str:
        return f"FakeClock({to_utc_iso(self._now)!r})"


def to_utc_iso(dt: datetime) -> str:
    utc = _as_utc(dt).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def from_utc_iso(s: str) -> datetime:
    if not isinstance(s, str):
        raise TypeError(f"expected str, got {type(s).__name__}")
    text = s.strip()
    if not text:
        raise ValueError("empty ISO-8601 string")
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    return _as_utc(datetime.fromisoformat(text))
