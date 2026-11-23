"""Build the animated architecture pictures for the README (``assets/architecture-{light,dark}.svg``).

GitHub shows README images without running scripts, but it plays SMIL animation inside an SVG, so
the picture animates by itself: packets run along the arrows in one 11-second loop, the hub's screen
and the caption change with each phase. The geometry is the wide layout of ``docs/diagram-config.js``
(the interactive version) — change both together.

    python scripts/build_architecture_svg.py
"""

from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CYCLE = 11.0
W, H = 960, 560

PALETTES = {
    "light": dict(page="#fbfcfe", grid="#e8ecf2", panel="#ffffff", edge="#c3cbd7", on="#2f6fed", text="#18202c",
                  muted="#5f6b7e", packet="#2f6fed", screen="#f1f4f9", badge="#18202c", badge_text="#ffffff",
                  client="#2f6fed", edge_g="#7a4cf0", core="#0b8f7d", data="#b86e00", ext="#cf2f68"),
    "dark": dict(page="#111821", grid="#1b2532", panel="#161f2b", edge="#3a4860", on="#6ea0ff", text="#e7edf5",
                 muted="#9aa8bb", packet="#9cc0ff", screen="#0f1620", badge="#e7edf5", badge_text="#0d1117",
                 client="#6ea0ff", edge_g="#a98bff", core="#3fd0b8", data="#f0a43a", ext="#ff6d9c"),
}

ICONS = {
    "phone": "M8 3h8a1.5 1.5 0 0 1 1.5 1.5v15A1.5 1.5 0 0 1 16 21H8a1.5 1.5 0 0 1-1.5-1.5v-15A1.5 1.5 0 0 1 8 3zM11 18h2",
    "cloud": "M7 18.5h10.5a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.4 9.3 4.6 4.6 0 0 0 7 18.5z",
    "queue": "M4 5.5h16v3.5H4zM4 10.25h16v3.5H4zM4 15h16v3.5H4z",
    "db": "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 "
          "3.1 3 7 3s7-1.3 7-3",
    "shield": "M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6zM9 12l2 2 4-4",
    "bot": "M7.5 7h9A2.5 2.5 0 0 1 19 9.5v6a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 5 15.5v-6A2.5 2.5 0 0 1 7.5 "
           "7zM12 7V4M9.4 12h.01M14.6 12h.01M9.5 15h5",
    "chat": "M4.5 5.5h15v10h-8.5l-4 3.5v-3.5h-2.5z",
    "send": "M3.5 11.5 20.5 4l-6 16.5-3.5-7z M11 13.5 20.5 4",
    "bolt": "M13 3 5 13.5h6L10 21l8-10.5h-6z",
}

# id: (title, sub, icon, group, x, y, w, h)
NODES = {
    "user": ("MAX app", "you, on a phone", "phone", "client", 24, 40, 170, 60),
    "maxapi": ("MAX Bot API", "platform-api2.max.ru", "cloud", "ext", 24, 210, 170, 100),
    "state": ("State", "marker · dedup", "db", "data", 242, 24, 170, 56),
    "transport": ("Transport", "long poll · webhook", "queue", "edge_g", 242, 120, 170, 60),
    "inbound": ("Inbound", "sender · media", "shield", "edge_g", 460, 120, 170, 60),
    "outbound": ("Outbound", "HTML · uploads", "send", "core", 460, 340, 170, 60),
    "limiter": ("Rate limiter", "30/s · 2/s per chat", "bolt", "data", 242, 340, 170, 60),
    "stt": ("Speech-to-text", "SpeechKit · Salute · Whisper", "chat", "ext", 690, 436, 246, 60),
}
HUB = (690, 80, 246, 300)

EDGES = {
    "in-user": ([(109, 100), (109, 210)], "both", False),
    "to-transport": ([(194, 240), (218, 240), (218, 150), (242, 150)], "end", False),
    "to-state": ([(327, 120), (327, 80)], "both", True),
    "to-inbound": ([(412, 150), (460, 150)], "end", False),
    "to-hermes": ([(630, 150), (690, 150)], "end", False),
    "to-outbound": ([(690, 370), (630, 370)], "end", False),
    "to-limiter": ([(460, 370), (412, 370)], "end", False),
    "to-max": ([(242, 370), (218, 370), (218, 280), (194, 280)], "end", False),
    "to-stt": ([(813, 380), (813, 436)], "both", False),
}

