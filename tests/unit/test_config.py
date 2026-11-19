from pathlib import Path

import pytest

from backinframe.config import ConfigError, Settings, from_env


def test_defaults():
    s = from_env({})
    assert s.media_dir == Path("/media")
    assert s.ntfy_url == "https://ntfy.sh"
    assert s.public_ntfy_url == "https://ntfy.sh"
    assert s.language == "en"
    assert s.send_videos is True
    assert s.db_path == Path("/data/backinframe.sqlite3")


def test_env_overrides():
    s = from_env({
        "BACKINFRAME_MEDIA_DIR": "/foto",
        "BACKINFRAME_NTFY_URL": "http://ntfy:80/",
        "BACKINFRAME_NTFY_PUBLIC_URL": "https://push.example.org/",
        "BACKINFRAME_MAX_ATTACHMENT_MB": "2.5",
        "BACKINFRAME_SEND_VIDEOS": "nee",
        "BACKINFRAME_WEB_PORT": "9000",
        "BACKINFRAME_LANGUAGE": "en",
    })
    assert s.media_dir == Path("/foto")
    assert s.public_ntfy_url == "https://push.example.org"
    assert s.max_attachment_mb == 2.5
    assert s.send_videos is False
    assert s.web_port == 9000
    assert s.language == "en"


def test_tz_fallback_to_TZ_variable():
    assert from_env({"TZ": "America/Argentina/Rio_Gallegos"}).timezone == "America/Argentina/Rio_Gallegos"
    assert from_env({"TZ": "UTC", "BACKINFRAME_TIMEZONE": "Europe/Paris"}).timezone == "Europe/Paris"


@pytest.mark.parametrize("env", [
    {"BACKINFRAME_TIMEZONE": "Mars/Olympus"},
    {"BACKINFRAME_LANGUAGE": "xx"},
    {"BACKINFRAME_SMTP_HOST": "mail.example.org"},  # no sender
    {"BACKINFRAME_SMTP_HOST": "m", "BACKINFRAME_SMTP_FROM": "a@b.nl", "BACKINFRAME_SMTP_SECURITY": "tls"},
    {"BACKINFRAME_SIGNAL_URL": "http://signal:8080"},  # no number
    {"BACKINFRAME_SIGNAL_URL": "http://signal:8080", "BACKINFRAME_SIGNAL_NUMBER": "0612345678"},
    {"BACKINFRAME_MATRIX_HOMESERVER": "https://matrix.org"},  # no token
    {"BACKINFRAME_MATRIX_HOMESERVER": "matrix.org", "BACKINFRAME_MATRIX_TOKEN": "x"},
    {"BACKINFRAME_DISCORD_ENABLED": "maybe"},
    {"BACKINFRAME_NTFY_URL": "ntfy.sh"},
    {"BACKINFRAME_WEB_PORT": "abc"},
    {"BACKINFRAME_SEND_VIDEOS": "misschien"},
    {"BACKINFRAME_IMAGE_MAX_EDGE": "100"},
    {"BACKINFRAME_VIDEO_MAX_MB": "0"},
])
def test_invalid_values_are_rejected(env):
    with pytest.raises(ConfigError):
        from_env(env)


def test_settings_validate_returns_self():
    s = Settings()
    assert s.validate() is s


@pytest.mark.parametrize("lang", ["en", "nl", "de", "fr", "es", "it", "pt", "pl", "ru", "zh", "ja"])
def test_every_language_is_accepted(lang):
    assert from_env({"BACKINFRAME_LANGUAGE": lang}).language == lang


def test_channels_are_on_when_configured():
    s = from_env({
        "BACKINFRAME_SMTP_HOST": "mail.example.org", "BACKINFRAME_SMTP_FROM": "photos@example.org",
        "BACKINFRAME_TELEGRAM_BOT_TOKEN": "123:abc",
        "BACKINFRAME_SIGNAL_URL": "http://signal:8080", "BACKINFRAME_SIGNAL_NUMBER": "+31612345678",
        "BACKINFRAME_MATRIX_HOMESERVER": "https://matrix.example.org", "BACKINFRAME_MATRIX_TOKEN": "syt_x",
    })
    assert s.email_enabled and s.telegram_enabled and s.signal_enabled and s.matrix_enabled and s.discord_enabled
    off = from_env({"BACKINFRAME_DISCORD_ENABLED": "false"})
    assert not (off.email_enabled or off.telegram_enabled or off.signal_enabled or off.matrix_enabled)
    assert not off.discord_enabled
