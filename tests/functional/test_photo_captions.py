"""The admin can change the caption per photo: a title instead of the album name, another date (kept over
rescans, with the file's own date to go back to) and a short note under the date."""

from __future__ import annotations

import os
import random
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import TZ, make_image

from backinframe import caption
from backinframe.app import create_recipient_app
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

ADMIN = "http://localhost:8080"
APP = "https://photos.example.org"


@pytest.fixture
def svc(media_dir, fake_ntfy, clock, settings):
    from dataclasses import replace
    make_image(media_dir / "Chile 2025" / "lake.jpg", taken=datetime(2025, 3, 2, 15, 30))
    make_image(media_dir / "Chile 2025" / "snow.jpg", taken=datetime(2025, 3, 4))
    make_image(media_dir / "Rome_2019" / "forum.jpg", taken=datetime(2019, 6, 1))
    make_image(media_dir / "loose.jpg", taken=datetime(2020, 1, 1))
    s = BackInFrame(replace(settings, app_url=APP), db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                       rng=random.Random(1))
    s.rescan()
    yield s
    s.db.close()


def mid(svc, name):
    return next(r["id"] for r in svc.db.query("SELECT id, path FROM media") if r["path"].endswith(name))


@pytest.fixture
def admin(svc):
    with TestClient(create_app(svc), base_url=ADMIN) as c:
        yield c


def test_title_and_note_end_up_in_the_notification(svc, admin, fake_ntfy, clock):
    lake = mid(svc, "lake.jpg")
    admin.post(f"/photos/{lake}",
               data={"title": "Lago Grey", "note": "Met Vera, net voor de storm", "taken": "2025-03-02"})
    m = svc.db.media_by_id(lake)
    assert (m.title, m.note, m.date_edited) == ("Lago Grey", "Met Vera, net voor de storm", False)
    title, text = caption.build(m, clock().date(), "nl")
    assert title == "Lago Grey" and text.endswith("\nMet Vera, net voor de storm") and "2025" in text
    r = svc.create_recipient("Sanne", delivery="ntfy")
    for name in ("snow.jpg", "forum.jpg", "loose.jpg"):  # only the lake is left to send
        svc.db.execute("UPDATE media SET missing = 1 WHERE id = ?", (mid(svc, name),))
    assert svc.send_now(r.id).status == "ok"
    msg = [m for m in fake_ntfy.on(r.topic) if m.get("attachment")][-1]
    assert msg["title"] == "Lago Grey" and "Met Vera, net voor de storm" in msg["message"]


def test_empty_title_falls_back_to_the_album(svc, admin):
    snow = mid(svc, "snow.jpg")
    admin.post(f"/photos/{snow}", data={"title": "Eigen titel", "note": ""})
    admin.post(f"/photos/{snow}", data={"title": "   ", "note": ""})
    assert caption.build(svc.db.media_by_id(snow), datetime(2026, 1, 1).date(), "en")[0] == "Chile 2025"
    forum = mid(svc, "forum.jpg")
    assert caption.album_name(svc.db.media_by_id(forum), "en") == "Rome 2019"


def test_changed_date_survives_a_rescan_and_can_be_reset(svc, admin, media_dir):
    lake = mid(svc, "lake.jpg")
    admin.post(f"/photos/{lake}", data={"taken": "2014-07-21"})
    m = svc.db.media_by_id(lake)
    assert m.taken_at == datetime(2014, 7, 21, 15, 30) and m.date_edited
    assert m.taken_original == datetime(2025, 3, 2, 15, 30)
    # the file changes on disk: its new date is remembered, the admin's date stays
    path = media_dir / "Chile 2025" / "lake.jpg"
    make_image(path, taken=datetime(2025, 3, 3, 9, 0), color="teal")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    svc.rescan()
    m = svc.db.media_by_id(lake)
    assert m.taken_at == datetime(2014, 7, 21, 15, 30) and m.taken_original == datetime(2025, 3, 3, 9, 0)
    page = admin.get(f"/photos/{lake}").text
    assert 'name="reset_date"' in page and "2025" in page
    admin.post(f"/photos/{lake}", data={"taken": "2014-07-21", "reset_date": "1"})
    m = svc.db.media_by_id(lake)
    assert m.taken_at == datetime(2025, 3, 3, 9, 0) and not m.date_edited and m.taken_original is None


def test_this_day_years_ago_follows_the_changed_date(svc, admin, clock):
    forum = mid(svc, "forum.jpg")
    admin.post(f"/photos/{forum}", data={"taken": "2019-11-07"})
    _, text = caption.build(svc.db.media_by_id(forum), clock().date(), "en")
    assert "7 years ago" in text.lower() and "today" in text.lower()


