# Back in Frame

**Your best memories, back on your phone.**

https://github.com/user-attachments/assets/bef738e6-7a0f-4fef-885f-2b497709dbf3

[User guide](docs/user-guide.md) · [Admin guide](docs/admin-guide.md) · [How it works](docs/how-it-works.md) · [Development](docs/development.md) · [Security](docs/security.md)

You take hundreds of photos (on trips, at parties, of the children growing up) and then hardly
ever look at them again. Back in Frame sends one
of them to the people you choose now and then, as a notification with the photo in it. They can
tap **More**, **Less** or **Pause 1 week** to set their own rhythm.

- **You choose the photos.** Drop them in a folder, or pick albums from **Immich** or a folder in
  **Nextcloud**. Every trip becomes the title of the notification.
- **A bit smart.** Photos from *this day, years ago* get priority, recently sent ones stay away
  for a while, and every trip gets a fair share.
- **Photos and videos.** Photos are resized for phones; videos are re-encoded with ffmpeg when
  needed.
- **Many ways to receive, per recipient.** The **Back in Frame web app**: recipients open a link,
  add it to their home screen and get notifications plus a gallery of everything they received.
  The free **ntfy app**, through the public ntfy.sh server, which needs no open ports at all. Or
  **e-mail, Telegram, Signal, Matrix or Discord**, once you set those up.
- **Eleven languages.** English, Dutch, German, French, Spanish, Italian, Portuguese, Polish,
  Russian, Chinese and Japanese, for the admin page and per recipient.
- **Easy on an iPhone.** The web app shows the exact steps for the recipient's iOS version and
  browser, with a short animation in their own language.
- **Accessible.** Checked automatically against WCAG 2.2 AA: contrast, labels, keyboard use,
  reflow at 320 px, light and dark mode, and *Reduce Motion*.
- **Self-hosted.** One small Docker container, SQLite, no accounts. MIT licensed.
- **Private by design.** No telemetry, no AI in the app, no tracking. Back in Frame never reads the location
  from your photos, strips location and other metadata before anything is sent (only the date
  stays), and web app notifications are end-to-end encrypted.

## Quick start

```bash
mkdir backinframe && cd backinframe
curl -O https://raw.githubusercontent.com/backinframe/backinframe/main/docker-compose.yml
mkdir photos data            # put your photos and videos in ./photos
docker compose up -d
```

Then open `http://<this-machine>:8080` and click **New recipient**.

- **With the web app** (set `BACKINFRAME_APP_URL` to the https address your reverse proxy gives
  port 8081): send the recipient their personal link. They open it, add it to the home screen on
  an iPhone, and tap **Turn on notifications**.
- **With ntfy** (no setup needed): the recipient installs the ntfy app. On Android they scan the
  QR code; on an iPhone they tap **+** and enter the topic shown on the page.

**Send a photo now** lets you test it straight away. Full instructions, including Unraid, the web
app, your own ntfy server and every setting, are in the **[admin guide](docs/admin-guide.md)**.
Send recipients the **[user guide](docs/user-guide.md)**.

## Requirements

- Docker (amd64 or arm64), or Python 3.11+ with ffmpeg.
- Recipients: any recent phone. The web app works on iPhone/iPad from iOS 16.4 and on Android;
  the ntfy app is on Google Play, F-Droid and the App Store.

The interface is in English, Dutch, German, French, Spanish, Italian, Portuguese, Polish, Russian,
Chinese and Japanese (`BACKINFRAME_LANGUAGE`); each recipient can have their own language.
Optional channels need their own service: an SMTP account, a Telegram bot, a
[signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) container, a Matrix bot
account, or a Discord webhook. See the [admin guide](docs/admin-guide.md#5-e-mail-telegram-signal-matrix-and-discord).

## How it was made

Back in Frame was built with the help of an AI coding assistant (Claude, by Anthropic). Code,
tests, documentation, the translations and the website texts were drafted with it; a human
maintainer decides what goes in, reviews it, tests it and publishes it. Every change passes the
same automated tests and security checks ([docs/security.md](docs/security.md)).

The app itself contains no AI and sends nothing to AI services.

The translations have not all been checked by native speakers yet: corrections are very welcome.
Found something odd? Open an issue; for security problems see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
