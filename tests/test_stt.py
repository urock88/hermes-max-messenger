"""The two speech-to-text providers, with fake HTTP and a short OGG/Opus note (no ffmpeg needed)."""

from __future__ import annotations

import httpx
import pytest
from max_messenger.stt.common import locale
from max_messenger.stt.sber_salutespeech import SberSaluteSpeechProvider
from max_messenger.stt.yandex_speechkit import YandexSpeechKitProvider

NOTE = b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x00" * 200


@pytest.fixture
def note(tmp_path):
    path = tmp_path / "voice.ogg"
    path.write_bytes(NOTE)
    return str(path)


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_locale():
    assert locale(None) == "ru-RU"
    assert locale("en") == "en-US"
    assert locale("kk-kz") == "kk-KZ"


def test_speechkit_recognizes(note, monkeypatch):
    monkeypatch.setenv("YANDEX_SPEECHKIT_API_KEY", "key-1")
    monkeypatch.setenv("YANDEX_SPEECHKIT_FOLDER_ID", "folder-1")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": "привет мир"})

    provider = YandexSpeechKitProvider(client=client(handler))
    assert provider.is_available()
    result = provider.transcribe(note, language="ru")
    assert result == {"success": True, "transcript": "привет мир", "provider": "yandex_speechkit"}
    request = seen[0]
    assert request.headers["authorization"] == "Api-Key key-1"
    assert request.url.params["lang"] == "ru-RU" and request.url.params["folderId"] == "folder-1"
    assert request.url.params["format"] == "oggopus" and request.content == NOTE


def test_speechkit_pauses_after_a_refused_key(note, monkeypatch):
    monkeypatch.setenv("YANDEX_SPEECHKIT_API_KEY", "bad")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error_code": "UNAUTHORIZED"})

    provider = YandexSpeechKitProvider(client=client(handler))
    first, second = provider.transcribe(note), provider.transcribe(note)
    assert not first["success"] and "ai.speechkit-stt.user" in first["error"]
    assert not second["success"] and len(calls) == 1  # no second request while paused


def test_speechkit_without_a_key(note, monkeypatch):
    monkeypatch.delenv("YANDEX_SPEECHKIT_API_KEY", raising=False)
    provider = YandexSpeechKitProvider(client=client(lambda r: httpx.Response(500)))
    assert not provider.is_available()
    assert provider.transcribe(note)["error"] == "YANDEX_SPEECHKIT_API_KEY is not set"


def test_salutespeech_caches_the_token_and_refreshes_after_401(note, monkeypatch):
    monkeypatch.setenv("SALUTESPEECH_AUTH_KEY", "base64key")
    monkeypatch.delenv("SALUTESPEECH_SCOPE", raising=False)
    oauth, recognize = [], []
    answers = iter([401, 200, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in request.url.path:
            oauth.append(request)
            return httpx.Response(200, json={"access_token": f"tok{len(oauth)}", "expires_at": 4_000_000_000_000})
        recognize.append(request)
        status = next(answers)
        return httpx.Response(status, json={"result": ["раз", "два"]} if status == 200 else {})

    provider = SberSaluteSpeechProvider(client=client(handler))
    assert provider.transcribe(note, language="ru")["transcript"] == "раз два"
    assert provider.transcribe(note)["success"]
    assert len(oauth) == 2  # the first token, then a forced refresh after the 401; the third call reuses it
    assert oauth[0].headers["authorization"] == "Basic base64key" and b"SALUTE_SPEECH_PERS" in oauth[0].content
    assert recognize[-1].headers["authorization"] == "Bearer tok2"
    assert recognize[0].headers["content-type"] == "audio/ogg;codecs=opus"


def test_salutespeech_explains_402(note, monkeypatch):
    monkeypatch.setenv("SALUTESPEECH_AUTH_KEY", "k")

    def handler(request):
        if "oauth" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_at": 4_000_000_000_000})
        return httpx.Response(402, json={})

    result = SberSaluteSpeechProvider(client=client(handler)).transcribe(note)
    assert not result["success"] and "402" in result["error"]


def test_providers_are_hermes_transcription_providers():
    from agent.transcription_provider import TranscriptionProvider

    for provider in (YandexSpeechKitProvider(), SberSaluteSpeechProvider()):
        assert isinstance(provider, TranscriptionProvider)
        assert provider.get_setup_schema()["env_vars"]
