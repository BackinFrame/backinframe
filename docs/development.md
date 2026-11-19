# Development

## Setup

```bash
git clone https://github.com/backinframe/backinframe && cd backinframe
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]" ruff
sudo apt install ffmpeg          # for video tests (they are skipped without it)
playwright install chromium      # browser and accessibility tests (skipped without Chrome)
```

To run the program locally against the public ntfy.sh:

```bash
mkdir -p /tmp/lm/photos && cp ~/Pictures/*.jpg /tmp/lm/photos/
BACKINFRAME_MEDIA_DIR=/tmp/lm/photos BACKINFRAME_DATA_DIR=/tmp/lm/data BACKINFRAME_WEB_PORT=8080 backinframe
```

## Tests

```bash
pytest                    # everything that can run here
pytest tests/unit         # fast, no network, no external programs (ffmpeg tests skip without it)
pytest -m "not ntfy"      # skip the tests that need a real ntfy server
pytest -rs                # show why tests were skipped
pytest -m security        # the security checks only (see docs/security.md)
```

The end-to-end tests start a **real ntfy server**. Point `NTFY_BIN` at the binary, or put `ntfy`
on your `PATH`:

```bash
curl -sSL https://github.com/binwiederhier/ntfy/releases/download/v2.14.0/ntfy_2.14.0_linux_amd64.tar.gz | tar xz -C /tmp
NTFY_BIN=/tmp/ntfy_2.14.0_linux_amd64/ntfy pytest -rs
```

### What is covered

| Suite | Scope |
|---|---|
| `unit/test_scheduler.py` | window and gap guarantees (property test over random schedules), every-N-days, pause, rolling over to the next day, time zones, summer time, determinism |
| `unit/test_selector.py` | this-day boost, cool-down and fallback, repeat penalty, fair share per folder |
| `unit/test_scanner.py` | EXIF date (and no location), ignored files, add/update/remove cycle, corrupt files, video `creation_time` |
| `unit/test_media.py` | resizing, EXIF rotation, transparency, shrinking under a limit, video passthrough, transcoding, skipping |
| `unit/test_ntfy.py` | publish parameters and action format, auth header, error mapping, limit detection, NDJSON polling |
| `unit/test_webpush.py` | push-service allow-list, VAPID key file and ES256 signature, encryption only the browser can read, failure mapping |
| `unit/test_control.py` | command aliases, More/Less steps and limits, pause/resume |
| `unit/test_caption.py` | album names, dates and "years ago" text in Dutch and English |
| `unit/test_i18n.py` | all eleven languages complete, placeholders kept, CLDR plural rules for Russian and Polish, date formats, fallback to English |
| `unit/test_channels.py` | e-mail, Telegram, Signal, Matrix and Discord against fakes that answer like the real services: address checks (including header injection and non-Discord URLs), what is sent, buttons, polling once, error mapping, tokens never in errors |
| `unit/test_service_channels.py` | channels through the service: menu only shows what is set up, welcome per channel, buttons and text replies in any language, forged presses ignored, failed sends logged, switching channels, a channel switched off later, recipient language everywhere |
| `unit/test_service.py` | recipients, sending when due, failures, broken and missing files, limit handling, buttons, pause, a two-week simulation |
| `functional/test_web.py` | the admin page through HTTP: full recipient flow, validation, 404/400, password, cross-site refusal, English UI |
| `functional/test_web_app.py` | the recipient web app over HTTP with a fake push service: secret links, headers, encrypted delivery, gallery photos without location, buttons, cross-site refusal, device cleanup, cache, settings and old databases |
| `functional/test_web_app_browser.py` | the web app's script in headless Chrome (skipped without Playwright/Chrome): notification state, gallery, buttons, the iPhone home-screen hint, no CSP violations |
| `functional/test_e2e_ntfy.py` | real ntfy: photo with attachment and buttons, tapping buttons the way the app does, restart without replaying taps, strict limits, video transcoding |
| `functional/test_channels_flow.py` | the admin page with every channel: a real SMTP server (aiosmtpd) receiving the photo without location, Telegram set up from /start to button press, refused addresses, switching channel and language, the web app in Japanese |
| `functional/test_sources_flow.py` | Immich and Nextcloud fakes → sync → scan → send with the album as title; a server that is down, an album that disappears, a source switched off, the admin page status and *Sync now* |
| `functional/test_web_app_browser.py` | the web app's script in Chrome: notifications, buttons, and the iPhone guide for iOS 26, iOS 18, Chrome on iOS, an in-app browser, an iPad and iOS 15, with *Reduce Motion* and *Copy link* |
| `functional/test_accessibility.py` | axe-core (WCAG 2.2 A and AA) on every page, on a phone and a desktop, light and dark, plus visible keyboard focus on every Tab stop and no sideways scrolling at 320 px |
| `functional/test_security.py` | OWASP ASVS 5.0 checks over HTTP: security headers on every response, HSTS behind https, CSRF (Origin, `Sec-Fetch-Site`, Referer), password throttle, IDOR between recipients, token entropy, path tricks, escaping, JSON limits, no leaking errors, no location in photos, decompression bombs, and the strict CSP in a real Chrome |
| `unit/test_security_static.py` | the source tree: no secrets, no shell/eval/pickle/`verify=False`/debug, XML only through defusedxml, auto-escaping, CSPRNG tokens, constant-time password check, an unprivileged read-only container, bandit (when installed) |
| `functional/test_app_process.py` | the real program as a separate process: admin page and web app on their own ports → photo on the "phone" → button tap → confirmation → clean shutdown on SIGTERM |

