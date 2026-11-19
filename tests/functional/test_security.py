"""Security checks against the running apps, after OWASP ASVS 5.0 and the OWASP Top 10 (2025).

Each test names the requirement it covers. They run with the normal suite (``pytest``) and on
their own with ``pytest -m security``.
"""

from __future__ import annotations

import random
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers import make_image

from backinframe import media, security
from backinframe.app import create_recipient_app
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame
from backinframe.web import create_app

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"


@pytest.fixture
def svc(tmp_path, media_dir, fake_ntfy, clock):
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                        language="en", app_url="https://memories.test", admin_password=PASSWORD).validate()
    service = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                             rng=random.Random(3))
    yield service
    service.db.close()


def log_in(client: TestClient, password: str = PASSWORD) -> TestClient:
    assert client.post("/login", data={"password": password}).status_code == 200
    return client


@pytest.fixture
def admin(svc):
    with TestClient(create_app(svc)) as client:
        yield log_in(client)


@pytest.fixture
def anonymous(svc):
    with TestClient(create_app(svc)) as client:
        yield client


@pytest.fixture
def phone(svc):
    with TestClient(create_recipient_app(svc), base_url="https://memories.test") as client:
        yield client


@pytest.fixture
def two_people(svc, media_dir):
    make_image(media_dir / "Chili" / "a.jpg", taken=datetime(2024, 11, 7, 12), gps=(-41.47, -72.94))
    make_image(media_dir / "Chili" / "b.jpg", taken=datetime(2023, 11, 7, 12))
    svc.rescan()
    oma, opa = svc.create_recipient("Oma"), svc.create_recipient("Opa")
    assert svc.send_now(oma.id).status == "ok"
    assert svc.send_now(opa.id).status == "ok"
    return oma, opa


# ------------------------------------------------------------------ V3 web frontend: headers


def test_admin_pages_send_strict_security_headers(admin, two_people):
    """ASVS 3.4.1-3.4.7: CSP without inline script, nosniff, no framing, referrer policy."""
    oma, _ = two_people
    for path in ("/", f"/recipients/{oma.id}", "/does-not-exist"):
        h = admin.get(path).headers
        csp = h["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp and "form-action 'self'" in csp
        assert "default-src 'none'" in csp
        script_src = re.search(r"script-src ([^;]*)", csp).group(1)
        assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "same-origin"
        assert h["cache-control"] == "no-store"
        assert "geolocation=()" in h["permissions-policy"]
        assert h["cross-origin-opener-policy"] == "same-origin"


def test_admin_pages_have_no_inline_script(admin, two_people):
    """ASVS 3.4.3: the strict CSP must not break the page; no inline handlers or script blocks."""
    oma, _ = two_people
    for path in ("/", f"/recipients/{oma.id}"):
        page = admin.get(path).text
        assert not re.search(r"\son[a-z]+\s*=", page), "inline event handler"
        for tag in re.findall(r"<script\b[^>]*>", page):
            assert "src=" in tag or 'type="application/json"' in tag, tag
    assert admin.get("/admin.js").headers["content-type"].startswith("text/javascript")


def test_recipient_app_sends_strict_security_headers_on_every_response(phone, two_people):
    """ASVS 3.4: also on error pages; the secret link never leaks through the Referer."""
    oma, _ = two_people
    token = oma.app_token
    for path in (f"/a/{token}/", f"/a/{token}/app.js", f"/a/{token}/icon.png", "/a/wrong/", "/nothing", "/healthz"):
        h = phone.get(path).headers
        assert h["referrer-policy"] == "no-referrer", path
        assert h["x-content-type-options"] == "nosniff", path
        assert "frame-ancestors 'none'" in h["content-security-policy"], path
        assert h["strict-transport-security"].startswith("max-age="), path
        assert "noindex" in h["x-robots-tag"], path


def test_hsts_only_over_https(svc):
    """ASVS 3.4.1: HSTS over https (directly or via the proxy's X-Forwarded-Proto), not over plain http."""
    with TestClient(create_app(svc)) as plain:
        log_in(plain)
        assert "strict-transport-security" not in plain.get("/").headers
        proxied = plain.get("/", headers={"X-Forwarded-Proto": "https"})
        assert proxied.headers["strict-transport-security"] == security.HSTS


def test_no_api_docs_or_schema_exposed(admin, phone):
    """ASVS 13.4 / Top 10 A02 security misconfiguration: no generated docs or OpenAPI schema."""
    for client in (admin, phone):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path


# ------------------------------------------------------------------ V6 authentication


def test_admin_needs_the_password_except_healthz(anonymous):
    """ASVS 6.1: every admin page behind the password; only the health check is open."""
    for path in ("/", "/recipients/1", "/security", "/security/2fa"):
        resp = anonymous.get(path, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"].startswith("/login?next="), path
    assert anonymous.post("/rescan").status_code == 401
    assert anonymous.post("/security/2fa/disable", data={"password": PASSWORD, "code": "1"}).status_code == 401
    assert anonymous.get("/healthz").status_code == 200
    assert anonymous.post("/login", data={"password": "wrong"}).status_code == 400
    basic = {"Authorization": "Basic YWRtaW46eA=="}  # the old browser password box is gone
    assert anonymous.get("/", headers=basic, follow_redirects=False).status_code == 303
    anonymous.cookies.set("bif_admin", "made-up")
    assert anonymous.get("/", follow_redirects=False).status_code == 303
    log_in(anonymous)
    assert anonymous.get("/").status_code == 200


def test_password_guessing_is_throttled(anonymous):
    """ASVS 6.3.1 / Top 10 A07: after 10 wrong passwords the address waits, even with the right one."""
    for _ in range(10):
        assert anonymous.post("/login", data={"password": "guess"}).status_code == 400
    blocked = anonymous.post("/login", data={"password": PASSWORD})
    assert blocked.status_code == 429
    assert "bif_admin" not in anonymous.cookies
    assert anonymous.get("/healthz").status_code == 200


def test_throttle_forgets_after_the_window_and_on_success():
    now = [0.0]
    t = security.LoginThrottle(limit=3, window=60, clock=lambda: now[0])
    for _ in range(3):
        t.failed("1.2.3.4")
    assert t.retry_after("1.2.3.4") > 0
    assert t.retry_after("5.6.7.8") == 0
    now[0] = 61
    assert t.retry_after("1.2.3.4") == 0
    t.failed("1.2.3.4")
    t.succeeded("1.2.3.4")
    assert t.retry_after("1.2.3.4") == 0


# ------------------------------------------------------------------ V3.5 / A01: cross-site requests


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Referer": "https://evil.example/page"},
])
def test_admin_refuses_cross_site_posts(admin, two_people, headers):
    """ASVS 3.5.1-3.5.3: state-changing requests only from our own pages (CSRF)."""
    oma, _ = two_people
    resp = admin.post(f"/recipients/{oma.id}/delete", headers=headers)
    assert resp.status_code == 403
    assert admin.get(f"/recipients/{oma.id}").status_code == 200  # still there


