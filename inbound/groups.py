"""When the bot answers in a group chat.

By default only when it is addressed: an @-mention of the bot (MAX marks it as ``user_mention``
markup, and the text carries ``@username``), a reply to one of the bot's messages, or a slash
command. ``free_chats`` lists group chats where every message counts. Who may use the bot at all is
decided by Hermes' authorization afterwards (allowed users, ``MAX_GROUP_ALLOWED_CHATS``); this module
only decides whether a message is meant for the bot.
"""

from __future__ import annotations

import re

from ..maxapi.models import IncomingMessage


def addressed_text(message: IncomingMessage, *, bot_user_id: int | None, bot_username: str = "",
                   require_mention: bool = True, free_chats: frozenset[str] = frozenset()) -> str | None:
    """The text the agent should see, with the bot's mention removed, or None to stay silent."""
    text = message.text or ""
    mentioned = any(m.user_id is not None and m.user_id == bot_user_id for m in message.mentions)
    handle = f"@{bot_username}".lower() if bot_username else ""
    if handle and re.search(rf"(?<![\w@]){re.escape(handle)}(?!\w)", text, re.IGNORECASE):
        mentioned = True
    replied = bot_user_id is not None and message.reply_to_sender_id == bot_user_id
    command = text.lstrip().startswith("/")
    free = str(message.chat_id) in free_chats
    if require_mention and not (mentioned or replied or command or free):
        return None
    # Drop the mention spans (right to left, so offsets stay valid), then any "@handle" left.
    for mention in sorted(message.mentions, key=lambda m: m.start, reverse=True):
        if mention.user_id == bot_user_id and 0 <= mention.start <= len(text):
            text = text[:mention.start] + text[mention.start + mention.length:]
    if handle:
        text = re.sub(rf"(?<![\w@]){re.escape(handle)}(?!\w)", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"^(/\w+){re.escape(handle)}", r"\1", text.lstrip(), flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", text).strip(" \t,:")
