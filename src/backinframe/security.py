"""Shared HTTP hardening: response headers, the cross-site check and a login throttle.

Follows OWASP ASVS 5.0 (V3 web frontend, V4 API, V6 authentication) for what a small
self-hosted app can do without a database of its own: strict headers on every response,
refuse state-changing requests from other sites, and slow down password guessing.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

from starlette.requests import Request

# The admin pages: own scripts and styles only; QR codes are inline SVG.
ADMIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
}
HSTS = "max-age=31536000"


def is_https(request: Request) -> bool:
    """True when the browser talks https to us (directly, or through a reverse proxy that says so)."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def cross_site(request: Request) -> bool:
    """A state-changing request that did not come from our own pages.

    Browsers send Sec-Fetch-Site and Origin on every POST; either one pointing elsewhere is enough
    to refuse. A request without both (curl, scripts) is allowed: it carries no browser cookies or
    stored passwords that another site could abuse.
    """
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == "null" or urlparse(origin).netloc != request.headers.get("host")
    referer = request.headers.get("referer")
    return bool(referer) and urlparse(referer).netloc != request.headers.get("host")


class LoginThrottle:
    """Slows down password guessing: after ``limit`` wrong passwords from one address within
    ``window`` seconds, that address waits until the window has passed (ASVS 6.3.1)."""

    def __init__(self, limit: int = 10, window: float = 900, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = self.clock()
        fails = [t for t in self._fails.get(key, []) if now - t < self.window]
        if fails:
            self._fails[key] = fails
        else:
            self._fails.pop(key, None)
        return fails

    def retry_after(self, key: str) -> int:
        """Seconds to wait, or 0 when this address may try."""
        with self._lock:
            fails = self._recent(key)
            if len(fails) < self.limit:
                return 0
            return max(1, int(self.window - (self.clock() - fails[0])) + 1)

    def failed(self, key: str) -> None:
        with self._lock:
            self._recent(key)
            self._fails.setdefault(key, []).append(self.clock())
            if len(self._fails) > 10_000:  # bound the memory a flood of addresses can take
                oldest = sorted(self._fails, key=lambda k: self._fails[k][-1])[:5_000]
                for k in oldest:
                    del self._fails[k]

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


class HeadersMiddleware:
    """Pure ASGI middleware: put the security headers on every response, errors included.
    Headers a route already set (a looser Cache-Control for icons, say) are kept."""

    def __init__(self, app, extra: dict[str, str] | None = None):
        self.app = app
        self.extra = extra or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        wanted = {**BASE_HEADERS, **self.extra}
        if is_https(request):
            wanted["Strict-Transport-Security"] = HSTS

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.decode().lower() for k, _ in message.get("headers", [])}
                extra = [(k.lower().encode(), v.encode()) for k, v in wanted.items() if k.lower() not in present]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)
