"""Sending from a process that runs no gateway (``hermes cron run``, ``hermes send --to max:<id>``).

Opens a short-lived client, sends the text (split like the live adapter does) and any files, closes.
It shares the sender, the uploads and the chat-kind table with the live adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .maxapi import MaxApiError, MaxClient
from .outbound import formatting
from .outbound.sender import deliver, message_id, resolve_target, upload
from .settings import read_settings

CHUNK = 3500


def _split(text: str) -> list[str]:
    from gateway.platforms.base import BasePlatformAdapter

    return BasePlatformAdapter.truncate_message(text, CHUNK) if text.strip() else []


async def standalone_send(pconfig, chat_id: str, message: str, *, thread_id: str | None = None,
                          media_files: list | None = None, force_document: bool = False) -> dict[str, Any]:
    from gateway.platforms._shared import send_error

    settings = read_settings(pconfig)
    if not settings.token:
        return send_error("MAX standalone send: MAX_BOT_TOKEN is not set")
    client = MaxClient(settings.token, api_base=settings.api_base)
    state = None
    try:
        me = await client.get_me()
        try:
            from .state import State

            state = State(bot=str(me.get("user_id") or "bot"))
            is_group = state.is_group
        except Exception:  # noqa: BLE001 - no plugin storage: fall back to the sign of the id
            is_group = lambda _c: False  # noqa: E731
        target = resolve_target(chat_id, is_group=is_group)
        last = ""
        for chunk in _split(message or ""):
            html = formatting.to_max_html(chunk)
            sent = await deliver(client, target, html=html if len(html) <= 4000 else None,
                                 plain=formatting.to_plain(chunk)[:4000])
            last = message_id(sent)
        for media in media_files or []:
            path = media.get("path") if isinstance(media, dict) else media
            if not path or not Path(path).is_file():
                continue
            voice = bool(isinstance(media, dict) and media.get("is_voice"))
            attachment = await upload(client, Path(path), voice=voice and not force_document, as_file=force_document)
            last = message_id(await deliver(client, target, html=None, plain="", attachments=[attachment]))
        return {"success": True, "platform": "max", "chat_id": chat_id, "message_id": last}
    except (MaxApiError, ValueError, OSError) as exc:
        return send_error(f"MAX standalone send failed: {exc}")
    finally:
        await client.close()
        if state is not None:
            state.close()
