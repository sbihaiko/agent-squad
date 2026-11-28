#!/bin/bash
# usage: shell-suites.sh   Runs every test-*.sh next to this script and reports every failing suite, not just the first; exit 1 if any failed.
# Hermetic by construction (#1410, #755): each suite runs with every inherited SG_*, SERIAL_* and PYTEST_* variable cleared (the gate exports its lock bounds, and a caller's pytest session sets PYTEST_*; #2787), no git location variable (#2306), and HOME pointed at its own temp dir, so the live loop's state, fork, profile and the caller's pytest settings never reach an assertion.
# Offline by construction: the suites stub gh, claude and the board and use throwaway repos; nothing here touches the network, a model or the emulator.
# No machine-wide `gate` lock (#94): the lock guards the full pytest gate only, taken around that half alone. Held
# across these suites, one holder blocked every other gate on the machine for a quarter hour.
here="$(cd "$(dirname "$0")" && pwd)"; failed=""; n=0
# (#2306) No suite writes a GIT_DIR its caller exported. A missing caps.sh or scrub refuses the run, never skips the scrub.
. "$here/caps.sh" && sg_git_env_scrub || { echo "shell-suites: $here/caps.sh gave no sg_git_env_scrub; refused" >&2; exit 2; }
# (#3056) A suite's bare `python3` is the gate interpreter, never the one the caller's PATH names first: under a minimal
# PATH a suite met Xcode's 3.9, where a module's `from datetime import UTC` raised. A bare answer names no directory to pin.
py=$(sg_gate_python 2>/dev/null); case $py in /*) pydir=${py%/*};; *) pydir=;; esac
for s in "$here"/test-*.sh; do [ -f "$s" ] || continue; b=$(basename "$s"); n=$((n+1))
  echo "=== $b"
  hm=$(mktemp -d); if env $(compgen -e | /usr/bin/grep -E "^(SG_|SERIAL_|PYTEST_)" | sed "s/^/-u /") HOME="$hm" PATH="${pydir:+$pydir:}$PATH" bash "$s"; then echo "--- ok: $b"; else rc=$?; echo "--- FAILED: $b (exit $rc)"; failed="$failed $b"; fi; rm -rf "$hm"
done
if [ -n "$failed" ]; then echo "shell suites: FAILED:$failed"; exit 1; fi
echo "shell suites: $n suites green"
