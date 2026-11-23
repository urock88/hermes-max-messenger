"""Speech-to-text providers for every Hermes platform (not only MAX).

Hermes picks the provider named in ``stt.provider`` (config.yaml). Built in: ``local``
(faster-whisper), ``local_command`` (any command: whisper.cpp, Vosk...), ``groq``, ``openai``,
``mistral``, ``xai``, ``elevenlabs``, ``deepinfra``. This package adds ``yandex_speechkit`` and
``sber_salutespeech`` through Hermes' ``TranscriptionProvider`` interface; see ``docs/stt.md`` for
writing another one.
"""
