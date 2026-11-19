"""Sharing the work: "from …" per album or photo, upload links for people without access, and more
admins (invited by another admin, own password, own second step)."""

from __future__ import annotations

import io
import random
import re
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import make_image
from PIL import Image

from backinframe import adminauth, caption, totp
from backinframe.app import create_recipient_app
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

ADMIN = "https://admin.test"
APP = "https://photos.example.org"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock):
    make_image(media_dir / "Chile 2025" / "lake.jpg", taken=datetime(2025, 3, 2))
    make_image(media_dir / "Chile 2025" / "snow.jpg", taken=datetime(2025, 3, 4))
    make_image(media_dir / "Rome" / "forum.jpg", taken=datetime(2019, 6, 1))
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                        language="en", admin_password=PASSWORD, app_url=APP).validate()
    s = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                       rng=random.Random(2))
    s.rescan()
    yield s
    s.db.close()


def admin(svc) -> TestClient:
    c = TestClient(create_app(svc), base_url=ADMIN)
    c.post("/login", data={"password": PASSWORD})
    return c


def jpeg(color="orange", size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def mid(svc, name):
    return next(r["id"] for r in svc.db.query("SELECT id, path FROM media") if r["path"].endswith(name))


# -- "from …" ------------------------------------------------------------------------------------

def test_album_sender_for_all_photos_and_new_ones(svc, media_dir, clock):
    with admin(svc) as c:
        c.post("/albums/sender", data={"folder": "Chile 2025", "sender": "Anna"})
        assert 'value="Anna"' in c.get("/photos", params={"folder": "Chile 2025"}).text
    assert svc.db.media_by_id(mid(svc, "lake.jpg")).sender == "Anna"
    assert svc.db.media_by_id(mid(svc, "forum.jpg")).sender == ""
    make_image(media_dir / "Chile 2025" / "new.jpg", taken=datetime(2025, 3, 5))
    svc.rescan()
    m = svc.db.media_by_id(mid(svc, "new.jpg"))
    assert m.sender == "Anna"
    _, text = caption.build(m, clock().date(), "en")
    assert text.endswith("· from Anna")
    assert caption.build(m, clock().date(), "nl")[1].endswith("· van Anna")


def test_sender_per_photo_wins_and_shows_in_the_web_app(svc):
    lake = mid(svc, "lake.jpg")
    with admin(svc) as c:
        c.post("/albums/sender", data={"folder": "Chile 2025", "sender": "Anna"})
        c.post(f"/photos/{lake}", data={"sender": "Opa Piet", "note": "Bij het meer"})
    m = svc.db.media_by_id(lake)
    assert m.sender == "Opa Piet"
    title, text = caption.build(m, svc.now().date(), "en")
    assert text.splitlines() == [text.splitlines()[0], "Bij het meer"] and "from Opa Piet" in text
    r = svc.create_recipient("Oma")
    for name in ("snow.jpg", "forum.jpg"):
        svc.db.execute("UPDATE media SET missing = 1 WHERE id = ?", (mid(svc, name),))
    assert svc.send_now(r.id).status == "ok"
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        assert "from Opa Piet" in phone.get(f"/a/{r.app_token}/").text


def test_album_sender_only_for_known_folders(svc):
    with admin(svc) as c:
        assert c.post("/albums/sender", data={"folder": "../etc", "sender": "x"}).status_code == 404


# -- upload links --------------------------------------------------------------------------------

def make_link(c, album="Kleinkinderen", name="Anna") -> str:
    c.post("/uploads", data={"album": album, "name": name})
    page = c.get("/uploads").text
    return re.search(r'href="' + re.escape(APP) + r'/u/([\w-]+)"', page).group(1)


def test_upload_link_adds_photos_with_the_name_as_sender(svc, tmp_path):
    with admin(svc) as c:
        token = make_link(c)
        assert "Anna → Kleinkinderen" in c.get("/uploads").text
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        page = phone.get(f"/u/{token}")
        assert page.status_code == 200 and "Kleinkinderen" in page.text and "Anna" in page.text
        assert "form-action 'self'" in page.headers["content-security-policy"]
        r = phone.post(f"/u/{token}", files=[("files", ("IMG 1.JPG", jpeg(), "image/jpeg")),
                                             ("files", ("../../evil.jpg", jpeg("teal"), "image/jpeg")),
                                             ("files", ("note.txt", b"hello", "text/plain")),
                                             ("files", ("fake.jpg", b"not a jpeg at all", "image/jpeg"))])
    assert r.status_code == 200 and "Added: 2" in r.text
    assert "note.txt (not a photo or video)" in r.text and "fake.jpg (could not be read)" in r.text
    rows = svc.db.query("SELECT path, sender FROM media WHERE path LIKE '@upload/%' ORDER BY path")
    assert [(row["path"], row["sender"]) for row in rows] == [
        ("@upload/Kleinkinderen/IMG_1.jpg", "Anna"), ("@upload/Kleinkinderen/evil.jpg", "Anna")]
    folder = svc.settings.uploads_dir / "Kleinkinderen"
    assert sorted(p.name for p in folder.iterdir()) == ["IMG_1.jpg", "evil.jpg"]
    assert not list(tmp_path.glob("**/.*.part"))
    assert svc.db.upload_links()[0]["files"] == 2


def test_same_name_twice_is_not_overwritten(svc):
    with admin(svc) as c:
        token = make_link(c)
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        for color in ("orange", "teal"):
            phone.post(f"/u/{token}", files=[("files", ("a.jpg", jpeg(color), "image/jpeg"))])
    names = sorted(p.name for p in (svc.settings.uploads_dir / "Kleinkinderen").iterdir())
    assert names == ["a-1.jpg", "a.jpg"]


def test_uploaded_photos_get_sent_and_can_be_removed(svc):
    with admin(svc) as c:
        token = make_link(c, album="Chile 2025", name="Bram")
        with TestClient(create_recipient_app(svc), base_url=APP) as phone:
            phone.post(f"/u/{token}", files=[("files", ("x.jpg", jpeg(), "image/jpeg"))])
        up = mid(svc, "x.jpg")
        listing = c.get("/photos").text
        assert "Chile 2025 (uploaded)" in listing
        page = c.get(f"/photos/{up}").text
        assert f'action="/photos/{up}/delete"' in page
        assert "/delete" not in c.get(f"/photos/{mid(svc, 'lake.jpg')}").text  # own folder: not here
        r = svc.create_recipient("Sanne", delivery="ntfy")
        for name in ("lake.jpg", "snow.jpg", "forum.jpg"):
            svc.db.execute("UPDATE media SET missing = 1 WHERE id = ?", (mid(svc, name),))
        assert svc.send_now(r.id).status == "ok"
        assert c.post(f"/photos/{mid(svc, 'lake.jpg')}/delete").status_code == 404
        c.post(f"/photos/{up}/delete")
    assert svc.db.media_by_id(up) is None
    assert not (svc.settings.uploads_dir / "Chile 2025" / "x.jpg").exists()
    assert (svc.settings.media_dir / "Chile 2025" / "lake.jpg").exists()


def test_revoked_or_unknown_link_and_cross_site(svc):
    with admin(svc) as c:
        token = make_link(c)
        link_id = svc.db.upload_links()[0]["id"]
        with TestClient(create_recipient_app(svc), base_url=APP) as phone:
            r = phone.post(f"/u/{token}", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))],
                           headers={"Origin": "https://evil.example"})
            assert r.status_code == 403
            c.post(f"/uploads/{link_id}/revoke")
            assert phone.get(f"/u/{token}").status_code == 404
            assert phone.post(f"/u/{token}", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))]).status_code == 404
            assert phone.get("/u/" + "x" * 30).status_code == 404
    assert not (svc.settings.uploads_dir / "Kleinkinderen").exists()


