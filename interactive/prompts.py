"""Pending prompts, bound to who may answer them.

A MAX button press carries only the presser's id. Without a binding, anyone in a group could press
"Always allow" on somebody else's command approval. So every prompt remembers its owner (the person
whose turn raised it), its chat and the message its buttons sit on, and expires after 15 minutes. A
press by anyone else is refused *without* consuming the prompt, so the owner can still answer.

Button payloads are short opaque ids (``hm:<prompt id>:<choice>``) — never model ids or session keys,
which can be long, contain ``:`` and leak internals.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PREFIX = "hm"
TTL_SECONDS = 900.0
MAX_PENDING = 256


@dataclass
class Prompt:
    id: str
    kind: str
    session_key: str
    owner_user_id: str
    chat_id: str
    expires_at: float
    message_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def payload(prompt_id: str, choice: str) -> str:
    return f"{PREFIX}:{prompt_id}:{choice}"


def parse_payload(raw: str) -> tuple[str, str] | None:
    """``(prompt_id, choice)`` of one of our buttons, or None for a foreign payload."""
    parts = (raw or "").split(":", 2)
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1]:
        return None
    return parts[1], parts[2]


class Prompts:
    def __init__(self, *, ttl: float = TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._items: dict[str, Prompt] = {}

    def add(self, kind: str, *, session_key: str, owner_user_id: str, chat_id: str,
            data: dict[str, Any] | None = None, supersede: bool = False) -> Prompt:
        self._prune()
        if supersede:
            for key in [k for k, p in self._items.items() if p.kind == kind and p.chat_id == str(chat_id)]:
                del self._items[key]
        prompt = Prompt(id=secrets.token_urlsafe(6), kind=kind, session_key=session_key,
                        owner_user_id=str(owner_user_id or ""), chat_id=str(chat_id),
                        expires_at=self._clock() + self._ttl, data=dict(data or {}))
        self._items[prompt.id] = prompt
        return prompt

    def bind_message(self, prompt_id: str, message_id: str) -> None:
        if prompt_id in self._items:
            self._items[prompt_id].message_id = str(message_id or "")

    def check(self, prompt_id: str, *, user_id: str, chat_id: str, message_id: str = "") -> tuple[Prompt | None, str]:
        """``(prompt, "")`` when ``user_id`` may answer it, else ``(None, reason)``; never consumes."""
        prompt = self._items.get(prompt_id)
        if prompt is None:
            return None, "unknown"
        if self._clock() >= prompt.expires_at:
            del self._items[prompt_id]
            return None, "expired"
        if not prompt.owner_user_id:
            return None, "no owner"
        if str(user_id) != prompt.owner_user_id:
            return None, "not the owner"
        if prompt.chat_id and chat_id and str(chat_id) != prompt.chat_id:
            return None, "other chat"
        if prompt.message_id and message_id and str(message_id) != prompt.message_id:
            return None, "stale button"
        return prompt, ""

    def pop(self, prompt_id: str) -> Prompt | None:
        return self._items.pop(prompt_id, None)

    def get(self, prompt_id: str) -> Prompt | None:
        return self._items.get(prompt_id)

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, p in self._items.items() if now >= p.expires_at]:
            del self._items[key]
        while len(self._items) >= MAX_PENDING:
            del self._items[next(iter(self._items))]


def keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    """``[[(label, payload), ...], ...]`` → an ``inline_keyboard`` attachment (MAX caps: 7 per row,
    30 rows, labels 1-128 characters)."""
    buttons = [[{"type": "callback", "text": (label or "·")[:128], "payload": data[:1024]}
                for label, data in row[:7]] for row in rows if row][:30]
    return {"type": "inline_keyboard", "payload": {"buttons": buttons}}


def rows_of(items: list[tuple[str, str]], width: int) -> list[list[tuple[str, str]]]:
    return [items[i:i + width] for i in range(0, len(items), width)]
