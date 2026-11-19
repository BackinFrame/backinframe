"""Every language is complete and every text fits its placeholders."""

import json
import re
from datetime import date

import pytest

from backinframe import i18n
from backinframe.caption import relative

LOCALES = i18n.LOCALES
EN = json.loads((LOCALES / "en.json").read_text())
PLURAL = re.compile(r"_(one|few|many|other)$")


def raw(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text())


def fields(text):
    return set(re.findall(r"{(\w+)}", text))


def test_there_is_a_file_per_language_and_no_more():
    assert {p.stem for p in LOCALES.glob("*.json")} == set(i18n.LANGUAGES)


@pytest.mark.parametrize("lang", list(i18n.LANGUAGES))
def test_language_is_complete(lang):
    data = raw(lang)
    bases = {PLURAL.sub("", k) for k in EN if PLURAL.search(k)}
    for key in EN:
        if PLURAL.search(key):
            continue
        assert key in data, f"{lang} misses {key}"
    for base in bases:
        assert f"{base}_other" in data, f"{lang} misses {base}_other"
        if lang in ("ru", "pl"):
            assert f"{base}_few" in data or base in ("today_years_ago",) and f"{base}_few" in data
    assert len(data["_months"]) == 12


@pytest.mark.parametrize("lang", list(i18n.LANGUAGES))
def test_placeholders_match_english(lang):
    for key, text in raw(lang).items():
        if key.startswith("_"):
            continue
        base = PLURAL.sub("", key)
        ref = EN.get(key) or EN.get(base + "_other")
        assert ref is not None, f"{lang}: unknown key {key}"
        assert fields(text) <= fields(ref) | {"n"}, f"{lang}.{key}: {fields(text)} vs {fields(ref)}"
        if "{n}" in ref and not key.endswith("_one"):
            assert "{n}" in text or lang in ("pl",) and key.endswith("_one"), f"{lang}.{key} lost {{n}}"


@pytest.mark.parametrize("lang,n,expected", [
    ("en", 1, "one"), ("en", 2, "other"), ("fr", 0, "one"), ("fr", 2, "other"),
    ("ru", 1, "one"), ("ru", 21, "one"), ("ru", 11, "many"), ("ru", 3, "few"), ("ru", 14, "many"), ("ru", 25, "many"),
    ("pl", 1, "one"), ("pl", 22, "few"), ("pl", 12, "many"), ("pl", 5, "many"), ("pl", 21, "many"),
    ("ja", 1, "other"), ("zh", 5, "other"),
])
def test_plural_rules(lang, n, expected):
    assert i18n.plural(lang, n) == expected


@pytest.mark.parametrize("lang,text", [
    ("en", "November 7, 2025"), ("nl", "7 november 2025"), ("de", "7. November 2025"),
    ("fr", "7 novembre 2025"), ("es", "7 de noviembre de 2025"), ("it", "7 novembre 2025"),
    ("pt", "7 de novembro de 2025"), ("pl", "7 listopada 2025"), ("ru", "7 ноября 2025"),
    ("zh", "2025年11月7日"), ("ja", "2025年11月7日"),
])
def test_dates(lang, text):
    assert i18n.format_date(date(2025, 11, 7), lang) == text


def test_counted_texts():
    today = date(2026, 11, 7)
    assert relative(date(2023, 11, 7), today, "ru") == "Сегодня 3 года назад"
    assert relative(date(2021, 11, 7), today, "ru") == "Сегодня 5 лет назад"
    assert relative(date(2024, 5, 1), today, "pl") == "2 lata temu"
    assert relative(date(2026, 5, 1), today, "pl") == "6 miesięcy temu"
    assert relative(date(2025, 11, 7), today, "ja") == "1年前の今日"
    assert i18n.frequency("pl", 2, 1) == "2 wspomnienia dziennie"
    assert i18n.frequency("pl", 5, 1) == "5 wspomnień dziennie"
    assert i18n.frequency("de", 1, 3) == "1 Erinnerung alle 3 Tage"


def test_unknown_language_falls_back_to_english():
    assert i18n.normalize("pt-BR") == "pt" and i18n.normalize("xx") == "en" and i18n.normalize(None) == "en"
    assert i18n.t("xx", "btn_more") == "More"
