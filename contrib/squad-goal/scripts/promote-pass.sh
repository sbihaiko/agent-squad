#!/bin/bash
# usage: promote-pass.sh   The owner's promotion pass (#459, ADR-0145 rule 3, #576): no cycle step runs it; started by hand, it earns
# the local proof of the dev tip and promotes it. It runs under its own single.sh lock, so two passes never overlap. SG_STATE must be set: the pass never reads the
# machine-wide /tmp/sg-state, a different file from the profile's state directory (exit 2).
# Debounce: no suite for a tip already proved or refused ($SG_STATE/promote-pass.tips, last 50), nor while the latest landing in
# $SG_STATE/landed.tsv is younger than SG_PROMOTE_QUIET_SECONDS (default 900): a burst of landings earns one proof.
# A full pass carries across a covered delta (ADR-0156): `delta-pass.sh` runs first, and any refusal or a held delta lock falls
# through to the full suite, so a tip that moved by a covered path promotes in minutes and never by a weaker proof.
# One hold of the proof's own lock, in try mode (serial.sh SERIAL_TRY, name `full-proof`): a held lock skips the tick, never
# queues. The proof never takes the `gate` lock, which the landing gate, the wave entry and the promotion all wait on, so a proof
# in flight holds no landing up and a landing holds no proof up (ADR-0145 rule 3, as the nightly of ADR-0153 does). Inside the
# hold: the suite (SG_PROMOTE_SUITE; default ruff, mypy, pytest at sg_battery_workers and serial) in a clean tree at the exact
# tip, the full line (gate.sh --status), a commit status on the sha (context squad/full-proof), its read-back, then
# SG_PROMOTE_PROOF=local promote-stg.sh.
# The tree is $SG_STATE/promote-tree (SG_PROMOTE_TREE), a plain clone, never a ~/wt-* worktree, so wtclean never removes it.
# A red or missing proof: one marker and one line per tip in $SG_STATE/promote.log; the next tip is tried on a later pass.
d=$(cd "$(dirname "$0")" && pwd)
[ -n "${SG_STATE:-}" ] || { echo "promote-pass: SG_STATE is not set; the pass never reads the machine-wide default" >&2; exit 2; }
state=$SG_STATE; export SG_STATE; mkdir -p "$state"; log=$state/promote.log; marks=$state/promote-pass.tips
tree=${SG_PROMOTE_TREE:-$state/promote-tree}; remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}; ctx=squad/full-proof
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
. "$d/caps.sh"
mark() {  # <tip> <reason>: one marker and one line per tip
  echo "$1 $2" >> "$marks"; tail -n 50 "$marks" > "$marks.tmp" && mv "$marks.tmp" "$marks"
  echo "$(date -u +%FT%TZ) promote-pass: $1 $2" >> "$log"; echo "promote-pass: $1 $2"; }
