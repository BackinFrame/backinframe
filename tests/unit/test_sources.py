"""Copying albums from Immich and folders from Nextcloud."""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from source_fakes import IMMICH, IMMICH_KEY, NC_PASSWORD, NC_USER, NEXTCLOUD, Asset, Remotes, jpeg

from backinframe import sources
from backinframe.config import ConfigError, Settings, from_env

MB = 1024 * 1024


@pytest.fixture
def remotes():
    return Remotes()


def immich(remotes, albums=(), max_bytes=50 * MB):
    return sources.ImmichSource(IMMICH, IMMICH_KEY, albums, max_bytes=max_bytes, client=remotes.client())


def nextcloud(remotes, folder="Photos", max_bytes=50 * MB):
    return sources.NextcloudSource(NEXTCLOUD, NC_USER, NC_PASSWORD, folder, max_bytes=max_bytes,
                                   client=remotes.client())


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
                  and not p.name.startswith("."))


# -- Immich ---------------------------------------------------------------------------

def test_immich_copies_the_chosen_albums_as_previews_with_the_date(remotes, tmp_path):
    taken = datetime(2025, 11, 7, 14, 30)
    remotes.immich.add("a1", "Chile 2025", Asset("as1", "IMG_0001.HEIC", taken, body=jpeg()),
                       Asset("as2", "IMG_0002.JPG", datetime(2025, 11, 8), body=jpeg("blue")))
    remotes.immich.add("a2", "Work", Asset("as3", "slide.png", taken, body=jpeg()))
    res = immich(remotes, ["chile 2025"]).sync(tmp_path)
    assert (res.downloaded, res.kept, res.removed) == (2, 0, 0)
    assert files(tmp_path) == ["Chile 2025/IMG_0001.jpg", "Chile 2025/IMG_0002.jpg"]
    # the preview has no EXIF: the date travels as the file's modification time
    assert datetime.fromtimestamp(os.stat(tmp_path / "Chile 2025/IMG_0001.jpg").st_mtime) == taken
    urls = [str(r.url) for r in remotes.immich.requests]
    assert any("/api/assets/as1/thumbnail?size=preview" in u for u in urls)
    assert not any("/original" in u for u in urls)  # photos never need the original (no GPS on disk)


def test_immich_without_album_names_takes_every_album_and_shared_ones(remotes, tmp_path):
    remotes.immich.add("a1", "Mine", Asset("x1", "a.jpg", datetime(2024, 1, 1), body=jpeg()))
    remotes.immich.add("a2", "From Vera", Asset("x2", "b.jpg", datetime(2024, 1, 2), body=jpeg(), owner="vera"),
                       shared=True)
    immich(remotes).sync(tmp_path)
    assert files(tmp_path) == ["From Vera/b.jpg", "Mine/a.jpg"]  # Vera's photo via the album itself


def test_immich_second_sync_downloads_only_what_changed(remotes, tmp_path):
    a = Asset("x1", "a.jpg", datetime(2024, 1, 1), body=jpeg())
    b = Asset("x2", "b.jpg", datetime(2024, 1, 2), body=jpeg())
    remotes.immich.add("a1", "Trip", a, b)
    src = immich(remotes)
    src.sync(tmp_path)
    remotes.immich.requests.clear()
    b.checksum, b.body = "c2", jpeg("green")
    remotes.immich.albums["a1"]["assets"] = [b]  # a left the album, b was edited
    res = src.sync(tmp_path)
    assert (res.downloaded, res.kept, res.removed) == (1, 0, 1)
    assert files(tmp_path) == ["Trip/b.jpg"]
    assert sum("/thumbnail" in str(r.url) for r in remotes.immich.requests) == 1
    res = src.sync(tmp_path)
    assert (res.downloaded, res.kept) == (0, 1)


