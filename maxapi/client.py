"""Asynchronous MAX Bot API client.

Wire facts (https://dev.max.ru/docs-api, checked 2026-11-23):

* the token goes only in the ``Authorization`` header, without ``Bearer``; query tokens are gone;
* ``platform-api2.max.ru`` is signed by the Russian Trusted Root CA (see :mod:`.tls`);
* some methods answer HTTP 200 with ``{"success": false, "code", "message"}``, so the body is
  checked as well as the status;
* 30 requests/s per bot, 2 messages/s per chat (:mod:`.limits`);
* uploads go to a separate host as multipart field ``data`` and never carry the bot token.
"""

from __future__ import annotations

import email.utils
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import MaxApiError
from .limits import RateLimiter
from .tls import ssl_context

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://platform-api2.max.ru"
USER_AGENT = "hermes-max-messenger/0.1"

# Upload URLs returned by POST /uploads live on MAX's own hosts; anything else is refused so a
# tampered answer cannot make us post a local file elsewhere.
UPLOAD_HOST_SUFFIXES = (".max.ru", ".oneme.ru", ".okcdn.ru", ".cdn-max.ru")

# Actions MAX accepts for POST /chats/{chatId}/actions.
ACTIONS = frozenset({"typing_on", "sending_photo", "sending_video", "sending_audio", "sending_file"})

UPLOAD_TYPES = frozenset({"image", "video", "audio", "file"})

# Long polling waits server-side for up to ``timeout`` seconds (MAX caps it at 90).
MAX_POLL_TIMEOUT = 90


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or an HTTP date), capped at 5 minutes."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = parsed.timestamp() - (time.time() if now is None else now)
    if seconds != seconds:  # NaN
        return None
    return min(max(seconds, 0.0), 300.0)


def is_upload_host(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == s[1:] or host.endswith(s) for s in UPLOAD_HOST_SUFFIXES)


