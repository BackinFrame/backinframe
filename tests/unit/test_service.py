import io
import random
from datetime import datetime, timedelta

import pytest
from helpers import FakeNtfy, make_image
from PIL import Image

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.scheduler import ScheduleError
from backinframe.service import BackInFrame, new_topic


def photos(media_dir, n=3, folder="Chili"):
    for i in range(n):
        make_image(media_dir / folder / f"IMG_{i}.jpg", taken=datetime(2024, 10, 1 + i, 12))


def test_new_topic_is_long_and_valid():
    t = new_topic()
    assert t.startswith("backinframe-") and len(t) >= 40
    assert all(c.isalnum() or c in "-_" for c in t)
    assert new_topic() != t


def test_create_recipient(service, fake_ntfy, clock):
    r = service.create_recipient("Oma", per_day=2, window_start="10:00", window_end="18:00")
    assert r.topic != r.control_topic
    assert (r.per_day, r.window_start) == (2, "10:00")
    assert r.next_send_at > clock()
    assert 10 <= r.next_send_at.hour < 18
    (welcome,) = fake_ntfy.on(r.topic)
    assert welcome["title"] == "Welkom bij Back in Frame"
    assert "2 herinneringen per dag" in welcome["message"]


def test_create_recipient_validation(service):
    with pytest.raises(ValueError):
        service.create_recipient("  ")
    with pytest.raises(ScheduleError):
        service.create_recipient("X", window_start="20:00", window_end="08:00")
    assert service.db.recipients() == []


def test_tick_sends_only_when_due(service, fake_ntfy, clock, media_dir):
    photos(media_dir)
    service.rescan()
    r = service.create_recipient("Vera", welcome=False)
    assert service.tick() == 0
    assert fake_ntfy.on(r.topic) == []

    clock.set(r.next_send_at)
    assert service.tick() == 1
    (msg,) = fake_ntfy.on(r.topic)
    assert msg["title"] == "Chili"
    assert "oktober 2024" in msg["message"]
    assert msg["attachment"]["name"].endswith(".jpg")
    Image.open(io.BytesIO(fake_ntfy.attachments[msg["id"]])).verify()
    assert msg["actions_raw"].count("http,") == 3
    assert r.control_topic in msg["actions_raw"]

    after = service.db.recipient(r.id)
    assert after.next_send_at > clock()
    assert service.tick() == 0  # not twice


