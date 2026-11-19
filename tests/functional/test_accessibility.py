"""Every page, checked with axe-core against WCAG 2.2 A and AA, on a phone and a desktop, light and dark."""

from __future__ import annotations

import dataclasses
import random
import re
from datetime import datetime

import pytest
from a11y import assert_accessible, audit, require_browser
from fastapi.testclient import TestClient
from helpers import make_image

from backinframe.app import create_recipient_app
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

ADMIN = "http://backinframe.test"
APP = "https://photos.example.org"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
IOS_26 = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1")
INSTAGRAM = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Mobile/15E148 Instagram 337.0.3.23.54")


@pytest.fixture(autouse=True)
def _browser_needed():
    require_browser()


@pytest.fixture(params=["en", "ja"])
def svc(request, tmp_path, media_dir, fake_ntfy, clock):
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                        language=request.param, app_url=APP, telegram_bot_token="123:abc",
                        smtp_host="mail.example.org", smtp_from="photos@example.org",
                        immich_url="https://immich.example.org", immich_api_key="k").validate()
    s = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                       rng=random.Random(3))
    make_image(media_dir / "Chile 2025" / "a.jpg", taken=datetime(2025, 11, 7))
    s.rescan()
    yield s
    s.db.close()


def test_admin_pages(svc):
    app_r = svc.create_recipient("Oma")
    ntfy_r = svc.create_recipient("Opa", delivery="ntfy")
    mail_r = svc.create_recipient("Vera", delivery="email", address="vera@example.org")
    svc.send_now(app_r.id)
    with TestClient(create_app(svc), base_url=ADMIN) as client:
        client.post("/uploads", data={"album": "Kleinkinderen", "name": "Anna"})
        failures = audit(client, ADMIN, ["/", "/hearts", "/review", "/photos", "/photos/1", f"/recipients/{app_r.id}",
                                         f"/recipients/{ntfy_r.id}", f"/recipients/{mail_r.id}", "/uploads",
                                         "/admins"])
    token = svc.db.upload_links()[0]["token"]
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        failures.update(audit(phone, APP, [f"/u/{token}"]))
    assert_accessible(failures)


@pytest.mark.parametrize("agent", [None, IPHONE, IOS_26, INSTAGRAM], ids=["android", "iphone", "ios26", "inapp"])
def test_recipient_web_app(svc, agent):
    r = svc.create_recipient("Oma")
    svc.send_now(r.id)
    with TestClient(create_recipient_app(svc), base_url=APP) as client:
        failures = audit(client, APP, [f"/a/{r.app_token}/"], user_agent=agent)
    assert_accessible(failures)


def test_login_and_security_pages(svc, tmp_path):
    """The login screen, the code step and the security pages, with a password set."""
    settings = dataclasses.replace(svc.settings, admin_password="a long admin password")
    s = BackInFrame(settings, db=svc.db, ntfy=svc.ntfy, clock=svc._clock, rng=random.Random(3))
    with TestClient(create_app(s), base_url=ADMIN) as client:
        failures = audit(client, ADMIN, ["/login"])
        client.post("/login", data={"password": "a long admin password"})
        failures.update(audit(client, ADMIN, ["/security", "/security/2fa", "/admins"]))
        page = client.post("/admins", data={"name": "Anna"}).text
        invite = re.search(r"(/invite/[\w-]+)", page).group(1)
    with TestClient(create_app(s), base_url=ADMIN) as anna:
        failures.update(audit(anna, ADMIN, [invite]))
        anna.post(invite, data={"password": "anna password 1", "again": "anna password 1"})
        failures.update(audit(anna, ADMIN, ["/security"]))
    assert_accessible(failures)
