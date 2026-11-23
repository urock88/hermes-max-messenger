"""What every recognizer here shares: cutting audio, running pieces in parallel, the result envelope.

Synchronous recognition APIs take short pieces: SpeechKit v1 at most 30 s and 1 MB, SaluteSpeech
about a minute and 2 MB. ffmpeg cuts the recording into 29-second OGG/Opus pieces — without
re-encoding when it already is OGG/Opus (a voice note), otherwise mono Opus 32 kbit/s. 29 s is two
of SpeechKit's 15-second billing units. Hermes calls ``transcribe`` from a worker thread, so this
module is synchronous.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - ffmpeg with a fixed argument list, no shell
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SEGMENT_SECONDS = 29
FFMPEG_TIMEOUT = 120
# OGG/Opus voice is about 4 KB a second; used when we must guess a duration without ffmpeg.
OPUS_BYTES_PER_SECOND = 4000

# Two-letter language → the locale codes both Russian recognizers take.
LOCALES = {
    "ru": "ru-RU", "en": "en-US", "kk": "kk-KZ", "uz": "uz-UZ", "tr": "tr-TR", "de": "de-DE",
    "es": "es-ES", "fr": "fr-FR", "it": "it-IT", "pl": "pl-PL", "pt": "pt-PT", "fi": "fi-FI",
    "sv": "sv-SE", "nl": "nl-NL", "he": "he-IL", "ar": "ar-SA", "zh": "zh-CN", "ky": "ky-KG",
}


class SttFailure(Exception):
    """A recognition failure with a short reason that is safe to show."""


def ok(transcript: str, provider: str) -> dict[str, Any]:
    return {"success": True, "transcript": transcript, "provider": provider}


def error(message: str, provider: str) -> dict[str, Any]:
    return {"success": False, "transcript": "", "provider": provider, "error": message}


def locale(language: str | None, default: str = "ru-RU") -> str:
    if not language:
        return default
    language = language.strip()
    if "-" in language:
        head, _, tail = language.partition("-")
        return f"{head.lower()}-{tail.upper()}"
    return LOCALES.get(language.lower(), default)


def ffmpeg_binary() -> str | None:
    return shutil.which(os.environ.get("FFMPEG_PATH", "ffmpeg"))


def is_ogg_opus(data: bytes) -> bool:
    return data[:4] == b"OggS" and b"OpusHead" in data[:200]


def split_audio(path: str, *, max_piece_bytes: int) -> list[bytes]:
    """The recording as OGG/Opus pieces of at most 29 s, in order."""
    data = Path(path).read_bytes()
    binary = ffmpeg_binary()
    if binary is None:
        if is_ogg_opus(data) and len(data) <= max_piece_bytes:
            return [data]
        raise SttFailure("ffmpeg is not installed (needed for audio that is not a short OGG/Opus note)")
    codec = ["-c", "copy"] if is_ogg_opus(data) else ["-ac", "1", "-c:a", "libopus", "-b:a", "32k"]
    with tempfile.TemporaryDirectory(prefix="hermes-max-stt-") as tmp:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-map", "0:a", "-vn", *codec,
             "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_format", "ogg",
             "-reset_timestamps", "1", str(Path(tmp, "out%03d.ogg"))],
            capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)
        if result.returncode != 0:
            tail = " ".join(result.stderr.decode("utf-8", "replace").strip().splitlines()[-2:])
            raise SttFailure(f"ffmpeg failed: {tail[:200]}")
        pieces = [p.read_bytes() for p in sorted(Path(tmp).glob("out*.ogg"))]
    for piece in pieces:
        if len(piece) > max_piece_bytes:
            raise SttFailure(f"an audio piece of {len(piece)} bytes is over the {max_piece_bytes}-byte limit")
    return pieces


def recognize_pieces(pieces: list[bytes], recognize: Callable[[bytes], str], *, parallel: int = 3) -> str:
    """Run ``recognize`` over the pieces (``parallel`` at a time) and join the text in order."""
    if len(pieces) == 1:
        return recognize(pieces[0]).strip()
    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="hermes-max-stt") as pool:
        parts = list(pool.map(recognize, pieces))
    return " ".join(p.strip() for p in parts if p and p.strip())
