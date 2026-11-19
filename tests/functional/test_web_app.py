"""The recipient web app, driven over HTTP like a phone would, with a fake push service."""

from __future__ import annotations

import io
import random
import sqlite3
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import PushBrowser, make_image, verify_vapid
from PIL import Image

from backinframe.app import create_recipient_app
from backinframe.config import ConfigError, Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

PUSH_HOST = "push.test"


class PushService:
    """Plays Apple/Google: receives encrypted pushes for the browsers it knows."""

    def __init__(self):
        self.browsers: dict[str, PushBrowser] = {}
        self.received: list[tuple[str, dict, str]] = []
        self.gone: set[str] = set()

    def browser(self) -> PushBrowser:
        b = PushBrowser(f"https://{PUSH_HOST}/send/{len(self.browsers)}")
        self.browsers[b.endpoint] = b
        return b

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = str(request.url)
        if endpoint in self.gone:
            return httpx.Response(410)
        message = self.browsers[endpoint].decrypt(request.content)
        self.received.append((endpoint, message, request.headers["authorization"]))
        return httpx.Response(201)


@pytest.fixture
def app_settings(tmp_path, media_dir) -> Settings:
    return Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                    language="en", app_url="https://memories.test").validate()


@pytest.fixture
def push_service() -> PushService:
    return PushService()


@pytest.fixture
def svc(app_settings, fake_ntfy, clock, push_service):
    service = BackInFrame(app_settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                             rng=random.Random(7))
    service.push_client = httpx.Client(transport=httpx.MockTransport(push_service.handler))
    service.push_hosts_for_tests = (PUSH_HOST,)
    yield service
    service.db.close()


@pytest.fixture
def phone(svc):
    with TestClient(create_recipient_app(svc), base_url="https://memories.test") as client:
        yield client


@pytest.fixture
def admin(svc):
    with TestClient(create_app(svc), follow_redirects=True) as client:
        yield client


@pytest.fixture
def oma(svc, media_dir):
    make_image(media_dir / "Chili 2025" / "a.jpg", taken=datetime(2025, 11, 7, 14, 0), gps=(-41.47, -72.94))
    svc.rescan()
    return svc.create_recipient("Oma")


def subscribe(phone, push_service, token) -> PushBrowser:
    browser = push_service.browser()
    resp = phone.post(f"/a/{token}/push", json=browser.json())
    assert resp.status_code == 200, resp.text
    return browser


def test_new_recipients_use_the_app_and_get_no_ntfy_welcome(svc, oma, fake_ntfy):
    assert oma.delivery == "app"
    assert len(oma.app_token) >= 40
    assert fake_ntfy.messages == []


def test_admin_page_shows_the_secret_link_and_qr(admin, oma):
    page = admin.get(f"/recipients/{oma.id}").text
    assert f"https://memories.test/a/{oma.app_token}" in page
    assert "<svg" in page
    assert "ntfy.sh" not in page


def test_app_page_is_private_and_installable(phone, oma):
    resp = phone.get(f"/a/{oma.app_token}/")
    assert resp.status_code == 200
    assert "Back in Frame" in resp.text and "for Oma" in resp.text
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["cache-control"] == "no-store"
    manifest = phone.get(f"/a/{oma.app_token}/manifest.webmanifest").json()
    assert manifest["start_url"] == manifest["scope"] == f"/a/{oma.app_token}/"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert phone.get(icon["src"]).headers["content-type"] == "image/png"
    sw = phone.get(f"/a/{oma.app_token}/sw.js")
    assert sw.headers["service-worker-allowed"] == f"/a/{oma.app_token}/"
    assert "showNotification" in sw.text


def test_wrong_or_ntfy_links_find_nothing(phone, svc, oma):
    assert phone.get("/a/not-a-token/").status_code == 404
    ntfy_user = svc.create_recipient("Opa", delivery="ntfy", welcome=False)
    assert phone.get(f"/a/{ntfy_user.app_token}/").status_code == 404


def test_memory_arrives_as_encrypted_push_and_in_the_gallery(phone, svc, oma, push_service, fake_ntfy):
    browser = subscribe(phone, push_service, oma.app_token)
    assert svc.send_now(oma.id).status == "ok"
    ((endpoint, message, auth),) = push_service.received
    assert endpoint == browser.endpoint
    verify_vapid(auth, svc.vapid.public_key)
    send_id = svc.db.last_send_id(oma.id)
    assert message["title"] == "Chili 2025"
    assert message["body"] == "November 7, 2025 · Today, 1 year ago"
    assert message["url"] == f"https://memories.test/a/{oma.app_token}/#m{send_id}"
    assert message["image"] == f"https://memories.test/a/{oma.app_token}/media/{send_id}"
    assert fake_ntfy.messages == []  # nothing went through ntfy

    page = phone.get(f"/a/{oma.app_token}/").text
    assert f'id="m{send_id}"' in page and "Chili 2025" in page


def test_gallery_photo_has_its_date_but_no_location(phone, svc, oma):
    svc.send_now(oma.id)
    send_id = svc.db.last_send_id(oma.id)
    resp = phone.get(f"/a/{oma.app_token}/media/{send_id}")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    exif = Image.open(io.BytesIO(resp.content)).getexif()
    assert exif.get_ifd(0x8825) == {}
    assert exif.get_ifd(0x8769)[0x9003] == "2025:11:07 14:00:00"


def test_someone_elses_photo_is_not_reachable(phone, svc, oma):
    svc.send_now(oma.id)
    send_id = svc.db.last_send_id(oma.id)
    other = svc.create_recipient("Neighbour")
    assert phone.get(f"/a/{other.app_token}/media/{send_id}").status_code == 404


