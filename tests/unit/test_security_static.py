"""Static security checks on the source tree and the deployment files (OWASP ASVS 5.0, V13/V15).

Fast, no network: they guard against regressions such as a debug flag, a shell call, a secret in
the repository or a container that runs as root. Bandit runs here too when it is installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "backinframe"
PY = sorted(SRC.rglob("*.py"))
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv"}
TRACKED = [p for p in ROOT.rglob("*") if p.is_file()
           and not any(part in SKIP_DIRS or part.endswith(".egg-info") for part in p.relative_to(ROOT).parts)
           and p.suffix not in {".png", ".jpg", ".woff2", ".ico"}]

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
    "Stripe/Mollie live key": re.compile(r"\b(?:sk_live|live)_[A-Za-z0-9]{20,}\b"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
}


def source(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("name,pattern", SECRET_PATTERNS.items(), ids=list(SECRET_PATTERNS))
def test_no_secrets_in_the_repository(name, pattern):
    """ASVS 13.3.1: no keys or tokens in the code, docs or examples."""
    hits = [f"{p.relative_to(ROOT)}" for p in TRACKED if pattern.search(source(p))]
    assert hits == [], f"{name} found in {hits}"


@pytest.mark.parametrize("pattern,why", [
    (r"shell\s*=\s*True", "no shell: arguments go as a list"),
    (r"\bos\.system\(|\bos\.popen\(", "no shell commands"),
    (r"\beval\(|\bexec\(", "no dynamic code"),
    (r"\bpickle\.|\bmarshal\.loads|\byaml\.load\(", "no unsafe deserialisation"),
    (r"verify\s*=\s*False", "TLS certificates are always checked"),
    (r"\bdebug\s*=\s*True", "never in debug mode"),
    (r"\bmd5\(|\bsha1\(", "no weak hashes"),
    (r"xml\.etree\.ElementTree\s+import\s+(?:fromstring|parse|XML)\b|ET\.fromstring", "XML only through defusedxml"),
    (r"autoescape\s*=\s*False|\|\s*safe\b|Markup\(", "templates stay auto-escaped"),
])
def test_dangerous_constructs_are_absent(pattern, why):
    """ASVS 1.2 / 1.5 / 12.3 / Top 10 A05, A08: injection, deserialisation and TLS shortcuts."""
    files = PY + sorted((SRC / "templates").glob("*.html"))
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in files for i, line in enumerate(source(p).splitlines(), 1)
            if re.search(pattern, line) and "# security: reviewed" not in line]
    assert hits == [], f"{why}: {hits}"


def test_secret_values_come_from_the_secrets_module():
    """ASVS 11.5.1: tokens and secret links from a CSPRNG, never from random."""
    for p in PY:
        for line in source(p).splitlines():
            if re.search(r"token|secret|password|key", line, re.I) and re.search(r"\brandom\.(?!Random\b)", line):
                pytest.fail(f"{p.name}: {line.strip()}")
    assert "secrets.token_urlsafe(32)" in source(SRC / "db.py")


def test_password_is_compared_in_constant_time():
    """ASVS 11.2.4 / 6.2: no timing side channel on the admin password."""
    auth = source(SRC / "adminauth.py")
    assert "secrets.compare_digest" in auth
    assert not re.search(r"==\s*self\.password|self\.password\s*==", auth + source(SRC / "web.py"))


def test_container_runs_unprivileged_and_read_only():
    """ASVS 13.2 / 15.2: least privilege for the container."""
    dockerfile = source(ROOT / "Dockerfile")
    assert re.search(r"^USER\s+(?!root)\S+", dockerfile, re.M)
    assert "HEALTHCHECK" in dockerfile
    for compose in ("docker-compose.yml", "docker-compose.own-ntfy.yml"):
        text = source(ROOT / compose)
        assert "/media:ro" in text, compose
        assert "read_only: true" in text and "no-new-privileges:true" in text and "cap_drop: [ALL]" in text, compose
        assert not re.search(r"privileged:\s*true", text), compose


def test_dependencies_have_lower_bounds():
    """ASVS 15.2.1 / Top 10 A03: known versions; pip-audit in CI checks them for advisories."""
    import tomllib

    deps = tomllib.loads(source(ROOT / "pyproject.toml"))["project"]["dependencies"]
    assert all(">=" in d or "==" in d for d in deps), deps
    assert any(d.startswith("defusedxml") for d in deps)


def test_ci_runs_the_security_tools():
    ci = source(ROOT / ".github" / "workflows" / "ci.yml")
    assert "pip-audit" in ci and "bandit" in ci


@pytest.mark.skipif(shutil.which("bandit") is None, reason="bandit not installed (pip install -e '.[security]')")
def test_bandit_finds_nothing():
    """Static analysis (SAST) with the reviewed exceptions in pyproject.toml [tool.bandit]."""
    result = subprocess.run(["bandit", "-q", "-c", str(ROOT / "pyproject.toml"), "-r", str(SRC)],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
