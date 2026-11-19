"""Settings, read from environment variables (Docker friendly)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .i18n import LANGUAGES

PREFIX = "BACKINFRAME_"


class ConfigError(ValueError):
    pass


def _bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"1", "true", "yes", "ja", "on"}:
        return True
    if v in {"0", "false", "no", "nee", "off"}:
        return False
    raise ConfigError(f"Not a boolean: {value!r}")


def _list(value: str) -> tuple[str, ...]:
    """Comma-separated names; a name with a comma in it is written with a backslash: "Rome\\, 2024"."""
    parts = re.split(r"(?<!\\),", value)
    return tuple(p.replace("\\,", ",").strip() for p in parts if p.strip())


@dataclass(frozen=True)
class Settings:
    media_dir: Path = Path("/media")
    data_dir: Path = Path("/data")
    ntfy_url: str = "https://ntfy.sh"
    # URL the *phone* uses to reach ntfy (for the buttons). Defaults to ntfy_url.
    ntfy_public_url: str = ""
    ntfy_token: str = ""
    # Override the attachment limit (MB). 0 = ask the ntfy server.
    max_attachment_mb: float = 0
    timezone: str = "Europe/Amsterdam"
    language: str = "en"
    rescan_minutes: int = 60
    poll_seconds: int = 20
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    admin_password: str = ""
    image_max_edge: int = 2048
    send_videos: bool = True
    video_max_mb: float = 50
    # Largest file someone may send through an upload link (MB).
    upload_max_mb: float = 500
    # The recipient web app: the public https address it is reachable on. Empty = off (ntfy only).
    app_url: str = ""
    app_port: int = 8081
    vapid_subject: str = ""
    # Extra channels. Each is on when its settings are filled in; recipients pick one.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: str = "starttls"  # starttls | ssl | none
    email_max_mb: float = 15
    telegram_bot_token: str = ""
    telegram_api_url: str = "https://api.telegram.org"
    signal_url: str = ""  # signal-cli-rest-api, e.g. http://signal-api:8080
    signal_number: str = ""  # the number registered there, +31…
    matrix_homeserver: str = ""
    matrix_token: str = ""
    discord_enabled: bool = True
    # Photos from Immich and Nextcloud, copied into <data_dir>/sources and checked every sync_minutes.
    immich_url: str = ""
    immich_api_key: str = ""
    immich_albums: tuple[str, ...] = ()  # album names; empty = every album the key can see
    nextcloud_url: str = ""
    nextcloud_user: str = ""
    nextcloud_password: str = ""  # an app password, not the login password
    nextcloud_folder: str = ""
    sync_minutes: int = 60
    # A heart on a memory in the web app (loved photos come back a bit more often; the admin page
    # shows which photos get hearts), and a year review in the first days of January.
    reactions_enabled: bool = True
    year_review_enabled: bool = True
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def public_ntfy_url(self) -> str:
        return (self.ntfy_public_url or self.ntfy_url).rstrip("/")

    @property
    def app_enabled(self) -> bool:
        return bool(self.app_url)

    @property
    def public_app_url(self) -> str:
        return self.app_url.rstrip("/")

    @property
    def push_subject(self) -> str:
        """Who push services can contact about this server (RFC 8292): an https address or mailto."""
        if self.vapid_subject:
            return self.vapid_subject
        if self.public_app_url.startswith("https://"):
            return self.public_app_url
        return "mailto:backinframe@localhost"  # only for testing on localhost

    @property
    def vapid_path(self) -> Path:
        return self.data_dir / "vapid_private.pem"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def signal_enabled(self) -> bool:
        return bool(self.signal_url and self.signal_number)

    @property
    def matrix_enabled(self) -> bool:
        return bool(self.matrix_homeserver and self.matrix_token)

    @property
    def immich_enabled(self) -> bool:
        return bool(self.immich_url and self.immich_api_key)

    @property
    def nextcloud_enabled(self) -> bool:
        return bool(self.nextcloud_url and self.nextcloud_user and self.nextcloud_password and self.nextcloud_folder)

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def uploads_dir(self) -> Path:
        """Photos that came in through an upload link: <data>/uploads/<album>/."""
        return self.data_dir / "uploads"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "backinframe.sqlite3"

    def validate(self) -> Settings:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"Unknown timezone: {self.timezone}") from exc
        if self.language not in LANGUAGES:
            raise ConfigError("language must be one of " + ", ".join(LANGUAGES))
        if self.smtp_security not in {"starttls", "ssl", "none"}:
            raise ConfigError("smtp_security must be starttls, ssl or none")
        if self.email_max_mb <= 0:
            raise ConfigError("email_max_mb must be positive")
        if self.smtp_host and not self.smtp_from:
            raise ConfigError("smtp_from is needed to send e-mail")
        if bool(self.signal_url) != bool(self.signal_number):
            raise ConfigError("Signal needs both signal_url and signal_number")
        if self.signal_number and not re.fullmatch(r"\+[1-9]\d{6,14}", self.signal_number):
            raise ConfigError("signal_number must be a phone number with country code, like +31612345678")
        for name in ("signal_url", "matrix_homeserver", "telegram_api_url"):
            value = getattr(self, name)
            if value and not value.startswith(("http://", "https://")):
                raise ConfigError(f"{name} must start with http:// or https://")
        if bool(self.matrix_homeserver) != bool(self.matrix_token):
            raise ConfigError("Matrix needs both matrix_homeserver and matrix_token")
        for name in ("immich_url", "nextcloud_url"):
            value = getattr(self, name)
            if value and not value.startswith(("http://", "https://")):
                raise ConfigError(f"{name} must start with http:// or https://")
        if bool(self.immich_url) != bool(self.immich_api_key):
            raise ConfigError("Immich needs both immich_url and immich_api_key")
        nc = [self.nextcloud_url, self.nextcloud_user, self.nextcloud_password, self.nextcloud_folder]
        if any(nc) and not all(nc):
            raise ConfigError("Nextcloud needs nextcloud_url, nextcloud_user, nextcloud_password and nextcloud_folder")
        if self.sync_minutes < 5:
            raise ConfigError("sync_minutes must be at least 5")
        if not self.ntfy_url.startswith(("http://", "https://")):
            raise ConfigError("ntfy_url must start with http:// or https://")
        if self.image_max_edge < 320:
            raise ConfigError("image_max_edge must be at least 320")
        if self.rescan_minutes < 1 or self.poll_seconds < 1:
            raise ConfigError("rescan_minutes and poll_seconds must be >= 1")
        if self.upload_max_mb <= 0:
            raise ConfigError("upload_max_mb must be positive")
        if self.max_attachment_mb < 0 or self.video_max_mb <= 0:
            raise ConfigError("attachment limits must be positive")
        if self.app_url:
            host = self.app_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            local = host in {"localhost", "127.0.0.1"}
            if not (self.app_url.startswith("https://") or (self.app_url.startswith("http://") and local)):
                raise ConfigError("app_url must be an https:// address (phones only allow push over https)")
            if self.app_port == self.web_port:
                raise ConfigError("app_port must differ from web_port: the admin page stays private")
            if self.vapid_subject and not self.vapid_subject.startswith(("mailto:", "https://")):
                raise ConfigError("vapid_subject must start with mailto: or https://")
        return self


_FIELDS = {
    "MEDIA_DIR": ("media_dir", Path),
    "DATA_DIR": ("data_dir", Path),
    "NTFY_URL": ("ntfy_url", str),
    "NTFY_PUBLIC_URL": ("ntfy_public_url", str),
    "NTFY_TOKEN": ("ntfy_token", str),
    "MAX_ATTACHMENT_MB": ("max_attachment_mb", float),
    "TIMEZONE": ("timezone", str),
    "LANGUAGE": ("language", str),
    "RESCAN_MINUTES": ("rescan_minutes", int),
    "POLL_SECONDS": ("poll_seconds", int),
    "WEB_HOST": ("web_host", str),
    "WEB_PORT": ("web_port", int),
    "ADMIN_PASSWORD": ("admin_password", str),
    "IMAGE_MAX_EDGE": ("image_max_edge", int),
    "SEND_VIDEOS": ("send_videos", _bool),
    "REACTIONS": ("reactions_enabled", _bool),
    "YEAR_REVIEW": ("year_review_enabled", _bool),
    "VIDEO_MAX_MB": ("video_max_mb", float),
    "UPLOAD_MAX_MB": ("upload_max_mb", float),
    "APP_URL": ("app_url", str),
    "APP_PORT": ("app_port", int),
    "VAPID_SUBJECT": ("vapid_subject", str),
    "SMTP_HOST": ("smtp_host", str),
    "SMTP_PORT": ("smtp_port", int),
    "SMTP_USER": ("smtp_user", str),
    "SMTP_PASSWORD": ("smtp_password", str),
    "SMTP_FROM": ("smtp_from", str),
    "SMTP_SECURITY": ("smtp_security", str),
    "EMAIL_MAX_MB": ("email_max_mb", float),
    "TELEGRAM_BOT_TOKEN": ("telegram_bot_token", str),
    "TELEGRAM_API_URL": ("telegram_api_url", str),
    "SIGNAL_URL": ("signal_url", str),
    "SIGNAL_NUMBER": ("signal_number", str),
    "MATRIX_HOMESERVER": ("matrix_homeserver", str),
    "MATRIX_TOKEN": ("matrix_token", str),
    "DISCORD_ENABLED": ("discord_enabled", _bool),
    "IMMICH_URL": ("immich_url", str),
    "IMMICH_API_KEY": ("immich_api_key", str),
    "IMMICH_ALBUMS": ("immich_albums", _list),
    "NEXTCLOUD_URL": ("nextcloud_url", str),
    "NEXTCLOUD_USER": ("nextcloud_user", str),
    "NEXTCLOUD_PASSWORD": ("nextcloud_password", str),
    "NEXTCLOUD_FOLDER": ("nextcloud_folder", str),
    "SYNC_MINUTES": ("sync_minutes", int),
}


def from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    kwargs = {}
    for suffix, (name, conv) in _FIELDS.items():
        raw = env.get(PREFIX + suffix)
        if raw is None or raw == "":
            continue
        try:
            kwargs[name] = conv(raw)
        except (ValueError, ConfigError) as exc:
            raise ConfigError(f"{PREFIX}{suffix}: {exc}") from exc
    if "timezone" not in kwargs and env.get("TZ"):
        kwargs["timezone"] = env["TZ"]
    return Settings(**kwargs).validate()
