"""Title and text that go with a photo."""

from __future__ import annotations

import re
from datetime import date

from .db import Media
from .i18n import format_date, t, tn
from .selector import is_on_this_day


def album_name(media: Media, lang: str) -> str:
    folder = media.folder
    if not folder:
        return t(lang, "memory")
    name = folder.rsplit("/", 1)[-1]
    name = re.sub(r"[_]+", " ", name).strip()
    return name or t(lang, "memory")


__all__ = ["album_name", "build", "format_date", "relative"]


def relative(taken: date, today: date, lang: str) -> str:
    if is_on_this_day_date(taken, today):
        years = today.year - taken.year
        return tn(lang, "today_years_ago", years)
    months = (today.year - taken.year) * 12 + (today.month - taken.month)
    if today.day < taken.day:
        months -= 1
    if months >= 12:
        years = months // 12
        return tn(lang, "years_ago", years)
    if months >= 2:
        return tn(lang, "months_ago", months)
    return t(lang, "recently")


def is_on_this_day_date(taken: date, today: date) -> bool:
    return taken.year < today.year and taken.month == today.month and taken.day == today.day


def build(media: Media, today: date, lang: str) -> tuple[str, str]:
    """Title: what the admin wrote for this photo, otherwise the album. Text: the date and how long ago,
    and the admin's note on a line of its own."""
    title = (getattr(media, "title", "") or "").strip() or album_name(media, lang)
    note = (getattr(media, "note", "") or "").strip()
    sender = (getattr(media, "sender", "") or "").strip()
    who = t(lang, "from_sender", name=sender) if sender else ""
    if media.taken_at is None:
        first = who
    else:
        taken = media.taken_at.date()
        first = f"{format_date(taken, lang)} · {relative(taken, today, lang)}"
        if is_on_this_day(media.taken_at, today) and not is_on_this_day_date(taken, today):
            first = "📅 " + first
        if who:
            first = f"{first} · {who}"
    return title, "\n".join(part for part in (first, note) if part)
