import random
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backinframe.db import Media
from backinframe.selector import choose, cooldown_days, is_on_this_day, weights

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=TZ)


def m(i, path, taken=None):
    return Media(id=i, path=path, kind="image", size=1, mtime=0, taken_at=taken)


def test_on_this_day():
    today = date(2026, 11, 7)
    assert is_on_this_day(datetime(2019, 11, 7), today)
    assert is_on_this_day(datetime(2019, 11, 4), today)
    assert not is_on_this_day(datetime(2019, 11, 3), today)
    assert not is_on_this_day(datetime(2026, 11, 7), today)  # this year doesn't count
    assert not is_on_this_day(None, today)
    # around new year
    assert is_on_this_day(datetime(2020, 12, 30), date(2027, 1, 1))
    # leap day
    assert is_on_this_day(datetime(2020, 2, 29), date(2026, 2, 28))


def test_cooldown_scales_with_collection():
    assert cooldown_days(1, 1) == 0
    assert cooldown_days(20, 1) == 10
    assert cooldown_days(20, 4) == 2
    assert cooldown_days(10_000, 1) == 90


def test_empty_returns_none():
    assert choose([], {}, NOW, random.Random(0)) is None


def test_recently_sent_is_excluded():
    items = [m(1, "a/1.jpg"), m(2, "a/2.jpg"), m(3, "a/3.jpg"), m(4, "a/4.jpg")]
    history = {1: (1, NOW - timedelta(hours=1))}
    w = weights(items, history, NOW.date(), NOW)
    assert w[0] == 0 and all(x > 0 for x in w[1:])


def test_falls_back_when_everything_is_on_cooldown():
    items = [m(1, "1.jpg"), m(2, "2.jpg"), m(3, "3.jpg")]
    history = {i: (1, NOW - timedelta(minutes=5)) for i in (1, 2, 3)}
    rng = random.Random(3)
    assert choose(items, history, NOW, rng) is not None


def test_often_sent_items_get_lower_weight():
    items = [m(1, "1.jpg"), m(2, "2.jpg")]
    long_ago = NOW - timedelta(days=400)
    w = weights(items, {1: (4, long_ago)}, NOW.date(), NOW)
    assert w[0] == w[1] / 5


def test_on_this_day_is_favoured():
    items = [m(i, f"{i}.jpg", datetime(2020, 3, 1)) for i in range(1, 51)]
    items.append(m(99, "special.jpg", datetime(2021, 11, 7)))
    rng = random.Random(7)
    picks = Counter(choose(items, {}, NOW, rng).id for _ in range(5000))
    # uniform would be ~98; with a 6x boost we expect ~536
    assert picks[99] > 350


def test_folders_get_a_fair_share():
    big = [m(i, f"Groot/{i}.jpg") for i in range(1, 101)]
    small = [m(1000 + i, f"Klein/{i}.jpg") for i in range(4)]
    rng = random.Random(11)
    picks = Counter(choose(big + small, {}, NOW, rng).folder for _ in range(4000))
    # by file count Klein would get ~4%; sqrt weighting gives 2/12 ~ 17%
    share = picks["Klein"] / 4000
    assert 0.12 < share < 0.22


def test_deterministic_with_seeded_rng():
    items = [m(i, f"{i}.jpg") for i in range(30)]
    a = [choose(items, {}, NOW, random.Random(5)).id for _ in range(3)]
    b = [choose(items, {}, NOW, random.Random(5)).id for _ in range(3)]
    assert a == b
