"""Walks the media folder and keeps the media table in sync."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .db import Database

log = logging.getLogger(__name__)

try:  # iPhone photos
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF = True
except ImportError:  # pragma: no cover - optional
    HEIF = False

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"} | ({".heic", ".heif"} if HEIF else set())
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".3gp", ".mkv", ".webm", ".avi", ".mts"}
SKIP_DIRS = {"@eaDir", "#recycle", ".thumbnails", "__MACOSX", "lost+found", ".Trash-1000"}

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003
DATETIME = 0x0132


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    total: int = 0


def kind_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _parse_exif_dt(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt if dt.year > 1900 else None
        except ValueError:
            continue
    return None


def image_metadata(path: Path) -> datetime | None:
    """The moment the photo was taken. Nothing else is read: no location, no camera details."""
    with Image.open(path) as img:
        exif = img.getexif()
    return _parse_exif_dt(exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL)) or _parse_exif_dt(exif.get(DATETIME))


def video_metadata(path: Path) -> datetime | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        tag = json.loads(out).get("format", {}).get("tags", {}).get("creation_time")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    if not tag:
        return None
    try:
        dt = datetime.fromisoformat(tag.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.year <= 1970:
        return None
    return dt.replace(tzinfo=None)


def iter_media(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            kind = kind_for(path)
            if kind:
                yield path, kind


def scan(db: Database, root: Path, extra_roots: dict[str, Path] | None = None) -> ScanResult:
    """Sync the media table with the photo folder and with the folders synced from other sources.

    Files in the photo folder are stored by their path in it ("Chile 2025/IMG_1.jpg"). Files from an
    extra root are stored with "@<name>/" in front ("@immich/Chile 2025/IMG_1.jpg"); the folder after
    that prefix is still the album.
    """
    roots: list[tuple[str, Path]] = [("", Path(root))]
    roots += [(f"@{name}/", Path(path)) for name, path in sorted((extra_roots or {}).items())]
    prefixes = [p for p, _ in roots if p]
    result = ScanResult()
    known = {r["path"]: r for r in db.query("SELECT id, path, size, mtime, missing FROM media")}
    seen: set[str] = set()
    absent: list[str] = []

    for prefix, base in roots:
        if not base.is_dir():
            if not prefix:
                log.warning("Media folder %s does not exist", base)
            absent.append(prefix)
            continue
        for path, kind in iter_media(base):
            rel = prefix + path.relative_to(base).as_posix()
            if not prefix and rel.startswith("@") and any(rel.startswith(p) for p in prefixes):
                continue  # a folder in the photo folder that looks like a source; never mix the two
            seen.add(rel)
            try:
                st = path.stat()
            except OSError:
                continue
            row = known.get(rel)
            if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-6:
                if row["missing"]:
                    db.execute("UPDATE media SET missing = 0 WHERE id = ?", (row["id"],))
                continue

            taken = None
            try:
                if kind == "image":
                    taken = image_metadata(path)
                else:
                    taken = video_metadata(path)
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
                log.info("Cannot read %s: %s", rel, exc)
                continue
            if taken is None:
                taken = datetime.fromtimestamp(st.st_mtime)

            values = (kind, st.st_size, st.st_mtime, taken.isoformat(timespec="seconds"))
            if row:
                db.execute(
                    # A date the admin set wins; the one in the file is kept to go back to.
                    "UPDATE media SET kind=?, size=?, mtime=?, "
                    "taken_original=CASE WHEN date_edited=1 THEN ? ELSE taken_original END, "
                    "taken_at=CASE WHEN date_edited=1 THEN taken_at ELSE ? END, missing=0, broken=0 WHERE id=?",
                    (*values[:3], values[3], values[3], row["id"]),
                )
                result.updated += 1
            else:
                folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
                db.execute(
                    "INSERT INTO media(kind, size, mtime, taken_at, path, sender) VALUES(?, ?, ?, ?, ?, "
                    "COALESCE((SELECT sender FROM albums WHERE folder = ?), ''))",
                    (*values, rel, folder),
                )
                result.added += 1

    gone = [r["id"] for p, r in known.items() if p not in seen and not r["missing"]]
    for media_id in gone:
        db.execute("UPDATE media SET missing = 1 WHERE id = ?", (media_id,))
    result.removed = len(gone)
    result.total = len(seen)
    if absent:
        log.debug("Not present: %s", [a or "photo folder" for a in absent])
    log.info("Scan done: %s", result)
    return result
