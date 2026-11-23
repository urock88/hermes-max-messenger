"""First contact: core's pairing reply in the guest's language, the owner's notice, reminders, approvals."""

from __future__ import annotations

import pytest
from conftest import message_update
from gateway.config import HomeChannel, Platform, PlatformConfig
from gateway.run_inbound_unauthorized import pairing_code_reply, pairing_rate_limited_reply
from max_messenger import onboarding
from max_messenger.adapter import MaxAdapter
from max_messenger.onboarding import Onboarding, approve_command, pick_language, safe_name
from max_messenger.settings import Settings
from max_messenger.setup_wizard import finish_lines


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


# ── the pure part ──────────────────────────────────────────────────────────────────────────────


def test_core_reply_is_recognised_in_its_real_shape():
    """The pattern must match what core really sends — this fails first if core changes it."""
    assert approve_command(pairing_code_reply("max", "CMKLP9SC")) == ("CMKLP9SC", "hermes pairing approve max CMKLP9SC")
    assert approve_command(pairing_code_reply("max", "AB23CD45", "-p work ")) == (
        "AB23CD45", "hermes -p work pairing approve max AB23CD45")
    assert approve_command(pairing_code_reply("telegram", "AB23CD45")) is None
    assert approve_command("an ordinary answer about hermes pairing") is None


@pytest.mark.parametrize(("locale", "fallback", "expected"), [
    ("ru", "en", "ru"), ("ru-RU", "en", "ru"), ("en_US", "ru", "en"), ("de", "en", "en"), ("", "ru", "ru"),
    ("", "xx", "ru"),
])
def test_language_follows_the_app_then_the_bot(locale, fallback, expected):
    assert pick_language(locale, fallback) == expected


def test_a_stranger_name_loses_its_markup():
    assert safe_name("  [Evil](https://x) `@all` ") == "Evilhttps://x all"
    assert safe_name("Ol‮ga\nNew​line") == "Olga Newline"
    assert len(safe_name("я" * 500)) <= 64


def test_only_a_fresh_stranger_chat_is_rewritten():
    clock = Clock()
    ob = Onboarding("ru", clock=clock, ttl_seconds=3600, quiet_seconds=600)
    core = pairing_code_reply("max", "CMKLP9SC")
    assert ob.outgoing("42", core).content == core  # nobody wrote from this chat
    ob.stranger_wrote("42", "42", "Ivan", "en")
    clock.now += onboarding.CONTACT_WINDOW_SECONDS + 1
    assert ob.outgoing("42", core).content == core  # too late to be core's answer to that message
    ob.stranger_wrote("42", "42", "Ivan", "en")
    rewrite = ob.outgoing("42", core)
    assert rewrite.issued is not None and "forward this whole message" in rewrite.content
    assert "`hermes pairing approve max CMKLP9SC`" in rewrite.content and "valid for 1 hour" in rewrite.content
    assert ob.outgoing("42", core).content == core  # one rewrite per message


def test_reminder_once_while_core_is_silent_then_core_again():
    clock = Clock()
    ob = Onboarding("ru", clock=clock, ttl_seconds=3600, quiet_seconds=600)
    ob.stranger_wrote("42", "42", "Ivan", "ru")
    ob.outgoing("42", pairing_code_reply("max", "CMKLP9SC"))
    clock.now += 60
    remind = ob.stranger_wrote("42", "42", "Ivan", "ru")
    assert remind is not None and remind.code == "CMKLP9SC"
    assert ob.stranger_wrote("42", "42", "Ivan", "ru") is None  # once per code
    ob2 = Onboarding("ru", clock=clock, ttl_seconds=3600, quiet_seconds=600)
    ob2.stranger_wrote("7", "7", "", "ru")
    ob2.outgoing("7", pairing_code_reply("max", "AAAA2222"))
    clock.now += 600
    assert ob2.stranger_wrote("7", "7", "", "ru") is None  # core answers with a new code itself


def test_approved_and_expired_codes_leave_the_list():
    clock = Clock()
    ob = Onboarding("ru", clock=clock, ttl_seconds=3600, quiet_seconds=600)
    for uid, code in (("1", "AAAA2222"), ("2", "BBBB3333")):
        ob.stranger_wrote(uid, uid, "", "ru")
        ob.outgoing(uid, pairing_code_reply("max", code))
    assert [p.user_id for p in ob.take_approved(lambda uid: uid == "1")] == ["1"]
    clock.now += 3601
    assert ob.take_approved(lambda uid: True) == [] and not ob.has_pending


def test_the_plugin_knows_core_pairing_timings():
    """Copied, not imported (gateway.pairing is not a public plugin surface): fail when core changes them."""
    from gateway.pairing import CODE_TTL_SECONDS, RATE_LIMIT_SECONDS

    assert (onboarding.CODE_TTL_SECONDS, onboarding.CORE_QUIET_SECONDS) == (CODE_TTL_SECONDS, RATE_LIMIT_SECONDS)


def test_other_replies_to_a_stranger_pass_unchanged():
    ob = Onboarding("en", clock=Clock())
    ob.stranger_wrote("42", "42", "", "ru")
    assert ob.outgoing("42", pairing_rate_limited_reply()).content == pairing_rate_limited_reply()
    assert ob.outgoing("42", "Sorry, this bot is private.").content == "Sorry, this bot is private."


