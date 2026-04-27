#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT"
exec "$ROOT/scripts/gateway_pytest.sh" tests/gateway/test_gateway_cli.py -m error "$@"