def test_gallery_photo_is_rebuilt_when_the_cache_was_cleared(phone, svc, oma):
    svc.send_now(oma.id)
    send_id = svc.db.last_send_id(oma.id)
    for f in svc.settings.cache_dir.iterdir():
        f.unlink()
    assert phone.get(f"/a/{oma.app_token}/media/{send_id}").status_code == 200


def test_buttons_change_the_rhythm_without_a_notification(phone, svc, oma, push_service):
    subscribe(phone, push_service, oma.app_token)
    out = phone.post(f"/a/{oma.app_token}/command/more").json()
    assert out["rhythm"] == "You get 2 memories per day, between 09:00 and 21:00."
    assert out["paused"] is False
    out = phone.post(f"/a/{oma.app_token}/command/pause").json()
    assert out["paused"] is True and out["rhythm"].startswith("Paused until November 14, 2026")
    assert push_service.received == []
    assert phone.post(f"/a/{oma.app_token}/command/delete").status_code == 400


def test_other_sites_cannot_post(phone, oma, push_service):
    resp = phone.post(f"/a/{oma.app_token}/command/more", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    browser = push_service.browser()
    resp = phone.post(f"/a/{oma.app_token}/push", json=browser.json(), headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_bad_subscriptions_are_refused(phone, oma):
    resp = phone.post(f"/a/{oma.app_token}/push", json={"endpoint": "https://10.0.0.1/x", "keys": {}})
    assert resp.status_code == 400
    assert phone.post(f"/a/{oma.app_token}/push", content=b"x" * 9000,
                      headers={"content-type": "application/json"}).status_code == 413


def test_unsubscribed_devices_are_forgotten(phone, svc, oma, push_service):
    gone = subscribe(phone, push_service, oma.app_token)
    kept = subscribe(phone, push_service, oma.app_token)
    push_service.gone.add(gone.endpoint)
    svc.send_now(oma.id)
    assert [row["endpoint"] for row in svc.db.push_subscriptions(oma.id)] == [kept.endpoint]
    phone.post(f"/a/{oma.app_token}/push/remove", json={"endpoint": kept.endpoint})
    assert svc.db.push_subscriptions(oma.id) == []


def test_at_most_ten_devices_per_recipient(phone, svc, oma, push_service):
    endpoints = [subscribe(phone, push_service, oma.app_token).endpoint for _ in range(12)]
    assert [row["endpoint"] for row in svc.db.push_subscriptions(oma.id)] == endpoints[2:]


def test_deleting_a_recipient_deletes_their_devices(phone, svc, oma, push_service):
    subscribe(phone, push_service, oma.app_token)
    svc.delete_recipient(oma.id)
    assert svc.db.query("SELECT COUNT(*) AS n FROM push_subscriptions")[0]["n"] == 0


def test_cache_keeps_only_what_a_gallery_can_show(svc, oma, media_dir):
    svc.send_now(oma.id)
    stray = svc.settings.cache_dir / "999-1.jpg"
    stray.write_bytes(b"x")
    assert svc.prune_cache() == 1
    assert not stray.exists() and any(svc.settings.cache_dir.iterdir())


def test_switching_a_recipient_to_ntfy_closes_the_app(admin, phone, svc, oma):
    form = {"name": "Oma", "per_day": "1", "every_days": "1", "window_start": "09:00", "window_end": "21:00",
            "min_gap_minutes": "60", "enabled": "1", "delivery": "ntfy"}
    admin.post(f"/recipients/{oma.id}", data=form)
    assert svc.db.recipient(oma.id).delivery == "ntfy"
    assert phone.get(f"/a/{oma.app_token}/").status_code == 404


def test_app_delivery_needs_an_app_url(tmp_path, media_dir, fake_ntfy, clock):
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "d", ntfy_url="https://ntfy.test").validate()
    service = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    assert service.create_recipient("Oma", welcome=False).delivery == "ntfy"
    with pytest.raises(ValueError):
        service.create_recipient("Opa", delivery="app")


@pytest.mark.parametrize(("url", "ok"), [("https://memories.example", True), ("http://localhost:8081", True),
                                         ("http://192.168.1.10:8081", False), ("memories.example", False)])
def test_app_url_must_be_https(tmp_path, url, ok):
    make = lambda: Settings(data_dir=tmp_path, app_url=url).validate()  # noqa: E731
    if ok:
        make()
    else:
        with pytest.raises(ConfigError):
            make()


def test_app_port_cannot_be_the_admin_port(tmp_path):
    with pytest.raises(ConfigError):
        Settings(data_dir=tmp_path, app_url="https://m.example", app_port=8080, web_port=8080).validate()


def test_databases_from_before_the_app_get_a_link_per_recipient(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE recipients (id INTEGER PRIMARY KEY, name TEXT NOT NULL, topic TEXT NOT NULL UNIQUE, "
                "control_topic TEXT NOT NULL UNIQUE, per_day INTEGER NOT NULL DEFAULT 1, every_days INTEGER NOT NULL "
                "DEFAULT 1, window_start TEXT NOT NULL DEFAULT '09:00', window_end TEXT NOT NULL DEFAULT '21:00', "
                "min_gap_minutes INTEGER NOT NULL DEFAULT 60, paused_until TEXT, enabled INTEGER NOT NULL DEFAULT 1, "
                "created_at TEXT NOT NULL, next_send_at TEXT)")
    old.execute("INSERT INTO recipients(name, topic, control_topic, created_at) VALUES('Oma', 't', 'c', "
                "'2026-01-01T00:00:00+01:00')")
    old.commit()
    old.close()
    db = Database(path)
    (r,) = db.recipients()
    assert r.delivery == "ntfy" and len(r.app_token) >= 40
    db.close()
