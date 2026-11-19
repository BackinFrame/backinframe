"""Two-step verification with time-based codes (TOTP, RFC 6238), as authenticator apps make them.

The key is stored encrypted (AES-GCM) with a key the caller supplies (the hosted version derives it from
its secret key, the self-hosted version keeps it in a file), so a copy of the database alone gives no
codes. Each time step is accepted once (ASVS 6.6.x), and one step of clock
drift either way is allowed. Recovery codes are stored as SHA-256 hashes and work once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STEP = 30
DIGITS = 6
RECOVERY_COUNT = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def new_secret() -> str:
    """160 random bits in base32, the form every authenticator app accepts."""
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret: str, step: int) -> str:
    # HMAC-SHA1 is what RFC 6238 and the authenticator apps use; it is not used here as a plain hash.
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10 ** DIGITS:0{DIGITS}d}"


def verify(secret: str, code: str, now: float, last_step: int | None) -> int | None:
    """The time step the code belongs to, or None. A step at or before ``last_step`` never counts."""
    code = "".join(ch for ch in code or "" if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int(now // STEP)
    for step in (current, current - 1, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def uri(secret: str, account_name: str, issuer: str = "Back in Frame") -> str:
    label = quote(f"{issuer}:{account_name}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def grouped(secret: str) -> str:
    """The key in groups of four, for typing it in by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# -- recovery codes ----------------------------------------------------------------

def _normalize(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def new_recovery_codes() -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(RECOVERY_COUNT)]


def hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def dump_hashes(codes: list[str]) -> str:
    return json.dumps([hash_recovery(c) for c in codes])


def use_recovery(stored: str | None, code: str) -> str | None:
    """The stored JSON without the used code, or None when the code is not one of them."""
    if not stored or len(_normalize(code)) != 10:
        return None
    hashes = json.loads(stored)
    wanted = hash_recovery(code)
    for h in hashes:
        if hmac.compare_digest(h, wanted):
            hashes.remove(h)
            return json.dumps(hashes)
    return None


def remaining(stored: str | None) -> int:
    return len(json.loads(stored)) if stored else 0


# -- encryption at rest ----------------------------------------------------------

def _aead(server_secret: str) -> AESGCM:
    return AESGCM(hashlib.sha256(b"backinframe-totp|" + server_secret.encode()).digest())


def encrypt(secret: str, server_secret: str) -> str:
    nonce = os.urandom(12)
    sealed = _aead(server_secret).encrypt(nonce, secret.encode(), b"totp")
    return "v1:" + base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(stored: str, server_secret: str) -> str:
    raw = base64.urlsafe_b64decode(stored.removeprefix("v1:"))
    return _aead(server_secret).decrypt(raw[:12], raw[12:], b"totp").decode()
