"""The recipient web app: a gallery of received memories, notifications and the rhythm buttons.

The routes live in :func:`router`, which works against a small backend interface, so the hosted
version can serve the same app from its own database. :func:`create_recipient_app` runs it for
the self-hosted version, on its own port (BACKINFRAME_APP_PORT): you can expose it to the
internet while the admin page stays on your own network. Access is by the secret link /a/<token>.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from . import __version__, caption, i18n, security, uploads
from .db import Media, Recipient
from .service import GALLERY_SIZE, BackInFrame

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
COMMANDS = ("more", "less", "pause", "resume")
REACTIONS = ("", "heart")  # prepared: '' takes a reaction back. No buttons in the app yet.
MAX_BODY = 8192
UPLOAD_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self'; "
              "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'; "
       "connect-src 'self'; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
# The link is the key: never leak it in a Referer, never let search engines index it.
HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": CSP, "Cache-Control": "no-store"}
ICONS = {"icon.png": "icon-192.png", "icon-512.png": "icon-512.png", "icon-maskable.png": "icon-maskable-512.png",
         "apple-touch-icon.png": "apple-touch-icon.png"}


@dataclass
class AppView:
    name: str
    lang: str
    rhythm: str
    paused: bool


class Backend(Protocol):
    """What the web app needs. Every method gets the secret token; unknown tokens give None/KeyError."""

    vapid_public_key: str

    def view(self, token: str) -> AppView | None: ...
    def gallery(self, token: str) -> list[dict]: ...  # {send_id, kind, title, text}, newest first
    def media(self, token: str, send_id: int) -> tuple[Path | bytes, str] | None: ...
    def subscribe(self, token: str, data: dict) -> None: ...  # ValueError for a bad subscription
    def unsubscribe(self, token: str, endpoint: str) -> None: ...
    def command(self, token: str, command: str) -> tuple[AppView, str]: ...  # new view, reply text
    # Optional: a backend without these has hearts and the year review off.
    # reactions_enabled: bool
    # def react(self, token: str, send_id: int, reaction: str) -> bool: ...  # False: not their memory
    # def year(self, token: str, year: int) -> dict | None: ...  # {title, text, items}
    # def year_collage(self, token: str, year: int) -> bytes | None: ...


def rhythm(lang: str, freq: str, start: str, end: str, paused_until: date | None) -> tuple[str, bool]:
    if paused_until is not None:
        return i18n.t(lang, "app_paused_text", date=caption.format_date(paused_until, lang)), True
    return i18n.t(lang, "app_rhythm_text", freq=freq, start=start, end=end), False


def item(send_id: int, kind: str, media: Media, today: date, lang: str) -> dict:
    title, text = caption.build(media, today, lang)
    return {"send_id": send_id, "kind": kind, "title": title, "text": text}


def manifest(base: str) -> dict:
    return {
        "name": "Back in Frame",
        "short_name": "Back in Frame",
        "id": base + "/",
        "start_url": base + "/",
        "scope": base + "/",
        "display": "standalone",
        "background_color": "#faf7f2",
        "theme_color": "#b04520",
        "icons": [
            {"src": base + "/icon.png", "sizes": "192x192", "type": "image/png"},
            {"src": base + "/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": base + "/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


def texts(lang: str) -> dict[str, str]:
    return i18n.js_texts(lang)


def secure(response: Response, **extra: str) -> Response:
    for key, value in {**HEADERS, **extra}.items():
        response.headers[key] = value
    return response


def router(backend: Backend) -> APIRouter:
    r = APIRouter()
    templates = Jinja2Templates(directory=str(TEMPLATES))

    def view_or_404(token: str) -> AppView:
        v = backend.view(token)
        if v is None:
            raise HTTPException(404, "Not found")
        return v

    async def json_body(request: Request) -> dict:
        if security.cross_site(request):
            raise HTTPException(403, "Cross-site request refused")
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            raise HTTPException(413, "Too large")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "Too large")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(400, "Not JSON") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "Not an object")
        return data

    @r.get("/a/{token}", include_in_schema=False)
    @r.get("/a/{token}/", response_class=HTMLResponse)
    def page(request: Request, token: str):
        v = view_or_404(token)
        base = f"/a/{token}"
        resp = templates.TemplateResponse(request, "app.html", {
            "_": lambda key, **kw: i18n.t(v.lang, key, **kw), "lang": v.lang, "name": v.name, "base": base,
            "vapid_key": backend.vapid_public_key, "rhythm": v.rhythm, "paused": v.paused,
            "items": backend.gallery(token), "texts": texts(v.lang),
            "hearts": getattr(backend, "reactions_enabled", False),
        })
        return secure(resp)

    @r.get("/a/{token}/year/{year}", response_class=HTMLResponse)
    def year_page(request: Request, token: str, year: int):
        v = view_or_404(token)
        review = backend.year(token, year) if hasattr(backend, "year") else None
        if review is None:
            raise HTTPException(404, "Not found")
        base = f"/a/{token}"
        resp = templates.TemplateResponse(request, "app_year.html", {
            "_": lambda key, **kw: i18n.t(v.lang, key, **kw), "lang": v.lang, "base": base, "year": year,
            "review": review,
        })
        return secure(resp)

    @r.get("/a/{token}/year/{year}/collage.jpg")
    def year_collage(token: str, year: int):
        view_or_404(token)
        data = backend.year_collage(token, year) if hasattr(backend, "year_collage") else None
        if data is None:
            raise HTTPException(404, "Not found")
        return secure(Response(data, media_type="image/jpeg"), **{"Cache-Control": "private, max-age=86400"})

    @r.get("/a/{token}/manifest.webmanifest")
    def app_manifest(token: str):
        view_or_404(token)
        return secure(Response(json.dumps(manifest(f"/a/{token}")), media_type="application/manifest+json"))

    @r.get("/a/{token}/sw.js")
    def service_worker(token: str):
        view_or_404(token)
        return secure(FileResponse(STATIC / "sw.js", media_type="text/javascript"),
                      **{"Service-Worker-Allowed": f"/a/{token}/", "Cache-Control": "no-cache"})

    @r.get("/a/{token}/app.js")
    def script(token: str):
        view_or_404(token)
        return secure(FileResponse(STATIC / "app.js", media_type="text/javascript"), **{"Cache-Control": "no-cache"})

    @r.get("/a/{token}/{name}.png")
    def icon(token: str, name: str):
        view_or_404(token)
        file = ICONS.get(name + ".png")
        if file is None:
            raise HTTPException(404, "Not found")
        return secure(FileResponse(STATIC / file, media_type="image/png"),
                      **{"Cache-Control": "public, max-age=604800"})

    @r.get("/a/{token}/media/{send_id}")
    def media(token: str, send_id: int):
        view_or_404(token)
        found = backend.media(token, send_id)
        if found is None:
            raise HTTPException(404, "Not found")
        content, content_type = found
        resp = (FileResponse(content, media_type=content_type) if isinstance(content, Path)
                else Response(content, media_type=content_type))
        return secure(resp, **{"Cache-Control": "private, max-age=86400"})

    @r.post("/a/{token}/push")
    async def subscribe(request: Request, token: str):
        data = await json_body(request)
        view_or_404(token)
        try:
            backend.subscribe(token, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return secure(JSONResponse({"ok": True}))

    @r.post("/a/{token}/push/remove")
    async def unsubscribe(request: Request, token: str):
        data = await json_body(request)
        view_or_404(token)
        backend.unsubscribe(token, str(data.get("endpoint", "")))
        return secure(JSONResponse({"ok": True}))

    @r.post("/a/{token}/command/{command}")
    async def command(request: Request, token: str, command: str):
        await json_body(request)
        view_or_404(token)
        if command not in COMMANDS:
            raise HTTPException(400, "Unknown command")
        v, reply = backend.command(token, command)
        return secure(JSONResponse({"rhythm": v.rhythm, "paused": v.paused, "reply": reply}))

    # -- upload links: someone adds photos to one album ------------------------------------------
    upload_throttle = security.LoginThrottle(limit=60, window=3600)  # 60 posts an hour per link

    def upload_lang(request: Request, info: dict) -> str:
        header = request.headers.get("accept-language", "").split(",")[0]
        return i18n.normalize(header) if header else info["lang"]

    def upload_page(request: Request, token: str, info: dict, **extra) -> Response:
        lang = upload_lang(request, info)
        resp = templates.TemplateResponse(request, "upload.html", {
            "_": lambda key, **kw: i18n.t(lang, key, **kw), "lang": lang, "base": f"/u/{token}",
            "album": info["album"], "name": info["name"], "max_mb": int(info["max_mb"]),
            "max_files": info["max_files"], "added": 0, "failed": "", "error": "", **extra,
        })
        return secure(resp, **{"Content-Security-Policy": UPLOAD_CSP})

    def upload_info(token: str) -> dict:
        info = backend.upload_info(token) if hasattr(backend, "upload_info") else None
        if info is None:
            raise HTTPException(404, "Not found")
        return info

    @r.get("/u/{token}", response_class=HTMLResponse)
    def upload_form(request: Request, token: str):
        return upload_page(request, token, upload_info(token))

    @r.get("/u/{token}/upload.js")
    def upload_script(token: str):
        upload_info(token)
        return secure(FileResponse(STATIC / "upload.js", media_type="text/javascript"))

    @r.post("/u/{token}", response_class=HTMLResponse)
    async def upload(request: Request, token: str):
        info = upload_info(token)
        lang = upload_lang(request, info)
        if security.cross_site(request):
            raise HTTPException(403, "Cross-site request refused")
        if upload_throttle.retry_after(token):
            return upload_page(request, token, info, error=i18n.t(lang, "upload_too_often"))
        upload_throttle.failed(token)  # counts every post
        limit = info["max_mb"] * 1024 * 1024 * info["max_files"] + 1024 * 1024
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, "Too large")
        form = await request.form(max_files=info["max_files"] + 1, max_fields=10)
        files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
        try:
            added, failed = backend.upload(token, [(f.filename, f.file) for f in files[:info["max_files"]]])
        finally:
            await form.close()
        reasons = "; ".join(f"{name} ({i18n.t(lang, 'upload_err_' + why)})" for name, why in failed[:10])
        return upload_page(request, token, info, added=added, failed=reasons)

    @r.post("/a/{token}/react/{send_id}")
    async def react(request: Request, token: str, send_id: int):
        """A heart on one of the recipient's own memories ("" takes it back). 404 with hearts off."""
        if not getattr(backend, "reactions_enabled", False):
            raise HTTPException(404, "Not found")
        data = await json_body(request)
        view_or_404(token)
        reaction = data.get("reaction", "")
        if reaction not in REACTIONS:
            raise HTTPException(400, "Unknown reaction")
        if not backend.react(token, send_id, reaction):
            raise HTTPException(404, "Not found")
        return secure(JSONResponse({"ok": True, "reaction": reaction}))

    return r


