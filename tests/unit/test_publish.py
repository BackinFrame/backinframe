"""The Unraid template and profile: valid XML, and every setting in them really exists."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 - our own files
from pathlib import Path

from backinframe.config import _FIELDS, PREFIX

UNRAID = Path(__file__).resolve().parents[2] / "publish" / "unraid"


def test_profile_has_what_community_applications_needs():
    root = ET.parse(UNRAID / "ca_profile.xml").getroot()  # nosec B314
    assert root.tag == "Maintainer"
    assert (root.findtext("Profile") or "").strip()
    assert root.findtext("Icon").startswith("https://")
    assert (UNRAID / "LICENSE").read_text().startswith("MIT License")
    assert (UNRAID / "icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_template_settings_exist():
    root = ET.parse(UNRAID / "templates" / "backinframe.xml").getroot()  # nosec B314
    assert root.findtext("Repository").startswith("ghcr.io/")
    for tag in ("Name", "Overview", "Category", "Icon", "Support", "Project", "TemplateURL", "Changes"):
        assert (root.findtext(tag) or "").strip(), tag
    known = {PREFIX + k for k in _FIELDS} | {"TZ"}
    for cfg in root.iter("Config"):
        if cfg.get("Type") == "Variable":
            assert cfg.get("Target") in known, cfg.get("Target")
        if cfg.get("Type") == "Path":
            assert cfg.get("Target") in ("/media", "/data")
    targets = {c.get("Target") for c in root.iter("Config")}
    assert {"BACKINFRAME_REACTIONS", "BACKINFRAME_YEAR_REVIEW", "8080", "/media"} <= targets


def test_support_link_is_the_same_in_both_files():
    profile = ET.parse(UNRAID / "ca_profile.xml").getroot()  # nosec B314
    template = ET.parse(UNRAID / "templates" / "backinframe.xml").getroot()  # nosec B314
    assert profile.findtext("Forum") == template.findtext("Support")
