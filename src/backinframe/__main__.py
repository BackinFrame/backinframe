"""Entry point: ``python -m backinframe``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import threading

import uvicorn

from .app import create_recipient_app
from .config import ConfigError, from_env
from .service import BackInFrame
from .web import create_app


class QuietServer(uvicorn.Server):
    """Two servers in one process: we handle the signals ourselves and stop both."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def reset_2fa() -> int:
    """Lost the phone and the recovery codes: turn two-step verification off and end all sessions.
    Run it where the container runs: ``docker exec backinframe backinframe reset-2fa``."""
    from .adminauth import AdminAuth
    from .db import Database

    try:
        settings = from_env()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 2
    db = Database(settings.db_path)
    auth = AdminAuth(db, settings.admin_password, settings.data_dir, lambda: None)
    auth.disable()
    auth.end_all()
    db.close()
    logging.info("Two-step verification for the admin page is off; every session has ended.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    if sys.argv[1:] == ["reset-2fa"]:
        return reset_2fa()
    # httpx logs full URLs, which contain the (secret) topic names.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = from_env()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 2

    service = BackInFrame(settings)
    stop = threading.Event()
    worker = threading.Thread(target=service.run_forever, args=(stop,), name="backinframe-loop", daemon=True)
    worker.start()

    servers = [QuietServer(uvicorn.Config(
        create_app(service), host=settings.web_host, port=settings.web_port, log_level="warning", access_log=False,
    ))]
    if settings.app_enabled:
        _ = service.vapid  # create the push key at start, not on the first visit
        servers.append(QuietServer(uvicorn.Config(
            create_recipient_app(service), host=settings.web_host, port=settings.app_port,
            log_level="warning", access_log=False,  # the links in the paths are secrets
            proxy_headers=True, forwarded_allow_ips="*",
        )))

    def shutdown(*_):
        stop.set()
        for server in servers:
            server.should_exit = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logging.info("Back in Frame admin on http://%s:%s (ntfy: %s)", settings.web_host, settings.web_port,
                 settings.ntfy_url)
    if settings.app_enabled:
        logging.info("Recipient web app on port %s, public address %s", settings.app_port, settings.public_app_url)

    async def serve() -> None:
        await asyncio.gather(*(server.serve() for server in servers))

    asyncio.run(serve())
    stop.set()
    worker.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
