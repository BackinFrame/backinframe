"""The CI set-up stays complete: dependency updates, the ZAP baseline scan and its rules."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[2]


def test_dependabot_covers_python_actions_and_docker():
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    assert cfg["version"] == 2
    ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
    assert {"pip", "github-actions", "docker"} <= ecosystems
    assert all(u["schedule"]["interval"] in ("daily", "weekly") for u in cfg["updates"])


def test_zap_scan_runs_before_publishing():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = ci["jobs"]
    assert "zap" in jobs and "zap" in jobs["docker"]["needs"]
    script = "\n".join(step.get("run", "") for step in jobs["zap"]["steps"])
    assert "zap-baseline.py" in script and "conf/rules.tsv" in script
    assert any(step.get("uses", "").startswith("actions/upload-artifact") for step in jobs["zap"]["steps"])


def test_zap_rules_are_valid_and_strict_where_it_matters():
    rules = {}
    for line in (ROOT / ".zap" / "rules.tsv").read_text().splitlines():
        rule_id, action, name = line.split("\t")
        assert rule_id.isdigit() and action in ("IGNORE", "INFO", "WARN", "FAIL"), line
        assert name.strip()
        rules[rule_id] = action
    for must_fail in ("10010", "10020", "10021", "10038", "90022"):  # cookies, framing, sniffing, CSP, errors
        assert rules[must_fail] == "FAIL"
