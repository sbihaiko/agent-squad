#!/bin/bash
# usage: shell-suites.sh   Runs every test-*.sh next to this script and reports every failing suite, not just the first; exit 1 if any failed.
# Hermetic by construction (#1410): each suite runs with every inherited SG_* variable cleared and HOME pointed at its own temp dir, so the live loop's state, fork and profile never reach an assertion.
# Offline by construction: the suites stub gh, claude and the board and use throwaway repos; nothing here touches the network, a model or the emulator.
# One full gate per machine (#81): the whole run holds the machine-wide `gate` lock through serial.sh, so the loop's land pass
# and a child's own suite run take turns instead of both competing for every core; the second prints one wait line and starts
# when the first ends. Re-entrant by serial.sh's own rule, so a gate inside a gate holder (the land pass runs a suite that runs
# the gate) waits for nobody. A copy with no serial.sh beside it runs unwrapped and SAYS SO: a missing lock must never pass as a held one.
here="$(cd "$(dirname "$0")" && pwd)"; failed=""; n=0
if [ -f "$here/serial.sh" ]; then
  case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) exec bash "$here/serial.sh" gate bash "$here/$(basename "$0")" "$@";; esac
else
  echo "shell-suites: no serial.sh beside this script; the machine-wide gate lock is NOT held" >&2
fi
for s in "$here"/test-*.sh; do [ -f "$s" ] || continue; b=$(basename "$s"); n=$((n+1))
  echo "=== $b"
  hm=$(mktemp -d); if env $(compgen -e | /usr/bin/grep "^SG_" | sed "s/^/-u /") HOME="$hm" bash "$s"; then echo "--- ok: $b"; else rc=$?; echo "--- FAILED: $b (exit $rc)"; failed="$failed $b"; fi; rm -rf "$hm"
done
if [ -n "$failed" ]; then echo "shell suites: FAILED:$failed"; exit 1; fi
echo "shell suites: $n suites green"
