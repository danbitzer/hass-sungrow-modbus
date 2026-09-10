#!/usr/bin/env bash
# Repository CI gate: format, lint, type-check, version sync, tests.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> ruff format --check"
uv run ruff format --check .
echo "==> ruff check"
uv run ruff check .
echo "==> mypy (library, strict)"
uv run mypy
echo "==> version sync"
uv run python scripts/sync_version.py --check
echo "==> pytest"
uv run pytest -q
echo "==> all checks passed"
