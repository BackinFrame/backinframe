"""A year review ("your year in memories").

What a recipient received in a year, per album and per month, a small varied selection, and a
collage of that selection. ``BackInFrame.send_review`` sends it in the first days of January (or
when the admin presses the button). Like the daily choice, nothing here looks at what is in a
photo: only dates, albums and hearts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path

from .db import Database


@dataclass
class ReviewItem:
    send_id: int
    media_id: int
    kind: str
    album: str
    taken_at: datetime | None
    sent_at: datetime
    reaction: str = ""


@dataclass
class YearReview:
    recipient_id: int
    year: int
    total: int
    photos: int
    videos: int
    albums: list[tuple[str, int]]          # most first
    months: list[int]                      # 12 counts, January first
    picks: list[ReviewItem] = field(default_factory=list)
    first_sent: datetime | None = None
    last_sent: datetime | None = None


def album_of(path: str) -> str:
    """The album a path belongs to: its folder, without the "@immich/" kind of prefix."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    name = folder.rsplit("/", 1)[-1]
    return "" if name.startswith("@") else name


def _parse(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt.replace(tzinfo=None)


def build(db: Database, recipient_id: int, year: int, limit: int = 12) -> YearReview | None:
    """What one recipient got in ``year``; None when they got nothing."""
    return build_rows(db.sends_in_year(recipient_id, year), recipient_id, year, limit)


def build_rows(rows, recipient_id: int, year: int, limit: int = 12) -> YearReview | None:
    """The same from rows with send_id, media_id, kind, path (album/file), taken_at, sent_at and
    reaction, oldest first: the hosted version passes its own query here."""
    if not rows:
        return None
    items = [ReviewItem(send_id=r["send_id"], media_id=r["media_id"], kind=r["kind"], album=album_of(r["path"]),
                        taken_at=_parse(r["taken_at"]), sent_at=_parse(r["sent_at"]), reaction=r["reaction"] or "")
             for r in rows]
    # one item per photo: the same photo can come back during a year
    unique: dict[int, ReviewItem] = {}
    for it in items:
        if it.media_id not in unique or (it.reaction and not unique[it.media_id].reaction):
            unique[it.media_id] = it
    months = [0] * 12
    for it in items:
        months[it.sent_at.month - 1] += 1
    albums = Counter(it.album for it in items)
    return YearReview(
        recipient_id=recipient_id, year=year, total=len(items),
        photos=sum(1 for it in items if it.kind == "image"), videos=sum(1 for it in items if it.kind == "video"),
        albums=sorted(albums.items(), key=lambda kv: (-kv[1], kv[0])), months=months,
        picks=pick(list(unique.values()), limit), first_sent=items[0].sent_at, last_sent=items[-1].sent_at,
    )


def pick(items: list[ReviewItem], limit: int) -> list[ReviewItem]:
    """A varied selection: hearts first, then spread over the months and albums, photos before videos.
    Deterministic, so the same review always shows the same picks."""
    def rank(it: ReviewItem):
        return (0 if it.reaction == "heart" else 1, 0 if it.kind == "image" else 1, it.sent_at, it.send_id)

    chosen: list[ReviewItem] = []
    used_months: set[int] = set()
    used_albums: Counter = Counter()
    for it in sorted(items, key=rank):  # round 1: one per month, at most two per album
        if len(chosen) >= limit:
            break
        if it.sent_at.month in used_months or used_albums[it.album] >= 2:
            continue
        chosen.append(it)
        used_months.add(it.sent_at.month)
        used_albums[it.album] += 1
    for it in sorted(items, key=rank):  # round 2: fill up
        if len(chosen) >= limit:
            break
        if it not in chosen:
            chosen.append(it)
    return sorted(chosen, key=lambda it: (it.sent_at, it.send_id))


MIN_MEMORIES = 6        # fewer than this in a year: no review
TILE = 420              # pixels per square in the collage
GAP = 18


def collage(paths: list[Path | bytes], columns: int = 3) -> bytes:
    """The picks as one JPEG: squares on white, like prints on a table. No metadata at all."""
    from PIL import Image, ImageOps

    from . import media  # noqa: F401 - registers HEIC support

    tiles = []
    for path in paths:
        try:
            with Image.open(BytesIO(path) if isinstance(path, bytes) else path) as img:
                if img.size[0] * img.size[1] > media.MAX_PIXELS:
                    continue
                img = ImageOps.exif_transpose(img).convert("RGB")
                tiles.append(ImageOps.fit(img, (TILE, TILE), Image.Resampling.LANCZOS))
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
    if not tiles:
        raise ValueError("no photos for a collage")
    columns = min(columns, len(tiles))
    rows = -(-len(tiles) // columns)
    width = columns * TILE + (columns + 1) * GAP
    height = rows * TILE + (rows + 1) * GAP
    sheet = Image.new("RGB", (width, height), (250, 247, 242))
    for i, tile in enumerate(tiles):
        x = GAP + (i % columns) * (TILE + GAP)
        y = GAP + (i // columns) * (TILE + GAP)
        sheet.paste(tile, (x, y))
    out = BytesIO()
    sheet.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()
