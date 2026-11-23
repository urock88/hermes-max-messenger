"""Hermes prompts rendered as MAX inline keyboards, and what a press does.

Mixed into the adapter. It relies on the adapter for ``_client``, ``_prompts``, ``_language``,
``_send_with_keyboard`` (send a message with a keyboard attachment), ``_prompt_owner`` (who may
answer a prompt) and ``_hermes_chat_id`` (the Hermes chat id of a press).

Every press is acknowledged with ``POST /answers``; when the press settles the prompt, the same call
replaces the message text and removes the keyboard, so the chat shows what was decided.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from ..maxapi import MaxApiError
from ..maxapi.models import CallbackPress
from .prompts import keyboard, parse_payload, payload, rows_of
from .ui import text as ui

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
PROVIDER_PAGE, MODEL_PAGE = 10, 8


def html_to_plain(value: str) -> str:
    return html.unescape(_TAG.sub("", value or ""))


class InteractiveMixin:
    # Exec-approval markup: Hermes builds the text, the adapter supplies HTML pieces.
    _EA_CODE_OPEN = "<pre>"
    _EA_CODE_CLOSE = "</pre>\n"

    def _ea_escape(self, text: str) -> str:
        return html.escape(text or "", quote=False)

    # ── sending prompts ─────────────────────────────────────────────────────────────────────────

    async def _send_exec_approval_prompt(self, prompt):
        owner = self._prompt_owner(prompt.session_key, prompt.chat_id, prompt.metadata)
        pending = self._prompts.add("approval", session_key=prompt.session_key, owner_user_id=owner,
                                    chat_id=prompt.chat_id, data={"text": prompt.text})
        buttons = [(label, payload(pending.id, choice)) for label, choice, _style in prompt.actions]
        return await self._send_prompt(pending, prompt.chat_id, prompt.text, rows_of(buttons, 2), prompt.metadata)

    # Slash-command confirmations (/reload-mcp ...) stay on Hermes' text path (/approve, /always,
    # /cancel): resolving them from a button needs a core module that is not public plugin API.

    async def send_clarify(self, chat_id: str, question: str, choices: list | None, clarify_id: str,
                           session_key: str, metadata: dict[str, Any] | None = None):
        body = f"❓ {html.escape(question)}"
        if not choices:
            # Open question: the gateway captures the next message as the answer.
            return await self._send_prompt(None, chat_id, body, [], metadata)
        body += "\n\n" + "\n".join(f"{i}. {html.escape(str(c))}" for i, c in enumerate(choices, start=1))
        owner = self._prompt_owner(session_key, chat_id, metadata)
        pending = self._prompts.add("clarify", session_key=session_key, owner_user_id=owner, chat_id=chat_id,
                                    data={"text": body, "clarify_id": clarify_id, "choices": [str(c) for c in choices]})
        buttons = [(str(i + 1), payload(pending.id, str(i))) for i in range(len(choices))]
        rows = rows_of(buttons, 5) + [[(ui(self._language, "other"), payload(pending.id, "other"))]]
        return await self._send_prompt(pending, chat_id, body, rows, metadata)

    async def send_choice_picker(self, chat_id: str, title: str, choices: list, session_key: str,
                                 on_choice_selected, metadata: dict[str, Any] | None = None):
        owner = self._prompt_owner(session_key, chat_id, metadata)
        pending = self._prompts.add("choice", session_key=session_key, owner_user_id=owner, chat_id=chat_id,
                                    data={"choices": list(choices), "callback": on_choice_selected},
                                    supersede=True)
        buttons = []
        for i, choice in enumerate(choices):
            label = str(choice.get("label") or choice.get("value") or "")
            buttons.append((f"✓ {label}" if choice.get("is_current") else label, payload(pending.id, f"c{i}")))
        if not buttons:
            from gateway.platforms.base import SendResult
            return SendResult(success=False, error="No choices")
        return await self._send_prompt(pending, chat_id, html.escape(title), rows_of(buttons, 2), metadata)

    async def send_model_picker(self, chat_id: str, providers: list, current_model: str, current_provider: str,
                                session_key: str, on_model_selected, metadata: dict[str, Any] | None = None):
        owner = self._prompt_owner(session_key, chat_id, metadata)
        pending = self._prompts.add(
            "model", session_key=session_key, owner_user_id=owner, chat_id=chat_id, supersede=True,
            data={"providers": list(providers), "current_model": current_model,
                  "current_provider": current_provider, "callback": on_model_selected})
        body, rows = self._provider_page(pending, 0)
        return await self._send_prompt(pending, chat_id, body, rows, metadata)

    async def _send_prompt(self, pending, chat_id: str, body_html: str, rows, metadata):
        attachments = [keyboard(rows)] if rows else None
        result = await self._send_with_keyboard(chat_id, body_html, attachments, metadata)
        if pending is not None:
            if result.success and result.message_id:
                self._prompts.bind_message(pending.id, result.message_id)
            elif not result.success:
                self._prompts.pop(pending.id)
        return result

    # ── model picker pages ──────────────────────────────────────────────────────────────────────

    def _page_nav(self, pending, page: int, pages: int, token: str) -> list[tuple[str, str]]:
        nav = []
        if page > 0:
            nav.append((ui(self._language, "prev"), payload(pending.id, f"{token}{page - 1}")))
        if pages > 1:
            nav.append((f"{page + 1}/{pages}", payload(pending.id, f"{token}{page}")))
        if page < pages - 1:
            nav.append((ui(self._language, "next"), payload(pending.id, f"{token}{page + 1}")))
        return nav

    def _provider_page(self, pending, page: int):
        lang, data = self._language, pending.data
        providers = data["providers"]
        pages = max(1, -(-len(providers) // PROVIDER_PAGE))
        page = max(0, min(page, pages - 1))
        start = page * PROVIDER_PAGE
        buttons = []
        for i, p in enumerate(providers[start:start + PROVIDER_PAGE], start=start):
            label = str(p.get("name") or p.get("slug") or "?")
            count = p.get("total_models", len(p.get("models") or []))
            label = f"{label} ({count})" if count else label
            buttons.append((f"✓ {label}" if p.get("is_current") else label, payload(pending.id, f"p{i}")))
        rows = rows_of(buttons, 2)
        nav = self._page_nav(pending, page, pages, "pp")
        if nav:
            rows.append(nav)
        rows.append([(ui(lang, "cancel"), payload(pending.id, "x"))])
        model = html.escape(str(data.get("current_model") or "?"))
        provider = html.escape(str(data.get("current_provider") or "?"))
        body = (f"<b>{ui(lang, 'picker_title')}</b>\n{ui(lang, 'current_model', model=model, provider=provider)}"
                f"\n\n{ui(lang, 'choose_provider')}")
        return body, rows

    def _model_page(self, pending, page: int):
        lang, data = self._language, pending.data
        provider = data["providers"][data["provider_index"]]
        models = [str(m) for m in provider.get("models") or []]
        pages = max(1, -(-len(models) // MODEL_PAGE))
        page = max(0, min(page, pages - 1))
        start = page * MODEL_PAGE
        rows = [[(m.split("/")[-1][:60], payload(pending.id, f"m{i}"))]
                for i, m in enumerate(models[start:start + MODEL_PAGE], start=start)]
        nav = self._page_nav(pending, page, pages, "mp")
        if nav:
            rows.append(nav)
        rows.append([(ui(lang, "back"), payload(pending.id, "back")), (ui(lang, "cancel"), payload(pending.id, "x"))])
        name = html.escape(str(provider.get("name") or provider.get("slug") or "?"))
        return f"<b>{ui(lang, 'picker_title')}</b>\n{ui(lang, 'choose_model', provider=name)}", rows

    # ── presses ─────────────────────────────────────────────────────────────────────────────────

    async def _answer(self, press: CallbackPress, *, body_html: str | None = None,
                      rows: list | None = None, notification: str | None = None) -> None:
        """Acknowledge a press; with ``body_html`` also replace the message (``rows`` = new keyboard)."""
        message = None
        if body_html is not None:
            message = {"text": body_html, "format": "html",
                       "attachments": [keyboard(rows)] if rows else []}
        try:
            await self._client.answer_callback(press.callback_id, message=message, notification=notification,
                                               chat_key=f"c{press.chat_id}")
        except MaxApiError as exc:
            if message is not None and exc.status == 400:
                # Refused markup: settle the press with plain text rather than leave it spinning.
                plain = {"text": html_to_plain(body_html), "attachments": [keyboard(rows)] if rows else []}
                try:
                    await self._client.answer_callback(press.callback_id, message=plain, notification=notification)
                    return
                except MaxApiError:
                    pass
            logger.warning("MAX: answering a button press failed: %s", exc.code or exc.status)

    async def _handle_press(self, press: CallbackPress) -> None:
        parsed = parse_payload(press.payload)
        if parsed is None:
            await self._answer(press)
            return
        prompt_id, choice = parsed
        chat_id = self._hermes_chat_id(press.chat_type, press.chat_id, press.user.user_id)
        prompt, reason = self._prompts.check(prompt_id, user_id=str(press.user.user_id), chat_id=chat_id,
                                             message_id=press.message_mid)
        if prompt is None:
            key = "not_yours" if reason in {"not the owner", "other chat", "no owner"} else "expired"
            logger.info("MAX: refused a button press (%s)", reason)
            await self._answer(press, notification=ui(self._language, key))
            return
        handler = {"approval": self._press_approval, "clarify": self._press_clarify,
                   "choice": self._press_choice, "model": self._press_model}.get(prompt.kind)
        if handler is None:
            await self._answer(press)
            return
        await handler(press, prompt, choice, chat_id)

    async def _press_approval(self, press, prompt, choice: str, chat_id: str) -> None:
        if choice not in {"once", "session", "always", "deny"}:
            await self._answer(press)
            return
        self._prompts.pop(prompt.id)
        try:
            from tools.approval import resolve_gateway_approval
            count = resolve_gateway_approval(prompt.session_key, choice)
        except Exception as exc:  # noqa: BLE001
            logger.error("MAX: resolving an approval failed: %s", exc)
            count = 0
        key = {"once": "approved_once", "session": "approved_session", "always": "approved_always",
               "deny": "denied"}[choice] if count else "approval_late"
        label = ui(self._language, key)
        name = html.escape(press.user.name or str(press.user.user_id))
        await self._answer(press, body_html=f"{prompt.data['text']}\n\n<b>{label}</b> — {name}")
        if count:
            self.resume_typing_for_chat(chat_id)

    async def _press_clarify(self, press, prompt, choice: str, chat_id: str) -> None:
        clarify_id = prompt.data["clarify_id"]
        if choice == "other":
            try:
                from tools.clarify_gateway import mark_awaiting_text
                flipped = mark_awaiting_text(clarify_id)
            except Exception:  # noqa: BLE001
                flipped = False
            self._prompts.pop(prompt.id)
            if not flipped:
                await self._answer(press, body_html=prompt.data["text"], notification=ui(self._language, "expired"))
                return
            hint = ui(self._language, "type_answer")
            await self._answer(press, body_html=f"{prompt.data['text']}\n\n<i>{hint}</i>", notification=hint)
            return
        try:
            index = int(choice)
            answer = prompt.data["choices"][index]
        except (ValueError, IndexError):
            await self._answer(press)
            return
        self._prompts.pop(prompt.id)
        try:
            from tools.clarify_gateway import resolve_gateway_clarify
            resolved = resolve_gateway_clarify(clarify_id, answer)
        except Exception as exc:  # noqa: BLE001
            logger.error("MAX: resolving a clarify prompt failed: %s", exc)
            resolved = False
        if not resolved:
            await self._answer(press, body_html=prompt.data["text"], notification=ui(self._language, "expired"))
            return
        done = ui(self._language, "answered", answer=html.escape(answer))
        await self._answer(press, body_html=f"{prompt.data['text']}\n\n<b>{done}</b>")

    async def _press_choice(self, press, prompt, choice: str, chat_id: str) -> None:
        try:
            picked = prompt.data["choices"][int(choice[1:])] if choice.startswith("c") else None
        except (ValueError, IndexError):
            picked = None
        if picked is None:
            await self._answer(press)
            return
        self._prompts.pop(prompt.id)
        try:
            result_text = await prompt.data["callback"](chat_id, str(picked.get("value") or ""))
        except Exception as exc:  # noqa: BLE001
            result_text = ui(self._language, "switch_failed", error=str(exc))
        await self._answer(press, body_html=html.escape(result_text or ""))

    async def _press_model(self, press, prompt, choice: str, chat_id: str) -> None:
        data = prompt.data
        try:
            if choice == "x":
                self._prompts.pop(prompt.id)
                await self._answer(press, body_html=html.escape(ui(self._language, "cancelled")))
            elif choice == "back":
                body, rows = self._provider_page(prompt, 0)
                await self._answer(press, body_html=body, rows=rows)
            elif choice.startswith("pp"):
                body, rows = self._provider_page(prompt, int(choice[2:]))
                await self._answer(press, body_html=body, rows=rows)
            elif choice.startswith("mp"):
                body, rows = self._model_page(prompt, int(choice[2:]))
                await self._answer(press, body_html=body, rows=rows)
            elif choice.startswith("p"):
                index = int(choice[1:])
                if not 0 <= index < len(data["providers"]):
                    raise IndexError
                data["provider_index"] = index
                body, rows = self._model_page(prompt, 0)
                await self._answer(press, body_html=body, rows=rows)
            elif choice.startswith("m"):
                provider = data["providers"][data["provider_index"]]
                model_id = str((provider.get("models") or [])[int(choice[1:])])
                self._prompts.pop(prompt.id)
                try:
                    result_text = await data["callback"](chat_id, model_id, str(provider.get("slug") or ""))
                except Exception as exc:  # noqa: BLE001
                    result_text = ui(self._language, "switch_failed", error=str(exc))
                await self._answer(press, body_html=html.escape(result_text or model_id))
            else:
                await self._answer(press)
        except (ValueError, IndexError, KeyError):
            await self._answer(press, notification=ui(self._language, "expired"))
