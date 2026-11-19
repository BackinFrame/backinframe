"""The Back in Frame launch video: 24 seconds, 1080x1920 (Reels, Shorts, TikTok, Stories).

    python3 make_video.py [--photos DIR] [--out backinframe-launch.mp4] [--today 2026-11-17]

DIR holds JPEG/PNG photos, in folders per album ("Chile 2025/IMG_1234.jpg") or loose. The album
name and the capture date (EXIF) end up in the notifications, as in the real app. Without --photos
the built-in illustrations are used. 12 or more photos look best, from different years; fewer are
repeated.
Needs Playwright with Chrome, Pillow and ffmpeg."""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
W, H, FPS = 1080, 1920, 30
SCENES = [0, 2.6, 5.4, 11.0, 15.6, 19.6, 21.8, 24.0]  # cut points; every scene is its own file
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def taken(path: Path, img: Image.Image) -> date | None:
    try:
        raw = img.getexif().get_ifd(0x8769).get(0x9003) or img.getexif().get(0x0132)
        return datetime.strptime(str(raw)[:10], "%Y:%m:%d").date() if raw else None
    except (ValueError, KeyError):
        return None


def ago_text(when: date | None, today: date) -> str:
    if when is None:
        return ""
    years = today.year - when.year - ((today.month, today.day) < (when.month, when.day))
    if (when.month, when.day) == (today.month, today.day) and years > 0:
        return f"Today, {years} year{'s' if years > 1 else ''} ago"
    if years > 0:
        return f"{years} year{'s' if years > 1 else ''} ago"
    return "Last year" if when.year < today.year else "This year"


def load_photos(folder: Path, today: date) -> tuple[list[dict], str]:
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    items = []
    for f in files:
        img = Image.open(f)
        when = taken(f, img)
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1200, 1200))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=86)
        album = f.parent.name if f.parent != folder else ""
        items.append((when, album, buf.getvalue()))
    if not items:
        raise SystemExit(f"No photos in {folder}")
    # The first notification is "Today, x years ago": the video is set on that photo's anniversary.
    dated = [w for w, _, _ in items if w and w.year < today.year]
    hero = min(dated, key=lambda w: abs((date(today.year, w.month, min(w.day, 28)) - today).days)) if dated else None
    if hero:
        today = date(today.year if (hero.month, hero.day) <= (today.month, today.day) else today.year - 1,
                     hero.month, hero.day)
        items.sort(key=lambda it: it[0] != hero)
    out = []
    for when, album, data in items:
        out.append({"src": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
                    "album": album or (str(when.year) if when else "Memories"),
                    "date": f"{when.day} {MONTHS[when.month - 1]} {when.year}" if when else "",
                    "ago": ago_text(when, today),
                    "stamp": f"'{when.year % 100:02d}  {when.month:2d}  {when.day:2d}" if when else ""})
    lock = f"{today.strftime('%A')} {today.day} {MONTHS[today.month - 1]}"
    return out, lock


def render(photos: list[dict], lock: str, out: Path) -> None:
    page = (HERE / "launch.html").read_text().replace("/*PHOTOS*/[]", json.dumps(photos)).replace("/*LOCK*/", lock)
    work = Path(tempfile.mkdtemp())
    (work / "v.html").write_text(page)
    for font in ("atkinson-latin.woff2", "atkinson-latin-ext.woff2", "dseg7.woff2"):
        shutil.copy(HERE / "fonts" / font, work / font)
    parts = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.goto((work / "v.html").as_uri())  # fonts sit next to the page
        pg.wait_for_timeout(800)
        for k, (a, e) in enumerate(zip(SCENES, SCENES[1:])):
            d = work / f"s{k}"
            d.mkdir()
            for i in range(round((e - a) * FPS)):
                pg.evaluate(f"render({a + i / FPS})")
                pg.screenshot(path=str(d / f"{i:04d}.jpg"), type="jpeg", quality=92)
            tmp, final = work / f"tmp_{k}.mp4", work / f"scene_{k}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(d / "%04d.jpg"),
                            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                            "-r", str(FPS), str(tmp)], check=True)
            tmp.rename(final)
            parts.append(final)
            shutil.rmtree(d)
        b.close()
    (work / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(work)
    print(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=Path, default=HERE / "demo-photos")
    ap.add_argument("--out", type=Path, default=Path("backinframe-launch.mp4"))
    ap.add_argument("--today", default=date.today().isoformat())
    args = ap.parse_args()
    photos, lock = load_photos(args.photos, date.fromisoformat(args.today))
    render(photos, lock, args.out)
