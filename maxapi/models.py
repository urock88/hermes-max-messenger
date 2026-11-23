"""MAX updates turned into small typed records.

Real payloads differ from the documentation in places (seen on production bots in 2026): timestamps
are milliseconds, ``sender.username`` is usually absent, a second tap on a deep link arrives as the
text ``/start <payload>`` instead of ``bot_started``, and voice notes are plain ``audio`` attachments
with only ``url`` and ``token``. The parser tolerates missing fields and never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DIALOG = "dialog"
CHAT = "chat"
CHANNEL = "channel"

# Update types the adapter asks for. bot_admin_permissions_changed is webhook-only.
UPDATE_TYPES = (
    "message_created", "message_edited", "message_callback",
    "bot_started", "bot_stopped", "bot_added", "bot_removed",
)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def display_name(user: dict[str, Any] | None) -> str:
    """A person's name as MAX shows it: first + last name, else the deprecated ``name``, else @username."""
    user = user or {}
    full = " ".join(p for p in (_str(user.get("first_name")).strip(), _str(user.get("last_name")).strip()) if p)
    return full or _str(user.get("name")).strip() or (f"@{user['username']}" if user.get("username") else "")


@dataclass(frozen=True)
class Attachment:
    """One attachment of an incoming message. ``kind`` is MAX's ``type``."""

    kind: str
    url: str = ""
    token: str = ""
    filename: str = ""
    size: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_media(self) -> bool:
        return self.kind in {"image", "video", "audio", "file", "sticker"} and bool(self.url)


@dataclass(frozen=True)
class Sender:
    user_id: int | None
    name: str = ""
    username: str = ""
    is_bot: bool = False


@dataclass(frozen=True)
class Mention:
    user_id: int | None
    start: int
    length: int
    text: str = ""


@dataclass(frozen=True)
class IncomingMessage:
    """``message_created`` (or ``message_edited`` when ``edited`` is True)."""

    mid: str
    text: str
    chat_id: int | None
    chat_type: str
    sender: Sender
    attachments: tuple[Attachment, ...] = ()
    mentions: tuple[Mention, ...] = ()
    reply_to_mid: str = ""
    reply_to_text: str = ""
    reply_to_sender_id: int | None = None
    forwarded_from: str = ""
    timestamp_ms: int = 0
    edited: bool = False
    user_locale: str = ""

    @property
    def is_dialog(self) -> bool:
        return self.chat_type == DIALOG


@dataclass(frozen=True)
class CallbackPress:
    """``message_callback``: somebody pressed an inline ``callback`` button."""

    callback_id: str
    payload: str
    user: Sender
    chat_id: int | None
    chat_type: str
    message_mid: str = ""
    message_text: str = ""


@dataclass(frozen=True)
class BotEvent:
    """``bot_started`` / ``bot_stopped`` / ``bot_added`` / ``bot_removed``."""

    kind: str
    chat_id: int | None
    user: Sender
    payload: str = ""
    timestamp_ms: int = 0
    is_channel: bool = False
    user_locale: str = ""


Update =IncomingMessage | CallbackPress | BotEvent


def _sender(raw: Any) -> Sender:
    raw = raw if isinstance(raw, dict) else {}
    return Sender(user_id=_int(raw.get("user_id")), name=display_name(raw),
                  username=_str(raw.get("username")), is_bot=bool(raw.get("is_bot")))


def _attachment(raw: Any) -> Attachment | None:
    if not isinstance(raw, dict):
        return None
    kind = _str(raw.get("type"))
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    filename = _str(raw.get("filename") or payload.get("filename") or raw.get("name"))
    size = _int(raw.get("size") or payload.get("size"))
    return Attachment(kind=kind, url=_str(payload.get("url")), token=_str(payload.get("token")),
                      filename=filename, size=size, payload=payload)