def test_link_cannot_escape_its_folder(svc):
    for album in ("../../etc", "@immich", "a/b", "  ..  "):
        try:
            svc.create_upload_link(album, "Eve")
        except Exception:  # noqa: BLE001 - refused is fine
            continue
        folder = svc.db.upload_links()[-1]["album"]
        assert "/" not in folder and not folder.startswith((".", "@"))


def test_too_large_and_too_many(svc):
    small = replace(svc.settings, upload_max_mb=0.001)  # 1 KB
    svc.settings = small
    with admin(svc) as c:
        token = make_link(c)
    with TestClient(create_recipient_app(svc), base_url=APP) as phone:
        r = phone.post(f"/u/{token}", files=[("files", ("big.jpg", jpeg(size=(800, 600)), "image/jpeg"))])
        assert "big.jpg (too large)" in r.text


def test_upload_links_need_the_app_url(svc):
    svc.settings = replace(svc.settings, app_url="")
    with admin(svc) as c:
        page = c.get("/uploads").text
        assert "BACKINFRAME_APP_URL" in page and 'name="album"' not in page
        c.post("/uploads", data={"album": "x", "name": "y"})
    assert svc.db.upload_links() == []


# -- more admins ---------------------------------------------------------------------------------

def invite(c, name="Anna") -> str:
    page = c.post("/admins", data={"name": name}).text
    return re.search(r"/invite/([\w-]+)", page).group(1)


