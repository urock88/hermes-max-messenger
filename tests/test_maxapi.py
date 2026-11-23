"""The MAX client, its errors, pacing and the update parser."""

from __future__ import annotations

import pytest
from conftest import UPLOAD_URL, message_update
from max_messenger.maxapi import MaxApiError
from max_messenger.maxapi.client import is_upload_host, parse_retry_after
from max_messenger.maxapi.limits import RateLimiter
from max_messenger.maxapi.models import BotEvent, CallbackPress, IncomingMessage, dedup_key, parse_update


async def test_token_goes_in_the_header_without_bearer(fake_max, make_client):
    client = make_client("abc123")
    await client.get_me()
    call = fake_max.calls[0]
    assert call["headers"]["authorization"] == "abc123"
    assert "access_token" not in call["params"]
    await client.close()


async def test_success_false_on_http_200_is_an_error(fake_max, make_client):
    client = make_client()
    fake_max.queue("PUT", "/messages", {"success": False, "code": "edit.denied", "message": "too old"})
    with pytest.raises(MaxApiError) as caught:
        await client.edit_message("mid.1", text="x")
    assert caught.value.status == 200 and caught.value.code == "edit.denied"
    await client.close()


async def test_429_carries_retry_after_and_pushes_the_limiter_back(fake_max, make_client):
    client = make_client()
    fake_max.queue("POST", "/messages", (429, {"code": "too.many.requests"}, {"Retry-After": "7"}))
    with pytest.raises(MaxApiError) as caught:
        await client.send_message(user_id=1, text="x")
    assert caught.value.is_rate_limited and caught.value.retry_after == 7
    assert caught.value.kind == "rate_limited" and caught.value.is_transient
    assert client.limiter.reserve() > 5  # the next request waits out the penalty
    await client.close()


async def test_a_send_without_a_message_id_is_not_a_success(fake_max, make_client):
    client = make_client()
    fake_max.queue("POST", "/messages", {"message": {"body": {}}})
    with pytest.raises(MaxApiError):
        await client.send_message(user_id=1, text="x")
    await client.close()


async def test_upload_refuses_a_foreign_host_and_never_sends_the_token(fake_max, make_client, tmp_path):
    client = make_client("tok")
    path = tmp_path / "a.txt"
    path.write_text("hello")
    with pytest.raises(MaxApiError):
        await client.upload_file("https://evil.example/upload", path)
    await client.upload_file(UPLOAD_URL, path)
    assert fake_max.uploads and "authorization" not in fake_max.uploads[0]["headers"]
    await client.close()


def test_upload_host_allowlist():
    assert is_upload_host("https://fu.oneme.ru/x")
    assert is_upload_host("https://iu.max.ru/x")
    assert not is_upload_host("http://fu.oneme.ru/x")
    assert not is_upload_host("https://oneme.ru.evil.com/x")


def test_retry_after_forms():
    assert parse_retry_after("3") == 3
    assert parse_retry_after("9999") == 300
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412470) == pytest.approx(10, abs=1)
    assert parse_retry_after("soon") is None


def test_error_kinds():
    assert MaxApiError(403, "chat.denied").kind == "forbidden"
    assert MaxApiError(404).kind == "not_found"
    assert MaxApiError(0, "network").kind == "transient"
    assert MaxApiError(400, "attachment.not.ready").is_not_ready


def test_limiter_spaces_one_chat_but_not_two():
    now = [100.0]
    limiter = RateLimiter(global_per_second=1000, chat_per_second=2, clock=lambda: now[0])
    assert limiter.reserve("a") == 0
    assert limiter.reserve("a") == pytest.approx(0.5)
    assert limiter.reserve("b") == pytest.approx(0.001, abs=0.0005)  # not behind chat a's wait
    assert limiter.reserve(None) == pytest.approx(0.002, abs=0.0005)


def test_limiter_keeps_the_global_rate():
    limiter = RateLimiter(global_per_second=30, chat_per_second=1000, clock=lambda: 0.0)
    waits = [limiter.reserve(str(i)) for i in range(31)]
    assert waits[30] == pytest.approx(1.0, abs=1e-6)  # the 31st request lands a full second later


def test_parse_dialog_message_with_reply_and_voice():
    update = message_update(text="", attachments=[{"type": "audio", "payload": {"url": "https://a/v.ogg", "token": "t"}}],
                            link={"type": "reply", "sender": {"user_id": 900}, "message": {"mid": "mid.0", "text": "q"}})
    record = parse_update(update)
    assert isinstance(record, IncomingMessage) and record.is_dialog
    assert record.attachments[0].kind == "audio" and record.attachments[0].is_media
    assert record.reply_to_mid == "mid.0" and record.reply_to_sender_id == 900
    assert dedup_key(record) == "m:mid.in.1"


def test_parse_forward_uses_the_forwarded_text():
    update = message_update(text="", link={"type": "forward", "sender": {"user_id": 5, "first_name": "Olga"},
                                           "message": {"mid": "x", "text": "original"}})
    record = parse_update(update)
    assert record.text == "original" and record.forwarded_from == "Olga"


def test_parse_callback_and_bot_started():
    press = parse_update({"update_type": "message_callback",
                          "callback": {"callback_id": "cb1", "payload": "hm:x:once", "user": {"user_id": 42}},
                          "message": {"recipient": {"chat_id": 4242, "chat_type": "dialog"}, "body": {"mid": "m1"}}})
    assert isinstance(press, CallbackPress) and press.user.user_id == 42 and press.message_mid == "m1"
    started = parse_update({"update_type": "bot_started", "chat_id": 4242, "user": {"user_id": 42, "name": "Ivan"},
                            "payload": "ref1", "timestamp": 5})
    assert isinstance(started, BotEvent) and started.payload == "ref1" and started.user.name == "Ivan"
    assert parse_update({"update_type": "dialog_muted"}) is None
    assert parse_update("garbage") is None
