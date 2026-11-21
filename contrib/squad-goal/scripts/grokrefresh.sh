#!/bin/bash
# Start the grok TUI briefly so the CLI writes a fresh creditUsagePercent line (#1530).
#
# The grok CLI writes that line only when an interactive TUI session starts, and the loop runs grok headless.
# The hub calls this script; the pseudo-terminal lives here, never on the hub's own path (#2492).
#
# Usage: grokrefresh.sh [--ttl <seconds>] [--seconds <tui life>]
#   --ttl      skip when the last start is newer than this many seconds (default 3600: at most one start an hour)
#   --seconds  how long the TUI is left alive before it is killed (default 20)
#
# Exit 0 when a start ran or was skipped, 2 when the grok CLI is absent.
# It prints one line: `refreshed: ...` or `skip: ...`.
set -u

TTL=3600
LIFE=20
while [ $# -gt 0 ]; do
  case "$1" in
    --ttl) TTL=$2; shift 2 ;;
    --seconds) LIFE=$2; shift 2 ;;
    *) echo "grokrefresh: unknown option $1" >&2; exit 2 ;;
  esac
done

HOME_DIR=${HOME:-/root}
STAMP=${SG_GROK_REFRESH_STAMP:-$HOME_DIR/.grok/credit-refresh.stamp}
LOCK=${SG_GROK_REFRESH_LOCK:-$HOME_DIR/.grok/refresh.lock}
mkdir -p "$(dirname "$STAMP")" "$(dirname "$LOCK")" 2>/dev/null

# The grok CLI and the pty it needs are the loop host's: macOS. A Linux runner has neither, and the gauge there
# reads the log the CLI wrote elsewhere, so a start has nothing to do.
[ "$(uname -s)" = Darwin ] || { echo "skip: the grok TUI start is macOS-only"; exit 0; }

command -v grok >/dev/null 2>&1 || { echo "skip: no grok CLI on PATH"; exit 2; }

now=$(date +%s)
if [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  if [ "$TTL" -gt 0 ] && [ $((now - last)) -lt "$TTL" ]; then
    echo "skip: a start ran $((now - last)) s ago (ttl ${TTL} s)"
    exit 0
  fi
fi

# One start at a time: a lock whose pid is alive means a TUI is already running.
if [ -d "$LOCK" ]; then
  holder=$(cat "$LOCK/pid" 2>/dev/null || echo '')
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    echo "skip: a grok TUI start is already running (pid $holder)"
    exit 0
  fi
  rm -rf "$LOCK"
fi
mkdir "$LOCK" 2>/dev/null || { echo "skip: another start took the lock"; exit 0; }
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM

# A pseudo-terminal is the only way the CLI writes the line. `script` allocates it; perl's alarm bounds the life.
# stdin is /dev/null so the TUI has nothing to read and leaves on its own.
if command -v perl >/dev/null 2>&1; then
  perl -e 'alarm shift; exec @ARGV' "$LIFE" /usr/bin/script -q /dev/null grok </dev/null >/dev/null 2>&1
else
  /usr/bin/script -q /dev/null grok </dev/null >/dev/null 2>&1 &
  p=$!; sleep "$LIFE"; kill -KILL "$p" 2>/dev/null; wait "$p" 2>/dev/null
fi

date +%s > "$STAMP"
echo "refreshed: the grok TUI ran for up to ${LIFE} s"
exit 0
