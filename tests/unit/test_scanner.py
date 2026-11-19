import os
from datetime import datetime

import pytest
from helpers import has_ffmpeg, make_image, make_video

from backinframe.scanner import image_metadata, kind_for, scan


def test_kind_for(tmp_path):
    assert kind_for(tmp_path / "a.JPG") == "image"
    assert kind_for(tmp_path / "a.mov") == "video"
    assert kind_for(tmp_path / "a.txt") is None


def test_exif_date_is_read_but_location_is_not(tmp_path, db, media_dir):
    p = make_image(media_dir / "x.jpg", taken=datetime(2019, 7, 1, 14, 30), gps=(-41.4693, -72.9424))
    assert image_metadata(p) == datetime(2019, 7, 1, 14, 30)
    scan(db, media_dir)
    columns = {r["name"] for r in db.query("PRAGMA table_info(media)")}
    assert not columns & {"lat", "lon"}  # nowhere to keep a location


def test_locations_from_older_versions_are_removed(tmp_path):
    import sqlite3

    from backinframe.db import Database

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, "
                "size INTEGER NOT NULL, mtime REAL NOT NULL, taken_at TEXT, lat REAL, lon REAL, "
                "missing INTEGER NOT NULL DEFAULT 0, broken INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO media(path, kind, size, mtime, taken_at, lat, lon) "
                "VALUES('Chili/a.jpg', 'image', 1, 0, '2019-07-01T14:30:00', -41.4, -72.9)")
    old.commit()
    old.close()
    db = Database(path)
    assert not {r["name"] for r in db.query("PRAGMA table_info(media)")} & {"lat", "lon"}
    (media,) = db.available_media()
    assert media.path == "Chili/a.jpg" and media.taken_at == datetime(2019, 7, 1, 14, 30)
    db.close()


def test_scan_adds_updates_and_removes(db, media_dir):
    make_image(media_dir / "Chili" / "a.jpg", taken=datetime(2024, 10, 3))
    make_image(media_dir / "Chili" / "b.png")
    (media_dir / "notes.txt").write_text("not media")
    make_image(media_dir / ".hidden" / "c.jpg")
    make_image(media_dir / "@eaDir" / "d.jpg")
    make_image(media_dir / "._e.jpg")

    r = scan(db, media_dir)
    assert (r.added, r.updated, r.removed, r.total) == (2, 0, 0, 2)
    items = {m.path: m for m in db.available_media()}
    assert set(items) == {"Chili/a.jpg", "Chili/b.png"}
    assert items["Chili/a.jpg"].taken_at == datetime(2024, 10, 3)
    assert items["Chili/b.png"].taken_at is not None  # falls back to file time

    # unchanged -> nothing to do
    r = scan(db, media_dir)
    assert (r.added, r.updated, r.removed) == (0, 0, 0)

    # changed file -> updated
    make_image(media_dir / "Chili" / "a.jpg", taken=datetime(2024, 10, 4), size=(100, 100))
    os.utime(media_dir / "Chili" / "a.jpg", (1, 1))
    r = scan(db, media_dir)
    assert r.updated == 1

    # removed file -> marked missing, and comes back when restored
    (media_dir / "Chili" / "b.png").unlink()
    r = scan(db, media_dir)
    assert r.removed == 1
    assert [m.path for m in db.available_media()] == ["Chili/a.jpg"]


def test_scan_ignores_corrupt_images(db, media_dir):
    (media_dir / "broken.jpg").write_bytes(b"definitely not a jpeg")
    make_image(media_dir / "ok.jpg")
    r = scan(db, media_dir)
    assert r.added == 1


def test_scan_missing_folder(db, tmp_path):
    r = scan(db, tmp_path / "nope")
    assert r.total == 0


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_scan_video_creation_time(db, media_dir):
    make_video(media_dir / "clip.mp4", seconds=1, creation="2022-08-15T10:00:00Z")
    scan(db, media_dir)
    (item,) = db.available_media()
    assert item.kind == "video"
    assert item.taken_at == datetime(2022, 8, 15, 10, 0)
    assert db.available_media(include_videos=False) == []
