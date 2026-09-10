"""The library version and the manifest pin must agree (see scripts/sync_version.py)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_versions_are_in_sync() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_version.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
