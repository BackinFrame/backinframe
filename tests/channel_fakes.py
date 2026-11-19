"""In-memory stand-ins for the Telegram Bot API, signal-cli-rest-api, a Matrix homeserver and Discord
webhooks, served through an httpx.MockTransport. They answer like the real services do, including
their error replies, and record what Back in Frame sent so tests can check it."""

from __future__ import annotations

import email
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import httpx

TELEGRAM_TOKEN = "123456:TEST-token"
BLOCKED_CHAT = "666"  # a chat whose user blocked the bot
UNREGISTERED = "+31600000000"  # not on Signal
GONE_ROOM = "!gone:matrix.test"
DEAD_WEBHOOK = "https://discord.com/api/webhooks/11111/" + "d" * 40


def form(request: httpx.Request) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """Fields and files of a urlencoded or multipart body."""
    ctype = request.headers.get("content-type", "")
    body = request.content
    if ctype.startswith("application/x-www-form-urlencoded"):
        return {k: v[0] for k, v in parse_qs(body.decode()).items()}, {}
    if not ctype.startswith("multipart/form-data"):
        return {}, {}
    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    fields, files = {}, {}
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        data = part.get_payload(decode=True)
        if filename is not None:
            files[name] = (filename, data, part.get_content_type())
        else:
            fields[name] = data.decode()
    return fields, files


@dataclass
class Services:
    telegram: list[dict] = field(default_factory=list)  # {method, fields, files}
    telegram_updates: list[dict] = field(default_factory=list)
    signal: list[dict] = field(default_factory=list)
    signal_inbox: list[dict] = field(default_factory=list)
    matrix: list[dict] = field(default_factory=list)  # {kind: upload|send, room, content}
    discord: list[dict] = field(default_factory=list)
    matrix_joined: set = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)
    _update_id: int = 100

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    # -- helpers for tests ---------------------------------------------------
    def press(self, chat: str, data: str) -> None:
        """A Telegram user taps an inline button."""
        self._update_id += 1
        self.telegram_updates.append({"update_id": self._update_id, "callback_query": {
            "id": f"cq{self._update_id}", "data": data, "message": {"chat": {"id": int(chat)}}}})

    def say_telegram(self, chat: str, text: str) -> None:
        self._update_id += 1
        self.telegram_updates.append({"update_id": self._update_id,
                                      "message": {"chat": {"id": int(chat)}, "text": text}})

    def say_signal(self, number: str, text: str) -> None:
        self.signal_inbox.append({"envelope": {"sourceNumber": number, "dataMessage": {"message": text}}})

    def sent(self, method: str) -> list[dict]:
        return [m for m in self.telegram if m["method"] == method]

    # -- the services ---------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "api.telegram.test":
            return self._telegram(request)
        if url.host == "signal.test":
            return self._signal(request)
        if url.host == "matrix.test":
            return self._matrix(request)
        if url.host == "discord.com":
            return self._discord(request)
        return httpx.Response(599, text="no such service")

    def _telegram(self, request: httpx.Request) -> httpx.Response:
        prefix, _, method = request.url.path.rpartition("/")
        if prefix != f"/bot{TELEGRAM_TOKEN}":
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        fields, files = form(request)
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True,
                                                                    "username": "BackInFrameTestBot"}})
        if method == "getUpdates":
            offset = int(fields.get("offset", 0))
            result = [u for u in self.telegram_updates if u["update_id"] >= offset]
            return httpx.Response(200, json={"ok": True, "result": result})
        self.telegram.append({"method": method, "fields": fields, "files": files})
        if fields.get("chat_id") == BLOCKED_CHAT:
            return httpx.Response(403, json={"ok": False, "error_code": 403,
                                             "description": "Forbidden: bot was blocked by the user"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.telegram)}})

    def _signal(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/send":
            body = json.loads(request.content)
            self.signal.append(body)
            if UNREGISTERED in body["recipients"]:
                return httpx.Response(400, json={"error": f"Failed to send message: Unregistered user {UNREGISTERED}"})
            return httpx.Response(201, json={"timestamp": "1"})
        if request.method == "GET" and request.url.path.startswith("/v1/receive/"):
            inbox, self.signal_inbox = self.signal_inbox, []
            return httpx.Response(200, json=inbox)
        return httpx.Response(404, json={"error": "not found"})

    def _matrix(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer syt_test":
            return httpx.Response(401, json={"errcode": "M_UNKNOWN_TOKEN", "error": "Invalid token"})
        path = request.url.path
        if path == "/_matrix/client/v3/account/whoami":
            return httpx.Response(200, json={"user_id": "@memories:matrix.test"})
        if path == "/_matrix/media/v3/upload":
            self.matrix.append({"kind": "upload", "bytes": request.content,
                                "type": request.headers.get("content-type"),
                                "filename": parse_qs(urlparse(str(request.url)).query).get("filename", [""])[0]})
            return httpx.Response(200, json={"content_uri": f"mxc://matrix.test/m{len(self.matrix)}"})
        if path.startswith("/_matrix/client/v3/join/"):
            room = unquote(path.rsplit("/", 1)[1])
            if room == GONE_ROOM:
                return httpx.Response(403, json={"errcode": "M_FORBIDDEN", "error": "You are not invited"})
            self.matrix_joined.add(room)
            return httpx.Response(200, json={"room_id": room})
        if path.startswith("/_matrix/client/v3/rooms/") and request.method == "PUT":
            room = unquote(path.split("/")[5])
            if room == GONE_ROOM:
                return httpx.Response(403, json={"errcode": "M_FORBIDDEN", "error": "not in room"})
            self.matrix.append({"kind": "send", "room": room, "content": json.loads(request.content)})
            return httpx.Response(200, json={"event_id": f"$e{len(self.matrix)}"})
        return httpx.Response(404, json={"errcode": "M_UNRECOGNIZED"})

    def _discord(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEAD_WEBHOOK:
            return httpx.Response(404, json={"message": "Unknown Webhook", "code": 10015})
        fields, files = form(request)
        self.discord.append({"url": str(request.url), "payload": json.loads(fields["payload_json"]),
                             "files": files})
        return httpx.Response(204)


def webhook(n: int = 1) -> str:
    return f"https://discord.com/api/webhooks/{12345 + n}/" + "A" * 30 + str(n)
