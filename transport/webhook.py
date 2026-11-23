"""Webhook receiver.

MAX posts updates to an https URL on port 443 with a certificate from a trusted CA and expects
HTTP 200 within 30 seconds; it retries a failed delivery up to 10 times and drops the subscription
after 8 hours without success. So the listener binds locally behind your reverse proxy, checks the
``X-Max-Bot-Api-Secret`` header (constant-time) before it reads the body, caps the body at 1 MiB,
answers at once and hands the update to the inbox. Without a secret it refuses to start.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Max-Bot-Api-Secret"
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,256}$")
MAX_BODY = 1024 * 1024


def secret_is_valid(secret: str) -> bool:
    return bool(SECRET_PATTERN.match(secret or ""))


def secret_matches(expected: str, received: str | None) -> bool:
    if not expected or received is None:
        return False
    return hmac.compare_digest(expected.encode(), received.encode())


class WebhookServer:
    def __init__(self, *, secret: str, on_update: Callable[[dict[str, Any]], None],
                 host: str = "127.0.0.1", port: int = 8646, path: str = "/max/webhook"):
        if not secret_is_valid(secret):
            raise ValueError("MAX_WEBHOOK_SECRET must be 5-256 characters of A-Z a-z 0-9 _ -")
        self._secret = secret
        self._on_update = on_update
        self.host, self.port, self.path = host, int(port), path if path.startswith("/") else f"/{path}"
        self._runner = None
        self.received = 0

    def build_app(self):
        from aiohttp import web

        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post(self.path, self._handle)
        app.router.add_get("/health", self._health)
        return app

    async def _health(self, request):
        from aiohttp import web

        return web.json_response({"ok": True, "received": self.received})

    async def _handle(self, request):
        from aiohttp import web

        if not secret_matches(self._secret, request.headers.get(SECRET_HEADER)):
            return web.Response(status=401)
        if (request.content_length or 0) > MAX_BODY:
            return web.Response(status=413)
        try:
            raw = await request.read()
            update = json.loads(raw)
        except (ValueError, web.HTTPRequestEntityTooLarge):
            return web.Response(status=400)
        if isinstance(update, dict):
            self.received += 1
            try:
                self._on_update(update)
            except Exception:
                logger.exception("MAX: could not queue a webhook update")
        return web.Response(status=200)

    async def start(self) -> None:
        from aiohttp import web

        runner = web.AppRunner(self.build_app(), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        logger.info("MAX: webhook listening on %s:%d%s", self.host, self.port, self.path)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
