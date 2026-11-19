"""The channel classes on their own, against fake services that answer like the real ones."""

import json

import pytest
from channel_fakes import (
    BLOCKED_CHAT,
    DEAD_WEBHOOK,
    GONE_ROOM,
    TELEGRAM_TOKEN,
    UNREGISTERED,
    Services,
    webhook,
)

from backinframe import channels
from backinframe.channels import ChannelError, ChannelGone, Message

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"


def photo(**kw) -> Message:
    return Message(title="Chili 2024", text="1 oktober 2024 · 2 jaar geleden", data=JPEG, filename="IMG_1.jpg",
                   kind="image", **kw)


@pytest.fixture
def services():
    return Services()


def telegram(services):
    return channels.TelegramChannel(TELEGRAM_TOKEN, "https://api.telegram.test", services.client())


# -- addresses ---------------------------------------------------------------------------------
@pytest.mark.parametrize("cls,args,good,bad", [
    (channels.EmailChannel, ("smtp", 587, "", "", "a@b.nl"),
     {"Oma@Example.ORG": "Oma@example.org", " jan.de-vries+lm@post.nl ": "jan.de-vries+lm@post.nl"},
     ["", "oma", "oma@", "@x.nl", "a b@c.nl", "a@b", "a..b@c.nl", "x@y.nl\nBcc: z@q.nl"]),
    (channels.TelegramChannel, ("t",), {"12345": "12345", " -100123 ": "-100123"}, ["", "@user", "12a", "1" * 25]),
    (channels.SignalChannel, ("http://s", "+31600000001"),
     {"+31 6 1234 5678": "+31612345678", "0031612345678": "+31612345678",
      "group.abcdefgh12345=": "group.abcdefgh12345="},
     ["0612345678", "+0612345678", "12", "group.x", "+31612345678; rm"]),
    (channels.MatrixChannel, ("https://m", "t"), {"!abcd:matrix.org": "!abcd:matrix.org"},
     ["#room:matrix.org", "abcd:matrix.org", "!ab", "!abcd"]),
    (channels.DiscordChannel, (), {webhook(): webhook(),
                                   webhook().replace("discord.com", "discordapp.com"): webhook()},
     ["http://discord.com/api/webhooks/1234567/" + "a" * 30, "https://evil.example/api/webhooks/1234567/" + "a" * 30,
      "https://discord.com.evil.example/api/webhooks/1234567/" + "a" * 30,
      "https://user@discord.com/api/webhooks/1234567/" + "a" * 30, "https://discord.com/api/other"]),
])
def test_addresses(cls, args, good, bad):
    ch = cls(*args)
    for raw, canonical in good.items():
        assert ch.check_address(raw) == canonical
    for raw in bad:
        with pytest.raises(ValueError):
            ch.check_address(raw)


# -- Telegram ----------------------------------------------------------------------------------
def test_telegram_sends_a_photo_with_buttons(services):
    telegram(services).send("4242", photo(actions=[("Vaker", "more"), ("Minder", "less")], ref="7"))
    (sent,) = services.sent("sendPhoto")
    assert sent["fields"]["chat_id"] == "4242"
    assert sent["fields"]["caption"] == "<b>Chili 2024</b>\n1 oktober 2024 · 2 jaar geleden"
    assert sent["fields"]["parse_mode"] == "HTML"
    assert sent["files"]["photo"] == ("IMG_1.jpg", JPEG, "image/jpeg")
    keyboard = json.loads(sent["fields"]["reply_markup"])["inline_keyboard"][0]
    assert keyboard == [{"text": "Vaker", "callback_data": "more:7"}, {"text": "Minder", "callback_data": "less:7"}]


def test_telegram_video_and_text(services):
    ch = telegram(services)
    ch.send("1", Message(title="T", data=b"mp4", filename="v.mp4", kind="video"))
    ch.send("1", Message(title="Welkom <3", text="a & b"))
    (video,) = services.sent("sendVideo")
    assert video["files"]["video"][2] == "video/mp4"
    (text,) = services.sent("sendMessage")
    assert text["fields"]["text"] == "<b>Welkom &lt;3</b>\na &amp; b"  # escaped for HTML mode
    assert "reply_markup" not in text["fields"]


def test_telegram_errors(services):
    with pytest.raises(ChannelGone):
        telegram(services).send(BLOCKED_CHAT, photo())
    bad = channels.TelegramChannel("wrong", "https://api.telegram.test", services.client())
    with pytest.raises(ChannelError) as exc:
        bad.send("1", photo())
    assert "wrong" not in str(exc.value) and "Unauthorized" in str(exc.value)  # the token never leaks


def test_telegram_poll_reads_presses_and_messages_once(services):
    ch = telegram(services)
    services.press("4242", "more:7")
    services.say_telegram("555", "/start")
    first = ch.poll()
    assert [(i.address, i.command, i.ref, i.text) for i in first] == [("4242", "more", "7", ""),
                                                                       ("555", "", "", "/start")]
    first[0].answer("Prima!")
    first[1].answer("Je chat-ID is 555")
    assert services.sent("answerCallbackQuery")[0]["fields"]["text"] == "Prima!"
    assert services.sent("sendMessage")[0]["fields"] == {"chat_id": "555", "text": "Je chat-ID is 555"}
    assert ch.poll() == []  # the offset moved on
    assert telegram(services).info() == {"bot": "@BackInFrameTestBot"}


