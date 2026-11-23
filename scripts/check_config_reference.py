"""Fail when the README's settings table and the code disagree.

Every environment variable the code reads must be documented in README.md and README_RU.md, and
every variable the READMEs name must exist in the code — so the documentation cannot drift.

    python scripts/check_config_reference.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = ["settings.py", "__init__.py", "plugin.yaml", "stt/yandex_speechkit.py", "stt/sber_salutespeech.py",
        "outbound/tables.py", "stt/common.py"]
NAME = re.compile(r"\b((?:MAX|YANDEX_SPEECHKIT|SALUTESPEECH)_[A-Z0-9_]+|FFMPEG_PATH)\b")
IGNORED = {"MAX_BODY", "MAX_TEXT", "MAX_FILES", "MAX_PENDING", "MAX_SECONDS", "MAX_PIECE_BYTES",
           "MAX_MESSAGE_LENGTH", "MAX_POLL_TIMEOUT", "MAX_CELL_CHARS", "MAX_TEXT_LIMIT"}


def names(paths: list[str]) -> set[str]:
    found: set[str] = set()
    for rel in paths:
        found |= set(NAME.findall((ROOT / rel).read_text(encoding="utf-8")))
    return found - IGNORED


def main() -> int:
    in_code = names(CODE)
    problems = []
    for readme in ("README.md", "README_RU.md"):
        documented = names([readme])
        problems += [f"{readme}: {n} is read by the code but not documented" for n in sorted(in_code - documented)]
        problems += [f"{readme}: {n} is documented but the code never reads it" for n in sorted(documented - in_code)]
    for line in problems:
        print(line)
    print(f"{len(in_code)} settings checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
