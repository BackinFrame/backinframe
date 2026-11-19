"""Upload links: someone without access to the server (a brother, a friend) puts photos in one album.

A link is /u/<token> on the web app port. It can only add photos to its own album, under
<data>/uploads/<album>/; it cannot list, open or delete anything. Every file is checked (a real
photo or video, not too big), the name is made safe, and nothing is ever overwritten. The person's
name on the link becomes the "from" of the photos.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - ffprobe with a fixed argument list
import unicodedata
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .scanner import IMAGE_EXT, VIDEO_EXT, kind_for

MAX_FILES_PER_POST = 50
MIN_FREE_BYTES = 1 << 30  # keep 1 GB free on the data disk
NAME_MAX = 60
ALBUM_MAX = 80


class UploadError(Exception):
    pass


def clean_text(text: str, limit: int) -> str:
    """One line without control characters."""
    return " ".join("".join(c if c.isprintable() else " " for c in text or "").split())[:limit]


def album_folder(name: str) -> str:
    """A folder name for an album: no slashes or dots in front, nothing a file system dislikes."""
    name = clean_text(unicodedata.normalize("NFC", name), ALBUM_MAX)
    name = re.sub(r'[/\\:*?"<>|]', " ", name).strip(" .")
    name = " ".join(name.split())
    if not name or name.startswith("@"):
        raise UploadError("album")
    return name


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    if ext not in IMAGE_EXT | VIDEO_EXT:
        raise UploadError("type")
    stem = re.sub(r"[^\w.-]+", "_", unicodedata.normalize("NFKD", stem)).strip("._") or "photo"
    return stem[:80] + ext


def unique_path(folder: Path, filename: str) -> Path:
    path = folder / filename
    n = 1
    while path.exists():
        path = folder / f"{Path(filename).stem}-{n}{Path(filename).suffix}"
        n += 1
    return path


def check_media(path: Path, kind: str | None = None) -> bool:
    """Is this really a photo or a video we can read?"""
    kind = kind or kind_for(path)
    if kind == "image":
        try:
            with Image.open(path) as img:
                if img.width * img.height > 100_000_000:
                    return False
                img.verify()
            return True
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            return False
    if kind == "video":
        if not shutil.which("ffprobe"):
            return True
        try:
            out = subprocess.run(  # nosec B603 B607
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=60, check=True).stdout
            info = json.loads(out)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            return False
        return any(s.get("codec_type") == "video" for s in info.get("streams", []))
    return False


def store(folder: Path, filename: str, stream, max_bytes: int) -> Path:
    """Copy one upload into the album folder, or raise UploadError ('type', 'size', 'space', 'broken')."""
    target_name = safe_filename(filename)
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < MIN_FREE_BYTES:
        raise UploadError("space")
    path = unique_path(folder, target_name)
    tmp = path.with_name("." + path.name + ".part")
    written = 0
    try:
        with open(tmp, "xb") as out:
            while chunk := stream.read(1 << 20):
                written += len(chunk)
                if written > max_bytes:
                    raise UploadError("size")
                out.write(chunk)
        if written == 0 or not check_media(tmp, kind_for(path)):
            raise UploadError("broken")
        tmp.rename(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