def test_every_text_exists_in_both_languages():
    for key, variants in onboarding.TEXTS.items():
        assert set(variants) == {"ru", "en"}, key


def test_setup_ends_with_a_four_step_checklist():
    for lang in ("ru", "en"):
        lines = finish_lines(lang)
        assert [n for n in "1234" if any(line.strip().startswith(f"{n}.") for line in lines)] == list("1234")
        assert any("hermes gateway restart" in line for line in lines)
        assert any("hermes pairing approve max" in line for line in lines)
        assert any("MAX_ALLOWED_USERS" in line for line in lines)


# ── through the adapter ────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def adapter(make_client, state_factory):
    config = PlatformConfig(enabled=True, token="secret-token")
    config.home_channel = HomeChannel(platform=Platform("max"), chat_id="1", name="Home")
    built = MaxAdapter(config, settings=Settings(token="secret-token", update_commands=False, language="ru"),
                       client_factory=lambda s: make_client(s.token), state_factory=state_factory)
    built._authorization_check = lambda user_id, *a, **kw: user_id == "1"
    core_replies: list[str] = []

    async def core(event):
        # What Hermes core does with a stranger's DM: one pairing code, through the adapter.
        if event.source.user_id != "1" and not core_replies:
            core_replies.append(pairing_code_reply("max", "CMKLP9SC"))
            await built.send(event.source.chat_id, core_replies[-1])

    built.handle_message = core
    assert await built.connect()
    yield built
    await built.disconnect()


async def _feed(adapter, *updates):
    for update in updates:
        adapter._on_raw_update(update)
    await adapter._inbox.join()
    for task in list(adapter._side_tasks):
        if task.get_name() != "max-poll" and not task.done() and "watch" not in repr(task.get_coro()):
            await task


def _texts(fake_max, user_id: str) -> list[str]:
    return [c["json"]["text"] for c in fake_max.made("POST", "/messages") if c["params"].get("user_id") == user_id]


async def test_a_stranger_gets_the_code_in_their_language_and_the_owner_hears(adapter, fake_max):
    update = message_update(text="привет", user_id=42, name="Olga [admin]")
    update["user_locale"] = "en"
    await _feed(adapter, update)
    (guest,) = _texts(fake_max, "42")
    assert "Hi! I don't recognize you yet" in guest and "CMKLP9SC" in guest and "forward this whole message" in guest
    (owner,) = _texts(fake_max, "1")
    assert "Новый человек просит доступ" in owner and "42" in owner and "Olga admin" in owner
    assert "hermes pairing approve max CMKLP9SC" in owner


async def test_bot_language_is_the_fallback_and_a_repeat_gets_one_reminder(adapter, fake_max):
    await _feed(adapter, message_update(text="hi", user_id=42, mid="m1"))
    assert "Ваш код доступа" in _texts(fake_max, "42")[0]
    await _feed(adapter, message_update(text="ау?", user_id=42, mid="m2"),
                message_update(text="ау??", user_id=42, mid="m3"))
    guest = _texts(fake_max, "42")
    assert len(guest) == 2 and guest[1].startswith("Код доступа уже выдан")
    assert len(_texts(fake_max, "1")) == 1  # the owner is told once per code


async def test_approval_is_announced_to_the_guest(adapter, fake_max):
    await _feed(adapter, message_update(text="hi", user_id=42))
    adapter._authorization_check = lambda user_id, *a, **kw: user_id in {"1", "42"}
    await adapter._check_approvals()
    assert _texts(fake_max, "42")[-1].startswith("Доступ одобрен")
    await adapter._check_approvals()
    assert len(_texts(fake_max, "42")) == 2


async def test_start_from_an_approved_person_says_they_may_write(adapter, fake_max):
    await _feed(adapter, {"update_type": "bot_started", "chat_id": 11, "user": {"user_id": 1, "first_name": "Me"},
                          "timestamp": 1, "user_locale": "en"})
    assert _texts(fake_max, "1") == ["You have access to this bot. Just type your question or send a voice note — "
                                      "I'll answer."]


async def test_a_wrong_token_is_explained_in_the_bot_language(fake_max, make_client, state_factory):
    fake_max.queue("GET", "/me", (401, {"code": "verify.token", "message": "Invalid access_token"}))
    built = MaxAdapter(PlatformConfig(enabled=True, token="bad"),
                       settings=Settings(token="bad", update_commands=False, language="ru"),
                       client_factory=lambda s: make_client(s.token), state_factory=state_factory)
    assert not await built.connect()
    assert built._fatal_error_code == "max_auth"
    assert built._fatal_error_message.startswith("MAX отклонил токен бота") and "MAX_BOT_TOKEN" in built._fatal_error_message


async def test_an_unreachable_api_names_the_host(fake_max, make_client, state_factory):
    fake_max.queue("GET", "/me", (502, {"message": "Bad Gateway"}))
    built = MaxAdapter(PlatformConfig(enabled=True, token="t"),
                       settings=Settings(token="t", update_commands=False, language="en"),
                       client_factory=lambda s: make_client(s.token), state_factory=state_factory)
    assert not await built.connect()
    assert built._fatal_error_message.startswith("Cannot reach the MAX API (platform-api2.max.ru)")
    assert "Traceback" not in built._fatal_error_message
