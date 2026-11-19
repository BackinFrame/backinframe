# How it works

## Overview

```
 photo folder ──scan──▶ SQLite ──choose──▶ resize / transcode ──PUT──▶ ntfy ──push──▶ phone
                          ▲                                                           │
                          └──────── apply command ◀── poll ── control topic ◀── button tap
```

One process runs two things:

- a **main loop** that scans, sends and polls;
- the **admin web page**, built with FastAPI.

Everything is stored in a single SQLite file.

## The main loop

Every 5 seconds, the main loop:

1. **Rescans** the folder if `RESCAN_MINUTES` have passed. The first scan runs at startup.
2. **Polls** the control topics if `POLL_SECONDS` have passed.
3. **Sends** to every active recipient whose next moment has come, then plans their next moment.

Errors are logged and the loop keeps going. If ntfy is unreachable, the send is logged as
`error` and the recipient simply moves on to the next planned moment. Back in Frame never tries to
catch up by sending a burst of photos.

## Scanning

- **Walking the folder:** the scan follows symlinks and skips hidden and system folders.
- **Changes:** a file counts as changed when its size or modification time changes. Unchanged
  files are not read again, so rescans of large collections are cheap.
- **Photo metadata:** only `DateTimeOriginal` (falling back to `DateTime`) is read from EXIF.
  Location, camera model and other details are not read and not stored.
- **Video metadata:** `creation_time` is read with ffprobe.
- **Deleted files** are marked *missing*; they are not removed from the database, so their send
  history survives if they come back.
- **Immich and Nextcloud** (`sources.py`): a sync (every `SYNC_MINUTES`, in its own thread) copies
  the chosen albums or folder into `<data>/sources/<name>/<album>/`, with a small manifest so only
  changed files are downloaded again (Immich checksum, WebDAV etag). Downloads go to a temporary
  file first; a failed download keeps the old copy. The date travels as the file's modification
  time, so Immich previews (which have no EXIF) still count for *this day, years ago*. The scan
  then reads those folders too and stores their paths as `@immich/<album>/<file>`; the album is
  the folder after that prefix.

## Choosing a photo

Each available item gets a weight. Then one is drawn at random according to the weights:

```
weight = 1 / √(number of items in the same folder)
       × 6                       if taken within ±3 days of today's date in an earlier year
       / (1 + times already sent to this recipient)
       × 0                       if sent to this recipient within the cool-down period
```

- **Cool-down:** about half of the time it takes to go through the whole collection at this
  recipient's rhythm, capped at 90 days. With 200 photos and 1 a day that is 90 days; with 20
  photos and 1 a day it is 10 days. If every item is cooling down (a tiny collection), the
  cool-down is ignored rather than sending nothing.
- **Per folder:** the square root means a trip with 400 photos gets √400 = 20 times the chance
  of a single photo, not 400 times. Big trips still come up more often, but small ones are
  never drowned out.
- **Bad files:** if the chosen file cannot be prepared (corrupt, too large to compress, deleted
  meanwhile), it is marked and another one is drawn, up to 5 attempts per send.

## Planning send moments

Per recipient there are four settings: `per_day`, `every_days`, a time window and `min_gap`.

1. **Which days:** a day is a send day when the number of days since the recipient was created
   is a multiple of `every_days`.
2. **Dividing the window:** on a send day, the window is cut into `per_day` equal segments.
3. **Placing each photo:** each photo is placed at the centre of its segment, then moved by a
   random jitter of at most `(segment − min_gap) / 2` in either direction. Two neighbours can
   therefore never be closer than `min_gap`. Validation guarantees that the gaps fit in the
   window.
4. **Fixed plan:** the random generator is seeded with the recipient, the date and the current
   settings. The plan for a day is fixed: a restart gives the same moments, and only a settings
   change gives a new plan.
5. **Wall-clock times:** times are local times in the configured time zone, so 09:00 stays
   09:00 across summer-time changes.

## Preparing media

