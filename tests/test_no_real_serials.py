"""No real inverter serial number or private address may be tracked by git.

This repository is public. A serial identifies an installation, and most of
the serials that pass through a Modbus project belong to other people. The
test matches the *shape* of a Sungrow serial (a letter followed by ten digits)
in every tracked file and requires each hit to be on the allow-list of
invented values. Add to the list deliberately, never with a real one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INVENTED_SERIALS = frozenset({"A123456789", "A987654321"})
# Documentation examples only; a real LAN address never belongs here.
ALLOWED_ADDRESSES = frozenset({"127.0.0.1", "0.0.0.0", "192.168.1.50"})

SERIAL = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{10}(?![A-Za-z0-9])")
PRIVATE_ADDRESS = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|0\.0\.0\.0)(?![\d.])"
)
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".lock"}


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        ROOT / line
        for line in out.splitlines()
        if line and Path(line).suffix not in BINARY_SUFFIXES and (ROOT / line).is_file()
    ]


def _hits(pattern: re.Pattern[str], allowed: frozenset[str]) -> list[str]:
    hits: list[str] = []
    for path in tracked_text_files():
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in pattern.finditer(text):
            if match.group(0) not in allowed:
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)}")
    return hits


def test_no_real_serial_numbers() -> None:
    assert _hits(SERIAL, INVENTED_SERIALS) == []


def test_no_private_addresses() -> None:
    assert _hits(PRIVATE_ADDRESS, ALLOWED_ADDRESSES) == []
