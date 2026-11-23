"""Sending a message the way MAX wants it, with the retries MAX needs.

* A person is addressed by ``user_id``, a group chat by ``chat_id``. Hermes knows a chat only by its
  id string: a positive id is a person, a negative one (or one :class:`~state.State` saw as a group)
  is a group chat.
* An attachment that was just uploaded may answer ``attachment.not.ready``; MAX asks to wait and try
  again, so the send is retried with growing pauses for up to 90 seconds.
* When MAX refuses the HTML (HTTP 400), the same text goes once more as plain text.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..maxapi import MaxApiError, MaxClient

logger = logging.getLogger(__name__)

NOT_READY_DELAYS = (1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 37.0)  # ≈ 90 s in all

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".heic", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".aac", ".flac"}
# MAX limits per upload type (https://dev.max.ru/docs-api/methods/POST/uploads).
SIZE_LIMIT = {"image": 50 * 1024**2, "video": 250 * 1024**2, "audio": 256 * 1024**2, "file": 4 * 1024**3}


@dataclass(frozen=True)
class Target:
    """Where a message goes: exactly one of ``user_id`` / ``chat_id``."""

    user_id: int | None = None
    chat_id: int | None = None

    @property
    def params(self) -> dict[str, int]:
        return {"user_id": self.user_id} if self.user_id is not None else {"chat_id": self.chat_id}

    @property
    def key(self) -> str:
        return f"u{self.user_id}" if self.user_id is not None else f"c{self.chat_id}"


def resolve_target(chat_id: str, *, is_group: Callable[[str], bool] = lambda _c: False) -> Target:
    """Hermes' chat id string → a MAX address. Raises ValueError for a non-numeric id."""
    raw = str(chat_id).strip()
    for prefix in ("max:", "user:", "chat:"):
        if raw.startswith(prefix):
            kind, raw = prefix[:-1], raw[len(prefix):]
            if kind == "user":
                return Target(user_id=int(raw))
            if kind == "chat":
                return Target(chat_id=int(raw))
    number = int(raw)
    if number < 0 or is_group(raw):
        return Target(chat_id=number)
    return Target(user_id=number)


def upload_kind(path: Path, *, voice: bool = False) -> str:
    ext = path.suffix.lower()
    if voice or ext in AUDIO_EXT:
        kind = "audio"
    elif ext in IMAGE_EXT:
        kind = "image"
    elif ext in VIDEO_EXT:
        kind = "video"
    else:
        kind = "file"
    try:
        if path.stat().st_size > SIZE_LIMIT[kind]:
            return "file"
    except OSError:
        pass
    return kind


def _token_from(kind: str, slot: dict[str, Any], uploaded: dict[str, Any]) -> str:
    """Where MAX puts the attachment token depends on the type: audio and video get it from
    ``POST /uploads`` itself, images in ``photos{<id>: {token}}`` of the upload answer, files in the
    upload answer's ``token``."""
    if kind in ("audio", "video") and slot.get("token"):
        return str(slot["token"])
    if uploaded.get("token"):
        return str(uploaded["token"])
    photos = uploaded.get("photos")
    if isinstance(photos, dict):
        for item in photos.values():
            if isinstance(item, dict) and item.get("token"):
                return str(item["token"])
    return str(slot.get("token") or "")


async def upload(client: MaxClient, path: Path, *, voice: bool = False, as_file: bool = False,
                 filename: str | None = None) -> dict[str, Any]:
    """Upload a local file; returns the attachment for ``POST /messages``. ``as_file`` sends a
    picture or a clip as a plain file (no preview, original quality)."""
    kind = "file" if as_file else upload_kind(path, voice=voice)
    slot = await client.get_upload_url(kind)
    uploaded = await client.upload_file(str(slot.get("url") or ""), path, filename=filename)
    token = _token_from(kind, slot, uploaded)
    if not token and kind != "file":
        # Some hosts answer audio/video uploads with nothing usable; a plain file always works.
        logger.info("MAX: upload of %s as %s returned no token; retrying as a file", path.suffix, kind)
        slot = await client.get_upload_url("file")
        uploaded = await client.upload_file(str(slot.get("url") or ""), path, filename=filename)
        kind, token = "file", _token_from("file", slot, uploaded)
    if not token:
        raise MaxApiError(0, "upload.no_token", "the upload answer carried no token", method="POST", path="upload")
    return {"type": kind, "payload": {"token": token}}


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def deliver(client: MaxClient, target: Target, *, html: str | None, plain: str,
                  attachments: list[dict[str, Any]] | None = None, reply_to: str | None = None,
                  notify: bool = True, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> dict[str, Any]:
    """POST /messages with the not-ready and plain-text retries. Returns MAX's message object."""
    text, fmt = (html, "html") if html is not None else (plain, None)
    delays = iter(NOT_READY_DELAYS)
    tried_plain = fmt is None
    while True:
        try:
            return await client.send_message(text=text or None, attachments=attachments, reply_to=reply_to,
                                             fmt=fmt, notify=notify, **target.params)
        except MaxApiError as exc:
            if exc.is_not_ready and attachments:
                delay = next(delays, None)
                if delay is None:
                    raise
                await sleep(delay)
                continue
            if exc.status == 400 and not tried_plain:
                logger.info("MAX refused the formatted text (%s); sending plain text", exc.code or exc.status)
                text, fmt, tried_plain = plain, None, True
                continue
            if exc.status in (400, 404) and reply_to:
                # The message we answer may be gone; answer without the reply link.
                reply_to = None
                continue
            raise


def message_id(message: dict[str, Any]) -> str:
    return str((message.get("body") or {}).get("mid") or "")
