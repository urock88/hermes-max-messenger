"""The adapter against the real Hermes core and a fake MAX API."""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest
from conftest import BOT_ID, message_update
from gateway.config import PlatformConfig
from gateway.platforms.event import MessageType
from max_messenger import adapter as adapter_module
from max_messenger.adapter import MaxAdapter
from max_messenger.inbound.media import InboundMedia, download_all
from max_messenger.maxapi.models import Attachment
from max_messenger.settings import Settings


@pytest.fixture
async def make_adapter(make_client, state_factory):
    built: list[MaxAdapter] = []

    async def factory(*, connect: bool = True, **settings) -> MaxAdapter:
        values = {"token": "secret-token", "update_commands": False, **settings}
        adapter = MaxAdapter(PlatformConfig(enabled=True, token="secret-token"), settings=Settings(**values),
                             client_factory=lambda s: make_client(s.token), state_factory=state_factory)
        adapter.events = []

        async def capture(event):
            adapter.events.append(event)

        adapter.handle_message = capture
        built.append(adapter)
        if connect:
            assert await adapter.connect()
        return adapter

    yield factory
    for adapter in built:
        await adapter.disconnect()


async def feed(adapter: MaxAdapter, *updates) -> None:
    for update in updates:
        adapter._on_raw_update(update)
    await adapter._inbox.join()


async def test_dm_text_reaches_hermes_with_the_right_source(make_adapter, fake_max):
    adapter = await make_adapter()
    await feed(adapter, message_update(text="hello", user_id=42, chat_id=4242))
    (event,) = adapter.events
    assert event.text == "hello" and event.message_type == MessageType.TEXT and event.message_id == "mid.in.1"
    assert (event.source.chat_id, event.source.chat_type, event.source.user_id) == ("42", "dm", "42")
    assert adapter._state.dialog_chat("42") == "4242"


async def test_a_redelivered_update_is_handled_once(make_adapter):
    adapter = await make_adapter()
    update = message_update(text="once")
    await feed(adapter, update, update)
    assert len(adapter.events) == 1


async def test_polling_runs_and_saves_the_marker(make_adapter, fake_max):
    adapter = await make_adapter()
    fake_max.updates = [message_update(text="via poll")]
    for _ in range(200):
        if adapter.events:
            break
        await asyncio.sleep(0.01)
    assert adapter.events[0].text == "via poll"
    for _ in range(100):
        if adapter._state.marker() == 1:
            break
        await asyncio.sleep(0.01)
    assert adapter._state.marker() == 1


async def test_stranger_attachments_are_not_downloaded(make_adapter, monkeypatch):
    adapter = await make_adapter()
    downloads = []

    async def fake_download(attachments, client=None):
        downloads.append(attachments)
        return InboundMedia(paths=["/tmp/p.jpg"], mime_types=["image/jpeg"], filenames=["p.jpg"], has_image=True)

    monkeypatch.setattr(adapter_module, "download_all", fake_download)
    adapter._authorization_check = lambda user_id, chat_type, chat_id, **kw: user_id == "42"
    photo = [{"type": "image", "payload": {"url": "https://i.oneme.ru/p.jpg", "photo_id": 1}}]
    await feed(adapter, message_update(text="", user_id=13, mid="m.a", attachments=photo),
               message_update(text="", user_id=42, mid="m.b", attachments=photo))
    stranger, owner = adapter.events
    assert stranger.media_urls == [] and "[image" in stranger.text
    assert owner.media_urls == ["/tmp/p.jpg"] and owner.message_type == MessageType.PHOTO
    assert len(downloads) == 1


async def test_voice_note_is_a_voice_event(make_adapter, monkeypatch):
    adapter = await make_adapter()

    async def fake_download(attachments, client=None):
        return InboundMedia(paths=["/tmp/v.ogg"], mime_types=["audio/ogg"], filenames=["v.ogg"], has_voice=True)

    monkeypatch.setattr(adapter_module, "download_all", fake_download)
    adapter._authorization_check = lambda *a, **kw: True
    voice = [{"type": "audio", "payload": {"url": "https://a.oneme.ru/v.ogg", "token": "t"}}]
    await feed(adapter, message_update(text="", attachments=voice))
    assert adapter.events[0].message_type == MessageType.VOICE


