#!/bin/bash
# usage: SG_GATE_WORKERS=<n> SG_GATE_REASON="<why>" [SG_GATE_ISSUE=<n>] hand-gate.sh   The fast gate of a hand fix (#1169), run in the fix's worktree.
# A person declares the worker count and the reason where the gate starts. No count, a count that is not a positive number,
# no reason, or a count above the ceiling for a small delta refuses before any battery runs (exit 2). A delta of at most
# SG_HAND_SMALL_FILES files (default 2) takes at most SG_HAND_SMALL_WORKERS workers (default 2), so one person's small change
# never takes the machine's budget. The gate is then fast-gate.sh, which runs under the machine lock and takes its slots.
here="$(cd "$(dirname "$0")" && pwd)"
base=${SG_FAST_BASE:-origin/${SG_BASE:-dev}}
n=${SG_GATE_WORKERS:-}; reason=${SG_GATE_REASON:-}
refuse() { echo "hand-gate: refused: $1" >&2; exit 2; }
case $n in ''|*[!0-9]*|0) refuse "no worker count declared: set SG_GATE_WORKERS=<n>, a positive whole number";; esac
[ -n "$reason" ] || refuse "no reason declared: set SG_GATE_REASON to say why $n workers"
mb=$(git merge-base HEAD "$base" 2>/dev/null) || refuse "base $base does not resolve"
files=$(git diff --name-only "$mb" | /usr/bin/grep -c .)
cap=${SG_HAND_SMALL_WORKERS:-2}; small=${SG_HAND_SMALL_FILES:-2}
[ "$files" -gt "$small" ] || [ "$n" -le "$cap" ] || refuse "a delta of $files files takes at most $cap workers: declare $cap or fewer"
echo "hand-gate: $n workers, $files files changed, reason: $reason"
export SG_GATE_WORKERS=$n
# The gate's card moves to Testing (#2563). The issue comes from SG_GATE_ISSUE only, never from the branch name. The marker
# is written before the exec: the pid stays $$ through it, and no trap runs after it. The reader drops a dead marker.
if [ -n "${SG_GATE_ISSUE:-}" ]; then
  PYTHONPATH="$(cd "$here/../../.." && pwd)${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.handhold gate "$SG_GATE_ISSUE" --pid $$ >&2 || echo "hand-gate: no marker written for #$SG_GATE_ISSUE; the gate runs" >&2
else
  echo "hand-gate: no issue declared: no card moves"
fi
# A hand gate measures like an entry gate: the one-bounded-attempt cap is a child's alone (#1527).
SG_GATE_CALLER=${SG_GATE_CALLER:-entry} exec bash "$here/fast-gate.sh"
