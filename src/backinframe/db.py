"""SQLite storage. One connection, guarded by a lock (the app is tiny)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL UNIQUE,      -- relative to the media dir
    kind      TEXT NOT NULL,             -- 'image' | 'video'
    size      INTEGER NOT NULL,
    mtime     REAL NOT NULL,
    taken_at  TEXT,                      -- ISO date-time (naive, local to camera); no location
    missing   INTEGER NOT NULL DEFAULT 0,
    broken    INTEGER NOT NULL DEFAULT 0, -- could not be prepared for sending
    title     TEXT NOT NULL DEFAULT '',   -- set by the admin: replaces the album name in the caption
    note      TEXT NOT NULL DEFAULT '',   -- set by the admin: an extra line under the date
    taken_original TEXT,                  -- the date the scanner found, when the admin changed it
    date_edited INTEGER NOT NULL DEFAULT 0,
    sender    TEXT NOT NULL DEFAULT ''    -- "from Anna": from the album, an upload link or the admin
);
CREATE TABLE IF NOT EXISTS albums (
    folder TEXT PRIMARY KEY,              -- as in Media.folder
    sender TEXT NOT NULL DEFAULT ''       -- given to new photos in this folder
);
CREATE TABLE IF NOT EXISTS upload_links (
    id         INTEGER PRIMARY KEY,
    token      TEXT NOT NULL UNIQUE,      -- secret part of /u/<token> on the web app port
    album      TEXT NOT NULL,             -- folder name under <data>/uploads
    name       TEXT NOT NULL,             -- who uploads with it; becomes the sender
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT,
    files      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recipients (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    topic           TEXT NOT NULL UNIQUE,
    control_topic   TEXT NOT NULL UNIQUE,
    per_day         INTEGER NOT NULL DEFAULT 1,
    every_days      INTEGER NOT NULL DEFAULT 1,
    window_start    TEXT NOT NULL DEFAULT '09:00',
    window_end      TEXT NOT NULL DEFAULT '21:00',
    min_gap_minutes INTEGER NOT NULL DEFAULT 60,
    paused_until    TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    next_send_at    TEXT,
    app_token       TEXT,                -- secret part of the recipient's web-app link
    delivery        TEXT NOT NULL DEFAULT 'ntfy', -- 'app' | 'ntfy' | 'email' | 'telegram' | ...
    address         TEXT NOT NULL DEFAULT '',     -- where the channel delivers (e-mail, chat ID, ...)
    language        TEXT NOT NULL DEFAULT '',     -- '' = the server's language
    year_review     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sends (
    id           INTEGER PRIMARY KEY,
    recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
    media_id     INTEGER REFERENCES media(id) ON DELETE SET NULL,
    sent_at      TEXT NOT NULL,
    status       TEXT NOT NULL,          -- 'ok' | 'error' | 'skipped'
    detail       TEXT,
    reaction     TEXT NOT NULL DEFAULT '', -- prepared, not used yet: '' or 'heart'
    reacted_at   TEXT
);
CREATE INDEX IF NOT EXISTS sends_by_recipient ON sends(recipient_id, sent_at);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id           INTEGER PRIMARY KEY,
    recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
    endpoint     TEXT NOT NULL UNIQUE,   -- the browser's push-service URL
    p256dh       TEXT NOT NULL,
    auth         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,           -- SHA-256 of the cookie value, never the value itself
    stage      TEXT NOT NULL,              -- 'mfa' (code still needed) or 'ok'
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    pw         TEXT NOT NULL,              -- tag of the admin password: a new password ends the session
    admin_id   INTEGER NOT NULL DEFAULT 0  -- 0: the main admin (BACKINFRAME_ADMIN_PASSWORD)
);
CREATE TABLE IF NOT EXISTS admins (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL DEFAULT '', -- scrypt; '' while the invitation is open
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL DEFAULT '',
    invite_hash    TEXT,                     -- SHA-256 of the one-time invitation token
    invite_expires TEXT,
    totp           TEXT,                     -- encrypted with <data>/admin.key
    totp_step      TEXT,
    totp_since     TEXT,
    recovery       TEXT
);
CREATE TABLE IF NOT EXISTS processed_controls (
    id      TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
"""


