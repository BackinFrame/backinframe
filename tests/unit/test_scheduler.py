import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backinframe.scheduler import (
    Schedule,
    ScheduleError,
    is_send_day,
    next_send,
    parse_hhmm,
    plan_day,
)

TZ = ZoneInfo("Europe/Amsterdam")
DAY = date(2026, 11, 7)


def test_parse_hhmm():
    assert parse_hhmm("09:30").hour == 9
    with pytest.raises(ScheduleError):
        parse_hhmm("9.30")
    with pytest.raises(ScheduleError):
        parse_hhmm("25:00")


@pytest.mark.parametrize("kwargs", [
    {"per_day": 0}, {"per_day": 25}, {"every_days": 0}, {"every_days": 61},
    {"window_start": "21:00", "window_end": "09:00"}, {"window_start": "10:00", "window_end": "10:00"},
    {"min_gap_minutes": -1},
    {"per_day": 5, "window_start": "09:00", "window_end": "10:00", "min_gap_minutes": 30},
])
def test_invalid_schedules(kwargs):
    with pytest.raises(ScheduleError):
        Schedule(**kwargs).validate()


def test_single_photo_inside_window():
    s = Schedule(per_day=1, window_start="09:00", window_end="21:00").validate()
    for seed in range(200):
        (moment,) = plan_day(DAY, s, TZ, DAY, str(seed))
        assert moment.tzinfo is TZ
        assert datetime(2026, 11, 7, 9, tzinfo=TZ) <= moment <= datetime(2026, 11, 7, 21, tzinfo=TZ)


def test_many_photos_respect_gap_and_window():
    s = Schedule(per_day=6, window_start="08:00", window_end="20:00", min_gap_minutes=90).validate()
    for seed in range(300):
        moments = plan_day(DAY, s, TZ, DAY, f"s{seed}")
        assert len(moments) == 6
        assert moments == sorted(moments)
        assert moments[0] >= datetime(2026, 11, 7, 8, tzinfo=TZ)
        assert moments[-1] <= datetime(2026, 11, 7, 20, tzinfo=TZ)
        for a, b in zip(moments, moments[1:], strict=False):
            assert b - a >= timedelta(minutes=90) - timedelta(microseconds=1)


def test_gap_exactly_filling_window():
    # 3 photos need 2 gaps of 30 min = 60 min; a 50 min window is too small
    with pytest.raises(ScheduleError):
        Schedule(per_day=3, window_start="10:00", window_end="10:50", min_gap_minutes=30).validate()
    # segment (30 min) == gap -> no jitter, evenly spaced
    ok = Schedule(per_day=3, window_start="10:00", window_end="11:30", min_gap_minutes=30).validate()
    moments = plan_day(DAY, ok, TZ, DAY, "x")
    assert [m.strftime("%H:%M") for m in moments] == ["10:15", "10:45", "11:15"]


def test_plan_is_deterministic_and_seed_dependent():
    s = Schedule(per_day=3).validate()
    assert plan_day(DAY, s, TZ, DAY, "a") == plan_day(DAY, s, TZ, DAY, "a")
    assert plan_day(DAY, s, TZ, DAY, "a") != plan_day(DAY, s, TZ, DAY, "b")
    assert plan_day(DAY, s, TZ, DAY, "a") != plan_day(DAY + timedelta(days=1), s, TZ, DAY, "a")


def test_every_n_days():
    anchor = date(2026, 1, 1)
    assert is_send_day(anchor, 3, anchor)
    assert not is_send_day(anchor + timedelta(days=1), 3, anchor)
    assert is_send_day(anchor + timedelta(days=6), 3, anchor)
    s = Schedule(every_days=3).validate()
    days_with_photos = [d for d in range(30) if plan_day(anchor + timedelta(days=d), s, TZ, anchor, "x")]
    assert days_with_photos == list(range(0, 30, 3))


def test_next_send_rolls_to_tomorrow_after_window():
    s = Schedule(per_day=1, window_start="09:00", window_end="10:00").validate()
    after = datetime(2026, 11, 7, 22, 0, tzinfo=TZ)
    nxt = next_send(after, s, TZ, DAY, "seed")
    assert nxt.date() == date(2026, 11, 8)
    assert 9 <= nxt.hour < 10


def test_next_send_same_day_before_window():
    s = Schedule(per_day=1, window_start="09:00", window_end="10:00").validate()
    nxt = next_send(datetime(2026, 11, 7, 6, 0, tzinfo=TZ), s, TZ, DAY, "seed")
    assert nxt.date() == DAY


def test_next_send_returns_each_moment_in_turn():
    s = Schedule(per_day=4, window_start="08:00", window_end="20:00", min_gap_minutes=60).validate()
    planned = plan_day(DAY, s, TZ, DAY, "k")
    t = datetime(2026, 11, 7, 0, 0, tzinfo=TZ)
    seen = []
    for _ in range(4):
        t = next_send(t, s, TZ, DAY, "k")
        seen.append(t)
    assert seen == planned


def test_pause_skips_until_paused_until():
    s = Schedule(per_day=1).validate()
    now = datetime(2026, 11, 7, 6, 0, tzinfo=TZ)
    paused = now + timedelta(days=7)
    nxt = next_send(now, s, TZ, DAY, "p", paused_until=paused)
    assert nxt >= paused


def test_next_send_from_other_timezone():
    s = Schedule(per_day=1, window_start="09:00", window_end="10:00").validate()
    utc_after = datetime(2026, 11, 7, 23, 30, tzinfo=ZoneInfo("UTC"))  # 00:30 local on the 8th
    nxt = next_send(utc_after, s, TZ, DAY, "z")
    assert nxt.date() == date(2026, 11, 8)


def test_dst_change_day_keeps_wall_clock_times():
    s = Schedule(per_day=1, window_start="09:00", window_end="10:00").validate()
    dst_day = date(2026, 3, 29)
    (moment,) = plan_day(dst_day, s, TZ, dst_day, "dst")
    assert moment.hour == 9
    assert moment.utcoffset() == timedelta(hours=2)


def test_sends_per_day():
    assert Schedule(per_day=2, every_days=1).sends_per_day == 2
    assert Schedule(per_day=1, every_days=4).sends_per_day == 0.25


def test_random_schedules_property():
    rng = random.Random(1)
    for i in range(300):
        start = rng.randint(0, 20)
        end = rng.randint(start + 1, 23)
        per_day = rng.randint(1, 6)
        window = (end - start) * 60
        gap = rng.randint(0, window // per_day)
        s = Schedule(per_day, rng.randint(1, 5), f"{start:02d}:00", f"{end:02d}:00", gap).validate()
        moments = plan_day(DAY, s, TZ, DAY, str(i))
        if not moments:
            continue
        for a, b in zip(moments, moments[1:], strict=False):
            assert (b - a) >= timedelta(minutes=gap) - timedelta(microseconds=1)
        assert all(start <= m.hour <= end for m in moments)
