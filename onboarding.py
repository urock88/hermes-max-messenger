"""First contact: what a stranger, a newly approved person and the owner read, in their language.

Hermes core answers a stranger's first message with a pairing code, in core's one global language
(``display.language``, English unless changed), and tells nobody else. That reply goes out through
:meth:`MaxAdapter.send`, so the adapter recognises it on the way — a reply to a chat a stranger has
written from seconds ago that carries ``pairing approve max <code>`` — and replaces it with its own
text in the language of the person's MAX app (``user_locale`` of the update, else ``MAX_LANGUAGE``).

Nothing about authorization changes here: core still decides who is a stranger, mints the code,
rate-limits it and approves it. The adapter only rewords that one reply and adds what core does not do:

* the owner hears about the request in the home chat at once, instead of when a code is forwarded;
* a stranger who writes again while core stays silent (one code per 10 minutes) is reminded of the
  code once, instead of being ignored;
* the guest is told when they have been approved;
* ``/start`` from an approved person says they may just write.

If core ever changes its reply so the pattern no longer matches, the reply passes through unchanged
(core's own text), and the CI run against Hermes ``main`` fails ``test_onboarding``. Core's other
answer to a stranger — "too many requests" when three codes are already pending — is left as core
says it: telling it from an owner's custom decline text would need core internals.

Codes live in memory only (core keeps just a salted hash on disk; so does this module on no disk).
A gateway restart forgets pending codes: the approved guest then simply gets an answer to their next
message.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LANGUAGES = ("ru", "en")
# Core's pairing timings (gateway/pairing.py CODE_TTL_SECONDS, RATE_LIMIT_SECONDS), copied because that
# module is not a public plugin surface; test_onboarding fails when core changes them.
CODE_TTL_SECONDS = 3600
CORE_QUIET_SECONDS = 600
# A stranger's chat counts as "just wrote" for this long: core answers within the same turn.
CONTACT_WINDOW_SECONDS = 120
# A name from the stranger, shown to the owner: bounded and without markup.
_MAX_NAME_CHARS = 64
_NAME_SIGILS = set("@<>[]()`*_~#|\\")

# ``hermes [-p <profile>] pairing approve max <CODE>``, as core builds it (gateway.run_inbound_unauthorized).
_APPROVE_COMMAND = re.compile(r"hermes\s+(?:-p\s+\S+\s+)?pairing\s+approve\s+max\s+([A-Z0-9]{4,16})\b")


def pick_language(user_locale: str, fallback: str) -> str:
    """``ru`` or ``en``: the person's MAX app language when it is one of them, else the bot's."""
    for value in (user_locale, fallback):
        lang = (value or "").strip().lower().replace("_", "-").split("-")[0]
        if lang in LANGUAGES:
            return lang
    return "ru"


def _hours(seconds: int, lang: str) -> str:
    hours = max(1, round(seconds / 3600))
    if lang == "en":
        return "1 hour" if hours == 1 else f"{hours} hours"
    if hours % 10 == 1 and hours % 100 != 11:
        word = "час"
    elif hours % 10 in (2, 3, 4) and hours % 100 not in (12, 13, 14):
        word = "часа"
    else:
        word = "часов"
    return f"{hours} {word}"


def safe_name(name: str) -> str:
    """A stranger's display name, fit to show the owner: one line, no control or direction-changing
    characters, no markup or mention sigils."""
    out = []
    for ch in str(name or ""):
        if ch.isspace():
            out.append(" ")
        elif unicodedata.category(ch) not in {"Cc", "Cf"} and ch not in _NAME_SIGILS:
            out.append(ch)
    return " ".join("".join(out).split())[:_MAX_NAME_CHARS].strip()


def mask_code(code: str) -> str:
    return code[:2] + "*" * max(0, len(code) - 4) + code[-2:] if len(code) > 4 else "*" * len(code)


TEXTS: dict[str, dict[str, str]] = {
    "code_reply": {
        "ru": ("Привет! Я вас пока не знаю и не могу отвечать: сначала владелец бота должен вас одобрить.\n\n"
               "Ваш код доступа: `{code}` (действителен {validity})\n\n"
               "Просто перешлите это сообщение владельцу бота любым способом — этого достаточно. "
               "Если бота запускаете вы сами, выполните в терминале: `{approve_cmd}`\n\n"
               "Когда владелец вас одобрит, я сообщу здесь. Если сообщения нет, просто напишите ещё раз чуть позже."),
        "en": ("Hi! I don't recognize you yet, so I can't reply until the bot's owner approves you.\n\n"
               "Your access code: `{code}` (valid for {validity})\n\n"
               "Just forward this whole message to the bot's owner — that's all it takes. "
               "If you run this bot yourself, run this in a terminal: `{approve_cmd}`\n\n"
               "I'll tell you here once you're approved. If you hear nothing, just write again a bit later."),
    },
    "reminder": {
        "ru": ("Код доступа уже выдан: `{code}`. Перешлите сообщение с кодом владельцу бота — "
               "пока он вас не одобрит, я не могу отвечать. Когда одобрит, я сообщу здесь."),
        "en": ("Your access code is already issued: `{code}`. Forward the message with the code to the "
               "bot's owner — I can't reply until they approve you. I'll tell you here once they do."),
    },
    "approved": {
        "ru": "Доступ одобрен ✅ Напишите ваш вопрос — теперь я отвечу.",
        "en": "You're approved ✅ Send your question — I'll answer now.",
    },
    "welcome": {
        "ru": "Вы допущены к боту. Просто напишите вопрос или пришлите голосовое — я отвечу.",
        "en": "You have access to this bot. Just type your question or send a voice note — I'll answer.",
    },
    "owner_notice": {
        "ru": ("🔑 Новый человек просит доступ к боту в MAX\n\n"
               "Имя: {name}\nMAX id: {user_id}\nКод: `{code}` (действителен {validity})\n\n"
               "Одобрить: `{approve_cmd}`\n"
               "Не знаете этого человека — ничего не делайте, код истечёт сам."),
        "en": ("🔑 Someone new asks for access to the MAX bot\n\n"
               "Name: {name}\nMAX id: {user_id}\nCode: `{code}` (valid for {validity})\n\n"
               "Approve: `{approve_cmd}`\n"
               "Don't know this person? Do nothing — the code expires by itself."),
    },
    # Fatal start-up errors: shown by `hermes gateway status` and in the log. No stack traces.
    "error_no_token": {
        "ru": ("Не задан MAX_BOT_TOKEN. Выполните `hermes gateway setup` → MAX и вставьте токен бота "
               "(business.max.ru → Чат-боты → ваш бот → Интеграция)."),
        "en": ("MAX_BOT_TOKEN is not set. Run `hermes gateway setup` → MAX and paste the bot token "
               "(business.max.ru → Chat bots → your bot → Integration)."),
    },
    "error_auth": {
        "ru": ("MAX отклонил токен бота (HTTP 401). Проверьте MAX_BOT_TOKEN: скопируйте токен заново "
               "(business.max.ru → Чат-боты → ваш бот → Интеграция), сохраните его через "
               "`hermes gateway setup` и выполните `hermes gateway restart`."),
        "en": ("MAX refused the bot token (HTTP 401). Check MAX_BOT_TOKEN: copy the token again "
               "(business.max.ru → Chat bots → your bot → Integration), save it with `hermes gateway setup` "
               "and run `hermes gateway restart`."),
    },
    "error_unreachable": {
        "ru": ("Нет связи с MAX API ({host}): {reason}. Проверьте сеть и прокси этой машины и что "
               "{host}:443 открывается отсюда. Шлюз повторит попытку сам."),
        "en": ("Cannot reach the MAX API ({host}): {reason}. Check this machine's network and proxy and that "
               "{host}:443 is reachable from here. The gateway retries by itself."),
    },
    "error_webhook_active": {
        "ru": ("У бота есть webhook-подписки ({count}), а пока они есть, MAX не отдаёт обновления long polling. "
               "Скорее всего, этот бот уже подключён к другой программе: у одного бота может быть только "
               "один получатель. Возьмите для Hermes отдельного бота или задайте MAX_POLLING_TAKEOVER=true, "
               "чтобы адаптер снял подписки сам."),
        "en": ("This bot has {count} webhook subscription(s), and MAX does not deliver to long polling while "
               "one exists. Most likely another program already uses this bot: a bot has exactly one reader. "
               "Use a separate bot for Hermes, or set MAX_POLLING_TAKEOVER=true to let the adapter remove them."),
    },
    "error_start": {
        "ru": "MAX: не удалось начать приём сообщений: {reason}. Шлюз повторит попытку сам.",
        "en": "MAX could not start receiving updates: {reason}. The gateway retries by itself.",
    },
    "error_poll_auth": {
        "ru": ("MAX перестал принимать токен бота во время работы (HTTP 401): токен отозван или заменён. "
               "Скопируйте новый токен (business.max.ru → Чат-боты → ваш бот → Интеграция), сохраните "
               "через `hermes gateway setup` и выполните `hermes gateway restart`."),
        "en": ("MAX stopped accepting the bot token while running (HTTP 401): it was revoked or replaced. "
               "Copy the new token (business.max.ru → Chat bots → your bot → Integration), save it with "
               "`hermes gateway setup` and run `hermes gateway restart`."),
    },
    # `hermes gateway setup` → MAX, the closing checklist.
    "setup_checklist": {
        "ru": ("Что дальше:\n"
               "  1. Перезапустите шлюз: hermes gateway restart\n"
               "  2. Напишите боту в MAX любое сообщение.\n"
               "  3. Если список MAX_ALLOWED_USERS пуст, бот ответит кодом доступа. Одобрите его:\n"
               "       hermes pairing approve max <код>\n"
               "     Так же входит каждый новый человек: он пишет боту, пересылает вам код, вы одобряете.\n"
               "     Заявки видны в hermes pairing list; если задан домашний чат, бот напишет туда сам.\n"
               "  4. Чтобы пускать только заранее известных людей, впишите их MAX id через запятую в\n"
               "     MAX_ALLOWED_USERS (id одобренных показывает hermes pairing list). С непустым списком\n"
               "     бот молчит в ответ незнакомцам, а их попытки видны в журнале и в домашнем чате."),
        "en": ("Next steps:\n"
               "  1. Restart the gateway: hermes gateway restart\n"
               "  2. Send the bot any message in MAX.\n"
               "  3. If MAX_ALLOWED_USERS is empty, the bot answers with an access code. Approve it:\n"
               "       hermes pairing approve max <code>\n"
               "     Every new person joins the same way: they message the bot, forward you the code, you approve.\n"
               "     Requests are listed by hermes pairing list; with a home chat set the bot also posts them there.\n"
               "  4. To admit only people you know in advance, put their MAX ids, comma-separated, into\n"
               "     MAX_ALLOWED_USERS (hermes pairing list shows the ids of approved people). With a non-empty\n"
               "     list the bot stays silent to strangers; their attempts show up in the log and the home chat."),
    },
    "setup_one_reader": {
        "ru": ("⚠ У одного бота MAX может быть только один получатель обновлений. Не запускайте второй шлюз "
               "или вебхук с этим же токеном — сообщения начнут теряться."),
        "en": ("⚠ A MAX bot has exactly one reader of its updates. Don't run a second gateway or webhook with "
               "the same token — messages would start going missing."),
    },
}


def text(key: str, lang: str, **values: object) -> str:
    variants = TEXTS[key]
    return variants.get(lang, variants["ru"]).format(**values)


def approve_command(content: str) -> tuple[str, str] | None:
    """``(code, command)`` when ``content`` is core's pairing reply for MAX, else None."""
    match = _APPROVE_COMMAND.search(content or "")
    return (match.group(1), match.group(0)) if match else None


