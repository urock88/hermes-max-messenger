"""MAX messenger for Hermes Agent.

``register(ctx)`` adds the ``max`` gateway platform and two speech-to-text providers
(``yandex_speechkit``, ``sber_salutespeech``). Heavy imports wait until the gateway builds the
adapter, so loading the plugin in a CLI process costs next to nothing.
"""

from __future__ import annotations

import dataclasses
import logging

logger = logging.getLogger(__name__)

PLATFORM_HINT = (
    "You are chatting through the MAX messenger. Replies are shown with basic formatting: bold, italic, "
    "strikethrough, inline code, code blocks, links, quotes and lists work; headings become bold lines "
    "and tables become monospace columns. Keep messages compact — a phone screen is narrow. To send a "
    "file, picture, video or voice note, put MEDIA:/absolute/path on its own line."
)

_YAML_BRIDGE = (
    ("require_mention", "MAX_REQUIRE_MENTION", "lower"),
    ("free_response_chats", "MAX_FREE_RESPONSE_CHATS", "csv"),
    ("group_allowed_chats", "MAX_GROUP_ALLOWED_CHATS", "csv"),
    ("webhook_url", "MAX_WEBHOOK_URL", "str"),
    ("webhook_port", "MAX_WEBHOOK_PORT", "str"),
    ("table_as_image", "MAX_TABLE_AS_IMAGE", "lower"),
    ("language", "MAX_LANGUAGE", "str"),
)


def _adapter_factory(config):
    from .adapter import MaxAdapter

    return MaxAdapter(config)


def _check_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def _validate_config(config) -> bool:
    from .settings import read_settings

    return bool(read_settings(config).token)


def _is_connected(config) -> bool:
    from gateway.platforms._shared import env_is_connected

    return bool(getattr(config, "token", None)) or env_is_connected("MAX_BOT_TOKEN")(config)


def _env_enablement() -> dict | None:
    from gateway.platforms._shared import get_scoped_secret, seed_extra_from_env

    if not str(get_scoped_secret("MAX_BOT_TOKEN", "") or "").strip():
        return None
    return seed_extra_from_env(
        (("MAX_BOT_TOKEN", "token", None), ("MAX_WEBHOOK_URL", "webhook_url", None),
         ("MAX_LANGUAGE", "language", None)),
        home_env="MAX_HOME_CHANNEL")


def _apply_yaml_config(yaml_cfg: dict, max_cfg: dict) -> dict | None:
    from gateway.platforms._shared import apply_yaml_bridge

    return apply_yaml_bridge(max_cfg or {}, _YAML_BRIDGE)


async def _standalone_send(pconfig, chat_id, message, **kwargs):
    from .standalone import standalone_send

    return await standalone_send(pconfig, chat_id, message, **kwargs)


def _setup():
    from .setup_wizard import interactive_setup

    interactive_setup()


def _platform_kwargs() -> dict:
    kwargs = dict(
        name="max", label="MAX", adapter_factory=_adapter_factory, check_fn=_check_requirements,
        validate_config=_validate_config, required_env=["MAX_BOT_TOKEN"],
        install_hint="hermes plugins install urock88/hermes-max-messenger --enable",
        setup_fn=_setup, env_enablement_fn=_env_enablement, apply_yaml_config_fn=_apply_yaml_config,
        is_connected=_is_connected, cron_deliver_env_var="MAX_HOME_CHANNEL", standalone_sender_fn=_standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS", allow_all_env="MAX_ALLOW_ALL_USERS",
        max_message_length=3500, emoji="🟪", platform_hint=PLATFORM_HINT, pii_safe=True,
        allow_update_command=True,
    )
    optional = {
        "group_allowed_chats_env": "MAX_GROUP_ALLOWED_CHATS",
        "display_tier": "high",
        "dashboard_label": "MAX",
        "dashboard_description": "Chat with Hermes in the MAX messenger (max.ru): DMs and groups.",
        "docs_url": "https://github.com/urock88/hermes-max-messenger",
        "dashboard_env_vars": ("MAX_BOT_TOKEN", "MAX_ALLOWED_USERS", "MAX_HOME_CHANNEL"),
    }
    try:
        from gateway.platform_registry import PlatformEntry

        known = {f.name for f in dataclasses.fields(PlatformEntry)}
    except Exception:  # noqa: BLE001
        known = set()
    kwargs.update({k: v for k, v in optional.items() if k in known})
    return kwargs


def register(ctx) -> None:
    """Called by Hermes' plugin loader."""
    ctx.register_platform(**_platform_kwargs())
    register_stt = getattr(ctx, "register_transcription_provider", None)
    if register_stt is None:
        logger.info("MAX plugin: this Hermes has no transcription-provider API; STT providers skipped")
        return
    from .stt.sber_salutespeech import SberSaluteSpeechProvider
    from .stt.yandex_speechkit import YandexSpeechKitProvider

    for provider in (YandexSpeechKitProvider(), SberSaluteSpeechProvider()):
        try:
            register_stt(provider)
        except Exception as exc:  # noqa: BLE001 - one provider must not cost the platform
            logger.warning("MAX plugin: could not register %s: %s", provider.name, exc)
