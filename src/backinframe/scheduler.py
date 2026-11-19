"""When should the next photo go out?

Each recipient has: a daily time window, a number of photos per send-day,
"every N days" and a minimum gap between photos. Times are random but
deterministic per (recipient, day, settings), so a restart does not shuffle
the plan.

Spacing guarantee: the window is cut into ``per_day`` equal segments; each
photo gets the centre of its segment plus a jitter of at most
``(segment - gap) / 2``. Neighbours are therefore always >= ``gap`` apart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

MAX_PER_DAY = 24
MAX_EVERY_DAYS = 60


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    per_day: int = 1
    every_days: int = 1
    window_start: str = "09:00"
    window_end: str = "21:00"
    min_gap_minutes: int = 60

    @property
    def start(self) -> time:
        return parse_hhmm(self.window_start)

    @property
    def end(self) -> time:
        return parse_hhmm(self.window_end)

    @property
    def window_minutes(self) -> int:
        s, e = self.start, self.end
        return (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)

    @property
    def sends_per_day(self) -> float:
        return self.per_day / self.every_days

    def validate(self) -> Schedule:
        if not 1 <= self.per_day <= MAX_PER_DAY:
            raise ScheduleError(f"per_day must be between 1 and {MAX_PER_DAY}")
        if not 1 <= self.every_days <= MAX_EVERY_DAYS:
            raise ScheduleError(f"every_days must be between 1 and {MAX_EVERY_DAYS}")
        if self.min_gap_minutes < 0:
            raise ScheduleError("min_gap_minutes cannot be negative")
        if self.window_minutes <= 0:
            raise ScheduleError("window_end must be after window_start")
        if self.per_day > 1 and (self.per_day - 1) * self.min_gap_minutes > self.window_minutes:
            raise ScheduleError("too many photos for this window and minimum gap")
        return self


def parse_hhmm(value: str) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ScheduleError(f"invalid time {value!r}, expected HH:MM") from exc


def is_send_day(day: date, every_days: int, anchor: date) -> bool:
    return (day - anchor).days % every_days == 0


def plan_day(day: date, sched: Schedule, tz: tzinfo, anchor: date, seed: str) -> list[datetime]:
    """All send moments for one day (timezone-aware), sorted."""
    if not is_send_day(day, sched.every_days, anchor):
        return []
    rng = random.Random(f"{seed}|{day.isoformat()}")
    start = datetime.combine(day, sched.start)
    window = timedelta(minutes=sched.window_minutes)
    segment = window / sched.per_day
    gap = timedelta(minutes=sched.min_gap_minutes)
    jitter = max(timedelta(0), (segment - gap) / 2)
    moments = []
    for i in range(sched.per_day):
        centre = start + segment * i + segment / 2
        offset = rng.uniform(-1, 1) * jitter
        naive = centre + offset
        # Wall-clock time in the recipient's zone (handles DST transitions).
        moments.append(naive.replace(tzinfo=tz))
    return sorted(moments)


def next_send(
    after: datetime,
    sched: Schedule,
    tz: tzinfo,
    anchor: date,
    seed: str,
    paused_until: datetime | None = None,
    horizon_days: int = 400,
) -> datetime | None:
    """First planned moment strictly after ``after`` (and not before ``paused_until``)."""
    floor = after
    if paused_until and paused_until > floor:
        floor = paused_until
    local = floor.astimezone(tz)
    for offset in range(horizon_days):
        day = local.date() + timedelta(days=offset)
        for moment in plan_day(day, sched, tz, anchor, seed):
            if moment > floor:
                return moment
    return None
