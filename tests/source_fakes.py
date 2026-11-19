"""In-memory Immich and Nextcloud servers for the source tests, behind one httpx mock transport."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit

import httpx
from PIL import Image

IMMICH = "https://immich.example.org"
IMMICH_KEY = "immich-secret-key"
NEXTCLOUD = "https://cloud.example.org"
NC_USER = "anna"
NC_PASSWORD = "app-pass-word"


def jpeg(color="orange", exif_date: datetime | None = None, size=(320, 240)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_date:
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = exif_date.strftime("%Y:%m:%d %H:%M:%S")
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


@dataclass
class Asset:
    id: str
    name: str
    taken: datetime
    kind: str = "IMAGE"
    checksum: str = "c1"
    body: bytes = b""
    owner: str = "me"
    size: int = 0


@dataclass
class FakeImmich:
    """Immich 2.x/3.x: /albums, /search/metadata with albumIds, /assets/{id}/thumbnail and /original."""
    albums: dict[str, dict] = field(default_factory=dict)  # id -> {name, assets: [Asset], shared}
    old_api: bool = False  # before /search/metadata knew albumIds
    down: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, album_id: str, name: str, *assets: Asset, shared: bool = False) -> None:
        self.albums[album_id] = {"name": name, "assets": list(assets), "shared": shared}

    def _asset_json(self, a: Asset) -> dict:
        return {"id": a.id, "type": a.kind, "originalFileName": a.name, "checksum": a.checksum,
                "localDateTime": a.taken.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "fileCreatedAt": a.taken.isoformat() + "Z", "isTrashed": False,
                "exifInfo": {"fileSizeInByte": a.size or len(a.body)}}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down")
        if request.headers.get("x-api-key") != IMMICH_KEY:
            return httpx.Response(401, json={"message": "Invalid API key"})
        path = request.url.path
        if path == "/api/albums" and request.method == "GET":
            shared = request.url.params.get("shared") == "true"
            out = [{"id": i, "albumName": a["name"], "assetCount": len(a["assets"])}
                   for i, a in self.albums.items() if a["shared"] == shared or not shared]
            return httpx.Response(200, json=out)
        if path.startswith("/api/albums/") and request.method == "GET":
            a = self.albums.get(path.rsplit("/", 1)[1])
            if a is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"albumName": a["name"],
                                             "assets": [self._asset_json(x) for x in a["assets"]]})
        if path == "/api/search/metadata" and request.method == "POST":
            if self.old_api:
                return httpx.Response(400, json={"message": "property albumIds should not exist"})
            body = json.loads(request.content)
            album = self.albums[body["albumIds"][0]]
            mine = [x for x in album["assets"] if x.owner == "me"]  # 2.x: only the caller's own assets
            page, size = body.get("page", 1), body.get("size", 250)
            chunk = mine[(page - 1) * size: page * size]
            nxt = str(page + 1) if page * size < len(mine) else None
            return httpx.Response(200, json={"assets": {"items": [self._asset_json(x) for x in chunk],
                                                        "nextPage": nxt}})
        if path.startswith("/api/assets/"):
            _, _, _, asset_id, what = path.split("/")
            asset = next((x for al in self.albums.values() for x in al["assets"] if x.id == asset_id), None)
            if asset is None:
                return httpx.Response(404)
            if what == "thumbnail" and request.url.params.get("size") == "preview":
                return httpx.Response(200, content=asset.body, headers={"content-type": "image/jpeg"})
            if what == "original":
                return httpx.Response(200, content=asset.body)
        return httpx.Response(404)


@dataclass
class FakeNextcloud:
    """WebDAV under /remote.php/dav/files/<user>/ with Depth: 1 PROPFIND and GET."""
    files: dict[str, bytes] = field(default_factory=dict)  # "Photos/Chile 2025/a.jpg" -> bytes
    etags: dict[str, str] = field(default_factory=dict)
    fail_get: set = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)

    def put(self, path: str, body: bytes, etag: str = "e1") -> None:
        self.files[path] = body
        self.etags[path] = etag

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = httpx.BasicAuth(NC_USER, NC_PASSWORD)
        expected = next(auth.auth_flow(httpx.Request("GET", "https://x"))).headers["Authorization"]
        if request.headers.get("authorization") != expected:
            return httpx.Response(401)
        prefix = f"/remote.php/dav/files/{NC_USER}/"
        raw = urlsplit(str(request.url)).path
        if not raw.startswith(prefix):
            return httpx.Response(404)
        rel = unquote(raw[len(prefix):]).strip("/")
        if request.method == "GET":
            if rel in self.fail_get:
                return httpx.Response(500)
            if rel in self.files:
                return httpx.Response(200, content=self.files[rel])
            return httpx.Response(404)
        if request.method == "PROPFIND":
            assert request.headers.get("Depth") == "1"
            dirs = {"/".join(p.split("/")[:i]) for p in self.files for i in range(1, p.count("/") + 1)}
            if rel not in dirs:
                return httpx.Response(404)
            entries = [(rel, True, 0, "")]
            for d in sorted(dirs):
                if d.rsplit("/", 1)[0] == rel and "/" in d and d != rel:
                    entries.append((d, True, 0, ""))
            for p, body in sorted(self.files.items()):
                if p.rsplit("/", 1)[0] == rel:
                    entries.append((p, False, len(body), self.etags[p]))
            parts = []
            for p, is_dir, size, etag in entries:
                href = prefix + quote(p) + ("/" if is_dir else "")
                rtype = "<d:resourcetype><d:collection/></d:resourcetype>" if is_dir else "<d:resourcetype/>"
                extra = "" if is_dir else (f"<d:getcontentlength>{size}</d:getcontentlength>"
                                           f"<d:getetag>&quot;{etag}&quot;</d:getetag>")
                parts.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{rtype}{extra}"
                             "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
            xml = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>"
            return httpx.Response(207, content=xml.encode(), headers={"content-type": "application/xml"})
        return httpx.Response(405)


@dataclass
class Remotes:
    immich: FakeImmich = field(default_factory=FakeImmich)
    nextcloud: FakeNextcloud = field(default_factory=FakeNextcloud)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == urlsplit(IMMICH).hostname:
            return self.immich.handle(request)
        if host == urlsplit(NEXTCLOUD).hostname:
            return self.nextcloud.handle(request)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))
