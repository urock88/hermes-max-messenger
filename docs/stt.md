# Speech-to-text for voice notes

When a voice note arrives, the MAX adapter downloads it and marks the event as voice. Hermes core then
transcribes it with the provider named in `stt.provider` and hands the agent the text. The choice is global:
the same provider serves Telegram, WhatsApp, MAX and every other platform.

## The options

| `stt.provider` | Where it runs | Needs | Good for |
|---|---|---|---|
| `yandex_speechkit` *(this plugin)* | Yandex Cloud | `YANDEX_SPEECHKIT_API_KEY`, optional `YANDEX_SPEECHKIT_FOLDER_ID` | Russian and CIS languages, small servers |
| `sber_salutespeech` *(this plugin)* | Sber | `SALUTESPEECH_AUTH_KEY`, optional `SALUTESPEECH_SCOPE` | Russian; for companies and self-employed |
| `local` *(Hermes)* | your server | faster-whisper (installed on demand) | privacy, no per-minute bill; needs CPU/RAM |
| `local_command` *(Hermes)* | your server | `HERMES_LOCAL_STT_COMMAND` or a whisper binary | whisper.cpp, Vosk, any CLI |
| `groq`, `openai`, `mistral`, `xai`, `elevenlabs`, `deepinfra` *(Hermes)* | their cloud | their API key | multilingual, fast |
| any name under `stt.providers.<name>` with `type: command` *(Hermes)* | anywhere | a shell command | a script or `curl` pipeline of your own |

## Yandex SpeechKit

1. In the Yandex Cloud console create a service account with the role `ai.speechkit-stt.user` and an API key
   for it.
2. Put the key on the Hermes server:
   ```bash
   YANDEX_SPEECHKIT_API_KEY=AQVN...
   # only for a key of a user account, not a service account:
   YANDEX_SPEECHKIT_FOLDER_ID=b1g...
   ```
3. Select it:
   ```yaml
   stt:
     provider: yandex_speechkit
     yandex_speechkit:
       language: ru          # ru, en, kk, uz, tr, de, fr, es, it, pl … or ru-RU
   ```

It uses the synchronous recognition API (v1): every request carries at most 30 seconds and 1 MB of audio, so
the plugin cuts a recording into 29-second pieces with `ffmpeg` (copying the stream, no re-encoding, for
OGG/Opus) and recognises three at a time. A recording longer than 10 minutes is refused with a message. On
HTTP 401, 402 or 403 (bad key, no money, no role) the provider stops calling Yandex for five minutes instead of
failing every voice note the same way. Network errors are retried three times.

## Sber SaluteSpeech

1. In the SaluteSpeech studio create a project and copy its **authorization key** (Base64 of
   `client_id:client_secret`).
2. Put it on the server:
   ```bash
   SALUTESPEECH_AUTH_KEY=MDE5...
   SALUTESPEECH_SCOPE=SALUTE_SPEECH_CORP   # SALUTE_SPEECH_PERS (default) / _CORP / _B2B
   ```
3. Select it: `stt.provider: sber_salutespeech` (language under `stt.sber_salutespeech.language`).

The provider gets an access token (valid 30 minutes) and renews it a minute before it expires or after a 401.
Since July 2026 Sber sells recognition only to legal entities and the self-employed; a personal key answers
HTTP 402 and the provider says so. Sber's own servers use the Russian Trusted Root CA, which the plugin adds to
the standard trust store; certificate checks stay on.

## Fully offline

Nothing leaves your server with Hermes' own providers:

```yaml
stt:
  provider: local          # faster-whisper; model under stt.local.model (base, small, medium …)
```

or, for whisper.cpp / Vosk / anything with a command line:

```yaml
stt:
  provider: my_whisper
  providers:
    my_whisper:
      type: command
      command: "whisper-cli -m /opt/models/ggml-small.bin -l {language} -f {input_path} -otxt -of {output_dir}/transcript"
      format: txt
      normalize: true      # convert to 16 kHz mono first
      timeout: 300
```

Placeholders: `{input_path}`, `{output_path}`, `{output_dir}`, `{format}`, `{language}`, `{model}`. The
transcript is read from the output file, or from stdout when there is no file.

## `ffmpeg`

Both plugin providers need `ffmpeg` for anything but a short OGG/Opus voice note (MAX voice notes are OGG/Opus,
but a forwarded MP3 or a long recording is not). Install it with the package manager (`apt install ffmpeg`), or
point `FFMPEG_PATH` at the binary.

## Writing a provider of your own

A provider is a class registered from any Hermes plugin:

```python
from agent.transcription_provider import TranscriptionProvider

class MyProvider(TranscriptionProvider):
    @property
    def name(self) -> str:
        return "my_provider"           # the value of stt.provider

    def is_available(self) -> bool:
        return True                    # e.g. "the API key is set"

    def transcribe(self, file_path, *, model=None, language=None, **extra):
        try:
            text = ...                 # call your recognizer
            return {"success": True, "transcript": text, "provider": self.name}
        except Exception as exc:       # never raise: Hermes expects an envelope
            return {"success": False, "transcript": "", "provider": self.name, "error": str(exc)}

def register(ctx):
    ctx.register_transcription_provider(MyProvider())
```

`transcribe` is synchronous and must never raise. `stt/yandex_speechkit.py` in this repository is
a complete example with piece cutting, parallel requests and back-off.
