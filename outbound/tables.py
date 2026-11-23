"""Markdown tables as PNG pictures (``MAX_TABLE_AS_IMAGE=true``).

Pillow only — no browser. A table needs a monospace TrueType font with Cyrillic; when none of the
usual ones is installed :func:`render_table_png` returns None and the table stays text (the
``<pre>`` columns of :mod:`.formatting`). Install ``fonts-dejavu-core`` on a minimal Linux server.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

from .formatting import table_rows

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
)
_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "C:/Windows/Fonts/consolab.ttf",
)
FONT_SIZE = 26
PAD_X, PAD_Y, GAP = 22, 14, 34
MAX_CELL_CHARS = 48


def _first_font(candidates: tuple[str, ...]) -> str | None:
    override = os.environ.get("MAX_TABLE_FONT")
    if override and Path(override).is_file():
        return override
    return next((c for c in candidates if Path(c).is_file()), None)


@lru_cache(maxsize=1)
def _fonts():
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    regular = _first_font(_FONT_CANDIDATES)
    if not regular:
        logger.info("MAX: no monospace TTF font found; tables stay text (set MAX_TABLE_FONT)")
        return None
    bold = _first_font(_BOLD_CANDIDATES) or regular
    return ImageFont.truetype(regular, FONT_SIZE), ImageFont.truetype(bold, FONT_SIZE)


def available() -> bool:
    return _fonts() is not None


def _clip(cell: str) -> str:
    return cell if len(cell) <= MAX_CELL_CHARS else cell[:MAX_CELL_CHARS - 1] + "…"


def render_table_png(lines: list[str], out_dir: Path) -> Path | None:
    """Draw one Markdown table; returns the PNG path (cached by content) or None."""
    fonts = _fonts()
    rows = [[_clip(c) for c in r] for r in table_rows(lines)]
    if fonts is None or not rows:
        return None
    from PIL import Image, ImageDraw

    regular, bold = fonts
    digest = hashlib.sha256("\n".join("\t".join(r) for r in rows).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"table-{digest}.png"
    if target.is_file():
        return target

    def text_width(text: str, font) -> int:
        return int(font.getlength(text)) if text else 0

    columns = len(rows[0])
    widths = [max(text_width(r[i], bold if n == 0 else regular) for n, r in enumerate(rows)) for i in range(columns)]
    line_height = int(FONT_SIZE * 1.6)
    width = sum(widths) + GAP * (columns - 1) + 2 * PAD_X
    height = line_height * len(rows) + 2 * PAD_Y
    image = Image.new("RGB", (max(width, 120), height), "white")
    draw = ImageDraw.Draw(image)
    for n, row in enumerate(rows):
        y = PAD_Y + n * line_height
        if n % 2 == 1:
            draw.rectangle([0, y - 2, image.width, y + line_height - 4], fill="#f3f5f8")
        x = PAD_X
        for i, cell in enumerate(row):
            draw.text((x, y + 4), cell, fill="#14181f", font=bold if n == 0 else regular)
            x += widths[i] + GAP
        if n == 0:
            draw.line([PAD_X, y + line_height - 3, image.width - PAD_X, y + line_height - 3], fill="#9aa4b2", width=2)
    image.save(target, "PNG", optimize=True)
    return target
