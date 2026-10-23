#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LOG_DIR="$ROOT/tmp/mission-control"
STAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
LOG_FILE="$LOG_DIR/$STAMP.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
cd "$ROOT"

python -c 'import sys; assert sys.version_info >= (3,13), f"Python 3.13+ required, found {sys.version}"'

echo "Log: $LOG_FILE"

exec python -B governance/extensions/mission_control/ui/serve.py "$@"