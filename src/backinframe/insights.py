"""Which photos get a heart, for the admin page.

Hearts can only be given in the web app, so everything here counts web-app memories only. The
overview is built from dates, albums and hearts; nothing looks at what is in a photo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .review import album_of


@dataclass
class LovedPhoto:
    media_id: int
    path: str
    kind: str
    album: str
    taken_at: datetime | None
    names: list[str]                 # who gave it a heart
    last: datetime | None            # newest heart

    @property
    def hearts(self) -> int:
        return len(self.names)


@dataclass
class AlbumStat:
    album: str
    sent: int                        # memories delivered in the web app
    hearted: int                     # of which got a heart

    @property
    def rate(self) -> int:
        return round(100 * self.hearted / self.sent) if self.sent else 0


@dataclass
class RecipientStat:
    recipient_id: int
    name: str
    sent: int
    hearted: int


@dataclass
class Unloved:
    media_id: int
    path: str
    kind: str
    album: str
    sent: int


@dataclass
class HeartOverview:
    sent: int = 0
    hearted: int = 0
    loved: list[LovedPhoto] = field(default_factory=list)       # most hearts first
    albums: list[AlbumStat] = field(default_factory=list)       # highest share first
    recipients: list[RecipientStat] = field(default_factory=list)
    unloved: list[Unloved] = field(default_factory=list)        # sent often, never a heart

    @property
    def rate(self) -> int:
        return round(100 * self.hearted / self.sent) if self.sent else 0


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def hearts(rows, unloved_after: int = 3, limit: int = 60) -> HeartOverview:
    """Build the overview from ``db.app_sends()`` rows (or rows with the same columns)."""
    out = HeartOverview()
    loved: dict[int, LovedPhoto] = {}
    albums: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    people: dict[int, RecipientStat] = {}
    per_media: dict[int, list] = {}
    for r in rows:
        album = album_of(r["path"]) or "–"
        heart = r["reaction"] == "heart"
        out.sent += 1
        out.hearted += heart
        albums[album][0] += 1
        albums[album][1] += heart
        stat = people.setdefault(r["recipient_id"], RecipientStat(r["recipient_id"], r["recipient"], 0, 0))
        stat.sent += 1
        stat.hearted += heart
        info = per_media.setdefault(r["media_id"], [r["path"], r["kind"], album, 0, False])
        info[3] += 1
        info[4] = info[4] or heart
        if heart:
            photo = loved.setdefault(r["media_id"], LovedPhoto(r["media_id"], r["path"], r["kind"], album,
                                                               _dt(r["taken_at"]), [], None))
            if r["recipient"] not in photo.names:
                photo.names.append(r["recipient"])
            when = _dt(r["reacted_at"])
            if when and (photo.last is None or when > photo.last):
                photo.last = when
    out.loved = sorted(loved.values(), key=lambda p: (-p.hearts, -(p.last.timestamp() if p.last else 0),
                                                     p.media_id))[:limit]
    out.albums = sorted((AlbumStat(a, s, h) for a, (s, h) in albums.items()),
                        key=lambda a: (-a.rate, -a.sent, a.album))
    out.recipients = sorted(people.values(), key=lambda p: p.name.lower())
    out.unloved = sorted((Unloved(mid, path, kind, album, n) for mid, (path, kind, album, n, hearted)
                          in per_media.items() if n >= unloved_after and not hearted),
                         key=lambda u: (-u.sent, u.media_id))[:limit]
    return out
