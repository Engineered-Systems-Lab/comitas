#!/usr/bin/env bash
# The lead developer's entry point to the claims commands, runnable from any folder in Git Bash. It adds no logic: it
# finds the primary checkout and runs `worktree.py --main <primary> <command> [args]` from there.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: governance/scripts/claims.sh <command> [args...]
  execution-status, -e   read the level, windows, claims and overlaps (alias: status)
  watch, -w              execution-status --watch
  clear, -c              list every open claim as STALE or LIVE; --apply [<id> ...] clears
  doctor, -d             check the claims database; --repair applies what it reports
clear and doctor are for the lead developer, never workers; a claim the tool can prove dead is cleared by the next preview, prepare, integrate or remove without them. Extra arguments pass through, for example: clear --apply <id>, --json-only
EOF
  exit 2
}

[ "$#" -ge 1 ] || usage
COMMAND="$1"
shift
case "$COMMAND" in
  execution-status|status|-e) set -- execution-status "$@" ;;
  watch|-w) set -- execution-status --watch "$@" ;;
  clear|-c) set -- clear "$@" ;;
  doctor|-d) set -- doctor "$@" ;;
  *) usage ;;
esac

# The primary checkout owns the Git common directory, so this works from a linked worktree and from any folder.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON="$(cd "$HERE" && git rev-parse --git-common-dir)"
COMMON="$(cd "$HERE" && cd "$COMMON" && pwd)"
PRIMARY="$(dirname "$COMMON")"
NATIVE="$PRIMARY"
if command -v cygpath >/dev/null 2>&1; then NATIVE="$(cygpath -m "$PRIMARY")"; fi

# Pick a Python 3 on PATH; the runtime guard at the start of worktree.py refuses one older than 3.13 with a clear message.
if command -v py >/dev/null 2>&1; then PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python >/dev/null 2>&1; then PY=(python)
else echo "FAIL: no Python 3 on PATH; run worktree.py with the path of a Python 3.13 or later" >&2; exit 1
fi

cd "$PRIMARY"
exec "${PY[@]}" -B governance/tools/worktree.py --main "$NATIVE" "$@"
