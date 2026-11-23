"""Addressing, uploads and the send retries."""

from __future__ import annotations

import pytest
from max_messenger.maxapi import MaxApiError
from max_messenger.outbound.sender import Target, deliver, resolve_target, upload, upload_kind


def test_resolve_target():
    assert resolve_target("42") == Target(user_id=42)
    assert resolve_target("-1001") == Target(chat_id=-1001)
    assert resolve_target("77", is_group=lambda c: c == "77") == Target(chat_id=77)
    assert resolve_target("chat:5") == Target(chat_id=5)
    with pytest.raises(ValueError):
        resolve_target("not-a-number")


def test_upload_kind(tmp_path):
    for name, kind in (("a.jpg", "image"), ("b.mp4", "video"), ("c.ogg", "audio"), ("d.pdf", "file")):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert upload_kind(path) == kind
    assert upload_kind(tmp_path / "d.pdf", voice=True) == "audio"


async def test_audio_token_comes_from_the_upload_slot(fake_max, make_client, tmp_path):
    client = make_client()
    path = tmp_path / "v.ogg"
    path.write_bytes(b"OggS")
    fake_max.upload_answer = {}
    attachment = await upload(client, path, voice=True)
    assert attachment == {"type": "audio", "payload": {"token": "audio-token"}}
    await client.close()


async def test_image_token_comes_from_photos(fake_max, make_client, tmp_path):
    client = make_client()
    path = tmp_path / "p.png"
    path.write_bytes(b"\x89PNG")
    fake_max.upload_answer = {"photos": {"123": {"token": "photo-token"}}}
    assert (await upload(client, path))["payload"]["token"] == "photo-token"
    await client.close()


async def test_not_ready_is_retried(fake_max, make_client, no_sleep):
    client = make_client()
    for _ in range(2):
        fake_max.queue("POST", "/messages", (400, {"code": "attachment.not.ready", "message": "wait"}))
    message = await deliver(client, Target(user_id=1), html=None, plain="", sleep=no_sleep,
                            attachments=[{"type": "file", "payload": {"token": "t"}}])
    assert message["body"]["mid"] and len(fake_max.made("POST", "/messages")) == 3
    await client.close()


async def test_refused_html_is_resent_as_plain_text(fake_max, make_client):
    client = make_client()
    fake_max.queue("POST", "/messages", (400, {"code": "proto.payload", "message": "bad markup"}))
    await deliver(client, Target(user_id=1), html="<b>x</b>", plain="x")
    first, second = fake_max.made("POST", "/messages")
    assert first["json"]["format"] == "html" and "format" not in second["json"] and second["json"]["text"] == "x"
    await client.close()


async def test_other_errors_are_not_retried(fake_max, make_client):
    client = make_client()
    fake_max.queue("POST", "/messages", (403, {"code": "chat.denied"}))
    with pytest.raises(MaxApiError) as caught:
        await deliver(client, Target(user_id=1), html=None, plain="x")
    assert caught.value.kind == "forbidden" and len(fake_max.made("POST", "/messages")) == 1
    await client.close()
