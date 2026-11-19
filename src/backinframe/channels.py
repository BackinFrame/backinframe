"""Extra delivery channels: e-mail, Telegram, Signal, Matrix and Discord.

The web app and ntfy are built into the service; the channels here all work the same way: one
"address" per recipient (an e-mail address, a Telegram chat ID, a phone number, a room ID or a
webhook URL), :meth:`Channel.send` to deliver a photo or a short text, and for the channels that
can talk back (Telegram buttons, Signal and Telegram text replies) :meth:`Channel.poll` to pick
up commands. The hosted version uses the same classes with its own settings.

Every channel only sends what Back in Frame already prepared: the resized photo without
location, the album name and the date. Nothing else about the sender or the recipient.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import secrets
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger(__name__)

MB = 1024 * 1024
COMMANDS = ("more", "less", "pause", "resume")


class ChannelError(Exception):
    """Sending failed; worth trying again later."""


class ChannelGone(ChannelError):
    """The address does not work any more (bot blocked, webhook deleted, room left)."""


@dataclass
class Message:
    title: str
    text: str = ""
    data: bytes | None = None
    filename: str = ""
    kind: str = ""  # 'image' | 'video' when there is a file
    actions: list[tuple[str, str]] = field(default_factory=list)  # (label, command) buttons
    ref: str = ""  # who the buttons are for (the recipient id); checked against the sender of a press
    hint: str = ""  # one line for channels that read text replies ("Reply more, less or pause")
    footer: str = ""  # e-mail only: why you get this

    @property
    def mime(self) -> str:
        return "video/mp4" if self.kind == "video" else "image/jpeg"

    @property
    def caption(self) -> str:
        return "\n".join(p for p in (self.title, self.text) if p)


@dataclass
class Incoming:
    """Something a recipient sent back: a button press (command + ref) or a text."""

    address: str
    text: str = ""
    command: str = ""
    ref: str = ""
    reply: Callable[[str], None] | None = None

    def answer(self, text: str) -> None:
        if self.reply is not None and text:
            try:
                self.reply(text)
            except ChannelError as exc:
                log.info("could not answer: %s", exc)


class Channel:
    name = ""
    buttons = False  # shows More / Less / Pause buttons
    replies = False  # reads text replies like "more"
    image_limit = 8 * MB
    video_limit = 50 * MB

    def check_address(self, address: str) -> str:
        """The address in its canonical form; ValueError when it cannot be right."""
        raise NotImplementedError

    def send(self, address: str, message: Message) -> None:
        raise NotImplementedError

    def poll(self) -> list[Incoming]:
        return []

    def info(self) -> dict[str, str]:
        """Details for the admin page, like the bot's name. Never raises."""
        return {}


def _http(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=60, headers={"User-Agent": "backinframe"})


# -- e-mail ---------------------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


