"""Long polling of ``GET /updates``.

The marker is saved after every batch, so a restart neither re-reads old updates nor loses new ones.
Errors back off from 2 to 60 seconds with jitter and honour ``Retry-After``; a refused token (401)
stops the loop and reports a fatal error instead of retrying forever.

MAX allows one long poll per bot and none while a webhook subscription exists — the adapter checks
subscriptions before starting this loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ..maxapi import MaxApiError, MaxClient

logger = logging.getLogger(__name__)

BACKOFF_START, BACKOFF_CAP = 2.0, 60.0


def backoff_delay(failures: int, *, retry_after: float | None = None,
                  rng: Callable[[], float] = random.random) -> float:
    """Seconds to wait after ``failures`` consecutive failures (1-based)."""
    base = min(BACKOFF_CAP, BACKOFF_START * (2 ** max(0, failures - 1)))
    delay = base * (0.8 + 0.4 * rng())
    return max(delay, retry_after or 0.0)


class Poller:
    def __init__(self, client: MaxClient, *, on_update: Callable[[dict[str, Any]], None],
                 load_marker: Callable[[], int | None], save_marker: Callable[[int | None], None],
                 on_fatal: Callable[[MaxApiError], Awaitable[None]], types: Iterable[str],
                 timeout: int = 30, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._client = client
        self._on_update = on_update
        self._save_marker = save_marker
        self._on_fatal = on_fatal
        self._types = tuple(types)
        self._timeout = timeout
        self._sleep = sleep
        self.marker = load_marker()
        self.running = False

    async def run(self) -> None:
        self.running = True
        failures = 0
        while self.running:
            try:
                updates, marker = await self._client.get_updates(
                    marker=self.marker, timeout=self._timeout, types=self._types)
            except asyncio.CancelledError:
                raise
            except MaxApiError as exc:
                if exc.is_auth:
                    logger.error("MAX: the bot token was refused (HTTP 401); polling stopped")
                    self.running = False
                    await self._on_fatal(exc)
                    return
                failures += 1
                delay = backoff_delay(failures, retry_after=exc.retry_after)
                reason = exc.code or exc.status or "network"
                logger.warning("MAX: polling failed (%s); retrying in %.0f s", reason, delay)
                await self._sleep(delay)
                continue
            failures = 0
            for update in updates:
                try:
                    self._on_update(update)
                except Exception:
                    logger.exception("MAX: could not queue an update")
            if marker is not None and marker != self.marker:
                self.marker = marker
                self._save_marker(marker)

    def stop(self) -> None:
        self.running = False
