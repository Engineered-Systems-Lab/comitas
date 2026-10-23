#!/usr/bin/env bash
# Compatibility entry point; all refresh behavior lives in Python. The datasets are never committed:
# `refresh` generates them locally, the same as the Refresh button (data_launcher.py refresh-local).
set -euo pipefail
usage() { echo "Usage: governance/extensions/mission_control/mission-control-data.sh refresh" >&2; exit 2; }
[ "$#" -eq 1 ] || usage
case "$1" in refresh) ;; *) usage ;; esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="$(command -v python || command -v python3 || true)"
[ -n "$PY" ] || { echo "FAIL: no Python on PATH; run the script with the path of a Python 3.13 or later" >&2; exit 1; }
cd "$ROOT"
exec "$PY" -B governance/extensions/mission_control/data_launcher.py refresh-local