@dataclass
class Contact:
    """A stranger who has just written: the chat core is about to answer."""

    user_id: str
    name: str
    lang: str
    at: float


@dataclass
class PendingCode:
    user_id: str
    chat_id: str
    name: str
    lang: str
    code: str
    approve_cmd: str
    issued_at: float
    reminded: bool = False


@dataclass(frozen=True)
class Rewrite:
    """What :meth:`Onboarding.outgoing` decided about one outgoing message."""

    content: str
    issued: PendingCode | None = None


class Onboarding:
    """The adapter's memory of first contacts. Pure bookkeeping: the adapter does every send."""

    def __init__(self, fallback_language: str, *, clock: Callable[[], float] = time.time,
                 ttl_seconds: int | None = None, quiet_seconds: int | None = None):
        self.fallback = pick_language("", fallback_language)
        self.ttl = int(ttl_seconds if ttl_seconds is not None else CODE_TTL_SECONDS)
        self.quiet = int(quiet_seconds if quiet_seconds is not None else CORE_QUIET_SECONDS)
        self._clock = clock
        self._contacts: dict[str, Contact] = {}
        self._pending: dict[str, PendingCode] = {}

    # ── inbound ────────────────────────────────────────────────────────────────────────────────

    def stranger_wrote(self, chat_id: str, user_id: str, name: str, user_locale: str) -> PendingCode | None:
        """Note a DM from someone core does not know yet. Returns the code to remind them of, once,
        when core is going to stay silent (it answers a person at most once per ``quiet`` seconds)."""
        now = self._clock()
        self._forget_old(now)
        lang = pick_language(user_locale, self.fallback)
        self._contacts[str(chat_id)] = Contact(user_id=str(user_id), name=name, lang=lang, at=now)
        pending = self._pending.get(str(user_id))
        if pending is None or pending.reminded:
            return None
        if now - pending.issued_at >= self.quiet - 5:
            return None  # core is about to answer with a fresh code itself
        pending.reminded = True
        pending.lang = lang
        return pending

    def known_wrote(self, user_id: str) -> None:
        """An authorized person wrote: whatever code they had is history."""
        self._pending.pop(str(user_id), None)

    def language_for(self, user_locale: str) -> str:
        return pick_language(user_locale, self.fallback)

    # ── outbound ───────────────────────────────────────────────────────────────────────────────

    def outgoing(self, chat_id: str, content: str) -> Rewrite:
        """Recognise core's pairing reply to a stranger and say it in the stranger's language."""
        contact = self._contacts.get(str(chat_id))
        if contact is None or self._clock() - contact.at > CONTACT_WINDOW_SECONDS:
            return Rewrite(content)
        found = approve_command(content)
        if found is None:
            return Rewrite(content)
        code, command = found
        pending = PendingCode(user_id=contact.user_id, chat_id=str(chat_id), name=contact.name,
                              lang=contact.lang, code=code, approve_cmd=command, issued_at=self._clock())
        self._pending[contact.user_id] = pending
        self._contacts.pop(str(chat_id), None)
        return Rewrite(self.code_reply(pending), issued=pending)

    def code_reply(self, pending: PendingCode) -> str:
        return text("code_reply", pending.lang, code=pending.code, validity=_hours(self.ttl, pending.lang),
                    approve_cmd=pending.approve_cmd)

    def owner_notice(self, pending: PendingCode) -> str:
        return text("owner_notice", self.fallback, name=safe_name(pending.name) or "—", user_id=pending.user_id,
                    code=pending.code, validity=_hours(self.ttl, self.fallback), approve_cmd=pending.approve_cmd)

    # ── approvals ──────────────────────────────────────────────────────────────────────────────

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def take_approved(self, is_approved: Callable[[str], bool]) -> list[PendingCode]:
        """Pending codes whose person is now authorized (removed from the list); expired ones are dropped."""
        now = self._clock()
        self._forget_old(now)
        approved = []
        for user_id in list(self._pending):
            try:
                ok = is_approved(user_id)
            except Exception:  # noqa: BLE001 - a failed check is "not yet"
                logger.debug("MAX: approval check failed for %s", user_id, exc_info=True)
                ok = False
            if ok:
                approved.append(self._pending.pop(user_id))
        return approved

    def _forget_old(self, now: float) -> None:
        for chat_id, contact in list(self._contacts.items()):
            if now - contact.at > CONTACT_WINDOW_SECONDS:
                del self._contacts[chat_id]
        for user_id, pending in list(self._pending.items()):
            if now - pending.issued_at > self.ttl:
                del self._pending[user_id]
