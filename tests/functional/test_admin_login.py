"""Login for the admin page: password, optional two-step verification, sessions on the server."""

from __future__ import annotations

import dataclasses
import os
import random
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backinframe import totp
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app, safe_next

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock):
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                        language="nl", admin_password=PASSWORD).validate()
    service = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                             rng=random.Random(3))
    yield service
    service.db.close()


def client(svc, **kw) -> TestClient:
    return TestClient(create_app(svc), **kw)


def log_in(c: TestClient, password: str = PASSWORD):
    return c.post("/login", data={"password": password})


def turn_on(c: TestClient, svc):
    page = c.get("/security/2fa")
    key = re.search(r'<code translate="no">([A-Z2-7 ]+)</code>', page.text).group(1).replace(" ", "")
    done = c.post("/security/2fa", data={"code": totp.code_at(key, int(svc.now().timestamp() // 30))})
    codes = re.findall(r"<li>([a-z2-9]{5}-[a-z2-9]{5})</li>", done.text)
    return key, codes


def code(svc, key, offset=0):
    return totp.code_at(key, int(svc.now().timestamp() // 30) + offset)


def test_login_logout_and_the_cookie(svc):
    with client(svc, base_url="https://admin.test") as c:
        first = c.get("/recipients/3?x=1", follow_redirects=False)
        assert first.headers["location"] == "/login?next=%2Frecipients%2F3%3Fx%3D1"
        login_page = c.get("/login")
        assert 'type="password"' in login_page.text and "Uitloggen" not in login_page.text
        r = c.post("/login", data={"password": PASSWORD, "next": "/"}, follow_redirects=False)
        cookie = r.headers["set-cookie"].lower()
        assert "bif_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
        page = c.get("/")
        assert "Uitloggen" in page.text and "Beveiliging" in page.text
        c.post("/logout")
        assert c.get("/", follow_redirects=False).status_code == 303


def test_sessions_are_stored_as_hashes_and_logout_ends_them_on_the_server(svc):
    with client(svc) as c:
        log_in(c)
        token = c.cookies["bif_admin"]
        rows = svc.db.query("SELECT token_hash FROM admin_sessions")
        assert len(rows) == 1 and token not in rows[0]["token_hash"]
        c.post("/logout")
    with client(svc, cookies={"bif_admin": token}) as thief:
        assert thief.get("/", follow_redirects=False).status_code == 303  # a copied cookie no longer works


def test_login_gives_a_new_session_id(svc):
    with client(svc) as c:
        log_in(c)
        old = c.cookies["bif_admin"]
        log_in(c)
        assert c.cookies["bif_admin"] != old
    with client(svc, cookies={"bif_admin": old}) as c2:
        assert c2.get("/", follow_redirects=False).status_code == 303


def test_sessions_expire_and_end_when_the_password_changes(svc, clock):
    with client(svc) as c:
        log_in(c)
        assert c.get("/").status_code == 200
        svc.settings = dataclasses.replace(svc.settings, admin_password="a new password for the admin")
    with client(svc, cookies=c.cookies) as c2:
        assert c2.get("/", follow_redirects=False).status_code == 303
    with client(svc) as c3:
        log_in(c3, "a new password for the admin")
        clock.advance(days=15)
        assert c3.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("target", ["//evil.example", "/\\\\evil.example", "https://evil.example", "/login", ""])
def test_no_open_redirect_after_login(target):
    assert safe_next(target) == "/"


def test_two_step_verification_end_to_end(svc, clock):
    with client(svc) as c:
        log_in(c)
        assert "Tweestapsverificatie aanzetten" in c.get("/security").text
        key, codes = turn_on(c, svc)
        assert len(codes) == 10 and "Aan sinds" in c.get("/security/2fa", follow_redirects=True).text
        stored = svc.db.get_state("admin_totp")
        assert stored.startswith("v1:") and key not in stored
        assert (svc.settings.data_dir / "admin.key").stat().st_mode & 0o077 == 0
        assert c.get("/").status_code == 200  # this browser stays logged in
        c.post("/logout")
        clock.advance(seconds=30)
        r = log_in(c)
        assert r.url.path == "/login/2fa"
        assert c.get("/", follow_redirects=False).status_code == 303  # password alone is not enough
        assert c.post("/login/2fa", data={"code": "000000"}).status_code == 400
        assert c.post("/login/2fa", data={"code": code(svc, key)}).url.path == "/"
        c.post("/logout")
        log_in(c)
        assert c.post("/login/2fa", data={"code": code(svc, key)}).status_code == 400  # same code twice
        assert c.post("/login/2fa", data={"code": codes[0].upper()}).url.path == "/"
        assert "Ongebruikte herstelcodes: 9" in c.get("/security").text


def test_turning_on_ends_other_sessions(svc):
    with client(svc) as other, client(svc) as me:
        log_in(other)
        log_in(me)
        turn_on(me, svc)
        assert me.get("/").status_code == 200
        assert other.get("/", follow_redirects=False).status_code == 303


def test_the_code_step_is_throttled_and_expires(svc, clock):
    with client(svc) as c:
        log_in(c)
        key, _ = turn_on(c, svc)
        c.post("/logout")
        clock.advance(seconds=30)
        log_in(c)
        for _ in range(5):
            c.post("/login/2fa", data={"code": "000000"})
        assert c.post("/login/2fa", data={"code": code(svc, key)}).status_code == 429
        clock.advance(minutes=6)
        late = c.post("/login/2fa", data={"code": code(svc, key)})
        assert late.url.path == "/login"


def test_turning_off_needs_password_and_code(svc, clock):
    with client(svc) as c:
        log_in(c)
        key, _ = turn_on(c, svc)
        clock.advance(seconds=30)
        c.post("/security/2fa/disable", data={"password": "wrong", "code": code(svc, key)})
        c.post("/security/2fa/disable", data={"password": PASSWORD, "code": "000000"})
        assert svc.db.get_state("admin_totp")
        page = c.post("/security/2fa/disable", data={"password": PASSWORD, "code": code(svc, key)})
        assert "Tweestapsverificatie staat uit" in page.text
        assert not svc.db.get_state("admin_totp")


def test_logout_everywhere_else(svc):
    with client(svc) as other, client(svc) as me:
        log_in(other)
        log_in(me)
        me.post("/security/logout-all")
        assert me.get("/").status_code == 200
        assert other.get("/", follow_redirects=False).status_code == 303


def test_without_a_password_there_is_no_login(tmp_path, media_dir, fake_ntfy, clock):
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "d", ntfy_url="https://ntfy.test",
                        language="nl").validate()
    svc = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock)
    with client(svc) as c:
        assert c.get("/").status_code == 200
        assert c.get("/login", follow_redirects=False).status_code == 303
        assert "BACKINFRAME_ADMIN_PASSWORD" in c.get("/security").text
        assert c.get("/security/2fa", follow_redirects=False).status_code == 303


def test_reset_2fa_command(tmp_path):
    """Lost phone and codes: the command on the server turns it off and ends every session."""
    data = tmp_path / "data"
    env = {**os.environ, "BACKINFRAME_MEDIA_DIR": str(tmp_path), "BACKINFRAME_DATA_DIR": str(data),
           "BACKINFRAME_ADMIN_PASSWORD": PASSWORD}
    from backinframe.adminauth import AdminAuth
    from backinframe.config import from_env

    settings = from_env(env)
    db = Database(settings.db_path)
    from datetime import datetime, timezone
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    auth = AdminAuth(db, PASSWORD, data, lambda: now)
    secret = totp.new_secret()
    assert auth.enable(secret, totp.code_at(secret, int(now.timestamp() // 30)))
    auth.create_session("ok")
    db.close()
    result = subprocess.run([sys.executable, "-m", "backinframe", "reset-2fa"], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    db = Database(settings.db_path)
    assert db.get_state("admin_totp") is None
    assert db.query("SELECT COUNT(*) AS n FROM admin_sessions")[0]["n"] == 0
    db.close()


def test_mfa_session_cannot_reach_the_admin_page(svc, clock):
    with client(svc) as c:
        log_in(c)
        turn_on(c, svc)
        c.post("/logout")
        clock.advance(seconds=30)
        log_in(c)  # password right, code not yet
        for path in ("/", "/security", "/security/2fa"):
            assert c.get(path, follow_redirects=False).status_code == 303
        assert c.post("/rescan").status_code == 401
