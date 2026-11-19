# Security checks

Back in Frame is checked against **OWASP ASVS 5.0** (Application Security Verification Standard,
May 2025) and the **OWASP Top 10 (2025)**. The checks that a machine can do run with every test
run and in CI; this page lists what is checked, where, and what is a deliberate choice.

## Running them

```bash
pip install -e ".[test,security]"
pytest -m security                      # the security tests only (about 5 seconds)
bandit -q -c pyproject.toml -r src      # static analysis
pip-audit --skip-editable               # dependencies against known vulnerabilities (needs internet)
```

`pytest` without `-m` runs them along with everything else. In CI, the `security` job also scans
the git history for secrets (gitleaks) and the Docker image for known vulnerabilities (Trivy), and
a release image is only built when the `test`, `security` and `zap` jobs pass.

The `zap` job starts the container and runs an OWASP ZAP baseline scan (spider plus passive
checks) against it. The rules in `.zap/rules.tsv` decide what breaks the build: missing CSP,
framing protection, `nosniff`, cookies without `HttpOnly`, error pages that leak details, and
CORS mistakes are `FAIL`; the rest is in the report, kept as a build artifact. Dependabot
(`.github/dependabot.yml`) opens weekly pull requests for Python packages, GitHub Actions and the
Docker base image, and security updates right away; each one goes through the same CI.

## What is checked

| Area (ASVS 5.0 / Top 10 2025) | Check | Where |
|---|---|---|
| V3.4 headers / A02 misconfiguration | Admin page: CSP without inline script (`default-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `no-store` – on every response, error pages included | `functional/test_security.py` |
| V3.4 | Web app: the same, plus `Referrer-Policy: no-referrer` and `noindex`, so the secret link never leaks | `functional/test_security.py` |
| V3.4.1 | HSTS when the browser talks https (directly or through the proxy's `X-Forwarded-Proto`), not over plain http | `functional/test_security.py` |
| V3.4.3 | The strict CSP breaks nothing: no inline handlers or script blocks in the pages, and in a real Chrome with the CSP enforced there are no violations, form posts work and the delete confirmation still asks | `functional/test_security.py` |
| V6.6 / V7 login and sessions | Admin login with a server-side session (cookie `HttpOnly`, `SameSite=Strict`, `Secure` over https, only a hash stored); a new session id at login; logout and a password change end sessions; 14-day limit; optional TOTP with encrypted key (`data/admin.key`), single-use codes and recovery codes, throttled; a `reset-2fa` command for a lost phone | `functional/test_admin_login.py` |
| V3.5 / A01 CSRF | Posts from another site are refused: a foreign or `null` Origin, `Sec-Fetch-Site: cross-site` or `same-site`, a foreign Referer – on the admin page and the web app | `functional/test_security.py` |
| V6.1 / V6.3 / A07 | Admin password required everywhere except `/healthz`; malformed headers refused; after 10 wrong passwords in 15 minutes an address waits (HTTP 429 with `Retry-After`); compared in constant time | `functional/test_security.py`, `unit/test_security_static.py` |
| V8.2 / A01 IDOR | One recipient's link never reaches another recipient's photos | `functional/test_security.py` |
| V7.2 / V11.5 | Secret links carry 256 bits from `secrets`, never from `random` | both |
| V5.3 / A01 | Path tricks (`..%2f`, encoded dots, non-numeric ids) find nothing | `functional/test_security.py` |
| V1.2 / A05 injection | Text in the URL and names are HTML-escaped; ids are typed, so SQL in them never reaches a query; templates stay auto-escaped (`|safe` only on reviewed lines) | both |
| V4 / V5.2 | JSON bodies are size-limited (8 KB) and type-checked | `functional/test_security.py` |
| V16.5 / A10 errors | Error responses contain no stack traces, paths or library names; no API docs or OpenAPI schema | `functional/test_security.py` |
| V14.2 privacy | Photos leave without location or camera details; only the capture date stays | `functional/test_security.py`, `unit/test_media.py` |
| V5.2 / A06 | Decompression bombs (more than 100 megapixels) are skipped, not decoded | `functional/test_security.py` |
| V1.5 / A08 | XML from Nextcloud is parsed with `defusedxml` (no entity expansion); no `pickle`, `eval`, `shell=True`, `verify=False` or debug mode | `unit/test_security_static.py`, bandit |
| V13.3 / A04 secrets | No private keys or tokens in the repository (pattern check here, gitleaks over the history in CI) | `unit/test_security_static.py`, CI |
| V13.2 / V15.2 | Container runs as a normal user; the compose files mount photos read-only and run the container read-only, without capabilities and with `no-new-privileges` | `unit/test_security_static.py`, CI smoke test |
| V15.2 / A03 supply chain | Dependencies with lower bounds; pip-audit against PyPI/OSV advisories; Trivy on the image | CI |
| SSRF (V1.3) | Immich/Nextcloud: no redirects followed; push only to the known push services | `unit/test_sources.py`, `unit/test_webpush.py` |

## Deliberate choices

- **One admin, one password, optional two-step verification.** The admin page is meant for your
  own network or VPN; for more, use your own login in front of it (admin guide §10). The throttle
  counts per client address: behind a reverse proxy that is the proxy's address, so ten wrong
  guesses lock everyone out for 15 minutes. That is the safe direction. Wrong codes are also
  counted over all addresses (5 per 15 minutes), because there is only one account to guess.
- **`style-src 'unsafe-inline'`** stays in the CSP: the pages carry their styles inline. Styles
  cannot run code; scripts stay `'self'` only.
- **Bandit findings that are accepted** are listed with a reason in `pyproject.toml` under
  `[tool.bandit]`: ffmpeg/ffprobe get argument lists (never a shell) and only paths we found
  ourselves; `random` picks photos and send times, not secrets; empty default passwords mean "not
  set". Two SQL statements build column names from a fixed list and carry `# nosec B608`.
- **Third-party scanners run as pinned container images**, not as GitHub Actions by tag:
  `trivy-action` tags were rewritten in the March 2026 supply-chain incident (CVE-2026-33634).
  Pin them by digest when you publish your own builds.

## Found and fixed while writing these checks

- The admin page sent no security headers at all, and still served `/openapi.json`.
- The delete confirmation used an inline `onsubmit` handler with the translated text pasted into
  JavaScript; a translation with a quote would have broken it. It is now in `admin.js`.
- With `Referrer-Policy: no-referrer` on the admin page, browsers send `Origin: null` with form
  posts. The cross-site check now treats `null` as foreign, which is why the admin page uses
  `same-origin` (the browser test catches a regression).
- No limit on password guesses for the admin page.
- Nextcloud's XML was parsed with the standard library parser; now `defusedxml`.
- A decompression bomb photo was decoded before being shrunk; it is now skipped.