# (edge, start, end, reverse, small)
PACKETS = [
    ("in-user", 0.2, 1.0, False, False), ("to-transport", 1.0, 1.6, False, False),
    ("to-state", 1.6, 2.0, False, True), ("to-inbound", 1.9, 2.4, False, False),
    ("to-hermes", 2.4, 2.9, False, False),
    ("to-stt", 3.0, 3.5, False, False), ("to-stt", 3.7, 4.2, True, False),
    ("to-outbound", 4.5, 4.8, False, True), ("to-limiter", 4.8, 5.1, False, True),
    ("to-max", 5.1, 5.6, False, True), ("in-user", 5.6, 6.0, True, True),
    ("to-outbound", 7.0, 7.5, False, False), ("to-limiter", 7.5, 7.9, False, False),
    ("to-max", 7.9, 8.6, False, False), ("in-user", 8.6, 9.4, True, False),
]

PHASES = [  # (start, end, screen head, screen body, caption)
    (0.0, 2.9, "LONG POLL", "1 update · marker saved", "① A message arrives — long poll or webhook, its place saved first"),
    (2.9, 4.3, "VOICE 0:23", "yandex_speechkit → text", "② A voice note goes to the speech-to-text provider you chose"),
    (4.3, 7.0, "THINKING", "typing_on every 2 s", "③ While Hermes thinks, MAX shows “typing…”"),
    (7.0, CYCLE, "STREAMING", "edit 3 · ≤ 2 edits/s", "④ The answer streams back as edits, paced by the limiter"),
]


def _path(points) -> str:
    return "M" + " L".join(f"{x},{y}" for x, y in points)


def _window(start: float, end: float, fade: float = 0.15) -> tuple[str, str]:
    """``values``/``keyTimes`` for an opacity that is 1 between start and end, 0 elsewhere."""
    s, e = start / CYCLE, end / CYCLE
    f = fade / CYCLE
    times = [0.0, max(0.0, s - f), s, e, min(1.0, e + f), 1.0]
    for i in range(1, len(times)):
        times[i] = max(times[i], times[i - 1])
    values = ["0", "0", "1", "1", "0", "0"]
    if s <= 0:
        times, values = [0.0, e, min(1.0, e + f), 1.0], ["1", "1", "0", "0"]
    if e >= 1:
        times, values = [0.0, max(0.0, s - f), s, 1.0], ["0", "0", "1", "1"]
    return ";".join(values), ";".join(f"{t:.4f}" for t in times)


def _fade(start: float, end: float) -> str:
    values, times = _window(start, end)
    return (f'<animate attributeName="opacity" dur="{CYCLE}s" repeatCount="indefinite" '
            f'values="{values}" keyTimes="{times}"/>')


def _arrow(points, color: str) -> str:
    (x1, y1), (x2, y2) = points[-2], points[-1]
    dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
    bx, by = x2 - 9 * dx, y2 - 9 * dy
    px, py = -dy * 5, dx * 5
    return f'<path d="M{x2},{y2} L{bx + px},{by + py} L{bx - px},{by - py}z" fill="{color}"/>'


def _node(c, title, sub, icon, group, x, y, w, h) -> str:
    hue = c[group]
    cy = y + h / 2
    return (f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{c["panel"]}" stroke="{hue}" '
            f'stroke-opacity=".75" stroke-width="1.5"/>'
            f'<path d="{ICONS[icon]}" transform="translate({x + 14},{cy - 12})" fill="none" stroke="{hue}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<text x="{x + 50}" y="{cy - 3}" font-size="15" font-weight="600" fill="{c["text"]}">{escape(title)}</text>'
            f'<text x="{x + 50}" y="{cy + 15}" font-size="11.5" fill="{c["muted"]}">{escape(sub)}</text></g>')


