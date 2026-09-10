#!/usr/bin/env python3
"""Keep the library version and the integration's requirement pin equal.

Home Assistant compares the manifest pin against the *installed* library on
every start and pip-installs on a mismatch, so the two must never drift.
``--check`` fails when they differ; ``--set X.Y.Z`` rewrites both and
re-installs the editable package so the installed metadata follows.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PYPROJECT = ROOT / "packages" / "sungrow-inverter" / "pyproject.toml"
LIBRARY_INIT = (
    ROOT / "packages" / "sungrow-inverter" / "src" / "sungrow_inverter" / "__init__.py"
)
MANIFEST = ROOT / "custom_components" / "sungrow" / "manifest.json"
PACKAGE = "sungrow-inverter"

_PYPROJECT_VERSION = re.compile(r'^version = "(?P<v>[^"]+)"$', re.MULTILINE)
_INIT_VERSION = re.compile(r'^__version__ = "(?P<v>[^"]+)"$', re.MULTILINE)
_PIN = re.compile(rf"^{re.escape(PACKAGE)}==(?P<v>.+)$")


def read_versions() -> dict[str, str]:
    pyproject = _PYPROJECT_VERSION.search(LIBRARY_PYPROJECT.read_text())
    init = _INIT_VERSION.search(LIBRARY_INIT.read_text())
    manifest = json.loads(MANIFEST.read_text())
    pins = [m for r in manifest["requirements"] if (m := _PIN.match(r))]
    if pyproject is None or init is None or len(pins) != 1:
        sys.exit("could not locate all three version declarations")
    return {
        str(LIBRARY_PYPROJECT.relative_to(ROOT)): pyproject["v"],
        str(LIBRARY_INIT.relative_to(ROOT)): init["v"],
        str(MANIFEST.relative_to(ROOT)): pins[0]["v"],
    }


def check() -> int:
    versions = read_versions()
    if len(set(versions.values())) == 1:
        print(f"version {next(iter(versions.values()))} consistent")
        return 0
    for path, version in versions.items():
        print(f"{path}: {version}")
    print("versions differ", file=sys.stderr)
    return 1


def set_version(version: str) -> int:
    LIBRARY_PYPROJECT.write_text(
        _PYPROJECT_VERSION.sub(f'version = "{version}"', LIBRARY_PYPROJECT.read_text())
    )
    LIBRARY_INIT.write_text(
        _INIT_VERSION.sub(f'__version__ = "{version}"', LIBRARY_INIT.read_text())
    )
    manifest = json.loads(MANIFEST.read_text())
    manifest["requirements"] = [
        f"{PACKAGE}=={version}" if _PIN.match(r) else r
        for r in manifest["requirements"]
    ]
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    # The editable install's metadata does not follow the file edit.
    subprocess.run(["uv", "sync", "--reinstall-package", PACKAGE], cwd=ROOT, check=True)
    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--set", metavar="VERSION")
    args = parser.parse_args()
    return set_version(args.set) if args.set else check()


if __name__ == "__main__":
    sys.exit(main())
