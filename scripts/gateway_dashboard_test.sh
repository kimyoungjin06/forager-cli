#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT"
exec "$ROOT/scripts/gateway_pytest.sh" \
  tests/gateway/test_control_dashboard.py \
  tests/gateway/test_nightly_session_summary.py \
  tests/gateway/test_operator_preferences.py \
  "$@"