def test_invite_accept_login_with_name(svc):
    with admin(svc) as c:
        token = invite(c)
        assert "Anna" in c.get("/admins").text
    with TestClient(create_app(svc), base_url=ADMIN) as anna:
        form = anna.get(f"/invite/{token}")
        assert form.status_code == 200 and "Anna" in form.text
        assert anna.post(f"/invite/{token}", data={"password": "short", "again": "short"}).status_code == 400
        assert anna.post(f"/invite/{token}", data={"password": "a long password 1", "again": "other one 12"}
                         ).status_code == 400
        done = anna.post(f"/invite/{token}", data={"password": "a long password 1", "again": "a long password 1"})
        assert done.status_code == 200 and "Anna" in done.text
        assert anna.get("/photos").status_code == 200  # logged in straight away
        assert anna.get(f"/invite/{token}").status_code == 404  # used up
    with TestClient(create_app(svc), base_url=ADMIN) as fresh:
        assert 'name="name"' in fresh.get("/login").text
        assert fresh.post("/login", data={"name": "anna", "password": "wrong password 1"}).status_code == 400
        fresh.post("/login", data={"name": "ANNA", "password": "a long password 1"})
        assert fresh.get("/admins").status_code == 200
        # and the main admin still logs in without a name
    with admin(svc) as main:
        assert main.get("/admins").status_code == 200


def test_new_admin_can_invite_and_remove_others(svc):
    with admin(svc) as c:
        token = invite(c, "Anna")
    with TestClient(create_app(svc), base_url=ADMIN) as anna:
        anna.post(f"/invite/{token}", data={"password": "anna password 1", "again": "anna password 1"})
        bram_token = invite(anna, "Bram")
        bram = next(a for a in svc.db.query("SELECT * FROM admins") if a["name"] == "Bram")
        assert bram["created_by"] == "Anna" and bram["password_hash"] == ""
        with TestClient(create_app(svc), base_url=ADMIN) as b:
            b.post(f"/invite/{bram_token}", data={"password": "bram password 1", "again": "bram password 1"})
            assert b.get("/").status_code == 200
            anna.post(f"/admins/{bram['id']}/remove")
            assert b.get("/", follow_redirects=False).status_code in (302, 303)  # session gone
        assert anna.post("/admins/0/remove").status_code == 404  # the main admin cannot be removed


def test_invitation_expires_and_duplicates(svc, clock):
    from datetime import timedelta
    with admin(svc) as c:
        token = invite(c, "Anna")
        assert "already an admin" not in c.post("/admins", data={"name": "anna"}).text  # new link, same person
        clock.advance(days=8)
        with TestClient(create_app(svc), base_url=ADMIN) as anna:
            assert anna.get(f"/invite/{token}").status_code == 404
        assert timedelta(days=adminauth.INVITE_DAYS) < timedelta(days=8)


def test_second_step_per_admin(svc):
    with admin(svc) as c:
        token = invite(c, "Anna")
    with TestClient(create_app(svc), base_url=ADMIN) as anna:
        anna.post(f"/invite/{token}", data={"password": "anna password 1", "again": "anna password 1"})
        page = anna.get("/security/2fa").text
        key = re.search(r'<code translate="no">([A-Z2-7 ]+)</code>', page).group(1).replace(" ", "")
        anna.post("/security/2fa", data={"code": totp.code_at(key, int(svc.now().timestamp() // 30))})
    aid = svc.db.query("SELECT id FROM admins")[0]["id"]
    auth = adminauth.AdminAuth(svc.db, PASSWORD, svc.settings.data_dir, svc.now)
    assert auth.totp_enabled(aid) and not auth.totp_enabled(adminauth.MAIN)
    with TestClient(create_app(svc), base_url=ADMIN) as again:
        r = again.post("/login", data={"name": "Anna", "password": "anna password 1"}, follow_redirects=False)
        assert r.headers["location"].startswith("/login/2fa")
    with admin(svc) as main:  # the main admin has no second step
        assert main.get("/", follow_redirects=False).status_code == 200


def test_own_password_change_ends_other_sessions(svc):
    with admin(svc) as c:
        token = invite(c, "Anna")
    with TestClient(create_app(svc), base_url=ADMIN) as one, TestClient(create_app(svc), base_url=ADMIN) as two:
        one.post(f"/invite/{token}", data={"password": "anna password 1", "again": "anna password 1"})
        two.post("/login", data={"name": "Anna", "password": "anna password 1"})
        assert two.get("/").status_code == 200
        one.post("/security/password", data={"current": "anna password 1", "new": "anna password 2"})
        assert one.get("/").status_code == 200
        assert two.get("/", follow_redirects=False).status_code in (302, 303)
    assert adminauth.verify_password("anna password 2", svc.db.query("SELECT password_hash FROM admins")[0][0])


def test_admins_need_the_server_password(svc):
    svc.settings = replace(svc.settings, admin_password="")
    with TestClient(create_app(svc), base_url=ADMIN) as c:
        assert "BACKINFRAME_ADMIN_PASSWORD" in c.get("/admins").text
        c.post("/admins", data={"name": "Anna"})
    assert svc.db.query("SELECT * FROM admins") == []


def test_passwords_are_hashed():
    stored = adminauth.hash_password("a long password 1")
    assert stored.startswith("scrypt$") and "a long password" not in stored
    assert adminauth.verify_password("a long password 1", stored)
    assert not adminauth.verify_password("a long password 2", stored)
    assert not adminauth.verify_password("x", "garbage")
