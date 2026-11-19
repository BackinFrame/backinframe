"""Web Push: subscription checks, VAPID signatures and payload encryption."""

from __future__ import annotations

import stat
import time

import httpx
import pytest
from helpers import PushBrowser as Browser
from helpers import verify_vapid

from backinframe import webpush
from backinframe.webpush import Subscription, Vapid, b64url_decode


def test_subscription_accepts_real_push_services():
    for endpoint in ("https://fcm.googleapis.com/fcm/send/x", "https://web.push.apple.com/QK/x",
                     "https://updates.push.services.mozilla.com/wpush/v2/x"):
        assert Subscription.from_json(Browser(endpoint).json()).endpoint == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/x",       # no https
    "https://evil.example/fcm.googleapis.com",     # unknown host
    "https://fcm.googleapis.com.evil.example/x",   # look-alike
    "https://10.0.0.1/x",                          # internal address
])
def test_subscription_refuses_anything_else(endpoint):
    with pytest.raises(ValueError):
        Subscription.from_json(Browser(endpoint).json())


@pytest.mark.parametrize("data", [{}, {"endpoint": "https://fcm.googleapis.com/x"}, "nope",
                                  {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "AA", "auth": "AA"}}])
def test_subscription_refuses_garbage(data):
    with pytest.raises(ValueError):
        Subscription.from_json(data)


def test_vapid_key_is_created_once_and_kept_private(tmp_path):
    path = tmp_path / "data" / "vapid_private.pem"
    first = Vapid.load_or_create(path, "https://memories.example")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert Vapid.load_or_create(path, "https://memories.example").public_key == first.public_key
    assert len(b64url_decode(first.public_key)) == 65


def test_vapid_subject_must_be_mailto_or_https(tmp_path):
    with pytest.raises(ValueError):
        Vapid.load_or_create(tmp_path / "k.pem", "memories.example")


def test_vapid_header_is_a_valid_es256_token(tmp_path):
    vapid = Vapid.load_or_create(tmp_path / "k.pem", "mailto:admin@example.org")
    claims = verify_vapid(vapid.header("https://web.push.apple.com/QK/abc"), vapid.public_key)
    assert claims["aud"] == "https://web.push.apple.com"
    assert claims["sub"] == "mailto:admin@example.org"
    assert time.time() < claims["exp"] <= time.time() + 24 * 3600  # Apple and Google refuse more than 24 h


def test_payload_only_the_browser_can_read(tmp_path):
    browser = Browser()
    sub = Subscription.from_json(browser.json())
    body = webpush.encrypt(b'{"title": "Chili"}', sub)
    assert b"Chili" not in body
    assert browser.decrypt(body) == {"title": "Chili"}


def test_send_posts_an_encrypted_message_with_the_right_headers(tmp_path):
    vapid = Vapid.load_or_create(tmp_path / "k.pem", "https://memories.example")
    browser = Browser()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        webpush.send(vapid, Subscription.from_json(browser.json()), {"title": "Oma"}, client=client)
    (req,) = seen
    assert str(req.url) == browser.endpoint
    assert req.headers["content-encoding"] == "aes128gcm"
    assert int(req.headers["ttl"]) > 0
    verify_vapid(req.headers["authorization"], vapid.public_key)
    assert browser.decrypt(req.content) == {"title": "Oma"}


@pytest.mark.parametrize(("status", "error"), [(404, webpush.PushGone), (410, webpush.PushGone),
                                                (429, webpush.PushError), (500, webpush.PushError)])
def test_send_reports_failures(tmp_path, status, error):
    vapid = Vapid.load_or_create(tmp_path / "k.pem", "https://memories.example")
    sub = Subscription.from_json(Browser().json())
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        with pytest.raises(error):
            webpush.send(vapid, sub, {}, client=client)