def build(theme: str) -> str:
    c = PALETTES[theme]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" role="img" '
           f'aria-label="hermes-max-messenger architecture: MAX app, MAX Bot API, transport, inbound, Hermes Agent, '
           f'speech-to-text, outbound, rate limiter">',
           f'<rect width="{W}" height="{H}" rx="14" fill="{c["page"]}"/>']
    out += [f'<path d="M{x},16 V{H - 16}" stroke="{c["grid"]}"/>' for x in range(48, W, 48)]
    out += [f'<path d="M16,{y} H{W - 16}" stroke="{c["grid"]}"/>' for y in range(48, H - 40, 48)]
    for points, arrows, dashed in EDGES.values():
        dash = ' stroke-dasharray="6 6"' if dashed else ""
        out.append(f'<path d="{_path(points)}" fill="none" stroke="{c["edge"]}" stroke-width="2"{dash}/>')
        out.append(_arrow(points, c["edge"]))
        if arrows == "both":
            out.append(_arrow(points[::-1], c["edge"]))
    for name, start, end, _reverse, _small in PACKETS:  # the edge glows while a packet runs on it
        points = EDGES[name][0]
        out.append(f'<path d="{_path(points)}" fill="none" stroke="{c["on"]}" stroke-width="2.5" opacity="0">'
                   f'{_fade(start - 0.05, end + 0.25)}</path>')
    for spec in NODES.values():
        out.append(_node(c, *spec))
    x, y, w, h = HUB
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{c["panel"]}" stroke="{c["core"]}" '
               f'stroke-width="2"/>')
    out.append(f'<path d="{ICONS["bot"]}" transform="translate({x + 16},{y + 14})" fill="none" stroke="{c["core"]}" '
               f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')
    out.append(f'<text x="{x + 50}" y="{y + 32}" font-size="16" font-weight="700" fill="{c["text"]}">Hermes Agent</text>')
    chips = [("pair", 0.0, 2.9), ("think", 4.3, 7.0), ("reply", 7.0, CYCLE)]
    for i, (label, start, end) in enumerate(chips):
        cx = x + 16 + i * 74
        out.append(f'<rect x="{cx}" y="{y + 52}" width="64" height="24" rx="12" fill="{c["panel"]}" '
                   f'stroke="{c["edge"]}"/>')
        out.append(f'<rect x="{cx}" y="{y + 52}" width="64" height="24" rx="12" fill="{c["core"]}" '
                   f'fill-opacity=".18" stroke="{c["core"]}" opacity="0">{_fade(start, end)}</rect>')
        out.append(f'<text x="{cx + 32}" y="{y + 68}" font-size="12" text-anchor="middle" fill="{c["text"]}">'
                   f'{label}</text>')
    sx, sy, sw, sh = x + 16, y + 92, w - 32, h - 108
    out.append(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="6" fill="{c["screen"]}" stroke="{c["grid"]}"/>')
    mono = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
    for start, end, head, body, caption in PHASES:
        out.append(f'<g opacity="0">{_fade(start, end)}'
                   f'<text x="{sx + 14}" y="{sy + 26}" font-family="{mono}" font-size="11.5" font-weight="600" '
                   f'letter-spacing=".06em" fill="{c["muted"]}">{escape(head)}</text>'
                   f'<text x="{sx + 14}" y="{sy + 52}" font-family="{mono}" font-size="13.5" fill="{c["text"]}">'
                   f'{escape(body)}</text></g>')
        out.append(f'<text x="{W / 2}" y="{H - 22}" font-size="15" text-anchor="middle" fill="{c["text"]}" opacity="0">'
                   f'{_fade(start, end)}{escape(caption)}</text>')
    for name, start, end, reverse, small in PACKETS:
        points = EDGES[name][0]
        path = _path(points[::-1] if reverse else points)
        s, e = start / CYCLE, end / CYCLE
        out.append(f'<circle r="{4 if small else 6}" fill="{c["packet"]}" opacity="0">'
                   f'<animateMotion dur="{CYCLE}s" repeatCount="indefinite" path="{path}" calcMode="linear" '
                   f'keyPoints="0;0;1;1" keyTimes="0;{s:.4f};{e:.4f};1"/>{_fade(start, end, )}</circle>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    for theme in PALETTES:
        target = ROOT / "assets" / f"architecture-{theme}.svg"
        target.write_text(build(theme), encoding="utf-8", newline="\n")
        print("written", target.relative_to(ROOT), target.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
