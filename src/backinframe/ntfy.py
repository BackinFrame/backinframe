"""Minimal ntfy client: publish (with attachment and buttons), poll, ask limits."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlparse

import httpx

log = logging.getLogger(__name__)


class NtfyError(Exception):
    pass


@dataclass(frozen=True)
class HttpAction:
    """A notification button that POSTs ``body`` to ``url`` (done by the phone)."""

    label: str
    url: str
    body: str
    clear: bool = True

    def render(self) -> str:
        for part in (self.label, self.url, self.body):
            if "," in part or ";" in part:
                raise ValueError("action fields may not contain ',' or ';'")
        return f"http, {self.label}, {self.url}, method=POST, body={self.body}, clear={str(self.clear).lower()}"


def subscribe_link(public_url: str, topic: str, display: str = "") -> str:
    """An ``ntfy://`` deep link: on Android, scanning it opens the ntfy app and subscribes to the
    topic on the right server in one go (https://docs.ntfy.sh/subscribe/phone/#ntfy-links)."""
    parsed = urlparse(public_url.rstrip("/"))
    params = {}
    if display:
        params["display"] = display
    if parsed.scheme == "http":
        params["secure"] = "false"
    query = ("?" + urlencode(params, quote_via=quote)) if params else ""
    return f"ntfy://{parsed.netloc}{parsed.path}/{topic}{query}"


class NtfyClient:
    def __init__(self, base_url: str, token: str = "", client: httpx.Client | None = None, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        headers = {"User-Agent": "backinframe"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = headers

    def topic_url(self, topic: str) -> str:
        return f"{self.base_url}/{topic}"

    def publish(
        self,
        topic: str,
        message: str,
        *,
        title: str = "",
        data: bytes | None = None,
        filename: str = "",
        actions: list[HttpAction] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        # Query parameters are UTF-8 safe, unlike HTTP headers.
        params = {"message": message}
        if title:
            params["title"] = title
        if filename:
            params["filename"] = filename
        if actions:
            params["actions"] = "; ".join(a.render() for a in actions)
        if tags:
            params["tags"] = ",".join(tags)
        try:
            resp = self._client.put(
                self.topic_url(topic), params=params, content=data if data is not None else b"",
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise NtfyError(f"cannot reach ntfy: {exc}") from exc
        if resp.status_code == 413:
            raise NtfyError("attachment too large for this ntfy server")
        if resp.status_code >= 400:
            raise NtfyError(f"ntfy said {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def attachment_limit(self) -> int | None:
        """Max attachment size in bytes as reported by the server, if available."""
        try:
            resp = self._client.get(f"{self.base_url}/v1/account", headers=self._headers)
            resp.raise_for_status()
            limit = resp.json().get("limits", {}).get("attachment_file_size")
        except (httpx.HTTPError, ValueError) as exc:
            log.info("Could not ask ntfy for limits: %s", exc)
            return None
        return int(limit) if limit else None

    def poll(self, topics: list[str], since: str) -> list[dict]:
        """Return cached messages on ``topics`` since a unix time / message id / 'all'."""
        if not topics:
            return []
        url = f"{self.base_url}/{','.join(topics)}/json"
        try:
            resp = self._client.get(url, params={"poll": "1", "since": since}, headers=self._headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NtfyError(f"poll failed: {exc}") from exc
        messages = []
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "message":
                messages.append(msg)
        return messages

    def close(self) -> None:
        self._client.close()
