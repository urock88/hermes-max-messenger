"""The plugin's own UI words, in Russian (default) and English (``MAX_LANGUAGE=en``)."""

from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "other": "✏️ Свой ответ",
        "type_answer": "Напишите ответ сообщением",
        "answered": "Ответ: {answer}",
        "expired": "Кнопка устарела",
        "not_yours": "Эта кнопка не для вас",
        "approved_once": "✅ Разрешено один раз",
        "approved_session": "✅ Разрешено до конца сессии",
        "approved_always": "✅ Разрешено всегда",
        "denied": "❌ Отклонено",
        "approval_late": "⌛ Время ответа вышло — команда не выполнена",
        "cancel": "Отмена",
        "cancelled": "Отменено",
        "picker_title": "⚙️ Модель",
        "current_model": "Сейчас: {model} ({provider})",
        "choose_provider": "Выберите провайдера:",
        "choose_model": "{provider}: выберите модель",
        "back": "← Назад",
        "prev": "‹",
        "next": "›",
        "switch_failed": "Не удалось переключить: {error}",
    },
    "en": {
        "other": "✏️ Other",
        "type_answer": "Type your answer as a message",
        "answered": "Answer: {answer}",
        "expired": "This button has expired",
        "not_yours": "This button is not for you",
        "approved_once": "✅ Allowed once",
        "approved_session": "✅ Allowed for this session",
        "approved_always": "✅ Always allowed",
        "denied": "❌ Denied",
        "approval_late": "⌛ Too late — the command was not run",
        "cancel": "Cancel",
        "cancelled": "Cancelled",
        "picker_title": "⚙️ Model",
        "current_model": "Now: {model} ({provider})",
        "choose_provider": "Choose a provider:",
        "choose_model": "{provider}: choose a model",
        "back": "← Back",
        "prev": "‹",
        "next": "›",
        "switch_failed": "Could not switch: {error}",
    },
}


def text(language: str, key: str, **values: str) -> str:
    table = TEXTS.get((language or "ru")[:2].lower(), TEXTS["ru"])
    return table.get(key, TEXTS["en"].get(key, key)).format(**values)
