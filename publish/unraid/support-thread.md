# Support thread (forums.unraid.net → Docker Containers)

**Title:** [Support] Back in Frame – your best memories, back on your phone

---

**Back in Frame** sends a photo or video from your own photos to the people you choose now and
then, as a notification with the picture in it, plus where it came from and how long ago it was.
So the photos from that trip finally get looked at again.

- **Project:** https://github.com/backinframe/backinframe
- **Website:** https://backinframe.com
- **Docker image:** `ghcr.io/backinframe/backinframe`
- **Admin guide (Unraid section):** https://github.com/backinframe/backinframe/blob/main/docs/admin-guide.md#3-install-on-unraid

**What it does**

- Picks a photo from a folder, from Immich albums or from a Nextcloud folder. Photos from "this day,
  years ago" get priority; nothing is analysed, there is no AI.
- Each recipient has their own rhythm (photos per day, every how many days, a time window).
- Delivery through its own web app with push notifications, ntfy, e-mail, Telegram, Signal, Matrix
  or Discord.
- Recipients can put a heart on a photo; you see which photos get hearts and which never do. In
  January everyone gets a review of the past year.
- 11 languages, optional two-step verification on the admin page.

**Install**

1. Install it from Community Applications (search for *Back in Frame*).
2. Point *Photos* at a share with your photos. It is mounted read-only.
3. Open the WebUI (port 8080), add a recipient and scan the QR code or send the link.

The admin page (8080) is for your LAN or VPN. Only the web app port (8081) goes behind your
reverse proxy, and only if you use the web app. Set *Admin password* if others can reach 8080.

**Asking for help**

Please post:

- the Unraid version and the Back in Frame version (shown at the bottom of the admin page);
- which delivery you use (web app, ntfy, e-mail, Telegram, ...);
- the container log (Docker tab → Back in Frame icon → *Logs*), with addresses, tokens and topic
  names removed.

Security problems: please do not post them here. Use *Report a vulnerability* on GitHub, or the
address in https://backinframe.com/.well-known/security.txt.

Feature requests and bugs that are clearly bugs are also welcome as GitHub issues.
