#!/bin/bash
# usage: shell-suites.sh   Runs every test-*.sh next to this script and reports every failing suite, not just the first; exit 1 if any failed.
# Offline by construction: the suites stub gh, claude and the board and use throwaway repos; nothing here touches the network, a model or the emulator.
here="$(cd "$(dirname "$0")" && pwd)"; failed=""; n=0
for s in "$here"/test-*.sh; do [ -f "$s" ] || continue; b=$(basename "$s"); n=$((n+1))
  echo "=== $b"
  if bash "$s"; then echo "--- ok: $b"; else rc=$?; echo "--- FAILED: $b (exit $rc)"; failed="$failed $b"; fi
done
if [ -n "$failed" ]; then echo "shell suites: FAILED:$failed"; exit 1; fi
echo "shell suites: $n suites green"
