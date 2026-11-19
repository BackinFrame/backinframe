# Admin guide

Everything you need to install, configure, run and troubleshoot Back in Frame.

- [1. Choose a setup](#1-choose-a-setup)
- [2. Install with Docker Compose](#2-install-with-docker-compose)
- [3. Install on Unraid](#3-install-on-unraid)
- [4. The recipient web app](#4-the-recipient-web-app)
- [5. E-mail, Telegram, Signal, Matrix and Discord](#5-e-mail-telegram-signal-matrix-and-discord)
- [6. Your own ntfy server](#6-your-own-ntfy-server)
- [7. The photo folder](#7-the-photo-folder)
- [8. Recipients](#8-recipients)
- [9. Configuration reference](#9-configuration-reference)
- [10. Security](#10-security)
- [11. Backup, updates and monitoring](#11-backup-updates-and-monitoring)
- [12. Troubleshooting](#12-troubleshooting)
- [13. Running without Docker](#13-running-without-docker)

## 1. Choose a setup

Recipients get their memories in one of these ways. You choose per recipient, and you can
switch at any time. The web app and ntfy are explained here; e-mail, Telegram, Signal, Matrix
and Discord in [section 5](#5-e-mail-telegram-signal-matrix-and-discord).

| | **Web app** (recommended) | **ntfy app** |
|---|---|---|
| What the recipient installs | nothing: they open a link and, on an iPhone, add it to the home screen | the free ntfy app, then subscribe to a topic |
| What they see | a notification, plus a gallery of everything they received | a notification with the photo attached |
| What you need | an https address for port 8081 (reverse proxy or tunnel) | nothing: works through the public ntfy.sh without open ports |
| Where photos pass through | only your server; the push message itself is end-to-end encrypted | briefly stored on ntfy.sh, or your own ntfy server |

Without `BACKINFRAME_APP_URL` the web app is off and every recipient uses ntfy, so you can
start without any networking and add the web app later.

For ntfy there are two setups:

| | **Easy (default)** | **Private** |
|---|---|---|
| Delivery through | public `ntfy.sh` | your own ntfy server |
| Open ports, domain, certificate | none | an HTTPS address for ntfy that phones can reach |
| Where photos pass through | briefly stored on ntfy.sh until the attachment expires | only your own hardware |
| Maximum attachment | the free-tier limit of ntfy.sh (Back in Frame asks for it and adapts) | what you configure, e.g. 50 MB |
| Videos | only if they can be compressed under the limit | up to 50 MB |
| Compose file | `docker-compose.yml` | `docker-compose.own-ntfy.yml` |

You can switch later. The topics stay the same, but recipients must re-subscribe on the new
server.

## 2. Install with Docker Compose

```bash
mkdir backinframe && cd backinframe
curl -O https://raw.githubusercontent.com/backinframe/backinframe/main/docker-compose.yml
mkdir photos data
docker compose up -d
docker compose logs -f        # optional: watch it start
```

Open `http://<host>:8080`. The first scan of the folder runs at startup and then every hour; the
**Scan again now** button starts one immediately.

Edit `docker-compose.yml` to set:
- your time zone (`TZ`);
- the language (`BACKINFRAME_LANGUAGE`: `en` or `nl`);
- the web app address (`BACKINFRAME_APP_URL`, see §4).
- an admin password (`BACKINFRAME_ADMIN_PASSWORD`).

The photo folder is mounted **read-only**: Back in Frame never changes your files.

## 3. Install on Unraid

Use the template in `publish/unraid/templates/backinframe.xml`, or add the container by hand
(*Docker → Add Container*):

| Field | Value |
|---|---|
| Repository | `ghcr.io/backinframe/backinframe:latest` |
| Network type | bridge |
| Port | container `8080` → host `8080` (admin page) |
| Port | container `8081` → host `8081` (web app, optional) |
| Path `/media` | your photo share, e.g. `/mnt/user/photos/backinframe`. Access mode: **Read Only** |
| Path `/data` | `/mnt/user/appdata/backinframe` |
| Variable `TZ` | `Europe/Amsterdam` |
| Variable `BACKINFRAME_LANGUAGE` | `en` or `nl` |
| Variable `BACKINFRAME_APP_URL` | optional: the https address of the web app (§4) |
| Variable `BACKINFRAME_ADMIN_PASSWORD` | optional |
| Extra parameters | `--user 99:100` (nobody:users, so it can read the share and write appdata) |

The WebUI link opens the admin page.

## 4. The recipient web app

The web app is a small website on port **8081** that recipients add to their home screen. It
shows a notification for every memory and keeps a gallery of the last 60 they received. It uses
Web Push, which works on Android, desktop browsers and iPhone/iPad (iOS 16.4 and later, once the
page is on the home screen).

1. **Give port 8081 an https address.** Phones only allow notifications over https. Use your
   reverse proxy, for example Caddy:

   ```caddy
   memories.example.com {
       reverse_proxy 127.0.0.1:8081
   }
   ```

   A Cloudflare Tunnel or Tailscale Funnel works too. Only port 8081 faces the internet; the
   admin page on 8080 stays on your own network. Port 8081 serves nothing but `/a/<secret>`
   pages and `/healthz`.
2. **Set `BACKINFRAME_APP_URL=https://memories.example.com`** and restart. New recipients
   now use the web app; existing ones can be switched on their page under *Delivery*.
3. **Send the link.** The recipient page shows the personal link and a QR code. The recipient
   opens it and taps *Turn on notifications*. On an iPhone they first tap *Share → Add to Home
   Screen* and open Back in Frame from there; the page explains this.

What to know:

- **The link is the key.** It holds 256 random bits. Anyone with it sees that recipient's
  photos, so share it only with them. Switching a recipient to ntfy closes their link.
- **Push messages are end-to-end encrypted** (RFC 8291). Apple, Google or Mozilla deliver them
  but only see ciphertext. The photo itself is loaded from your server, never from theirs.
- **The push key** (`vapid_private.pem`) is created in `/data` on first start. Keep it with the
  database: a new key means every device has to turn notifications on again.
- **Prepared photos** (resized, without location) are cached in `/data/cache` for the galleries
  and cleaned up automatically.
- Optional: `BACKINFRAME_VAPID_SUBJECT=mailto:you@example.com` tells push services whom to
  contact about your server. By default the app address is used.

## 5. E-mail, Telegram, Signal, Matrix and Discord

Besides the web app and ntfy, Back in Frame can deliver to five other places. Each one is
**off until you fill in its settings**; a channel that is set up appears in the *Delivery* menu
of every recipient. You install and run the services yourself: Back in Frame only talks to
them. Every recipient has one **address** for their channel.

| Channel | You set up | The recipient's address | Buttons |
|---|---|---|---|
| E-mail | an SMTP account | an e-mail address | no: change the rhythm on the admin page |
| Telegram | a bot (free, 2 minutes) | their chat ID; the bot tells them | More / Less / Pause under each photo, and text replies |
| Signal | a Signal number in [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) | their phone number, `+31…` | text replies: *more*, *less*, *pause*, *resume* |
| Matrix | a bot account on any homeserver | a room ID, `!abc:example.org` | no |
| Discord | nothing | a channel webhook URL | no |

What goes out is the same everywhere: the resized photo or video without location, the album
name and the date. With these channels the photo passes through that service (the recipient's
mail provider, Telegram, Signal, the Matrix homeserver, Discord), under its own privacy terms.
Tell your recipients; the web app and your own ntfy server keep everything on your hardware.

Replies like *more* are understood in all eleven languages (*vaker*, *mehr*, *plus*, *más*,
*più*, *mais*, *więcej*, *чаще*, *多一些*, *もっと*, …), with or without a leading `/`.

### E-mail

```yaml
BACKINFRAME_SMTP_HOST: smtp.example.org
BACKINFRAME_SMTP_PORT: "587"           # 465 with SMTP_SECURITY=ssl
BACKINFRAME_SMTP_SECURITY: starttls    # starttls | ssl | none
BACKINFRAME_SMTP_USER: photos@example.org
BACKINFRAME_SMTP_PASSWORD: "…"
BACKINFRAME_SMTP_FROM: "Back in Frame <photos@example.org>"
BACKINFRAME_EMAIL_MAX_MB: "15"         # larger videos are compressed or skipped
```

Photos are in the message itself, videos attached. Each mail ends with a line explaining why
the recipient gets it. Use a sender address with SPF/DKIM, or the mails land in spam.

### Telegram

1. In Telegram, talk to **@BotFather**, send `/newbot`, pick a name. You get a token like
   `123456:ABC-…`.
2. Set `BACKINFRAME_TELEGRAM_BOT_TOKEN` to it and restart.
3. The recipient opens your bot (`t.me/<botname>`) and taps **Start**. The bot answers with
   their chat ID. Enter that as the address. For a group: add the bot to the group and send
   `/id` there; group IDs are negative numbers.

Back in Frame fetches button taps and messages every `POLL_SECONDS` (no webhook, no open
port). A button only works in the chat it was sent to.

### Signal

Signal has no official bot API. Run [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)
next to Back in Frame, with its own phone number (a cheap prepaid SIM or a landline that can
receive a call), or linked to your own Signal app as an extra device:

```yaml
  signal:
    image: bbernhard/signal-cli-rest-api:latest
    environment:
      MODE: native               # 'json-rpc' does not support the receive call Back in Frame uses
    volumes:
      - ./signal:/home/.local/share/signal-cli
    ports:
      - "127.0.0.1:8095:8080"    # only to link the device once; Back in Frame uses the network name
    restart: unless-stopped
```

Link it: open `http://localhost:8095/v1/qrcodelink?device_name=backinframe` and scan the QR
code in Signal → Settings → Linked devices. Then:

```yaml
BACKINFRAME_SIGNAL_URL: http://signal:8080
BACKINFRAME_SIGNAL_NUMBER: "+31612345678"   # the number signal-cli uses
```

Recipients reply with *more*, *less*, *pause* or *resume*. Replies in groups are ignored.

### Matrix

1. Create an account for the bot on a homeserver, for example `@memories:example.org`.
2. Get its access token: log in once with Element and copy it from Settings → Help & About →
   Access token, then log out of Element *without* signing out the session (close the tab), or
   use the login API.
3. Set `BACKINFRAME_MATRIX_HOMESERVER` (`https://matrix.example.org`) and
   `BACKINFRAME_MATRIX_TOKEN`.
4. Invite the bot to a room with the recipient and enter the room ID (room settings → Advanced).
   The bot accepts the invitation at the first send. Encrypted rooms are not supported: use an
   unencrypted room, or the web app for full end-to-end encryption.

### Discord

Nothing to set up on the server (`BACKINFRAME_DISCORD_ENABLED=false` hides it). Make a webhook
in the Discord channel (channel settings → Integrations → Webhooks → New webhook → Copy webhook
URL) and enter that URL as the address. Everyone in that channel sees the photos; Discord takes
up to 10 MB per message. Back in Frame only ever posts to `discord.com`, whatever is stored.

### Languages

The admin page speaks the language of `BACKINFRAME_LANGUAGE`: `en`, `nl`, `de`, `fr`, `es`,
`it`, `pt`, `pl`, `ru`, `zh` or `ja`. Each recipient can have their own language (*Language* on
the recipient page); captions, dates, buttons, replies, e-mails and the web app then use that
one.

## 6. Your own ntfy server

Use `docker-compose.own-ntfy.yml`. It runs ntfy next to Back in Frame.

1. **Choose a public address**, e.g. `https://ntfy.example.org`, and point your reverse proxy at
   port `8081` of the host. Caddy example:

   ```caddy
   ntfy.example.org {
       reverse_proxy 127.0.0.1:8081
   }
   ```

2. **Set the address twice in the compose file:** `NTFY_BASE_URL` in the ntfy service (needed
   for attachment links) and `BACKINFRAME_NTFY_PUBLIC_URL` in the Back in Frame service (used for the
   QR code and the buttons). Back in Frame itself talks to ntfy over the internal Docker network
   (`BACKINFRAME_NTFY_URL=http://ntfy:80`).

3. **iPhone push.** Apple only delivers push notifications through its own service. With
   `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh`, your server sends a small content-free "poll"
   signal through ntfy.sh, and the iPhone then fetches the message from *your* server. Without
   it, iPhones only get messages when the app is opened. Android does not need this.

4. **Limits.** The file sets attachments to 50 MB, 2 GB in total, kept for 72 hours. Back in Frame
   reads the limit from the server automatically.

5. **Access control (optional).** If you lock ntfy down (`NTFY_AUTH_DEFAULT_ACCESS=deny-all`):
   - create a user for Back in Frame with write access to `backinframe-*` and read access to
     `backinframe-ctl-*`, and set `BACKINFRAME_NTFY_TOKEN`;
   - recipients then need read access to their own topic and write access to their control topic.

   The open default (secret, unguessable topic names) is simpler and is what most people use.

A VPN instead of a public address (Tailscale, Headscale, WireGuard) also works, as long as every
recipient's phone is on that VPN.

## 7. The photo folder

- **Supported types:** JPEG, PNG, WebP, TIFF, HEIC/HEIF (iPhone), and the video formats MP4,
  MOV, M4V, 3GP, MKV, WebM, AVI and MTS.
- **Subfolders** become the notification title: `Chile 2026/Santiago/IMG_1234.jpg` gets the title
  *Santiago*. Underscores become spaces.
- **Ignored:**
  - hidden files and folders (starting with `.`);
  - `@eaDir` (Synology), `#recycle`, `.thumbnails`, `__MACOSX`;
  - anything that is not media.
- **Dates:**
  - photos: EXIF *DateTimeOriginal*;
  - videos: the `creation_time` tag;
  - otherwise the file's modification time. Copying files can change that time, so prefer formats
    that keep metadata.
- **Changes are picked up** on the next scan: added files, edited files, and deleted files
  (which are no longer sent). Restored files come back.
- **Symlinks** are followed, so you can link to folders elsewhere on the same share.
- **Files that cannot be read or made small enough** are marked and skipped. They appear as
  `skipped` in the recipient's history.

### Photos from Immich and Nextcloud

Instead of (or next to) the photo folder, Back in Frame can take albums from **Immich** and a
folder from **Nextcloud**. It only reads; nothing is ever changed or uploaded there.

How it works: every `SYNC_MINUTES` (default 60) Back in Frame copies what changed into
`<data folder>/sources/immich/<album>/` and `<data folder>/sources/nextcloud/<subfolder>/`, and
removes what is gone. Those copies count like the photo folder: each Immich album and each
Nextcloud subfolder is an album, with its name as the notification title. The admin page shows
the last sync per source and has a **Sync now** button. If a server cannot be reached, the copies
stay and sending goes on; the error is shown on the admin page.

**Immich**

1. In Immich: *Account settings → API keys → New API key*. Give it only these permissions:
   `album.read`, `asset.read`, `asset.view` and `asset.download`.
2. Set:

   ```yaml
   BACKINFRAME_IMMICH_URL: https://immich.example.org     # or http://immich-server:2283
   BACKINFRAME_IMMICH_API_KEY: <the key>
   BACKINFRAME_IMMICH_ALBUMS: Chile 2025, Rome 2019       # empty = every album the key can see
   ```

   Write a comma inside an album name as `\,`. Albums shared with you count too.
3. Photos come as Immich's preview (1440 px JPEG, without location data), videos as the original
   file; they are made smaller when sent. Videos larger than 4 × `VIDEO_MAX_MB` (at least 200 MB) are
   skipped. The date is the one Immich shows, also for the previews.

**Nextcloud**

1. In Nextcloud: *Personal settings → Security → Devices & sessions → Create new app password*.
   Use that, never your login password.
2. Set:

   ```yaml
   BACKINFRAME_NEXTCLOUD_URL: https://cloud.example.org
   BACKINFRAME_NEXTCLOUD_USER: anna
   BACKINFRAME_NEXTCLOUD_PASSWORD: <the app password>
   BACKINFRAME_NEXTCLOUD_FOLDER: Photos/Back in Frame    # path inside your Nextcloud files
   ```

3. Every subfolder (up to four levels deep) becomes an album; files directly in the folder get the
   folder's name. The files are copied as they are, so the date comes from the photo itself.
   Other WebDAV servers work too: give the full address in `NEXTCLOUD_URL`
   (`https://dav.example.org/remote.php/dav/files/anna`).

**Good to know**

- The copies take disk space in the data folder: for Immich roughly 300 KB per photo, for
  Nextcloud the size of the originals. Removing a source from the settings takes its photos out at
  the next scan; delete `<data folder>/sources/<name>` to free the space.
- A Nextcloud original can contain the location. It stays in the copy on your own disk, is never
  read, and is removed from every photo before it is sent, as with the photo folder.
- A top-level folder in the photo folder named `@immich` or `@nextcloud` is ignored: those names
  are reserved for the copies.

## 8. Recipients

Each recipient gets a secret web app link and two random ntfy topics. *Delivery* decides what is
used: the web app, ntfy, or one of the channels in [section 5](#5-e-mail-telegram-signal-matrix-and-discord)
with its *address*:

- **web app link** (`/a/<secret>`): the gallery, the notifications and the buttons.
- **topic:** with ntfy, the photos arrive here. This is what the phone subscribes to.
- **control topic:** with ntfy, the buttons post here; Back in Frame reads it.

Settings per recipient:

| Setting | Meaning | Range |
|---|---|---|
| Photos per day | how many on a send day | 1–24 |
| Every how many days | 1 = every day, 3 = every third day | 1–60 |
| Between … and … | time window, in `TZ` | must not cross midnight |
| Minimum minutes in between | spacing between photos on the same day | must fit in the window |
| Active | off = nothing is sent, nothing is planned | |
| Delivery and address | web app, ntfy, e-mail, Telegram, Signal, Matrix or Discord, and where to | only channels that are set up |
| Language | captions, buttons, replies and the web app for this recipient | 11 languages, or the server's |

**Pause 1 week** and **Resume** on the admin page do the same as the buttons on the phone. The
recipient gets a confirmation for every change made with the buttons.

### The caption per photo

A memory arrives with the album name (the folder) as title and the capture date with "x years ago"
underneath. Under **Photos** on the admin page you can change that per photo:

- **Title:** replaces the album name for this photo only. Empty: the album name again.
- **Date:** if the date in the file is wrong (a scan, a camera with the wrong clock), set the right
  one. "This day, years ago" uses it too. A rescan keeps your date; the date from the file is
  remembered and *Use the date from the photo again* goes back to it.
- **Note:** one short line under the date, for example who is in it or where it was.

The photo list can be filtered per album and shows which photos have been changed. Changes count
from the next send and show up in the web app galleries at once; notifications already sent stay
as they were. The titles and notes are stored in the database, not in the photo files.

### "From …" and photos from others

Several people often take the photos: the children all send photos of the grandchildren, friends
all have their own pictures of the weekend. Two things help, without anyone needing an account:

- **From:** on the **Photos** page, pick an album and fill in *From (whole album)*, for example
  "Anna". The notification then says "12 March 2025 · 1 year ago · from Anna". It counts for new
  photos in that folder too. A single photo can have its own "from" on its caption page.
- **Upload links:** under **Upload links** you make a link for one person and one album (new or
  existing). They open it on their phone, pick photos or videos, and that is all: they cannot see
  or delete anything. Their name becomes the "from" of their photos. The files go to
  `data/uploads/<album>/` (not into your photo folder, which stays read-only); photos that came in
  this way can be removed on their caption page. Upload links run on the web app port, so they need
  `BACKINFRAME_APP_URL`. Each file is checked (a real photo or video, at most
  `BACKINFRAME_UPLOAD_MAX_MB`), 50 files per go, 60 uploads an hour per link, and at least 1 GB
  stays free on the disk. Stop a link when it is no longer needed.

Someone who is added separately by several people simply gets several links. If that gets too
much, agree that one person manages it (with upload links for the others), or set a calmer rhythm.

### Hearts and the year review

In the web app a recipient can tap the heart under a photo. The web app tells them the sender
sees their hearts. A photo with a heart comes back 1.5 times as often for that recipient. The
**Hearts** page on the admin page shows:

- the photos with the most hearts, and from whom;
- per album and per person: how many memories went out and how many got a heart;
- photos sent three times or more that never got a heart (maybe they can go from the folder).

Only web app recipients can give hearts, so only they are counted. ntfy, e-mail, Telegram, Signal,
Matrix and Discord have no heart button yet.

The **year review** goes out at the first send moment in the first two weeks of January, once per
recipient, instead of a memory: up to 12 varied photos from what that recipient got that year
(hearts first, spread over the months and albums). Web app recipients get a notification that
opens a page with the photos; other channels get one collage image without metadata. With fewer
than six memories that year nothing is sent. The **Year review** page shows it for every recipient
and has a *Send now* button; the recipient page has a box to turn it off for that person.

Deleting a recipient removes its history, its link and its devices. An ntfy phone stays
subscribed, but nothing new arrives.

## 9. Configuration reference

All settings are environment variables.

| Variable | Default | Description |
|---|---|---|
| `BACKINFRAME_MEDIA_DIR` | `/media` | Folder with photos and videos |
| `BACKINFRAME_DATA_DIR` | `/data` | Where `backinframe.sqlite3` lives |
| `BACKINFRAME_NTFY_URL` | `https://ntfy.sh` | ntfy server Back in Frame publishes to and polls |
| `BACKINFRAME_NTFY_PUBLIC_URL` | same as `NTFY_URL` | ntfy address as seen from phones (QR code, buttons) |
| `BACKINFRAME_NTFY_TOKEN` | – | Bearer token if your ntfy requires login |
| `BACKINFRAME_MAX_ATTACHMENT_MB` | `0` = ask server | Force an attachment limit |
| `BACKINFRAME_VIDEO_MAX_MB` | `50` | Upper limit for videos (never above the server limit) |
| `BACKINFRAME_UPLOAD_MAX_MB` | `500` | Largest file someone may send through an upload link (§8) |
| `BACKINFRAME_SEND_VIDEOS` | `true` | `false` sends photos only |
| `BACKINFRAME_IMAGE_MAX_EDGE` | `2048` | Longest side of sent photos, in pixels (≥ 320) |
| `BACKINFRAME_LANGUAGE` | `en` | `en`, `nl`, `de`, `fr`, `es`, `it`, `pt`, `pl`, `ru`, `zh` or `ja`: the admin page, and recipients without their own language |
| `BACKINFRAME_TIMEZONE` | `TZ` or `Europe/Amsterdam` | Time zone of the time windows |
| `BACKINFRAME_ADMIN_PASSWORD` | – | Password for the admin page's login screen; with it you can also turn on two-step verification (§10) |
| `BACKINFRAME_RESCAN_MINUTES` | `60` | Folder scan interval |
| `BACKINFRAME_POLL_SECONDS` | `20` | How often button taps are fetched |
| `BACKINFRAME_WEB_HOST` | `0.0.0.0` | Admin page listen address |
| `BACKINFRAME_WEB_PORT` | `8080` | Admin page port |
| `BACKINFRAME_APP_URL` | – | Public https address of the web app. Empty = web app off |
| `BACKINFRAME_APP_PORT` | `8081` | Web app port (must differ from the admin port) |
| `BACKINFRAME_VAPID_SUBJECT` | the app address | `mailto:` or `https://` contact for push services |
| `BACKINFRAME_SMTP_HOST` | – | Mail server for the e-mail channel. Empty = e-mail off |
| `BACKINFRAME_SMTP_PORT` | `587` | |
| `BACKINFRAME_SMTP_SECURITY` | `starttls` | `starttls`, `ssl` or `none` |
| `BACKINFRAME_SMTP_USER` / `_PASSWORD` | – | Login, if the server needs one |
| `BACKINFRAME_SMTP_FROM` | – | Sender, e.g. `Back in Frame <photos@example.org>` (required with a host) |
| `BACKINFRAME_EMAIL_MAX_MB` | `15` | Largest attachment |
| `BACKINFRAME_TELEGRAM_BOT_TOKEN` | – | From @BotFather. Empty = Telegram off |
| `BACKINFRAME_TELEGRAM_API_URL` | `https://api.telegram.org` | For a local Bot API server |
| `BACKINFRAME_SIGNAL_URL` | – | signal-cli-rest-api, e.g. `http://signal:8080`. Empty = Signal off |
| `BACKINFRAME_SIGNAL_NUMBER` | – | The number registered there, `+31…` |
| `BACKINFRAME_MATRIX_HOMESERVER` | – | e.g. `https://matrix.example.org`. Empty = Matrix off |
| `BACKINFRAME_MATRIX_TOKEN` | – | Access token of the bot account |
| `BACKINFRAME_DISCORD_ENABLED` | `true` | `false` hides Discord |
| `BACKINFRAME_IMMICH_URL` | – | Immich address. Empty = Immich off |
| `BACKINFRAME_IMMICH_API_KEY` | – | API key with `album.read`, `asset.read`, `asset.view`, `asset.download` |
| `BACKINFRAME_IMMICH_ALBUMS` | all | Album names, comma-separated (`\,` for a comma in a name) |
| `BACKINFRAME_NEXTCLOUD_URL` | – | Nextcloud address. Empty = Nextcloud off |
| `BACKINFRAME_NEXTCLOUD_USER` / `_PASSWORD` | – | User name and an app password |
| `BACKINFRAME_NEXTCLOUD_FOLDER` | – | Folder inside the user's files, e.g. `Photos/Back in Frame` |
| `BACKINFRAME_SYNC_MINUTES` | `60` | How often Immich and Nextcloud are checked (≥ 5) |
| `BACKINFRAME_REACTIONS` | `true` | `false` removes the heart button and the *Hearts* page (§8) |
| `BACKINFRAME_YEAR_REVIEW` | `true` | `false` sends no year review in January (§8) |

Invalid values stop the program at startup with a clear message, for example
`Configuration error: Unknown timezone`.

## 10. Security

- **Topic names are secrets.** Each one carries about 190 bits of randomness, so it cannot be
  guessed, but anyone who has it can read the photos (main topic) or change the rhythm
  (control topic).
  - Topics are not written to the logs.
  - Treat the recipient page as sensitive.
- **The admin page** has one login: the admin password. Keep the page on your LAN or VPN, and set
  `BACKINFRAME_ADMIN_PASSWORD` if others can reach it; then turn on two-step verification (below)
  or put it behind your own login. Form posts from other websites are refused.
- **Read-only media.** Mount the photo folder read-only; Back in Frame never needs to write there.
- **Unprivileged.** The container runs as UID 1000 by default, or 99:100 on Unraid. The compose
  files run it read-only (it writes only to `/data` and a temporary `/tmp`), without Linux
  capabilities and with `no-new-privileges`. On Unraid you can add the same in *Extra Parameters*:
  `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`.
- **Password guessing:** after 10 wrong admin passwords in 15 minutes, that address has to wait;
  after 5 wrong codes, the second step waits too.
- **Security headers** on every page (a strict Content Security Policy, no framing, no sniffing,
  HSTS when you use https). What is checked automatically is listed in
  [security.md](security.md).
- **The web app** (if on) only answers `/a/<secret>` and `/healthz`. It sends no referrer, is not
  indexed, refuses posts from other sites, and accepts push subscriptions only from the real push
  services of Apple, Google, Mozilla and Microsoft, so it cannot be used to make your server
  call arbitrary addresses.
- **Channel secrets:** the SMTP password, bot token and Matrix token are only in your
  environment; they are never logged (errors mention the service, not the URL). Recipient
  addresses are as sensitive as topic names: a Discord webhook URL lets anyone post into that
  channel.
- **Immich and Nextcloud** only need read access: give the Immich key just the four permissions
  in [section 7](#photos-from-immich-and-nextcloud) and use a Nextcloud app password, which you can
  revoke on its own. Keys and passwords never appear in errors or on the admin page.
- **Accessibility:** the admin page and the web app are checked automatically against WCAG 2.2
  level AA (contrast, labels, keyboard focus, no sideways scrolling at 320 px), on a phone and a
  desktop, in light and dark mode.
- **Replies are checked:** a Telegram button press is applied only when it comes from the chat
  the photo was sent to; text replies only change recipients registered at that exact address.
- **What leaves your network** (easy ntfy setup):
  - the resized photo or video;
  - the caption (folder name and date);
  - the topic names, sent to ntfy.sh.

  Nothing else does: there is no telemetry.

### Logging in and two-step verification

With `BACKINFRAME_ADMIN_PASSWORD` set, the admin page shows a login screen. The session lasts
14 days and is stored on the server: *Log out* really ends it, and changing the password ends
every session.

Under **Security** (top right) you can turn on **two-step verification**:

1. Choose *Turn on two-step verification* and scan the QR code with an authenticator app
   (Aegis, 2FAS, Google Authenticator, 1Password, Bitwarden and the like).
2. Enter the 6-digit code. You get **10 recovery codes** once: store them in your password manager.
3. From then on the login asks for a code after the password. A code works once; a recovery code
   also works once.

Turning it off needs the password and a code. *Log out everywhere else* ends every other session.
The key for the app is stored encrypted; the encryption key is `data/admin.key`, so keep that file
in your backup together with the database.

**Lost your phone and the recovery codes?** Run on the server:

```bash
docker exec backinframe backinframe reset-2fa
```

That turns two-step verification off and ends every session; log in with the password and turn it
on again.

### More admins

The main admin logs in with `BACKINFRAME_ADMIN_PASSWORD`. Under **Admins** every admin can add
others: fill in a name, and you get a one-time link (valid for 7 days) to send yourself. The new
admin opens it, picks a password (at least 10 characters) and logs in from then on with name and
password. Each admin can turn on two-step verification and change their own password under
**Security**. Any admin can remove another; that ends their sessions straight away. The main admin
cannot be removed. Passwords are stored as scrypt hashes; the invitation link only as a hash.

The link points to the admin page, so it only works where that page can be reached (your network,
a VPN, or behind your proxy login). Without `BACKINFRAME_ADMIN_PASSWORD` there are no separate
admins: then everyone who can reach the page is one.

### Behind your own login (reverse proxy)

Already running Authelia, Authentik or Keycloak? Then let that login guard the admin page, with
its own two-step verification. Two rules:

- **Only the admin page (port 8080) goes behind the login, never the web app (port 8081):**
  recipients open their link without an account.
- **The admin port must not be reachable around the proxy.** Remove `ports: - "8080:8080"` from the
  compose file and let the proxy reach the container over a Docker network. Otherwise anyone on
  the network skips the login.

You can then leave `BACKINFRAME_ADMIN_PASSWORD` empty, or keep it as a second lock.

**Caddy with Authelia** (forward auth):

```caddyfile
memories-admin.example.org {
    forward_auth authelia:9091 {
        uri /api/authz/forward-auth
        copy_headers Remote-User Remote-Groups
    }
    reverse_proxy backinframe:8080
}
```

**Keycloak or Authentik through oauth2-proxy** (OpenID Connect), as a container in front of the
admin page:

```bash
oauth2-proxy \
  --provider=keycloak-oidc --oidc-issuer-url=https://sso.example.org/realms/home \
  --client-id=backinframe --client-secret=… --cookie-secret=… \
  --email-domain='*' --allowed-role=backinframe-admin \
  --upstream=http://backinframe:8080 --http-address=0.0.0.0:4180
```

Point your reverse proxy for the admin host name at `oauth2-proxy:4180`. Authentik also has its own
*proxy provider* that does the same without oauth2-proxy.

## 11. Backup, updates and monitoring

- **Backup:** back up `/data` (the SQLite file, `vapid_private.pem` and `admin.key`; `cache/` may be skipped).
  Losing it means new topics and links, so recipients would have to sign up again.
- **Update:** `docker compose pull && docker compose up -d`. The database and all recipients are kept.
- **Health:**
  - `GET /healthz` returns `{"status":"ok", ...}` with the number of photos and videos, and needs
    no password;
  - the container has a Docker `HEALTHCHECK`;
  - for Uptime Kuma and the like, monitor `http://<host>:8080/healthz`.
- **Logs:** `docker compose logs backinframe`. They show scans, sends, skipped files and button taps.

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| *0 photos* on the admin page | Wrong mount, or the container user cannot read the folder. Check with `docker exec backinframe ls /media`. On Unraid use `--user 99:100`. |
| "No photos found in the folder" when sending | Same as above, or only unsupported formats. |
| Welcome message arrives, photos never do | Look at *Next* on the admin page; it may simply be outside the time window. Use **Send a photo now** to test. |
| The buttons do nothing | The phone cannot reach the address in `BACKINFRAME_NTFY_PUBLIC_URL`, or the control topic is blocked by ntfy access control. Taps are fetched every `POLL_SECONDS`. |
| iPhone gets messages only when the app is open | Own server without `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh`. |
| Videos never arrive | They are larger than the server limit and cannot be compressed under it (too long). See `skipped` in the history. Use your own ntfy server or `BACKINFRAME_SEND_VIDEOS=false`. |
| `attachment too large` in the logs | The server lowered its limit. Back in Frame halves its own limit and retries at the next send. You can also set `BACKINFRAME_MAX_ATTACHMENT_MB`. |
| `429` / `poll failed` in the logs | ntfy rate limit. Do not set `POLL_SECONDS` below about 10 on ntfy.sh. On your own server, exempt Back in Frame with `NTFY_VISITOR_REQUEST_LIMIT_EXEMPT_HOSTS`. |
| Web app: *This browser cannot show notifications* | On an iPhone the page must be opened from the home screen (iOS 16.4+). Elsewhere: an old browser, or a private window. |
| Web app: no notifications although they are on | The device count on the recipient page shows whether a device signed up. Check that `BACKINFRAME_APP_URL` is the exact https address of port 8081, and look for `push failed` in the logs. |
| Web app: the link gives *Not found* | The recipient is set to ntfy, was deleted, or the reverse proxy points at 8080 instead of 8081. |
| Channel missing from the *Delivery* menu | Its settings are incomplete; the log shows a configuration error at startup if one half is missing. Discord is hidden with `DISCORD_ENABLED=false`. |
| Telegram: `Forbidden: bot was blocked by the user` | The recipient blocked or never started the bot. Ask them to tap Start again. |
| Telegram: the bot does not answer /start | A webhook is set on the bot by another program; Back in Frame uses `getUpdates`. Remove it with `https://api.telegram.org/bot<token>/deleteWebhook`. |
| Signal: `Unregistered user` | That number is not on Signal. |
| Signal: replies are ignored | signal-cli-rest-api runs in `json-rpc` mode; use `MODE=native` or `normal`. |
| Matrix: `M_FORBIDDEN` | The bot was not invited, was kicked, or the room is encrypted. |
| Discord: `the webhook no longer exists` | Someone deleted the webhook; make a new one and save it as the address. |
| E-mail lands in spam | Set up SPF and DKIM for the sender domain, or send through your provider's SMTP. |
| Photos arrive at the wrong hour | Check `TZ` / `BACKINFRAME_TIMEZONE`. |
| Wrong date in the caption | The file has no EXIF date and its modification time changed while copying. |

## 13. Running without Docker

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install .                 # needs Python 3.11+, and ffmpeg on PATH for videos
export BACKINFRAME_MEDIA_DIR=~/Pictures/holidays BACKINFRAME_DATA_DIR=~/.local/share/backinframe
backinframe
```

A systemd unit:

```ini
[Unit]
Description=Back in Frame
After=network-online.target

[Service]
Environment=BACKINFRAME_MEDIA_DIR=/srv/photos BACKINFRAME_DATA_DIR=/var/lib/backinframe
ExecStart=/opt/backinframe/.venv/bin/backinframe
DynamicUser=yes
StateDirectory=backinframe
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
