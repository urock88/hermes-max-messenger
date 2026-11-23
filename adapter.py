"""The MAX gateway adapter: glue between the MAX Bot API and Hermes' ``BasePlatformAdapter``.

What Hermes core already does for every adapter and this one only plugs into:

* the "typing…" indicator — core calls :meth:`MaxAdapter.send_typing` every 2 s while the agent
  thinks, pauses it while a person decides an approval, and stops it when the answer is out;
* streamed replies — core sends the first piece, then calls :meth:`MaxAdapter.edit_message`;
* authorization and pairing — unknown senders reach core, which answers with a pairing code;
* splitting long replies (code fences kept intact) and cron delivery.

Chat ids: a person is their MAX user id (positive), a group chat its chat id (negative, or any id
the bot has seen as a group). That is also the form for ``MAX_HOME_CHANNEL`` and ``max:<id>``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType

from .inbound.groups import addressed_text
from .inbound.media import InboundMedia, describe, download_all
from .interactive.handlers import InteractiveMixin, html_to_plain
from .interactive.prompts import Prompts
from .maxapi import MaxApiError, MaxClient
from .maxapi.models import (
    DIALOG,
    UPDATE_TYPES,
    BotEvent,
    CallbackPress,
    IncomingMessage,
    dedup_key,
    parse_update,
)
from .outbound import formatting
from .outbound.sender import Target, deliver, message_id, resolve_target, upload
from .settings import PLATFORM, Settings, read_settings
from .state import State
from .transport.inbox import Inbox
from .transport.polling import Poller
from .transport.webhook import WebhookServer

logger = logging.getLogger(__name__)

# Room for the markup a chunk gains in HTML: MAX caps a message at 4000 characters.
SOURCE_CHUNK = 3500
MAX_TEXT = 4000
_UPLOAD_ACTION = {"image": "sending_photo", "video": "sending_video", "audio": "sending_audio", "file": "sending_file"}


def _not_connected() -> SendResult:
    return SendResult(success=False, error="MAX adapter is not connected", retryable=True, error_kind="transient")


def _failure(exc: Exception) -> SendResult:
    if isinstance(exc, MaxApiError):
        return SendResult(success=False, error=str(exc), retryable=exc.is_transient,
                          retry_after=exc.retry_after, error_kind=exc.kind)
    return SendResult(success=False, error=f"{type(exc).__name__}: {exc}", error_kind="unknown")


class MaxAdapter(InteractiveMixin, BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = SOURCE_CHUNK
    splits_long_messages = True
    supports_code_blocks = True

    def __init__(self, config: PlatformConfig, *, settings: Settings | None = None,
                 client_factory=None, state_factory=None):
        super().__init__(config, Platform(PLATFORM))
        self.settings = settings or read_settings(config)
        self._language = self.settings.language
        self._client_factory = client_factory or (lambda s: MaxClient(s.token, api_base=s.api_base))
        self._state_factory = state_factory or (lambda bot: State(bot=bot))
        self._client: MaxClient | None = None
        self._state: State | None = None
        self._inbox: Inbox | None = None
        self._poller: Poller | None = None
        self._poll_task: asyncio.Task | None = None
        self._webhook: WebhookServer | None = None
        self._side_tasks: set[asyncio.Task] = set()
        self._prompts = Prompts()
        self.bot_id: int | None = None
        self.bot_username = ""
        self._session_owners: dict[str, str] = {}
        self._chat_requesters: dict[str, str] = {}

    # ── lifecycle ───────────────────────────────────────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        s = self.settings
        if not s.token:
            self._set_fatal_error("max_no_token", "MAX_BOT_TOKEN is not set", retryable=False)
            return False
        client = self._client_factory(s)
        try:
            me = await client.get_me()
        except MaxApiError as exc:
            await client.close()
            if exc.is_auth:
                self._set_fatal_error("max_auth", "MAX refused the bot token (HTTP 401): check MAX_BOT_TOKEN",
                                      retryable=False)
            else:
                self._set_fatal_error("max_unreachable", f"MAX API is unreachable: {exc}", retryable=True)
            return False
        identity = hashlib.sha256(s.token.encode()).hexdigest()
        if not self._acquire_platform_lock("max-bot-token", identity, "MAX bot token"):
            await client.close()
            return False
        self._client = client
        self.bot_id = int(me.get("user_id")) if me.get("user_id") is not None else None
        self.bot_username = str(me.get("username") or "")
        self._state = self._state_factory(str(self.bot_id or "bot"))
        self._inbox = Inbox(self._handle_record)
        try:
            if s.webhook_mode:
                await self._start_webhook()
            else:
                if not await self._prepare_polling():
                    await self._teardown()
                    return False
                self._poller = Poller(client, on_update=self._on_raw_update, load_marker=self._state.marker,
                                      save_marker=self._state.save_marker, on_fatal=self._on_poll_fatal,
                                      types=UPDATE_TYPES)
                self._poll_task = asyncio.create_task(self._poller.run(), name="max-poll")
        except Exception as exc:  # noqa: BLE001
            logger.error("MAX: could not start receiving updates: %s", exc)
            self._set_fatal_error("max_start", f"MAX could not start receiving updates: {exc}", retryable=True)
            await self._teardown()
            return False
        if s.update_commands:
            self._spawn(self._publish_commands())
        self._mark_connected()
        self._wire_plugin_handlers(None)
        logger.info("MAX: connected as @%s (%s), %s", self.bot_username or "?", self.bot_id,
                    "webhook" if s.webhook_mode else "long polling")
        return True

    async def _prepare_polling(self) -> bool:
        """MAX does not deliver to a long poll while a webhook subscription exists."""
        subscriptions = await self._client.list_subscriptions()
        if not subscriptions:
            return True
        urls = [str(sub.get("url") or "") for sub in subscriptions]
        if not self.settings.polling_takeover:
            self._set_fatal_error(
                "max_webhook_active",
                f"This bot has {len(urls)} webhook subscription(s), and MAX does not deliver to long polling "
                "while one exists. Remove it (DELETE /subscriptions) or set MAX_POLLING_TAKEOVER=true to let "
                "the adapter remove it.", retryable=False)
            return False
        for url in urls:
            await self._client.unsubscribe(url)
            logger.warning("MAX: removed webhook subscription %s (MAX_POLLING_TAKEOVER=true)", url)
        return True

    async def _start_webhook(self) -> None:
        s = self.settings
        self._webhook = WebhookServer(secret=s.webhook_secret, on_update=self._on_raw_update,
                                      host=s.webhook_host, port=s.webhook_port, path=s.webhook_path)
        await self._webhook.start()
        await self._client.subscribe(s.webhook_url, secret=s.webhook_secret, update_types=UPDATE_TYPES)

    async def _on_poll_fatal(self, exc: MaxApiError) -> None:
        self._set_fatal_error("max_auth", "MAX refused the bot token while polling (HTTP 401)", retryable=False)
        await self._notify_fatal_error()

    async def disconnect(self) -> None:
        await self._teardown()
        self._mark_disconnected()

    async def _teardown(self) -> None:
        if self._poller:
            self._poller.stop()
        for task in [self._poll_task, *self._side_tasks]:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in [self._poll_task, *self._side_tasks] if t], return_exceptions=True)
        self._poll_task, self._poller = None, None
        self._side_tasks.clear()
        if self._webhook:
            await self._webhook.stop()
            self._webhook = None
        if self._inbox:
            await self._inbox.close()
            self._inbox = None
        if self._client:
            await self._client.close()
            self._client = None
        if self._state:
            self._state.close()
            self._state = None
        self._release_platform_lock()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    async def _publish_commands(self) -> None:
        try:
            await self._client.set_commands(command_menu(self._language))
        except Exception as exc:  # noqa: BLE001 - the menu is a convenience
            logger.info("MAX: could not publish the command menu: %s", exc)

    # ── inbound ─────────────────────────────────────────────────────────────────────────────────

    def _on_raw_update(self, update: dict[str, Any]) -> None:
        record = parse_update(update)
        if record is None or self._inbox is None or self._state is None:
            return
        if not self._state.first_seen(dedup_key(record)):
            return
        if isinstance(record, IncomingMessage):
            key = f"{record.chat_type}:{record.chat_id or record.sender.user_id}"
        elif isinstance(record, CallbackPress):
            key = f"cb:{record.chat_id}"
        else:
            key = f"ev:{record.chat_id or record.user.user_id}"
        self._inbox.submit(key, record)

    async def _handle_record(self, record) -> None:
        if isinstance(record, IncomingMessage):
            await self._handle_message(record)
        elif isinstance(record, CallbackPress):
            await self._handle_press(record)
        elif isinstance(record, BotEvent):
            await self._handle_bot_event(record)

    def _hermes_chat_id(self, chat_type: str, chat_id: int | None, user_id: int | None) -> str:
        return str(user_id) if chat_type == DIALOG else str(chat_id)

    async def _handle_message(self, msg: IncomingMessage) -> None:
        user_id = msg.sender.user_id
        if user_id is None or msg.sender.is_bot or user_id == self.bot_id:
            return
        if msg.chat_type == "channel":
            return
        if msg.edited:
            logger.debug("MAX: ignoring an edit of %s", msg.mid)
            return
        if msg.is_dialog:
            chat_id, chat_type = str(user_id), "dm"
            if msg.chat_id is not None:
                self._state.remember_dialog(str(user_id), str(msg.chat_id))
            text: str | None = msg.text
        else:
            chat_id, chat_type = str(msg.chat_id), "group"
            self._state.remember_group(chat_id)
            text = addressed_text(msg, bot_user_id=self.bot_id, bot_username=self.bot_username,
                                  require_mention=self.settings.require_mention, free_chats=self.settings.free_chats)
            if text is None:
                return
        authorized = self._is_sender_authorized(str(user_id), chat_type, chat_id) is True
        media = await self._media(msg, authorized)
        body = "\n".join(part for part in [
            f"[forwarded from {msg.forwarded_from}]" if msg.forwarded_from else "", text or "", *media.notes] if part)
        source = self.build_source(chat_id=chat_id, chat_type=chat_type, user_id=str(user_id),
                                   user_name=msg.sender.name or None, message_id=msg.mid)
        session_key = self._source_session_key(source)
        self._session_owners[session_key] = str(user_id)
        self._chat_requesters[chat_id] = str(user_id)
        from gateway.platforms.base import resolve_channel_prompt

        event = MessageEvent(
            text=body, message_type=self._message_type(body, media), source=source, message_id=msg.mid,
            media_urls=media.paths, media_types=media.mime_types, media_filenames=media.filenames,
            reply_to_message_id=msg.reply_to_mid or None, reply_to_text=msg.reply_to_text or None,
            reply_to_is_own_message=self.bot_id is not None and msg.reply_to_sender_id == self.bot_id,
            channel_prompt=resolve_channel_prompt(self.config.extra, chat_id, None))
        await self.handle_message(event)

    async def _media(self, msg: IncomingMessage, authorized: bool) -> InboundMedia:
        if not msg.attachments:
            return InboundMedia()
        if not authorized:
            # Strangers get a pairing code from core; the server fetches nothing on their behalf.
            notes = [describe(a) or f"[{a.kind}]" for a in msg.attachments if a.kind != "inline_keyboard"]
            return InboundMedia(notes=notes)
        return await download_all(msg.attachments)

    @staticmethod
    def _message_type(text: str, media: InboundMedia) -> MessageType:
        if text.startswith("/"):
            return MessageType.COMMAND
        if media.has_voice and not text.strip():
            return MessageType.VOICE
        kinds = media.kinds
        if "image" in kinds:
            return MessageType.PHOTO
        if "video" in kinds:
            return MessageType.VIDEO
        if "audio" in kinds:
            return MessageType.AUDIO
        if media.paths:
            return MessageType.DOCUMENT
        return MessageType.TEXT

    async def _handle_bot_event(self, event: BotEvent) -> None:
        if event.kind == "bot_added" and event.chat_id is not None and not event.is_channel:
            self._state.remember_group(str(event.chat_id))
        elif event.kind == "bot_removed" and event.chat_id is not None:
            self._state.forget_group(str(event.chat_id))
        elif event.kind == "bot_started" and event.user.user_id is not None:
            user_id = str(event.user.user_id)
            if event.chat_id is not None:
                self._state.remember_dialog(user_id, str(event.chat_id))
            source = self.build_source(chat_id=user_id, chat_type="dm", user_id=user_id,
                                       user_name=event.user.name or None)
            text = f"/start {event.payload}".strip()
            await self.handle_message(MessageEvent(text=text, message_type=MessageType.COMMAND, source=source))
        elif event.kind == "bot_stopped":
            logger.info("MAX: user %s stopped the bot", event.user.user_id)

    # ── outbound ────────────────────────────────────────────────────────────────────────────────

    def _target(self, chat_id: str) -> Target:
        return resolve_target(chat_id, is_group=self._state.is_group if self._state else (lambda _c: False))

    async def send(self, chat_id: str, content: str, reply_to: str | None = None,
                   metadata: dict[str, Any] | None = None) -> SendResult:
        if not content or not content.strip():
            return SendResult(success=True)
        if self._client is None:
            return _not_connected()
        try:
            target = self._target(chat_id)
            content, table_images = self._tables_as_images(content)
            ids: list[str] = []
            for n, chunk in enumerate(self.truncate_message(content, SOURCE_CHUNK) if content.strip() else []):
                html = formatting.to_max_html(chunk)
                message = await deliver(self._client, target, html=html if len(html) <= MAX_TEXT else None,
                                        plain=formatting.to_plain(chunk)[:MAX_TEXT],
                                        reply_to=reply_to if n == 0 else None)
                ids.append(message_id(message))
            for picture in table_images:
                attachment = await upload(self._client, picture)
                ids.append(message_id(await deliver(self._client, target, html=None, plain="",
                                                    attachments=[attachment])))
        except (MaxApiError, ValueError, OSError) as exc:
            return _failure(exc)
        return SendResult(success=True, message_id=ids[-1] if ids else None,
                          continuation_message_ids=tuple(ids[:-1]))

    def _tables_as_images(self, content: str) -> tuple[str, list[Path]]:
        if not self.settings.table_as_image:
            return content, []
        from .outbound.tables import available, render_table_png

        if not available():
            return content, []
        out_dir = Path(self._media_dir())
        pictures: list[Path] = []
        rewritten = content
        for lines in formatting.find_tables(content):
            picture = render_table_png(lines, out_dir)
            if picture is None:
                return content, []  # all tables as pictures or none: never a table lost
            pictures.append(picture)
            rewritten = rewritten.replace("\n".join(lines), "📊", 1)
        return rewritten, pictures

    @staticmethod
    def _media_dir() -> str:
        try:
            from plugins.plugin_storage import plugin_data_dir

            return str(plugin_data_dir("max-messenger") / "tables")
        except Exception:  # noqa: BLE001
            import tempfile

            return str(Path(tempfile.gettempdir()) / "hermes-max-tables")

    async def edit_message(self, chat_id: str, message_id: str, content: str, *,
                           finalize: bool = False) -> SendResult:
        if self._client is None:
            return _not_connected()
        key = self._target(chat_id).key
        html = formatting.to_max_html(content)
        try:
            if len(html) <= MAX_TEXT:
                try:
                    await self._client.edit_message(message_id, text=html, fmt="html", chat_key=key)
                    return SendResult(success=True, message_id=message_id)
                except MaxApiError as exc:
                    if exc.status != 400:
                        raise
            await self._client.edit_message(message_id, text=formatting.to_plain(content)[:MAX_TEXT], fmt=None,
                                            chat_key=key)
        except MaxApiError as exc:
            return _failure(exc)
        return SendResult(success=True, message_id=message_id)

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.delete_message(message_id, chat_key=self._target(chat_id).key)
            return True
        except MaxApiError:
            return False

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        await self._chat_action(chat_id, "typing_on")

    async def _chat_action(self, chat_id: str, action: str) -> None:
        if self._client is None or self._state is None:
            return
        target = self._target(chat_id)
        dialog = target.chat_id if target.chat_id is not None else self._state.dialog_chat(str(target.user_id))
        if dialog is None:
            return  # no message from this person yet: MAX gives no chat id to show "typing" in
        try:
            await self._client.send_action(int(dialog), action)
        except MaxApiError as exc:
            logger.debug("MAX: %s failed: %s", action, exc.code or exc.status)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        target = self._target(chat_id)
        if target.chat_id is None or self._client is None:
            return {"name": chat_id, "type": "dm"}
        try:
            chat = await self._client.get_chat(target.chat_id)
        except MaxApiError:
            return {"name": chat_id, "type": "group"}
        kind = "channel" if chat.get("type") == "channel" else "group"
        return {"name": str(chat.get("title") or chat_id), "type": kind}

    async def _send_with_keyboard(self, chat_id: str, body_html: str, attachments, metadata) -> SendResult:
        if self._client is None:
            return _not_connected()
        try:
            message = await deliver(self._client, self._target(chat_id), html=body_html[:MAX_TEXT],
                                    plain=html_to_plain(body_html)[:MAX_TEXT], attachments=attachments)
        except (MaxApiError, ValueError) as exc:
            return _failure(exc)
        return SendResult(success=True, message_id=message_id(message))

    def _prompt_owner(self, session_key: str, chat_id: str, metadata: dict[str, Any] | None) -> str:
        meta = metadata or {}
        for key in ("user_id", "recipient_user_id"):
            if meta.get(key):
                return str(meta[key])
        if session_key in self._session_owners:
            return self._session_owners[session_key]
        try:
            if self._target(chat_id).user_id is not None:
                return str(chat_id)
        except ValueError:
            pass
        return self._chat_requesters.get(str(chat_id), "")

    # ── media out ───────────────────────────────────────────────────────────────────────────────

    async def _send_file(self, chat_id: str, path: str, caption: str | None, reply_to: str | None, *,
                         voice: bool = False, file_name: str | None = None) -> SendResult:
        if self._client is None:
            return _not_connected()
        local = Path(path)
        if not local.is_file():
            return SendResult(success=False, error="file not found", error_kind="not_found")
        try:
            target = self._target(chat_id)
            from .outbound.sender import upload_kind

            await self._chat_action(chat_id, _UPLOAD_ACTION[upload_kind(local, voice=voice)])
            attachment = await upload(self._client, local, voice=voice, filename=file_name)
            caption_md = (caption or "").strip()[:SOURCE_CHUNK]
            message = await deliver(self._client, target,
                                    html=formatting.to_max_html(caption_md) if caption_md else None,
                                    plain=formatting.to_plain(caption_md), attachments=[attachment],
                                    reply_to=reply_to)
        except (MaxApiError, ValueError, OSError) as exc:
            return _failure(exc)
        return SendResult(success=True, message_id=message_id(message))

    async def send_image_file(self, chat_id: str, image_path: str, caption: str | None = None,
                              reply_to: str | None = None, metadata=None, **kwargs) -> SendResult:
        return await self._send_file(chat_id, image_path, caption, reply_to)

    async def send_document(self, chat_id: str, file_path: str, caption: str | None = None,
                            file_name: str | None = None, reply_to: str | None = None,
                            metadata=None, **kwargs) -> SendResult:
        return await self._send_file(chat_id, file_path, caption, reply_to, file_name=file_name)

    async def send_voice(self, chat_id: str, audio_path: str, caption: str | None = None,
                         reply_to: str | None = None, metadata=None, **kwargs) -> SendResult:
        return await self._send_file(chat_id, audio_path, caption, reply_to, voice=True)

    async def send_video(self, chat_id: str, video_path: str, caption: str | None = None,
                         reply_to: str | None = None, metadata=None, **kwargs) -> SendResult:
        return await self._send_file(chat_id, video_path, caption, reply_to)

    async def send_image(self, chat_id: str, image_url: str, caption: str | None = None,
                         reply_to: str | None = None, metadata=None) -> SendResult:
        if self._client is None or not image_url.lower().startswith("https://"):
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata)
        try:
            caption_md = (caption or "").strip()[:SOURCE_CHUNK]
            message = await deliver(self._client, self._target(chat_id),
                                    html=formatting.to_max_html(caption_md) if caption_md else None,
                                    plain=formatting.to_plain(caption_md),
                                    attachments=[{"type": "image", "payload": {"url": image_url}}], reply_to=reply_to)
        except (MaxApiError, ValueError):
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata)
        return SendResult(success=True, message_id=message_id(message))


# The command menu MAX shows under "/". Every Hermes command still works when typed; the menu only
# lists the everyday ones, with descriptions in the bot's language. (Hermes' full command registry
# is internal API, so the plugin keeps its own short list.)
MENU = (
    ("new", "Новый разговор", "New conversation"),
    ("model", "Сменить модель", "Switch model"),
    ("status", "Модель, токены, контекст", "Model, tokens, context"),
    ("stop", "Остановить текущую работу", "Stop running work"),
    ("retry", "Повторить последний запрос", "Retry the last message"),
    ("undo", "Отменить последний шаг", "Undo the last turn"),
    ("compress", "Сжать историю разговора", "Compress the conversation"),
    ("title", "Назвать сессию", "Name this session"),
    ("resume", "Вернуться к сессии", "Resume a session"),
    ("voice", "Голосовые ответы вкл/выкл", "Voice replies on/off"),
    ("usage", "Расход токенов", "Token usage"),
    ("sethome", "Сделать этот чат домашним", "Make this the home chat"),
    ("help", "Все команды", "All commands"),
)


def command_menu(language: str = "ru") -> list[tuple[str, str]]:
    english = (language or "ru").lower().startswith("en")
    return [(name, en if english else ru) for name, ru, en in MENU]
