#!/bin/bash
# usage: fast-gate.sh      the fast gate of a work-branch tip or of a child that enters a wave (ADR-0145), run in that tip's worktree.
#
# It is the base battery (ruff, mypy and the fast pytest, SG_FAST_CMD), then the shell suite test-<script>.sh of every .sh or .py
# script under contrib/squad-goal/scripts that the tip changes; a changed test-<script>.sh runs itself. The changed files are the diff of HEAD
# against its merge-base with SG_FAST_BASE (default origin/$SG_BASE, else origin/dev), so work that landed on the base after the
# branch point adds no suite. A base that does not resolve fails the gate, never skips the suites. Each suite runs hermetic, as
# shell-suites.sh runs it: no SG_* variable, its own HOME. The promotion gate is the full suite (promote-stg.sh), not this.
# The gate runs under the machine-wide gate lock (gate.sh) and leaves the fast status line the hub reads. Exit 0 when all pass,
# 1 on a defect, 2 when the base does not resolve, 4 when the battery is inconclusive twice (#699): 4 holds landing.
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
# classify <log> <rc>: the failing class of a red battery (#699), one word, then its detail: lint (ruff's code and path, #670,
# #671), type (mypy's line), collection (the files pytest could not collect), pytest (the failed or errored test ids),
# saturation (a worker crashed, or a signal killed the battery) or unknown (none of these). A red that names no failed test
# is never read as one cause: it is a lint, type or collection defect, or it is inconclusive.
ids_of() { sed -n -e 's/^FAILED \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ ]*::[^ ]*\).*/\1/p' "$1" | sort -u; }
classify() {
  local log=$1 rc=$2 p f
  p=$(sed -n -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \[[^]]*\] \(.*\)$/\1 \2/p' -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \([^[].*\)$/\1 \2/p' "$log" | head -n 1)   # with or without the fix marker (#671)
  if [ -n "$p" ]; then f=$(sed -n 's/^ *--> \(.*\)$/\1/p' "$log" | head -n 1); echo "lint the base battery failed at ruff: ${f:+$f }$p"; return; fi
  p=$(sed -n 's/^\([^ ]*:[0-9][0-9]*\): error: \(.*\)$/\1: \2/p' "$log" | head -n 1)
  [ -n "$p" ] && { echo "type the base battery failed at mypy: $p"; return; }
  p=$(sed -n -e 's/^ERROR collecting \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ :]*\.py\)\( .*\)*$/\1/p' "$log" | sort -u | tr '\n' ' ' | sed 's/ $//')
  if [ -n "$p" ] || /usr/bin/grep -qE 'errors? during collection' "$log"; then echo "collection pytest could not collect ${p:-its tests}"; return; fi
  p=$(ids_of "$log" | tr '\n' ' ' | sed 's/ $//')
  [ -n "$p" ] && { echo "pytest the base battery failed at $p"; return; }
  if [ "$rc" -ge 128 ] || /usr/bin/grep -qE 'node down|crashed worker|worker .*crashed|^Killed|MemoryError|Cannot allocate memory|Too many open files|Resource temporarily unavailable' "$log"; then
    echo "saturation the battery exited $rc with a crashed worker or a kill, and no test id"; return; fi
  echo "unknown the battery exited $rc and named no lint, type, collection or test failure"
}
# A test that fails under the parallel battery and passes alone is saturation, not a defect (#520): the failed tests the log
# names run once more, serially (pyproject turns the pytest cache off, so --lf would rerun the whole battery). A red there too
# fails the gate. A saturation or unknown red is inconclusive: the battery runs once more, and only once. A pass there goes
# on; a defect there fails as that defect; inconclusive again exits 4, and the landing holds. The battery output stays in a
# log that the failure line names. Exit 1 is a defect, 4 is inconclusive; neither grants a landing.
retrycmd=${SG_FAST_RETRY_CMD:-"$py -m pytest -p no:deepeval -p no:xdist -o heavy_cap_seconds=20"}
log=${SG_STATE:-${TMPDIR:-/tmp}}/fast-gate-$(git rev-parse --short HEAD 2>/dev/null || echo x).log
for try in 1 2; do
  lg=$log; [ $try = 1 ] || lg=${log%.log}-retry.log
  bash -c "$fastcmd" > "$lg" 2>&1 && break
  rc=$?; tail -n 5 "$lg"
  c=$(classify "$lg" "$rc"); cls=${c%% *}; detail=${c#* }
  case $cls in
    saturation|unknown)
      [ $try = 1 ] && { echo "fast-gate: $cls: $detail; inconclusive, so the battery runs once more ($lg)"; continue; }
      echo "fast-gate: $cls: $detail; inconclusive twice, so the gate does not pass and the landing holds ($lg)"; exit 4;;
    pytest)
      # shellcheck disable=SC2086  # one word per test id; set -f keeps a parametrized id such as t[a.sh] from globbing
      set -f; if bash -c "$retrycmd \"\$@\"" retry $(ids_of "$lg") >> "$lg" 2>&1; then set +f
        echo "fast-gate: the base battery failed under load and its failed tests passed alone: saturation, not a defect ($lg)"; break; fi
      echo "fast-gate: pytest: $detail, and they fail alone too ($lg)"; exit 1;;
    *) echo "fast-gate: $cls: $detail ($lg)"; exit 1;;
  esac
done
suites=$(git diff --name-only --diff-filter=d "$mb" HEAD | sed -n -e 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' -e 's|^contrib/squad-goal/scripts/\(.*\)\.py$|\1|p' | grep -v / \
  | sed 's|^test-||' | sort -u)
failed=""
for n in $suites; do s=$top/contrib/squad-goal/scripts/test-$n.sh; [ -f "$s" ] || continue
  echo "=== test-$n.sh"
  hm=$(mktemp -d); if (cd "$top" && env $(compgen -e | grep '^SG_' | sed 's/^/-u /') HOME="$hm" bash "$s"); then echo "--- ok: test-$n.sh"
  else echo "--- FAILED: test-$n.sh"; failed="$failed test-$n.sh"; fi; rm -rf "$hm"; done
[ -z "$failed" ] || { echo "fast-gate: FAILED:$failed"; exit 1; }
