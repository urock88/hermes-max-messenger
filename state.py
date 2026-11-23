"""What must survive a gateway restart, in one SQLite file.

* the long-polling marker — without it a restart either re-reads old updates or loses new ones;
* user id → dialog chat id — MAX sends DMs by ``user_id`` but the typing indicator needs the
  dialog's ``chat_id``, which arrives only with an inbound message;
* group chats seen — a group chat id must be sent with ``chat_id``, a person with ``user_id``;
* recently seen updates — webhook retries and overlapping poll batches must not run a turn twice.

Hermes gives each plugin ``<HERMES_HOME>/plugin-data/<name>/`` (``plugins.plugin_storage``); the
install directory is wiped by ``hermes plugins update``, so nothing is kept there.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable

PLUGIN_NAME = "max-messenger"
SEEN_TTL_SECONDS = 7 * 24 * 3600

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS dialogs (bot TEXT NOT NULL, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,"
    " updated_at REAL NOT NULL, PRIMARY KEY (bot, user_id))",
    "CREATE TABLE IF NOT EXISTS group_chats (bot TEXT NOT NULL, chat_id TEXT NOT NULL, title TEXT,"
    " updated_at REAL NOT NULL, PRIMARY KEY (bot, chat_id))",
    "CREATE TABLE IF NOT EXISTS seen (bot TEXT NOT NULL, key TEXT NOT NULL, at REAL NOT NULL,"
    " PRIMARY KEY (bot, key))",
    "CREATE INDEX IF NOT EXISTS seen_at ON seen (at)",
)


def default_connection() -> sqlite3.Connection:
    from plugins.plugin_storage import plugin_db

    return plugin_db(PLUGIN_NAME, "state.db")


class State:
    """Per-bot state. ``bot`` (the bot's user id) keeps two bots of one profile apart."""

    def __init__(self, connect: Callable[[], sqlite3.Connection] = default_connection, *,
                 bot: str = "", clock: Callable[[], float] = time.time):
        self._conn = connect()
        self._lock = threading.Lock()
        self._clock = clock
        self.bot = bot
        with self._lock, self._conn:
            for statement in _SCHEMA:
                self._conn.execute(statement)
        self._prune_seen()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock, self._conn:
            return self._conn.execute(sql, args).fetchall()

    # ── marker ──────────────────────────────────────────────────────────────────────────────────

    def marker(self) -> int | None:
        rows = self._execute("SELECT value FROM kv WHERE key = ?", (f"marker:{self.bot}",))
        try:
            return int(rows[0][0]) if rows else None
        except ValueError:
            return None

    def save_marker(self, marker: int | None) -> None:
        if marker is None:
            return
        self._execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (f"marker:{self.bot}", str(int(marker))))

    # ── chats ───────────────────────────────────────────────────────────────────────────────────

    def remember_dialog(self, user_id: str, chat_id: str) -> None:
        self._execute(
            "INSERT INTO dialogs (bot, user_id, chat_id, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(bot, user_id) DO UPDATE SET chat_id = excluded.chat_id, updated_at = excluded.updated_at",
            (self.bot, str(user_id), str(chat_id), self._clock()))

    def dialog_chat(self, user_id: str) -> str | None:
        rows = self._execute("SELECT chat_id FROM dialogs WHERE bot = ? AND user_id = ?", (self.bot, str(user_id)))
        return rows[0][0] if rows else None

    def remember_group(self, chat_id: str, title: str = "") -> None:
        self._execute(
            "INSERT INTO group_chats (bot, chat_id, title, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(bot, chat_id) DO UPDATE SET title = COALESCE(NULLIF(excluded.title, ''), group_chats.title),"
            " updated_at = excluded.updated_at",
            (self.bot, str(chat_id), title, self._clock()))

    def forget_group(self, chat_id: str) -> None:
        self._execute("DELETE FROM group_chats WHERE bot = ? AND chat_id = ?", (self.bot, str(chat_id)))

    def is_group(self, chat_id: str) -> bool:
        return bool(self._execute("SELECT 1 FROM group_chats WHERE bot = ? AND chat_id = ?", (self.bot, str(chat_id))))

    # ── dedup ───────────────────────────────────────────────────────────────────────────────────

    def first_seen(self, key: str) -> bool:
        """True the first time ``key`` is offered, False for every repeat (inside the TTL)."""
        with self._lock, self._conn:
            cursor = self._conn.execute("INSERT OR IGNORE INTO seen (bot, key, at) VALUES (?, ?, ?)",
                                        (self.bot, key, self._clock()))
            return cursor.rowcount == 1

    def _prune_seen(self) -> None:
        self._execute("DELETE FROM seen WHERE at < ?", (self._clock() - SEEN_TTL_SECONDS,))
