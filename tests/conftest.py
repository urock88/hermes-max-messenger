"""Test setup: an isolated HERMES_HOME, the plugin loaded as a package, a fake MAX API."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Never touch a real Hermes installation: core reads HERMES_HOME at import time in places.
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="hermes-max-test-home-")
os.environ.setdefault("HERMES_NONINTERACTIVE", "1")


def _load_plugin(name: str = "max_messenger"):
    """Import the repository root as a package, the way Hermes' plugin loader does."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()


def _register_platform() -> None:
    """In Hermes the loader registers the platform before any adapter exists; do the same here so
    ``Platform("max")`` resolves."""
    from gateway.platform_registry import PlatformEntry, platform_registry

    if not platform_registry.is_registered("max"):
        platform_registry.register(PlatformEntry(**plugin._platform_kwargs()))


_register_platform()

import httpx  # noqa: E402
from max_messenger.maxapi import MaxClient  # noqa: E402
from max_messenger.maxapi.limits import RateLimiter  # noqa: E402
from max_messenger.state import State  # noqa: E402

BOT_ID = 900
UPLOAD_URL = "https://fu.oneme.ru/upload?sig=abc"


class FakeMax:
    """A scripted MAX Bot API. ``queue(path, response)`` makes the next call to ``path`` answer
    ``response`` (a dict body, or ``(status, body)`` / ``(status, body, headers)``)."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.subscriptions: list[dict[str, Any]] = []
        self.scripted: dict[tuple[str, str], list[Any]] = {}
        self.next_mid = 1000
        self.marker = 0
        self.upload_answer: dict[str, Any] = {"token": "file-token"}

    def queue(self, method: str, path: str, response: Any) -> None:
        self.scripted.setdefault((method, path), []).append(response)

    def made(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"] == path]

    def _reply(self, response: Any) -> httpx.Response:
        if isinstance(response, tuple):
            status, body, *rest = response
            return httpx.Response(status, json=body, headers=rest[0] if rest else None)
        return httpx.Response(200, json=response)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        call = {"method": request.method, "path": request.url.path, "params": dict(request.url.params),
                "json": body, "headers": dict(request.headers)}
        self.calls.append(call)
        key = (request.method, request.url.path)
        if self.scripted.get(key):
            return self._reply(self.scripted[key].pop(0))
        path = request.url.path
        if path == "/me" and request.method == "GET":
            return httpx.Response(200, json={"user_id": BOT_ID, "first_name": "Hermes", "username": "hermes_bot",
                                             "is_bot": True})
        if path == "/updates":
            if not self.updates:
                await asyncio.sleep(0.01)
                return httpx.Response(200, json={"updates": [], "marker": self.marker})
            batch, self.updates = self.updates, []
            self.marker += len(batch)
            return httpx.Response(200, json={"updates": batch, "marker": self.marker})
        if path == "/subscriptions" and request.method == "GET":
            return httpx.Response(200, json={"subscriptions": self.subscriptions})
        if path == "/subscriptions":
            return httpx.Response(200, json={"success": True})
        if path == "/messages" and request.method == "POST":
            self.next_mid += 1
            return httpx.Response(200, json={"message": {"body": {"mid": f"mid.{self.next_mid}", "text": body.get("text")},
                                                         "recipient": {}}})
        if path == "/uploads":
            kind = call["params"].get("type")
            answer = {"url": UPLOAD_URL}
            if kind in ("audio", "video"):
                answer["token"] = f"{kind}-token"
            return httpx.Response(200, json=answer)
        return httpx.Response(200, json={"success": True})

    async def handle_upload(self, request: httpx.Request) -> httpx.Response:
        self.uploads.append({"url": str(request.url), "headers": dict(request.headers), "size": len(request.content)})
        return httpx.Response(200, json=self.upload_answer)


@pytest.fixture
def fake_max() -> FakeMax:
    return FakeMax()


@pytest.fixture
def no_sleep():
    async def sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    return sleep


@pytest.fixture
def make_client(fake_max: FakeMax, no_sleep) -> Callable[..., MaxClient]:
    created: list[MaxClient] = []

    def factory(token: str = "secret-token", **_kw) -> MaxClient:
        client = MaxClient(token, transport=httpx.MockTransport(fake_max.handle),
                           upload_transport=httpx.MockTransport(fake_max.handle_upload),
                           limiter=RateLimiter(sleep=no_sleep, global_per_second=10_000, chat_per_second=10_000))
        created.append(client)
        return client

    return factory


@pytest.fixture
def state_factory(tmp_path):
    db = tmp_path / "state.db"

    def factory(bot: str = str(BOT_ID)) -> State:
        import sqlite3

        return State(lambda: sqlite3.connect(db, check_same_thread=False), bot=bot)

    return factory


def message_update(*, text: str = "hi", user_id: int = 42, chat_id: int | None = 4242, chat_type: str = "dialog",
                   mid: str = "mid.in.1", attachments: list | None = None, markup: list | None = None,
                   link: dict | None = None, name: str = "Ivan") -> dict[str, Any]:
    body: dict[str, Any] = {"mid": mid, "seq": 1, "text": text}
    if attachments:
        body["attachments"] = attachments
    if markup:
        body["markup"] = markup
    message: dict[str, Any] = {"sender": {"user_id": user_id, "first_name": name, "is_bot": False},
                               "recipient": {"chat_id": chat_id, "chat_type": chat_type}, "timestamp": 1700000000000,
                               "body": body}
    if link:
        message["link"] = link
    return {"update_type": "message_created", "timestamp": 1700000000000, "message": message}