- **Metadata:** everything a camera or phone writes into a file (location, camera model,
  orientation flags, titles) is removed before sending. Only the capture date is written back, so
  a saved photo or video lands on the right day in the recipient's gallery.
- **Photos:**
  - EXIF orientation is applied, and transparency is flattened onto white;
  - the photo is scaled down to `IMAGE_MAX_EDGE` and encoded as progressive JPEG at quality 85;
  - if the result is still over the limit, the quality goes down to 65, then the size shrinks in
    25 % steps (down to 480 px);
  - if it still does not fit, the photo is skipped.
- **Videos:**
  - MP4, M4V and MOV files under the limit are not re-encoded: video and audio are copied into a
    clean container (without metadata, rotation kept); without ffmpeg they are sent as they are;
  - other videos are transcoded to H.264/AAC MP4, at most 1280 px on the long side;
  - the bitrate is computed from the limit and the duration (90 % of the budget, 96 kbit/s for
    audio), with a second try at 75 %;
  - if the video bitrate would drop below 250 kbit/s, the video is too long for the limit and is
    skipped.
- **The limit** is the smaller of the server's `attachment_file_size` (from `GET /v1/account`)
  and, for videos, `VIDEO_MAX_MB`. If the server answers *413 too large*, the cached limit is
  halved.

## Talking to ntfy

- **Publishing:** `PUT /<topic>` with the file as the body. Title, message, file name and actions
  go in query parameters, which (unlike HTTP headers) handle non-ASCII text such as accents and
  emoji.
- **Buttons:** each button is an ntfy `http` action:
  `http, More, https://ntfy.sh/<control-topic>, method=POST, body=more, clear=true`.
  When it is tapped, the ntfy app on the phone sends the POST directly. Back in Frame is not involved
  and needs no reachable address.
- **Reading taps:** every `POLL_SECONDS`, `GET /<ctl1>,<ctl2>,…/json?poll=1&since=<time>` fetches
  new messages on all control topics in one request.
  - The newest time seen is stored; on first start, polling begins at the creation time of the
    oldest recipient.
  - Message IDs are remembered for two days, so a tap is never applied twice, even after a
    restart.
- **Confirmation:** after applying a command, a short text message is sent to the recipient's
  topic.
