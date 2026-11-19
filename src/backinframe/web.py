"""Small admin page: add recipients, show their QR code, tweak settings."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import quote, urlencode

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, adminauth, caption, i18n, insights, media, review, security, totp, uploads
from .adminauth import AdminAuth
from .security import LoginThrottle
from .service import BackInFrame, validation_message

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
# Same-origin (not no-referrer): the browser then still sends a real Origin with our own form posts.
ADMIN_HEADERS = {"Content-Security-Policy": security.ADMIN_CSP, "Referrer-Policy": "same-origin",
                 "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")


OPEN_PATHS = {"/healthz", "/admin.js", "/login", "/login/2fa"}


def safe_next(target: str | None) -> str:
    """Only a path on this admin page after login: no other host, no //, no backslash tricks."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    if any(ord(c) < 32 for c in target) or target.startswith(("/login", "/logout")):
        return "/"
    return target


class GuardMiddleware(BaseHTTPMiddleware):
    """The cross-site check on form posts, and (with an admin password) the login."""

    def __init__(self, app, auth: AdminAuth):
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and security.cross_site(request):
            return Response("Cross-site request refused", 403)
        request.state.admin_id = adminauth.MAIN
        if not self.auth.enabled or request.url.path in OPEN_PATHS or request.url.path.startswith("/invite/"):
            return await call_next(request)
        info = self.auth.session_info(request.cookies.get(adminauth.COOKIE))
        if info and info[0] == "ok":
            request.state.admin_id = info[1]
            return await call_next(request)
        if request.method in ("GET", "HEAD"):
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)
        return Response("Login required", 401)


