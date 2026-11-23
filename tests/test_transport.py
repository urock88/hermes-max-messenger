"""State, inbox, polling and the webhook receiver."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from max_messenger.maxapi import MaxApiError
from max_messenger.transport.inbox import Inbox
from max_messenger.transport.polling import Poller, backoff_delay
from max_messenger.transport.webhook import SECRET_HEADER, WebhookServer


def test_state_survives_a_restart(state_factory):
    first = state_factory()
    first.save_marker(77)
    first.remember_dialog("42", "4242")
    first.remember_group("-5")
    assert first.first_seen("m:1") and not first.first_seen("m:1")
    first.close()
    second = state_factory()
    assert second.marker() == 77 and second.dialog_chat("42") == "4242" and second.is_group("-5")
    assert not second.first_seen("m:1")
    assert state_factory(bot="other").marker() is None  # bots are kept apart


async def test_inbox_keeps_order_per_chat_and_runs_chats_side_by_side():
    seen: list[str] = []
    gate = asyncio.Event()

    async def handle(item):
        if item == "a1":
            await gate.wait()
        seen.append(item)

    inbox = Inbox(handle)
    for key, item in (("a", "a1"), ("a", "a2"), ("b", "b1")):
        inbox.submit(key, item)
    await asyncio.sleep(0.01)
    assert seen == ["b1"]  # chat b is not stuck behind chat a
    gate.set()
    await inbox.join()
    assert seen == ["b1", "a1", "a2"]
    await inbox.close()


async def test_poller_saves_the_marker_and_backs_off(fake_max, make_client, no_sleep):
    client = make_client()
    saved, got = [], []
    fake_max.queue("GET", "/updates", (502, {}))
    fake_max.updates = [{"update_type": "message_created"}]
    poller = Poller(client, on_update=got.append, load_marker=lambda: 5, save_marker=saved.append,
                    on_fatal=None, types=("message_created",), sleep=no_sleep)
    task = asyncio.create_task(poller.run())
    for _ in range(100):
        if saved:
            break
        await asyncio.sleep(0.01)
    poller.stop()
    await asyncio.wait_for(task, 1)
    calls = fake_max.made("GET", "/updates")
    assert calls[0]["params"]["marker"] == "5" and calls[0]["params"]["types"] == "message_created"
    assert got == [{"update_type": "message_created"}] and saved[0] == 1
    await client.close()


async def test_poller_stops_on_a_refused_token(fake_max, make_client, no_sleep):
    client = make_client()
    fake_max.queue("GET", "/updates", (401, {"code": "verify.token"}))
    fatal: list[MaxApiError] = []

    async def on_fatal(exc):
        fatal.append(exc)

    poller = Poller(client, on_update=lambda u: None, load_marker=lambda: None, save_marker=lambda m: None,
                    on_fatal=on_fatal, types=(), sleep=no_sleep)
    await asyncio.wait_for(poller.run(), 1)
    assert fatal and fatal[0].is_auth
    await client.close()


def test_backoff_grows_and_respects_retry_after():
    assert backoff_delay(1, rng=lambda: 0.5) == pytest.approx(2.0)
    assert backoff_delay(10, rng=lambda: 0.5) == pytest.approx(60.0)
    assert backoff_delay(1, retry_after=30, rng=lambda: 0.5) == 30


async def test_webhook_checks_the_secret_before_anything_else():
    got = []
    server = WebhookServer(secret="s3cret_value", on_update=got.append)
    async with TestClient(TestServer(server.build_app())) as client:
        assert (await client.post("/max/webhook", json={"a": 1})).status == 401
        assert (await client.post("/max/webhook", json={"a": 1}, headers={SECRET_HEADER: "wrong"})).status == 401
        assert (await client.post("/max/webhook", data="{", headers={SECRET_HEADER: "s3cret_value"})).status == 400
        ok = await client.post("/max/webhook", json={"update_type": "x"}, headers={SECRET_HEADER: "s3cret_value"})
        assert ok.status == 200 and got == [{"update_type": "x"}]
        assert (await client.get("/health")).status == 200


def test_webhook_refuses_a_weak_secret():
    with pytest.raises(ValueError):
        WebhookServer(secret="abc", on_update=lambda u: None)