- **Subscribing:** the QR code on the recipient page holds an
  [`ntfy://` deep link](https://docs.ntfy.sh/subscribe/phone/#ntfy-links)
  (`ntfy://<server>/<topic>?display=Back in Frame`). Android opens the app with it and subscribes on
  the right server in one go. The iPhone app does not handle these links, so iPhone users enter the
  server and topic by hand.

## The web app

The recipient web app runs as a second FastAPI app on its own port (`BACKINFRAME_APP_PORT`,
8081), so the admin page never has to face the internet. Both share the scheduler, the selector
and the database.

- **Pages:** `/a/<secret>/` (gallery, notification switch, rhythm buttons), its manifest, a
  service worker scoped to that path, icons, and `/a/<secret>/media/<send>` for the photos. The
  secret is 32 random bytes. A wrong secret, or a recipient on ntfy, gets a 404.
- **Sending:** the photo is prepared exactly as for ntfy (resized, metadata stripped, only the date
  kept), stored in `/data/cache`, logged as a send, and announced with Web Push to every device of
  the recipient. The push message holds the caption and the link; the photo itself is loaded from
  your server.
- **The iPhone guide:** on iOS outside the home screen, `app.js` picks one of five flows from the
  user agent: iOS 26 Safari (which reports iOS 18.6, so `Version/26` decides: ••• → Share → Add to
  Home Screen → *Open as Web App*), Safari 16.4–18 (Share → Add to Home Screen → Add), Chrome,
  Firefox or Edge on iOS, a link opened inside another app (open in Safari first, *Copy link*), and
  iOS before 16.4 (no web push at all). The steps are text, in the recipient's language; the SVG
  animation beside them is drawn from the same labels, can be paused and stepped through, and stays
  on step 1 with *Reduce Motion*.
- **Web Push:** [`webpush.py`](../src/backinframe/webpush.py) implements VAPID (RFC 8292,
  ES256-signed JWT) and payload encryption (RFC 8291, aes128gcm) on top of `cryptography` and
  `http-ece`. The VAPID key is created once in `/data`.
- **Safety:** subscriptions are only accepted for the push services of Apple, Google, Mozilla
  and Microsoft. Responses carry `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex`, a strict
  CSP (`script-src 'self'`, no inline script) and `Cache-Control: no-store`; posts from other
  origins are refused.
- **Cache:** prepared files of media that are no longer in anyone's last 60 are removed at each
  scan. A missing file is prepared again on request.

## Other channels and languages

[`channels.py`](../src/backinframe/channels.py) holds one small class per extra channel:
`EmailChannel` (SMTP, photo inline in the HTML part, videos attached), `TelegramChannel` (Bot API:
`sendPhoto`/`sendVideo` with an inline keyboard, `getUpdates` for presses and messages),
`SignalChannel` (signal-cli-rest-api: `POST /v2/send` with a base64 attachment, `GET
/v1/receive/<number>` for replies), `MatrixChannel` (join the room once, upload to the media
repository, post `m.image`/`m.video`) and `DiscordChannel` (a multipart post to the webhook,
mentions disabled). They share one interface: `check_address`, `send(address, Message)`,
`poll()` for channels that can talk back, and `info()` for the admin page. The hosted version
uses the same classes.

- **Limits:** every channel has its own maximum photo and video size; the media is prepared to
  fit, or a video is skipped.
- **Buttons and replies:** a Telegram button carries `command:recipient-id` and is only applied
  when it comes from the chat that recipient is registered at. Text replies (Signal, Telegram)
  apply to the recipients registered at that exact address. `/start` and `/id` answer with the
  chat ID. The Telegram update offset is stored, so a press is never applied twice.
- **Errors:** a send that fails is logged as an `error` with the reason (`bot was blocked`,
  `Unregistered user`, `M_FORBIDDEN`, …). The token of a bot or webhook is never put in a log line.
- **Languages:** [`locales/`](../src/backinframe/locales) has one JSON file per language with
  every text, the month names as used in a date, the date pattern, and plural forms
  (`_one`/`_few`/`_many`/`_other`, chosen with the CLDR rules for Russian and Polish). A missing
  text falls back to English. The admin page uses `BACKINFRAME_LANGUAGE`; everything a recipient
  sees uses their own language when it is set.

## Data model

| Table | Content |
|---|---|
| `media` | relative path, kind, size, mtime, taken_at, missing/broken flags. No location. |
| `recipients` | name, delivery (`app` / `ntfy` / `email` / `telegram` / `signal` / `matrix` / `discord`), address for that channel, language, app link secret, topic, control topic, schedule, pause, next send moment |
| `sends` | every attempt: when, which item, `ok` / `error` / `skipped`, detail |
| `state` | last scan, poll position, Telegram update offset |
| `push_subscriptions` | per web app device: push-service endpoint and its two public keys (max. 10 per recipient; removed when the service says the device is gone) |
| `processed_controls` | IDs of handled button taps (pruned after 2 days) |

## Privacy and security model

- **No accounts, no phone numbers, no third-party SDKs.** The phone side is the web app (plain HTML
  and one small script) or the standard ntfy app.
- **No telemetry, no AI.** Back in Frame never contacts anyone but your ntfy server, for the web
  app the push services of the recipients' devices, and the channels you set up yourself. Choosing a photo is
  a plain rule on dates and folders; nothing looks at what is in your photos.
- **Topics as capabilities:** each topic is `backinframe-` plus 24 random bytes (URL-safe base64).
  The main topic lets you read the photos; the control topic lets you change the rhythm.
- **What ntfy sees:** the resized media, the caption and the topic names. On ntfy.sh, attachments
  are deleted when they expire. On your own server, nothing leaves your hardware, apart from the
  optional content-free iOS push relay.
- **Admin page:**
  - optional login (password, server-side sessions, optional two-step verification);
  - same-origin check on form posts;
  - `/healthz` is the only route that never needs the password.