Shared helpers are in `tests/helpers.py`:

- `make_image` and `make_video` create media with metadata;
- `Clock` is a controllable clock;
- `FakeNtfy` is an in-memory ntfy server for httpx;
- `start_ntfy` launches the real ntfy binary;
- `source_fakes.Remotes` plays an Immich server (API key, albums, `search/metadata`, previews and
  originals, an older API) and a Nextcloud WebDAV server (PROPFIND with Depth 1, etags);
- `a11y.audit` runs axe-core through Playwright, with the pages served by a TestClient;
- `channel_fakes.Services` plays the Telegram Bot API, signal-cli-rest-api, a Matrix homeserver and
  Discord webhooks through an `httpx.MockTransport`, with their real error replies.

## Code layout

```
src/backinframe/
  config.py     environment → Settings (validated)
  db.py         SQLite schema and queries
  scanner.py    folder (and synced sources) → media table
  sources.py    Immich and Nextcloud → <data>/sources
  review.py     the year review: picks per recipient and the collage
  insights.py   the hearts overview: loved, per album, per person, never loved
  selector.py   weighted choice
  scheduler.py  send moments
  media.py      resizing and transcoding
  ntfy.py       HTTP client
  channels.py   e-mail, Telegram, Signal, Matrix, Discord
  webpush.py    VAPID and encrypted Web Push
  app.py        the recipient web app
  control.py    button commands
  caption.py    title and text
  i18n.py       translations, plurals and dates; texts in locales/<lang>.json
  service.py    ties it together, main loop
  web.py        admin page (FastAPI + Jinja templates in templates/)
  __main__.py   entry point (`backinframe` / `python -m backinframe`)
```

## Conventions

- **Lint:** `ruff check --select F,E,W,B,I --line-length 120 src tests`. CI runs this.
- **Testable time and randomness:** time comes from the injected clock (`BackInFrame(clock=…)`),
  randomness from the injected `random.Random`. Code that does this stays deterministic in tests.
- **Every user-facing string goes in `locales/en.json`** and in each other language; a test checks
  they are complete and keep their `{placeholders}`. Counted texts get `_one`/`_other` keys (and
  `_few`/`_many` for Russian and Polish).
- **A new channel** is a `Channel` subclass in `channels.py` (`check_address`, `send`, and `poll`
  if it can talk back), a few settings in `config.py`, a line in `BackInFrame.channels`, its
  labels and help text in the locales, and tests against a fake in `tests/channel_fakes.py`.
- **A new photo source** is a `Source` subclass in `sources.py` (`list` and `fetch`; `sync` does
  the bookkeeping), a name in `sources.NAMES`, settings in `config.py` and a fake in
  `tests/source_fakes.py`.
- **Accessibility:** a new page goes into `tests/functional/test_accessibility.py`. Use the CSS
  variables for colours (they pass WCAG AA in light and dark), give every field a label, and keep
  tap targets at least 24 px.

## Releasing

1. Update the version in `pyproject.toml` and `src/backinframe/__init__.py`, and add a
   `CHANGELOG.md` entry.
2. Tag the release: `git tag v0.2.0 && git push --tags`.
3. CI runs the tests, then builds and pushes `ghcr.io/<owner>/backinframe:<version>` and `:latest`
   for amd64 and arm64.
4. Paste the changelog entry into the GitHub release. The texts in `publish/` help here.

## Publishing for the first time

The code is not on GitHub yet. From the unpacked `backinframe` folder:

```bash
git init -b main
git add -A
git commit -m "Back in Frame 0.1.0"
gh repo create backinframe/backinframe --public --source . --push   # or create it on github.com and: git remote add origin … && git push -u origin main
git tag -a v0.1.0 -m "Back in Frame 0.1.0"
git push origin v0.1.0
```

Pushing the tag starts CI: tests, the security job, then the image is built for amd64 and arm64
and pushed to `ghcr.io/backinframe/backinframe` (tags `0.1.0` and `latest`). After the
first push, make the package public under *Packages → backinframe → Package settings*. The
hosted version pins `backinframe @ git+https://github.com/backinframe/backinframe@v0.1.0`, so
tag the core before you build the cloud image. The four-month clock of awesome-selfhosted starts
at this tag.
