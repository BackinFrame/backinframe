# Back in Frame 0.1.0 — first release

We take hundreds of photos on every trip and then never look at them again. Back in Frame sends
one of them to the people you choose now and then, as a notification with the photo in it:
*"Chile 2025 — 7 November 2025 · Today, 1 year ago."*

## What's in it

**Delivery, per recipient**
- The Back in Frame web app: a personal link that installs to the home screen, end-to-end
  encrypted push notifications, a gallery of the last 60 memories. Runs on its own port, so the
  admin page stays at home.
- Or ntfy (public ntfy.sh without open ports, or your own server), e-mail, Telegram, Signal,
  Matrix or Discord.

**Choosing and sending**
- Photos from "this day, years ago" first, a cool-down for recent ones, a fair share per album.
- Each recipient has their own rhythm: photos per day, every how many days, a time window. They
  can tap *More*, *Less* or *Pause 1 week* themselves.
- Photos are resized and videos transcoded to fit; location and other metadata are removed. HEIC
  works. No AI, no telemetry.
- Photos from a folder, from Immich albums or from a Nextcloud folder.

**New: hearts and the year review**
- Recipients can put a heart on a photo; it then comes back a bit more often. The *Hearts* page
  shows the most loved photos, the share per album and per person, and photos that were sent
  three times or more without ever getting a heart.
- In the first two weeks of January everyone gets a review of the past year: a page in the web
  app, a collage on the other channels.

**Admin and security**
- Admin page in 11 languages, a QR code and link per recipient, optional password with two-step
  verification, or put it behind Authelia, Authentik or Keycloak.
- Checked against OWASP ASVS 5.0 on every test run; pip-audit, bandit, gitleaks, Trivy and an
  OWASP ZAP baseline scan in CI. WCAG 2.2 AA checked with axe-core on every page.

## Install

```bash
docker run -d --name backinframe -p 8080:8080 -p 8081:8081 \
  -v /path/to/photos:/media:ro -v backinframe-data:/data \
  -e TZ=Europe/Amsterdam -e BACKINFRAME_APP_URL=https://memories.example.com \
  ghcr.io/backinframe/backinframe:0.1.0
```

Then open http://your-server:8080, add a recipient and send them the link. Without
`BACKINFRAME_APP_URL` it works with the ntfy app only. Compose files and an Unraid template are
in the repository; see the [admin guide](https://github.com/backinframe/backinframe/blob/main/docs/admin-guide.md).

Images for `linux/amd64` and `linux/arm64`.

## Docs

[User guide](https://github.com/backinframe/backinframe/blob/main/docs/user-guide.md) ·
[Admin guide](https://github.com/backinframe/backinframe/blob/main/docs/admin-guide.md) ·
[How it works](https://github.com/backinframe/backinframe/blob/main/docs/how-it-works.md) ·
[Security](https://github.com/backinframe/backinframe/blob/main/docs/security.md)

Don't want to run a server? The same, hosted, at https://backinframe.com.

Full list of changes: [CHANGELOG.md](https://github.com/backinframe/backinframe/blob/main/CHANGELOG.md).
