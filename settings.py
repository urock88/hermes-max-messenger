"""The adapter's settings: environment first, then ``config.yaml`` (``platforms.max.extra``), then defaults.

Values are read through Hermes' profile-aware helpers, never ``os.environ``, so two Hermes profiles
served by one gateway each see their own bot token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PLATFORM = "max"


def _truthy(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _csv(value: Any) -> frozenset[str]:
    items = value if isinstance(value, (list, tuple, set, frozenset)) else str(value or "").split(",")
    return frozenset(str(v).strip() for v in items if str(v).strip())


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    token: str = ""
    api_base: str = "https://platform-api2.max.ru"
    language: str = "ru"
    require_mention: bool = True
    free_chats: frozenset[str] = field(default_factory=frozenset)
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8646
    webhook_path: str = "/max/webhook"
    polling_takeover: bool = False
    table_as_image: bool = False
    update_commands: bool = True

    @property
    def webhook_mode(self) -> bool:
        return bool(self.webhook_url)


def read_settings(config: Any | None = None) -> Settings:
    from gateway.platforms._shared import extra_or_secret

    extra = dict(getattr(config, "extra", None) or {})

    def get(key: str, env: str, default: Any = "") -> Any:
        return extra_or_secret(extra, key, env, default)

    token = getattr(config, "token", None) or get("token", "MAX_BOT_TOKEN")
    return Settings(
        token=str(token or "").strip(),
        api_base=str(get("api_base", "MAX_API_BASE", "https://platform-api2.max.ru") or "").strip()
        or "https://platform-api2.max.ru",
        language=str(get("language", "MAX_LANGUAGE", "ru") or "ru").strip().lower(),
        require_mention=_truthy(get("require_mention", "MAX_REQUIRE_MENTION", ""), True),
        free_chats=_csv(get("free_response_chats", "MAX_FREE_RESPONSE_CHATS", "")),
        webhook_url=str(get("webhook_url", "MAX_WEBHOOK_URL", "") or "").strip(),
        webhook_secret=str(get("webhook_secret", "MAX_WEBHOOK_SECRET", "") or "").strip(),
        webhook_host=str(get("webhook_host", "MAX_WEBHOOK_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        webhook_port=_int(get("webhook_port", "MAX_WEBHOOK_PORT", 8646), 8646),
        webhook_path=str(get("webhook_path", "MAX_WEBHOOK_PATH", "/max/webhook") or "/max/webhook").strip(),
        polling_takeover=_truthy(get("polling_takeover", "MAX_POLLING_TAKEOVER", ""), False),
        table_as_image=_truthy(get("table_as_image", "MAX_TABLE_AS_IMAGE", ""), False),
        update_commands=_truthy(get("update_commands", "MAX_UPDATE_COMMANDS", ""), True),
    )
