"""Yandex SpeechKit v1 synchronous recognition as a Hermes transcription provider.

``stt.provider: yandex_speechkit``. Needs ``YANDEX_SPEECHKIT_API_KEY`` (an API key of a service
account with the ``ai.speechkit-stt.user`` role); ``YANDEX_SPEECHKIT_FOLDER_ID`` is required for a
user-account key. Language: ``stt.yandex_speechkit.language`` (``ru``, ``en``, ``kk``... or a full
``ru-RU``), Russian by default.

Behaviour carried over from a production bot: raw OGG/Opus pieces of ≤ 29 s / 1 MB, three at a
time; a network error is retried up to three times (some routes to Yandex Cloud drop connections),
an HTTP error answer never is; after 401/402/403 the provider pauses for five minutes, because a
revoked key, a missing role or an unpaid cloud needs a person and every voice note in between would
only collect the same refusal.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from agent.transcription_provider import TranscriptionProvider

from ..maxapi.tls import ssl_context
from .common import SEGMENT_SECONDS, SttFailure, error, locale, ok, recognize_pieces, split_audio

logger = logging.getLogger(__name__)

URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
NAME = "yandex_speechkit"
MAX_PIECE_BYTES = 1024 * 1024
MAX_SECONDS = 600
PAUSE_SECONDS = 300


def _secret(name: str) -> str:
    from gateway.platforms._shared import get_scoped_secret

    return str(get_scoped_secret(name, "") or "").strip()


class YandexSpeechKitProvider(TranscriptionProvider):
    def __init__(self, *, client: httpx.Client | None = None):
        self._client = client
        self._paused_until = 0.0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return "Yandex SpeechKit"

    def is_available(self) -> bool:
        return bool(_secret("YANDEX_SPEECHKIT_API_KEY"))

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "general", "display": "SpeechKit v1 general"}]

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": self.display_name, "badge": "RU", "tag": "Yandex Cloud, billed per 15 s",
                "env_vars": [{"key": "YANDEX_SPEECHKIT_API_KEY", "prompt": "Yandex Cloud API key",
                              "url": "https://yandex.cloud/docs/speechkit/stt/request"},
                             {"key": "YANDEX_SPEECHKIT_FOLDER_ID", "prompt": "Folder id (user-account keys only)"}]}

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(verify=ssl_context(), timeout=httpx.Timeout(30.0, connect=10.0))
            return self._client

    def transcribe(self, file_path: str, *, model: str | None = None, language: str | None = None,
                   **extra: Any) -> dict[str, Any]:
        api_key = _secret("YANDEX_SPEECHKIT_API_KEY")
        if not api_key:
            return error("YANDEX_SPEECHKIT_API_KEY is not set", NAME)
        if time.monotonic() < self._paused_until:
            return error("Yandex SpeechKit refused the key a few minutes ago; check the key, role and billing", NAME)
        params = {"lang": locale(language), "format": "oggopus", "topic": model or "general"}
        folder = _secret("YANDEX_SPEECHKIT_FOLDER_ID")
        if folder:
            params["folderId"] = folder
        try:
            pieces = split_audio(file_path, max_piece_bytes=MAX_PIECE_BYTES)
            if not pieces:
                return error("no audio in the file", NAME)
            if (len(pieces) - 1) * SEGMENT_SECONDS >= MAX_SECONDS:
                return error(f"the recording is longer than {MAX_SECONDS // 60} minutes", NAME)
            text = recognize_pieces(pieces, lambda piece: self._recognize(piece, params, api_key))
        except SttFailure as exc:
            return error(str(exc), NAME)
        except Exception as exc:  # noqa: BLE001 - the provider contract forbids raising
            logger.exception("Yandex SpeechKit failed")
            return error(f"Yandex SpeechKit failed: {type(exc).__name__}", NAME)
        return ok(text, NAME) if text else error("no speech recognised", NAME)

    def _recognize(self, piece: bytes, params: dict[str, str], api_key: str) -> str:
        headers = {"Authorization": f"Api-Key {api_key}"}
        last: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                response = self._http().post(URL, params=params, content=piece, headers=headers,
                                             timeout=httpx.Timeout(60.0 if attempt == 3 else 20.0, connect=10.0))
                break
            except httpx.TransportError as exc:
                last = exc
                logger.warning("Yandex SpeechKit network error, attempt %d/3: %s", attempt, type(exc).__name__)
        else:
            raise SttFailure(f"Yandex SpeechKit is unreachable ({type(last).__name__})")
        if response.status_code in (401, 402, 403):
            self._paused_until = time.monotonic() + PAUSE_SECONDS
            raise SttFailure(f"Yandex SpeechKit refused the request (HTTP {response.status_code}): "
                             "check the API key, the ai.speechkit-stt.user role and the cloud's billing")
        if response.status_code >= 400:
            raise SttFailure(f"Yandex SpeechKit answered HTTP {response.status_code}")
        try:
            return str(response.json().get("result") or "")
        except ValueError as exc:
            raise SttFailure("Yandex SpeechKit answered with something that is not JSON") from exc
