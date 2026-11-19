from datetime import date, datetime

from backinframe.caption import album_name, build, format_date, relative
from backinframe.db import Media
from backinframe.i18n import STRINGS, frequency


def m(path, taken=None):
    return Media(1, path, "image", 1, 0, taken)


def test_album_name():
    assert album_name(m("2023 Chili/Santiago/IMG_1.jpg"), "nl") == "Santiago"
    assert album_name(m("Zomer_2019/a.jpg"), "nl") == "Zomer 2019"
    assert album_name(m("a.jpg"), "nl") == "Herinnering"
    assert album_name(m("a.jpg"), "en") == "Memory"


def test_format_date():
    assert format_date(date(2023, 3, 14), "nl") == "14 maart 2023"
    assert format_date(date(2023, 3, 14), "en") == "March 14, 2023"


def test_relative():
    today = date(2026, 11, 7)
    assert relative(date(2023, 11, 7), today, "nl") == "Vandaag 3 jaar geleden"
    assert relative(date(2025, 11, 8), today, "nl") == "11 maanden geleden"
    assert relative(date(2025, 11, 7), today, "en") == "Today, 1 year ago"
    assert relative(date(2025, 10, 1), today, "nl") == "1 jaar geleden"
    assert relative(date(2020, 1, 1), today, "nl") == "6 jaar geleden"
    assert relative(date(2026, 10, 20), today, "nl") == "Nog niet zo lang geleden"


def test_build():
    title, text = build(m("Chili/x.jpg", datetime(2024, 11, 9, 10)), date(2026, 11, 7), "nl")
    assert title == "Chili"
    assert text == "📅 9 november 2024 · 1 jaar geleden"
    assert build(m("x.jpg"), date(2026, 1, 1), "nl") == ("Herinnering", "")


def test_translations_complete():
    assert set(STRINGS["nl"]) == set(STRINGS["en"])


def test_frequency():
    assert frequency("nl", 1, 1) == "1 herinnering per dag"
    assert frequency("nl", 3, 1) == "3 herinneringen per dag"
    assert frequency("en", 1, 4) == "1 memory every 4 days"
