#!/bin/bash
# usage: fast-gate.sh      the fast gate of a work-branch tip or of a child that enters a wave (ADR-0145), run in that tip's worktree.
#
# It is the base battery (ruff, mypy and the fast pytest, SG_FAST_CMD), then the shell suite test-<script>.sh of every .sh or .py
# script under contrib/squad-goal/scripts that the tip changes; a changed test-<script>.sh runs itself. The changed files are the diff of HEAD
# against its merge-base with SG_FAST_BASE (default origin/$SG_BASE, else origin/dev), so work that landed on the base after the
# branch point adds no suite. A base that does not resolve fails the gate, never skips the suites. Each suite runs hermetic, as
# shell-suites.sh runs it: no SG_* variable, its own HOME. The promotion gate is the full suite (promote-stg.sh), not this.
# The gate runs under the machine-wide gate lock (gate.sh) and leaves the fast status line the hub reads. Exit 0 when all pass.
here="$(cd "$(dirname "$0")" && pwd)"
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_KIND=fast exec bash "$here/gate.sh" bash "$0" "$@";; esac
base=${SG_FAST_BASE:-origin/${SG_BASE:-dev}}
# The base battery is one definition (base-battery.sh), the one branchland.sh's landing check and wave.sh's tip check run
# over the same fast gate. Its pytest takes a bounded worker count, SG_GATE_WORKERS, never `auto`.
. "$here/base-battery.sh"
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
fastcmd=${SG_FAST_CMD:-"$(sg_base_battery "$py")"}
mb=$(git merge-base HEAD "$base" 2>/dev/null) || { echo "fast-gate: base $base does not resolve: the changed scripts are unknown, so the gate does not pass"; exit 2; }
top=$(git rev-parse --show-toplevel) || exit 2
# The check the battery failed at, named with its own line (#670): ruff's code and path, mypy's line, or pytest, which named no
# failed test. A reason that says only "names no failed test" hides a lint or type failure from the child that must repair it.
stage_of() {
  local p
  p=$(sed -n 's/^\([A-Z][0-9][0-9][0-9]\) \[[^]]*\] \(.*\)$/\1 \2/p' "$1" | head -n 1)
  if [ -n "$p" ]; then
    local f; f=$(sed -n 's/^ *--> \(.*\)$/\1/p' "$1" | head -n 1)
    echo "ruff: ${f:+$f }$p"; return
  fi
  p=$(sed -n 's/^\([^ ]*:[0-9][0-9]*\): error: \(.*\)$/\1: \2/p' "$1" | head -n 1)
  [ -n "$p" ] && { echo "mypy: $p"; return; }
  echo "pytest: it named no failed test"
}
# A test that fails under the parallel battery and passes alone is saturation, not a defect (#520): the failed tests the log
# names run once more, serially (pyproject turns the pytest cache off, so --lf would rerun the whole battery). A red that
# names no test (lint, types) or a red there too fails the gate. The battery output stays in a log that the failure line names.
retrycmd=${SG_FAST_RETRY_CMD:-"$py -m pytest -p no:deepeval -p no:xdist -o heavy_cap_seconds=20"}
log=${SG_STATE:-${TMPDIR:-/tmp}}/fast-gate-$(git rev-parse --short HEAD 2>/dev/null || echo x).log
if ! bash -c "$fastcmd" > "$log" 2>&1; then
  tail -n 5 "$log"
  ids=$(sed -n 's/^FAILED \([^ ]*\).*/\1/p' "$log" | sort -u)
  [ -n "$ids" ] || { echo "fast-gate: the base battery failed at $(stage_of "$log") ($log)"; exit 1; }
  # shellcheck disable=SC2086  # one word per test id; set -f keeps a parametrized id such as t[a.sh] from globbing
  set -f; if bash -c "$retrycmd \"\$@\"" retry $ids >> "$log" 2>&1; then echo "fast-gate: the base battery failed under load and its failed tests passed alone: saturation, not a defect ($log)"
  else echo "fast-gate: the base battery failed, and its failed tests fail alone too ($log)"; exit 1; fi; set +f
fi
suites=$(git diff --name-only --diff-filter=d "$mb" HEAD | sed -n -e 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' -e 's|^contrib/squad-goal/scripts/\(.*\)\.py$|\1|p' | grep -v / \
  | sed 's|^test-||' | sort -u)
failed=""
for n in $suites; do s=$top/contrib/squad-goal/scripts/test-$n.sh; [ -f "$s" ] || continue
  echo "=== test-$n.sh"
  hm=$(mktemp -d); if (cd "$top" && env $(compgen -e | grep '^SG_' | sed 's/^/-u /') HOME="$hm" bash "$s"); then echo "--- ok: test-$n.sh"
  else echo "--- FAILED: test-$n.sh"; failed="$failed test-$n.sh"; fi; rm -rf "$hm"; done
[ -z "$failed" ] || { echo "fast-gate: FAILED:$failed"; exit 1; }
