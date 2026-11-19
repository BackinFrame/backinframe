"""Glue: scanning, scheduling, sending and handling button presses."""

from __future__ import annotations

import json
import logging
import random
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import caption, channels, control, i18n, media, review, scanner, selector, sources, uploads, webpush
from .config import Settings
from .db import Database, Recipient
from .ntfy import HttpAction, NtfyClient, NtfyError, subscribe_link
from .scheduler import Schedule, ScheduleError, next_send

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 15 * 1024 * 1024  # ntfy's own default
APP_IMAGE_LIMIT = 8 * 1024 * 1024  # app delivery: no ntfy limit, still keep phones' data use low
GALLERY_SIZE = 60
MAX_DEVICES = 10
DELIVERIES = ("app", "ntfy", "email", "telegram", "signal", "matrix", "discord")
BUILT_IN = ("app", "ntfy")
MAX_ATTEMPTS = 5
REACTION_BOOST = 1.5  # a photo with a heart from this recipient comes back 1.5 times as often
REVIEW_DAYS = 14      # the year review goes out at the first send moment in the first two weeks of January


@dataclass
class SendResult:
    status: str  # 'ok' | 'empty' | 'error'
    media_id: int | None = None
    detail: str = ""


def new_topic(prefix: str = "backinframe") -> str:
    # ntfy topics: [-_A-Za-z0-9]{1,64}. ~190 bits of randomness: it is a password.
    return f"{prefix}-{secrets.token_urlsafe(24)}"


