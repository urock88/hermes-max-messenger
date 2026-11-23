"""Incoming attachments: download, cache, describe.

Only an authorized sender's attachments are downloaded (decided by the adapter before calling here):
a stranger who is shown a pairing code must not be able to make the server fetch anything. The
download goes through Hermes' SSRF-safe client (connect-time IP checks, every redirect re-checked)
and never carries the bot token — MAX media URLs are signed on their own. Files land in Hermes'
media cache, where the agent's tools can read them.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass, field

from ..maxapi.models import Attachment
from ..maxapi.tls import ssl_context

logger = logging.getLogger(__name__)

PER_FILE_LIMIT = 50 * 1024 * 1024
PER_MESSAGE_LIMIT = 100 * 1024 * 1024
MAX_FILES = 10
DOWNLOAD_TIMEOUT = 60.0

_DEFAULT_KIND = {"image": "image", "sticker": "image", "audio": "audio", "video": "video", "file": None}
_DEFAULT_EXT = {"image": ".jpg", "sticker": ".webp", "audio": ".ogg", "video": ".mp4"}


@dataclass
class InboundMedia:
    paths: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # lines added to the text the agent sees
    has_voice: bool = False
    has_image: bool = False

    @property
    def kinds(self) -> set[str]:
        return {m.split("/", 1)[0] for m in self.mime_types}


def describe(attachment: Attachment) -> str | None:
    """A text line for an attachment that is not a file (contact, location, link, sticker)."""
    payload = attachment.payload
    if attachment.kind == "contact":
        name = str(payload.get("name") or (payload.get("max_info") or {}).get("name") or "").strip()
        phone = ""
        for line in str(payload.get("vcf_info") or "").splitlines():
            if line.upper().startswith("TEL"):
                phone = line.split(":", 1)[-1].strip()
                break
        return f"[contact: {' '.join(p for p in (name, phone) if p) or 'shared'}]"
    if attachment.kind == "location":
        return f"[location: {payload.get('latitude')}, {payload.get('longitude')}]"
    if attachment.kind == "share":
        return f"[link: {payload.get('url') or attachment.url}]"
    if attachment.kind == "sticker" and not attachment.url:
        return "[sticker]"
    if attachment.kind in {"image", "video", "audio", "file"} and not attachment.url:
        return f"[{attachment.kind} without a download link]"
    return None


def _filename(attachment: Attachment, mime: str) -> str:
    if attachment.filename:
        return attachment.filename
    ext = mimetypes.guess_extension(mime.split(";")[0].strip()) if mime else None
    return f"{attachment.kind}{ext or _DEFAULT_EXT.get(attachment.kind, '.bin')}"


async def _fetch(client, url: str, limit: int) -> tuple[bytes, str]:
    async with client.stream("GET", url) as response:
        if response.status_code >= 400:
            raise ValueError(f"HTTP {response.status_code}")
        declared = int(response.headers.get("content-length") or 0)
        if declared and declared > limit:
            raise ValueError("too large")
        chunks, total = [], 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise ValueError("too large")
            chunks.append(chunk)
        return b"".join(chunks), (response.headers.get("content-type") or "").split(";")[0].strip()


async def download_all(attachments: tuple[Attachment, ...], *, client=None) -> InboundMedia:
    """Download every media attachment (in order) and describe the rest."""
    from gateway.platforms.base import cache_media_bytes_async, get_inbound_media_max_bytes
    from gateway.platforms.helpers import platform_http_client

    result = InboundMedia()
    core_cap = get_inbound_media_max_bytes()
    per_file = min(PER_FILE_LIMIT, core_cap) if core_cap > 0 else PER_FILE_LIMIT
    budget = PER_MESSAGE_LIMIT
    own_client = client is None
    if own_client:
        client = platform_http_client(timeout=DOWNLOAD_TIMEOUT, verify=ssl_context())
    try:
        for attachment in attachments:
            note = describe(attachment)
            if note:
                result.notes.append(note)
            if not attachment.is_media:
                continue
            if len(result.paths) >= MAX_FILES:
                result.notes.append(f"[{attachment.kind} skipped: more than {MAX_FILES} attachments]")
                continue
            try:
                data, mime = await asyncio.wait_for(_fetch(client, attachment.url, min(per_file, budget)),
                                                    DOWNLOAD_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - one bad file must not lose the message
                logger.warning("MAX: could not download a %s attachment: %s", attachment.kind, type(exc).__name__)
                result.notes.append(f"[{attachment.kind} could not be downloaded]")
                continue
            budget -= len(data)
            filename = _filename(attachment, mime)
            cached = await cache_media_bytes_async(data, filename=filename if attachment.filename else "",
                                                   mime_type=mime, default_kind=_DEFAULT_KIND.get(attachment.kind))
            if cached is None:
                result.notes.append(f"[{attachment.kind} could not be read]")
                continue
            result.paths.append(cached.path)
            result.mime_types.append(cached.media_type)
            result.filenames.append(cached.display_name)
            if cached.kind == "audio" and attachment.kind == "audio":
                result.has_voice = True
            if cached.kind == "image":
                result.has_image = True
    finally:
        if own_client:
            await client.aclose()
    return result