def test_admin_accepts_same_origin_posts(admin, two_people):
    oma, _ = two_people
    resp = admin.post(f"/recipients/{oma.id}/command/pause",
                      headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code in (200, 303)


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_recipient_app_refuses_cross_site_posts(phone, two_people, headers):
    oma, _ = two_people
    resp = phone.post(f"/a/{oma.app_token}/command/pause", json={}, headers=headers)
    assert resp.status_code == 403


# ------------------------------------------------------------------ V8 / A01: access control


def test_one_link_never_reaches_someone_elses_memories(phone, svc, two_people):
    """ASVS 8.2.2 (IDOR): a send id of Opa through Oma's link is not found."""
    oma, opa = two_people
    opa_send = svc.db.last_send_id(opa.id)
    oma_send = svc.db.last_send_id(oma.id)
    assert phone.get(f"/a/{oma.app_token}/media/{oma_send}").status_code == 200
    assert phone.get(f"/a/{oma.app_token}/media/{opa_send}").status_code == 404
    assert phone.get(f"/a/{opa.app_token}/media/{oma_send}").status_code == 404
    assert phone.get(f"/a/{oma.app_token[:-1]}x/media/{oma_send}").status_code == 404


def test_secret_links_have_enough_entropy(svc):
    """ASVS 7.2.3: unguessable tokens of at least 128 bits from a CSPRNG (we use 256)."""
    tokens = {svc.create_recipient(f"P{i}", welcome=False).app_token for i in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", token)


@pytest.mark.parametrize("path", [
    "/a/{t}/..%2f..%2f..%2fetc%2fpasswd",
    "/a/{t}/%2e%2e/%2e%2e/etc/passwd.png",
    "/a/{t}/..%2fapp.py.png",
    "/a/{t}/media/..%2f1",
    "/a/{t}/media/1%20OR%201=1",
    "/a/{t}/media/-1",
])
def test_path_tricks_find_nothing(phone, two_people, path):
    """ASVS 5.3.2 / A01: no path traversal through the icon or media routes."""
    oma, _ = two_people
    resp = phone.get(path.format(t=oma.app_token))
    assert resp.status_code in (404, 422)
    assert b"root:" not in resp.content and b"import" not in resp.content


# ------------------------------------------------------------------ V1 / A05: injection and output


def test_messages_in_the_url_are_escaped(admin):
    """ASVS 1.2.1 / A05: reflected text (msg, err) is HTML-escaped."""
    page = admin.get("/", params={"msg": "<script>alert(1)</script>", "err": '"><img src=x onerror=alert(1)>'}).text
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_names_are_escaped_everywhere(admin, phone, svc):
    evil = '<img src=x onerror=alert(1)>'
    r = svc.create_recipient(evil, welcome=False)
    assert evil not in admin.get("/").text
    assert evil not in admin.get(f"/recipients/{r.id}").text
    assert evil not in phone.get(f"/a/{r.app_token}/").text


def test_sql_injection_in_ids_is_rejected(admin):
    """ASVS 1.2.4: ids are typed; SQL in them never reaches a query."""
    assert admin.get("/recipients/1 OR 1=1").status_code in (404, 422)
    assert admin.get("/recipients/1;DROP TABLE recipients").status_code in (404, 422)
    assert admin.get("/").status_code == 200


# ------------------------------------------------------------------ V4 API / V16 errors


def test_json_bodies_are_bounded_and_checked(phone, two_people):
    """ASVS 4.2 / 5.2: size limit and type checks on the recipient app's JSON."""
    oma, _ = two_people
    base = f"/a/{oma.app_token}"
    assert phone.post(f"{base}/push", content=b"x" * 20_000,
                      headers={"Content-Type": "application/json"}).status_code == 413
    json_type = {"Content-Type": "application/json"}
    assert phone.post(f"{base}/push", content=b"{not json", headers=json_type).status_code == 400
    assert phone.post(f"{base}/push", json=[1, 2]).status_code == 400
    assert phone.post(f"{base}/command/rm -rf", json={}).status_code in (400, 404)


def test_errors_reveal_no_internals(admin, phone, two_people):
    """ASVS 16.5.1 / A10: no stack traces, paths or versions of libraries in error responses."""
    oma, _ = two_people
    for resp in (admin.get("/recipients/999999"), admin.post("/recipients/abc"),
                 phone.post(f"/a/{oma.app_token}/push", content=b"{", headers={"Content-Type": "application/json"}),
                 phone.get("/a/x/media/1")):
        body = resp.text
        for leak in ("Traceback", 'File "', "/home/", "site-packages"):
            assert leak not in body
        assert "server" not in {k.lower() for k in resp.headers} or "uvicorn" not in resp.headers.get("server", "")


# ------------------------------------------------------------------ V5 / privacy: what we send out


def test_photos_leave_without_location_or_camera_details(phone, svc, two_people):
    """ASVS 14.2 (data protection): only the capture date survives; GPS and camera data are stripped."""
    import io

    from PIL import Image

    oma, _ = two_people
    data = phone.get(f"/a/{oma.app_token}/media/{svc.db.last_send_id(oma.id)}").content
    exif = Image.open(io.BytesIO(data)).getexif()
    assert not exif.get_ifd(0x8825)
    assert set(exif) <= {0x0132, 0x8769}


def test_decompression_bombs_are_skipped(tmp_path, monkeypatch):
    """ASVS 5.2.x / A06: an image that unpacks to too many pixels is not decoded."""
    monkeypatch.setattr(media, "MAX_PIXELS", 1000)
    bomb = make_image(tmp_path / "bomb.jpg", size=(400, 400))
    with pytest.raises(media.SkipMedia):
        media.prepare_image(bomb, 2048, 15_000_000)


# ------------------------------------------------------------------ the CSP in a real browser


def test_strict_csp_breaks_nothing_in_a_real_browser(svc, two_people):
    """ASVS 3.4.3: with the CSP enforced, the pages load without violations, our own form posts
    pass the cross-site check, and the delete confirmation (now in admin.js) still asks first."""
    from a11y import CHROME, _in_thread, _serve_from, require_browser

    require_browser()
    from playwright.sync_api import sync_playwright

    oma, opa = two_people
    base, app_base = "http://admin.test", "https://memories.test"

    def run():
        out = {"csp": [], "statuses": []}
        with TestClient(create_app(svc), base_url=base) as admin_client, \
                TestClient(create_recipient_app(svc), base_url=app_base) as app_client, sync_playwright() as pw:
            log_in(admin_client)
            browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            page = browser.new_context().new_page()
            page.on("console", lambda m: "Content Security Policy" in m.text and out["csp"].append(m.text))
            page.on("response", lambda r: out["statuses"].append((r.request.method, r.status)))
            page.route(base + "/**", _serve_from(admin_client, base))
            page.route(app_base + "/**", _serve_from(app_client, app_base))
            page.goto(f"{base}/recipients/{oma.id}")
            page.locator(f"form[action='/recipients/{oma.id}/command/pause'] button").click()
            page.wait_for_load_state()
            out["after_pause"] = page.content()
            page.goto(f"{base}/recipients/{opa.id}")
            page.once("dialog", lambda d: d.dismiss())
            page.locator("button.danger").click()
            page.wait_for_timeout(200)
            out["kept"] = svc.db.recipient(opa.id) is not None
            page.once("dialog", lambda d: d.accept())
            page.locator("button.danger").click()
            page.wait_for_load_state()
            page.wait_for_timeout(200)
            out["deleted"] = svc.db.recipient(opa.id) is None
            page.goto(f"{app_base}/a/{oma.app_token}/")
            page.wait_for_timeout(300)
            browser.close()
        return out

    out = _in_thread(run)
    assert out["csp"] == []
    assert ("POST", 403) not in out["statuses"]
    assert "Cross-site" not in out["after_pause"]
    assert out["kept"] and out["deleted"]
