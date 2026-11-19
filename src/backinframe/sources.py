"""Photos from Immich and Nextcloud.

Both work the same way: every sync copies the chosen albums or folders into
``<data dir>/sources/<name>/<album>/`` and removes what is gone. The scanner then treats that folder
like the photo folder, with "@<name>/" in front of the paths. Nothing is ever written back to
Immich or Nextcloud: Back in Frame only needs read access.

Each copy carries the date the photo was taken, as the file's modification time, so the "on this
day" choice keeps working when the copy has no EXIF data (Immich previews have none).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as defused_fromstring

from .scanner import kind_for

log = logging.getLogger(__name__)

MANIFEST = ".backinframe-sync.json"
NAMES = ("immich", "nextcloud")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class SourceError(Exception):
    """A sync failed (unreachable, wrong key, ...). The message never contains secrets."""


@dataclass
class Remote:
    """One file on the other side."""
    key: str            # stable id there (Immich asset id, WebDAV path)
    album: str          # becomes the folder name
    name: str           # file name to store under
    version: str        # changes when the file changes (checksum, etag)
    taken: datetime | None = None
    size: int = 0


@dataclass
class SyncResult:
    downloaded: int = 0
    kept: int = 0
    removed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def safe_name(text: str, fallback: str = "untitled") -> str:
    text = _UNSAFE.sub("_", (text or "").strip()).strip(". ")
    return text[:120] or fallback


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.year > 1900 else None


class Source:
    """Base class: list what is there, fetch one file. ``sync`` does the bookkeeping."""

    name = ""

    def __init__(self, max_bytes: int, client: httpx.Client | None = None):
        self.max_bytes = max_bytes
        self.client = client or httpx.Client(timeout=60, follow_redirects=False)

    def list(self) -> list[Remote]:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch(self, item: Remote, dest: Path) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def info(self) -> dict:
        return {}

    # -- helpers -------------------------------------------------------------------------

    def _download(self, url: str, dest: Path, **kw) -> None:
        with self.client.stream("GET", url, **kw) as resp:
            self._check(resp)
            total = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise SourceError("file too large")
                    fh.write(chunk)

    def _check(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise SourceError(f"{self.name}: access refused ({resp.status_code}); check the key or password")
        if resp.status_code == 404:
            raise SourceError(f"{self.name}: not found (404); check the address, album or folder")
        if resp.status_code >= 400:
            raise SourceError(f"{self.name}: server answered {resp.status_code}")

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.name}: cannot reach the server ({type(exc).__name__})") from None
        self._check(resp)
        return resp

    # -- sync ----------------------------------------------------------------------------

    def sync(self, folder: Path) -> SyncResult:
        """Make ``folder`` hold exactly the remote files. Errors on single files are collected."""
        result = SyncResult()
        folder.mkdir(parents=True, exist_ok=True)
        manifest_path = folder / MANIFEST
        try:
            manifest: dict = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = {}
        remote = self.list()  # raises SourceError when the server is unreachable: keep everything
        wanted: dict[str, dict] = {}
        used: set[str] = set()
        for item in remote:
            if kind_for(Path(item.name)) is None:
                result.skipped += 1
                continue
            if item.size and item.size > self.max_bytes:
                result.skipped += 1
                continue
            rel = f"{safe_name(item.album)}/{safe_name(item.name)}"
            if rel.lower() in used:  # two files with the same name in one album
                stem, dot, ext = rel.rpartition(".")
                rel = f"{stem}-{item.key[-8:]}{dot}{ext}" if dot else f"{rel}-{item.key[-8:]}"
            used.add(rel.lower())
            old = manifest.get(item.key)
            target = folder / rel
            if old and old.get("version") == item.version and old.get("path") == rel and target.exists():
                wanted[item.key] = old
                result.kept += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".part-")
            os.close(fd)
            try:
                self.fetch(item, Path(tmp))
                if item.taken:
                    ts = item.taken.timestamp()
                    os.utime(tmp, (ts, ts))
                os.replace(tmp, target)
            except (SourceError, httpx.HTTPError, OSError) as exc:
                Path(tmp).unlink(missing_ok=True)
                msg = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
                result.errors.append(f"{item.album}/{item.name}: {msg}")
                if old and (folder / old["path"]).exists():
                    wanted[item.key] = old  # keep the old copy until the new one arrives
                continue
            if old and old.get("path") != rel:
                (folder / old["path"]).unlink(missing_ok=True)
            wanted[item.key] = {"path": rel, "version": item.version}
            result.downloaded += 1

        keep = {v["path"] for v in wanted.values()}
        for key, old in manifest.items():
            if key not in wanted and old.get("path") not in keep:
                (folder / old["path"]).unlink(missing_ok=True)
                result.removed += 1
        tmp_manifest = manifest_path.with_suffix(".tmp")
        tmp_manifest.write_text(json.dumps(wanted, indent=0, sort_keys=True))
        os.replace(tmp_manifest, manifest_path)
        for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()  # only empty album folders go
            except OSError:
                pass
        return result


class ImmichSource(Source):
    """Albums from an Immich server, through its API with an API key.

    The key needs: album.read, asset.read, asset.view (photos, as the 1440 px preview) and
    asset.download (videos, as the original file; they are made smaller when sent).
    """

    name = "immich"

    def __init__(self, url: str, api_key: str, albums: tuple[str, ...] = (), *, max_bytes: int,
                 client: httpx.Client | None = None):
        super().__init__(max_bytes, client)
        self.url = url.rstrip("/")
        if not self.url.endswith("/api"):
            self.url += "/api"
        self.albums = tuple(a.strip() for a in albums if a.strip())
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    def _json(self, method: str, path: str, **kw):
        resp = self._request(method, self.url + path, headers=self.headers, **kw)
        try:
            return resp.json()
        except ValueError:
            raise SourceError("immich: the answer is not JSON; is the address right?") from None

    def all_albums(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for params in ({}, {"shared": "true"}):
            for album in self._json("GET", "/albums", params=params):
                seen[album["id"]] = album
        return list(seen.values())

    def chosen_albums(self) -> list[dict]:
        albums = self.all_albums()
        if not self.albums:
            return albums
        wanted = {a.casefold() for a in self.albums}
        chosen = [a for a in albums if a.get("albumName", "").casefold() in wanted]
        missing = wanted - {a.get("albumName", "").casefold() for a in chosen}
        for name in sorted(missing):
            log.warning("Immich: no album called %r (the key may not see it)", name)
        return chosen

    def _assets(self, album: dict) -> list[dict]:
        assets: list[dict] = []
        page: int | None = 1
        while page:
            body = {"albumIds": [album["id"]], "page": page, "size": 250, "withExif": True}
            try:
                data = self._json("POST", "/search/metadata", json=body)
            except SourceError:
                if page == 1:  # an older Immich: the album lists its own assets
                    return self._json("GET", f"/albums/{album['id']}").get("assets", [])
                raise
            block = data.get("assets", {})
            assets += block.get("items", [])
            nxt = block.get("nextPage")
            page = int(nxt) if nxt else None
        if len(assets) < int(album.get("assetCount") or 0):
            # Immich 2.x leaves out what other people added to a shared album; ask the album itself
            listed = self._json("GET", f"/albums/{album['id']}").get("assets") or []
            if len(listed) > len(assets):
                return listed
        return assets

    def list(self) -> list[Remote]:
        out: list[Remote] = []
        for album in self.chosen_albums():
            for a in self._assets(album):
                if a.get("isTrashed"):
                    continue
                kind = "video" if a.get("type") == "VIDEO" else "image"
                original = a.get("originalFileName") or a["id"]
                stem = original.rsplit(".", 1)[0]
                name = f"{stem}.jpg" if kind == "image" else original
                exif = a.get("exifInfo") or {}
                taken = (_parse_time(a.get("localDateTime")) or _parse_time(exif.get("dateTimeOriginal"))
                         or _parse_time(a.get("fileCreatedAt")))
                size = int(exif.get("fileSizeInByte") or 0) if kind == "video" else 0
                version = a.get("checksum") or a.get("updatedAt") or a["id"]
                out.append(Remote(key=a["id"], album=album.get("albumName") or "Immich", name=name,
                                  version=f"{kind}:{version}", taken=taken, size=size))
        return out

    def fetch(self, item: Remote, dest: Path) -> None:
        if item.version.startswith("video:"):
            self._download(f"{self.url}/assets/{item.key}/original", dest, headers=self.headers)
        else:
            self._download(f"{self.url}/assets/{item.key}/thumbnail", dest, headers=self.headers,
                           params={"size": "preview"})

    def info(self) -> dict:
        return {"albums": ", ".join(self.albums) if self.albums else "*"}


DAV = "{DAV:}"


class NextcloudSource(Source):
    """A folder in Nextcloud (or any WebDAV server), read with an app password.

    Every subfolder becomes an album, just like in the photo folder. Files directly in the folder go
    in an album named after the folder.
    """

    name = "nextcloud"

    def __init__(self, url: str, user: str, password: str, folder: str, *, max_bytes: int,
                 client: httpx.Client | None = None, max_depth: int = 4):
        super().__init__(max_bytes, client)
        base = url.rstrip("/")
        if "/remote.php/" not in base:
            base += f"/remote.php/dav/files/{quote(user)}"
        self.base = base
        self.root = "/" + "/".join(quote(p) for p in folder.strip("/").split("/") if p)
        self.folder_name = folder.strip("/").rsplit("/", 1)[-1] or "Nextcloud"
        self.auth = (user, password)
        self.max_depth = max_depth

    def _propfind(self, path: str) -> list[dict]:
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/>'
                "<d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>")
        resp = self._request("PROPFIND", self.base + path, auth=self.auth, content=body,
                             headers={"Depth": "1", "Content-Type": "application/xml"})
        try:
            tree = defused_fromstring(resp.content)
        except (ParseError, DefusedXmlException):
            raise SourceError("nextcloud: the answer is not WebDAV; is the address right?") from None
        prefix = urlsplit(self.base).path
        entries = []
        for r in tree.findall(f"{DAV}response"):
            href = unquote(urlsplit(r.findtext(f"{DAV}href") or "").path)
            if not href.startswith(prefix):
                continue
            rel = href[len(prefix):]
            prop = r.find(f"{DAV}propstat/{DAV}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{DAV}resourcetype/{DAV}collection") is not None
            entries.append({
                "path": rel.rstrip("/"), "dir": is_dir,
                "etag": (prop.findtext(f"{DAV}getetag") or "").strip('"'),
                "size": int(prop.findtext(f"{DAV}getcontentlength") or 0),
            })
        return entries

    def list(self) -> list[Remote]:
        out: list[Remote] = []
        root_plain = unquote(self.root).rstrip("/")

        def walk(path: str, depth: int):
            for e in self._propfind(path):
                if e["path"] == unquote(path).rstrip("/"):
                    continue  # the folder itself
                name = e["path"].rsplit("/", 1)[-1]
                if name.startswith("."):
                    continue
                if e["dir"]:
                    if depth < self.max_depth:
                        walk("/".join(quote(p) for p in e["path"].split("/")), depth + 1)
                    continue
                inside = e["path"][len(root_plain):].strip("/")
                album = inside.rsplit("/", 1)[0] if "/" in inside else self.folder_name
                out.append(Remote(key=e["path"], album=album, name=name, version=e["etag"] or str(e["size"]),
                                  size=e["size"]))

        walk(self.root, 0)
        return out

    def fetch(self, item: Remote, dest: Path) -> None:
        url = self.base + "/".join(quote(p) for p in item.key.split("/"))
        self._download(url, dest, auth=self.auth)

    def info(self) -> dict:
        return {"folder": unquote(self.root)}


def build_sources(settings, client: httpx.Client | None = None) -> dict[str, Source]:
    max_bytes = int(max(settings.video_max_mb * 4, 200) * 1024 * 1024)
    sources: dict[str, Source] = {}
    if settings.immich_enabled:
        sources["immich"] = ImmichSource(settings.immich_url, settings.immich_api_key, settings.immich_albums,
                                         max_bytes=max_bytes, client=client)
    if settings.nextcloud_enabled:
        sources["nextcloud"] = NextcloudSource(settings.nextcloud_url, settings.nextcloud_user,
                                               settings.nextcloud_password, settings.nextcloud_folder,
                                               max_bytes=max_bytes, client=client)
    return sources
