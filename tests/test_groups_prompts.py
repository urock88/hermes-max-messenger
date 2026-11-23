"""Group gating and owner-bound prompts."""

from __future__ import annotations

from conftest import BOT_ID, message_update
from max_messenger.inbound.groups import addressed_text
from max_messenger.interactive.prompts import Prompts, keyboard, parse_payload, payload
from max_messenger.maxapi.models import parse_update


def group(text: str, **kw):
    return parse_update(message_update(text=text, chat_id=-500, chat_type="chat", **kw))


def test_group_messages_need_to_address_the_bot():
    assert addressed_text(group("hello all"), bot_user_id=BOT_ID, bot_username="hermes_bot") is None
    assert addressed_text(group("@hermes_bot what time is it?"), bot_user_id=BOT_ID,
                          bot_username="hermes_bot") == "what time is it?"
    assert addressed_text(group("/new@hermes_bot"), bot_user_id=BOT_ID, bot_username="hermes_bot") == "/new"
    mention = [{"type": "user_mention", "from": 0, "length": 6, "user_id": BOT_ID}]
    assert addressed_text(group("Hermes, help", markup=mention), bot_user_id=BOT_ID) == "help"
    reply = {"type": "reply", "sender": {"user_id": BOT_ID}, "message": {"mid": "m0", "text": "?"}}
    assert addressed_text(group("yes", link=reply), bot_user_id=BOT_ID) == "yes"
    assert addressed_text(group("hello"), bot_user_id=BOT_ID, free_chats=frozenset({"-500"})) == "hello"
    assert addressed_text(group("hello"), bot_user_id=BOT_ID, require_mention=False) == "hello"


def test_prompt_is_answered_only_by_its_owner_and_a_refusal_does_not_consume_it():
    clock = [0.0]
    prompts = Prompts(clock=lambda: clock[0])
    prompt = prompts.add("approval", session_key="s", owner_user_id="42", chat_id="42")
    prompts.bind_message(prompt.id, "mid.9")
    assert prompts.check(prompt.id, user_id="13", chat_id="42")[1] == "not the owner"
    assert prompts.check(prompt.id, user_id="42", chat_id="42", message_id="mid.8")[1] == "stale button"
    found, reason = prompts.check(prompt.id, user_id="42", chat_id="42", message_id="mid.9")
    assert found is prompt and reason == ""
    clock[0] = 10_000
    assert prompts.check(prompt.id, user_id="42", chat_id="42")[1] == "expired"


def test_supersede_drops_older_pickers_of_the_chat():
    prompts = Prompts()
    old = prompts.add("model", session_key="s", owner_user_id="1", chat_id="1")
    prompts.add("model", session_key="s", owner_user_id="1", chat_id="1", supersede=True)
    assert prompts.get(old.id) is None


def test_payloads_and_keyboard():
    assert parse_payload(payload("abc", "once")) == ("abc", "once")
    assert parse_payload(payload("abc", "m:qwen3:8b")) == ("abc", "m:qwen3:8b")
    assert parse_payload("foreign:payload") is None
    board = keyboard([[("x" * 200, "hm:a:b")] * 9])
    row = board["payload"]["buttons"][0]
    assert board["type"] == "inline_keyboard" and len(row) == 7 and len(row[0]["text"]) == 128