class EmailChannel(Channel):
    name = "email"

    def __init__(self, host: str, port: int = 587, user: str = "", password: str = "", sender: str = "",
                 security: str = "starttls", max_mb: float = 15, smtp_factory: Callable | None = None):
        if security not in ("starttls", "ssl", "none"):
            raise ValueError("security must be starttls, ssl or none")
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender = sender
        self.security = security
        self.image_limit = self.video_limit = int(max_mb * MB)
        self._factory = smtp_factory

    def check_address(self, address: str) -> str:
        address = (address or "").strip()
        if not EMAIL_RE.match(address) or ".." in address:
            raise ValueError("not a valid e-mail address")
        local, _, domain = address.rpartition("@")
        return f"{local}@{domain.lower()}"

    def build(self, address: str, message: Message) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = address
        msg["Subject"] = message.title + (f" · {message.text}" if message.text else "")
        msg["Auto-Submitted"] = "auto-generated"
        plain = "\n\n".join(p for p in (message.caption, message.footer) if p)
        msg.set_content(plain)
        cid = make_msgid(domain="backinframe")
        img = (f'<p><img src="cid:{cid[1:-1]}" alt="" style="max-width:100%;border-radius:12px"></p>'
               if message.data and message.kind == "image" else "")
        body = (f'<div style="font-family:system-ui,sans-serif;max-width:640px">'
                f"<h2 style=\"margin:0 0 4px\">{html.escape(message.title)}</h2>"
                f'<p style="margin:0 0 12px;color:#555">{html.escape(message.text)}</p>{img}'
                f'<p style="color:#888;font-size:13px">{html.escape(message.footer)}</p></div>')
        msg.add_alternative(body, subtype="html")
        if message.data and message.kind == "image":
            msg.get_payload()[1].add_related(message.data, "image", "jpeg", cid=cid, filename=message.filename)
        elif message.data:
            msg.add_attachment(message.data, maintype="video", subtype="mp4", filename=message.filename)
        return msg

    def send(self, address: str, message: Message) -> None:
        msg = self.build(address, message)
        try:
            if self._factory is not None:
                smtp = self._factory(self.host, self.port)
            elif self.security == "ssl":
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=60, context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=60)
            with smtp:
                if self.security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise ChannelGone(f"e-mail address refused: {exc}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise ChannelError(f"e-mail failed: {exc}") from exc


# -- Telegram -------------------------------------------------------------------------------------
class TelegramChannel(Channel):
    """A Telegram bot. Recipients start a chat with the bot; the bot tells them their chat ID."""

    name = "telegram"
    buttons = True
    replies = True
    image_limit = 10 * MB  # sendPhoto
    video_limit = 50 * MB  # bots can upload up to 50 MB

    def __init__(self, token: str, api_url: str = "https://api.telegram.org", client: httpx.Client | None = None):
        self.base = f"{api_url.rstrip('/')}/bot{token}"
        self.client = _http(client)
        self.offset = 0  # next update to fetch; the service stores it
        self._me: dict | None = None

    def _call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict | list:
        try:
            resp = self.client.post(f"{self.base}/{method}", data=data, files=files)
        except httpx.HTTPError as exc:
            raise ChannelError(f"telegram: {exc.__class__.__name__}") from None  # the URL holds the token
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200 and body.get("ok"):
            return body["result"]
        detail = body.get("description") or f"HTTP {resp.status_code}"
        if resp.status_code == 403 or "chat not found" in detail.lower():
            raise ChannelGone(f"telegram: {detail}")
        raise ChannelError(f"telegram: {detail}")

    def check_address(self, address: str) -> str:
        address = (address or "").strip()
        if not re.fullmatch(r"-?\d{1,20}", address):
            raise ValueError("a Telegram chat ID is a number (the bot tells it after /start)")
        return address

    def keyboard(self, message: Message) -> str:
        row = [{"text": label, "callback_data": f"{cmd}:{message.ref}"} for label, cmd in message.actions]
        return json.dumps({"inline_keyboard": [row]})

    def send(self, address: str, message: Message) -> None:
        text = f"<b>{html.escape(message.title)}</b>" + (f"\n{html.escape(message.text)}" if message.text else "")
        data = {"chat_id": address, "parse_mode": "HTML"}
        if message.actions and message.ref:
            data["reply_markup"] = self.keyboard(message)
        if message.data:
            method, field_name = ("sendVideo", "video") if message.kind == "video" else ("sendPhoto", "photo")
            data["caption"] = text
            self._call(method, data, {field_name: (message.filename or "photo.jpg", message.data, message.mime)})
        else:
            data["text"] = text
            self._call("sendMessage", data)

    def say(self, chat_id: str, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def poll(self) -> list[Incoming]:
        updates = self._call("getUpdates", {"offset": self.offset, "timeout": 0,
                                            "allowed_updates": json.dumps(["message", "callback_query"])})
        out: list[Incoming] = []
        for update in updates:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
            cq = update.get("callback_query")
            if cq:
                chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
                command, _, ref = str(cq.get("data", "")).partition(":")
                qid = cq.get("id")
                out.append(Incoming(chat, command=command, ref=ref, reply=lambda t, q=qid: self._call(
                    "answerCallbackQuery", {"callback_query_id": q, "text": t[:190]})))
                continue
            msg = update.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat and msg.get("text"):
                out.append(Incoming(chat, text=msg["text"], reply=lambda t, c=chat: self.say(c, t)))
        return out

    def info(self) -> dict[str, str]:
        if self._me is None:
            try:
                self._me = self._call("getMe")
            except ChannelError as exc:
                log.warning("%s", exc)
                return {}
        return {"bot": "@" + str(self._me.get("username", ""))}


# -- Signal (through signal-cli-rest-api) -------------------------------------------------------
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class SignalChannel(Channel):
    """Signal through https://github.com/bbernhard/signal-cli-rest-api (MODE=native or normal)."""

    name = "signal"
    replies = True
    image_limit = 8 * MB
    video_limit = 95 * MB

    def __init__(self, url: str, number: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.number = number
        self.client = _http(client)

    def check_address(self, address: str) -> str:
        address = (address or "").strip()
        if address.startswith("group."):
            if re.fullmatch(r"group\.[A-Za-z0-9+/=_-]{8,200}", address):
                return address
            raise ValueError("not a valid Signal group ID")
        address = re.sub(r"[\s().-]", "", address)
        if address.startswith("00"):
            address = "+" + address[2:]
        if PHONE_RE.match(address):
            return address
        raise ValueError("use a phone number with country code, like +31612345678, or a group ID")

    def send(self, address: str, message: Message) -> None:
        text = "\n\n".join(p for p in (message.caption, message.hint) if p)
        body: dict = {"message": text, "number": self.number, "recipients": [address]}
        if message.data:
            name = (message.filename or "photo.jpg").replace(";", "_")
            b64 = base64.b64encode(message.data).decode()
            body["base64_attachments"] = [f"data:{message.mime};filename={name};base64,{b64}"]
        try:
            resp = self.client.post(f"{self.url}/v2/send", json=body)
        except httpx.HTTPError as exc:
            raise ChannelError(f"signal: {exc}") from exc
        if resp.status_code not in (200, 201):
            detail = resp.text[:200]
            if "Unregistered user" in detail or "not a member" in detail:
                raise ChannelGone(f"signal: {detail}")
            raise ChannelError(f"signal: HTTP {resp.status_code} {detail}")

    def poll(self) -> list[Incoming]:
        try:
            resp = self.client.get(f"{self.url}/v1/receive/{quote(self.number)}")
        except httpx.HTTPError as exc:
            raise ChannelError(f"signal: {exc}") from exc
        if resp.status_code != 200:
            raise ChannelError(f"signal: HTTP {resp.status_code}")
        out = []
        for item in resp.json() or []:
            env = item.get("envelope") or {}
            data = env.get("dataMessage") or {}
            source = env.get("sourceNumber") or env.get("source") or ""
            if source and data.get("message") and not data.get("groupInfo"):
                out.append(Incoming(source, text=data["message"],
                                    reply=lambda t, s=source: self.send(s, Message(title=t))))
        return out

    def info(self) -> dict[str, str]:
        return {"number": self.number}


# -- Matrix ---------------------------------------------------------------------------------------
class MatrixChannel(Channel):
    """A Matrix account (a bot user) that posts into rooms it was invited to."""

    name = "matrix"
    image_limit = 8 * MB
    video_limit = 45 * MB  # many homeservers allow 50 MB uploads

    def __init__(self, homeserver: str, token: str, client: httpx.Client | None = None):
        self.hs = homeserver.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = _http(client)
        self._me: str | None = None
        self._joined: set[str] = set()

    def check_address(self, address: str) -> str:
        address = (address or "").strip()
        if not re.fullmatch(r"![A-Za-z0-9._=+/-]{4,200}:[A-Za-z0-9.-]+(:\d+)?", address):
            raise ValueError("a Matrix room ID looks like !abcdef:example.org (room settings → Advanced)")
        return address

    def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = self.client.request(method, self.hs + path, headers={**self.headers, **kw.pop("headers", {})},
                                       **kw)
        except httpx.HTTPError as exc:
            raise ChannelError(f"matrix: {exc}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200:
            return body
        detail = body.get("error") or f"HTTP {resp.status_code}"
        if body.get("errcode") in ("M_FORBIDDEN", "M_NOT_FOUND"):
            raise ChannelGone(f"matrix: {detail}")
        raise ChannelError(f"matrix: {detail}")

    def _post(self, room: str, content: dict) -> None:
        txn = secrets.token_hex(8)
        self._request("PUT", f"/_matrix/client/v3/rooms/{quote(room, safe='')}/send/m.room.message/{txn}",
                      json=content)

    def join(self, room: str) -> None:
        """Accept the invitation (a no-op when already in the room), once per room."""
        if room not in self._joined:
            self._request("POST", f"/_matrix/client/v3/join/{quote(room, safe='')}", json={})
            self._joined.add(room)

    def send(self, address: str, message: Message) -> None:
        self.join(address)
        text = {"msgtype": "m.text", "body": message.caption, "format": "org.matrix.custom.html",
                "formatted_body": f"<b>{html.escape(message.title)}</b>"
                                  + (f"<br>{html.escape(message.text)}" if message.text else "")}
        self._post(address, text)
        if message.data:
            name = message.filename or "photo.jpg"
            up = self._request("POST", f"/_matrix/media/v3/upload?filename={quote(name)}", content=message.data,
                               headers={"Content-Type": message.mime})
            self._post(address, {"msgtype": "m.video" if message.kind == "video" else "m.image", "body": name,
                                 "url": up["content_uri"], "info": {"mimetype": message.mime,
                                                                    "size": len(message.data)}})

    def info(self) -> dict[str, str]:
        if self._me is None:
            try:
                self._me = self._request("GET", "/_matrix/client/v3/account/whoami").get("user_id", "")
            except ChannelError as exc:
                log.warning("%s", exc)
                return {}
        return {"user": self._me}


# -- Discord --------------------------------------------------------------------------------------
DISCORD_HOSTS = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}


class DiscordChannel(Channel):
    """A channel webhook: the recipient (or you) makes one in Discord and gives you its URL."""

    name = "discord"
    image_limit = video_limit = 9 * MB  # webhooks take 10 MB per message

    def __init__(self, client: httpx.Client | None = None):
        self.client = _http(client)

    def check_address(self, address: str) -> str:
        address = (address or "").strip()
        u = urlparse(address)
        if (u.scheme != "https" or u.hostname not in DISCORD_HOSTS or u.port or u.username
                or not re.fullmatch(r"/api/webhooks/\d{5,25}/[A-Za-z0-9_-]{20,120}", u.path)):
            raise ValueError("use a Discord webhook URL: https://discord.com/api/webhooks/…")
        return f"https://discord.com{u.path}"

    def send(self, address: str, message: Message) -> None:
        url = self.check_address(address)  # never post anywhere but Discord
        content = f"**{message.title}**" + (f"\n{message.text}" if message.text else "")
        payload = json.dumps({"content": content[:2000], "allowed_mentions": {"parse": []}})
        files = None
        if message.data:
            files = {"files[0]": (message.filename or "photo.jpg", message.data, message.mime)}
        try:
            resp = self.client.post(url, data={"payload_json": payload}, files=files)
        except httpx.HTTPError as exc:
            raise ChannelError(f"discord: {exc.__class__.__name__}") from None  # the URL is a secret
        if resp.status_code in (200, 204):
            return
        if resp.status_code in (401, 404):
            raise ChannelGone("discord: the webhook no longer exists")
        raise ChannelError(f"discord: HTTP {resp.status_code}")
