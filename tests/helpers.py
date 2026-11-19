"""Test helpers: media factories, a fake clock, an in-memory ntfy and a real-ntfy launcher."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image

from backinframe.ntfy import NtfyClient

TZ = ZoneInfo("Europe/Amsterdam")

# --------------------------------------------------------------------------- media


def make_image(
    path: Path,
    taken: datetime | None = None,
    size: tuple[int, int] = (800, 600),
    color: str = "orange",
    gps: tuple[float, float] | None = None,
    orientation: int | None = None,
    noise: bool = False,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if noise:
        img = Image.effect_noise(size, 80).convert("RGB")
    else:
        img = Image.new("RGB", size, color)
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
    if gps:
        lat, lon = gps

        def dms(v):
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            s = (v - d - m / 60) * 3600
            return (float(d), float(m), round(s, 4))

        g = exif.get_ifd(0x8825)
        g[1] = "N" if lat >= 0 else "S"
        g[2] = dms(lat)
        g[3] = "E" if lon >= 0 else "W"
        g[4] = dms(lon)
    if orientation:
        exif[0x0112] = orientation
    fmt = "PNG" if path.suffix.lower() == ".png" else "JPEG"
    img.save(path, fmt, exif=exif.tobytes()) if fmt == "JPEG" else img.save(path, fmt)
    return path


def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_video(path: Path, seconds: float = 2, size: str = "320x240", creation: str | None = None,
               noisy: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    src = f"testsrc2=size={size}:rate=25" if not noisy else f"cellauto=s={size}:rate=25:rule=110"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
           "-f", "lavfi", "-i", "sine=frequency=440",
           "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac"]
    if noisy:
        cmd += ["-b:v", "4M"]
    if creation:
        cmd += ["-metadata", f"creation_time={creation}"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)
    return path


# --------------------------------------------------------------------------- clock


class Clock:
    def __init__(self, start: datetime):
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self.value = self.value + timedelta(**kwargs)


# --------------------------------------------------------------------------- fake ntfy


class FakeNtfy:
    """In-memory stand-in for an ntfy server, mounted as an httpx transport."""

    def __init__(self, attachment_limit: int = 15 * 1024 * 1024):
        self.messages: list[dict] = []
        self.attachments: dict[str, bytes] = {}
        self.attachment_limit = attachment_limit
        self.fail_publish = False
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.strip("/")
        if path == "v1/account":
            return httpx.Response(200, json={"limits": {"attachment_file_size": self.attachment_limit}})
        if path.endswith("/json"):
            topics = path[: -len("/json")].split(",")
            since = request.url.params.get("since", "all")
            out = [m for m in self.messages if m["topic"] in topics and self._after(m, since)]
            return httpx.Response(200, text="\n".join(json.dumps(m) for m in out) + "\n")
        if request.method in ("PUT", "POST"):
            if self.fail_publish:
                return httpx.Response(500, text="boom")
            body = request.content
            params = request.url.params
            filename = params.get("filename")
            if filename and len(body) > self.attachment_limit:
                return httpx.Response(413, json={"error": "too large"})
            self._n += 1
            msg = {
                "id": f"m{self._n}", "time": int(time.time()), "event": "message", "topic": path,
                "message": params.get("message") or body.decode("utf-8", "replace"),
            }
            if params.get("title"):
                msg["title"] = params["title"]
            if params.get("actions"):
                msg["actions_raw"] = params["actions"]
            if filename:
                msg["attachment"] = {"name": filename, "size": len(body)}
                self.attachments[msg["id"]] = body
            self.messages.append(msg)
            return httpx.Response(200, json=msg)
        return httpx.Response(404)

    @staticmethod
    def _after(msg: dict, since: str) -> bool:
        if since == "all":
            return True
        if since.isdigit():
            return msg["time"] >= int(since)
        return True

    def on(self, topic: str) -> list[dict]:
        return [m for m in self.messages if m["topic"] == topic]

    def press(self, topic: str, body: str, ago: int = 0) -> None:
        """Simulate the phone pressing a notification button (``ago`` seconds in the past)."""
        self._n += 1
        self.messages.append({"id": f"m{self._n}", "time": int(time.time()) - ago, "event": "message",
                              "topic": topic, "message": body})

    def client(self, base_url: str = "https://ntfy.test") -> NtfyClient:
        return NtfyClient(base_url, client=httpx.Client(transport=httpx.MockTransport(self.handler)))


# --------------------------------------------------------------------------- real ntfy


def ntfy_binary() -> str | None:
    return os.environ.get("NTFY_BIN") or shutil.which("ntfy")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not come up")


class NtfyServer:
    def __init__(self, url: str, proc: subprocess.Popen):
        self.url = url
        self.proc = proc

    def messages(self, topic: str) -> list[dict]:
        resp = httpx.get(f"{self.url}/{topic}/json", params={"poll": "1", "since": "all"}, timeout=10)
        resp.raise_for_status()
        return [json.loads(line) for line in resp.text.splitlines() if line.strip()]

    def wait_messages(self, topic: str, count: int, timeout: float = 20) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msgs = self.messages(topic)
            if len(msgs) >= count:
                return msgs
            time.sleep(0.2)
        raise AssertionError(f"expected {count} messages on {topic}, got {len(self.messages(topic))}")


def start_ntfy(tmp: Path, attachment_limit: str = "15M") -> NtfyServer:
    binary = ntfy_binary()
    if not binary:
        pytest.skip("ntfy binary not found (set NTFY_BIN or put ntfy on PATH)")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    tmp.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [binary, "serve", "--listen-http", f"127.0.0.1:{port}", "--base-url", url,
         "--cache-file", str(tmp / "cache.db"), "--attachment-cache-dir", str(tmp / "att"),
         "--attachment-file-size-limit", attachment_limit,
         # the tests poll much faster than the app does in real life
         "--visitor-request-limit-exempt-hosts", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(f"{url}/v1/health")
    except RuntimeError:
        proc.kill()
        raise
    return NtfyServer(url, proc)




# -- Web Push -----------------------------------------------------------------

import http_ece  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import (  # noqa: E402
    encode_dss_signature,
)

from backinframe.webpush import b64url, b64url_decode  # noqa: E402


class PushBrowser:
    """What a browser holds for one push subscription."""

    def __init__(self, endpoint: str = "https://fcm.googleapis.com/fcm/send/abc"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def json(self) -> dict:
        raw = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64url(raw), "auth": b64url(self.auth)}}

    def decrypt(self, body: bytes) -> dict:
        return json.loads(http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth, version="aes128gcm"))


def verify_vapid(header: str, public_key_b64: str) -> dict:
    assert header.startswith("vapid t=")
    token, _, k = header[len("vapid t="):].partition(", k=")
    assert k == public_key_b64
    head, claims, sig = token.split(".")
    raw = b64url_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(k))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if forged
    assert json.loads(b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
    return json.loads(b64url_decode(claims))
