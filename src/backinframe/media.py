"""Turns a file from the collection into something small enough to send."""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from . import scanner  # noqa: F401  (registers the HEIF opener)

log = logging.getLogger(__name__)

PASSTHROUGH_VIDEO = {".mp4", ".m4v", ".mov"}
MIN_VIDEO_KBPS = 250
AUDIO_KBPS = 96


# A decompression bomb (a tiny file that unpacks to billions of pixels) is skipped, not decoded.
# 100 megapixels is larger than any phone or common camera photo.
MAX_PIXELS = 100_000_000


class SkipMedia(Exception):
    """This item cannot be sent within the limits; try another one."""


@dataclass
class Prepared:
    data: bytes
    filename: str
    content_type: str


def date_exif(taken_at: datetime | None) -> bytes | None:
    """EXIF holding only the capture date: no location, camera or other details."""
    if taken_at is None:
        return None
    stamp = taken_at.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()
    exif[0x0132] = stamp  # DateTime
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = stamp  # DateTimeOriginal
    sub[0x9004] = stamp  # DateTimeDigitized
    return exif.tobytes()


def prepare_image(path: Path, max_edge: int, limit_bytes: int, taken_at: datetime | None = None) -> Prepared:
    """Resize to a phone-sized JPEG. All metadata is dropped; only the capture date is written back,
    so the photo lands on the right day when someone saves it."""
    exif = date_exif(taken_at)
    try:
        img_file = Image.open(path)
    except Image.DecompressionBombError as exc:
        raise SkipMedia(f"too many pixels: {path.name}") from exc
    with img_file as img:
        if img.size[0] * img.size[1] > MAX_PIXELS:
            raise SkipMedia(f"too many pixels ({img.size[0]}x{img.size[1]}): {path.name}")
        # JPEG: decode straight at 1/2, 1/4 or 1/8 scale when that is still big enough.
        # Saves most of the memory and time for large camera photos (helps on a Raspberry Pi).
        w, h = img.size
        img.draft("RGB", (max_edge, 1) if w >= h else (1, max_edge))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        edge = max_edge
        quality = 85
        while True:
            copy = img if edge == max_edge else img.copy()
            if edge != max_edge:
                copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            extra = {"exif": exif} if exif else {}
            copy.save(buf, "JPEG", quality=quality, optimize=True, progressive=True, **extra)
            data = buf.getvalue()
            if len(data) <= limit_bytes:
                break
            if quality > 60:
                quality -= 10
            elif edge > 480:
                edge = int(edge * 0.75)
            else:
                raise SkipMedia(f"cannot get {path.name} under {limit_bytes} bytes")
    return Prepared(data=data, filename=path.stem + ".jpg", content_type="image/jpeg")


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def video_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def metadata_args(taken_at: datetime | None) -> list[str]:
    """ffmpeg arguments that drop all metadata (location!) and keep only the creation time."""
    args = ["-map_metadata", "-1"]
    if taken_at is not None:
        args += ["-metadata", f"creation_time={taken_at.strftime('%Y-%m-%dT%H:%M:%S')}Z"]
    return args


def strip_video(path: Path, out: Path, taken_at: datetime | None = None) -> None:
    """Copy video and audio without re-encoding, minus the metadata (keeps the rotation)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
           *metadata_args(taken_at), "-c", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, timeout=600, capture_output=True)


def transcode(path: Path, out: Path, total_kbps: int, taken_at: datetime | None = None) -> None:
    video_kbps = max(total_kbps - AUDIO_KBPS, 50)
    scale = "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-vf", scale, "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2",
        *metadata_args(taken_at), "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True, timeout=1800, capture_output=True)


def prepare_video(path: Path, limit_bytes: int, taken_at: datetime | None = None) -> Prepared:
    """Send small MP4/MOV files as they are (minus metadata); transcode the rest to fit."""
    size = path.stat().st_size
    if size <= limit_bytes and path.suffix.lower() in PASSTHROUGH_VIDEO:
        content_type = "video/quicktime" if path.suffix.lower() == ".mov" else "video/mp4"
        if not ffmpeg_available():
            log.warning("ffmpeg not installed: sending %s with its original metadata", path.name)
            return Prepared(path.read_bytes(), path.name, content_type)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"clean{path.suffix.lower()}"
            try:
                strip_video(path, out, taken_at)
            except subprocess.SubprocessError as exc:
                raise SkipMedia(f"cannot remove metadata from {path.name}: {exc}") from exc
            if out.stat().st_size <= limit_bytes:
                return Prepared(out.read_bytes(), path.name, content_type)
    if not ffmpeg_available():
        raise SkipMedia("video too large and ffmpeg is not installed")
    try:
        duration = video_duration(path)
    except (subprocess.SubprocessError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise SkipMedia(f"cannot read video: {exc}") from exc
    if duration <= 0:
        raise SkipMedia("video has no duration")

    budget_kbps = int(limit_bytes * 8 / 1000 / duration * 0.9)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / (path.stem + ".mp4")
        for factor in (1.0, 0.75):
            kbps = int(budget_kbps * factor)
            if kbps - AUDIO_KBPS < MIN_VIDEO_KBPS:
                raise SkipMedia(f"video too long for {limit_bytes} bytes ({duration:.0f}s)")
            try:
                transcode(path, out, kbps, taken_at)
            except subprocess.SubprocessError as exc:
                raise SkipMedia(f"ffmpeg failed: {exc}") from exc
            if out.stat().st_size <= limit_bytes:
                return Prepared(out.read_bytes(), out.name, "video/mp4")
    raise SkipMedia("transcoded video still too large")


def prepare(path: Path, kind: str, *, max_edge: int, image_limit: int, video_limit: int,
            taken_at: datetime | None = None) -> Prepared:
    if kind == "image":
        return prepare_image(path, max_edge, image_limit, taken_at)
    return prepare_video(path, video_limit, taken_at)
