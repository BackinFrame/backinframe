"""Web Push (RFC 8030) with VAPID (RFC 8292) and aes128gcm encryption (RFC 8291).

The payload is encrypted for the receiving browser: the push service (Apple, Google, Mozilla)
only sees ciphertext, the endpoint and the time.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import http_ece
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

log = logging.getLogger(__name__)

TTL = 24 * 3600  # a phone that is off for a day still gets the memory


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class PushGone(Exception):
    """The subscription no longer exists (404/410): forget it."""


class PushError(Exception):
    pass


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str

    @classmethod
    def from_json(cls, data: dict, allowed_hosts: tuple[str, ...] | None = None) -> Subscription:
        """Validate what a browser sent. Only real push services are accepted, so the server can
        never be made to post to an arbitrary (internal) address."""
        try:
            endpoint = str(data["endpoint"])
            keys = data["keys"]
            p256dh, auth = str(keys["p256dh"]), str(keys["auth"])
            raw_key, raw_auth = b64url_decode(p256dh), b64url_decode(auth)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("not a push subscription") from exc
        url = urlparse(endpoint)
        hosts = PUSH_SERVICES if allowed_hosts is None else allowed_hosts
        host = (url.hostname or "").lower()
        if allowed_hosts is None and url.scheme != "https":
            raise ValueError("push endpoint must use https")
        if not any(host == h or host.endswith("." + h) for h in hosts):
            raise ValueError("unknown push service")
        if len(endpoint) > 1024 or len(raw_key) != 65 or len(raw_auth) != 16:
            raise ValueError("not a push subscription")
        return cls(endpoint, p256dh, auth)


# The push services browsers use (Apple, Google/Chromium, Mozilla, Microsoft).
PUSH_SERVICES = ("push.apple.com", "fcm.googleapis.com", "android.googleapis.com",
                 "push.services.mozilla.com", "notify.windows.com")


class Vapid:
    """The server's signing key. The public half goes into the page as applicationServerKey."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey, subject: str):
        if not subject.startswith(("mailto:", "https://")):
            raise ValueError("VAPID subject must be a mailto: or https:// URL")
        self.key = private_key
        self.subject = subject

    @classmethod
    def load_or_create(cls, path: Path, subject: str) -> Vapid:
        """Load the key, or create it once. Safe when two processes (web and worker) start together."""
        if not path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # the other process won; use its key
            else:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(pem)
        for _ in range(50):  # the other process may still be writing
            data = path.read_bytes()
            if data.strip().endswith(b"-----END PRIVATE KEY-----"):
                break
            time.sleep(0.02)
        return cls(serialization.load_pem_private_key(data, password=None), subject)

    @property
    def public_key(self) -> str:
        raw = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        return b64url(raw)

    def header(self, endpoint: str, now: float | None = None) -> str:
        url = urlparse(endpoint)
        claims = {"aud": f"{url.scheme}://{url.netloc}", "exp": int((now or time.time()) + 12 * 3600),
                  "sub": self.subject}
        signing_input = (b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "."
                         + b64url(json.dumps(claims, separators=(",", ":")).encode()))
        der = self.key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        token = signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={token}, k={self.public_key}"


def encrypt(payload: bytes, sub: Subscription) -> bytes:
    server_key = ec.generate_private_key(ec.SECP256R1())
    return http_ece.encrypt(
        payload, private_key=server_key, dh=b64url_decode(sub.p256dh),
        auth_secret=b64url_decode(sub.auth), version="aes128gcm",
    )


def send(vapid: Vapid, sub: Subscription, message: dict, client: httpx.Client | None = None,
         urgency: str = "normal") -> None:
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub)
    headers = {
        "Authorization": vapid.header(sub.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL),
        "Urgency": urgency,
    }
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        resp = client.post(sub.endpoint, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise PushError(f"push service unreachable: {exc.__class__.__name__}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code in (404, 410):
        raise PushGone(resp.status_code)
    if resp.status_code >= 400:
        raise PushError(f"push service answered {resp.status_code}")
