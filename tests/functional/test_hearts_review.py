"""Hearts in the web app, the hearts overview on the admin page, and the year review."""

from __future__ import annotations

import io
import random
import re
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import TZ, make_image
from PIL import Image

from backinframe import insights, review
from backinframe.app import create_recipient_app
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

APP = "https://photos.example.org"


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock, settings):
    s = replace(settings, app_url=APP)
    for album, n in (("Chile 2025", 6), ("Rome 2019", 4)):
        for i in range(n):
            make_image(media_dir / album / f"{i}.jpg", taken=datetime(2019 if "Rome" in album else 2025, 5, i + 1),
                       gps=(41.9, 12.5))
    service = BackInFrame(s, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                             rng=random.Random(2))
    service.rescan()
    yield service
    service.db.close()


def send_many(svc, r, n, clock, hours=6):
    for _ in range(n):
        assert svc.send_now(r.id).status == "ok"
        clock.advance(hours=hours)


# -- hearts in the web app ---------------------------------------------------------------------------

def test_web_app_shows_heart_buttons_and_tells_about_them(svc, clock):
    oma = svc.create_recipient("Oma")
    send_many(svc, oma, 2, clock)
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        page = phone.get(f"/a/{oma.app_token}/").text
        assert page.count('class="heart"') == 2 and page.count('aria-pressed="false"') == 2
        assert "een hartje hebben" in page  # transparency for the recipient
        send_id = svc.db.last_send_id(oma.id)
        assert phone.post(f"/a/{oma.app_token}/react/{send_id}", json={"reaction": "heart"}).status_code == 200
        assert phone.get(f"/a/{oma.app_token}/").text.count('aria-pressed="false"') == 1
        phone.post(f"/a/{oma.app_token}/react/{send_id}", json={"reaction": ""})
        assert phone.get(f"/a/{oma.app_token}/").text.count('aria-pressed="false"') == 2


def test_hearts_switched_off_hide_the_buttons(svc, clock):
    svc.settings = replace(svc.settings, reactions_enabled=False)
    oma = svc.create_recipient("Oma")
    send_many(svc, oma, 1, clock)
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        assert 'class="heart"' not in phone.get(f"/a/{oma.app_token}/").text


def test_a_heart_makes_a_photo_come_back_more_often(svc, clock):
    oma = svc.create_recipient("Oma")
    send_many(svc, oma, 1, clock)
    send_id = svc.db.last_send_id(oma.id)
    media_id = svc.db.query("SELECT media_id FROM sends WHERE id = ?", (send_id,))[0]["media_id"]
    assert svc.react(oma, send_id, "heart")
    assert svc.reaction_boost(oma) == {media_id: 1.5}


# -- the hearts overview -----------------------------------------------------------------------------

def test_overview_counts_only_web_app_memories(svc, clock):
    oma, opa = svc.create_recipient("Oma"), svc.create_recipient("Opa")
    mail = svc.create_recipient("Vera", delivery="ntfy")
    send_many(svc, oma, 4, clock)
    send_many(svc, opa, 3, clock)
    send_many(svc, mail, 2, clock)
    first = svc.db.query("SELECT id, media_id FROM sends WHERE recipient_id = ? ORDER BY id", (oma.id,))
    svc.react(oma, first[0]["id"], "heart")
    svc.react(oma, first[1]["id"], "heart")
    opa_sends = svc.db.query("SELECT id, media_id FROM sends WHERE recipient_id = ? ORDER BY id", (opa.id,))
    svc.react(opa, opa_sends[0]["id"], "heart")
    o = insights.hearts(svc.db.app_sends())
    assert o.sent == 7 and o.hearted == 3 and o.rate == 43
    assert {p.name: (p.sent, p.hearted) for p in o.recipients} == {"Oma": (4, 2), "Opa": (3, 1)}
    assert sum(a.sent for a in o.albums) == 7
    assert all(n in ("Oma", "Opa") for p in o.loved for n in p.names)


