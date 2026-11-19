"""Recipients on e-mail, Telegram, Signal, Matrix and Discord, through the whole service."""

import json
import random
from datetime import datetime

import pytest
from channel_fakes import BLOCKED_CHAT, TELEGRAM_TOKEN, Services, webhook
from helpers import make_image

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def services():
    FakeSMTP.sent = []
    return Services()


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock, services):
    settings = Settings(
        media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test", language="nl",
        smtp_host="smtp.test", smtp_from="Back in Frame <photos@example.org>",
        telegram_bot_token=TELEGRAM_TOKEN, telegram_api_url="https://api.telegram.test",
        signal_url="http://signal.test", signal_number="+31600000001",
        matrix_homeserver="https://matrix.test", matrix_token="syt_test",
    ).validate()
    s = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                       rng=random.Random(1))
    s.http_client = services.client()
    s.smtp_factory = FakeSMTP
    for i in range(3):
        make_image(media_dir / "Chili 2024" / f"IMG_{i}.jpg", taken=datetime(2024, 11, 7 + i, 12))
    s.rescan()
    yield s
    s.db.close()


def test_menu_lists_only_what_is_set_up(svc, settings, fake_ntfy, clock):
    assert svc.deliveries() == ["ntfy", "email", "telegram", "signal", "matrix", "discord"]
    plain = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    assert plain.deliveries() == ["ntfy", "discord"]  # discord needs nothing on the server
    with pytest.raises(ValueError, match="not set up"):
        plain.create_recipient("Oma", delivery="telegram", address="123")


def test_address_is_required_and_checked(svc):
    with pytest.raises(ValueError, match="needs an address"):
        svc.create_recipient("Oma", delivery="email")
    with pytest.raises(ValueError):
        svc.create_recipient("Oma", delivery="email", address="geen-adres")
    with pytest.raises(ValueError):
        svc.create_recipient("Oma", delivery="pigeon", address="x")
    assert svc.db.recipients() == []
    r = svc.create_recipient("Oma", delivery="email", address="Oma@Example.org", welcome=False)
    assert r.address == "Oma@example.org"


def test_email_recipient_gets_welcome_and_photo(svc):
    r = svc.create_recipient("Oma", delivery="email", address="oma@example.org")
    welcome = FakeSMTP.sent[-1]
    assert welcome["Subject"].startswith("Welkom bij Back in Frame")
    assert svc.send_now(r.id).status == "ok"
    mail = FakeSMTP.sent[-1]
    assert mail["To"] == "oma@example.org" and mail["Subject"].startswith("Chili 2024 · ")
    assert any(p.get_content_type() == "image/jpeg" for p in mail.walk())
    assert "Antwoorden op deze e-mail worden niet gelezen" in mail.get_body(("plain",)).get_content()


def test_telegram_buttons_change_the_rhythm(svc, services):
    r = svc.create_recipient("Vera", delivery="telegram", address="4242", per_day=1)
    assert services.sent("sendMessage")[0]["fields"]["chat_id"] == "4242"  # the welcome
    assert svc.send_now(r.id).status == "ok"
    (photo,) = services.sent("sendPhoto")
    assert '"callback_data": "more:%d"' % r.id in photo["fields"]["reply_markup"]
    assert "Vaker" in photo["fields"]["reply_markup"]
    services.press("4242", f"more:{r.id}")
    assert svc.poll_channels() == 1
    assert svc.db.recipient(r.id).per_day == 2
    assert services.sent("answerCallbackQuery")[0]["fields"]["text"].startswith("Prima! Vanaf nu: 2")
    assert svc.poll_channels() == 0  # each press counts once (offset stored)
    assert svc.db.get_state("telegram_offset") == str(services.telegram_updates[-1]["update_id"] + 1)


def test_telegram_press_from_another_chat_is_ignored(svc, services):
    r = svc.create_recipient("Vera", delivery="telegram", address="4242", welcome=False)
    services.press("9999", f"pause:{r.id}")  # a forged button press from someone else's chat
    services.press("4242", "pause:99999")  # a recipient that does not exist
    services.press("4242", f"explode:{r.id}")
    assert svc.poll_channels() == 0
    assert svc.db.recipient(r.id).paused_until is None


