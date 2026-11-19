# Publication texts

Ready-to-paste texts for the places where Back in Frame is published.

---

## GitHub repository

**Description** (About box, max 350 characters):

> Your best memories, back on your phone. Self-hosted service that sends a photo or video from a folder to the people you choose now and then, through its own web app, ntfy, e-mail, Telegram, Signal, Matrix or Discord. Smart choice ("this day, years ago"), adjustable rhythm, no AI, no tracking. One Docker container.

**Website:** https://backinframe.com

**Topics:** `self-hosted` `photos` `pwa` `web-push` `ntfy` `telegram-bot` `signal` `matrix` `memories` `docker` `unraid` `homelab` `python` `fastapi` `privacy` `open-source`

**Social preview text** (image 1280×640): *Back in Frame — your best memories, back on your phone.*

---

## Short description (one line)

Sends a forgotten photo to your phone now and then — self-hosted, private, no AI.

---

## Long description

**Back in Frame — your best memories, back on your phone**

We take hundreds of photos on every trip and then never look at them again. Back in Frame changes
that. Put your favourite photos and videos in a folder, and Back in Frame sends one of them to the
people you choose now and then, as a notification with the picture right in it, together with
where it came from and how long ago it was.

*"Chile 2025 — November 7, 2025 · Today, 1 year ago."*

**Made to be easy**
- One small Docker container. Point it at a folder and you are done, or pick albums from Immich or a folder in Nextcloud.
- Recipients open a personal link and add the Back in Frame web app to their home screen: no app
  store, no account, no phone number. They get notifications and a gallery of what they received.
- Or they use the free ntfy app, through the public ntfy.sh server: then the container needs no
  open ports, domain or certificate at all.
- Or they get the photos by e-mail, Telegram, Signal, Matrix or Discord, whatever they already
  use. You choose per person.
- In eleven languages, per person: English, Dutch, German, French, Spanish, Italian, Portuguese,
  Polish, Russian, Chinese and Japanese.

**Smart, but not too smart**
- Photos from *this day, years ago* get priority.
- Recently sent photos stay away for a while.
- Every album gets its fair share, so one huge trip doesn't drown out the small moments.

**Everyone their own rhythm**
- Per person: how many photos a day, every how many days, between which hours, and how far apart.
- The recipient adjusts it themselves with *More*, *Less* and *Pause 1 week*.

**Your data, your hardware**
- Your photo folder stays read-only.
- No telemetry, no AI, no tracking. The location is never read from your photos, and metadata is
  stripped before anything is sent; only the date stays.
- Web app notifications are end-to-end encrypted; the photos themselves only come from your server.

**Features**
- JPEG, PNG, WebP and HEIC (iPhone) photos, resized for phones.
- Videos, re-encoded to fit when needed.
- Eleven languages, per recipient.
- Optional delivery by e-mail, Telegram (with buttons), Signal (with text replies), Matrix and Discord.
- Photos from Immich albums or a Nextcloud folder, read-only.
- A step-by-step iPhone guide with animation in the recipient's language; checked against WCAG 2.2 AA.
- Admin page with a link and QR code per recipient and a "send now" button.
- Runs on amd64 and arm64: Unraid, TrueNAS, Synology, QNAP, Proxmox, Raspberry Pi, any Docker host.
- MIT licensed, with automated tests including end-to-end tests against a real ntfy server and a
  real browser.

---

## Unraid Community Applications — Overview field

> Back in Frame sends a photo or video from a folder of your choice to the people you choose now and then, as a notification — so the photos you took get seen again. Photos from "this day, years ago" get priority; each recipient sets their own rhythm with More / Less / Pause. Recipients use the Back in Frame web app (put port 8081 behind your reverse proxy), the free ntfy app through ntfy.sh (no open ports), or e-mail, Telegram, Signal, Matrix or Discord once configured. Eleven languages. Mount your photo share read-only on /media.

**Category:** `MediaApp:Photos Tools:Utilities`

---

## Announcement post (Reddit r/selfhosted, forums, Mastodon)

> **Back in Frame — get your best memories back on your phone (self-hosted)**
>
> I kept taking hundreds of photos on every trip and never looked at them again, so I built a
> small self-hosted service that sends one photo or video from a folder to the people you choose
> now and then.
>
> - Photos from "this day, years ago" get priority; every trip gets a fair share
> - Per person: photos per day / every N days / time window; recipients tap *More*, *Less* or *Pause*
> - Recipients get a tiny web app (link → home screen → notifications + gallery), or use ntfy
> - No AI, no telemetry, location stripped, end-to-end encrypted push; HEIC and video support
> - One Docker container, English + Dutch, MIT
>
> Repo: https://github.com/backinframe/backinframe — feedback welcome!

---

## Release v0.1.0 (GitHub release text)

See `publish/release-0.1.0.md`.

