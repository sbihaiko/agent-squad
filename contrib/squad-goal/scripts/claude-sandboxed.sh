#!/bin/sh
# The CLI the SDK starts for a child session (#1469). The kernel refuses a signal to a process outside the session, whatever
# spells it, so no text rule has to guess. Without sandbox-exec no child starts: no OS wall, no session.
[ -x /usr/bin/sandbox-exec ] || { echo "claude-sandboxed: /usr/bin/sandbox-exec is missing; no child session starts without the signal wall" >&2; exit 1; }
exec /usr/bin/sandbox-exec -p "${SQUAD_SIGNAL_PROFILE:?the signal profile is unset}" "${SQUAD_CLAUDE_CLI:?the CLI is unset}" "$@"
