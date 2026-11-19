"""The extra channels end to end: the admin page as a browser uses it, a real SMTP server for e-mail,
and the Telegram, Signal, Matrix and Discord fakes for the rest."""

from __future__ import annotations

import random
import re
import time
from datetime import datetime
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller
from channel_fakes import TELEGRAM_TOKEN, Services, webhook
from fastapi.testclient import TestClient
from helpers import free_port, make_image

from backinframe.app import create_recipient_app
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.rcpt_tos, message_from_bytes(envelope.content, policy=policy.default)))
        return "250 OK"


@pytest.fixture
def smtp_server():
    inbox = Inbox()
    controller = Controller(inbox, hostname="127.0.0.1", port=free_port())
    controller.start()
    yield controller, inbox
    controller.stop()


@pytest.fixture
def setup(tmp_path, fake_ntfy, clock, smtp_server):
    controller, inbox = smtp_server
    media = tmp_path / "media"
    for i in range(3):
        make_image(media / "Lissabon 2023" / f"IMG_{i}.jpg", taken=datetime(2023, 11, 6 + i, 12))
    settings = Settings(
        media_dir=media, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test", language="en",
        app_url="https://photos.example.org",
        smtp_host=controller.hostname, smtp_port=controller.port, smtp_security="none",
        smtp_from="Back in Frame <photos@example.org>",
        telegram_bot_token=TELEGRAM_TOKEN, telegram_api_url="https://api.telegram.test",
        signal_url="http://signal.test", signal_number="+31600000001",
        matrix_homeserver="https://matrix.test", matrix_token="syt_test",
    ).validate()
    svc = BackInFrame(settings, db=Database(tmp_path / "lm.sqlite3"), ntfy=fake_ntfy.client(), clock=clock,
                         rng=random.Random(3))
    services = Services()
    svc.http_client = services.client()
    svc.rescan()
    with TestClient(create_app(svc), follow_redirects=True) as admin:
        yield svc, admin, services, inbox
    svc.db.close()


def rid(resp) -> int:
    return int(re.search(r"/recipients/(\d+)", str(resp.url)).group(1))


def wait_for_mail(inbox, n, timeout=5.0):
    end = time.monotonic() + timeout
    while len(inbox.messages) < n and time.monotonic() < end:
        time.sleep(0.05)
    return inbox.messages


def test_admin_offers_every_configured_channel(setup):
    svc, admin, services, inbox = setup
    page = admin.get("/").text
    for value in ("app", "ntfy", "email", "telegram", "signal", "matrix", "discord"):
        assert f'<option value="{value}">' in page
    for code, name in (("ja", "日本語"), ("pl", "Polski"), ("it", "Italiano")):
        assert f'<option value="{code}" lang="{code}">{name}</option>' in page


def test_email_recipient_over_real_smtp(setup):
    svc, admin, services, inbox = setup
    resp = admin.post("/recipients", data={"name": "Oma", "delivery": "email", "address": "oma@example.org",
                                           "language": "nl"})
    assert resp.status_code == 200 and "Photos arrive as an e-mail" in resp.text
    (to, welcome), = wait_for_mail(inbox, 1)
    assert to == ["oma@example.org"] and welcome["Subject"].startswith("Welkom bij Back in Frame")
    resp = admin.post(f"/recipients/{rid(resp)}/send")
    assert "Sent." in resp.text
    _, mail = wait_for_mail(inbox, 2)[1]
    assert mail["Subject"].startswith("Lissabon 2023 · ")
    assert "jaar geleden" in mail["Subject"]  # the recipient's language, not the admin page's
    image = next(p for p in mail.walk() if p.get_content_type() == "image/jpeg")
    data = image.get_payload(decode=True)
    assert data[:2] == b"\xff\xd8" and b"GPS" not in data


def test_telegram_recipient_set_up_from_the_admin_page(setup):
    svc, admin, services, inbox = setup
    services.say_telegram("4242", "/start")  # the recipient opens the bot first
    svc.poll_channels()
    assert "4242" in services.sent("sendMessage")[0]["fields"]["text"]
    resp = admin.post("/recipients", data={"name": "Vera", "delivery": "telegram", "address": "4242"})
    assert "@BackInFrameTestBot" in resp.text  # the help names the bot
    assert '<code id="address">4242</code>' in resp.text
    r = rid(resp)
    admin.post(f"/recipients/{r}/send")
    (photo,) = services.sent("sendPhoto")
    services.press("4242", f"less:{r}")
    svc.poll_channels()
    assert svc.db.recipient(r).every_days == 2


def test_bad_address_is_refused_with_a_message(setup):
    svc, admin, services, inbox = setup
    resp = admin.post("/recipients", data={"name": "X", "delivery": "signal", "address": "0612"})
    assert "country code" in resp.text
    assert svc.db.recipients() == []


def test_switch_channels_and_language_in_settings(setup):
    svc, admin, services, inbox = setup
    resp = admin.post("/recipients", data={"name": "Club", "delivery": "ntfy"})
    r = rid(resp)
    form = {"name": "Club", "per_day": "1", "every_days": "1", "window_start": "09:00", "window_end": "21:00",
            "min_gap_minutes": "60", "enabled": "1", "delivery": "discord", "address": webhook(), "language": "it"}
    resp = admin.post(f"/recipients/{r}", data=form)
    assert "Integrations → Webhooks" in resp.text
    assert svc.db.recipient(r).language == "it"
    assert services.discord[-1]["payload"]["content"].startswith("**Benvenuto in Back in Frame**")
    admin.post(f"/recipients/{r}/send")
    assert "anni fa" in services.discord[-1]["payload"]["content"]
    form.update(delivery="matrix", address="!room:matrix.test")
    resp = admin.post(f"/recipients/{r}", data=form)
    assert "@memories:matrix.test" in resp.text
    form.update(delivery="signal", address="+31612345678", language="")
    admin.post(f"/recipients/{r}", data=form)
    assert services.signal[-1]["message"].startswith("Welcome to Back in Frame")


def test_web_app_speaks_the_recipient_language(setup):
    svc, admin, services, inbox = setup
    r = svc.create_recipient("Yuki", delivery="app", language="ja")
    with TestClient(create_recipient_app(svc)) as phone:
        page = phone.get(f"/a/{r.app_token}/")
    assert '<html lang="ja"' in page.text
    assert "通知をオンにする" in page.text and "あなたの思い出" in page.text


def test_web_app_link_only_for_app_recipients(setup):
    svc, admin, services, inbox = setup
    r = svc.create_recipient("Vera", delivery="telegram", address="4242", welcome=False)
    with TestClient(create_recipient_app(svc)) as phone:
        assert phone.get(f"/a/{r.app_token}/").status_code == 404
