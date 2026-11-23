"""Client-side pacing for the MAX limits.

MAX allows 30 requests per second per bot and 2 messages (sends, edits, deletes, callback answers)
per second per chat; going over answers HTTP 429. Each call reserves a slot and sleeps until it:
reservation happens without an ``await`` in between, so concurrent tasks on one event loop never get
the same slot and no lock is needed.

Global slots are kept as a sorted list of reserved moments, not a single "next free" time: a message
waiting for its chat's half-second gap must not push back a request for another chat, which may take
any free moment in between.
"""

from __future__ import annotations

import asyncio
import bisect
import time
from collections.abc import Awaitable, Callable

GLOBAL_PER_SECOND = 30
CHAT_PER_SECOND = 2
# Forget per-chat slots older than this, so the table does not grow with every chat ever seen.
_CHAT_SLOT_TTL = 60.0


class RateLimiter:
    def __init__(self, *, global_per_second: float = GLOBAL_PER_SECOND,
                 chat_per_second: float = CHAT_PER_SECOND,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._global_gap = 1.0 / global_per_second
        self._chat_gap = 1.0 / chat_per_second
        self._clock = clock
        self._sleep = sleep
        self._blocked_until = 0.0
        self._slots: list[float] = []  # reserved global moments, sorted
        self._chat_next: dict[str, float] = {}

    def _free_global_slot(self, earliest: float) -> float:
        """The first moment ≥ ``earliest`` at least one global gap away from every reserved one."""
        gap, slot = self._global_gap, earliest
        while True:
            i = bisect.bisect_left(self._slots, slot - gap + 1e-9)
            if i < len(self._slots) and self._slots[i] < slot + gap - 1e-9:
                slot = self._slots[i] + gap
                continue
            return slot

    def reserve(self, chat_key: str | None = None) -> float:
        """Reserve a slot and return how long to wait for it (seconds, never negative)."""
        now = self._clock()
        del self._slots[:bisect.bisect_left(self._slots, now - 1.0)]
        earliest = max(now, self._blocked_until)
        if chat_key is not None:
            earliest = max(earliest, self._chat_next.get(chat_key, 0.0))
        slot = self._free_global_slot(earliest)
        bisect.insort(self._slots, slot)
        if chat_key is not None:
            self._chat_next[chat_key] = slot + self._chat_gap
            if len(self._chat_next) > 512:
                self._forget_idle(now)
        return slot - now

    async def acquire(self, chat_key: str | None = None) -> None:
        wait = self.reserve(chat_key)
        if wait > 0:
            await self._sleep(wait)

    def penalize(self, seconds: float) -> None:
        """Push every later request back after a 429 (``Retry-After`` or our own back-off)."""
        until = self._clock() + max(0.0, seconds)
        self._blocked_until = max(self._blocked_until, until)

    def _forget_idle(self, now: float) -> None:
        for key, next_free in list(self._chat_next.items()):
            if next_free < now - _CHAT_SLOT_TTL:
                del self._chat_next[key]
