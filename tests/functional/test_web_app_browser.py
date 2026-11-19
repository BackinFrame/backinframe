"""The web app's script in a real (headless) Chrome. Skipped when Playwright or Chrome is missing."""

from __future__ import annotations

import random
import shutil
import socket
import threading
import time
from datetime import datetime

import pytest
import uvicorn
from helpers import make_image

from backinframe.app import create_recipient_app
from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame

playwright = pytest.importorskip("playwright.sync_api")
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome")

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_app(tmp_path, media_dir, fake_ntfy, clock):
    port = free_port()
    settings = Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                        language="en", app_url=f"http://localhost:{port}", app_port=port).validate()
    svc = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                         rng=random.Random(3))
    make_image(media_dir / "Chili 2025" / "a.jpg", taken=datetime(2025, 11, 7))
    svc.rescan()
    r = svc.create_recipient("Oma")
    svc.send_now(r.id)
    server = uvicorn.Server(uvicorn.Config(create_recipient_app(svc), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://localhost:{port}/a/{r.app_token}/"
    server.should_exit = True
    thread.join(timeout=5)


def wait_text(page, selector: str, check, timeout: float = 10) -> str:
    """Poll from Python: the app's CSP (rightly) forbids the eval Playwright's wait_for_function uses."""
    end = time.monotonic() + timeout
    while True:
        text = page.text_content(selector) or ""
        if check(text) or time.monotonic() > end:
            return text
        time.sleep(0.05)


def in_thread(fn):
    """Playwright's sync API refuses to run where an event loop is active; give it its own thread."""
    outcome: dict = {}

    def run():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            outcome["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=60)
    if "error" in outcome:
        raise outcome["error"]


def open_page(url: str, user_agent: str | None = None):
    pw = playwright.sync_playwright().start()
    browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 390, "height": 844}, user_agent=user_agent)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(url)
    wait_text(page, "#push-status", lambda t: t != "Checking…")
    return pw, browser, page, errors


def test_script_runs_and_buttons_work(live_app):
    in_thread(lambda: check_script(live_app))


def check_script(live_app):
    pw, browser, page, errors = open_page(live_app)
    try:
        assert "Turn them on" in page.text_content("#push-status")
        assert page.is_visible("#push-on")
        assert page.locator("figure img").count() == 1
        assert page.locator("figure img").evaluate("img => img.complete && img.naturalWidth") > 0
        page.click("[data-command=more]")
        assert "2 memories" in wait_text(page, "#rhythm", lambda t: "2 memories" in t)
        page.click("#pause")
        page.wait_for_selector("#resume", state="visible")
        assert errors == []
    finally:
        browser.close()
        pw.stop()


def test_iphone_is_asked_to_add_the_app_to_the_home_screen_first(live_app):
    in_thread(lambda: check_iphone(live_app))


def check_iphone(live_app):
    pw, browser, page, errors = open_page(live_app, IPHONE)
    try:
        assert page.is_visible("#install-hint")
        assert "home screen" in page.text_content("#push-status")
        assert not page.is_visible("#push-on")
        assert errors == []
    finally:
        browser.close()
        pw.stop()


IOS_26 = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1")
IOS_15 = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/15.6 Mobile/15E148 Safari/604.1")
CHROME_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) CriOS/126.0.6478.54 Mobile/15E148 Safari/604.1")
INSTAGRAM = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Mobile/15E148 Instagram 337.0.3.23.54")
IPAD = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")


@pytest.mark.parametrize("agent,flow,steps", [
    (IOS_26, "ios26", 5), (IPHONE, "ios", 4), (CHROME_IOS, "browser", 4), (INSTAGRAM, "inapp", 0),
    (IOS_15, "old", 0),
])
def test_iphone_guide_picks_the_steps_for_the_browser(live_app, agent, flow, steps):
    in_thread(lambda: check_guide(live_app, agent, flow, steps))


def check_guide(live_app, agent, flow, steps):
    pw, browser, page, errors = open_page(live_app, agent)
    try:
        assert page.is_visible(f'[data-flow="{flow}"]')
        others = [f for f in ("ios26", "ios", "browser", "inapp", "old") if f != flow]
        assert not any(page.is_visible(f'[data-flow="{f}"]') for f in others)
        if steps:
            items = page.locator(f'[data-flow="{flow}"] li')
            assert items.count() == steps
            assert page.text_content("#guide-count") == f"Step 1 of {steps}"
            assert items.nth(0).get_attribute("aria-current") == "step"
            assert page.locator(".frame:not([hidden])").count() == 1
            page.click("#guide-next")  # stops the animation and moves on
            assert page.text_content("#guide-count") == f"Step 2 of {steps}"
            assert items.nth(1).get_attribute("aria-current") == "step"
            assert "Play" in page.text_content("#guide-play")
            page.click("#guide-prev")
            page.click("#guide-prev")
            assert page.text_content("#guide-count") == f"Step {steps} of {steps}"
        if flow == "ios26":
            assert "Open as Web App" in page.text_content('[data-flow="ios26"]')
        if flow == "browser":
            assert page.is_visible("text=This works best in Safari.")
        if flow == "inapp":
            assert page.is_visible('.frame[data-frame="safari"]')
            assert not page.is_visible("#guide-missing")
        if flow == "old":
            assert not page.is_visible(".guide-picture")
            assert "16.4" in page.text_content('[data-flow="old"]')
        assert page.is_visible("#copy-link")
        assert errors == []
    finally:
        browser.close()
        pw.stop()


def test_ipad_counts_as_ios(live_app):
    def check():
        pw = playwright.sync_playwright().start()
        browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 820, "height": 1180}, user_agent=IPAD, has_touch=True)
        page.add_init_script("Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});"
                             "Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5});")
        try:
            page.goto(live_app)
            page.wait_for_selector('[data-flow="ios"]', state="visible")
        finally:
            browser.close()
            pw.stop()
    in_thread(check)


def test_the_animation_waits_for_people_who_want_less_motion(live_app):
    def check():
        pw = playwright.sync_playwright().start()
        browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 390, "height": 844}, user_agent=IOS_26, reduced_motion="reduce")
        try:
            page.goto(live_app)
            page.wait_for_selector('[data-flow="ios26"]', state="visible")
            time.sleep(3.2)  # longer than one step
            assert page.text_content("#guide-count") == "Step 1 of 5"
            assert "Play" in page.text_content("#guide-play")
        finally:
            browser.close()
            pw.stop()
    in_thread(check)


def test_copy_link_button(live_app):
    def check():
        pw = playwright.sync_playwright().start()
        browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=INSTAGRAM,
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        try:
            page.goto(live_app)
            page.click("#copy-link")
            assert "Link copied" in wait_text(page, "#copy-done", lambda t: bool(t))
            assert page.evaluate("navigator.clipboard.readText()") == live_app
        finally:
            browser.close()
            pw.stop()
    in_thread(check)
