import json

import httpx
import pytest

from backinframe.ntfy import HttpAction, NtfyClient, NtfyError


def client_with(handler, token=""):
    return NtfyClient("https://ntfy.example/", token=token,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_publish_with_attachment_title_and_actions():
    seen = {}

    def handler(req: httpx.Request):
        seen["method"] = req.method
        seen["url"] = req.url
        seen["body"] = req.content
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": "abc"})

    c = client_with(handler, token="tk_secret")
    actions = [HttpAction("Vaker", "https://ntfy.example/ctl", "more"),
               HttpAction("Pauze 1 week", "https://ntfy.example/ctl", "pause")]
    res = c.publish("topic1", "9 november 2024 · 2 jaar geleden", title="Chili ☀",
                    data=b"JPEGDATA", filename="a.jpg", actions=actions)
    assert res == {"id": "abc"}
    assert seen["method"] == "PUT"
    assert seen["url"].path == "/topic1"
    assert seen["body"] == b"JPEGDATA"
    assert seen["auth"] == "Bearer tk_secret"
    params = seen["url"].params
    assert params["title"] == "Chili ☀"
    assert params["filename"] == "a.jpg"
    assert params["message"].startswith("9 november")
    assert params["actions"] == (
        "http, Vaker, https://ntfy.example/ctl, method=POST, body=more, clear=true; "
        "http, Pauze 1 week, https://ntfy.example/ctl, method=POST, body=pause, clear=true"
    )


def test_action_rejects_separators():
    with pytest.raises(ValueError):
        HttpAction("a,b", "https://x", "more").render()


def test_publish_errors():
    c = client_with(lambda req: httpx.Response(413, json={"error": "too large"}))
    with pytest.raises(NtfyError, match="too large"):
        c.publish("t", "m", data=b"x", filename="x.jpg")
    c = client_with(lambda req: httpx.Response(429, text="slow down"))
    with pytest.raises(NtfyError, match="429"):
        c.publish("t", "m")

    def boom(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(NtfyError, match="cannot reach"):
        client_with(boom).publish("t", "m")


def test_attachment_limit():
    c = client_with(lambda req: httpx.Response(200, json={"limits": {"attachment_file_size": 2097152}}))
    assert c.attachment_limit() == 2097152
    assert client_with(lambda req: httpx.Response(404)).attachment_limit() is None
    assert client_with(lambda req: httpx.Response(200, json={})).attachment_limit() is None


def test_poll_parses_ndjson():
    lines = [
        {"id": "1", "event": "open", "topic": "a"},
        {"id": "2", "event": "message", "topic": "a", "message": "more", "time": 10},
        {"id": "3", "event": "message", "topic": "b", "message": "less", "time": 11},
    ]

    def handler(req):
        assert req.url.path == "/a,b/json"
        assert req.url.params["poll"] == "1"
        assert req.url.params["since"] == "123"
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines) + "\nnot json\n")

    msgs = client_with(handler).poll(["a", "b"], "123")
    assert [m["id"] for m in msgs] == ["2", "3"]
    assert client_with(handler).poll([], "1") == []


def test_poll_error():
    with pytest.raises(NtfyError):
        client_with(lambda req: httpx.Response(500)).poll(["a"], "all")


def test_subscribe_link_for_android_deep_linking():
    from backinframe.ntfy import subscribe_link

    assert subscribe_link("https://ntfy.sh", "backinframe-abc", "Back in Frame") == \
        "ntfy://ntfy.sh/backinframe-abc?display=Back%20in%20Frame"
    assert subscribe_link("https://push.example.org/", "t1") == "ntfy://push.example.org/t1"
    # plain http (a LAN server) must tell the app not to use https
    assert subscribe_link("http://192.168.1.5:8081", "t1", "Oma Riet") == \
        "ntfy://192.168.1.5:8081/t1?display=Oma%20Riet&secure=false"
