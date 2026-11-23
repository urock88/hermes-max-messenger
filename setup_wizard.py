"""``hermes gateway setup`` → MAX."""

from __future__ import annotations

import asyncio
import secrets


def _check_token(token: str) -> tuple[bool, str]:
    from .maxapi import MaxApiError, MaxClient

    async def probe() -> tuple[bool, str]:
        client = MaxClient(token)
        try:
            me = await client.get_me()
            name = me.get("first_name") or me.get("name") or "?"
            return True, f"{name} (@{me.get('username') or '?'}, id {me.get('user_id')})"
        except MaxApiError as exc:
            return False, "MAX refused the token (HTTP 401)" if exc.is_auth else str(exc)
        finally:
            await client.close()

    try:
        return asyncio.run(probe())
    except RuntimeError:  # a running loop (embedded use): skip the live check
        return True, "not checked"


def interactive_setup() -> None:
    from hermes_cli.cli_output import print_info, print_success, print_warning
    from hermes_cli.gateway_setup_wizard import (
        confirm_reconfigure,
        print_setup_header,
        prompt_csv,
        save_env_values,
    )
    from hermes_cli.setup import prompt, prompt_choice, prompt_yes_no

    print_setup_header("MAX")
    if not confirm_reconfigure("MAX", "MAX_BOT_TOKEN"):
        return
    for line in (
        "1. On business.max.ru open Chat bots → Create a bot (a verified organisation or",
        "   self-employed profile is required by MAX).",
        "2. Open the bot → Integration → copy the token.",
        "3. Use a separate bot for Hermes: one token can be polled by only one program.",
    ):
        print_info(line)
    token = prompt("MAX bot token", password=True).strip()
    if not token:
        return
    good, detail = _check_token(token)
    if not good:
        print_warning(f"Token check failed: {detail}")
        if not prompt_yes_no("Save it anyway?", False):
            return
    else:
        print_success(f"Bot: {detail}")
    save_env_values(MAX_BOT_TOKEN=token)

    print()
    print_info("Who may use the bot. Leave empty to use pairing: a new person gets a code that you")
    print_info("approve with  hermes pairing approve max <code>.")
    allowed = prompt_csv("Allowed MAX user ids (comma-separated)", "")
    if allowed:
        save_env_values(MAX_ALLOWED_USERS=allowed)

    print()
    mode = prompt_choice("How should updates arrive?", [
        "Long polling (works anywhere, no public address)",
        "Webhook (needs an https URL on port 443 and a reverse proxy)",
    ], 0)
    if mode == 1:
        url = prompt("Public webhook URL (https://…/max/webhook)").strip()
        if url:
            secret = prompt("Webhook secret (empty = generate)").strip() or secrets.token_urlsafe(24)
            save_env_values(MAX_WEBHOOK_URL=url, MAX_WEBHOOK_SECRET=secret)
            print_info("Point your reverse proxy at 127.0.0.1:8646/max/webhook (MAX_WEBHOOK_PORT / _PATH).")

    print()
    print_info("Home chat for cron results: your MAX user id, or a group chat id (negative).")
    print_info("You can also send /sethome in the chat later.")
    home = prompt("Home chat id (empty = later)").strip()
    if home:
        save_env_values(MAX_HOME_CHANNEL=home)

    print()
    print_info("Voice notes are transcribed by Hermes' speech-to-text (config.yaml → stt.provider).")
    print_info("This plugin adds: yandex_speechkit (YANDEX_SPEECHKIT_API_KEY) and")
    print_info("sber_salutespeech (SALUTESPEECH_AUTH_KEY). Without a cloud key use 'local'.")
    print_success("MAX is configured. Restart the gateway: hermes gateway restart")