def _mentions(markup: Any, text: str) -> tuple[Mention, ...]:
    found: list[Mention] = []
    for item in markup if isinstance(markup, list) else ():
        if not isinstance(item, dict) or item.get("type") != "user_mention":
            continue
        start, length = _int(item.get("from")) or 0, _int(item.get("length")) or 0
        found.append(Mention(user_id=_int(item.get("user_id")), start=start, length=length,
                             text=text[start:start + length] if text else _str(item.get("user_link"))))
    return tuple(found)


def _incoming(update: dict[str, Any], *, edited: bool) -> IncomingMessage | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    mid = _str(body.get("mid"))
    if not mid:
        return None
    recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
    chat_type = _str(recipient.get("chat_type")) or DIALOG
    text = _str(body.get("text"))
    attachments = tuple(a for a in (_attachment(x) for x in body.get("attachments") or []) if a)
    link = message.get("link") if isinstance(message.get("link"), dict) else {}
    linked = link.get("message") if isinstance(link.get("message"), dict) else {}
    linked_sender = _sender(link.get("sender"))
    reply_mid = reply_text = forwarded = ""
    reply_sender: int | None = None
    if link.get("type") == "reply":
        reply_mid, reply_text, reply_sender = _str(linked.get("mid")), _str(linked.get("text")), linked_sender.user_id
    elif link.get("type") == "forward":
        forwarded = linked_sender.name or "?"
        if not text:
            text = _str(linked.get("text"))
            attachments = attachments or tuple(
                a for a in (_attachment(x) for x in linked.get("attachments") or []) if a)
    return IncomingMessage(
        mid=mid, text=text, chat_id=_int(recipient.get("chat_id")), chat_type=chat_type,
        sender=_sender(message.get("sender")), attachments=attachments,
        mentions=_mentions(body.get("markup"), text), reply_to_mid=reply_mid, reply_to_text=reply_text,
        reply_to_sender_id=reply_sender, forwarded_from=forwarded,
        timestamp_ms=_int(message.get("timestamp")) or _int(update.get("timestamp")) or 0,
        edited=edited, user_locale=_str(update.get("user_locale")))


def _callback(update: dict[str, Any]) -> CallbackPress | None:
    callback = update.get("callback") if isinstance(update.get("callback"), dict) else {}
    callback_id = _str(callback.get("callback_id"))
    if not callback_id:
        return None
    message = update.get("message") if isinstance(update.get("message"), dict) else {}
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
    return CallbackPress(
        callback_id=callback_id, payload=_str(callback.get("payload")), user=_sender(callback.get("user")),
        chat_id=_int(recipient.get("chat_id")), chat_type=_str(recipient.get("chat_type")) or DIALOG,
        message_mid=_str(body.get("mid")), message_text=_str(body.get("text")))


def parse_update(update: Any) -> Update | None:
    """One raw update → a record, or None for anything the adapter does not act on."""
    if not isinstance(update, dict):
        return None
    kind = _str(update.get("update_type"))
    if kind == "message_created":
        return _incoming(update, edited=False)
    if kind == "message_edited":
        return _incoming(update, edited=True)
    if kind == "message_callback":
        return _callback(update)
    if kind in {"bot_started", "bot_stopped", "bot_added", "bot_removed"}:
        return BotEvent(kind=kind, chat_id=_int(update.get("chat_id")), user=_sender(update.get("user")),
                        payload=_str(update.get("payload")), timestamp_ms=_int(update.get("timestamp")) or 0,
                        is_channel=bool(update.get("is_channel")), user_locale=_str(update.get("user_locale")))
    return None


def dedup_key(record: Update) -> str:
    """A stable identity for redeliveries (webhook retries, a re-read poll batch)."""
    if isinstance(record, IncomingMessage):
        return f"m:{record.mid}:e{record.timestamp_ms}" if record.edited else f"m:{record.mid}"
    if isinstance(record, CallbackPress):
        return f"cb:{record.callback_id}"
    return f"{record.kind}:{record.user.user_id}:{record.chat_id}:{record.timestamp_ms}"
