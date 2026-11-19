# Changelog

## 0.1.0 — 2026-11-15

First release.

- Admin page login with a login screen instead of the browser's password box, server-side
  sessions, and optional two-step verification (authenticator app, recovery codes,
  `backinframe reset-2fa` for a lost phone). Docs for putting the admin page behind Authelia,
  Authentik or Keycloak.
- Security checks after OWASP ASVS 5.0 and the OWASP Top 10 (2025), run with every test run
  (`pytest -m security`) and in CI with pip-audit, bandit, gitleaks and Trivy; see
  docs/security.md. Fixed on the way: security headers and no OpenAPI schema on the admin page, a
  throttle on admin password guesses, no inline script (strict CSP), cross-site checks that also
  use `Sec-Fetch-Site`, Nextcloud XML through defusedxml, decompression bombs skipped, and the
  compose files run the container read-only without capabilities.

- Photos from Immich (albums, through an API key with read rights only) and Nextcloud (a folder,
  through WebDAV with an app password), next to or instead of the photo folder. Copies are kept up
  to date every `SYNC_MINUTES`; the admin page shows the last sync and has a *Sync now* button.
- A step-by-step guide for the iPhone home screen, chosen by iOS version and browser (iOS 26,
  16.4–18, Chrome/Firefox/Edge, links opened inside other apps, too old), with an animation that
  can be paused or stepped through and stands still with *Reduce Motion*, and a *Copy link* button.
- Accessibility: WCAG 2.2 AA contrast in light and dark mode, visible keyboard focus, a skip link,
  labelled fields, no sideways scrolling at 320 px; checked with axe-core in the test suite.
- Sharing the work: "from …" per album or photo in every memory; upload links, so someone without
  access to the server adds photos to one album from their phone (checked, size-limited, never
  overwriting, revocable; uploaded photos can be removed again); and more admins, invited with a
  one-time link, each with their own password and optional two-step verification.
- Caption per photo: under *Photos* the admin can give a photo its own title (instead of the album
  name), a corrected date (kept over rescans, with the file's date to go back to) and a short note
  under the date. Used in every channel and in the web app.
- Hearts: recipients in the web app can put a heart on a memory (they are told the sender sees
  it). A photo with a heart comes back 1.5 times as often. The admin page has a *Hearts* page:
  the most loved photos, the share per album and per person, and photos sent three times or more
  that never got a heart. `BACKINFRAME_REACTIONS=false` turns it off.
- Year review: in the first two weeks of January every recipient gets one review of the past
  year, once, instead of a memory: a gallery page in the web app, a collage (no metadata) on the
  other channels. At least six memories are needed. The admin page shows it per recipient and can
  send it by hand; it can be turned off per recipient or with `BACKINFRAME_YEAR_REVIEW=false`.

- The Back in Frame web app for recipients: a personal link that installs to the home screen, with
  end-to-end encrypted Web Push notifications (VAPID, RFC 8291), a gallery of the last 60
  memories and the rhythm buttons. Runs on its own port, so the admin page stays private.
- Or delivery through ntfy (public ntfy.sh without open ports, or your own server).
- Or by e-mail (SMTP), Telegram (a bot, with buttons), Signal (signal-cli-rest-api, with text
  replies), Matrix (a bot account) or Discord (a webhook), chosen per recipient. Each channel is
  off until configured. Button presses are only accepted from the chat they were sent to.
- Eleven languages: English, Dutch, German, French, Spanish, Italian, Portuguese, Polish,
  Russian, Chinese and Japanese, with proper dates and plural forms. Each recipient can have
  their own language; text replies like "more" are understood in all of them.
- Sends photos and videos from a folder to phones, with a caption
  (folder name, date, "x years ago") and the buttons More, Less and Pause 1 week.
- Per-recipient schedule: photos per day, every N days, time window and minimum gap. Moments are
  random but fixed per day, and keep their local time across summer-time changes.
- Smart choice: photos from this day in earlier years get priority, there is a cool-down for
  recently sent items, and every folder gets a fair share.
- Photos are resized for phones; videos are transcoded to fit the server's attachment limit,
  which is detected automatically.
- HEIC/HEIF support for iPhone photos.
- Privacy by design: only the capture date is read from your files (never the location), and
  location and other metadata are stripped from photos and videos before sending; only the capture
  date is kept, so saved photos land on the right day. No telemetry, no AI.
- The QR code is an `ntfy://` deep link: on Android one scan subscribes on the right server.
- Admin page in eleven languages, with a QR code per recipient, an optional password and a
  "send now" button.
- Works with the public ntfy.sh (no open ports needed) or your own ntfy server.
- Docker image (amd64/arm64), Compose files and Unraid instructions.
- 428 tests, including end-to-end tests against a real ntfy server, a real SMTP server and a real browser, and accessibility checks with axe-core.
