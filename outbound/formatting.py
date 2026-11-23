"""Model Markdown → the HTML subset MAX renders.

MAX's own Markdown has no escape syntax and no code fences, so ``snake_case_names``, ``2*3*4`` or a
fenced command come out mangled. Its HTML mode (``format: "html"``) has ``<b> <i> <s> <u> <code>
<pre> <a> <blockquote>`` and plain escaping, so every character the model wrote survives. Headings
become bold lines, lists keep a bullet, Markdown tables become an aligned ``<pre>`` block.

:func:`to_plain` is the fallback when MAX refuses the markup: the same text with the Markdown
markers removed.
"""

from __future__ import annotations

import html
import re
import unicodedata

_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

_INLINE_CODE = re.compile(r"(`+)(.+?)\1", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", re.S)
_ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", re.S)
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "max://", "tel:")
# Placeholders for fragments already turned into HTML (code spans, links) while the rest is escaped.
_STASH = "\x00{}\x00"
_STASH_RE = re.compile("\x00(\\d+)\x00")


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def table_rows(lines: list[str]) -> list[list[str]]:
    """Cells of a Markdown table, the separator line dropped and inline markers removed."""
    rows = [[_strip_inline(c) for c in _cells(line)] for line in lines if not _TABLE_SEPARATOR.match(line)]
    columns = max((len(r) for r in rows), default=0)
    return [r + [""] * (columns - len(r)) for r in rows]


def table_as_text(lines: list[str]) -> str:
    """A Markdown table as monospace columns (header, a rule, rows)."""
    rows = table_rows(lines)
    if not rows:
        return ""
    widths = [max(_width(r[i]) for r in rows) for i in range(len(rows[0]))]

    def render(row: list[str]) -> str:
        return "  ".join(cell + " " * (widths[i] - _width(cell)) for i, cell in enumerate(row)).rstrip()

    out = [render(rows[0]), "  ".join("─" * w for w in widths)]
    out.extend(render(r) for r in rows[1:])
    return "\n".join(out)


def _safe_url(url: str) -> str:
    return url if url.lower().startswith(_SAFE_SCHEMES) else ""


def _inline(text: str) -> str:
    """Inline Markdown of one line (or paragraph) → escaped HTML."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return _STASH.format(len(stash) - 1)

    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(2).strip())}</code>"), text)

    def link(m: re.Match[str], image: bool = False) -> str:
        label, url = m.group(1) or ("image" if image else ""), _safe_url(m.group(2))
        if not url:
            return keep(html.escape(m.group(0)))
        return keep(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')

    text = _IMAGE.sub(lambda m: link(m, image=True), text)
    text = _LINK.sub(link, text)
    text = html.escape(text, quote=False)
    text = _BOLD.sub(lambda m: f"<b>{m.group(2)}</b>", text)
    text = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _ITALIC_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _ITALIC_UNDERSCORE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    while _STASH_RE.search(text):
        text = _STASH_RE.sub(lambda m: stash[int(m.group(1))], text)
    return text


def _strip_inline(text: str) -> str:
    text = _INLINE_CODE.sub(lambda m: m.group(2).strip(), text)
    text = _IMAGE.sub(lambda m: f"{m.group(1) or 'image'} ({m.group(2)})", text)
    text = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", text)
    text = _BOLD.sub(lambda m: m.group(2), text)
    text = _STRIKE.sub(lambda m: m.group(1), text)
    text = _ITALIC_STAR.sub(lambda m: m.group(1), text)
    return _ITALIC_UNDERSCORE.sub(lambda m: m.group(1), text)


def _blocks(markdown: str):
    """Yield ``("code", lang, text)``, ``("table", "", lines)`` and ``("line", "", text)`` in order."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        fence = _FENCE.match(lines[i])
        if fence:
            marker, lang, body = fence.group(1), fence.group(2), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence (or the end of the text)
            yield "code", lang, "\n".join(body)
            continue
        if (_TABLE_ROW.match(lines[i]) and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1])):
            table = [lines[i], lines[i + 1]]
            i += 2
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                table.append(lines[i])
                i += 1
            yield "table", "", table
            continue
        yield "line", "", lines[i]
        i += 1


def to_max_html(markdown: str) -> str:
    """Markdown as the model writes it → MAX HTML (``format: "html"``)."""
    out: list[str] = []
    quote: list[str] = []

    def flush_quote() -> None:
        if quote:
            out.append(f"<blockquote>{chr(10).join(quote)}</blockquote>")
            quote.clear()

    for kind, _lang, value in _blocks(markdown or ""):
        if kind != "line" or not _QUOTE.match(value):
            flush_quote()
        if kind == "code":
            out.append(f"<pre>{html.escape(value)}</pre>")
        elif kind == "table":
            out.append(f"<pre>{html.escape(table_as_text(value))}</pre>")
        elif (m := _QUOTE.match(value)) is not None:
            quote.append(_inline(m.group(1)))
        elif (m := _HEADING.match(value)) is not None:
            out.append(f"<b>{_inline(m.group(2))}</b>")
        elif _RULE.match(value):
            out.append("──────────")
        elif (m := _BULLET.match(value)) is not None:
            indent = " " * (len(m.group(1).expandtabs(4)) // 2 * 2)
            out.append(f"{indent}• {_inline(m.group(2))}")
        else:
            out.append(_inline(value))
    flush_quote()
    return "\n".join(out).strip("\n")


def to_plain(markdown: str) -> str:
    """Markdown with its markers removed, for when MAX refuses the HTML."""
    out: list[str] = []
    for kind, _lang, value in _blocks(markdown or ""):
        if kind == "code":
            out.append(value)
        elif kind == "table":
            out.append(table_as_text(value))
        elif (m := _QUOTE.match(value)) is not None:
            out.append("│ " + _strip_inline(m.group(1)))
        elif (m := _HEADING.match(value)) is not None:
            out.append(_strip_inline(m.group(2)))
        elif (m := _BULLET.match(value)) is not None:
            out.append(f"{m.group(1)}• {_strip_inline(m.group(2))}")
        else:
            out.append(_strip_inline(value))
    return "\n".join(out).strip("\n")


def find_tables(markdown: str) -> list[list[str]]:
    """Every Markdown table in ``markdown`` as its source lines (for the PNG renderer)."""
    return [value for kind, _lang, value in _blocks(markdown or "") if kind == "table"]