def test_overview_lists_photos_sent_often_without_a_heart():
    rows = []
    for i in range(4):
        rows.append({"send_id": i, "sent_at": "2026-01-01", "reaction": "", "reacted_at": None, "recipient_id": 1,
                     "recipient": "Oma", "media_id": 7, "kind": "image", "path": "Rome/a.jpg", "taken_at": None})
    rows.append({"send_id": 9, "sent_at": "2026-01-02", "reaction": "heart", "reacted_at": "2026-01-02T10:00:00",
                 "recipient_id": 2, "recipient": "Opa", "media_id": 8, "kind": "image", "path": "Chile/b.jpg",
                 "taken_at": "2025-05-01T10:00:00"})
    o = insights.hearts(rows)
    assert [(u.media_id, u.sent) for u in o.unloved] == [(7, 4)]
    assert [(p.media_id, p.names) for p in o.loved] == [(8, ["Opa"])]
    assert [(a.album, a.rate) for a in o.albums] == [("Chile", 100), ("Rome", 0)]


def test_admin_hearts_page_and_thumbnails(svc, clock):
    oma = svc.create_recipient("Oma")
    send_many(svc, oma, 3, clock)
    send_id = svc.db.last_send_id(oma.id)
    svc.react(oma, send_id, "heart")
    media_id = svc.db.query("SELECT media_id FROM sends WHERE id = ?", (send_id,))[0]["media_id"]
    with TestClient(create_app(svc)) as admin:
        page = admin.get("/hearts").text
        assert "Meest geliefd" in page and f'/thumb/{media_id}"' in page and "Oma" in page
        thumb = admin.get(f"/thumb/{media_id}")
        assert thumb.headers["content-type"] == "image/jpeg"
        img = Image.open(io.BytesIO(thumb.content))
        assert max(img.size) <= 360 and not img.getexif().get_ifd(0x8825)  # small, no location
        assert admin.get("/thumb/999999").status_code == 404
        assert "Hartjes" in admin.get("/").text  # in the menu


def test_hearts_page_explains_when_nobody_uses_the_web_app(svc):
    svc.create_recipient("Vera", delivery="ntfy")
    with TestClient(create_app(svc)) as admin:
        assert "Nog geen ontvanger gebruikt die" in admin.get("/hearts").text


def test_hearts_page_and_thumbs_need_the_login(svc):
    svc.settings = replace(svc.settings, admin_password="a long admin password")
    with TestClient(create_app(svc)) as anon:
        assert anon.get("/hearts", follow_redirects=False).status_code == 303
        assert anon.get("/thumb/1", follow_redirects=False).status_code == 303
        assert anon.get("/review", follow_redirects=False).status_code == 303


# -- the year review ---------------------------------------------------------------------------------

def a_year_of_memories(svc, clock, r, n=8):
    clock.set(datetime(2025, 3, 1, 10, tzinfo=TZ))
    send_many(svc, r, n, clock, hours=24 * 20)


def test_review_needs_enough_memories(svc, clock):
    oma = svc.create_recipient("Oma")
    clock.set(datetime(2025, 6, 1, 10, tzinfo=TZ))
    send_many(svc, oma, review.MIN_MEMORIES - 1, clock)
    assert svc.year_review(oma, 2025) is None
    assert svc.send_review(oma, 2025).status == "empty"


def test_review_goes_out_once_at_the_first_moment_in_january(svc, clock, fake_ntfy):
    opa = svc.create_recipient("Opa", delivery="ntfy")
    a_year_of_memories(svc, clock, opa)
    clock.set(datetime(2026, 1, 3, 23, tzinfo=TZ))
    svc.db.update_recipient(opa.id, next_send_at=datetime(2026, 1, 3, 22, tzinfo=TZ))
    before = len(fake_ntfy.messages)
    assert svc.tick() == 1
    msg = fake_ntfy.messages[-1]
    assert msg["title"] == "Jouw 2025 in herinneringen" and "Herinneringen dit jaar: 8" in msg["message"]
    collage = Image.open(io.BytesIO(fake_ntfy.attachments[msg["id"]]))
    assert collage.format == "JPEG" and not collage.getexif()  # no metadata at all
    assert svc.review_sent(opa, 2025) is not None
    assert len(fake_ntfy.messages) == before + 1
    svc.db.update_recipient(opa.id, next_send_at=datetime(2026, 1, 3, 23, tzinfo=TZ))
    svc.tick()  # the next moment is an ordinary memory again
    assert fake_ntfy.messages[-1].get("title") != "Jouw 2025 in herinneringen"


