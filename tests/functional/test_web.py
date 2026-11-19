"""Functional tests of the admin pages, driven through HTTP like a browser would."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import make_image

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app


@pytest.fixture
def browser(service):
    with TestClient(create_app(service), follow_redirects=True) as client:
        yield client


def rid_from(url) -> int:
    return int(re.search(r"/recipients/(\d+)", str(url)).group(1))


def test_home_page_shows_collection(browser, service, media_dir):
    make_image(media_dir / "Chili" / "a.jpg", taken=datetime(2024, 10, 2))
    make_image(media_dir / "Chili" / "b.jpg", taken=datetime(2024, 10, 3))
    resp = browser.post("/rescan")
    assert resp.status_code == 200
    assert "Scan klaar: 2 nieuw" in resp.text
    assert "<strong>2</strong> foto&#39;s" in resp.text or "<strong>2</strong> foto's" in resp.text
    assert "Nog geen ontvangers" in resp.text


def test_full_recipient_flow(browser, service, fake_ntfy, media_dir):
    make_image(media_dir / "Chili" / "a.jpg", taken=datetime(2024, 10, 2))
    browser.post("/rescan")

    # add a recipient -> lands on their page with a QR code and the topic
    resp = browser.post("/recipients", data={"name": "Oma Riet"})
    assert resp.status_code == 200
    rid = rid_from(resp.url)
    r = service.db.recipient(rid)
    assert "<svg" in resp.text
    assert r.topic in resp.text
    assert "https://ntfy.test" in resp.text
    assert "Aanmelden op de telefoon" in resp.text
    assert len(fake_ntfy.on(r.topic)) == 1  # welcome message

    # it shows up on the home page with its next moment
    home = browser.get("/")
    assert "Oma Riet" in home.text and "Volgende" in home.text

    # change settings
    resp = browser.post(f"/recipients/{rid}", data={
        "name": "Oma", "per_day": "3", "every_days": "1", "window_start": "08:00",
        "window_end": "20:00", "min_gap_minutes": "90", "enabled": "1",
    })
    assert "Opgeslagen." in resp.text
    r = service.db.recipient(rid)
    assert (r.name, r.per_day, r.window_start, r.min_gap_minutes) == ("Oma", 3, "08:00", 90)
    assert "3 herinneringen per dag" in resp.text

    # send one right now
    resp = browser.post(f"/recipients/{rid}/send")
    assert "Verstuurd." in resp.text
    last = fake_ntfy.on(r.topic)[-1]
    assert last["attachment"]["name"] == "a.jpg"
    assert "Chili/a.jpg" in resp.text  # history

    # pause and resume from the page
    resp = browser.post(f"/recipients/{rid}/command/pause")
    assert "Gepauzeerd tot" in resp.text
    resp = browser.post(f"/recipients/{rid}/command/resume")
    assert "Gepauzeerd tot" not in resp.text

    # delete
    resp = browser.post(f"/recipients/{rid}/delete")
    assert "Nog geen ontvangers" in resp.text
    assert service.db.recipient(rid) is None


def test_qr_code_is_an_android_deep_link(browser, service, monkeypatch):
    from backinframe import web

    seen = []
    real = web.qr_svg
    monkeypatch.setattr(web, "qr_svg", lambda data: seen.append(data) or real(data))
    page = browser.post("/recipients", data={"name": "Oma"})
    r = service.db.recipient(rid_from(page.url))
    assert seen == [f"ntfy://ntfy.test/{r.topic}?display=Back%20in%20Frame"]
    assert "Android" in page.text and "iPhone" in page.text and r.topic in page.text


def test_invalid_settings_show_an_error(browser, service):
    rid = rid_from(browser.post("/recipients", data={"name": "X"}).url)
    resp = browser.post(f"/recipients/{rid}", data={
        "name": "X", "per_day": "2", "every_days": "1", "window_start": "20:00",
        "window_end": "08:00", "min_gap_minutes": "60", "enabled": "1",
    })
    assert 'role="alert"' in resp.text
    assert service.db.recipient(rid).window_start == "09:00"
    resp = browser.post(f"/recipients/{rid}", data={
        "name": "X", "per_day": "veel", "every_days": "1", "window_start": "09:00",
        "window_end": "21:00", "min_gap_minutes": "60",
    })
    assert "not a number" in resp.text


def test_empty_name_rejected(browser, service):
    resp = browser.post("/recipients", data={"name": "   "})
    assert 'role="alert"' in resp.text
    assert service.db.recipients() == []


def test_send_without_photos_says_so(browser):
    rid = rid_from(browser.post("/recipients", data={"name": "X"}).url)
    resp = browser.post(f"/recipients/{rid}/send")
    assert "Geen foto&#39;s gevonden" in resp.text or "Geen foto's gevonden" in resp.text


def test_unknown_recipient_404(browser):
    assert browser.get("/recipients/999").status_code == 404
    assert browser.post("/recipients/999/send").status_code == 404


def test_unknown_command_400(browser):
    rid = rid_from(browser.post("/recipients", data={"name": "X"}).url)
    assert browser.post(f"/recipients/{rid}/command/dance").status_code == 400


def test_health(browser):
    resp = browser.get("/healthz")
    assert resp.json()["status"] == "ok"


def test_cross_site_post_refused(browser):
    resp = browser.post("/rescan", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_password_protection(tmp_path, fake_ntfy, clock):
    s = Settings(media_dir=tmp_path, data_dir=tmp_path, ntfy_url="https://ntfy.test",
                 admin_password="geheim").validate()
    svc = BackInFrame(s, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    with TestClient(create_app(svc)) as c:
        first = c.get("/", follow_redirects=False)
        assert first.status_code == 303 and first.headers["location"] == "/login?next=%2F"
        assert c.get("/healthz").status_code == 200
        assert c.post("/login", data={"password": "fout"}).status_code == 400
        assert c.post("/login", data={"password": "geheim"}).url.path == "/"
        assert c.get("/").status_code == 200


def test_english_ui(tmp_path, fake_ntfy, clock):
    s = Settings(media_dir=tmp_path, data_dir=tmp_path, ntfy_url="https://ntfy.test", language="en").validate()
    svc = BackInFrame(s, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    with TestClient(create_app(svc)) as c:
        assert "No recipients yet" in c.get("/").text
