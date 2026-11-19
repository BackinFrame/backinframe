"""Accessibility checks with axe-core in a real (headless) Chrome.

Pages are served by a TestClient: every request the browser makes is answered by the app in this
process, so the checks need no running server and keep the TestClient's login cookie.
"""

from __future__ import annotations

import shutil
import threading
from urllib.parse import urlsplit

import pytest

CHROME = shutil.which("google-chrome") or shutil.which("chromium")

# WCAG 2.2 level A and AA, the level the European Accessibility Act points to (EN 301 549).
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}


def require_browser():
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("axe_playwright_python")
    if CHROME is None:
        pytest.skip("no Chrome")


def _in_thread(fn):
    """Playwright's sync API refuses to run where an event loop is active; give it its own thread."""
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            out["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=300)
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _serve_from(client, base: str):
    host = urlsplit(base).netloc

    def handle(route):
        req = route.request
        parts = urlsplit(req.url)
        if parts.netloc != host:
            route.abort()
            return
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "accept", "origin")}
        resp = client.request(req.method, path, content=req.post_data_buffer, headers=headers,
                              follow_redirects=True)
        keep = {k: v for k, v in resp.headers.items()
                if k.lower() not in ("content-length", "content-encoding", "transfer-encoding", "set-cookie")}
        route.fulfill(status=resp.status_code, headers=keep, body=resp.content)

    return handle


FOCUS_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const st = getComputedStyle(el);
  const outline = st.outlineStyle !== 'none' && parseFloat(st.outlineWidth) >= 2;
  const shadow = st.boxShadow && st.boxShadow !== 'none';
  const r = el.getBoundingClientRect();
  const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 40);
  return {tag: el.tagName.toLowerCase(), text: text,
          visible: outline || shadow, onscreen: r.width > 0 && r.height > 0};
}"""


def _keyboard_and_reflow(page, vp) -> list[dict]:
    """What axe cannot see: a visible focus ring on every Tab stop (WCAG 2.4.7) and no sideways
    scrolling at 320 CSS px wide (1.4.10 Reflow)."""
    problems: list[dict] = []
    seen = set()
    for _ in range(60):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info is None:
            break
        key = (info["tag"], info["text"])
        if key in seen:
            break
        seen.add(key)
        if info["onscreen"] and not info["visible"]:
            problems.append({"id": "focus-visible", "impact": "serious", "help": "Keyboard focus must be visible",
                             "nodes": [{"target": [f"{info['tag']} '{info['text']}'"]}]})
    if vp["width"] <= 400:
        page.set_viewport_size({"width": 320, "height": vp["height"]})
        wide = page.evaluate("() => document.documentElement.scrollWidth")
        page.set_viewport_size(vp)
        if wide > 320:
            problems.append({"id": "reflow", "impact": "serious",
                             "help": f"Content must fit 320 px without sideways scrolling ({wide} px)",
                             "nodes": [{"target": ["html"]}]})
    return problems


def describe(violations: list[dict]) -> str:
    lines = []
    for v in violations:
        targets = ", ".join(" ".join(n["target"]) for n in v["nodes"][:3])
        lines.append(f"{v['id']} ({v['impact']}): {v['help']} -> {targets}")
        summary = (v["nodes"][0].get("failureSummary") or "").replace("\n", " ")
        if summary:
            lines.append(f"    {summary[:300]}")
    return "\n".join(lines)


def audit(client, base: str, paths: list[str], *, viewports=(PHONE, DESKTOP), schemes=("light", "dark"),
          user_agent: str | None = None, setup=None) -> dict[str, list[dict]]:
    """Run axe on every path, on a phone and a desktop, in light and dark mode.

    Returns {"<path> @<width> <scheme>": [violation, ...]} for the pages that fail.
    """
    from axe_playwright_python.sync_playwright import Axe
    from playwright.sync_api import sync_playwright

    def run():
        failures: dict[str, list[dict]] = {}
        axe = Axe()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            try:
                for vp, scheme in [(v, c) for v in viewports for c in schemes]:
                    context = browser.new_context(viewport=vp, user_agent=user_agent, bypass_csp=True,
                                                  color_scheme=scheme)
                    page = context.new_page()
                    page.route("**/*", _serve_from(client, base))
                    for path in paths:
                        page.goto(base + path)
                        if setup:
                            setup(page, path)
                        result = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
                        violations = list(result.response["violations"])
                        if scheme == "light":
                            violations += _keyboard_and_reflow(page, vp)
                        if violations:
                            failures[f"{path} @{vp['width']} {scheme}"] = violations
                    context.close()
            finally:
                browser.close()
        return failures

    return _in_thread(run)


def assert_accessible(failures: dict[str, list[dict]]) -> None:
    assert not failures, "\n\n".join(f"{page}\n{describe(v)}" for page, v in failures.items())
