#!/bin/bash
# usage: fast-gate.sh      the fast gate of a work-branch tip or of a child that enters a wave (ADR-0145), run in that tip's worktree.
#
# It is the base battery (ruff, mypy and the fast pytest, SG_FAST_CMD), then the shell suite test-<script>.sh of every script under
# contrib/squad-goal/scripts that the tip changes; a changed test-<script>.sh runs itself. The changed files are the diff of HEAD
# against its merge-base with SG_FAST_BASE (default origin/$SG_BASE, else origin/dev), so work that landed on the base after the
# branch point adds no suite. A base that does not resolve fails the gate, never skips the suites. Each suite runs hermetic, as
# shell-suites.sh runs it: no SG_* variable, its own HOME. The promotion gate is the full suite (promote-stg.sh), not this.
# The gate runs under the machine-wide gate lock (gate.sh) and leaves the fast status line the hub reads. Exit 0 when all pass.
here="$(cd "$(dirname "$0")" && pwd)"
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_KIND=fast exec bash "$here/gate.sh" bash "$0" "$@";; esac
base=${SG_FAST_BASE:-origin/${SG_BASE:-dev}}
fastcmd=${SG_FAST_CMD:-'uv run --extra dev ruff check . && uv run --extra dev mypy && uv run --extra dev pytest -n auto -m "not heavy" -p no:deepeval'}
mb=$(git merge-base HEAD "$base" 2>/dev/null) || { echo "fast-gate: base $base does not resolve: the changed scripts are unknown, so the gate does not pass"; exit 2; }
top=$(git rev-parse --show-toplevel) || exit 2
bash -c "$fastcmd" || { echo "fast-gate: the base battery failed"; exit 1; }
suites=$(git diff --name-only --diff-filter=d "$mb" HEAD | sed -n 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' | grep -v / \
  | sed 's|^test-||' | sort -u)
failed=""
for n in $suites; do s=$top/contrib/squad-goal/scripts/test-$n.sh; [ -f "$s" ] || continue
  echo "=== test-$n.sh"
  hm=$(mktemp -d); if (cd "$top" && env $(compgen -e | grep '^SG_' | sed 's/^/-u /') HOME="$hm" bash "$s"); then echo "--- ok: test-$n.sh"
  else echo "--- FAILED: test-$n.sh"; failed="$failed test-$n.sh"; fi; rm -rf "$hm"; done
[ -z "$failed" ] || { echo "fast-gate: FAILED:$failed"; exit 1; }