def test_immich_videos_come_as_originals_and_too_large_ones_are_skipped(remotes, tmp_path):
    remotes.immich.add("a1", "Trip", Asset("v1", "clip.MOV", datetime(2024, 1, 1), kind="VIDEO", body=b"x" * 100),
                       Asset("v2", "long.mp4", datetime(2024, 1, 1), kind="VIDEO", body=b"x", size=60 * MB))
    res = immich(remotes, max_bytes=50 * MB).sync(tmp_path)
    assert files(tmp_path) == ["Trip/clip.MOV"] and res.skipped == 1
    assert any("/api/assets/v1/original" in str(r.url) for r in remotes.immich.requests)


def test_immich_same_name_twice_in_an_album_gets_two_files(remotes, tmp_path):
    remotes.immich.add("a1", "Trip", Asset("aaaa1111", "IMG_1.jpg", datetime(2024, 1, 1), body=jpeg()),
                       Asset("bbbb2222", "IMG_1.jpg", datetime(2024, 1, 2), body=jpeg("red")))
    immich(remotes).sync(tmp_path)
    assert files(tmp_path) == ["Trip/IMG_1-bbbb2222.jpg", "Trip/IMG_1.jpg"]


def test_immich_older_server_lists_assets_on_the_album(remotes, tmp_path):
    remotes.immich.old_api = True
    remotes.immich.add("a1", "Trip", Asset("x1", "a.jpg", datetime(2024, 1, 1), body=jpeg()))
    immich(remotes).sync(tmp_path)
    assert files(tmp_path) == ["Trip/a.jpg"]


def test_immich_wrong_key_or_server_down_keeps_the_copies(remotes, tmp_path):
    remotes.immich.add("a1", "Trip", Asset("x1", "a.jpg", datetime(2024, 1, 1), body=jpeg()))
    immich(remotes).sync(tmp_path)
    bad = sources.ImmichSource(IMMICH, "wrong", (), max_bytes=MB, client=remotes.client())
    with pytest.raises(sources.SourceError, match="access refused") as exc:
        bad.sync(tmp_path)
    assert "wrong" not in str(exc.value) and IMMICH_KEY not in str(exc.value)
    remotes.immich.down = True
    with pytest.raises(sources.SourceError, match="cannot reach"):
        immich(remotes).sync(tmp_path)
    assert files(tmp_path) == ["Trip/a.jpg"]


def test_immich_album_names_cannot_escape_the_folder(remotes, tmp_path):
    remotes.immich.add("a1", "../../etc", Asset("x1", "../passwd.jpg", datetime(2024, 1, 1), body=jpeg()))
    immich(remotes).sync(tmp_path / "immich")
    assert files(tmp_path) == ["immich/_.._etc/_passwd.jpg"]


# -- Nextcloud --------------------------------------------------------------------------

def test_nextcloud_copies_a_folder_with_subfolders_as_albums(remotes, tmp_path):
    nc = remotes.nextcloud
    nc.put("Photos/Chile 2025/a.jpg", jpeg(exif_date=datetime(2025, 11, 7)))
    nc.put("Photos/Chile 2025/notes.txt", b"hi")
    nc.put("Photos/Rome & more/b.jpg", jpeg())
    nc.put("Photos/loose.jpg", jpeg())
    nc.put("Photos/.hidden/c.jpg", jpeg())
    nc.put("Other/x.jpg", jpeg())
    res = nextcloud(remotes).sync(tmp_path)
    assert files(tmp_path) == ["Chile 2025/a.jpg", "Photos/loose.jpg", "Rome & more/b.jpg"]
    assert res.skipped == 1  # notes.txt
    assert all(r.method in ("PROPFIND", "GET") for r in nc.requests)  # read-only


def test_nextcloud_uses_the_etag(remotes, tmp_path):
    nc = remotes.nextcloud
    nc.put("Photos/Trip/a.jpg", jpeg(), etag="v1")
    src = nextcloud(remotes)
    src.sync(tmp_path)
    nc.requests.clear()
    assert src.sync(tmp_path).kept == 1 and not [r for r in nc.requests if r.method == "GET"]
    nc.put("Photos/Trip/a.jpg", jpeg("red"), etag="v2")
    assert src.sync(tmp_path).downloaded == 1