class BackInFrame:
    def __init__(
        self,
        settings: Settings,
        db: Database | None = None,
        ntfy: NtfyClient | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ):
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.ntfy = ntfy or NtfyClient(settings.ntfy_url, settings.ntfy_token)
        self._clock = clock or (lambda: datetime.now(settings.tz))
        self.rng = rng or random.Random()
        self._server_limit: int | None = None
        self._vapid: webpush.Vapid | None = None
        self.push_client = None  # an httpx.Client for tests; None = a fresh one per push
        self.http_client = None  # the channels' httpx.Client (tests put a mock transport here)
        self.smtp_factory = None  # tests: a callable(host, port) returning an SMTP-like object
        self._channels: dict[str, channels.Channel] | None = None
        self._sources: dict[str, sources.Source] | None = None
        self._sync_thread: threading.Thread | None = None
        self.last_reply = ""  # the text of the last command reply (shown in the web app)
        self._lock = threading.RLock()

    # -- helpers ---------------------------------------------------------
    def now(self) -> datetime:
        return self._clock().astimezone(self.settings.tz)

    @property
    def lang(self) -> str:
        """The language of the admin page (and the default for recipients)."""
        return self.settings.language

    def lang_of(self, r: Recipient) -> str:
        return r.language if r.language in i18n.LANGUAGES else self.settings.language

    # -- channels --------------------------------------------------------
    @property
    def channels(self) -> dict[str, channels.Channel]:
        """The extra channels that are set up, by name."""
        if self._channels is None:
            st, client = self.settings, self.http_client
            found: dict[str, channels.Channel] = {}
            if st.email_enabled:
                found["email"] = channels.EmailChannel(
                    st.smtp_host, st.smtp_port, st.smtp_user, st.smtp_password, st.smtp_from, st.smtp_security,
                    st.email_max_mb, smtp_factory=self.smtp_factory)
            if st.telegram_enabled:
                found["telegram"] = channels.TelegramChannel(st.telegram_bot_token, st.telegram_api_url, client)
            if st.signal_enabled:
                found["signal"] = channels.SignalChannel(st.signal_url, st.signal_number, client)
            if st.matrix_enabled:
                found["matrix"] = channels.MatrixChannel(st.matrix_homeserver, st.matrix_token, client)
            if st.discord_enabled:
                found["discord"] = channels.DiscordChannel(client)
            self._channels = found
        return self._channels

    def deliveries(self) -> list[str]:
        """What a recipient can choose on this server, in menu order."""
        out = ["app"] if self.settings.app_enabled else []
        return out + ["ntfy"] + [d for d in DELIVERIES if d in self.channels]

    def server_limit(self) -> int:
        if self._server_limit is None:
            if self.settings.max_attachment_mb:
                self._server_limit = int(self.settings.max_attachment_mb * 1024 * 1024)
            else:
                self._server_limit = self.ntfy.attachment_limit() or DEFAULT_LIMIT
            log.info("Attachment limit: %.1f MB", self._server_limit / 1024 / 1024)
        return self._server_limit

    def limits(self) -> tuple[int, int]:
        server = self.server_limit()
        video = min(server, int(self.settings.video_max_mb * 1024 * 1024))
        return server, video

    @staticmethod
    def schedule_of(r: Recipient) -> Schedule:
        return Schedule(r.per_day, r.every_days, r.window_start, r.window_end, r.min_gap_minutes)

    def frequency_text(self, r: Recipient, lang: str | None = None) -> str:
        return i18n.frequency(lang or self.lang_of(r), r.per_day, r.every_days)

    def subscribe_url(self, r: Recipient) -> str:
        return f"{self.settings.public_ntfy_url}/{r.topic}"

    def subscribe_link(self, r: Recipient) -> str:
        """For the QR code: opens the Android app and subscribes in one scan."""
        return subscribe_link(self.settings.public_ntfy_url, r.topic, display="Back in Frame")

    # -- web app ---------------------------------------------------------
    @property
    def vapid(self) -> webpush.Vapid:
        if self._vapid is None:
            self._vapid = webpush.Vapid.load_or_create(self.settings.vapid_path, self.settings.push_subject)
        return self._vapid

    def app_link(self, r: Recipient) -> str:
        return f"{self.settings.public_app_url}/a/{r.app_token}"

    def default_delivery(self) -> str:
        return "app" if self.settings.app_enabled else "ntfy"

    def subscribe_push(self, r: Recipient, data: dict) -> None:
        sub = webpush.Subscription.from_json(data, getattr(self, "push_hosts_for_tests", None))
        self.db.add_push_subscription(r.id, sub.endpoint, sub.p256dh, sub.auth, self.now())
        devices = self.db.push_subscriptions(r.id)
        for old in devices[:-MAX_DEVICES]:  # a recipient has a few phones and tablets, not hundreds
            self.db.delete_push_subscription(old["endpoint"])

    def unsubscribe_push(self, r: Recipient, endpoint: str) -> None:
        self.db.delete_push_subscription(endpoint, r.id)

    def push_to(self, r: Recipient, message: dict) -> int:
        """Send an (encrypted) notification to every device of the recipient. Returns deliveries."""
        delivered = 0
        for row in self.db.push_subscriptions(r.id):
            sub = webpush.Subscription(row["endpoint"], row["p256dh"], row["auth"])
            try:
                webpush.send(self.vapid, sub, message, client=self.push_client)
                delivered += 1
            except webpush.PushGone:
                log.info("%s: a device unsubscribed, forgetting it", r.name)
                self.db.delete_push_subscription(row["endpoint"])
            except webpush.PushError as exc:
                log.warning("%s: push failed: %s", r.name, exc)
        return delivered

    def cache_path(self, media_id: int, mtime: float, kind: str) -> Path:
        ext = "jpg" if kind == "image" else "mp4"
        return self.settings.cache_dir / f"{media_id}-{int(mtime)}.{ext}"

    def _store(self, item, prepared: media.Prepared) -> None:
        path = self.cache_path(item.id, item.mtime, item.kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(prepared.data)
        tmp.replace(path)

    def gallery_media(self, r: Recipient, send_id: int) -> tuple[Path, str] | None:
        """The prepared (metadata-free) file for one item of the recipient's gallery."""
        row = self.db.gallery_item(r.id, send_id)
        if row is None:
            return None
        path = self.cache_path(row["media_id"], row["mtime"], row["kind"])
        if not path.exists():
            item = self.db.media_by_id(row["media_id"])
            try:
                prepared = media.prepare(
                    self.media_file(item.path), item.kind,
                    max_edge=self.settings.image_max_edge, image_limit=APP_IMAGE_LIMIT,
                    video_limit=int(self.settings.video_max_mb * 1024 * 1024), taken_at=item.taken_at,
                )
            except (FileNotFoundError, media.SkipMedia):
                return None
            self._store(item, prepared)
        return path, ("image/jpeg" if row["kind"] == "image" else "video/mp4")

    def prune_cache(self) -> int:
        keep = self.db.media_in_galleries(GALLERY_SIZE)
        removed = 0
        if not self.settings.cache_dir.exists():
            return 0
        for path in self.settings.cache_dir.iterdir():
            try:
                media_id = int(path.name.split("-", 1)[0])
            except ValueError:
                continue
            if media_id not in keep:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def control_url(self, r: Recipient) -> str:
        return f"{self.settings.public_ntfy_url}/{r.control_topic}"

    def buttons(self, r: Recipient) -> list[tuple[str, str]]:
        lang = self.lang_of(r)
        return [(i18n.t(lang, "btn_more"), "more"), (i18n.t(lang, "btn_less"), "less"),
                (i18n.t(lang, "btn_pause"), "pause")]

    def actions_for(self, r: Recipient) -> list[HttpAction]:
        url = self.control_url(r)
        return [HttpAction(label, url, command) for label, command in self.buttons(r)]

    def channel_message(self, r: Recipient, title: str, text: str = "", prepared: media.Prepared | None = None,
                        kind: str = "") -> channels.Message:
        ch = self.channels[r.delivery]
        lang = self.lang_of(r)
        msg = channels.Message(title=title, text=text, ref=str(r.id),
                               footer=i18n.t(lang, "mail_footer") if ch.name == "email" else "")
        if prepared is not None:
            msg.data, msg.filename, msg.kind = prepared.data, prepared.filename, kind
            if ch.buttons:
                msg.actions = self.buttons(r)
            elif ch.replies:
                msg.hint = i18n.t(lang, "reply_hint")
        return msg

    # -- scanning --------------------------------------------------------
    @property
    def sources(self) -> dict[str, sources.Source]:
        """Immich and Nextcloud, when they are set up."""
        if self._sources is None:
            self._sources = sources.build_sources(self.settings, self.http_client)
        return self._sources

    def media_file(self, rel: str) -> Path:
        """Where a media item lives: the photo folder, or the synced copy of a source."""
        name, _, rest = rel.partition("/")
        if name == "@upload" and rest:
            return self.settings.uploads_dir / rest
        if name.startswith("@") and name[1:] in sources.NAMES and rest:
            return self.settings.sources_dir / name[1:] / rest
        return Path(self.settings.media_dir) / rel

    # -- upload links -----------------------------------------------------------------------------
    def create_upload_link(self, album: str, name: str) -> str:
        folder = uploads.album_folder(album)
        who = uploads.clean_text(name, uploads.NAME_MAX)
        if not who:
            raise uploads.UploadError("name")
        token = secrets.token_urlsafe(24)
        self.db.add_upload_link(token, folder, who, self.now())
        return token

    def upload_link_url(self, token: str) -> str:
        return f"{self.settings.app_url.rstrip('/')}/u/{token}" if self.settings.app_url else ""

    def upload_files(self, token: str, files) -> tuple[int, list[tuple[str, str]]]:
        """``files``: (filename, stream). Returns how many were added and (filename, reason) for the rest."""
        link = self.db.upload_link(token)
        if link is None:
            raise uploads.UploadError("link")
        folder = self.settings.uploads_dir / link["album"]
        limit = int(self.settings.upload_max_mb * 1024 * 1024)
        saved, failed = [], []
        for filename, stream in list(files)[:uploads.MAX_FILES_PER_POST]:
            try:
                saved.append(uploads.store(folder, filename, stream, limit))
            except uploads.UploadError as exc:
                failed.append((uploads.clean_text(Path(filename or "?").name, 80), str(exc)))
                if str(exc) == "space":
                    break
        if saved:
            self.rescan()
            for path in saved:
                rel = "@upload/" + path.relative_to(self.settings.uploads_dir).as_posix()
                self.db.execute("UPDATE media SET sender = ? WHERE path = ?", (link["name"], rel))
            self.db.count_upload(link["id"], len(saved), self.now())
            log.info("Upload link %s: %d file(s) into %s", link["id"], len(saved), link["album"])
        return len(saved), failed

    def delete_upload(self, media_id: int) -> bool:
        """Remove a photo that came in through an upload link (other photos are not ours to delete)."""
        m = self.db.media_by_id(media_id)
        if m is None or not m.path.startswith("@upload/"):
            return False
        path = self.media_file(m.path)
        base = self.settings.uploads_dir.resolve()
        if base in path.resolve().parents:
            path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM media WHERE id = ?", (media_id,))
        return True

    def rescan(self) -> scanner.ScanResult:
        with self._lock:
            extra = {name: self.settings.sources_dir / name for name in self.sources}
            extra["upload"] = self.settings.uploads_dir
            result = scanner.scan(self.db, self.settings.media_dir, extra)
            self.db.set_state("last_scan", self.now().isoformat())
            return result

    def sync_sources(self) -> dict[str, dict]:
        """Copy what changed in Immich and Nextcloud, then rescan. Returns the status per source."""
        status = {}
        for name, src in self.sources.items():
            entry = {"at": self.now().isoformat(timespec="seconds"), **src.info()}
            try:
                res = src.sync(self.settings.sources_dir / name)
            except sources.SourceError as exc:
                entry.update(ok=False, error=str(exc))
                log.warning("%s", exc)
            else:
                entry.update(ok=not res.errors, downloaded=res.downloaded, kept=res.kept, removed=res.removed,
                             skipped=res.skipped, errors=res.errors[:5], failed=len(res.errors))
                log.info("Synced %s: %d new, %d kept, %d removed, %d failed", name, res.downloaded, res.kept,
                         res.removed, len(res.errors))
            self.db.set_state(f"source_{name}", json.dumps(entry))
            status[name] = entry
        if self.sources:
            self.rescan()
        return status

    def sync_in_background(self) -> bool:
        """Start a sync in its own thread (a first sync can take a while); False if one is running."""
        if self._sync_thread and self._sync_thread.is_alive():
            return False

        def run():
            try:
                self.sync_sources()
            except Exception:  # noqa: BLE001 - a broken sync must not stop the service
                log.exception("Sync failed")

        self._sync_thread = threading.Thread(target=run, name="sync", daemon=True)
        self._sync_thread.start()
        return True

    def source_status(self) -> dict[str, dict]:
        out = {}
        for name in self.sources:
            raw = self.db.get_state(f"source_{name}")
            try:
                out[name] = json.loads(raw) if raw else {}
            except ValueError:
                out[name] = {}
        return out

    # -- recipients --------------------------------------------------------
    def _check_delivery(self, delivery: str, address: str = "") -> str:
        """Validates the choice; returns the address in canonical form ('' for app and ntfy)."""
        if delivery not in DELIVERIES:
            raise ValueError("delivery must be one of " + ", ".join(DELIVERIES))
        if delivery == "app" and not self.settings.app_enabled:
            raise ValueError("the web app is off: set BACKINFRAME_APP_URL first")
        if delivery in BUILT_IN:
            return ""
        if delivery not in self.channels:
            raise ValueError(f"{delivery} is not set up on this server (see the admin guide)")
        if not (address or "").strip():
            raise ValueError(f"{delivery} needs an address")
        return self.channels[delivery].check_address(address)

    @staticmethod
    def _check_language(language: str) -> str:
        if language and language not in i18n.LANGUAGES:
            raise ValueError("unknown language")
        return language or ""

    def create_recipient(self, name: str, *, welcome: bool = True, delivery: str | None = None,
                         address: str = "", language: str = "", **schedule_fields) -> Recipient:
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
        delivery = delivery or self.default_delivery()
        address = self._check_delivery(delivery, address)
        language = self._check_language(language)
        sched = Schedule(**{**Schedule().__dict__, **schedule_fields}).validate()
        with self._lock:
            rid = self.db.insert_recipient(name, new_topic(), new_topic("backinframe-ctl"), self.now(),
                                           delivery=delivery, address=address, language=language)
            self.db.update_recipient(rid, **sched.__dict__)
            r = self.reschedule(rid)
        if welcome and r.delivery != "app":  # app users see the welcome in the app itself
            self._welcome(r)
        return r

    def _welcome(self, r: Recipient) -> None:
        lang = self.lang_of(r)
        key = ("welcome_buttons" if r.delivery in ("ntfy", "telegram")
               else "welcome_replies" if r.delivery == "signal" else "welcome")
        self._notify(r, i18n.t(lang, key, freq=self.frequency_text(r), start=r.window_start, end=r.window_end),
                     title=i18n.t(lang, "welcome_title"))

    def update_recipient(self, rid: int, **fields) -> Recipient:
        r = self.db.recipient(rid)
        if r is None:
            raise KeyError(rid)
        sched_keys = set(Schedule().__dict__)
        sched_fields = {k: v for k, v in fields.items() if k in sched_keys}
        if sched_fields:
            merged = {**self.schedule_of(r).__dict__, **sched_fields}
            Schedule(**merged).validate()
        if "name" in fields and not str(fields["name"]).strip():
            raise ValueError("name is required")
        if "delivery" in fields or "address" in fields:
            delivery = fields.get("delivery", r.delivery)
            address = fields.get("address", r.address if delivery == r.delivery else "")
            fields["address"] = self._check_delivery(delivery, address)
        if "language" in fields:
            fields["language"] = self._check_language(fields["language"])
        with self._lock:
            self.db.update_recipient(rid, **fields)
            updated = self.reschedule(rid)
        moved = (updated.delivery, updated.address) != (r.delivery, r.address)
        if moved and updated.delivery != "app":
            self._welcome(updated)  # a new place to receive: greet them there
        return updated

    def delete_recipient(self, rid: int) -> None:
        self.db.delete_recipient(rid)

    def reschedule(self, rid: int, after: datetime | None = None) -> Recipient:
        r = self.db.recipient(rid)
        when = None
        if r.enabled:
            when = next_send(
                after or self.now(), self.schedule_of(r), self.settings.tz,
                anchor=r.created_at.astimezone(self.settings.tz).date(),
                seed=f"{r.id}|{r.fingerprint()}", paused_until=r.paused_until,
            )
        self.db.update_recipient(rid, next_send_at=when)
        return self.db.recipient(rid)

    # -- sending ---------------------------------------------------------
    def _notify(self, r: Recipient, message: str, title: str = "") -> None:
        if r.delivery == "app":
            self.push_to(r, {"title": title or "Back in Frame", "body": message, "url": self.app_link(r)})
            return
        if r.delivery not in BUILT_IN:
            ch = self.channels.get(r.delivery)
            if ch is None:
                log.warning("%s: %s is not set up any more", r.name, r.delivery)
                return
            try:
                ch.send(r.address, self.channel_message(r, title or "Back in Frame", message))
            except channels.ChannelError as exc:
                log.warning("Could not notify %s: %s", r.name, exc)
            return
        try:
            self.ntfy.publish(r.topic, message, title=title, tags=["camera"])
        except NtfyError as exc:
            log.warning("Could not notify %s: %s", r.name, exc)

    def send_to(self, r: Recipient) -> SendResult:
        now = self.now()
        video_max = int(self.settings.video_max_mb * 1024 * 1024)
        ch = self.channels.get(r.delivery) if r.delivery not in BUILT_IN else None
        if r.delivery not in BUILT_IN and ch is None:
            detail = f"{r.delivery} is not set up on this server"
            self.db.log_send(r.id, None, now, "error", detail)
            return SendResult("error", None, detail)
        if r.delivery == "app":
            image_limit, video_limit = APP_IMAGE_LIMIT, video_max
        elif ch is not None:
            image_limit, video_limit = min(APP_IMAGE_LIMIT, ch.image_limit), min(video_max, ch.video_limit)
        else:
            image_limit, video_limit = self.limits()
        items = self.db.available_media(include_videos=self.settings.send_videos)
        history = self.db.send_history(r.id)
        tried: set[int] = set()
        for _ in range(MAX_ATTEMPTS):
            candidates = [m for m in items if m.id not in tried]
            item = selector.choose(candidates, history, now, self.rng, self.schedule_of(r).sends_per_day,
                                   self.reaction_boost(r))
            if item is None:
                break
            tried.add(item.id)
            path = self.media_file(item.path)
            try:
                prepared = media.prepare(
                    path, item.kind, max_edge=self.settings.image_max_edge,
                    image_limit=image_limit, video_limit=video_limit, taken_at=item.taken_at,
                )
            except FileNotFoundError:
                self.db.execute("UPDATE media SET missing = 1 WHERE id = ?", (item.id,))
                continue
            except media.SkipMedia as exc:
                log.info("Skipping %s: %s", item.path, exc)
                self.db.mark_broken(item.id)
                self.db.log_send(r.id, item.id, now, "skipped", str(exc))
                continue
            except Exception as exc:  # corrupt files and the like
                log.warning("Cannot prepare %s: %s", item.path, exc)
                self.db.mark_broken(item.id)
                continue

            title, text = caption.build(item, now.date(), self.lang_of(r))
            if r.delivery == "app":
                return self._deliver_in_app(r, item, prepared, title, text, now)
            if ch is not None:
                return self._deliver_by_channel(r, ch, item, prepared, title, text, now)
            try:
                self.ntfy.publish(
                    r.topic, text or title, title=title, data=prepared.data,
                    filename=prepared.filename, actions=self.actions_for(r),
                )
            except NtfyError as exc:
                if "too large" in str(exc):
                    self._server_limit = max(256 * 1024, self.server_limit() // 2)
                self.db.log_send(r.id, item.id, now, "error", str(exc))
                return SendResult("error", item.id, str(exc))
            self.db.log_send(r.id, item.id, now, "ok", prepared.filename)
            log.info("Sent %s to %s", item.path, r.name)
            return SendResult("ok", item.id)
        self.db.log_send(r.id, None, now, "skipped", "no media available")
        return SendResult("empty", None, "no media available")

    def _deliver_in_app(self, r: Recipient, item, prepared: media.Prepared, title: str, text: str,
                        now: datetime) -> SendResult:
        self._store(item, prepared)
        self.db.log_send(r.id, item.id, now, "ok", prepared.filename)
        send_id = self.db.last_send_id(r.id)
        link = self.app_link(r)
        message = {"title": title, "body": text or title, "url": f"{link}/#m{send_id}", "tag": f"m{send_id}"}
        if item.kind == "image":
            message["image"] = f"{link}/media/{send_id}"
        devices = self.push_to(r, message)
        log.info("Sent %s to %s in the app (%d device(s))", item.path, r.name, devices)
        return SendResult("ok", item.id, f"{devices} device(s)")

    def _deliver_by_channel(self, r: Recipient, ch: channels.Channel, item, prepared: media.Prepared, title: str,
                            text: str, now: datetime) -> SendResult:
        try:
            ch.send(r.address, self.channel_message(r, title, text, prepared, item.kind))
        except channels.ChannelError as exc:
            self.db.log_send(r.id, item.id, now, "error", str(exc)[:500])
            log.warning("%s: %s", r.name, exc)
            return SendResult("error", item.id, str(exc))
        self.db.log_send(r.id, item.id, now, "ok", prepared.filename)
        log.info("Sent %s to %s by %s", item.path, r.name, ch.name)
        return SendResult("ok", item.id)

    def send_now(self, rid: int) -> SendResult:
        r = self.db.recipient(rid)
        if r is None:
            raise KeyError(rid)
        with self._lock:
            return self.send_to(r)

    def tick(self) -> int:
        """Send to everyone whose moment has come. Returns the number of sends."""
        sent = 0
        now = self.now()
        with self._lock:
            for r in self.db.recipients():
                if not r.enabled:
                    continue
                if r.next_send_at is None:
                    self.reschedule(r.id)
                    continue
                if r.next_send_at > now:
                    continue
                if r.paused_until and r.paused_until > now:
                    self.reschedule(r.id)
                    continue
                if self.review_due(r, now):  # this send moment brings the year review instead
                    result = self.send_review(r, now.year - 1)
                    if result.status != "ok":
                        result = self.send_to(r)
                else:
                    result = self.send_to(r)
                sent += result.status == "ok"
                self.reschedule(r.id, after=now)
        return sent

    # -- buttons -----------------------------------------------------------
    def poll_controls(self) -> int:
        """Fetch button presses from ntfy and apply them. Returns commands applied."""
        recipients = {r.control_topic: r for r in self.db.recipients() if r.delivery == "ntfy"}
        real_now = int(datetime.now(UTC).timestamp())  # ntfy works in server time
        since = self.db.get_state("control_since")
        if since is None:
            # First run: start from the oldest recipient, so nothing pressed since then is lost.
            created = [int(r.created_at.timestamp()) for r in recipients.values()]
            since = str(min([real_now, *created]))
        if not recipients:
            self.db.set_state("control_since", since)
            return 0
        messages = self.ntfy.poll(sorted(recipients), since)
        applied = 0
        newest = int(since)
        for msg in sorted(messages, key=lambda m: m.get("time", 0)):
            newest = max(newest, int(msg.get("time", 0)))
            if self.db.control_seen(msg["id"]):
                continue
            self.db.mark_control(msg["id"], datetime.now(UTC))
            r = recipients.get(msg.get("topic"))
            command = control.parse(msg.get("message", ""))
            if r is None or command is None:
                continue
            self.handle_command(r.id, command)
            applied += 1
        self.db.set_state("control_since", str(newest))
        self.db.prune_controls(datetime.now(UTC) - timedelta(days=2))
        return applied

    def poll_channels(self) -> int:
        """Button presses and replies from Telegram and Signal. Returns commands applied."""
        applied = 0
        for name, ch in self.channels.items():
            if not (ch.buttons or ch.replies):
                continue
            state_key = f"{name}_offset"
            if hasattr(ch, "offset"):
                ch.offset = int(self.db.get_state(state_key) or 0)
            try:
                incoming = ch.poll()
            except channels.ChannelError as exc:
                log.warning("%s", exc)
                continue
            finally:
                if hasattr(ch, "offset"):
                    self.db.set_state(state_key, str(ch.offset))
            for inc in incoming:
                applied += self._handle_incoming(name, inc)
        return applied

    def _handle_incoming(self, name: str, inc: channels.Incoming) -> int:
        if inc.command:  # a button: only for the recipient it was sent to, from that same chat
            r = self.db.recipient(int(inc.ref)) if inc.ref.isdigit() else None
            if r is None or r.delivery != name or r.address != inc.address or inc.command not in channels.COMMANDS:
                return 0
            self.handle_command(r.id, inc.command, notify=False)
            inc.answer(self.last_reply)
            return 1
        if inc.text.strip().lower().split("@", 1)[0] in ("/start", "/id"):
            known = self.db.recipients_at(name, inc.address)
            lang = self.lang_of(known[0]) if known else self.lang
            inc.answer(i18n.t(lang, "chat_id", id=inc.address))
            return 0
        command = control.parse(inc.text)
        recipients = self.db.recipients_at(name, inc.address)
        if command is None or not recipients:
            return 0
        for r in recipients:
            self.handle_command(r.id, command, notify=False)
        inc.answer(self.last_reply)
        return len(recipients)

    def handle_command(self, rid: int, command: str, notify: bool = True) -> Recipient:
        with self._lock:
            r = self.db.recipient(rid)
            outcome = control.apply(command, self.schedule_of(r), r.paused_until, self.now())
            if outcome.changed:
                self.db.update_recipient(rid, paused_until=outcome.paused_until, **outcome.schedule.__dict__)
                r = self.reschedule(rid)
        lang = self.lang_of(r)
        freq = self.frequency_text(r)
        if outcome.limit_hit == "max":
            text = i18n.t(lang, "reply_limit_max", freq=freq)
        elif outcome.limit_hit == "min":
            text = i18n.t(lang, "reply_limit_min", freq=freq)
        elif command == "pause":
            text = i18n.t(lang, "reply_paused", date=caption.format_date(r.paused_until.date(), lang))
        elif command == "resume":
            text = i18n.t(lang, "reply_resumed", freq=freq)
        else:
            text = i18n.t(lang, "reply_set", freq=freq)
        self.last_reply = text
        if notify:
            self._notify(r, text)
        log.info("%s: %s -> %s", r.name, command, freq)
        return r

    # -- reactions ------------------------------------------------------------
    def react(self, r: Recipient, send_id: int, reaction: str) -> bool:
        """A recipient reacts on one of their own memories ('' takes it back)."""
        if not self.settings.reactions_enabled:
            return False
        return self.db.set_reaction(r.id, send_id, reaction, self.now())

    def reaction_boost(self, r: Recipient) -> dict[int, float] | None:
        """How much more often loved photos come back. None with reactions off: no change at all."""
        if not self.settings.reactions_enabled:
            return None
        loved = self.db.reactions(r.id)
        return {media_id: REACTION_BOOST for media_id, reaction in loved.items() if reaction == "heart"}

    def year_review(self, r: Recipient, year: int) -> review.YearReview | None:
        """What a recipient got in ``year``; None when it was too little for a review."""
        rv = review.build(self.db, r.id, year)
        return rv if rv is not None and rv.total >= review.MIN_MEMORIES else None

    def review_collage(self, r: Recipient, year: int) -> bytes | None:
        rv = self.year_review(r, year)
        if rv is None:
            return None
        paths = []
        for pick in rv.picks:
            item = self.db.media_by_id(pick.media_id)
            if item is not None and item.kind == "image":
                path = self.media_file(item.path)
                if path.exists():
                    paths.append(path)
        try:
            return review.collage(paths)
        except ValueError:
            return None

    def review_text(self, r: Recipient, rv: review.YearReview) -> tuple[str, str]:
        lang = self.lang_of(r)
        title = i18n.t(lang, "review_title", year=rv.year)
        top = rv.albums[0][0] if rv.albums and rv.albums[0][0] else ""
        text = (i18n.t(lang, "review_text", n=rv.total, album=top) if top
                else i18n.t(lang, "review_text_plain", n=rv.total))
        return title, text

    def review_sent(self, r: Recipient, year: int) -> datetime | None:
        value = self.db.get_state(f"year_review:{r.id}:{year}")
        return datetime.fromisoformat(value) if value else None

    def review_due(self, r: Recipient, now: datetime) -> bool:
        """At the first send moment in the first two weeks of January, once, if it was a full year."""
        return (self.settings.year_review_enabled and r.year_review and now.month == 1
                and now.day <= REVIEW_DAYS and self.review_sent(r, now.year - 1) is None
                and self.year_review(r, now.year - 1) is not None)

    def send_review(self, r: Recipient, year: int) -> SendResult:
        """Send the year review: a gallery page in the web app, a collage everywhere else."""
        now = self.now()
        rv = self.year_review(r, year)
        if rv is None:
            return SendResult("empty", None, "too few memories that year")
        title, text = self.review_text(r, rv)
        if r.delivery == "app":
            link = self.app_link(r)
            devices = self.push_to(r, {"title": title, "body": text, "url": f"{link}/year/{year}",
                                       "image": f"{link}/year/{year}/collage.jpg", "tag": f"y{year}"})
            detail = f"{devices} device(s)"
        else:
            data = self.review_collage(r, year)
            if data is None:
                return SendResult("empty", None, "no photos for a collage")
            prepared = media.Prepared(data, f"backinframe-{year}.jpg", "image/jpeg")
            detail = ""
            try:
                if r.delivery in BUILT_IN:
                    self.ntfy.publish(r.topic, text, title=title, data=data, filename=prepared.filename)
                else:
                    ch = self.channels.get(r.delivery)
                    if ch is None:
                        return SendResult("error", None, f"{r.delivery} is not set up on this server")
                    ch.send(r.address, self.channel_message(r, title, text, prepared, "image"))
            except (NtfyError, channels.ChannelError) as exc:
                log.warning("Year review for %s failed: %s", r.name, exc)
                return SendResult("error", None, str(exc))
        self.db.set_state(f"year_review:{r.id}:{year}", now.isoformat())
        log.info("Sent the %s review to %s", year, r.name)
        return SendResult("ok", None, detail)

    # -- main loop ---------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        last_scan = last_poll = last_sync = 0.0
        import time as _time

        while not stop.is_set():
            mono = _time.monotonic()
            try:
                if self.sources and (mono - last_sync >= self.settings.sync_minutes * 60 or last_sync == 0):
                    last_sync = mono
                    self.sync_in_background()
                if mono - last_scan >= self.settings.rescan_minutes * 60 or last_scan == 0:
                    self.rescan()
                    self.prune_cache()
                    last_scan = mono
                if mono - last_poll >= self.settings.poll_seconds:
                    last_poll = mono
                    try:
                        self.poll_controls()
                    except NtfyError as exc:
                        log.warning("%s", exc)
                    self.poll_channels()
                self.tick()
            except Exception:  # keep the loop alive no matter what
                log.exception("Unexpected error in main loop")
            stop.wait(5)


def validation_message(exc: Exception) -> str:
    if isinstance(exc, (ScheduleError, ValueError)):
        return str(exc)
    return repr(exc)
