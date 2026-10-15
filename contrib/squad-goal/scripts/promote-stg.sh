#!/bin/bash
# usage: promote-stg.sh      fast-forward stg to the dev tip when the full suite passes on that tip, and never otherwise (ADR-0145).
#
# The promotion gate is the full suite in both modes, once, under the machine-wide gate lock: `-n auto`, then `-p no:xdist`, in a
# clean checkout of the tip. stg moves only when both pass; a red mode stops the gate and moves nothing. A tip whose gate was red
# is not gated again until dev moves. The result and the tip go on the status line the hub reads (gate-status.sh).
# Exit 0: stg is at the dev tip (promoted, or already there). Exit 1: nothing promoted; the reason goes to $SG_STATE/promote.log
# once per tip and reason, so a pass that repeats says nothing new. A later green tip carries the earlier work with it.
# The push is plain, never forced, so stg moves by a fast-forward or not at all.
# The loop never writes main: the owner does, with promote-main.sh. The work happens in a throwaway clone.
# $SG_PROMOTE_REMOTE overrides the remote; $SG_PROMOTE_GATE_CMD replaces the full gate command (the test seam); $SG_GATE_PY is
# the python it runs (default python3).
d=$(cd "$(dirname "$0")" && pwd); remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
# The promotion gate takes the machine-wide gate lock (#392); a pass already inside it just runs.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_STATUS=self exec bash "$d/gate.sh" bash "$0" "$@";; esac
state=${SG_STATE:-/tmp/sg-state};   # the machine-wide default serial.sh uses: never TMPDIR, which is per user
mkdir -p "$state"; log=$state/promote.log
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
say() {  # sha, reason: one line per tip and reason
  grep -qxF "$1 $2" "$state/promote.seen" 2>/dev/null && return; echo "$1 $2" >> "$state/promote.seen"
  echo "$(date -u +%FT%TZ) promote-stg: $1 $2" >> "$log"; }
py=${SG_GATE_PY:-python3}
gatecmd=${SG_PROMOTE_GATE_CMD:-"'$py' -m pytest -n auto -p no:deepeval && '$py' -m pytest -p no:xdist -p no:deepeval"}
# The gate runs with none of the loop's SG_* variables (as branchland.sh's does), so a test reads its own fixture; SG_SERIAL_HELD stays.
noloop=$(env | sed -n 's/^\(SG_[A-Za-z0-9_]*\)=.*/-u \1/p' | grep -v -x -- '-u SG_SERIAL_HELD' | tr '\n' ' ')
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-stg: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse origin/dev) || exit 1
[ "$(g rev-parse origin/stg)" = "$tip" ] && exit 0
g merge-base --is-ancestor origin/stg "$tip" || { say "$tip" "not promoted: stg is not an ancestor of dev, no fast-forward"; exit 1; }
grep -qxF "$tip gate red" "$state/promote.seen" 2>/dev/null && exit 1   # this tip was gated and was red: dev has to move first
g checkout -q --detach "$tip" 2>/dev/null || { say "$tip" "not promoted: no clean checkout of the tip"; exit 1; }
if (cd "$W" && PYTHONPATH=. env $noloop bash -c "$gatecmd") >"$W/.gate.out" 2>&1; then
  bash "$d/gate.sh" --status full pass "$tip"
else
  bash "$d/gate.sh" --status full fail "$tip"; echo "$tip gate red" >> "$state/promote.seen"
  say "$tip" "not promoted: the full gate is red: $(grep -v '^$' "$W/.gate.out" | tail -1)"; exit 1; fi
o=$(g push -q origin "$tip:refs/heads/stg" 2>&1) || { say "$tip" "not promoted: push refused: $(printf '%s' "$o" | tail -1)"; exit 1; }
echo "PROMOTED stg $tip"
