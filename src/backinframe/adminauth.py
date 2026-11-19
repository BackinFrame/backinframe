"""Login for the admin page.

The main admin logs in with BACKINFRAME_ADMIN_PASSWORD (admin id 0). Admins can invite more admins
by name (table ``admins``): the invitee opens a one-time link, picks a password and is in. Every admin
can turn on a second step with an authenticator app.

Sessions live on the server (table ``admin_sessions``, only a hash of the cookie value), so logging
out really ends them. TOTP keys are encrypted with a key kept in ``<data>/admin.key``, apart from the
database: a copied database alone gives no codes. A new password, or removing an admin, ends that
admin's sessions; changing BACKINFRAME_ADMIN_PASSWORD ends the main admin's.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import totp
from .db import Database

SESSION_DAYS = 14
MFA_SECONDS = 300
INVITE_DAYS = 7
COOKIE = "bif_admin"
MAIN = 0
PASSWORD_MIN = 10
PASSWORD_MAX = 128
NAME_MAX = 60

# Where the second step lives: the main admin in the state table, the others in their row.
_STATE_KEYS = {"totp": "admin_totp", "totp_step": "admin_totp_step", "totp_since": "admin_totp_since",
               "recovery": "admin_recovery"}


@dataclass
class Admin:
    id: int
    name: str
    active: bool  # False: invited, no password yet
    created_at: str
    created_by: str


class AdminAuth:
    def __init__(self, db: Database, password: str, data_dir: Path, clock):
        self.db = db
        self.password = password
        self.data_dir = Path(data_dir)
        self.clock = clock
        self._key: bytes | None = None

    # -- keys -------------------------------------------------------------------------
    @property
    def key(self) -> bytes:
        """32 random bytes in <data>/admin.key, made on first use, readable by the owner only."""
        if self._key is None:
            path = self.data_dir / "admin.key"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(secrets.token_bytes(32))
            self._key = path.read_bytes()
        return self._key

    @property
    def _secret(self) -> str:
        return self.key.hex()

    def _tag(self, admin_id: int) -> str:
        """Changes when that admin's password changes: older sessions stop working."""
        if admin_id == MAIN:
            basis = self.password
        else:
            rows = self.db.query("SELECT password_hash FROM admins WHERE id = ?", (admin_id,))
            basis = rows[0]["password_hash"] if rows and rows[0]["password_hash"] else ""
            if not basis:
                return ""
        return hmac.new(self.key, f"{admin_id}:{basis}".encode(), hashlib.sha256).hexdigest()[:32]

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    # -- passwords --------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def password_ok(self, password: str, admin_id: int = MAIN) -> bool:
        if admin_id == MAIN:
            return secrets.compare_digest((password or "").encode(), self.password.encode())
        rows = self.db.query("SELECT password_hash FROM admins WHERE id = ?", (admin_id,))
        return bool(rows and rows[0]["password_hash"]) and verify_password(password, rows[0]["password_hash"])

    def login(self, name: str, password: str) -> int | None:
        """The admin id for this name and password, or None. An empty name is the main admin."""
        name = (name or "").strip()
        if not name:
            return MAIN if self.password_ok(password) else None
        rows = self.db.query("SELECT id FROM admins WHERE name = ? COLLATE NOCASE AND password_hash != ''",
                             (name,))
        if not rows:
            verify_password(password, _DUMMY)  # same time as a real check
            return None
        return rows[0]["id"] if self.password_ok(password, rows[0]["id"]) else None

    # -- other admins -----------------------------------------------------------------------
    def admins(self) -> list[Admin]:
        return [Admin(r["id"], r["name"], bool(r["password_hash"]), r["created_at"], r["created_by"])
                for r in self.db.query("SELECT * FROM admins ORDER BY name COLLATE NOCASE")]

    def has_others(self) -> bool:
        return bool(self.db.query("SELECT 1 FROM admins WHERE password_hash != '' LIMIT 1"))

    def name_of(self, admin_id: int) -> str:
        if admin_id == MAIN:
            return ""
        rows = self.db.query("SELECT name FROM admins WHERE id = ?", (admin_id,))
        return rows[0]["name"] if rows else ""

    def invite(self, name: str, by: int) -> str:
        """A new admin (or a new link for one who did not finish). Returns the one-time token."""
        name = " ".join("".join(c for c in name if c.isprintable()).split())[:NAME_MAX]
        if not name:
            raise ValueError("name")
        token = secrets.token_urlsafe(32)
        expires = (self.clock().astimezone(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat()
        existing = self.db.query("SELECT id, password_hash FROM admins WHERE name = ? COLLATE NOCASE", (name,))
        if existing and existing[0]["password_hash"]:
            raise ValueError("exists")
        if existing:
            self.db.execute("UPDATE admins SET invite_hash = ?, invite_expires = ? WHERE id = ?",
                            (self._hash(token), expires, existing[0]["id"]))
        else:
            self.db.execute(
                "INSERT INTO admins(name, password_hash, created_at, created_by, invite_hash, invite_expires) "
                "VALUES(?, '', ?, ?, ?, ?)",
                (name, self.clock().isoformat(timespec="seconds"), self.name_of(by),
                 self._hash(token), expires))
        return token

    def invited(self, token: str) -> Admin | None:
        if not token or len(token) > 100:
            return None
        rows = self.db.query("SELECT * FROM admins WHERE invite_hash = ?", (self._hash(token),))
        if not rows or datetime.fromisoformat(rows[0]["invite_expires"]) <= self.clock():
            return None
        r = rows[0]
        return Admin(r["id"], r["name"], bool(r["password_hash"]), r["created_at"], r["created_by"])

    def accept(self, token: str, password: str) -> int | None:
        """Set the password from an invitation; the link stops working."""
        admin = self.invited(token)
        if admin is None:
            return None
        self.set_password(admin.id, password)
        self.db.execute("UPDATE admins SET invite_hash = NULL, invite_expires = NULL WHERE id = ?", (admin.id,))
        return admin.id

    def set_password(self, admin_id: int, password: str) -> None:
        if admin_id == MAIN:
            raise ValueError("main")
        check_new_password(password)
        self.db.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (hash_password(password), admin_id))
        self.end_all(admin_id)

    def remove(self, admin_id: int) -> None:
        if admin_id == MAIN:
            raise ValueError("main")
        self.end_all(admin_id)
        self.db.execute("DELETE FROM admins WHERE id = ?", (admin_id,))

    # -- sessions -------------------------------------------------------------------------
    def create_session(self, stage: str, admin_id: int = MAIN) -> str:
        """stage 'mfa': password right, code still needed (5 minutes); 'ok': logged in."""
        token = secrets.token_urlsafe(32)
        now = self.clock().astimezone(timezone.utc)
        life = timedelta(seconds=MFA_SECONDS) if stage == "mfa" else timedelta(days=SESSION_DAYS)
        self.db.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (now.isoformat(),))
        self.db.execute(
            "INSERT INTO admin_sessions(token_hash, stage, created_at, expires_at, pw, admin_id) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (self._hash(token), stage, now.isoformat(), (now + life).isoformat(), self._tag(admin_id), admin_id),
        )
        return token

    def session_info(self, token: str | None) -> tuple[str, int] | None:
        """(stage, admin id) of a valid session, or None."""
        if not token or len(token) > 100:
            return None
        rows = self.db.query("SELECT stage, expires_at, pw, admin_id FROM admin_sessions WHERE token_hash = ?",
                             (self._hash(token),))
        if not rows:
            return None
        row = rows[0]
        tag = self._tag(row["admin_id"])
        if datetime.fromisoformat(row["expires_at"]) <= self.clock() or not tag or not hmac.compare_digest(
                row["pw"], tag):
            self.end(token)
            return None
        return row["stage"], row["admin_id"]

    def session(self, token: str | None) -> str | None:
        """The stage of a valid session, or None."""
        info = self.session_info(token)
        return info[0] if info else None

    def end(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (self._hash(token),))

    def end_all(self, admin_id: int | None = None) -> None:
        if admin_id is None:
            self.db.execute("DELETE FROM admin_sessions")
        else:
            self.db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (admin_id,))

    # -- two-step verification ------------------------------------------------------------
    def _get(self, admin_id: int, field: str) -> str | None:
        if admin_id == MAIN:
            return self.db.get_state(_STATE_KEYS[field])
        rows = self.db.query(f"SELECT {field} FROM admins WHERE id = ?", (admin_id,))  # nosec B608 - fixed names
        return rows[0][field] if rows else None

    def _set(self, admin_id: int, field: str, value: str | None) -> None:
        if admin_id == MAIN:
            if value is None:
                self.db.execute("DELETE FROM state WHERE key = ?", (_STATE_KEYS[field],))
            else:
                self.db.set_state(_STATE_KEYS[field], value)
        else:
            self.db.execute(f"UPDATE admins SET {field} = ? WHERE id = ?", (value, admin_id))  # nosec B608

    def totp_enabled(self, admin_id: int = MAIN) -> bool:
        return bool(self._get(admin_id, "totp"))

    def totp_since(self, admin_id: int = MAIN) -> datetime | None:
        value = self._get(admin_id, "totp_since")
        return datetime.fromisoformat(value) if value else None

    def recovery_left(self, admin_id: int = MAIN) -> int:
        return totp.remaining(self._get(admin_id, "recovery"))

    def seal(self, secret: str) -> str:
        return totp.encrypt(secret, self._secret)

    def unseal(self, stored: str) -> str:
        return totp.decrypt(stored, self._secret)

    def enable(self, secret: str, code: str, admin_id: int = MAIN) -> list[str] | None:
        """Turn it on after the first right code; returns the recovery codes, or None."""
        step = totp.verify(secret, code, self.clock().timestamp(), None)
        if step is None or self.totp_enabled(admin_id):
            return None
        codes = totp.new_recovery_codes()
        self._set(admin_id, "totp", self.seal(secret))
        self._set(admin_id, "totp_step", str(step))
        self._set(admin_id, "totp_since", self.clock().isoformat())
        self._set(admin_id, "recovery", totp.dump_hashes(codes))
        return codes

    def check(self, code: str, admin_id: int = MAIN) -> bool:
        """A code from the app (each time step once) or an unused recovery code (used up)."""
        stored = self._get(admin_id, "totp")
        if not stored:
            return False
        last = self._get(admin_id, "totp_step")
        step = totp.verify(self.unseal(stored), code, self.clock().timestamp(), int(last) if last else None)
        if step is not None:
            self._set(admin_id, "totp_step", str(step))
            return True
        left = totp.use_recovery(self._get(admin_id, "recovery"), code)
        if left is not None:
            self._set(admin_id, "recovery", left)
            return True
        return False

    def disable(self, admin_id: int = MAIN) -> None:
        for field in _STATE_KEYS:
            self._set(admin_id, field, None)


# -- password hashing (scrypt, standard library) ---------------------------------------------------
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt((password or "")[:PASSWORD_MAX].encode(), salt=bytes.fromhex(salt), n=int(n),
                             r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_DUMMY = hash_password(secrets.token_hex(8))


def check_new_password(password: str) -> None:
    if not (PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX):
        raise ValueError("length")