async def test_download_all_caches_through_core(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"0" * 64, headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        media = await download_all((Attachment(kind="image", url="https://i.oneme.ru/x"),
                                    Attachment(kind="contact", payload={"name": "Olga", "vcf_info": "TEL;CELL:+7900"})),
                                   client=client)
    assert media.has_image and media.paths[0].endswith(".png")
    assert media.notes == ["[contact: Olga +7900]"]


async def test_group_needs_a_mention(make_adapter):
    adapter = await make_adapter()
    await feed(adapter, message_update(text="chatter", chat_id=-500, chat_type="chat", mid="g1"),
               message_update(text="@hermes_bot ping", chat_id=-500, chat_type="chat", mid="g2"))
    (event,) = adapter.events
    assert event.text == "ping" and event.source.chat_id == "-500" and event.source.chat_type == "group"
    assert adapter._state.is_group("-500")


async def test_bot_started_becomes_start(make_adapter):
    adapter = await make_adapter()
    await feed(adapter, {"update_type": "bot_started", "chat_id": 4242, "user": {"user_id": 42, "name": "Ivan"},
                         "payload": "ref", "timestamp": 1})
    assert adapter.events[0].text == "/start ref" and adapter.events[0].message_type == MessageType.COMMAND


async def test_long_reply_is_split_into_html_messages(make_adapter, fake_max):
    adapter = await make_adapter()
    result = await adapter.send("42", "**Hi**\n\n" + "word " * 1200)
    sends = fake_max.made("POST", "/messages")
    assert result.success and len(sends) == 2 and result.continuation_message_ids
    assert all(s["params"] == {"user_id": "42"} and s["json"]["format"] == "html" for s in sends)
    assert sends[0]["json"]["text"].startswith("<b>Hi</b>")


async def test_group_reply_goes_by_chat_id(make_adapter, fake_max):
    adapter = await make_adapter()
    await adapter.send("-500", "hi")
    assert fake_max.made("POST", "/messages")[0]["params"] == {"chat_id": "-500"}


async def test_send_failure_is_typed(make_adapter, fake_max):
    adapter = await make_adapter()
    fake_max.queue("POST", "/messages", (403, {"code": "chat.denied", "message": "bot stopped"}))
    result = await adapter.send("42", "hi")
    assert not result.success and result.error_kind == "forbidden" and not result.retryable


async def test_typing_uses_the_dialog_chat_id(make_adapter, fake_max):
    adapter = await make_adapter()
    await adapter.send_typing("42")
    assert not fake_max.made("POST", "/chats/4242/actions")  # no chat id known yet
    await feed(adapter, message_update(user_id=42, chat_id=4242))
    await adapter.send_typing("42")
    assert fake_max.made("POST", "/chats/4242/actions")[0]["json"] == {"action": "typing_on"}


async def test_edit_falls_back_to_plain_text(make_adapter, fake_max):
    adapter = await make_adapter()
    fake_max.queue("PUT", "/messages", (400, {"code": "bad.markup"}))
    result = await adapter.edit_message("42", "mid.5", "**done**")
    first, second = fake_max.made("PUT", "/messages")
    assert result.success and first["json"]["format"] == "html" and second["json"] == {"text": "done"}


async def test_file_is_uploaded_and_sent(make_adapter, fake_max, tmp_path):
    adapter = await make_adapter()
    await feed(adapter, message_update(user_id=42, chat_id=4242))
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4")
    result = await adapter.send_document("42", str(path), caption="Отчёт")
    assert result.success
    assert fake_max.made("POST", "/uploads")[0]["params"]["type"] == "file"
    assert fake_max.made("POST", "/chats/4242/actions")[-1]["json"] == {"action": "sending_file"}
    sent = fake_max.made("POST", "/messages")[-1]["json"]
    assert sent["attachments"] == [{"type": "file", "payload": {"token": "file-token"}}] and sent["text"] == "Отчёт"


async def test_exec_approval_buttons_answer_only_the_owner(make_adapter, fake_max, monkeypatch):
    adapter = await make_adapter()
    resolved = []
    import tools.approval

    monkeypatch.setattr(tools.approval, "resolve_gateway_approval",
                        lambda session_key, choice: resolved.append((session_key, choice)) or 1)
    result = await adapter.send_exec_approval(chat_id="42", command="rm -rf /tmp/x", session_key="sess-1")
    assert result.success
    sent = fake_max.made("POST", "/messages")[-1]["json"]
    assert "<pre>rm -rf /tmp/x</pre>" in sent["text"] and sent["format"] == "html"
    buttons = sent["attachments"][0]["payload"]["buttons"]
    once = buttons[0][0]["payload"]

    def press(user_id: int, cb: str) -> dict:
        return {"update_type": "message_callback",
                "callback": {"callback_id": cb, "payload": once, "user": {"user_id": user_id, "first_name": "U"}},
                "message": {"recipient": {"chat_id": 4242, "chat_type": "dialog"}, "body": {"mid": result.message_id}}}

    await feed(adapter, press(13, "cb-stranger"))
    stranger_answer = fake_max.made("POST", "/answers")[-1]
    assert stranger_answer["params"]["callback_id"] == "cb-stranger" and "message" not in stranger_answer["json"]
    assert not resolved
    await feed(adapter, press(42, "cb-owner"))
    owner_answer = fake_max.made("POST", "/answers")[-1]
    assert resolved == [("sess-1", "once")]
    assert owner_answer["json"]["message"]["attachments"] == []  # keyboard removed, decision shown


async def test_clarify_buttons_resolve_with_the_choice_text(make_adapter, fake_max, monkeypatch):
    adapter = await make_adapter()
    import tools.clarify_gateway

    picked = []
    monkeypatch.setattr(tools.clarify_gateway, "resolve_gateway_clarify",
                        lambda clarify_id, text: picked.append((clarify_id, text)) or True)
    result = await adapter.send_clarify("42", "Which one?", ["red", "blue"], "cl-1", "sess-2")
    buttons = fake_max.made("POST", "/messages")[-1]["json"]["attachments"][0]["payload"]["buttons"]
    second = buttons[0][1]["payload"]
    await feed(adapter, {"update_type": "message_callback",
                         "callback": {"callback_id": "c", "payload": second, "user": {"user_id": 42}},
                         "message": {"recipient": {"chat_id": 4242, "chat_type": "dialog"},
                                     "body": {"mid": result.message_id}}})
    assert picked == [("cl-1", "blue")]


async def test_model_picker_handles_ids_with_colons(make_adapter, fake_max):
    adapter = await make_adapter()
    chosen = []

    async def on_model_selected(chat_id, model_id, provider):
        chosen.append((chat_id, model_id, provider))
        return f"Switched to {model_id}"

    providers = [{"slug": "ollama", "name": "Ollama", "models": ["qwen3:8b", "llama3:70b"]}]
    result = await adapter.send_model_picker("42", providers, "x", "y", "sess-3", on_model_selected)

    async def press(choice_index: tuple[int, int], cb: str):
        message = fake_max.made("POST", "/answers")[-1]["json"]["message"] if fake_max.made("POST", "/answers") \
            else fake_max.made("POST", "/messages")[-1]["json"]
        row, col = choice_index
        data = message["attachments"][0]["payload"]["buttons"][row][col]["payload"]
        await feed(adapter, {"update_type": "message_callback",
                             "callback": {"callback_id": cb, "payload": data, "user": {"user_id": 42}},
                             "message": {"recipient": {"chat_id": 4242, "chat_type": "dialog"},
                                         "body": {"mid": result.message_id}}})

    await press((0, 0), "p1")  # the provider
    await press((0, 0), "p2")  # its first model
    assert chosen == [("42", "qwen3:8b", "ollama")]


async def test_polling_refuses_to_steal_a_webhook(make_adapter, fake_max):
    fake_max.subscriptions = [{"url": "https://example.org/hook"}]
    adapter = await make_adapter(connect=False)
    assert not await adapter.connect()
    assert "MAX_POLLING_TAKEOVER=true" in (adapter._fatal_error_message or "")
    assert not fake_max.made("DELETE", "/subscriptions")


async def test_polling_takeover_removes_the_webhook(make_adapter, fake_max):
    fake_max.subscriptions = [{"url": "https://example.org/hook"}]
    await make_adapter(polling_takeover=True)
    assert fake_max.made("DELETE", "/subscriptions")[0]["params"] == {"url": "https://example.org/hook"}


async def test_webhook_mode_subscribes_with_the_secret(make_adapter, fake_max, unused_tcp_port):
    await make_adapter(webhook_url="https://bot.example.org/max/webhook", webhook_secret="s3cret_value",
                       webhook_port=unused_tcp_port)
    subscribe = fake_max.made("POST", "/subscriptions")[0]["json"]
    assert subscribe["url"] == "https://bot.example.org/max/webhook" and subscribe["secret"] == "s3cret_value"
    assert "message_created" in subscribe["update_types"]
    assert not fake_max.made("GET", "/updates")


def test_register_builds_a_valid_platform_entry_and_stt_providers():
    from gateway.platform_registry import PlatformEntry
    from max_messenger import register

    class Ctx:
        def __init__(self):
            self.platform, self.providers = None, []

        def register_platform(self, name, label, adapter_factory, check_fn, validate_config=None,
                              required_env=None, install_hint="", **kwargs):
            known = {f.name for f in dataclasses.fields(PlatformEntry)}
            assert set(kwargs) <= known, set(kwargs) - known
            self.platform = PlatformEntry(name=name, label=label, adapter_factory=adapter_factory, check_fn=check_fn,
                                          validate_config=validate_config, required_env=required_env or [],
                                          install_hint=install_hint, **kwargs)

        def register_transcription_provider(self, provider):
            self.providers.append(provider.name)

    ctx = Ctx()
    register(ctx)
    assert ctx.platform.name == "max" and ctx.platform.allowed_users_env == "MAX_ALLOWED_USERS"
    assert ctx.platform.allowlist_suppresses_pairing is False
    assert ctx.providers == ["yandex_speechkit", "sber_salutespeech"]
    assert ctx.platform.check_fn()


async def test_standalone_send(fake_max, make_client, monkeypatch, tmp_path):
    from max_messenger import standalone

    monkeypatch.setattr(standalone, "MaxClient", lambda token, api_base=None: make_client(token))
    path = tmp_path / "chart.png"
    path.write_bytes(b"\x89PNG")
    fake_max.upload_answer = {"photos": {"1": {"token": "img"}}}
    result = await standalone.standalone_send(PlatformConfig(enabled=True, token="tok"), "42", "Cron done",
                                              media_files=[str(path)])
    assert result["success"]
    texts = [c["json"].get("text") for c in fake_max.made("POST", "/messages")]
    assert texts == ["Cron done", None]


async def test_tables_are_all_pictures_or_all_text(make_adapter, fake_max, monkeypatch, tmp_path):
    from max_messenger.outbound import tables

    adapter = await make_adapter(table_as_image=True)
    picture = tmp_path / "t.png"
    picture.write_bytes(b"\x89PNG")
    answers = iter([picture, None])  # the second table cannot be drawn
    monkeypatch.setattr(tables, "available", lambda: True)
    monkeypatch.setattr(tables, "render_table_png", lambda lines, out_dir: next(answers))
    text = "A\n| a | b |\n|---|---|\n| 1 | 2 |\n\nB\n| c | d |\n|---|---|\n| 3 | 4 |"
    await adapter.send("42", text)
    sent = fake_max.made("POST", "/messages")
    assert len(sent) == 1 and "<pre>a  b" in sent[0]["json"]["text"] and "<pre>c  d" in sent[0]["json"]["text"]


async def test_command_menu_is_published_in_the_bot_language(make_adapter, fake_max):
    await make_adapter(update_commands=True)
    for _ in range(100):
        if fake_max.made("PATCH", "/me/commands"):
            break
        await asyncio.sleep(0.01)
    commands = fake_max.made("PATCH", "/me/commands")[0]["json"]["commands"]
    assert commands[0] == {"name": "new", "description": "Новый разговор"} and len(commands) <= 32


async def test_the_bot_ignores_its_own_messages(make_adapter):
    adapter = await make_adapter()
    await feed(adapter, message_update(text="echo", user_id=BOT_ID))
    assert adapter.events == []