def test_no_review_after_the_first_two_weeks_or_when_turned_off(svc, clock, fake_ntfy):
    opa = svc.create_recipient("Opa", delivery="ntfy")
    a_year_of_memories(svc, clock, opa)
    assert not svc.review_due(opa, datetime(2026, 1, 20, 10, tzinfo=TZ))
    assert svc.review_due(opa, datetime(2026, 1, 2, 10, tzinfo=TZ))
    svc.db.update_recipient(opa.id, year_review=False)
    assert not svc.review_due(svc.db.recipient(opa.id), datetime(2026, 1, 2, 10, tzinfo=TZ))
    svc.db.update_recipient(opa.id, year_review=True)
    svc.settings = replace(svc.settings, year_review_enabled=False)
    assert not svc.review_due(svc.db.recipient(opa.id), datetime(2026, 1, 2, 10, tzinfo=TZ))


def test_web_app_recipients_get_a_year_page(svc, clock):
    oma = svc.create_recipient("Oma")
    a_year_of_memories(svc, clock, oma)
    first = svc.db.query("SELECT id FROM sends WHERE recipient_id = ? ORDER BY id", (oma.id,))[0]["id"]
    svc.react(oma, first, "heart")
    clock.set(datetime(2026, 1, 2, 10, tzinfo=TZ))
    assert svc.send_review(oma, 2025).status == "ok"
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        page = phone.get(f"/a/{oma.app_token}/year/2025")
        assert page.status_code == 200 and "Jouw 2025 in herinneringen" in page.text
        assert len(re.findall(r'<figure>', page.text)) >= 6 and "met een hartje" in page.text
        assert phone.get(f"/a/{oma.app_token}/year/2025/collage.jpg").headers["content-type"] == "image/jpeg"
        assert phone.get(f"/a/{oma.app_token}/year/2024").status_code == 404
        assert phone.get("/a/wrong-token/year/2025").status_code == 404
        assert phone.get("/a/wrong-token/year/2025/collage.jpg").status_code == 404


def test_admin_review_page_and_send_button(svc, clock, fake_ntfy):
    opa = svc.create_recipient("Opa", delivery="ntfy")
    a_year_of_memories(svc, clock, opa)
    clock.set(datetime(2025, 12, 20, 10, tzinfo=TZ))
    with TestClient(create_app(svc)) as admin:
        page = admin.get("/review?year=2025").text
        assert "Jaaroverzicht 2025" in page and "Jouw 2025 in herinneringen" in page
        done = admin.post(f"/recipients/{opa.id}/review", data={"year": "2025"})
        assert "Jaaroverzicht verstuurd naar Opa" in done.text and "Verstuurd op" in done.text
        assert fake_ntfy.messages[-1]["title"] == "Jouw 2025 in herinneringen"


def test_year_review_setting_per_recipient_is_saved(svc):
    opa = svc.create_recipient("Opa", delivery="ntfy")
    form = {"name": "Opa", "per_day": "1", "every_days": "1", "window_start": "09:00", "window_end": "21:00",
            "min_gap_minutes": "60", "enabled": "1", "delivery": "ntfy"}
    with TestClient(create_app(svc)) as admin:
        assert "In januari een jaaroverzicht sturen" in admin.get(f"/recipients/{opa.id}").text
        admin.post(f"/recipients/{opa.id}", data=form)
        assert svc.db.recipient(opa.id).year_review is False
        admin.post(f"/recipients/{opa.id}", data={**form, "year_review": "1"})
        assert svc.db.recipient(opa.id).year_review is True


def test_tapping_the_heart_in_a_real_browser(svc, clock):
    """The button works with the strict CSP: aria-pressed flips and the heart is stored."""
    from a11y import CHROME, _in_thread, _serve_from, require_browser

    require_browser()
    from playwright.sync_api import sync_playwright

    oma = svc.create_recipient("Oma")
    send_many(svc, oma, 1, clock)
    send_id = svc.db.last_send_id(oma.id)

    def run():
        with TestClient(create_recipient_app(svc), base_url=APP) as phone, sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            page = browser.new_context().new_page()
            page.route(APP + "/**", _serve_from(phone, APP))
            page.goto(f"{APP}/a/{oma.app_token}/")
            button = page.locator(f'[data-react="{send_id}"]')
            button.click()
            page.wait_for_function(f"document.querySelector('[data-react=\"{send_id}\"]')"
                                   ".getAttribute('aria-pressed') === 'true'")
            pressed = button.get_attribute("aria-pressed")
            browser.close()
            return pressed

    assert _in_thread(run) == "true"
    assert svc.db.reactions(oma.id)
