from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backinframe import control
from backinframe.scheduler import Schedule

NOW = datetime(2026, 11, 7, 12, tzinfo=ZoneInfo("Europe/Amsterdam"))


@pytest.mark.parametrize("body,expected", [
    ("more", "more"), ("Vaker", "more"), (" MEER ", "more"),
    ("less", "less"), ("minder", "less"),
    ("pause", "pause"), ("pauze", "pause"),
    ("resume", "resume"), ("hervat", "resume"),
    ("hallo", None), ("", None), (None, None),
])
def test_parse(body, expected):
    assert control.parse(body) == expected


def test_more_steps_from_every_n_days_to_per_day():
    s = Schedule(per_day=1, every_days=3)
    s = control.more(s)
    assert (s.per_day, s.every_days) == (1, 2)
    s = control.more(control.more(s))
    assert (s.per_day, s.every_days) == (2, 1)


def test_less_steps_from_per_day_to_every_n_days():
    s = Schedule(per_day=2, every_days=1)
    s = control.less(s)
    assert (s.per_day, s.every_days) == (1, 1)
    s = control.less(s)
    assert (s.per_day, s.every_days) == (1, 2)


def test_more_squeezes_gap_when_needed():
    s = Schedule(per_day=2, window_start="09:00", window_end="11:00", min_gap_minutes=120)
    new = control.more(s)
    assert new.per_day == 3
    new.validate()


def test_limits():
    assert control.more(Schedule(per_day=24, window_start="00:00", window_end="23:59", min_gap_minutes=0)) is None
    assert control.less(Schedule(per_day=1, every_days=60)) is None
    out = control.apply("less", Schedule(per_day=1, every_days=60), None, NOW)
    assert out.limit_hit == "min" and not out.changed


def test_pause_and_resume():
    s = Schedule()
    out = control.apply("pause", s, None, NOW)
    assert out.paused_until == NOW + timedelta(days=7) and out.changed
    out = control.apply("resume", s, out.paused_until, NOW)
    assert out.paused_until is None and out.changed
    assert not control.apply("resume", s, None, NOW).changed


def test_more_also_ends_pause():
    out = control.apply("more", Schedule(), NOW + timedelta(days=3), NOW)
    assert out.paused_until is None


def test_unknown_command():
    with pytest.raises(ValueError):
        control.apply("dance", Schedule(), None, NOW)
