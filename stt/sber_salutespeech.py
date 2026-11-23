"""Sber SaluteSpeech synchronous recognition as a Hermes transcription provider.

``stt.provider: sber_salutespeech``. Needs ``SALUTESPEECH_AUTH_KEY`` — the authorization key from the
SaluteSpeech project (Base64 of ``client_id:client_secret``). ``SALUTESPEECH_SCOPE`` is
``SALUTE_SPEECH_PERS`` for individuals (default), ``SALUTE_SPEECH_CORP`` or ``SALUTE_SPEECH_B2B`` for
companies. The access token (30 minutes) is cached and refreshed a minute before it expires, and
once more after a 401. Sber's hosts use the Russian Trusted Root CA like MAX.

Note: since July 2026 Sber sells no SaluteSpeech packages to individuals; such keys answer HTTP 402.
It works for legal entities and self-employed accounts.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import httpx
from agent.transcription_provider import TranscriptionProvider

from ..maxapi.tls import ssl_context
from .common import SEGMENT_SECONDS, SttFailure, error, locale, ok, recognize_pieces, split_audio

logger = logging.getLogger(__name__)

NAME = "sber_salutespeech"
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
RECOGNIZE_URL = "https://smartspeech.sber.ru/rest/v1/speech:recognize"
DEFAULT_SCOPE = "SALUTE_SPEECH_PERS"
MAX_PIECE_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 600
REFRESH_MARGIN = 60.0


def _secret(name: str) -> str:
    from gateway.platforms._shared import get_scoped_secret

    return str(get_scoped_secret(name, "") or "").strip()


class SberSaluteSpeechProvider(TranscriptionProvider):
    def __init__(self, *, client: httpx.Client | None = None, clock=time.time):
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._token: tuple[str, str, float] | None = None  # (auth key + scope, access token, expires at)

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return "Sber SaluteSpeech"

    def is_available(self) -> bool:
        return bool(_secret("SALUTESPEECH_AUTH_KEY"))

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "general", "display": "SaluteSpeech general"},
                {"id": "callcenter", "display": "SaluteSpeech call centre"}]

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": self.display_name, "badge": "RU", "tag": "Sber, legal entities and self-employed",
                "env_vars": [{"key": "SALUTESPEECH_AUTH_KEY", "prompt": "SaluteSpeech authorization key",
                              "url": "https://developers.sber.ru/docs/ru/salutespeech/overview"},
                             {"key": "SALUTESPEECH_SCOPE", "prompt": "Scope (SALUTE_SPEECH_PERS / _CORP / _B2B)"}]}

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(verify=ssl_context(), timeout=httpx.Timeout(45.0, connect=10.0))
            return self._client

    def _access_token(self, auth_key: str, scope: str, *, force: bool = False) -> str:
        identity = f"{scope}:{auth_key}"
        with self._lock:
            cached = self._token
            if not force and cached and cached[0] == identity and cached[2] - REFRESH_MARGIN > self._clock():
                return cached[1]
        response = self._http().post(
            OAUTH_URL, data={"scope": scope},
            headers={"Authorization": f"Basic {auth_key}", "RqUID": str(uuid.uuid4()), "Accept": "application/json"},
            timeout=httpx.Timeout(15.0, connect=10.0))
        if response.status_code >= 400:
            raise SttFailure(f"SaluteSpeech refused the authorization key (HTTP {response.status_code})")
        body = response.json()
        token, expires_ms = body.get("access_token"), body.get("expires_at")
        if not token or not isinstance(expires_ms, (int, float)):
            raise SttFailure("SaluteSpeech OAuth answered without a token")
        with self._lock:
            self._token = (identity, str(token), float(expires_ms) / 1000.0)
        return str(token)

    def transcribe(self, file_path: str, *, model: str | None = None, language: str | None = None,
                   **extra: Any) -> dict[str, Any]:
        auth_key = _secret("SALUTESPEECH_AUTH_KEY")
        if not auth_key:
            return error("SALUTESPEECH_AUTH_KEY is not set", NAME)
        scope = _secret("SALUTESPEECH_SCOPE") or DEFAULT_SCOPE
        params = {"language": locale(language), "model": model or "general"}
        try:
            pieces = split_audio(file_path, max_piece_bytes=MAX_PIECE_BYTES)
            if not pieces:
                return error("no audio in the file", NAME)
            if (len(pieces) - 1) * SEGMENT_SECONDS >= MAX_SECONDS:
                return error(f"the recording is longer than {MAX_SECONDS // 60} minutes", NAME)
            self._access_token(auth_key, scope)
            text = recognize_pieces(pieces, lambda piece: self._recognize(piece, params, auth_key, scope))
        except SttFailure as exc:
            return error(str(exc), NAME)
        except Exception as exc:  # noqa: BLE001 - the provider contract forbids raising
            logger.exception("SaluteSpeech failed")
            return error(f"SaluteSpeech failed: {type(exc).__name__}", NAME)
        return ok(text, NAME) if text else error("no speech recognised", NAME)

    def _recognize(self, piece: bytes, params: dict[str, str], auth_key: str, scope: str) -> str:
        for attempt in (1, 2):
            token = self._access_token(auth_key, scope, force=attempt == 2)
            try:
                response = self._http().post(
                    RECOGNIZE_URL, params=params, content=piece,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/ogg;codecs=opus",
                             "Accept": "application/json"})
            except httpx.TransportError as exc:
                raise SttFailure(f"SaluteSpeech is unreachable ({type(exc).__name__})") from exc
            if response.status_code == 401 and attempt == 1:
                continue
            if response.status_code == 402:
                raise SttFailure("SaluteSpeech answered 402: no paid package for this account")
            if response.status_code >= 400:
                raise SttFailure(f"SaluteSpeech answered HTTP {response.status_code}")
            result = response.json().get("result")
            if isinstance(result, list):
                return " ".join(str(r) for r in result if isinstance(r, str))
            return str(result or "")
        raise SttFailure("SaluteSpeech kept refusing the access token")