if [ "${1:-}" = --hold ]; then   # inside the proof's own lock: suite, full line, commit status, read-back, promotion
  tip=$2; . "$d/ghcall.sh"; unset SG_GATE_STATUS SG_GATE_KIND   # the suite inherits no gate setting of this hold
  # The proof reserves what it measures (#739): the dispatchers read this marker (gh_dispatch_hold) and start no run while it
  # stands, so the suite is not measured against the next wave landing. Its bound is the gate lock's own (SERIAL_HOLD_SECONDS),
  # and the trap removes it on exit, INT and TERM, so a stopped proof leaves nothing behind.
  printf '%s %s\n' "$(date +%s)" "$$" > "$state/promote-reserve.$$" && mv "$state/promote-reserve.$$" "$state/promote-reserve"
  # The proof samples the load while it runs, not only at the start: a red taken with the box over its bound is no proof at all,
  # so it writes no status and no marker and the same tip is retried on a quieter pass (ADR-0018 stays whole: a red taken under
  # the bound is a real defect). The sample file is named for this process and removed with the reserve.
  samples=$state/promote-load.$$
  ( while :; do sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' | tr -d '{}' >> "$samples"; sleep 10; done ) & mon=$!
  trap 'kill "$mon" 2>/dev/null; rm -f "$state/promote-reserve" "$samples"' EXIT INT TERM
  [ "$(g rev-parse HEAD)" = "$tip" ] || { mark "$tip" "not promoted: the clean tree is not at the tip"; exit 1; }
  # ADR-0156: a full pass carries across a covered delta. When one is on record and the tip moved only by covered paths, the
  # delta proof runs instead of the full suite and the promotion reads its record. Any refusal (an uncovered path, a bound, no
  # full pass) or a held delta lock falls through to the full suite below: the cheaper proof is an optimization, never a weaker gate.
  if bash "$d/delta-pass.sh" > "$state/delta-pass.log" 2>&1; then
    o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
    [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
    mark "$tip" "promoted: the delta carried the full pass ($state/delta-pass.log)"; exit 0
  fi
  py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
  suite=${SG_PROMOTE_SUITE:-"$py -m ruff check squad tests && $py -m mypy squad && $py -m pytest -p no:deepeval -n ${SG_PROMOTE_WORKERS:-$(sg_battery_workers)} && $py -m pytest -p no:deepeval -p no:xdist"}
  out=$state/promote-suite-${tip:0:12}.log
  cores2=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1); lim2=${SG_PROMOTE_LOAD_LIMIT:-$cores2}
  # ADR-0157: a proof is bounded. Past its reserve bound (30 minutes) the reserve lifts and the proof keeps running, so the
  # loop dispatches beside it; past its deadline (two hours) the suite and every child it started are killed, and the tip is
  # recorded indeterminate: not tested, no promotion, no gate status, and no refusal, so a later pass retries it.
  rbound=${SG_PROMOTE_RESERVE_SECONDS:-1800}; dline=${SG_PROMOTE_DEADLINE_SECONDS:-7200}
  kill_tree() { local p=$1 c; for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c"; done; kill "$p" 2>/dev/null; }
  s0=$(date +%s); (cd "$tree" && bash -c "$suite") > "$out" 2>&1 & spid=$!
  rc=0; relieved=
  while kill -0 "$spid" 2>/dev/null; do
    el=$(( $(date +%s) - s0 ))
    if [ -z "$relieved" ] && [ "$el" -ge "$rbound" ]; then
      relieved=1; rm -f "$state/promote-reserve"
      echo "$(date -u +%FT%TZ) promote-pass: $tip passed its ${rbound}s reserve bound: the reserve lifts, the loop works beside the proof" >> "$log"
    fi
    [ "$el" -ge "$dline" ] && { rc=124; kill_tree "$spid"; break; }
    sleep 5
  done
  if [ "$rc" = 124 ]; then wait "$spid" 2>/dev/null; else wait "$spid"; rc=$?; fi
  if [ "$rc" = 124 ]; then
    kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
    printf '%s\t%s\t%s\n' "$(date +%s)" "$tip" "$dline" > "$state/promote-indeterminate.$$" \
      && mv "$state/promote-indeterminate.$$" "$state/promote-indeterminate"
    echo "$(date -u +%FT%TZ) promote-pass: $tip not tested: the proof passed its ${dline}s bound and was killed" >> "$log"
    echo "promote-pass: $tip not tested: the proof passed its ${dline}s bound and was killed"
    exit 1; fi
  if [ "$rc" != 0 ]; then
    kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
    read -r peak mean < <(awk '{ if ($1 + 0 > p) p = $1 + 0; s += $1 + 0; n++ } END { printf "%d %.2f", p, (n ? s / n : 0) }' "$samples" 2>/dev/null)
    peak=${peak:-0}; mean=${mean:-0}
    # The sample is a fact about the run, never a verdict: the suite is itself the load, so a threshold on this number cannot
    # tell a loaded box from a working one. A red stays a red (ADR-0018): the peak is written beside it so the reader can see
    # whether the box was fighting the suite, and the guard at the start is what keeps the proof off a busy machine.
    bash "$d/gate.sh" --status full fail "$tip"; mark "$tip" "not promoted: the full suite failed ($out), peak load $peak on $cores2 cores, mean $mean"; exit 1; fi
  kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
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
# The proof is a measurement (#738): the suite's assertions are the wall time of real scripts, so it never measures under the
# loop's own load. A live run, or a load average at the bound, marks the tip "not proven: machine loaded" and writes no marker,
# so a later pass takes the same tip once the machine is free. SG_PROMOTE_LOAD_LIMIT names the load average bound (default:
# the core count), and SG_PROMOTE_WORKERS the pytest workers (default: sg_battery_workers, never `auto`).
live=$state/active-runs.txt
cores=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1)
limit=${SG_PROMOTE_LOAD_LIMIT:-$cores}
load1=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' | tr -d '{}')
load1=${load1:-$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)}; load1=${load1:-0}
# A listed run is live while its own record moves, or while it has no verdict yet (#944): the ledger keeps a run with a verdict
# until its issue closes, and a failed run leaves its issue open, so reading the file's line count held every proof on a busy
# loop and stg fell further behind dev each hour. A line the pass cannot resolve is still read as live, the safe way.
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
now=$(date +%s); idle=${SG_PROMOTE_LIVE_IDLE:-1200}; nlive=0
while IFS= read -r r; do
  [ -n "$r" ] || continue
  [ -d "$r" ] || { nlive=$((nlive + 1)); continue; }
  f=$r/record.jsonl
  [ -f "$f" ] || { nlive=$((nlive + 1)); continue; }
  /usr/bin/grep -q '"verdict"' "$f" 2>/dev/null || { nlive=$((nlive + 1)); continue; }
  [ $((now - $(mtime "$f"))) -lt "$idle" ] && nlive=$((nlive + 1))
