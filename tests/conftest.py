"""Shared pytest fixtures."""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

import pytest
from helpers import TZ, Clock, FakeNtfy, start_ntfy

from backinframe.config import Settings
from backinframe.db import Database
from backinframe.service import BackInFrame


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    d = tmp_path / "media"
    d.mkdir()
    return d


@pytest.fixture
def settings(tmp_path: Path, media_dir: Path) -> Settings:
    return Settings(media_dir=media_dir, data_dir=tmp_path / "data", ntfy_url="https://ntfy.test",
                    language="nl").validate()


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 11, 7, 8, 0, tzinfo=TZ))


@pytest.fixture
def fake_ntfy() -> FakeNtfy:
    return FakeNtfy()


@pytest.fixture
def db() -> Database:
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def service(settings, fake_ntfy, clock) -> BackInFrame:
    svc = BackInFrame(settings, db=Database(":memory:"), ntfy=fake_ntfy.client(), clock=clock,
                    rng=random.Random(42))
    yield svc
    svc.db.close()


@pytest.fixture
def ntfy_server(tmp_path):
    server = start_ntfy(tmp_path / "ntfy")
    yield server
    server.proc.terminate()
    server.proc.wait(timeout=10)


@pytest.fixture
def strict_ntfy_server(tmp_path):
    """A server with a tiny attachment limit, like a stingy public instance."""
    server = start_ntfy(tmp_path / "ntfy-strict", attachment_limit="300k")
    yield server
    server.proc.terminate()
    server.proc.wait(timeout=10)