class MaxClient:
    """One bot's view of the MAX Bot API. Create, use, ``await close()``."""

    def __init__(self, token: str, *, api_base: str = DEFAULT_API_BASE,
                 limiter: RateLimiter | None = None, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 upload_transport: httpx.AsyncBaseTransport | None = None):
        if not token:
            raise ValueError("MAX bot token is empty")
        self.api_base = api_base.rstrip("/")
        self.limiter = limiter or RateLimiter()
        verify = ssl_context()
        self._http = httpx.AsyncClient(
            base_url=self.api_base, timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Authorization": token, "User-Agent": USER_AGENT},
            follow_redirects=False, verify=verify, transport=transport)
        # Uploads: no token, no redirects, MAX hosts only.
        self._upload_http = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0), headers={"User-Agent": USER_AGENT},
            follow_redirects=False, verify=verify, transport=upload_transport)

    async def close(self) -> None:
        await self._http.aclose()
        await self._upload_http.aclose()

    # ── transport ───────────────────────────────────────────────────────────────────────────────

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None, chat_key: str | None = None,
                      timeout: float | None = None) -> dict[str, Any]:
        """Call the API and return the JSON object, or raise :class:`MaxApiError`.

        ``chat_key`` paces the call per chat (messages, edits, deletes, answers).
        """
        await self.limiter.acquire(chat_key)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        kwargs: dict[str, Any] = {"params": clean}
        if json is not None:
            kwargs["json"] = json
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=10.0)
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise MaxApiError(0, "timeout", type(exc).__name__, method=method, path=path) from exc
        except httpx.TransportError as exc:
            raise MaxApiError(0, "network", type(exc).__name__, method=method, path=path) from exc
        return self._decode(response, method, path)

    def _decode(self, response: httpx.Response, method: str, path: str) -> dict[str, Any]:
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {"result": body}
        status = response.status_code
        if status >= 400:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            if status == 429:
                self.limiter.penalize(retry_after if retry_after is not None else 1.0)
            raise MaxApiError(status, str(body.get("code") or ""), str(body.get("message") or ""),
                              retry_after=retry_after, method=method, path=path)
        if body.get("success") is False:
            raise MaxApiError(status, str(body.get("code") or ""), str(body.get("message") or ""),
                              method=method, path=path)
        return body

    # ── bot ─────────────────────────────────────────────────────────────────────────────────────

    async def get_me(self) -> dict[str, Any]:
        return await self.request("GET", "/me")

    async def set_commands(self, commands: Iterable[tuple[str, str]]) -> dict[str, Any]:
        """Replace the bot's command menu (up to 32 entries; description 1-128 chars)."""
        items = [{"name": name, "description": (desc or name)[:128]} for name, desc in commands][:32]
        return await self.request("PATCH", "/me/commands", json={"commands": items})

    # ── updates ─────────────────────────────────────────────────────────────────────────────────

    async def get_updates(self, *, marker: int | None = None, timeout: int = 30, limit: int = 100,
                          types: Iterable[str] | None = None) -> tuple[list[dict[str, Any]], int | None]:
        """Long poll. Returns ``(updates, next_marker)``; the marker is None when MAX sent none."""
        timeout = max(0, min(int(timeout), MAX_POLL_TIMEOUT))
        params: dict[str, Any] = {"timeout": timeout, "limit": max(1, min(int(limit), 1000)), "marker": marker}
        if types:
            params["types"] = ",".join(types)
        body = await self.request("GET", "/updates", params=params, timeout=timeout + 15)
        updates = body.get("updates") or []
        next_marker = body.get("marker")
        return [u for u in updates if isinstance(u, dict)], (int(next_marker) if next_marker is not None else None)

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        body = await self.request("GET", "/subscriptions")
        return [s for s in body.get("subscriptions") or [] if isinstance(s, dict)]

    async def subscribe(self, url: str, *, secret: str | None = None,
                        update_types: Iterable[str] | None = None) -> None:
        payload: dict[str, Any] = {"url": url}
        if secret:
            payload["secret"] = secret
        if update_types:
            payload["update_types"] = list(update_types)
        await self.request("POST", "/subscriptions", json=payload)

    async def unsubscribe(self, url: str) -> None:
        await self.request("DELETE", "/subscriptions", params={"url": url})

    # ── messages ────────────────────────────────────────────────────────────────────────────────

    async def send_message(self, *, user_id: int | None = None, chat_id: int | None = None,
                           text: str | None = None, attachments: list[dict[str, Any]] | None = None,
                           reply_to: str | None = None, fmt: str | None = "markdown",
                           notify: bool = True, disable_link_preview: bool | None = None) -> dict[str, Any]:
        """POST /messages to a user (DM) or a chat. Returns the created message object."""
        if (user_id is None) == (chat_id is None):
            raise ValueError("pass exactly one of user_id / chat_id")
        body: dict[str, Any] = {"text": text, "notify": notify}
        if attachments:
            body["attachments"] = attachments
        if reply_to:
            body["link"] = {"type": "reply", "mid": str(reply_to)}
        if fmt:
            body["format"] = fmt
        key = f"u{user_id}" if user_id is not None else f"c{chat_id}"
        preview = None if disable_link_preview is None else str(bool(disable_link_preview)).lower()
        result = await self.request(
            "POST", "/messages", chat_key=key,
            params={"user_id": user_id, "chat_id": chat_id, "disable_link_preview": preview}, json=body)
        message = result.get("message")
        if not isinstance(message, dict) or not (message.get("body") or {}).get("mid"):
            # A 2xx answer without a message id means MAX accepted nothing we can point at.
            raise MaxApiError(200, "no.message", "MAX answered without a message id", method="POST", path="/messages")
        return message

    async def edit_message(self, message_id: str, *, text: str | None = None,
                           attachments: list[dict[str, Any]] | None = None, fmt: str | None = "markdown",
                           chat_key: str | None = None) -> None:
        """PUT /messages. ``attachments=None`` keeps them, ``[]`` removes them all."""
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        await self.request("PUT", "/messages", params={"message_id": message_id}, json=body, chat_key=chat_key)

    async def delete_message(self, message_id: str, *, chat_key: str | None = None) -> None:
        await self.request("DELETE", "/messages", params={"message_id": message_id}, chat_key=chat_key)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/messages/{message_id}")

    async def send_action(self, chat_id: int, action: str = "typing_on") -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown MAX chat action {action!r}")
        await self.request("POST", f"/chats/{int(chat_id)}/actions", json={"action": action}, timeout=5.0)

    async def answer_callback(self, callback_id: str, *, message: dict[str, Any] | None = None,
                              notification: str | None = None, chat_key: str | None = None) -> None:
        """POST /answers: acknowledge a button press, optionally replacing the message it sits on."""
        body: dict[str, Any] = {}
        if message is not None:
            body["message"] = message
        if notification:
            body["notification"] = notification
        await self.request("POST", "/answers", params={"callback_id": callback_id}, json=body, chat_key=chat_key)

    # ── chats ───────────────────────────────────────────────────────────────────────────────────

    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        return await self.request("GET", f"/chats/{int(chat_id)}")

    # ── uploads ─────────────────────────────────────────────────────────────────────────────────

    async def get_upload_url(self, kind: str) -> dict[str, Any]:
        if kind not in UPLOAD_TYPES:
            raise ValueError(f"unknown upload type {kind!r}")
        return await self.request("POST", "/uploads", params={"type": kind})

    async def upload_file(self, upload_url: str, path: Path, filename: str | None = None) -> dict[str, Any]:
        """Send a local file to an upload URL as multipart field ``data``. Returns the JSON answer
        (``{}`` when the host answered with something else, as it does for audio and video)."""
        if not is_upload_host(upload_url):
            raise MaxApiError(0, "upload.host", "upload URL is not on a MAX host", method="POST", path="upload")
        await self.limiter.acquire(None)
        try:
            with path.open("rb") as handle:
                response = await self._upload_http.post(
                    upload_url, files={"data": (filename or path.name, handle)})
        except httpx.TransportError as exc:
            raise MaxApiError(0, "network", type(exc).__name__, method="POST", path="upload") from exc
        if response.status_code >= 400:
            raise MaxApiError(response.status_code, "upload.failed", "", method="POST", path="upload")
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
