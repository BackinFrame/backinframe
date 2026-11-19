"""Picks the next photo or video for a recipient — random, but a little smart.

* Photos taken "on this day" in an earlier year get a strong boost.
* Recently sent items are left out for a while (a cooldown that scales with
  the size of the collection), and often-sent items get a lower weight.
* Every subfolder (e.g. one per trip) gets a fair share, so one huge trip
  does not drown out the small ones.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from datetime import date, datetime, timedelta

from .db import Media

ON_THIS_DAY_BOOST = 6.0
ON_THIS_DAY_WINDOW = 3  # days either side
MAX_COOLDOWN_DAYS = 90


def is_on_this_day(taken: datetime | None, today: date, window: int = ON_THIS_DAY_WINDOW) -> bool:
    if taken is None or taken.year >= today.year:
        return False
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            anniversary = taken.date().replace(year=year)
        except ValueError:  # 29 February
            anniversary = date(year, 2, 28)
        if abs((anniversary - today).days) <= window:
            return True
    return False


def cooldown_days(n_items: int, sends_per_day: float) -> int:
    """Roughly: don't repeat an item before half the collection has passed by."""
    if n_items <= 1:
        return 0
    days = n_items / max(sends_per_day, 0.01) / 2
    return int(min(MAX_COOLDOWN_DAYS, days))


def weights(
    items: list[Media],
    history: dict[int, tuple[int, datetime]],
    today: date,
    now: datetime,
    sends_per_day: float = 1.0,
    boost: dict[int, float] | None = None,
) -> list[float]:
    cooldown = timedelta(days=cooldown_days(len(items), sends_per_day))
    folder_sizes = Counter(m.folder for m in items)

    def compute(apply_cooldown: bool) -> list[float]:
        out = []
        for m in items:
            count, last = history.get(m.id, (0, None))
            if apply_cooldown and last is not None and now - last < cooldown:
                out.append(0.0)
                continue
            w = 1.0 / math.sqrt(folder_sizes[m.folder])
            if is_on_this_day(m.taken_at, today):
                w *= ON_THIS_DAY_BOOST
            w /= 1 + count
            if boost:
                w *= boost.get(m.id, 1.0)
            out.append(w)
        return out

    result = compute(apply_cooldown=True)
    if not any(result):
        result = compute(apply_cooldown=False)
    return result


def choose(
    items: list[Media],
    history: dict[int, tuple[int, datetime]],
    now: datetime,
    rng: random.Random,
    sends_per_day: float = 1.0,
    boost: dict[int, float] | None = None,
) -> Media | None:
    """Pick one item. ``boost`` multiplies the weight of some items (e.g. ones with a heart);
    nothing uses it yet, so by default the choice is as before."""
    if not items:
        return None
    w = weights(items, history, now.date(), now, sends_per_day, boost)
    return rng.choices(items, weights=w, k=1)[0]