def test_buttons_use_public_ntfy_url(tmp_path, fake_ntfy, clock, media_dir):
    s = Settings(media_dir=media_dir, data_dir=tmp_path, ntfy_url="http://ntfy:80",
                 ntfy_public_url="https://push.example.org").validate()
    svc = BackInFrame(s, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    photos(media_dir, 1)
    svc.rescan()
    r = svc.create_recipient("A", welcome=False)
    svc.send_now(r.id)
    assert f"https://push.example.org/{r.control_topic}" in fake_ntfy.on(r.topic)[0]["actions_raw"]
    assert svc.subscribe_url(r) == f"https://push.example.org/{r.topic}"


def test_empty_library(service, fake_ntfy):
    r = service.create_recipient("Leeg", welcome=False)
    res = service.send_now(r.id)
    assert res.status == "empty"
    assert fake_ntfy.on(r.topic) == []
    assert service.db.recent_sends(r.id)[0]["status"] == "skipped"


def test_broken_and_missing_files_are_skipped(service, fake_ntfy, media_dir):
    make_image(media_dir / "good.jpg")
    make_image(media_dir / "gone.jpg")
    service.rescan()
    (media_dir / "gone.jpg").unlink()
    r = service.create_recipient("A", welcome=False)
    for _ in range(3):
        assert service.send_now(r.id).status == "ok"
    names = {m["attachment"]["name"] for m in fake_ntfy.on(r.topic)}
    assert names == {"good.jpg"}
    assert [m.path for m in service.db.available_media()] == ["good.jpg"]


def test_ntfy_failure_is_logged_and_retried_next_time(service, fake_ntfy, clock, media_dir):
    photos(media_dir, 1)
    service.rescan()
    r = service.create_recipient("A", welcome=False)
    fake_ntfy.fail_publish = True
    clock.set(r.next_send_at)
    assert service.tick() == 0
    assert service.db.recent_sends(r.id)[0]["status"] == "error"
    assert service.db.recipient(r.id).next_send_at > clock()
    fake_ntfy.fail_publish = False
    assert service.send_now(r.id).status == "ok"


def test_attachment_limit_from_server_and_override(settings, clock, tmp_path):
    fake = FakeNtfy(attachment_limit=2 * 1024 * 1024)
    svc = BackInFrame(settings, db=Database(":memory:"), ntfy=fake.client(), clock=clock)
    assert svc.limits() == (2 * 1024 * 1024, 2 * 1024 * 1024)

    s2 = Settings(media_dir=settings.media_dir, data_dir=tmp_path, ntfy_url="https://x",
                  max_attachment_mb=100, video_max_mb=50).validate()
    svc2 = BackInFrame(s2, db=Database(":memory:"), ntfy=fake.client(), clock=clock)
    assert svc2.limits() == (100 * 1024 * 1024, 50 * 1024 * 1024)


def test_too_large_response_halves_the_limit(settings, clock, media_dir):
    fake = FakeNtfy(attachment_limit=4 * 1024 * 1024)
    svc = BackInFrame(settings, db=Database(":memory:"), ntfy=fake.client(), clock=clock, rng=random.Random(1))
    make_image(media_dir / "noise.jpg", size=(2048, 2048), noise=True)
    svc.rescan()
    r = svc.create_recipient("A", welcome=False)
    assert svc.server_limit() == 4 * 1024 * 1024
    fake.attachment_limit = 200 * 1024  # server got stricter than it told us
    assert svc.send_now(r.id).status == "error"
    assert svc.server_limit() == 2 * 1024 * 1024


def test_control_buttons(service, fake_ntfy, clock):
    r = service.create_recipient("Vera", welcome=False)
    service.poll_controls()  # establishes the starting point

    fake_ntfy.press(r.control_topic, "more")
    assert service.poll_controls() == 1
    assert service.db.recipient(r.id).per_day == 2
    assert "2 herinneringen per dag" in fake_ntfy.on(r.topic)[-1]["message"]

    # the same message is never applied twice
    assert service.poll_controls() == 0
    assert service.db.recipient(r.id).per_day == 2

    fake_ntfy.press(r.control_topic, "minder")
    fake_ntfy.press(r.control_topic, "less")
    fake_ntfy.press(r.control_topic, "nonsense")
    fake_ntfy.press("some-other-topic", "more")
    assert service.poll_controls() == 2
    r2 = service.db.recipient(r.id)
    assert (r2.per_day, r2.every_days) == (1, 2)


def test_pause_stops_sending(service, fake_ntfy, clock, media_dir):
    photos(media_dir, 2)
    service.rescan()
    r = service.create_recipient("A", welcome=False)
    service.handle_command(r.id, "pause")
    r = service.db.recipient(r.id)
    assert r.paused_until == clock() + timedelta(days=7)
    assert r.next_send_at >= r.paused_until
    assert "pauze" in fake_ntfy.on(r.topic)[-1]["message"].lower()

    clock.advance(days=3)
    assert service.tick() == 0
    clock.set(r.next_send_at)
    assert service.tick() == 1

    service.handle_command(r.id, "pause")
    service.handle_command(r.id, "resume")
    assert service.db.recipient(r.id).paused_until is None


def test_disabled_recipient(service, clock, media_dir, fake_ntfy):
    photos(media_dir, 1)
    service.rescan()
    r = service.create_recipient("A", welcome=False)
    r = service.update_recipient(r.id, enabled=False)
    assert r.next_send_at is None
    clock.advance(days=2)
    assert service.tick() == 0
    r = service.update_recipient(r.id, enabled=True)
    assert r.next_send_at is not None


def test_update_validation(service):
    r = service.create_recipient("A", welcome=False)
    with pytest.raises(ScheduleError):
        service.update_recipient(r.id, per_day=0)
    with pytest.raises(ValueError):
        service.update_recipient(r.id, name=" ")
    with pytest.raises(KeyError):
        service.update_recipient(999, per_day=1)


def test_settings_change_replans(service, clock):
    r = service.create_recipient("A", welcome=False, window_start="09:00", window_end="10:00")
    r2 = service.update_recipient(r.id, window_start="19:00", window_end="20:00")
    assert r2.next_send_at.hour == 19


def test_over_many_days_every_photo_comes_by(service, clock, media_dir, fake_ntfy):
    for folder in ("Chili", "Oostenrijk", "Texel"):
        photos(media_dir, 4, folder)
    service.rescan()
    r = service.create_recipient("A", welcome=False, per_day=3, min_gap_minutes=60)
    for _ in range(40):
        r = service.db.recipient(r.id)
        clock.set(r.next_send_at)
        service.tick()
    sent = fake_ntfy.on(r.topic)
    assert len(sent) == 40
    names = [(m["title"], m["attachment"]["name"]) for m in sent]
    assert len(set(names)) == 12  # every photo came by
    assert all(a != b for a, b in zip(names, names[1:], strict=False))  # never the same one twice in a row
    # 40 sends at 3 per day span 14 days
    assert sent and service.db.recipient(r.id).next_send_at.date() >= datetime(2026, 11, 20).date()


def test_rescan_records_time(service, clock):
    service.rescan()
    assert service.db.get_state("last_scan") == clock().isoformat()


def test_button_pressed_before_first_poll_is_not_lost(service, fake_ntfy):
    r = service.create_recipient("A", welcome=False)
    fake_ntfy.press(r.control_topic, "more", ago=30)  # pressed well before the next poll
    assert service.poll_controls() == 1
    assert service.db.recipient(r.id).per_day == 2
