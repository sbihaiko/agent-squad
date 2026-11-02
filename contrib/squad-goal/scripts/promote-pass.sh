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
  # The proof holds nothing by default (#739 with ADR-0157 rule 1 rewritten, owner decision 2026-10-31): the dispatchers read
  # the marker (gh_dispatch_hold) and start no run while it stands, so a standing marker costs run slots. The owner named
  # parallelism as the priority, so the default bound is zero and no marker is written; an owner who wants the measurement
  # shielded names a bound in SG_PROMOTE_RESERVE_SECONDS. The trap removes a named marker on exit, INT and TERM.
  # ADR-0163: one bound covers the whole attempt, never one step of it (Astra review, 2026-11-01). The delta proof, the
  # fallback and the promotion all spend from the same fifteen minutes; a bound on the suite alone would let an attempt run
  # the suite to its bound and only then start the fallback.
  # ADR-0164: delivery outranks the proof. An unfinished proof never holds a landing; only a bound the delta cannot carry
  # reaches the indeterminate branch below, and that branch never promotes a tip a test named red.
  t0=$(date +%s); bound=${SG_PROMOTE_BOUND_SECONDS:-900}; keep=${SG_PROMOTE_FALLBACK_SECONDS:-240}
  reserve_s=${SG_PROMOTE_RESERVE_SECONDS:-0}
  [ "$reserve_s" -gt 0 ] && { printf '%s %s\n' "$(date +%s)" "$$" > "$state/promote-reserve.$$" && mv "$state/promote-reserve.$$" "$state/promote-reserve"; }
  # The proof samples the load while it runs, not only at the start: a red taken with the box over its bound is no proof at all,
  # so it writes no status and no marker and the same tip is retried on a quieter pass (ADR-0018 stays whole: a red taken under
  # the bound is a real defect). The sample file is named for this process and removed with the reserve.
  samples=$state/promote-load.$$
  ( while :; do sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' | tr -d '{}' >> "$samples"; sleep 10; done ) & mon=$!
  trap 'kill "$mon" 2>/dev/null; rm -f "$state/promote-reserve" "$samples"' EXIT INT TERM
  [ "$(g rev-parse HEAD)" = "$tip" ] || { mark "$tip" "not promoted: the clean tree is not at the tip"; exit 1; }
  # ADR-0164 (#1051, #1062): the check job of ci.yml on the tip, plus every full-proof shard that file names, is the full
  # pass when all are green. The shard job is withdrawn while #1062 is open, so the names come from the file and a restored
  # matrix is read with no second change. Green writes the full line and the squad/full-proof status, then promotes with no
  # suite; a red job holds the tip and names it; a job that is missing, running or timed out holds nothing, and the proof
  # below answers as before.
  # SG_CI_WORKFLOW names the workflow file the shard names are read from: the repository's own by default, a fixture in a test.
  ci_file=${SG_CI_WORKFLOW:-$d/../../../.github/workflows/ci.yml}
  shard_list=$(/usr/bin/grep -o 'shard: \[[0-9, ]*\]' "$ci_file" 2>/dev/null | tr -dc '0-9 ')
  full_status() {  # <tip> <description>: the squad/full-proof status on the sha, read back; the reason is printed when it does not stand
    local o st
    o=$(gh_send -X POST "repos/$SG_REPO/statuses/$1" -f state=success -f "context=$ctx" -f "description=$2") \
      || { echo "the commit status write failed: $(printf '%s' "$o" | tail -1)"; return 1; }
    st=$(gh_get "repos/$SG_REPO/commits/$1/statuses" | jq -r --arg c "$ctx" '[.[] | select(.context == $c)][0].state // empty' 2>/dev/null)
    [ "$st" = success ] || { echo "the commit status $ctx did not read back as success (${st:-missing})"; return 1; }
  }
  ci_verdict() {  # <tip>: pass, "fail <job>" for a red job, or pending
    local cr n st c v=pass sh names=("check")
    for sh in $shard_list; do names+=("full-proof shard $sh"); done   # none while the shard job is withdrawn (#1062)
    cr=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=latest") || { echo pending; return; }
    printf '%s' "$cr" | jq -e '.check_runs | type == "array"' >/dev/null 2>&1 || { echo pending; return; }
    for n in "${names[@]}"; do
      read -r st c < <(printf '%s' "$cr" | jq -r --arg n "$n" '[.check_runs[] | select(.name == $n)] | last | "\(.status) \(.conclusion)"')
      [ "$st" = completed ] && [ "$c" = failure ] && { echo "fail $n"; return; }
      { [ "$st" = completed ] && [ "$c" = success ]; } || v=pending
    done
    echo "$v"
  }
  v=$(ci_verdict "$tip")
  case "$v" in
    pass)
      bash "$d/gate.sh" --status full pass "$tip"
      why=$(full_status "$tip" "$([ -n "$shard_list" ] && echo "full suite, both modes, four CI shards and the check job" || echo "CI check job, both Pythons, the fast battery (#1062: the shards are withdrawn)")") || { mark "$tip" "not promoted: $why"; exit 1; }
      o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
      [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
      mark "$tip" "promoted: the CI shards carried the full pass"; exit 0;;
    fail*) mark "$tip" "not promoted: CI ${v#fail } is red"; exit 1;;
  esac
  # ADR-0156: a full pass carries across a covered delta. When one is on record and the tip moved only by covered paths, the
  # delta proof runs instead of the full suite and the promotion reads its record. Any refusal (an uncovered path, a bound, no
  # full pass) or a held delta lock falls through to the full suite below: the cheaper proof is an optimization, never a weaker gate.
  if bash "$d/delta-pass.sh" > "$state/delta-pass.log" 2>&1; then
    o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
    [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
    mark "$tip" "promoted: the delta carried the full pass ($state/delta-pass.log)"; exit 0
  fi
  # The interpreter holds the suite's tools (#1086, sg_gate_python in caps.sh). A default suite whose tool is missing refuses the
  # pass: the suite would die on a missing module, which reads as a red of the tip, a defect of the proof (ADR-0018). The
  # refusal writes no marker, so a later pass takes the same tip.
  py=$(sg_gate_python)
  [ -n "${SG_GATE_PY:-}" ] && { [ -x "$py" ] || { echo "promote-pass: SG_GATE_PY $py is not executable; nothing was proved" >&2; exit 2; }; }
  if [ -z "${SG_PROMOTE_SUITE:-}" ]; then
    miss=$(sg_missing_tool "$py")
    [ -z "$miss" ] || { echo "promote-pass: $py holds no $miss, so the suite would answer a red of the tip that is a red of the proof; nothing was proved" >&2; exit 2; }
  fi
  suite=${SG_PROMOTE_SUITE:-"$py -m ruff check squad tests && $py -m mypy squad && $py -m pytest -p no:deepeval -n ${SG_PROMOTE_WORKERS:-$(sg_battery_workers)} && $py -m pytest -p no:deepeval -p no:xdist"}
  out=$state/promote-suite-${tip:0:12}.log
  cores2=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1); lim2=${SG_PROMOTE_LOAD_LIMIT:-$cores2}
  # ADR-0163 (owner, 2026-11-01): no proof runs past fifteen minutes. Past its reserve bound the reserve lifts and the loop
  # dispatches beside it; past its deadline the suite and every child it started are killed, and the delta proof answers for
  # the tip instead. What the delta does not cover is proven hot: by the nightly and by the running system.
  kill_tree() { local p=$1 c; for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c"; done; kill "$p" 2>/dev/null; }
  # bounded <seconds> <cmd...>: runs the command and kills its tree past the bound; answers 124 when it was killed.
  bounded() { local b=$1 p s; shift; s=$(date +%s); "$@" & p=$!
    while kill -0 "$p" 2>/dev/null; do
      [ $(( $(date +%s) - s )) -ge "$b" ] && { kill_tree "$p"; wait "$p" 2>/dev/null; return 124; }
      sleep 2
    done
    wait "$p"; }
  # The suite is bounded at what the attempt has left once the fallback keeps its share, and the fallback at what is left then.
  rbound=$reserve_s; dline=$(( bound - keep - ( $(date +%s) - t0 ) )); [ "$dline" -lt 5 ] && dline=5
  s0=$(date +%s); (cd "$tree" && bash -c "$suite") > "$out" 2>&1 & spid=$!
  rc=0; relieved=
  while kill -0 "$spid" 2>/dev/null; do
    el=$(( $(date +%s) - s0 ))
    if [ "$rbound" -gt 0 ] && [ -z "$relieved" ] && [ "$el" -ge "$rbound" ]; then
      relieved=1; rm -f "$state/promote-reserve"
      echo "$(date -u +%FT%TZ) promote-pass: $tip passed its ${rbound}s reserve bound: the reserve lifts, the loop works beside the proof" >> "$log"
    fi
    [ "$el" -ge "$dline" ] && { rc=124; kill_tree "$spid"; break; }
    sleep 5
  done
  if [ "$rc" = 124 ]; then wait "$spid" 2>/dev/null; else wait "$spid"; rc=$?; fi
  if [ "$rc" = 124 ]; then
    kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
    # ADR-0163: the bound is not a verdict. The full suite is killed and the delta proof, which names the important part,
    # answers for the tip; the part it does not cover is proven hot, by the nightly and by the running system. Only a tip
    # the delta cannot carry stays indeterminate, and a later pass retries it.
    echo "$(date -u +%FT%TZ) promote-pass: $tip passed its ${dline}s bound: the full suite is killed and the delta proof answers" >> "$log"
    left=$(( bound - ( $(date +%s) - t0 ) )); [ "$left" -lt 60 ] && left=60
    if bounded "$left" bash "$d/delta-pass.sh" > "$state/delta-pass-over-bound.log" 2>&1; then
      o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rcd=$?
      [ $rcd = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
      mark "$tip" "promoted: the full suite was killed by the ${bound}s bound, the delta carried the tip ($state/delta-pass-over-bound.log)"; exit 0
    fi
    # ADR-0164 (#1046): a red the delta measured holds the tip as the full suite's red does; only an unfinished proof ships.
    IFS=$'\t' read -r _ dk dres _ dT _ 2>/dev/null < "$state/gate-status-delta"
    [ "$dk" = delta ] && [ "$dT" = "$tip" ] && [ "$dres" = fail ] && { mark "$tip" "not promoted: the delta suite failed ($state/delta-pass-over-bound.log)"; exit 1; }
    gap=$(sed -n 's/^delta-pass: uncovered paths: \(.*\); each needs a line.*/\1/p' "$state/delta-pass-over-bound.log" | tail -n 1)
    [ -n "$gap" ] || gap="every part of the tip (delta: $(tail -n 1 "$state/delta-pass-over-bound.log" | sed 's/^delta-pass: //'))"
    printf '%s\t%s\t%s\n' "$(date +%s)" "$tip" "$bound" > "$state/promote-indeterminate.$$" \
      && mv "$state/promote-indeterminate.$$" "$state/promote-indeterminate"
    echo "$(date -u +%FT%TZ) promote-pass: $tip not tested: the proof passed its ${bound}s bound and the delta could not carry it" >> "$log"
    o=$(SG_PROMOTE_PROOF=bound bash "$d/promote-stg.sh" 2>&1); rcb=$?
    [ $rcb = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$tip" "$bound" "$gap" >> "$state/promote-bound"
    mark "$tip" "promoted: bound-ended: the ${bound}s bound ended the full suite and the delta could not carry the tip; not covered: $gap"; exit 0
  fi
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
  why=$(full_status "$tip" "full suite, both modes, local") || { mark "$tip" "not promoted: $why"; exit 1; }
  o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
  [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
  mark "$tip" "promoted: the local full suite answered for the tip"; exit 0
fi
tip=$(/usr/bin/git ls-remote "$remote" refs/heads/dev | cut -f1); [ -n "$tip" ] || { echo "promote-pass: dev does not resolve on $remote" >&2; exit 1; }
/usr/bin/grep -q "^$tip " "$marks" 2>/dev/null && exit 0   # already proved or refused
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-pass: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
[ "$(g rev-parse origin/dev)" = "$tip" ] || exit 0   # dev moved under the read: a later pass takes the new tip
g merge-base --is-ancestor "$tip" origin/stg && exit 0   # the dev tip is already in stg
# ADR-0164 (#1051): the dev-to-stg pull request starts the four shards on the tip, so promote-stg.sh opens it before the quiet
# window passes and the shards run while it does. Best effort: a failure there leaves the proof below to answer.
SG_PROMOTE_OPEN_ONLY=1 bash "$d/promote-stg.sh" >/dev/null 2>&1
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
# A promotion leaves a merge commit in stg (#743), so stg is never an ancestor of the next dev tip: only the work stg holds and
# dev lacks decides. Any plain commit there is the owner's to resolve, and no suite is spent on it.
o=$(g rev-list --no-merges "$tip..origin/stg" 2>/dev/null) || o=
[ -z "$o" ] || { mark "$tip" "not promoted: stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the dev tip lacks; the owner resolves it"; exit 1; }
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { mark "$tip" "not promoted: the clean tree could not check out the tip"; exit 1; }
SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SG_PROMOTE_HOLD_SECONDS:-10800} \
  bash "$d/serial.sh" full-proof bash "$0" --hold "$tip" 2>"$state/promote-pass.err"; rc=$?
[ $rc = 73 ] && exit 0   # another proof holds the proof lock: this tick is skipped, never queued
cat "$state/promote-pass.err" >&2; exit $rc
