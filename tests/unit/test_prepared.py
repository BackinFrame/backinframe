"""Reactions (a heart) and the year review: the groundwork, and that switched off nothing changes."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import TZ, make_image

from backinframe import review, selector
from backinframe.app import create_recipient_app
from backinframe.db import Database, Media
from backinframe.service import BackInFrame

APP = "https://photos.example.org"


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock, settings):
    s = replace(settings, app_url=APP)
    service = BackInFrame(s, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock, rng=random.Random(1))
    for album, n in (("Chile 2025", 3), ("Rome 2019", 2)):
        for i in range(n):
            make_image(media_dir / album / f"{i}.jpg", taken=datetime(2019 if "Rome" in album else 2025, 5, i + 1))
    service.rescan()
    yield service
    service.db.close()


def on(service: BackInFrame, **flags) -> BackInFrame:
    service.settings = replace(service.settings, **flags)
    return service


# -- reactions -------------------------------------------------------------------------

def test_reactions_switched_off_change_nothing(svc):
    on(svc, reactions_enabled=False)
    r = svc.create_recipient("Oma")
    send_id = svc.db.last_send_id(r.id) or (svc.send_now(r.id) and svc.db.last_send_id(r.id))
    assert svc.react(r, send_id, "heart") is False
    assert svc.reaction_boost(r) is None
    with TestClient(create_recipient_app(svc), base_url=APP) as c:
        assert c.post(f"/a/{r.app_token}/react/{send_id}", json={"reaction": "heart"}).status_code == 404
    assert svc.db.reactions() == {}


def test_a_heart_is_stored_on_the_own_memory_only(svc):
    on(svc, reactions_enabled=True)
    oma, opa = svc.create_recipient("Oma"), svc.create_recipient("Opa")
    svc.send_now(oma.id)
    svc.send_now(opa.id)
    send_id = svc.db.last_send_id(oma.id)
    with TestClient(create_recipient_app(svc), base_url=APP) as c:
        assert c.post(f"/a/{oma.app_token}/react/{send_id}", json={"reaction": "heart"}).json()["ok"]
        assert c.post(f"/a/{opa.app_token}/react/{send_id}", json={"reaction": "heart"}).status_code == 404
        assert c.post(f"/a/{oma.app_token}/react/{send_id}", json={"reaction": "angry"}).status_code == 400
        assert c.post(f"/a/{oma.app_token}/react/{send_id}", json={"reaction": "heart"},
                      headers={"Origin": "https://evil.example"}).status_code == 403
    media_id = svc.db.gallery_item(oma.id, send_id)["media_id"]
    assert svc.db.reactions(oma.id) == {media_id: "heart"}
    assert svc.reaction_boost(oma) == {media_id: 1.5}
    assert svc.reaction_boost(opa) == {}
    assert svc.react(oma, send_id, "") and svc.db.reactions(oma.id) == {}  # taken back


def test_boost_changes_nothing_when_empty_and_favours_hearts_when_set():
    now = datetime(2026, 11, 7, 12, tzinfo=TZ)
    items = [Media(id=i, path=f"A/{i}.jpg", kind="image", size=1, mtime=0, taken_at=datetime(2020, 1, 1))
             for i in range(1, 5)]
    plain = selector.weights(items, {}, now.date(), now)
    assert selector.weights(items, {}, now.date(), now, boost={}) == plain
    boosted = selector.weights(items, {}, now.date(), now, boost={2: 1.5})
    assert boosted[1] == pytest.approx(plain[1] * 1.5) and boosted[0] == plain[0]


def test_an_old_database_gets_the_reaction_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE sends (id INTEGER PRIMARY KEY, recipient_id INTEGER NOT NULL, media_id INTEGER, "
                       "sent_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT);"
                       "INSERT INTO sends(recipient_id, media_id, sent_at, status) VALUES (1, 1, '2026-01-01', 'ok');")
    conn.close()
    db = Database(path)
    assert db.query("SELECT reaction, reacted_at FROM sends")[0]["reaction"] == ""
    db.close()


# -- year review -----------------------------------------------------------------------

def test_year_review_counts_and_picks(svc, clock):
    r = svc.create_recipient("Oma", delivery="ntfy", welcome=False)
    for month in range(1, 13):
        clock.set(datetime(2026, month, 3, 10, tzinfo=TZ))
        svc.send_now(r.id)
    clock.set(datetime(2027, 1, 2, 10, tzinfo=TZ))
    svc.send_now(r.id)  # next year: not in the 2026 review
    rev = svc.year_review(r, 2026)
    assert rev.total == 12 and rev.photos == 12 and rev.videos == 0
    assert rev.months == [1] * 12
    assert sum(n for _, n in rev.albums) == 12 and {a for a, _ in rev.albums} <= {"Chile 2025", "Rome 2019"}
    assert 1 <= len(rev.picks) <= 5  # five different photos in the collection
    assert len({p.media_id for p in rev.picks}) == len(rev.picks)
    assert rev.first_sent.month == 1 and rev.last_sent.month == 12
    assert svc.year_review(r, 2020) is None
    assert svc.year_review(r, 2026) == rev  # the same every time


def test_review_picks_hearts_first_and_spreads_out():
    def it(i, month, album, reaction="", kind="image"):
        return review.ReviewItem(send_id=i, media_id=i, kind=kind, album=album, taken_at=None,
                                 sent_at=datetime(2026, month, 1), reaction=reaction)
    items = [it(1, 1, "A"), it(2, 1, "A"), it(3, 2, "A"), it(4, 3, "A"), it(5, 4, "B", kind="video"),
             it(6, 5, "B", reaction="heart"), it(7, 6, "C")]
    picks = review.pick(items, 4)
    assert [p.send_id for p in picks] == [1, 3, 6, 7]  # one a month, max two per album, heart in, video last


@pytest.mark.parametrize("path,album", [("Chile 2025/a.jpg", "Chile 2025"), ("@immich/Chile 2025/a.jpg", "Chile 2025"),
                                        ("Trips/Rome/a.jpg", "Rome"), ("a.jpg", "")])
def test_album_of(path, album):
    assert review.album_of(path) == album
