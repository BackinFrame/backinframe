"""Translations. One JSON file per language in ``locales/``; English is the reference.

Keys starting with ``_`` are not texts: ``_months`` (month names as used in a date, so genitive
where a language needs it) and ``_date`` (how a date is written: {day}, {month}, {m}, {year}).

Counted texts have a key per plural category, like ``years_ago_one`` and ``years_ago_other``;
Russian and Polish add ``_few`` and ``_many``. :func:`tn` picks the right one.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path

LOCALES = Path(__file__).parent / "locales"

# Language code -> its own name. The order is the order of the language menus.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "nl": "Nederlands",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "pl": "Polski",
    "ru": "Русский",
    "zh": "简体中文",
    "ja": "日本語",
}
DEFAULT = "en"


@cache
def table(lang: str) -> dict:
    if lang not in LANGUAGES:
        lang = DEFAULT
    data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    if lang != DEFAULT:
        data = {**table(DEFAULT), **data}  # a missing key falls back to English
    return data


# Kept for callers that want the raw tables (tests, the hosted service).
class _Strings(dict):
    def __missing__(self, lang: str) -> dict:
        return table(lang)


STRINGS = _Strings()


def normalize(lang: str | None) -> str:
    """'pt-BR' -> 'pt', unknown -> English."""
    code = (lang or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return code if code in LANGUAGES else DEFAULT


def t(lang: str, key: str, **kwargs) -> str:
    text = table(lang)[key]
    return text.format(**kwargs) if kwargs else text


def plural(lang: str, n: int) -> str:
    """CLDR plural category, for the languages we have."""
    if lang in ("ja", "zh"):
        return "other"
    if lang == "fr":
        return "one" if n in (0, 1) else "other"
    if lang in ("ru", "pl"):
        if lang == "ru" and n % 10 == 1 and n % 100 != 11:
            return "one"
        if lang == "pl" and n == 1:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"


def tn(lang: str, base: str, n: int, **kwargs) -> str:
    data = table(lang)
    key = f"{base}_{plural(lang, n)}"
    if key not in data:
        key = f"{base}_other"
    return data[key].format(n=n, **kwargs)


def format_date(d: date, lang: str) -> str:
    data = table(lang)
    return data["_date"].format(day=d.day, month=data["_months"][d.month - 1], m=d.month, year=d.year)


def frequency(lang: str, per_day: int, every_days: int) -> str:
    if every_days > 1:
        return tn(lang, "freq_every_days", every_days)
    return tn(lang, "freq_per_day", per_day)


def js_texts(lang: str) -> dict[str, str]:
    """The texts the recipient web app's script needs."""
    return {k[3:]: v for k, v in table(lang).items() if k.startswith("js_")}