@dataclass
class Media:
    id: int
    path: str
    kind: str
    size: int
    mtime: float
    taken_at: datetime | None
    title: str = ""
    note: str = ""
    date_edited: bool = False
    taken_original: datetime | None = None
    sender: str = ""

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


@dataclass
class Recipient:
    id: int
    name: str
    topic: str
    control_topic: str
    per_day: int
    every_days: int
    window_start: str
    window_end: str
    min_gap_minutes: int
    paused_until: datetime | None
    enabled: bool
    created_at: datetime
    next_send_at: datetime | None
    app_token: str = ""
    delivery: str = "ntfy"
    address: str = ""
    language: str = ""
    year_review: bool = True

    def fingerprint(self) -> str:
        """Changes whenever schedule-relevant settings change."""
        return f"{self.per_day}|{self.every_days}|{self.window_start}|{self.window_end}|{self.min_gap_minutes}"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL") if str(path) != ":memory:" else None
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._forget_locations()
            self._add_app_columns()

    def _forget_locations(self) -> None:
        """Development builds stored GPS coordinates. They were never used: remove them."""
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(media)")}
        for column in ("lat", "lon"):
            if column in columns:
                try:
                    self._conn.execute(f"ALTER TABLE media DROP COLUMN {column}")
                except sqlite3.OperationalError:  # SQLite < 3.35: at least wipe the values
                    self._conn.execute(f"UPDATE media SET {column} = NULL")  # nosec B608
        self._conn.commit()

    def _add_app_columns(self) -> None:
        """Databases from before the web app get the two recipient columns."""
        import secrets

        media_columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(media)")}
        for column, ddl in (("title", "TEXT NOT NULL DEFAULT ''"), ("note", "TEXT NOT NULL DEFAULT ''"),
                            ("taken_original", "TEXT"), ("date_edited", "INTEGER NOT NULL DEFAULT 0"),
                            ("sender", "TEXT NOT NULL DEFAULT ''")):
            if column not in media_columns:
                self._conn.execute(f"ALTER TABLE media ADD COLUMN {column} {ddl}")

        session_columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(admin_sessions)")}
        if "admin_id" not in session_columns:
            self._conn.execute("ALTER TABLE admin_sessions ADD COLUMN admin_id INTEGER NOT NULL DEFAULT 0")

        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(recipients)")}
        if "app_token" not in columns:
            self._conn.execute("ALTER TABLE recipients ADD COLUMN app_token TEXT")
        if "delivery" not in columns:
            self._conn.execute("ALTER TABLE recipients ADD COLUMN delivery TEXT NOT NULL DEFAULT 'ntfy'")
        if "address" not in columns:
            self._conn.execute("ALTER TABLE recipients ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if "language" not in columns:
            self._conn.execute("ALTER TABLE recipients ADD COLUMN language TEXT NOT NULL DEFAULT ''")
        if "year_review" not in columns:  # 1: send a year review in early January (the default)
            self._conn.execute("ALTER TABLE recipients ADD COLUMN year_review INTEGER NOT NULL DEFAULT 1")
        for row in self._conn.execute("SELECT id FROM recipients WHERE app_token IS NULL").fetchall():
            self._conn.execute("UPDATE recipients SET app_token = ? WHERE id = ?",
                               (secrets.token_urlsafe(32), row["id"]))
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS recipients_app_token ON recipients(app_token)")
        send_columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(sends)")}
        if "reaction" not in send_columns:  # prepared for reactions (a heart); nothing sets them yet
            self._conn.execute("ALTER TABLE sends ADD COLUMN reaction TEXT NOT NULL DEFAULT ''")
            self._conn.execute("ALTER TABLE sends ADD COLUMN reacted_at TEXT")
        self._conn.commit()

    # -- low level -------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- state -----------------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        rows = self.query("SELECT value FROM state WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_state(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- media -----------------------------------------------------------
    @staticmethod
    def _media(row: sqlite3.Row) -> Media:
        return Media(
            id=row["id"], path=row["path"], kind=row["kind"], size=row["size"],
            mtime=row["mtime"], taken_at=_dt(row["taken_at"]), title=row["title"], note=row["note"],
            date_edited=bool(row["date_edited"]), taken_original=_dt(row["taken_original"]),
            sender=row["sender"],
        )

    def available_media(self, include_videos: bool = True) -> list[Media]:
        sql = "SELECT * FROM media WHERE missing = 0 AND broken = 0"
        if not include_videos:
            sql += " AND kind = 'image'"
        return [self._media(r) for r in self.query(sql + " ORDER BY id")]

    def media_by_id(self, media_id: int) -> Media | None:
        rows = self.query("SELECT * FROM media WHERE id = ?", (media_id,))
        return self._media(rows[0]) if rows else None

    def edit_media(self, media_id: int, title: str, note: str, taken: datetime | None, reset_date: bool) -> None:
        """What the admin set for one photo. ``taken`` None keeps the date; ``reset_date`` goes back to the
        date the scanner found."""
        m = self.media_by_id(media_id)
        if m is None:
            return
        if reset_date and m.date_edited:
            self.execute("UPDATE media SET taken_at = taken_original, taken_original = NULL, date_edited = 0 "
                         "WHERE id = ?", (media_id,))
        elif taken is not None and taken != m.taken_at:
            original = m.taken_original if m.date_edited else m.taken_at
            self.execute("UPDATE media SET taken_at = ?, taken_original = ?, date_edited = 1 WHERE id = ?",
                         (taken.isoformat(timespec="seconds"),
                          original.isoformat(timespec="seconds") if original else None, media_id))
        self.execute("UPDATE media SET title = ?, note = ? WHERE id = ?", (title, note, media_id))

    # -- who a photo is from ------------------------------------------------------------------
    def album_sender(self, folder: str) -> str:
        rows = self.query("SELECT sender FROM albums WHERE folder = ?", (folder,))
        return rows[0]["sender"] if rows else ""

    def set_album_sender(self, folder: str, sender: str) -> None:
        """The sender for every photo in this folder now, and for new ones."""
        self.execute("INSERT INTO albums(folder, sender) VALUES(?, ?) "
                     "ON CONFLICT(folder) DO UPDATE SET sender = excluded.sender", (folder, sender))
        for m in self.query("SELECT id, path FROM media"):
            if (m["path"].rsplit("/", 1)[0] if "/" in m["path"] else "") == folder:
                self.execute("UPDATE media SET sender = ? WHERE id = ?", (sender, m["id"]))

    def set_sender(self, media_id: int, sender: str) -> None:
        self.execute("UPDATE media SET sender = ? WHERE id = ?", (sender, media_id))

    # -- upload links ---------------------------------------------------------------------------
    def upload_links(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM upload_links WHERE revoked_at IS NULL ORDER BY album, name")

    def upload_link(self, token: str) -> sqlite3.Row | None:
        rows = self.query("SELECT * FROM upload_links WHERE token = ? AND revoked_at IS NULL", (token,))
        return rows[0] if rows else None

    def add_upload_link(self, token: str, album: str, name: str, now: datetime) -> int:
        cur = self.execute("INSERT INTO upload_links(token, album, name, created_at) VALUES(?, ?, ?, ?)",
                           (token, album, name, now.isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def revoke_upload_link(self, link_id: int, now: datetime) -> None:
        self.execute("UPDATE upload_links SET revoked_at = ? WHERE id = ?",
                     (now.isoformat(timespec="seconds"), link_id))

    def count_upload(self, link_id: int, files: int, now: datetime) -> None:
        self.execute("UPDATE upload_links SET files = files + ?, last_used_at = ? WHERE id = ?",
                     (files, now.isoformat(timespec="seconds"), link_id))

    def media_page(self, folder: str | None, offset: int, limit: int) -> tuple[list[Media], int]:
        """Photos for the admin's photo list, by folder and name. ``folder`` "" is the top folder."""
        where, params = "missing = 0", []
        if folder is not None:
            if folder:
                where += " AND path LIKE ? ESCAPE '\\' AND path NOT LIKE ? ESCAPE '\\'"
                esc = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                params += [esc + "/%", esc + "/%/%"]
            else:
                where += " AND path NOT LIKE '%/%'"
        total = self.query(f"SELECT COUNT(*) AS n FROM media WHERE {where}", tuple(params))[0]["n"]  # nosec B608
        rows = self.query(f"SELECT * FROM media WHERE {where} ORDER BY path LIMIT ? OFFSET ?",  # nosec B608
                          (*params, limit, offset))
        return [self._media(r) for r in rows], total

    def media_folders(self) -> list[tuple[str, int]]:
        rows = self.query("SELECT path FROM media WHERE missing = 0")
        counts: dict[str, int] = {}
        for r in rows:
            folder = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else ""
            counts[folder] = counts.get(folder, 0) + 1
        return sorted(counts.items())

    def media_counts(self) -> dict[str, int]:
        rows = self.query(
            "SELECT kind, COUNT(*) AS n FROM media WHERE missing = 0 AND broken = 0 GROUP BY kind"
        )
        counts = {"image": 0, "video": 0}
        counts.update({r["kind"]: r["n"] for r in rows})
        return counts

    def mark_broken(self, media_id: int) -> None:
        self.execute("UPDATE media SET broken = 1 WHERE id = ?", (media_id,))

    # -- recipients ------------------------------------------------------
    @staticmethod
    def _recipient(row: sqlite3.Row) -> Recipient:
        return Recipient(
            id=row["id"], name=row["name"], topic=row["topic"], control_topic=row["control_topic"],
            per_day=row["per_day"], every_days=row["every_days"],
            window_start=row["window_start"], window_end=row["window_end"],
            min_gap_minutes=row["min_gap_minutes"], paused_until=_dt(row["paused_until"]),
            enabled=bool(row["enabled"]), created_at=_dt(row["created_at"]),
            next_send_at=_dt(row["next_send_at"]),
            app_token=row["app_token"] or "", delivery=row["delivery"] or "ntfy",
            address=row["address"] or "", language=row["language"] or "",
            year_review=bool(row["year_review"]),
        )

    def recipients(self) -> list[Recipient]:
        return [self._recipient(r) for r in self.query("SELECT * FROM recipients ORDER BY id")]

    def recipient(self, rid: int) -> Recipient | None:
        rows = self.query("SELECT * FROM recipients WHERE id = ?", (rid,))
        return self._recipient(rows[0]) if rows else None

    def recipient_by_control_topic(self, topic: str) -> Recipient | None:
        rows = self.query("SELECT * FROM recipients WHERE control_topic = ?", (topic,))
        return self._recipient(rows[0]) if rows else None

    def insert_recipient(self, name: str, topic: str, control_topic: str, created_at: datetime,
                         app_token: str = "", delivery: str = "ntfy", address: str = "", language: str = "") -> int:
        import secrets

        cur = self.execute(
            "INSERT INTO recipients(name, topic, control_topic, created_at, app_token, delivery, address, language) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (name, topic, control_topic, created_at.isoformat(), app_token or secrets.token_urlsafe(32),
             delivery, address, language),
        )
        return int(cur.lastrowid)

    def recipients_at(self, delivery: str, address: str) -> list[Recipient]:
        rows = self.query("SELECT * FROM recipients WHERE delivery = ? AND address = ? ORDER BY id",
                          (delivery, address))
        return [self._recipient(r) for r in rows]

    def recipient_by_app_token(self, token: str) -> Recipient | None:
        if not token:
            return None
        rows = self.query("SELECT * FROM recipients WHERE app_token = ?", (token,))
        return self._recipient(rows[0]) if rows else None

    _UPDATABLE = {
        "name", "per_day", "every_days", "window_start", "window_end",
        "min_gap_minutes", "paused_until", "enabled", "next_send_at", "delivery", "address", "language",
        "year_review",
    }

    def update_recipient(self, rid: int, **fields: Any) -> None:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise KeyError(f"Cannot update {unknown}")
        if not fields:
            return
        values = []
        for value in fields.values():
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = int(value)
            values.append(value)
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE recipients SET {sets} WHERE id = ?", (*values, rid))  # nosec B608

    def delete_recipient(self, rid: int) -> None:
        self.execute("DELETE FROM recipients WHERE id = ?", (rid,))

    # -- sends -----------------------------------------------------------
    def log_send(self, rid: int, media_id: int | None, when: datetime, status: str, detail: str = "") -> None:
        self.execute(
            "INSERT INTO sends(recipient_id, media_id, sent_at, status, detail) VALUES(?, ?, ?, ?, ?)",
            (rid, media_id, when.isoformat(), status, detail),
        )

    def send_history(self, rid: int) -> dict[int, tuple[int, datetime]]:
        """media_id -> (times sent, last sent) for successful sends."""
        rows = self.query(
            "SELECT media_id, COUNT(*) AS n, MAX(sent_at) AS last FROM sends "
            "WHERE recipient_id = ? AND status = 'ok' AND media_id IS NOT NULL GROUP BY media_id",
            (rid,),
        )
        return {r["media_id"]: (r["n"], datetime.fromisoformat(r["last"])) for r in rows}

    def recent_sends(self, rid: int, limit: int = 10) -> list[sqlite3.Row]:
        return self.query(
            "SELECT s.*, m.path FROM sends s LEFT JOIN media m ON m.id = s.media_id "
            "WHERE s.recipient_id = ? ORDER BY s.id DESC LIMIT ?",
            (rid, limit),
        )

    # -- control dedupe ----------------------------------------------------
    def control_seen(self, msg_id: str) -> bool:
        return bool(self.query("SELECT 1 FROM processed_controls WHERE id = ?", (msg_id,)))

    def mark_control(self, msg_id: str, when: datetime) -> None:
        self.execute(
            "INSERT OR IGNORE INTO processed_controls(id, seen_at) VALUES(?, ?)", (msg_id, when.isoformat())
        )

    def prune_controls(self, before: datetime) -> None:
        self.execute("DELETE FROM processed_controls WHERE seen_at < ?", (before.isoformat(),))

    # -- web app -----------------------------------------------------------
    def gallery(self, rid: int, limit: int = 60) -> list[sqlite3.Row]:
        """What the recipient received, newest first. Only media that still exist."""
        return self.query(
            "SELECT s.id AS send_id, s.sent_at, s.reaction, m.id AS media_id, m.kind, m.path, m.taken_at, m.mtime, "
            "m.title, m.note, m.sender "
            "FROM sends s JOIN media m ON m.id = s.media_id "
            "WHERE s.recipient_id = ? AND s.status = 'ok' AND m.missing = 0 AND m.broken = 0 "
            "ORDER BY s.id DESC LIMIT ?",
            (rid, limit),
        )

    def gallery_item(self, rid: int, send_id: int) -> sqlite3.Row | None:
        rows = self.query(
            "SELECT s.id AS send_id, s.sent_at, m.id AS media_id, m.kind, m.path, m.taken_at, m.mtime "
            "FROM sends s JOIN media m ON m.id = s.media_id "
            "WHERE s.recipient_id = ? AND s.id = ? AND s.status = 'ok' AND m.missing = 0",
            (rid, send_id),
        )
        return rows[0] if rows else None

    # -- reactions ------------------------------------------------------------------------
    def set_reaction(self, rid: int, send_id: int, reaction: str, when: datetime) -> bool:
        """Store a recipient's reaction on one of their own memories. False if it is not theirs."""
        cur = self.execute(
            "UPDATE sends SET reaction = ?, reacted_at = ? WHERE id = ? AND recipient_id = ? AND status = 'ok'",
            (reaction, when.isoformat() if reaction else None, send_id, rid),
        )
        return cur.rowcount == 1

    def reactions(self, rid: int | None = None) -> dict[int, str]:
        """media_id -> reaction, for one recipient or for everyone (the last one wins)."""
        sql = "SELECT media_id, reaction FROM sends WHERE reaction != '' AND media_id IS NOT NULL"
        params: tuple = ()
        if rid is not None:
            sql += " AND recipient_id = ?"
            params = (rid,)
        return {row["media_id"]: row["reaction"] for row in self.query(sql + " ORDER BY id", params)}

    def app_sends(self) -> list[sqlite3.Row]:
        """Every memory delivered in the web app (the only place a heart can be given), with its
        photo and recipient: the basis of the hearts overview on the admin page."""
        return self.query(
            "SELECT s.id AS send_id, s.sent_at, s.reaction, s.reacted_at, s.recipient_id, r.name AS recipient, "
            "m.id AS media_id, m.kind, m.path, m.taken_at "
            "FROM sends s JOIN media m ON m.id = s.media_id JOIN recipients r ON r.id = s.recipient_id "
            "WHERE s.status = 'ok' AND r.delivery = 'app' ORDER BY s.id"
        )

    def sends_in_year(self, rid: int, year: int) -> list[sqlite3.Row]:
        """Everything a recipient received in one calendar year, oldest first (for a year review)."""
        return self.query(
            "SELECT s.id AS send_id, s.sent_at, s.reaction, m.id AS media_id, m.kind, m.path, m.taken_at "
            "FROM sends s JOIN media m ON m.id = s.media_id "
            "WHERE s.recipient_id = ? AND s.status = 'ok' AND s.sent_at >= ? AND s.sent_at < ? "
            "ORDER BY s.sent_at, s.id",
            (rid, f"{year:04d}-01-01", f"{year + 1:04d}-01-01"),
        )

    def last_send_id(self, rid: int) -> int | None:
        rows = self.query("SELECT MAX(id) AS id FROM sends WHERE recipient_id = ? AND status = 'ok'", (rid,))
        return rows[0]["id"] if rows and rows[0]["id"] is not None else None

    def add_push_subscription(self, rid: int, endpoint: str, p256dh: str, auth: str, when: datetime) -> None:
        self.execute(
            "INSERT INTO push_subscriptions(recipient_id, endpoint, p256dh, auth, created_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET recipient_id = excluded.recipient_id, "
            "p256dh = excluded.p256dh, auth = excluded.auth",
            (rid, endpoint, p256dh, auth, when.isoformat()),
        )

    def push_subscriptions(self, rid: int) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM push_subscriptions WHERE recipient_id = ? ORDER BY id", (rid,))

    def delete_push_subscription(self, endpoint: str, rid: int | None = None) -> None:
        if rid is None:
            self.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        else:
            self.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND recipient_id = ?", (endpoint, rid))

    def media_in_galleries(self, limit: int = 60) -> set[int]:
        """Media ids someone can still open in the app (for cleaning the cache)."""
        rows = self.query(
            "SELECT DISTINCT media_id FROM (SELECT media_id, ROW_NUMBER() OVER "
            "(PARTITION BY recipient_id ORDER BY id DESC) AS n FROM sends "
            "WHERE status = 'ok' AND media_id IS NOT NULL) WHERE n <= ?",
            (limit,),
        )
        return {r["media_id"] for r in rows}
