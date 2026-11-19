# Roadmap and prepared work

Things that are not finished yet, and ideas that were looked into.

## Hearts and the year review — done in 0.1.0

Both are on by default (`BACKINFRAME_REACTIONS`, `BACKINFRAME_YEAR_REVIEW`; see the admin
guide, §8). Still open:

- A heart button in Telegram. The message would need the `send_id`, which is only known after the
  send; a callback query also needs a webhook or long polling for the bot. Hearts work in the web
  app only for now, and the statistics count web app recipients only.
- A short video instead of a collage for the year review (ffmpeg is already there).

## WhatsApp — looked into, not built

Short version: only Meta's Cloud API is allowed, and a photo sent by the business outside a
24-hour reply window must be a pre-approved template. A memory is most likely billed as
*marketing*: about $0.16 per message to a Dutch number since October 2026, so one photo a day
costs about $4.80 per recipient per month. Unofficial libraries (Baileys, whatsapp-web.js) break
WhatsApp's terms and get numbers banned.

What could be done instead:

1. A *Share* button in the web app, so a recipient forwards a photo to WhatsApp themselves. Free,
   no rules, no number needed.
2. For self-hosting, an optional "WhatsApp Cloud API" channel where the admin brings their own
   Meta account and pays Meta, with a clear cost warning.
3. For the subscription, only as a paid extra per WhatsApp recipient, after the beta shows demand.

The full write-up with sources is in the project notes.

## Other ideas

- Immich: use its own dates and albums only; no faces or other analysis, by design.
- A test button per channel on the admin page.
- A status overview per recipient (last delivery, failing channel) with a notice to the admin.
