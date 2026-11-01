from datetime import datetime, timedelta, timezone

import pytest

from clock import Clock, FakeClock, SystemClock, ensure_utc, iso_now, parse_iso, to_iso


def test_system_clock_is_timezone_aware_utc():
    now = SystemClock().now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - now).total_seconds()) < 5


def test_both_clocks_satisfy_the_protocol():
    assert isinstance(SystemClock(), Clock)
    assert isinstance(FakeClock(), Clock)


def test_iso_format_is_fixed_width_utc_with_z():
    assert to_iso(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)) == "2026-01-02T03:04:05.000006Z"
    plus_two = timezone(timedelta(hours=2))
    assert to_iso(datetime(2026, 1, 2, 5, 4, 5, tzinfo=plus_two)) == "2026-01-02T03:04:05.000000Z"


def test_iso_strings_sort_chronologically():
    clock = FakeClock("2026-01-01T00:00:00Z")
    stamps = []
    for step in (0, 0.000001, 1, 59.999999, 3600, 86400 * 400):
        clock.advance(step)
        stamps.append(iso_now(clock))
    assert stamps == sorted(stamps)


@pytest.mark.parametrize(
    "text",
    ["2026-10-31T18:09:00Z", "2026-10-31T18:09:00.000000Z", "2026-10-31T19:09:00+01:00", "2026-10-31T18:09:00"],
)
def test_parse_iso_accepts_z_offsets_and_naive_v1_strings(text):
    assert parse_iso(text) == datetime(2026, 10, 31, 18, 9, 0, tzinfo=timezone.utc)


def test_parse_iso_rejects_garbage():
    for bad in ("", "yesterday", None, 12):
        with pytest.raises((ValueError, TypeError)):
            parse_iso(bad)  # type: ignore[arg-type]


def test_naive_datetimes_are_a_bug():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))


def test_fake_clock_stands_still_until_moved():
    clock = FakeClock("2026-03-01T12:00:00Z")
    assert clock.now() == clock.now() == datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def test_fake_clock_time_travel():
    clock = FakeClock("2026-03-01T12:00:00Z")
    clock.advance(30)
    assert clock.now() == datetime(2026, 3, 1, 12, 0, 30, tzinfo=timezone.utc)
    clock.advance(timedelta(hours=1))
    clock.travel(days=60, minutes=1)
    assert clock.now() == datetime(2026, 4, 30, 13, 1, 30, tzinfo=timezone.utc)
    clock.set("2026-01-01T00:00:00Z")  # going back is explicit
    assert clock.now().year == 2026 and clock.now().month == 1
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_fake_clock_auto_step_is_strictly_increasing():
    clock = FakeClock("2026-03-01T00:00:00Z", auto_step=1)
    reads = [clock.now() for _ in range(5)]
    assert reads == sorted(set(reads))
    assert clock.peek() == reads[-1] + timedelta(seconds=1)
    with pytest.raises(ValueError):
        FakeClock(auto_step=0)


def test_advance_rejects_non_numbers():
    with pytest.raises(TypeError):
        FakeClock().advance("5")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FakeClock().advance(True)