# -- Signal ------------------------------------------------------------------------------------
def test_signal_sends_text_and_attachment(services):
    ch = channels.SignalChannel("http://signal.test", "+31600000001", services.client())
    ch.send("+31612345678", photo(hint="Antwoord vaker, minder of pauze."))
    (body,) = services.signal
    assert body["number"] == "+31600000001" and body["recipients"] == ["+31612345678"]
    assert body["message"] == "Chili 2024\n1 oktober 2024 · 2 jaar geleden\n\nAntwoord vaker, minder of pauze."
    (att,) = body["base64_attachments"]
    assert att.startswith("data:image/jpeg;filename=IMG_1.jpg;base64,")
    with pytest.raises(ChannelGone):
        ch.send(UNREGISTERED, photo())


def test_signal_poll_ignores_groups(services):
    ch = channels.SignalChannel("http://signal.test", "+31600000001", services.client())
    services.say_signal("+31612345678", "vaker")
    services.signal_inbox.append({"envelope": {"sourceNumber": "+31699999999",
                                               "dataMessage": {"message": "more", "groupInfo": {"groupId": "x"}}}})
    (inc,) = ch.poll()
    assert (inc.address, inc.text) == ("+31612345678", "vaker")
    inc.answer("Prima!")
    assert services.signal[-1]["message"] == "Prima!" and services.signal[-1]["recipients"] == ["+31612345678"]


# -- Matrix ------------------------------------------------------------------------------------
def test_matrix_uploads_then_posts(services):
    ch = channels.MatrixChannel("https://matrix.test", "syt_test", services.client())
    ch.send("!room:matrix.test", photo())
    text, upload, image = services.matrix
    assert text["content"]["body"] == "Chili 2024\n1 oktober 2024 · 2 jaar geleden"
    assert upload["bytes"] == JPEG and upload["type"] == "image/jpeg" and upload["filename"] == "IMG_1.jpg"
    assert image["content"]["msgtype"] == "m.image" and image["content"]["url"] == "mxc://matrix.test/m2"
    assert image["room"] == "!room:matrix.test"
    assert services.matrix_joined == {"!room:matrix.test"}  # it accepted the invitation first
    with pytest.raises(ChannelGone):
        ch.send(GONE_ROOM, photo())
    assert ch.info() == {"user": "@memories:matrix.test"}
    with pytest.raises(ChannelError):
        channels.MatrixChannel("https://matrix.test", "wrong", services.client()).send("!room:matrix.test", photo())


# -- Discord -----------------------------------------------------------------------------------
def test_discord_posts_file_without_mentions(services):
    ch = channels.DiscordChannel(services.client())
    ch.send(webhook(), photo())
    (post,) = services.discord
    assert post["payload"]["content"] == "**Chili 2024**\n1 oktober 2024 · 2 jaar geleden"
    assert post["payload"]["allowed_mentions"] == {"parse": []}
    assert post["files"]["files[0]"] == ("IMG_1.jpg", JPEG, "image/jpeg")
    with pytest.raises(ChannelGone):
        ch.send(DEAD_WEBHOOK, photo())
    with pytest.raises(ValueError):  # a stored address that was tampered with is still never contacted
        ch.send("https://evil.example/api/webhooks/123456/" + "a" * 30, photo())
    assert all(r.url.host == "discord.com" for r in services.requests)


# -- e-mail ------------------------------------------------------------------------------------
def test_email_message_has_inline_photo_and_footer():
    ch = channels.EmailChannel("smtp", 587, "", "", "Back in Frame <photos@example.org>")
    msg = ch.build("oma@example.org", photo(footer="Je krijgt dit omdat…"))
    assert msg["Subject"] == "Chili 2024 · 1 oktober 2024 · 2 jaar geleden"
    assert msg["To"] == "oma@example.org" and msg["Auto-Submitted"] == "auto-generated"
    plain = msg.get_body(("plain",)).get_content()
    html_part = msg.get_body(("html",))
    assert "Chili 2024" in plain and "Je krijgt dit omdat" in plain
    image = next(p for p in msg.walk() if p.get_content_type() == "image/jpeg")
    assert image.get_payload(decode=True) == JPEG
    assert f'cid:{image["Content-ID"][1:-1]}' in html_part.get_content()


def test_email_video_is_an_attachment():
    ch = channels.EmailChannel("smtp", 587, "", "", "photos@example.org")
    msg = ch.build("oma@example.org", Message(title="T", data=b"mp4data", filename="v.mp4", kind="video"))
    (att,) = list(msg.iter_attachments())
    assert att.get_content_type() == "video/mp4" and att.get_filename() == "v.mp4"


def test_email_html_is_escaped():
    ch = channels.EmailChannel("smtp", 587, "", "", "photos@example.org")
    msg = ch.build("a@b.nl", Message(title="<script>x</script>", text="a & b"))
    assert "<script>" not in msg.get_body(("html",)).get_content()