def _int(value: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: not a number") from exc


def create_app(app_service: BackInFrame) -> FastAPI:
    app = FastAPI(title="Back in Frame", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    auth = AdminAuth(app_service.db, app_service.settings.admin_password, app_service.settings.data_dir,
                     app_service.now)
    app.state.auth = auth
    password_throttle = LoginThrottle(limit=10, window=900)
    code_throttle = LoginThrottle(limit=5, window=900)
    app.add_middleware(GuardMiddleware, auth=auth)
    app.add_middleware(security.HeadersMiddleware, extra=ADMIN_HEADERS)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    lang = app_service.lang

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.update(
            _=lambda key, **kw: i18n.t(lang, key, **kw),
            lang=lang,
            version=__version__,
            msg=request.query_params.get("msg", ""),
            err=request.query_params.get("err", ""),
            svc=app_service,
            fmt=lambda dt: dt.astimezone(app_service.settings.tz).strftime("%d-%m-%Y %H:%M") if dt else "–",
            languages=i18n.LANGUAGES,
            deliveries=app_service.deliveries(),
            auth=auth,
            logged_in=auth.session(request.cookies.get(adminauth.COOKIE)) == "ok" or bool(ctx.get("codes")),
            others=auth.has_others(),
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def back(url: str, msg: str = "", err: str = "") -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        if err:
            url += ("&" if "?" in url else "?") + "err=" + quote(err)
        return RedirectResponse(url, status_code=303)

    def recipient_or_404(rid: int):
        r = app_service.db.recipient(rid)
        if r is None:
            raise HTTPException(404, "Not found")
        return r

    # -- login ------------------------------------------------------------------------------
    def with_cookie(response: Response, request: Request, token: str, stage: str) -> Response:
        age = adminauth.MFA_SECONDS if stage == "mfa" else adminauth.SESSION_DAYS * 86400
        response.set_cookie(adminauth.COOKIE, token, max_age=age, httponly=True, samesite="strict",
                            secure=security.is_https(request), path="/")
        return response

    def client(request: Request) -> str:
        return request.client.host if request.client else "?"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", next=safe_next(next))

    @app.post("/login")
    def login(request: Request, password: str = Form(""), name: str = Form(""), next: str = Form("/")):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        who = client(request)
        wait = password_throttle.retry_after(who)
        if wait:
            return render(request, "login.html", status=429, next=safe_next(next),
                          error=i18n.t(lang, "ui_err_too_many_tries"))
        admin_id = auth.login(name, password)
        if admin_id is None:
            password_throttle.failed(who)
            return render(request, "login.html", status=400, next=safe_next(next), login_name=name,
                          error=i18n.t(lang, "ui_err_login"))
        password_throttle.succeeded(who)
        auth.end(request.cookies.get(adminauth.COOKIE))  # never keep a session id from before the login
        if auth.totp_enabled(admin_id):
            token = auth.create_session("mfa", admin_id)
            target = "/login/2fa?next=" + quote(safe_next(next), safe="")
            return with_cookie(RedirectResponse(target, status_code=303), request, token, "mfa")
        token = auth.create_session("ok", admin_id)
        return with_cookie(RedirectResponse(safe_next(next), status_code=303), request, token, "ok")

    @app.get("/login/2fa", response_class=HTMLResponse)
    def login_2fa_form(request: Request, next: str = "/"):
        if auth.session(request.cookies.get(adminauth.COOKIE)) != "mfa":
            return RedirectResponse("/login", status_code=303)
        return render(request, "login_2fa.html", next=safe_next(next))

    @app.post("/login/2fa")
    def login_2fa(request: Request, code: str = Form(""), next: str = Form("/")):
        token = request.cookies.get(adminauth.COOKIE)
        info = auth.session_info(token)
        if not info or info[0] != "mfa":
            return back("/login", err=i18n.t(lang, "ui_err_twofa_expired"))
        admin_id = info[1]
        who = "2fa:" + client(request)
        if code_throttle.retry_after(who) or code_throttle.retry_after("2fa"):
            return render(request, "login_2fa.html", status=429, next=safe_next(next),
                          error=i18n.t(lang, "ui_err_too_many_tries"))
        if not auth.check(code, admin_id):
            code_throttle.failed(who)
            code_throttle.failed("2fa")  # a limit over all addresses too
            return render(request, "login_2fa.html", status=400, next=safe_next(next),
                          error=i18n.t(lang, "ui_err_twofa_code"))
        code_throttle.succeeded(who)
        auth.end(token)
        new = auth.create_session("ok", admin_id)
        return with_cookie(RedirectResponse(safe_next(next), status_code=303), request, new, "ok")

    @app.post("/logout")
    def logout(request: Request):
        auth.end(request.cookies.get(adminauth.COOKIE))
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(adminauth.COOKIE, path="/")
        return response

    # -- security page ------------------------------------------------------------------
    def me(request: Request) -> int:
        return getattr(request.state, "admin_id", adminauth.MAIN)

    def fresh_session(request: Request, url: str, msg: str) -> Response:
        """After a change to how you log in: your other sessions end, this browser stays in."""
        admin_id = me(request)
        auth.end_all(admin_id)
        token = auth.create_session("ok", admin_id)
        return with_cookie(back(url, msg=msg), request, token, "ok")

    def pending_key(admin_id: int) -> str:
        return "admin_totp_pending" if admin_id == adminauth.MAIN else f"admin_totp_pending:{admin_id}"

    def totp_label(admin_id: int) -> str:
        return auth.name_of(admin_id) or "admin"

    @app.get("/security", response_class=HTMLResponse)
    def security_page(request: Request):
        admin_id = me(request)
        return render(request, "security.html", me_id=admin_id, me_name=auth.name_of(admin_id),
                      totp_on=auth.totp_enabled(admin_id), totp_since=auth.totp_since(admin_id),
                      recovery_left=auth.recovery_left(admin_id))

    @app.post("/security/logout-all")
    def logout_all(request: Request):
        return fresh_session(request, "/security", i18n.t(lang, "ui_sessions_ended"))

    @app.get("/security/2fa", response_class=HTMLResponse)
    def twofa_setup(request: Request):
        admin_id = me(request)
        if not auth.enabled or auth.totp_enabled(admin_id):
            return RedirectResponse("/security", status_code=303)
        stored = app_service.db.get_state(pending_key(admin_id))  # a reload keeps the same key
        secret = auth.unseal(stored) if stored else totp.new_secret()
        app_service.db.set_state(pending_key(admin_id), auth.seal(secret))
        return render(request, "twofa_setup.html", key=totp.grouped(secret),
                      qr=qr_svg(totp.uri(secret, totp_label(admin_id))))

    @app.post("/security/2fa")
    def twofa_enable(request: Request, code: str = Form("")):
        admin_id = me(request)
        stored = app_service.db.get_state(pending_key(admin_id))
        if not auth.enabled or auth.totp_enabled(admin_id) or not stored:
            return RedirectResponse("/security", status_code=303)
        secret = auth.unseal(stored)
        codes = auth.enable(secret, code, admin_id)
        if codes is None:
            return render(request, "twofa_setup.html", status=400, key=totp.grouped(secret),
                          qr=qr_svg(totp.uri(secret, totp_label(admin_id))),
                          error=i18n.t(lang, "ui_err_twofa_code"))
        app_service.db.execute("DELETE FROM state WHERE key = ?", (pending_key(admin_id),))
        auth.end_all(admin_id)
        token = auth.create_session("ok", admin_id)
        return with_cookie(render(request, "twofa_codes.html", codes=codes), request, token, "ok")

    @app.post("/security/2fa/disable")
    def twofa_disable(request: Request, password: str = Form(""), code: str = Form("")):
        admin_id = me(request)
        who = "2fa:" + client(request)
        if code_throttle.retry_after(who):
            return back("/security", err=i18n.t(lang, "ui_err_too_many_tries"))
        if not auth.password_ok(password, admin_id) or not auth.check(code, admin_id):
            code_throttle.failed(who)
            return back("/security", err=i18n.t(lang, "ui_err_twofa_disable"))
        auth.disable(admin_id)
        return fresh_session(request, "/security", i18n.t(lang, "ui_twofa_off_done"))

    @app.post("/security/password")
    def change_password(request: Request, current: str = Form(""), new: str = Form("")):
        admin_id = me(request)
        if admin_id == adminauth.MAIN:
            raise HTTPException(404, "Not found")
        who = "pw:" + client(request)
        if password_throttle.retry_after(who):
            return back("/security", err=i18n.t(lang, "ui_err_too_many_tries"))
        if not auth.password_ok(current, admin_id):
            password_throttle.failed(who)
            return back("/security", err=i18n.t(lang, "ui_err_login"))
        try:
            auth.set_password(admin_id, new)
        except ValueError:
            return back("/security", err=i18n.t(lang, "ui_err_admin_password", n=adminauth.PASSWORD_MIN))
        return fresh_session(request, "/security", i18n.t(lang, "ui_admin_password_changed"))

    # -- more admins ----------------------------------------------------------------------
    @app.get("/admins", response_class=HTMLResponse)
    def admins_page(request: Request):
        return render(request, "admins.html", admins=auth.admins(), me_id=me(request),
                      invite_link=request.query_params.get("link", ""))

    @app.post("/admins")
    def invite_admin(request: Request, name: str = Form("")):
        if not auth.enabled:
            return back("/admins", err=i18n.t(lang, "ui_security_no_password"))
        try:
            token = auth.invite(name, me(request))
        except ValueError as exc:
            return back("/admins", err=i18n.t(lang, "ui_err_admin_" + str(exc)))
        link = str(request.base_url).rstrip("/") + "/invite/" + token
        return render(request, "admins.html", admins=auth.admins(), me_id=me(request), invite_link=link,
                      invite_qr=qr_svg(link), invite_name=name)

    @app.post("/admins/{admin_id}/remove")
    def remove_admin(request: Request, admin_id: int):
        if admin_id == adminauth.MAIN:
            raise HTTPException(404, "Not found")
        auth.remove(admin_id)
        if admin_id == me(request):
            response = RedirectResponse("/login", status_code=303)
            response.delete_cookie(adminauth.COOKIE, path="/")
            return response
        return back("/admins", msg=i18n.t(lang, "ui_admin_removed"))

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite_form(request: Request, token: str):
        admin = auth.invited(token)
        if admin is None:
            return render(request, "invite.html", status=404, gone=True)
        return render(request, "invite.html", invitee=admin.name, token=token, min=adminauth.PASSWORD_MIN)

    @app.post("/invite/{token}")
    def invite_accept(request: Request, token: str, password: str = Form(""), again: str = Form("")):
        admin = auth.invited(token)
        if admin is None:
            return render(request, "invite.html", status=404, gone=True)
        error = ""
        if password != again:
            error = i18n.t(lang, "ui_err_admin_mismatch")
        else:
            try:
                adminauth.check_new_password(password)
            except ValueError:
                error = i18n.t(lang, "ui_err_admin_password", n=adminauth.PASSWORD_MIN)
        if error:
            return render(request, "invite.html", status=400, invitee=admin.name, token=token,
                          min=adminauth.PASSWORD_MIN, error=error)
        admin_id = auth.accept(token, password)
        auth.end(request.cookies.get(adminauth.COOKIE))
        session = auth.create_session("ok", admin_id)
        return with_cookie(back("/", msg=i18n.t(lang, "ui_admin_welcome", name=admin.name)), request, session, "ok")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        last_scan = app_service.db.get_state("last_scan")
        return render(
            request, "index.html",
            recipients=app_service.db.recipients(),
            counts=app_service.db.media_counts(),
            last_scan=last_scan[:16].replace("T", " ") if last_scan else "–",
            sources=app_service.source_status(),
        )

    @app.post("/recipients")
    def add_recipient(name: str = Form(""), delivery: str = Form(""), address: str = Form(""),
                      language: str = Form("")):
        try:
            r = app_service.create_recipient(name, delivery=delivery or None, address=address, language=language)
        except ValueError as exc:
            return back("/", err=validation_message(exc))
        return back(f"/recipients/{r.id}")

    @app.get("/recipients/{rid}", response_class=HTMLResponse)
    def show_recipient(request: Request, rid: int):
        r = recipient_or_404(rid)
        url = app_service.subscribe_url(r)
        link = app_service.subscribe_link(r)
        app_link = app_service.app_link(r) if app_service.settings.app_enabled else ""
        return render(
            request, "recipient.html", r=r, qr=qr_svg(link), subscribe_url=url, deep_link=link,
            app_link=app_link, app_qr=qr_svg(app_link) if app_link else "",
            app_enabled=app_service.settings.app_enabled,
            devices=len(app_service.db.push_subscriptions(rid)),
            server=app_service.settings.public_ntfy_url,
            history=app_service.db.recent_sends(rid),
            freq=app_service.frequency_text(r, lang),
            channel=app_service.channels.get(r.delivery),
            channel_info=app_service.channels[r.delivery].info() if r.delivery in app_service.channels else {},
        )

    @app.post("/recipients/{rid}")
    def save_recipient(
        rid: int,
        name: str = Form(...),
        per_day: str = Form(...),
        every_days: str = Form(...),
        window_start: str = Form(...),
        window_end: str = Form(...),
        min_gap_minutes: str = Form(...),
        enabled: str = Form(""),
        delivery: str = Form(""),
        address: str = Form(""),
        language: str = Form(""),
        year_review: str = Form(""),
    ):
        current = recipient_or_404(rid)
        try:
            app_service.update_recipient(
                rid, delivery=delivery or current.delivery, address=address, language=language,
                name=name.strip(), per_day=_int(per_day, "per_day"),
                every_days=_int(every_days, "every_days"), window_start=window_start,
                window_end=window_end, min_gap_minutes=_int(min_gap_minutes, "min_gap_minutes"),
                enabled=bool(enabled), year_review=bool(year_review),
            )
        except ValueError as exc:
            return back(f"/recipients/{rid}", err=validation_message(exc))
        return back(f"/recipients/{rid}", msg=i18n.t(lang, "ui_saved"))

    # -- hearts and the year review ------------------------------------------------------
    @app.get("/hearts", response_class=HTMLResponse)
    def hearts_page(request: Request):
        overview = insights.hearts(app_service.db.app_sends())
        return render(request, "hearts.html", overview=overview,
                      app_recipients=[r for r in app_service.db.recipients() if r.delivery == "app"])

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, year: int = 0):
        now = app_service.now()
        year = year or (now.year - 1 if now.month == 1 else now.year)
        rows = []
        for r in app_service.db.recipients():
            rv = app_service.year_review(r, year)
            rows.append({"r": r, "review": rv, "sent": app_service.review_sent(r, year),
                         "text": app_service.review_text(r, rv) if rv else None})
        return render(request, "review.html", year=year, rows=rows, min_memories=review.MIN_MEMORIES)

    @app.post("/recipients/{rid}/review")
    def send_review(rid: int, year: int = Form(0)):
        r = recipient_or_404(rid)
        result = app_service.send_review(r, year)
        if result.status == "ok":
            return back(f"/review?year={year}", msg=i18n.t(lang, "ui_review_sent", name=r.name))
        return back(f"/review?year={year}", err=f"{i18n.t(lang, 'ui_send_failed')}: {result.detail}")

    # -- photos: the caption per photo ---------------------------------------------------------
    PHOTOS_PER_PAGE = 60
    TITLE_MAX, NOTE_MAX = 80, 200

    @app.get("/photos", response_class=HTMLResponse)
    def photos_page(request: Request, folder: str | None = None, page: int = 1):
        folders = app_service.db.media_folders()
        if folder is not None and folder not in {f for f, _ in folders}:
            folder = None
        page = max(1, page)
        items, total = app_service.db.media_page(folder, (page - 1) * PHOTOS_PER_PAGE, PHOTOS_PER_PAGE)
        pages = max(1, -(-total // PHOTOS_PER_PAGE))
        return render(request, "photos.html", items=items, folders=folders, folder=folder, page=page,
                      pages=pages, total=total, album_name=lambda m: caption.album_name(m, lang),
                      folder_sender=app_service.db.album_sender(folder) if folder is not None else "",
                      folder_label=lambda f: i18n.t(lang, "ui_photos_uploaded", album=f[len("@upload/"):])
                      if f.startswith("@upload/") else f)

    @app.get("/photos/{media_id}", response_class=HTMLResponse)
    def photo_page(request: Request, media_id: int, folder: str | None = None, page: int = 1):
        m = app_service.db.media_by_id(media_id)
        if m is None:
            raise HTTPException(404, "Not found")
        title, text = caption.build(m, app_service.now().date(), lang)
        return render(request, "photo.html", m=m, album=caption.album_name(m, lang), preview_title=title,
                      preview_text=text, folder=folder, page=page, title_max=TITLE_MAX, note_max=NOTE_MAX,
                      original=caption.format_date(m.taken_original.date(), lang) if m.taken_original else "")

    @app.post("/photos/{media_id}")
    def save_photo(media_id: int, title: str = Form(""), note: str = Form(""), taken: str = Form(""),
                   sender: str | None = Form(None),
                   reset_date: str = Form(""), folder: str | None = Form(None), page: int = Form(1)):
        m = app_service.db.media_by_id(media_id)
        if m is None:
            raise HTTPException(404, "Not found")
        here = f"/photos/{media_id}?" + urlencode({**({"folder": folder} if folder is not None else {}), "page": page})
        def clean(text: str, limit: int) -> str:  # one line, no control characters
            return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:limit]
        when = None
        if taken.strip() and not reset_date:
            try:
                day = date.fromisoformat(taken.strip())
            except ValueError:
                return back(here, err=i18n.t(lang, "ui_err_photo_date"))
            if not (1900 <= day.year <= app_service.now().year) or day > app_service.now().date():
                return back(here, err=i18n.t(lang, "ui_err_photo_date"))
            keep_time = m.taken_at.time() if m.taken_at else time(12, 0)
            when = datetime.combine(day, keep_time)
        app_service.db.edit_media(media_id, clean(title, TITLE_MAX), clean(note, NOTE_MAX), when, bool(reset_date))
        if sender is not None:
            app_service.db.set_sender(media_id, clean(sender, uploads.NAME_MAX))
        return back(here, msg=i18n.t(lang, "ui_saved"))

    # -- upload links -------------------------------------------------------------------------------
    @app.get("/uploads", response_class=HTMLResponse)
    def uploads_page(request: Request):
        links = [dict(row) | {"url": app_service.upload_link_url(row["token"])}
                 for row in app_service.db.upload_links()]
        for link in links:
            link["qr"] = qr_svg(link["url"]) if link["url"] else ""
        albums = sorted({caption.album_name(m, lang) for m in app_service.db.available_media()} |
                        {row["album"] for row in links})
        return render(request, "uploads.html", links=links, albums=albums, can=bool(app_service.settings.app_url))

    @app.post("/uploads")
    def add_upload_link(album: str = Form(""), name: str = Form("")):
        if not app_service.settings.app_url:
            return back("/uploads", err=i18n.t(lang, "ui_uploads_need_app"))
        try:
            app_service.create_upload_link(album, name)
        except uploads.UploadError as exc:
            return back("/uploads", err=i18n.t(lang, "ui_err_upload_" + str(exc)))
        return back("/uploads", msg=i18n.t(lang, "ui_uploads_made"))

    @app.post("/uploads/{link_id}/revoke")
    def revoke_upload_link(link_id: int):
        app_service.db.revoke_upload_link(link_id, app_service.now())
        return back("/uploads", msg=i18n.t(lang, "ui_uploads_revoked"))

    @app.post("/photos/{media_id}/delete")
    def delete_photo(media_id: int, folder: str | None = Form(None), page: int = Form(1)):
        if not app_service.delete_upload(media_id):
            raise HTTPException(404, "Not found")
        query = urlencode({**({"folder": folder} if folder is not None else {}), "page": page})
        return back("/photos?" + query, msg=i18n.t(lang, "ui_photo_deleted"))

    @app.post("/albums/sender")
    def album_sender(folder: str = Form(...), sender: str = Form("")):
        if folder not in {f for f, _ in app_service.db.media_folders()}:
            raise HTTPException(404, "Not found")
        app_service.db.set_album_sender(folder, uploads.clean_text(sender, uploads.NAME_MAX))
        return back("/photos?" + urlencode({"folder": folder}), msg=i18n.t(lang, "ui_saved"))

    @app.get("/thumb/{media_id}")
    def thumb(media_id: int):
        """A small JPEG of a photo for the admin page, made once and kept in the cache folder."""
        item = app_service.db.media_by_id(media_id)
        if item is None or item.kind != "image":
            raise HTTPException(404, "Not found")
        cache = app_service.settings.data_dir / "cache" / "thumbs" / f"{media_id}-{int(item.mtime)}.jpg"
        if not cache.exists():
            path = app_service.media_file(item.path)
            try:
                prepared = media.prepare(path, "image", max_edge=360, image_limit=200_000, video_limit=0,
                                         taken_at=None)
            except (FileNotFoundError, media.SkipMedia, OSError, ValueError) as exc:
                raise HTTPException(404, "Not found") from exc
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(prepared.data)
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/recipients/{rid}/send")
    def send_now(rid: int):
        recipient_or_404(rid)
        result = app_service.send_now(rid)
        if result.status == "ok":
            return back(f"/recipients/{rid}", msg=i18n.t(lang, "ui_sent"))
        if result.status == "empty":
            return back(f"/recipients/{rid}", err=i18n.t(lang, "ui_empty"))
        return back(f"/recipients/{rid}", err=f"{i18n.t(lang, 'ui_send_failed')}: {result.detail}")

    @app.post("/recipients/{rid}/command/{command}")
    def command(rid: int, command: str):
        recipient_or_404(rid)
        if command not in {"pause", "resume", "more", "less"}:
            raise HTTPException(400, "Unknown command")
        app_service.handle_command(rid, command)
        return back(f"/recipients/{rid}", msg=i18n.t(lang, "ui_saved"))

    @app.post("/recipients/{rid}/delete")
    def delete(rid: int):
        recipient_or_404(rid)
        app_service.delete_recipient(rid)
        return back("/")

    @app.post("/rescan")
    def rescan():
        res = app_service.rescan()
        return back("/", msg=i18n.t(lang, "ui_scanned", added=res.added, removed=res.removed, total=res.total))

    @app.post("/sources/sync")
    def sync_sources():
        started = app_service.sync_in_background()
        return back("/", msg=i18n.t(lang, "ui_sync_started" if started else "ui_sync_running"))

    @app.get("/admin.js")
    def admin_script():
        return FileResponse(STATIC / "admin.js", media_type="text/javascript")

    @app.get("/healthz")
    def health():
        return JSONResponse({"status": "ok", "version": __version__, "media": app_service.db.media_counts()})

    return app