def test_telegram_start_tells_the_chat_id(svc, services):
    services.say_telegram("777", "/start")
    svc.poll_channels()
    (reply,) = services.sent("sendMessage")
    assert reply["fields"] == {"chat_id": "777", "text": "Hallo! Je chat-ID is 777. Geef die aan degene die je "
                                                          "herinneringen stuurt."}


def test_telegram_text_command(svc, services):
    r = svc.create_recipient("Vera", delivery="telegram", address="4242", welcome=False)
    services.say_telegram("4242", "/pause")
    assert svc.poll_channels() == 1
    assert svc.db.recipient(r.id).paused_until is not None


def test_signal_text_replies_in_any_language(svc, services):
    r = svc.create_recipient("Opa", delivery="signal", address="+31 6 1234 5678", per_day=2, language="de")
    assert services.signal[0]["message"].startswith("Willkommen")  # the welcome, in German
    assert svc.send_now(r.id).status == "ok"
    assert "Antworte mehr, weniger oder pause" in services.signal[-1]["message"]
    services.say_signal("+31612345678", "Weniger")
    services.say_signal("+31699999999", "weniger")  # not a recipient
    assert svc.poll_channels() == 1
    assert svc.db.recipient(r.id).per_day == 1
    assert services.signal[-1]["recipients"] == ["+31612345678"]
    assert "1 Erinnerung pro Tag" in services.signal[-1]["message"]


def test_matrix_and_discord(svc, services):
    m = svc.create_recipient("Club", delivery="matrix", address="!room:matrix.test", welcome=False)
    d = svc.create_recipient("Gamers", delivery="discord", address=webhook(), welcome=False)
    assert svc.send_now(m.id).status == "ok"
    assert svc.send_now(d.id).status == "ok"
    assert [e["kind"] for e in services.matrix] == ["send", "upload", "send"]
    assert services.discord[0]["files"]["files[0]"][2] == "image/jpeg"


def test_failed_channel_send_is_logged(svc, services):
    r = svc.create_recipient("Weg", delivery="telegram", address=BLOCKED_CHAT, welcome=False)
    result = svc.send_now(r.id)
    assert result.status == "error" and "blocked" in result.detail
    assert svc.db.recent_sends(r.id)[0]["status"] == "error"


def test_switching_channel_greets_once_and_rechecks(svc, services):
    r = svc.create_recipient("Vera", welcome=False)  # ntfy
    svc.update_recipient(r.id, delivery="telegram", address="4242")
    assert len(services.sent("sendMessage")) == 1
    svc.update_recipient(r.id, per_day=2)  # same channel: no new welcome
    assert len(services.sent("sendMessage")) == 1
    with pytest.raises(ValueError):
        svc.update_recipient(r.id, delivery="email")  # the chat ID is no e-mail address
    assert svc.db.recipient(r.id).delivery == "telegram"


def test_channel_turned_off_later(svc, settings, fake_ntfy, clock):
    r = svc.create_recipient("Vera", delivery="matrix", address="!room:matrix.test", welcome=False)
    svc._channels.pop("matrix")  # the operator removed the Matrix settings
    result = svc.send_now(r.id)
    assert result.status == "error" and "not set up" in result.detail


def test_recipient_language_is_used_everywhere(svc, services):
    r = svc.create_recipient("Yuki", delivery="telegram", address="31", language="ja", welcome=False)
    svc.send_now(r.id)
    caption = services.sent("sendPhoto")[0]["fields"]["caption"]
    assert "2024年11月7日" in caption or "2024年11月8日" in caption or "2024年11月9日" in caption
    assert "年前" in caption
    keyboard = json.loads(services.sent("sendPhoto")[0]["fields"]["reply_markup"])["inline_keyboard"]
    assert keyboard[0][0]["text"] == "もっと"
    with pytest.raises(ValueError):
        svc.update_recipient(r.id, language="xx")
