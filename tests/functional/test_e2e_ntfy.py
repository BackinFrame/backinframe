"""End-to-end: Back in Frame against a real ntfy server, the phone simulated over HTTP.

What the phone does is exactly what the ntfy app does:
* read messages on the subscribed topic and download the attachment;
* on a button press, POST the action's body to the action's URL.
"""

from __future__ import annotations

import io
import random
from datetime import datetime

import httpx
import pytest
from helpers import TZ, Clock, has_ffmpeg, make_image, make_video
from PIL import Image

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.ntfy import NtfyClient
from backinframe.service import BackInFrame

pytestmark = pytest.mark.ntfy


def build(server, media_dir, tmp_path, clock, **overrides) -> BackInFrame:
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url=server.url,
                        **overrides).validate()
    return BackInFrame(settings, db=Database(settings.db_path), ntfy=NtfyClient(server.url), clock=clock,
                     rng=random.Random(1))


def press(action: dict) -> None:
    """Do what the ntfy app does when a button is tapped."""
    assert action["action"] == "http"
    httpx.request(action.get("method", "POST"), action["url"], content=action.get("body", ""),
                  timeout=10).raise_for_status()


@pytest.fixture
def clock():
    return Clock(datetime(2026, 11, 7, 7, 0, tzinfo=TZ))


def test_photo_arrives_on_phone_with_buttons_that_work(ntfy_server, media_dir, tmp_path, clock):
    make_image(media_dir / "Chili 2026" / "Torres.jpg", taken=datetime(2025, 11, 7, 10), size=(4032, 3024))
    svc = build(ntfy_server, media_dir, tmp_path, clock)
    svc.rescan()
    assert svc.server_limit() == 15 * 1024 * 1024  # asked from the real server

    r = svc.create_recipient("Vera", window_start="09:00", window_end="12:00")
    svc.poll_controls()

    # nothing before the planned moment
    assert svc.tick() == 0
    clock.set(r.next_send_at)
    assert svc.tick() == 1

    welcome, photo = ntfy_server.wait_messages(r.topic, 2)
    assert welcome["title"] == "Welcome to Back in Frame"
    assert photo["title"] == "Chili 2026"
    assert photo["message"] == "November 7, 2025 · Today, 1 year ago"

    # the attachment downloads and is a phone-sized JPEG
    att = photo["attachment"]
    data = httpx.get(att["url"], timeout=10).content
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and max(img.size) == 2048
    assert att["size"] == len(data)

    # three buttons, all pointing at the control topic
    labels = [a["label"] for a in photo["actions"]]
    assert labels == ["More", "Less", "Pause 1 week"]
    assert all(a["url"] == f"{ntfy_server.url}/{r.control_topic}" for a in photo["actions"])

    # tap "More" -> service picks it up and confirms
    press(photo["actions"][0])
    assert svc.poll_controls() == 1
    assert svc.db.recipient(r.id).per_day == 2
    confirm = ntfy_server.wait_messages(r.topic, 3)[-1]
    assert confirm["message"] == "Done! From now on: 2 memories per day."

    # tap "Pause" -> paused for a week, nothing goes out meanwhile
    press(photo["actions"][2])
    assert svc.poll_controls() == 1
    paused = svc.db.recipient(r.id)
    assert paused.paused_until is not None
    clock.advance(days=2)
    assert svc.tick() == 0

    # a restart (new service object, same database) does not re-apply old presses
    svc2 = build(ntfy_server, media_dir, tmp_path, clock)
    assert svc2.poll_controls() == 0
    assert svc2.db.recipient(r.id).per_day == 2


def test_typed_command_from_the_app(ntfy_server, media_dir, tmp_path, clock):
    """Users can also type 'hervat' into the control topic from the ntfy app."""
    svc = build(ntfy_server, media_dir, tmp_path, clock)
    r = svc.create_recipient("A", welcome=False)
    svc.poll_controls()
    svc.handle_command(r.id, "pause")
    httpx.post(f"{ntfy_server.url}/{r.control_topic}", content="hervat", timeout=10)
    assert svc.poll_controls() == 1
    assert svc.db.recipient(r.id).paused_until is None


def test_big_photo_is_squeezed_under_a_strict_server_limit(strict_ntfy_server, media_dir, tmp_path, clock):
    make_image(media_dir / "noise.jpg", size=(3000, 3000), noise=True)
    svc = build(strict_ntfy_server, media_dir, tmp_path, clock)
    svc.rescan()
    assert svc.server_limit() == 300 * 1024
    r = svc.create_recipient("A", welcome=False)
    assert svc.send_now(r.id).status == "ok"
    (msg,) = strict_ntfy_server.wait_messages(r.topic, 1)
    assert msg["attachment"]["size"] <= 300 * 1024


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_video_is_transcoded_to_fit(strict_ntfy_server, media_dir, tmp_path, clock):
    make_video(media_dir / "Reis" / "clip.mov", seconds=3, size="1280x720", noisy=True,
               creation="2024-07-01T12:00:00Z")
    svc = build(strict_ntfy_server, media_dir, tmp_path, clock)
    svc.rescan()
    r = svc.create_recipient("A", welcome=False)
    assert svc.send_now(r.id).status == "ok"
    (msg,) = strict_ntfy_server.wait_messages(r.topic, 1)
    assert msg["attachment"]["name"] == "clip.mp4"
    assert msg["attachment"]["type"] == "video/mp4"
    assert msg["attachment"]["size"] <= 300 * 1024
    assert msg["message"].startswith("July 1, 2024")


def test_no_videos_when_disabled(ntfy_server, media_dir, tmp_path, clock):
    if has_ffmpeg():
        make_video(media_dir / "clip.mp4", seconds=1)
    make_image(media_dir / "photo.jpg")
    svc = build(ntfy_server, media_dir, tmp_path, clock, send_videos=False)
    svc.rescan()
    r = svc.create_recipient("A", welcome=False)
    for _ in range(4):
        svc.send_now(r.id)
    names = {m["attachment"]["name"] for m in ntfy_server.wait_messages(r.topic, 4)}
    assert names == {"photo.jpg"}
