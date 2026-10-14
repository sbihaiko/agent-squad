#!/bin/bash
# usage: gate.sh <command...>   Runs a gate under the machine-wide `gate` lock (#392), so two gates never run together.
# The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all come through here. A second gate
# waits for the first and then runs; it is never dropped, so the wait is long (SERIAL_WAIT_SECONDS, default 86400) and says so on one
# line naming the holder. A gate holder that died, or that outlived its run, loses the lock to the next gate (serial.sh).
# SERIAL_HOLD_SECONDS (default 7200) is the longest a gate may hold the lock. The exit code of the command is passed through.
# The lock lives in $SG_STATE/locks/gate. With SG_STATE unset it is the one machine-wide /tmp/sg-state, never a TMPDIR path:
# TMPDIR is per user on macOS, so two users or a launchd job would resolve two lock directories and run two gates together.
[ $# -gt 0 ] || { echo "usage: gate.sh <command...>   (lock in \$SG_STATE/locks/gate; SG_STATE unset: /tmp/sg-state, never TMPDIR)" >&2; exit 2; }
export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-86400} SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-7200}
exec bash "$(cd "$(dirname "$0")" && pwd)/serial.sh" gate "$@"