done < "$live" 2>/dev/null
# A live run is not a reason to wait: the line count was a proxy for load, and the load is measured below, directly, and again
# while the suite runs. A proxy that never opens held every proof on a busy loop and froze stg while dev took landings all day
# (#944). Two facts do still refuse the tick: the memory left on the box, and a nightly that already owns the machine -- two full
# suites at once saturate memory before either shows in the load average.
memfree=$(vm_stat 2>/dev/null | awk '/Pages free/ { gsub(/\./, "", $3); f = $3 } /Pages inactive/ { gsub(/\./, "", $3); i = $3 } END { print int((f + i) * 16384 / 1048576) }')
memfree=${memfree:-99999}; memmin=${SG_PROMOTE_MEM_MIN_MB:-512}
nightly=$state/locks/nightly
if awk -v l="$load1" -v m="$limit" 'BEGIN { exit !(l + 0 >= m + 0) }' || [ "$memfree" -lt "$memmin" ] || [ -d "$nightly" ]; then
  l="promote-pass: $tip not proven: machine loaded (load $load1 on $cores cores, limit $limit, $memfree MB free, nightly $([ -d "$nightly" ] && echo held || echo free), $nlive runs alive)"
  echo "$(date -u +%FT%TZ) $l" >> "$log"; echo "$l"; exit 0
fi
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-pass: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
[ "$(g rev-parse origin/dev)" = "$tip" ] || exit 0   # dev moved under the read: a later pass takes the new tip
g merge-base --is-ancestor "$tip" origin/stg && exit 0   # the dev tip is already in stg
# A promotion leaves a merge commit in stg (#743), so stg is never an ancestor of the next dev tip: only the work stg holds and
# dev lacks decides. Any plain commit there is the owner's to resolve, and no suite is spent on it.
o=$(g rev-list --no-merges "$tip..origin/stg" 2>/dev/null) || o=
[ -z "$o" ] || { mark "$tip" "not promoted: stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the dev tip lacks; the owner resolves it"; exit 1; }
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { mark "$tip" "not promoted: the clean tree could not check out the tip"; exit 1; }
SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SG_PROMOTE_HOLD_SECONDS:-10800} \
  bash "$d/serial.sh" full-proof bash "$0" --hold "$tip" 2>"$state/promote-pass.err"; rc=$?
[ $rc = 73 ] && exit 0   # another proof holds the proof lock: this tick is skipped, never queued
cat "$state/promote-pass.err" >&2; exit $rc
