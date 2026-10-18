#!/bin/bash
# usage: promote-pass.sh   The owner's promotion pass (#459, ADR-0145 rule 3, #576): no cycle step runs it; started by hand, it earns
# the local proof of the dev tip and promotes it. It runs under its own single.sh lock, so two passes never overlap. SG_STATE must be set: the pass never reads the
# machine-wide /tmp/sg-state, a different file from the profile's state directory (exit 2).
# Debounce: no suite for a tip already proved or refused ($SG_STATE/promote-pass.tips, last 50), nor while the latest landing in
# $SG_STATE/landed.tsv is younger than SG_PROMOTE_QUIET_SECONDS (default 900): a burst of landings earns one proof.
# One hold of the gate lock, in try mode (serial.sh SERIAL_TRY): a held lock skips the tick, never queues. Inside the hold: the
# suite (SG_PROMOTE_SUITE; default ruff, mypy, pytest -n auto and serial) in a clean tree at the exact tip, the full line
# (gate.sh --status), a commit status on the sha (context squad/full-proof), its read-back, then SG_PROMOTE_PROOF=local promote-stg.sh.
# The tree is $SG_STATE/promote-tree (SG_PROMOTE_TREE), a plain clone, never a ~/wt-* worktree, so wtclean never removes it.
# A red or missing proof: one marker and one line per tip in $SG_STATE/promote.log; the next tip is tried on a later pass.
d=$(cd "$(dirname "$0")" && pwd)
[ -n "${SG_STATE:-}" ] || { echo "promote-pass: SG_STATE is not set; the pass never reads the machine-wide default" >&2; exit 2; }
state=$SG_STATE; export SG_STATE; mkdir -p "$state"; log=$state/promote.log; marks=$state/promote-pass.tips
tree=${SG_PROMOTE_TREE:-$state/promote-tree}; remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}; ctx=squad/full-proof
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
mark() {  # <tip> <reason>: one marker and one line per tip
  echo "$1 $2" >> "$marks"; tail -n 50 "$marks" > "$marks.tmp" && mv "$marks.tmp" "$marks"
  echo "$(date -u +%FT%TZ) promote-pass: $1 $2" >> "$log"; echo "promote-pass: $1 $2"; }
if [ "${1:-}" = --hold ]; then   # inside the gate lock: suite, full line, commit status, read-back, promotion
  tip=$2; . "$d/ghcall.sh"; unset SG_GATE_STATUS SG_GATE_KIND   # the suite inherits no gate setting of this hold
  [ "$(g rev-parse HEAD)" = "$tip" ] || { mark "$tip" "not promoted: the clean tree is not at the tip"; exit 1; }
  py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
  suite=${SG_PROMOTE_SUITE:-"$py -m ruff check squad tests && $py -m mypy squad && $py -m pytest -p no:deepeval -n auto && $py -m pytest -p no:deepeval -p no:xdist"}
  out=$state/promote-suite-${tip:0:12}.log
  if ! (cd "$tree" && bash -c "$suite") > "$out" 2>&1; then
    bash "$d/gate.sh" --status full fail "$tip"; mark "$tip" "not promoted: the full suite failed ($out)"; exit 1; fi
  bash "$d/gate.sh" --status full pass "$tip"
  o=$(gh_send -X POST "repos/$SG_REPO/statuses/$tip" -f state=success -f "context=$ctx" -f "description=full suite, both modes, local") \
    || { mark "$tip" "not promoted: the commit status write failed: $(printf '%s' "$o" | tail -1)"; exit 1; }
  st=$(gh_get "repos/$SG_REPO/commits/$tip/statuses" | jq -r --arg c "$ctx" '[.[] | select(.context == $c)][0].state // empty' 2>/dev/null)
  [ "$st" = success ] || { mark "$tip" "not promoted: the commit status $ctx did not read back as success (${st:-missing})"; exit 1; }
  o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
  [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
  mark "$tip" "promoted: stg is at the proven tip"; exit 0
fi
tip=$(/usr/bin/git ls-remote "$remote" refs/heads/dev | cut -f1); [ -n "$tip" ] || { echo "promote-pass: dev does not resolve on $remote" >&2; exit 1; }
/usr/bin/grep -q "^$tip " "$marks" 2>/dev/null && exit 0   # already proved or refused
last=$(awk -F'\t' '$1 > m {m = $1} END {print m + 0}' "$state/landed.tsv" 2>/dev/null); last=${last:-0}
[ $(( $(date +%s) - last )) -ge "${SG_PROMOTE_QUIET_SECONDS:-900}" ] || exit 0   # a landing is still recent: wait for the burst to end
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-pass: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
[ "$(g rev-parse origin/dev)" = "$tip" ] || exit 0   # dev moved under the read: a later pass takes the new tip
[ "$(g rev-parse origin/stg)" = "$tip" ] && exit 0
g merge-base --is-ancestor origin/stg "$tip" || { mark "$tip" "not promoted: stg is not an ancestor of dev, no fast-forward"; exit 1; }
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { mark "$tip" "not promoted: the clean tree could not check out the tip"; exit 1; }
SERIAL_TRY=1 SG_GATE_STATUS=self SG_GATE_KIND=full bash "$d/gate.sh" bash "$0" --hold "$tip" 2>"$state/promote-pass.err"; rc=$?
[ $rc = 73 ] && exit 0   # another gate holds the lock: this tick is skipped, never queued
cat "$state/promote-pass.err" >&2; exit $rc
