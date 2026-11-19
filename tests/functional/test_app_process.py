"""Black-box: start the real program (`python -m backinframe`) next to a real ntfy server."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from helpers import free_port, make_image, wait_for

pytestmark = pytest.mark.ntfy

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def app(ntfy_server, tmp_path):
    media = tmp_path / "media"
    make_image(media / "Oostenrijk" / "bergen.jpg", taken=datetime(2023, 8, 1, 9))
    port, app_port = free_port(), free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        "BACKINFRAME_MEDIA_DIR": str(media),
        "BACKINFRAME_DATA_DIR": str(tmp_path / "data"),
        "BACKINFRAME_NTFY_URL": ntfy_server.url,
        "BACKINFRAME_WEB_HOST": "127.0.0.1",
        "BACKINFRAME_WEB_PORT": str(port),
        "BACKINFRAME_POLL_SECONDS": "1",
        "BACKINFRAME_ADMIN_PASSWORD": "secret",
        "BACKINFRAME_APP_URL": f"http://localhost:{app_port}",
        "BACKINFRAME_APP_PORT": str(app_port),
    }
    proc = subprocess.Popen([sys.executable, "-m", "backinframe"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for(f"{url}/healthz", timeout=20)
    except RuntimeError:
        proc.kill()
        print(proc.stdout.read())
        raise
    wait_for(f"http://127.0.0.1:{app_port}/healthz", timeout=20)
    yield url, ntfy_server, proc, f"http://127.0.0.1:{app_port}"
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("backinframe did not stop on SIGTERM")


def test_program_end_to_end(app):
    url, ntfy, proc, app_url = app
    first = httpx.get(url)
    assert first.status_code == 303 and first.headers["location"].startswith("/login")  # needs the password
    c = httpx.Client(base_url=url, follow_redirects=True, timeout=30)
    assert c.post("/login", data={"password": "secret"}).url.path == "/"

    # background scan picked up the photo
    health = c.get("/healthz").json()
    assert health["status"] == "ok"
    for _ in range(50):
        if c.get("/healthz").json()["media"]["image"] == 1:
            break
        import time
        time.sleep(0.2)
    assert c.get("/healthz").json()["media"]["image"] == 1

    # add a recipient through the page
    page = c.post("/recipients", data={"name": "Vera"})
    assert page.status_code == 200
    # with the web app on, new recipients get it; switch this one to ntfy for the phone test below
    token = re.search(r"/a/([A-Za-z0-9_-]+)", page.text).group(1)
    assert httpx.get(f"{app_url}/a/{token}/").status_code == 200  # no admin password on the app port
    assert httpx.get(f"{app_url}/").status_code == 404           # and nothing else there
    rid = re.search(r"/recipients/(\d+)", str(page.url)).group(1)
    form = {"name": "Vera", "per_day": "1", "every_days": "1", "window_start": "09:00", "window_end": "21:00",
            "min_gap_minutes": "60", "enabled": "1", "delivery": "ntfy"}
    page = c.post(f"/recipients/{rid}", data=form)
    topic = re.search(r'<code id="topic">([^<]+)</code>', page.text).group(1)
    rid = re.search(r"/recipients/(\d+)", str(page.url)).group(1)

    # "send now" delivers to the phone
    c.post(f"/recipients/{rid}/send")
    msgs = ntfy.wait_messages(topic, 2)
    photo = msgs[-1]
    assert photo["title"] == "Oostenrijk"
    assert photo["attachment"]["name"] == "bergen.jpg"

    # pressing a button on the phone is picked up by the running program
    action = next(a for a in photo["actions"] if a["label"] == "Less")
    httpx.post(action["url"], content=action["body"], timeout=10)
    reply = ntfy.wait_messages(topic, 3, timeout=30)[-1]
    assert "1 memory every 2 days" in reply["message"]
    assert "1 memory every 2 days" in c.get(f"/recipients/{rid}").text

    # clean shutdown
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=15) == 0