def test_nextcloud_wrong_password(remotes, tmp_path):
    bad = sources.NextcloudSource(NEXTCLOUD, NC_USER, "nope", "Photos", max_bytes=MB, client=remotes.client())
    with pytest.raises(sources.SourceError, match="access refused") as exc:
        bad.sync(tmp_path)
    assert "nope" not in str(exc.value)


def test_nextcloud_missing_folder(remotes, tmp_path):
    remotes.nextcloud.put("Photos/a.jpg", jpeg())
    with pytest.raises(sources.SourceError, match="not found"):
        nextcloud(remotes, folder="Fotos").sync(tmp_path)


def test_a_failed_download_keeps_the_old_copy(remotes, tmp_path):
    nc = remotes.nextcloud
    nc.put("Photos/Trip/a.jpg", jpeg(), etag="v1")
    src = nextcloud(remotes, max_bytes=MB)
    src.sync(tmp_path)
    old = (tmp_path / "Trip/a.jpg").read_bytes()
    nc.put("Photos/Trip/a.jpg", jpeg("red"), etag="v2")
    nc.fail_get.add("Photos/Trip/a.jpg")  # the new version cannot be fetched right now
    res = src.sync(tmp_path)
    assert files(tmp_path) == ["Trip/a.jpg"] and (tmp_path / "Trip/a.jpg").read_bytes() == old
    assert res.errors and "500" in res.errors[0]
    assert not list(tmp_path.rglob(".part-*"))
    nc.fail_get.clear()
    assert src.sync(tmp_path).downloaded == 1  # and it is fetched next time


def test_a_file_that_grew_too_large_is_dropped(remotes, tmp_path):
    nc = remotes.nextcloud
    nc.put("Photos/Trip/a.mp4", b"x" * 100, etag="v1")
    src = nextcloud(remotes, max_bytes=MB)
    src.sync(tmp_path)
    nc.put("Photos/Trip/a.mp4", b"x" * (2 * MB), etag="v2")
    res = src.sync(tmp_path)
    assert files(tmp_path) == [] and res.skipped == 1


# -- settings ---------------------------------------------------------------------------

def test_settings_for_sources(tmp_path):
    s = from_env({"BACKINFRAME_IMMICH_URL": IMMICH, "BACKINFRAME_IMMICH_API_KEY": "k",
                  "BACKINFRAME_IMMICH_ALBUMS": "Chile 2025, Rome\\, 2024",
                  "BACKINFRAME_NEXTCLOUD_URL": NEXTCLOUD, "BACKINFRAME_NEXTCLOUD_USER": "anna",
                  "BACKINFRAME_NEXTCLOUD_PASSWORD": "p", "BACKINFRAME_NEXTCLOUD_FOLDER": "Photos/Trips",
                  "BACKINFRAME_SYNC_MINUTES": "30"})
    assert s.immich_albums == ("Chile 2025", "Rome, 2024")
    assert s.immich_enabled and s.nextcloud_enabled and s.sync_minutes == 30
    assert set(sources.build_sources(s)) == {"immich", "nextcloud"}
    assert sources.build_sources(Settings()) == {}


@pytest.mark.parametrize("kw,msg", [
    ({"immich_url": IMMICH}, "Immich needs"),
    ({"immich_url": "immich.local", "immich_api_key": "k"}, "immich_url must start"),
    ({"nextcloud_url": NEXTCLOUD, "nextcloud_user": "a"}, "Nextcloud needs"),
    ({"sync_minutes": 1}, "sync_minutes"),
])
def test_bad_source_settings(kw, msg):
    with pytest.raises(ConfigError, match=msg):
        Settings(**kw).validate()
