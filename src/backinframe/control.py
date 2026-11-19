"""Commands a recipient can send back (via the notification buttons)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .scheduler import MAX_EVERY_DAYS, MAX_PER_DAY, Schedule, ScheduleError

_WORDS = {
    # English, Dutch, German, French, Spanish, Italian, Portuguese, Polish, Russian, Chinese, Japanese
    "more": "more vaker meer mehr öfter oefter plus más mas più piu mais więcej wiecej częściej czesciej "
            "чаще больше 多一些 多 もっと 多く 増やす",
    "less": "less minder weniger seltener moins menos meno menos mniej rzadziej реже меньше 少一些 少 少なく 減らす",
    "pause": "pause pauze pausa pauza stop пауза 暂停 一時停止 停止",
    "resume": "resume start hervat weiter fortsetzen reprendre reanudar riprendi retomar wznów wznow "
              "продолжить 继续 再開",
}
ALIASES = {word: command for command, words in _WORDS.items() for word in words.split()}

PAUSE = timedelta(days=7)


@dataclass
class Outcome:
    command: str
    schedule: Schedule
    paused_until: datetime | None
    changed: bool
    limit_hit: str | None = None  # 'max' | 'min'


def parse(body: str) -> str | None:
    """'More', '/more', 'vaker!' and 'Mehr' all mean 'more'."""
    word = (body or "").strip().lower().lstrip("/").split("@", 1)[0].strip(" .!?。！")
    return ALIASES.get(word)


def _fits(s: Schedule) -> bool:
    try:
        s.validate()
        return True
    except ScheduleError:
        return False


def more(s: Schedule) -> Schedule | None:
    if s.every_days > 1:
        return replace(s, every_days=s.every_days - 1)
    if s.per_day >= MAX_PER_DAY:
        return None
    candidate = replace(s, per_day=s.per_day + 1)
    if _fits(candidate):
        return candidate
    # Squeeze the minimum gap so another photo fits in the window.
    gap = s.window_minutes // s.per_day
    candidate = replace(candidate, min_gap_minutes=gap)
    return candidate if _fits(candidate) else None


def less(s: Schedule) -> Schedule | None:
    if s.per_day > 1:
        return replace(s, per_day=s.per_day - 1)
    if s.every_days >= MAX_EVERY_DAYS:
        return None
    return replace(s, every_days=s.every_days + 1)


def apply(command: str, schedule: Schedule, paused_until: datetime | None, now: datetime) -> Outcome:
    if command == "more":
        new = more(schedule)
        if new is None:
            return Outcome(command, schedule, paused_until, False, "max")
        return Outcome(command, new, None, True)
    if command == "less":
        new = less(schedule)
        if new is None:
            return Outcome(command, schedule, paused_until, False, "min")
        return Outcome(command, new, paused_until, True)
    if command == "pause":
        return Outcome(command, schedule, now + PAUSE, True)
    if command == "resume":
        return Outcome(command, schedule, None, paused_until is not None)
    raise ValueError(f"unknown command {command!r}")
