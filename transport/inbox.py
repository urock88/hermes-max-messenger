"""An ordered inbox per chat.

Turning an update into a Hermes event can take a while (downloading a voice note or a photo), and
the poll loop must not wait for it — otherwise one big file stalls every other chat. Each chat gets
its own queue so its messages keep their order; chats run side by side, at most ``concurrency`` at
a time. A worker exits when its queue runs dry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class Inbox:
    def __init__(self, handler: Callable[[Any], Awaitable[None]], *, concurrency: int = 8,
                 max_pending: int = 1000):
        self._handler = handler
        self._slots = asyncio.Semaphore(concurrency)
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._max_pending = max_pending
        self._closed = False

    @property
    def pending(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    def submit(self, chat_key: str, item: Any) -> bool:
        """Queue ``item`` behind the chat's earlier items. False when closed or overloaded."""
        if self._closed:
            return False
        if self.pending >= self._max_pending:
            logger.warning("MAX: inbox full (%d pending); dropping an update for chat %s", self.pending, chat_key)
            return False
        queue = self._queues.setdefault(chat_key, asyncio.Queue())
        queue.put_nowait(item)
        worker = self._workers.get(chat_key)
        if worker is None or worker.done():
            self._workers[chat_key] = asyncio.create_task(self._drain(chat_key, queue), name=f"max-inbox-{chat_key}")
        return True

    async def _drain(self, chat_key: str, queue: asyncio.Queue) -> None:
        try:
            while not queue.empty():
                item = queue.get_nowait()
                async with self._slots:
                    try:
                        await self._handler(item)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("MAX: handling an update for chat %s failed", chat_key)
        finally:
            if queue.empty() and self._queues.get(chat_key) is queue:
                self._queues.pop(chat_key, None)
                self._workers.pop(chat_key, None)

    async def join(self) -> None:
        """Wait until everything queued so far is handled (tests, graceful shutdown)."""
        while self._workers:
            await asyncio.gather(*list(self._workers.values()), return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        workers = list(self._workers.values())
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        self._queues.clear()