class CoreBackend:
    """The self-hosted version: recipients and photos from the local database and folder."""

    def __init__(self, svc: BackInFrame):
        self.svc = svc

    @property
    def vapid_public_key(self) -> str:
        return self.svc.vapid.public_key

    def _recipient(self, token: str) -> Recipient | None:
        r = self.svc.db.recipient_by_app_token(token)
        return r if r is not None and r.delivery == "app" else None

    def _view(self, r: Recipient) -> AppView:
        paused = r.paused_until.date() if r.paused_until and r.paused_until > self.svc.now() else None
        lang = self.svc.lang_of(r)
        text, is_paused = rhythm(lang, self.svc.frequency_text(r), r.window_start, r.window_end, paused)
        return AppView(r.name, lang, text, is_paused)

    def view(self, token: str) -> AppView | None:
        r = self._recipient(token)
        return None if r is None else self._view(r)

    def gallery(self, token: str) -> list[dict]:
        r = self._recipient(token)
        today = self.svc.now().date()
        out = []
        for row in self.svc.db.gallery(r.id, GALLERY_SIZE):
            taken = datetime.fromisoformat(row["taken_at"]) if row["taken_at"] else None
            media = Media(id=row["media_id"], path=row["path"], kind=row["kind"], size=0, mtime=row["mtime"],
                          taken_at=taken, title=row["title"], note=row["note"], sender=row["sender"])
            entry = item(row["send_id"], row["kind"], media, today, self.svc.lang_of(r))
            entry["reaction"] = row["reaction"]
            out.append(entry)
        return out

    def media(self, token: str, send_id: int):
        return self.svc.gallery_media(self._recipient(token), send_id)

    def subscribe(self, token: str, data: dict) -> None:
        self.svc.subscribe_push(self._recipient(token), data)

    def unsubscribe(self, token: str, endpoint: str) -> None:
        self.svc.unsubscribe_push(self._recipient(token), endpoint)

    def command(self, token: str, command: str) -> tuple[AppView, str]:
        r = self.svc.handle_command(self._recipient(token).id, command, notify=False)
        return self._view(r), self.svc.last_reply

    @property
    def reactions_enabled(self) -> bool:
        return self.svc.settings.reactions_enabled

    def react(self, token: str, send_id: int, reaction: str) -> bool:
        return self.svc.react(self._recipient(token), send_id, reaction)

    def year(self, token: str, year: int) -> dict | None:
        r = self._recipient(token)
        rv = self.svc.year_review(r, year)
        if rv is None:
            return None
        title, text = self.svc.review_text(r, rv)
        lang = self.svc.lang_of(r)
        today = self.svc.now().date()
        items = []
        for pick in rv.picks:
            m = self.svc.db.media_by_id(pick.media_id)
            if m is None:
                continue
            entry = item(pick.send_id, m.kind, m, today, lang)
            entry["reaction"] = pick.reaction
            items.append(entry)
        return {"title": title, "text": text, "items": items, "total": rv.total, "albums": rv.albums[:5]}

    def year_collage(self, token: str, year: int) -> bytes | None:
        return self.svc.review_collage(self._recipient(token), year)

    def upload_info(self, token: str) -> dict | None:
        link = self.svc.db.upload_link(token) if token and len(token) < 100 else None
        if link is None:
            return None
        return {"album": link["album"], "name": link["name"], "lang": self.svc.settings.language,
                "max_mb": self.svc.settings.upload_max_mb, "max_files": uploads.MAX_FILES_PER_POST}

    def upload(self, token: str, files) -> tuple[int, list[tuple[str, str]]]:
        return self.svc.upload_files(token, files)


def create_recipient_app(svc: BackInFrame) -> FastAPI:
    app = FastAPI(title="Back in Frame app", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router(CoreBackend(svc)))
    app.add_middleware(security.HeadersMiddleware, extra=HEADERS)

    @app.get("/healthz")
    def health():
        return JSONResponse({"status": "ok", "version": __version__})

    return app
