"""Immich and Nextcloud end to end: sync, scan, send, and what the admin page shows."""

from __future__ import annotations

import random
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import make_image
from source_fakes import IMMICH, IMMICH_KEY, NC_PASSWORD, NC_USER, NEXTCLOUD, Asset, Remotes, jpeg

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app


def build(tmp_path, fake_ntfy, clock, remotes, **kw):
    base = dict(media_dir=tmp_path / "media", data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                language="en", immich_url=IMMICH, immich_api_key=IMMICH_KEY, nextcloud_url=NEXTCLOUD,
                nextcloud_user=NC_USER, nextcloud_password=NC_PASSWORD, nextcloud_folder="Photos")
    base.update(kw)
    svc = BackInFrame(Settings(**base).validate(), db=Database(":memory:"), ntfy=fake_ntfy.client(),
                         clock=clock, rng=random.Random(5))
    svc.http_client = remotes.client()
    return svc


@pytest.fixture
def remotes():
    r = Remotes()
    r.immich.add("a1", "Chile 2025", Asset("as1", "IMG_1.HEIC", datetime(2025, 11, 7, 10), body=jpeg()))
    r.nextcloud.put("Photos/Rome 2019/b.jpg", jpeg("blue", exif_date=datetime(2019, 5, 1, 9)))
    return r


def test_sync_scan_and_send_from_immich_and_nextcloud(tmp_path, fake_ntfy, clock, remotes):
    make_image(tmp_path / "media" / "Lisbon 2023" / "c.jpg", taken=datetime(2023, 11, 7, 12))
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    status = svc.sync_sources()
    assert status["immich"]["ok"] and status["immich"]["downloaded"] == 1
    assert status["nextcloud"]["ok"] and status["nextcloud"]["downloaded"] == 1
    paths = sorted(r["path"] for r in svc.db.query("SELECT path FROM media WHERE missing = 0"))
    assert paths == ["@immich/Chile 2025/IMG_1.jpg", "@nextcloud/Rome 2019/b.jpg", "Lisbon 2023/c.jpg"]
    taken = {r["path"]: r["taken_at"] for r in svc.db.query("SELECT path, taken_at FROM media")}
    assert taken["@immich/Chile 2025/IMG_1.jpg"] == "2025-11-07T10:00:00"  # from Immich, not the file
    assert taken["@nextcloud/Rome 2019/b.jpg"] == "2019-05-01T09:00:00"  # from the photo's EXIF

    r = svc.create_recipient("Oma", delivery="ntfy", welcome=False)
    titles = set()
    for _ in range(12):
        assert svc.send_now(r.id).status == "ok"
        titles.add(fake_ntfy.messages[-1]["title"])
    # the album is the folder after the "@source/" part, also in the caption
    assert {"Chile 2025", "Rome 2019", "Lisbon 2023"} <= {t.split(" · ")[0] for t in titles}


def test_a_source_switched_off_takes_its_photos_out(tmp_path, fake_ntfy, clock, remotes):
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    svc.sync_sources()
    assert svc.db.media_counts()["image"] == 2
    svc2 = build(tmp_path, fake_ntfy, clock, remotes, nextcloud_url="", nextcloud_user="", nextcloud_password="",
                 nextcloud_folder="")
    svc2.db = svc.db
    svc2.rescan()
    assert svc.db.media_counts()["image"] == 1


def test_an_album_removed_in_immich_disappears_after_the_next_sync(tmp_path, fake_ntfy, clock, remotes):
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    svc.sync_sources()
    remotes.immich.albums.clear()
    status = svc.sync_sources()
    assert status["immich"]["removed"] == 1
    assert [r["path"] for r in svc.db.query("SELECT path FROM media WHERE missing = 0")] == [
        "@nextcloud/Rome 2019/b.jpg"]


def test_unreachable_source_keeps_its_photos_and_shows_the_error(tmp_path, fake_ntfy, clock, remotes):
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    svc.sync_sources()
    remotes.immich.down = True
    status = svc.sync_sources()
    assert status["immich"]["ok"] is False and "cannot reach" in status["immich"]["error"]
    assert svc.db.media_counts()["image"] == 2
    with TestClient(create_app(svc)) as client:
        page = client.get("/").text
    assert "Immich and Nextcloud" in page and "cannot reach" in page
    assert IMMICH_KEY not in page and NC_PASSWORD not in page


def test_a_photo_folder_named_like_a_source_is_not_mixed_in(tmp_path, fake_ntfy, clock, remotes):
    make_image(tmp_path / "media" / "@immich" / "Fake" / "x.jpg")
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    svc.sync_sources()
    paths = {r["path"] for r in svc.db.query("SELECT path FROM media")}
    assert "@immich/Fake/x.jpg" not in paths


def test_admin_page_sync_button(tmp_path, fake_ntfy, clock, remotes):
    svc = build(tmp_path, fake_ntfy, clock, remotes)
    with TestClient(create_app(svc)) as client:
        page = client.get("/").text
        assert "not synced yet" in page and "Sync now" in page
        r = client.post("/sources/sync")
        assert "Sync started" in r.text
        svc._sync_thread.join(timeout=10)
        page = client.get("/").text
    assert "1 new, 0 unchanged, 0 removed" in page
    assert svc.db.media_counts()["image"] == 2


def test_no_sources_no_section(service):
    with TestClient(create_app(service)) as client:
        page = client.get("/").text
    assert "Immich" not in page