@pytest.mark.parametrize("bad", ["2099-01-01", "1850-05-05", "07-11-2025", "not a date"])
def test_wrong_dates_are_refused(svc, admin, bad):
    lake = mid(svc, "lake.jpg")
    r = admin.post(f"/photos/{lake}", data={"taken": bad, "title": "x"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    m = svc.db.media_by_id(lake)
    assert not m.date_edited and m.title == ""


def test_input_is_cleaned_and_escaped(svc, admin):
    lake = mid(svc, "lake.jpg")
    admin.post(f"/photos/{lake}", data={"title": "<script>alert(1)</script>\x00\x1b" + "a" * 200,
                                        "note": "regel 1\nregel 2\t <b>vet</b>"})
    m = svc.db.media_by_id(lake)
    assert len(m.title) == 80 and "\x00" not in m.title and "\x1b" not in m.title
    assert m.note == "regel 1 regel 2 <b>vet</b>"
    page = admin.get(f"/photos/{lake}").text + admin.get("/photos").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page and "<b>vet</b>" not in page


def test_photo_list_by_album_and_pages(svc, admin, media_dir):
    page = admin.get("/photos").text
    assert page.count('class="thumblink"') == 4
    assert admin.get("/photos", params={"folder": "Chile 2025"}).text.count('class="thumblink"') == 2
    assert admin.get("/photos", params={"folder": ""}).text.count('class="thumblink"') == 1  # main folder
    assert admin.get("/photos", params={"folder": "../etc"}).text.count('class="thumblink"') == 4  # unknown: all
    for i in range(61):
        make_image(media_dir / "Big_%" / f"{i:03d}.jpg", size=(40, 30), taken=datetime(2021, 1, 1))
    svc.rescan()
    first = admin.get("/photos", params={"folder": "Big_%"}).text
    assert first.count('class="thumblink"') == 60 and "page=2" in first
    assert admin.get("/photos", params={"folder": "Big_%", "page": 2}).text.count('class="thumblink"') == 1
    assert admin.get("/photos", params={"folder": "Big"}).status_code == 200


def test_edited_mark_and_back_link_keep_the_filter(svc, admin):
    lake = mid(svc, "lake.jpg")
    admin.post(f"/photos/{lake}", data={"note": "x", "folder": "Chile 2025", "page": "1"}, follow_redirects=False)
    listing = admin.get("/photos", params={"folder": "Chile 2025"}).text
    assert listing.count('class="edited"') == 1
    page = admin.get(f"/photos/{lake}", params={"folder": "Chile 2025"}).text
    assert 'href="/photos?folder=Chile%202025&amp;page=1"' in page


def test_web_app_gallery_shows_the_new_caption(svc, admin, clock):
    r = svc.create_recipient("Oma")
    assert svc.send_now(r.id).status == "ok"
    sent = svc.db.query("SELECT media_id FROM sends WHERE recipient_id = ? ORDER BY id DESC", (r.id,))[0]["media_id"]
    admin.post(f"/photos/{sent}", data={"title": "Bij oma", "note": "Haar 80ste verjaardag"})
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        page = phone.get(f"/a/{r.app_token}/").text
    assert "Bij oma" in page and "Haar 80ste verjaardag" in page


def test_photo_pages_need_login_and_same_origin(svc, settings, media_dir, fake_ntfy, clock):
    from dataclasses import replace
    s = BackInFrame(replace(settings, admin_password="correct horse battery"), db=svc.db,
                       ntfy=fake_ntfy.client(), clock=clock, rng=random.Random(1))
    with TestClient(create_app(s), base_url=ADMIN) as c:
        assert c.get("/photos", follow_redirects=False).status_code in (302, 303)
        assert c.post("/photos/1", data={"title": "x"}, follow_redirects=False).status_code in (302, 303, 401, 403)
    with TestClient(create_app(svc), base_url=ADMIN) as c:
        r = c.post("/photos/1", data={"title": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
    assert svc.db.media_by_id(1).title == ""


def test_unknown_photo_is_404(admin):
    assert admin.get("/photos/999").status_code == 404
    assert admin.post("/photos/999", data={"title": "x"}).status_code == 404


def test_old_database_gets_the_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, "
                      "size INTEGER NOT NULL, mtime REAL NOT NULL, taken_at TEXT, missing INTEGER NOT NULL DEFAULT 0, "
                      "broken INTEGER NOT NULL DEFAULT 0);"
                      "INSERT INTO media(path, kind, size, mtime, taken_at) "
                      "VALUES('a/b.jpg', 'image', 1, 1, '2020-01-01T10:00:00');")
    con.commit()
    con.close()
    db = Database(str(path))
    m = db.media_by_id(1)
    assert (m.title, m.note, m.date_edited, m.taken_original) == ("", "", False, None)
    db.close()
    assert TZ  # the helpers' time zone is what the clock uses
