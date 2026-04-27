#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT"
ARGS=("$@")
if [ "${#ARGS[@]}" -eq 0 ]; then
  ARGS=(tests/gateway/test_gateway_cli.py)
fi

exec uv run --with pytest --with jinja2 pytest -q "${ARGS[@]}"
