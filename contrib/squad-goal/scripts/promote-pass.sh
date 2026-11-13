#!/bin/bash
# usage: promote-pass.sh   The promotion pass (#459, ADR-0145 rule 3, #576): the watch cycle runs it once per quiet window
# under `single.sh promote`, and a hand run does the same; it earns the proof of the dev tip and promotes it. Its own
# full-proof serial hold means two passes never overlap, whatever started them. SG_STATE must be set: the pass never reads
# the machine-wide /tmp/sg-state, a different file from the profile's state directory (exit 2).
# Debounce: no suite for a tip already proved or refused ($SG_STATE/promote-pass.tips, last 50), nor while the latest landing in
# $SG_STATE/landed.tsv is younger than SG_PROMOTE_QUIET_SECONDS (default 900): a burst of landings earns one proof. A CI
# proof of a hand run skips that window (a landing costs a job on the runner nothing); the watch cycle carries its own
# debounce on that window for its step, bounded so a stream of landings cannot postpone the promotion for ever.
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
# One clock covers the whole attempt (#1171): from the proof lock, SG_PROMOTE_BOUND_SECONDS covers the suite, its retry and the
# delta. SG_PROMOTE_FALLBACK_SECONDS is reserved for the delta before the suite starts and is never taken by the suite, and the
# clock never restarts. Past the deadline every descendant is stopped, TERM then KILL. A defect a check reported before the
# deadline is a fail the bound never turns into a pass (#1170); an attempt that reported nothing is recorded bounded.
d=$(cd "$(dirname "$0")" && pwd)
dev=${SG_BASE:-dev}; stg=${SG_STG_BRANCH:-stg}   # the work branch and the promotion branch, both named by the profile (#1646)
[ "${SG_PROMOTE:-on}" = off ] && exit 0         # a profile that delivers by another path promotes nothing: no fetch, no line
# ci_job_names <workflow file> [gated]: the check-run name of every job under jobs:, one per line; with `gated`, only the
# jobs whose `if:` names base_ref. The reader stays inside the jobs: block (a line at column zero ends it), so a step's
# `name:`, a `with: name:` or any other four-space `name:` outside a job never becomes a check run whose absence would hold
# every tip forever (ADR-0164, #1322). Only a job that names itself is read; the full suite's shard jobs name themselves, so
# they are read like every other job (#1518). A file that names no job answers nothing, and its caller reads that as pending,
# never as a vacuous pass. A job gated on base_ref runs only on a pull request into that branch, so it never runs on the
# dev-to-stg pull request the promotion reads (#1461): the caller refuses the promotion and names it, never recording a pass
# over the jobs that did run.
ci_job_names() {
  awk -v want="${2:-}" '
    function flush() { if (cur != "" && (want != "gated" || gated)) print cur; cur = ""; gated = 0 }
    /^jobs:[[:space:]]*$/ { injobs = 1; next }
    /^[^[:space:]#]/ { if (injobs) flush(); injobs = 0 }
    !injobs { next }
    /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ { flush(); job = 1; next }
    /^    name:[[:space:]]+/ { if (!job) next; cur = $0; sub(/^    name:[[:space:]]+/, "", cur); next }
    /^    if:[[:space:]]/ && /base_ref/ { gated = 1 }
    END { flush() }
  ' "$1" 2>/dev/null
}
# The Statuses API takes a description no longer than 140 characters and refuses a longer one with a 422; the status would
# never stand and a green shard set would never promote (#1518). The description carries the names that fit and counts the
# rest; the pass writes the whole list to its log.
full_desc_max=140
full_prefix="full suite, both modes, every CI job"
full_status_desc() {  # <prefix> <check name...>: a status description the Statuses API takes, never longer than 140
  local d=$1 sep=": " n i=0 rest=0 total
  shift; total=$#
  for n in "$@"; do
    i=$((i + 1))
    # 13 characters stay reserved for the " and N more" tail, so the tail itself never pushes the line past the cap.
    if [ $(( ${#d} + ${#sep} + ${#n} + 13 )) -le "$full_desc_max" ]; then d="$d$sep$n"; sep=", "
    else rest=$(( total - i + 1 )); break; fi
  done
  if [ "$rest" -gt 0 ]; then
    if [ "$i" -gt 1 ]; then d="$d and $rest more"; else d="$d: $total jobs"; fi
  fi
  [ "${#d}" -le "$full_desc_max" ] || d=$(printf '%s' "$d" | cut -c1-"$full_desc_max")
  printf '%s' "$d"
}
if [ "${1:-}" = --ci-job-names ]; then   # the check names a workflow's job block produces, printed one per line
  [ -n "${2:-}" ] || { echo "promote-pass: --ci-job-names needs a workflow file" >&2; exit 2; }
  ci_job_names "$2"; exit 0
fi
if [ "${1:-}" = --ci-description ]; then   # the bounded status description a workflow's job list produces
  [ -n "${2:-}" ] || { echo "promote-pass: --ci-description needs a workflow file" >&2; exit 2; }
  ns=(); while IFS= read -r j; do [ -n "$j" ] && ns+=("$j"); done < <(ci_job_names "$2")
  full_status_desc "$full_prefix" ${ns[@]+"${ns[@]}"}; echo; exit 0
fi
[ -n "${SG_STATE:-}" ] || { echo "promote-pass: SG_STATE is not set; the pass never reads the machine-wide default" >&2; exit 2; }
state=$SG_STATE; export SG_STATE; mkdir -p "$state"; log=$state/promote.log; marks=$state/promote-pass.tips
tree=${SG_PROMOTE_TREE:-$state/promote-tree}; remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}; ctx=squad/full-proof
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
. "$d/caps.sh"
mark() {  # <tip> <reason>: one marker and one line per tip
  echo "$1 $2" >> "$marks"; tail -n 50 "$marks" > "$marks.tmp" && mv "$marks.tmp" "$marks"
  echo "$(date -u +%FT%TZ) promote-pass: $1 $2" >> "$log"; echo "promote-pass: $1 $2"; }
refused() {  # <tip> <reason>: logged and answered, never a marker: a transient is unproven, so the next pass takes the same tip (#1490)
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
  case $bound in ''|*[!0-9]*|0) bound=900;; esac
  case $keep in ''|*[!0-9]*) keep=240;; esac
  due=$(( t0 + bound ))   # one deadline for the whole attempt: the suite, its retry and the delta never restart the clock
  # The delta's slice is taken from the one budget before the gate starts, so the gate can never spend it: the suite stops
  # at due-keep, and the delta answers on what is left, which is its slice unless the suite ended early (ADR-0163, #1171).
  echo "$(date -u +%FT%TZ) promote-pass: delta slice reserved: ${keep}s of the ${bound}s budget" >> "$log"
  echo "promote-pass: the delta's ${keep}s slice of the ${bound}s budget is reserved"
  reserve_s=${SG_PROMOTE_RESERVE_SECONDS:-0}
  [ "$reserve_s" -gt 0 ] && { printf '%s %s\n' "$(date +%s)" "$$" > "$state/promote-reserve.$$" && mv "$state/promote-reserve.$$" "$state/promote-reserve"; }
  # The proof samples the load while it runs, not only at the start: a red taken with the box over its bound is no proof at all,
  # so it writes no status and no marker and the same tip is retried on a quieter pass (ADR-0018 stays whole: a red taken under
  # the bound is a real defect). The sample file is named for this process and removed with the reserve.
  samples=$state/promote-load.$$
  ( while :; do sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' | tr -d '{}' >> "$samples"; sleep 10; done ) & mon=$!
  trap 'kill "$mon" 2>/dev/null; rm -f "$state/promote-reserve" "$samples"' EXIT INT TERM
  # tree_pids <pid>: the pid and every descendant it started, children first.
  tree_pids() { local p=$1 c; for c in $(pgrep -P "$p" 2>/dev/null); do tree_pids "$c"; done; printf '%s\n' "$p"; }
  # stop_tree <pid>: stop the tree as it stands now, TERM then KILL, so a child that ignores TERM still goes. The list is
  # taken before the first signal: a child the parent leaves behind is reparented and can no longer be found from the parent.
  stop_tree() { local p=$1 list x
    list=$(mktemp "${TMPDIR:-/tmp}/pp-stop.XXXXXX" 2>/dev/null)
    if [ -z "$list" ]; then kill -TERM "$p" 2>/dev/null; sleep 1; kill -KILL "$p" 2>/dev/null; return 0; fi
    tree_pids "$p" > "$list"
    while read -r x; do [ -n "$x" ] && kill -TERM "$x" 2>/dev/null; done < "$list"; sleep 1
    while read -r x; do [ -n "$x" ] && kill -KILL "$x" 2>/dev/null; done < "$list"
    rm -f "$list"; }
  # bounded <seconds> <cmd...>: runs the command and stops its whole tree past the bound; answers 124 when it stopped it.
  bounded() { local b=$1 p s; shift; s=$(date +%s); "$@" & p=$!
    while kill -0 "$p" 2>/dev/null; do
      [ $(( $(date +%s) - s )) -ge "$b" ] && { stop_tree "$p"; wait "$p" 2>/dev/null; return 124; }
      sleep 1
    done
    wait "$p"; }
  # defected <log>: a check already reported a defect before the deadline, so the bound never turns it into a pass.
  defected() { /usr/bin/grep -qE '^(FAILED|ERROR) |errors? during collection|^[^ ]+:[0-9]+: error: |^[A-Z][A-Z]*[0-9]+ ' "$1"; }
  [ "$(g rev-parse HEAD)" = "$tip" ] || { mark "$tip" "not promoted: the clean tree is not at the tip"; exit 1; }
  # ADR-0164 (#1051, #1322, #1461, #1518): every job of ci.yml on the tip is the full pass when all are green, the full
  # suite's twelve shard jobs included. Both tiers run on the dev-to-stg pull request, so the complete tier is one of the
  # checks this pass reads; a job the file gates to another pull request is refused by name, never skipped. Green writes the
  # full line and the squad/full-proof status, then promotes with no suite; a red job holds the tip and names it; a job that
  # is missing, running or timed out holds nothing, and the proof below answers as before.
  # SG_CI_WORKFLOW names the workflow file the check names are read from: the repository's own by default, a fixture in a test.
  # The workflow file lives in the checkout the profile names, never beside the live copy: a live copy holds no
  # .github, so the old default read nothing and every tip waited for checks that were never named (#1646). A value
  # with a slash is a path as it stands, which is how a test names its fixture.
  case "${SG_CI_WORKFLOW:-}" in
    "") ci_file=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/ci.yml;;
    */*) ci_file=$SG_CI_WORKFLOW;;
    *) ci_file=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/$SG_CI_WORKFLOW;;
  esac
  full_status() {  # <tip> <description>: the squad/full-proof status on the sha, read back; the reason is printed when it does not stand
    local o st
    o=$(gh_send -X POST "repos/$SG_REPO/statuses/$1" -f state=success -f "context=$ctx" -f "description=$2") \
      || { echo "the commit status write failed: $(printf '%s' "$o" | tail -1)"; return 1; }
    st=$(gh_get "repos/$SG_REPO/commits/$1/statuses" | jq -r --arg c "$ctx" '[.[] | select(.context == $c)][0].state // empty' 2>/dev/null)
    [ "$st" = success ] || { echo "the commit status $ctx did not read back as success (${st:-missing})"; return 1; }
  }
  # The check names are read here, in this shell, not inside ci_verdict's command substitution: the record below names
  # the same list the verdict demanded. Every job of the file is a check the dev tip waits on, the complete tier included
  # (#1461). A job the file gates to another pull request never runs on this one, so the tip can never prove it: the pass
  # refuses by name instead of reading the rest as a full pass.
  ci_names=()
  while IFS= read -r n; do [ -n "$n" ] && ci_names+=("$n"); done < <(ci_job_names "$ci_file")
  ci_gated=()
  while IFS= read -r n; do [ -n "$n" ] && ci_gated+=("$n"); done < <(ci_job_names "$ci_file" gated)
  if [ "${#ci_gated[@]}" -gt 0 ]; then
    gated_list=$(printf '%s, ' "${ci_gated[@]}"); gated_list=${gated_list%, }
    mark "$tip" "not promoted: the workflow gates $gated_list to another pull request, so the dev tip never proves it"; exit 1
  fi
  ci_verdict() {  # <tip>: pass, "fail <check>" for a measured red, or pending
    local cr n st c v=pass ref
    [ "${#ci_names[@]}" -gt 0 ] || { echo pending; return; }   # a workflow that names no job proves nothing
    cr=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=latest") || { echo pending; return; }
    printf '%s' "$cr" | jq -e '.check_runs | type == "array"' >/dev/null 2>&1 || { echo pending; return; }
    . "$d/basecheck.sh"   # the same refinement the base check reads (#1629): a job that never started measured nothing
    ref=$(sg_ci_refine "$cr" 2>/dev/null) || ref=
    for n in "${ci_names[@]}"; do
      read -r st c < <(printf '%s' "$cr" | jq -r --arg n "$n" '[.check_runs[] | select(.name == $n)] | last | "\(.status) \(.conclusion)"')
      if [ "$st" = completed ] && [ "$c" = failure ]; then
        case "$ref" in
          red\ *) echo "fail $n"; return;;   # a job of the run ran a step and failed: the red job is the one read
          *) v=pending;;   # nothing measured the fail: the tip is unproven, never a pass
        esac
        continue
      fi
      { [ "$st" = completed ] && [ "$c" = success ]; } || v=pending
    done
    echo "$v"
  }
  v=$(ci_verdict "$tip")
  case "$v" in
    pass)
      bash "$d/gate.sh" --status full pass "$tip"
      # The record names the jobs just read from the workflow, never a hand-written copy of them: a renamed or extended
      # fast tier is recorded as it is, so the record never lies about what carried the pass (#1322). The status line is
      # bounded by the Statuses API, so the log carries every name and the status counts the ones it leaves out (#1518).
      ci_jobs=$(printf '%s, ' "${ci_names[@]}"); ci_jobs=${ci_jobs%, }
      echo "$(date -u +%FT%TZ) promote-pass: $tip CI checks green: $ci_jobs" >> "$log"
      why=$(full_status "$tip" "$(full_status_desc "$full_prefix" "${ci_names[@]}")") || { mark "$tip" "not promoted: $why"; exit 1; }
      # #1495: the CI proof is read on the tip promote-stg.sh promotes, which may be a later one than $tip when a landing moved
      # dev while the pass waited for the gate lock. A refusal is keyed to the tip it evaluated; a tip whose checks have not
      # reported (exit 75) is a wait with no marker, so a later pass retries.
      o=$(SG_PROMOTE_PROOF=ci bash "$d/promote-stg.sh" 2>&1); rc=$?
      etip=$(printf '%s\n' "$o" | sed -n 's/^promote-stg: tip //p' | tail -n 1); etip=${etip:-$tip}
      if [ $rc = 75 ]; then
        echo "$(date -u +%FT%TZ) promote-pass: $etip waiting for CI: the check has not reported on it yet" >> "$log"
        echo "promote-pass: $etip waiting for CI"; exit 0
      fi
      # (#1490) promote-stg.sh refused by the gate cap (try mode, exit 73) is a wait, never a verdict on the tip: no marker.
      [ $rc = 73 ] && { refused "$tip" "not proven: the gate cap is full, retried later"; exit 0; }
      [ $rc = 0 ] || { mark "$etip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
      mark "$etip" "promoted: the CI checks carried the full pass"; exit 0;;
    fail*) mark "$tip" "not promoted: CI ${v#fail } is red"; exit 1;;
  esac
  # (#1192) A tip whose CI has not answered yet is measured by waiting, never by a local suite. The suite saturates the machine
  # for eleven minutes, and the loop's own runs then die at the takeover bound, while the check that would have answered is
  # already in flight on the tip's pull request. Past SG_PROMOTE_CI_WAIT the suite answers as before, so a CI that never
  # reports never holds delivery. No marker is written: a later pass takes the same tip.
  age=$(( $(date +%s) - $(g show -s --format=%ct "$tip" 2>/dev/null || echo 0) ))
  wait_for=${SG_PROMOTE_CI_WAIT:-900}
  if [ "$age" -lt "$wait_for" ]; then
    echo "$(date -u +%FT%TZ) promote-pass: $tip waiting for CI: the check has not reported on it yet ($age s of ${wait_for}s)" >> "$log"
    echo "promote-pass: $tip waiting for CI"
    exit 0
  fi
  # ADR-0156: a full pass carries across a covered delta. When one is on record and the tip moved only by covered paths, the
  # delta proof runs instead of the full suite and the promotion reads its record. Any refusal (an uncovered path, a bound, no
  # full pass) or a held delta lock falls through to the full suite below: the cheaper proof is an optimization, never a weaker gate.
  dleft=$(( due - $(date +%s) )); [ "$dleft" -lt 1 ] && dleft=1
  if bounded "$dleft" bash "$d/delta-pass.sh" > "$state/delta-pass.log" 2>&1; then
    o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rc=$?
    { [ $rc = 75 ] || [ $rc = 73 ]; } && { refused "$tip" "not promoted yet: $(printf '%s' "$o" | tail -1)"; exit 0; }
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
  # The suite is bounded at what the attempt has left once the delta's reserved slice is kept; the delta answers on what is
  # left then. Neither bound is ever rounded up: the whole attempt stays inside the one budget.
  rbound=$reserve_s; dline=$(( t0 + bound - keep - $(date +%s) )); [ "$dline" -lt 1 ] && dline=1
  s0=$(date +%s); (cd "$tree" && bash -c "$suite") > "$out" 2>&1 & spid=$!
  rc=0; relieved=
  while kill -0 "$spid" 2>/dev/null; do
    el=$(( $(date +%s) - s0 ))
    if [ "$rbound" -gt 0 ] && [ -z "$relieved" ] && [ "$el" -ge "$rbound" ]; then
      relieved=1; rm -f "$state/promote-reserve"
      echo "$(date -u +%FT%TZ) promote-pass: $tip passed its ${rbound}s reserve bound: the reserve lifts, the loop works beside the proof" >> "$log"
    fi
    [ "$el" -ge "$dline" ] && { rc=124; stop_tree "$spid"; break; }
    sleep 5
  done
  if [ "$rc" = 124 ]; then wait "$spid" 2>/dev/null; else wait "$spid"; rc=$?; fi
  if [ "$rc" = 124 ]; then
    kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
    # ADR-0164 (#1170): a defect a check reported before the deadline is a measured red, and the bound never turns it into
    # a pass or a bounded attempt: it holds the tip exactly as a suite that ran to its summary would.
    if defected "$out"; then
      ids=$(/usr/bin/grep -E '^(FAILED|ERROR) ' "$out" | head -n 3 | tr '\n' ' ' | sed 's/ $//')
      bash "$d/gate.sh" --status full fail "$tip"; mark "$tip" "not promoted: the full suite passed its ${dline}s bound with $([ -n "$ids" ] && echo "the defect $ids" || echo "a defect") already reported ($out)"; exit 1
    fi
    # ADR-0163: the bound is not a verdict. The full suite is killed and the delta proof, which names the important part,
    # answers for the tip; the part it does not cover is proven hot, by the nightly and by the running system. Only a tip
    # the delta cannot carry stays indeterminate, and a later pass retries it.
    echo "$(date -u +%FT%TZ) promote-pass: $tip passed its ${dline}s bound: the full suite is killed and the delta proof answers" >> "$log"
    left=$(( due - $(date +%s) )); [ "$left" -lt 1 ] && left=1
    if bounded "$left" bash "$d/delta-pass.sh" > "$state/delta-pass-over-bound.log" 2>&1; then
      o=$(SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" 2>&1); rcd=$?
      { [ $rcd = 75 ] || [ $rcd = 73 ]; } && { refused "$tip" "not promoted yet: $(printf '%s' "$o" | tail -1)"; exit 0; }
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
    { [ $rcb = 75 ] || [ $rcb = 73 ]; } && { refused "$tip" "not promoted yet: $(printf '%s' "$o" | tail -1)"; exit 0; }
    [ $rcb = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$tip" "$bound" "$gap" >> "$state/promote-bound"
    mark "$tip" "promoted: bound-ended: the ${bound}s bound ended the full suite and the delta could not carry the tip; not covered: $gap"; exit 0
  fi
  if [ "$rc" != 0 ]; then
    kill "$mon" 2>/dev/null; wait "$mon" 2>/dev/null
    # (#1490) A full machine cap is a wait, not a red of the tip: the suite never ran, so no fail line and no marker.
    if [ "$rc" = 73 ] || { /usr/bin/grep -q 'caps: caps.gates is full' "$out" && /usr/bin/grep -q 'skipped, not queued (try)' "$out"; }; then
      refused "$tip" "not proven: the gate cap is full, retried later"; exit 0
    fi
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
  { [ $rc = 75 ] || [ $rc = 73 ]; } && { refused "$tip" "not promoted yet: $(printf '%s' "$o" | tail -1)"; exit 0; }
  [ $rc = 0 ] || { mark "$tip" "not promoted: $(printf '%s' "$o" | tail -1)"; exit 1; }
  mark "$tip" "promoted: the local full suite answered for the tip"; exit 0
fi
tip=$(/usr/bin/git ls-remote "$remote" "refs/heads/$dev" | cut -f1); [ -n "$tip" ] || { echo "promote-pass: $dev does not resolve on $remote" >&2; exit 1; }
/usr/bin/grep -q "^$tip " "$marks" 2>/dev/null && exit 0   # already proved or refused
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/$dev:refs/remotes/origin/$dev" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) || { echo "promote-pass: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
[ "$(g rev-parse "origin/$dev")" = "$tip" ] || exit 0   # the work branch moved under the read: a later pass takes the new tip
g merge-base --is-ancestor "$tip" "origin/$stg" && exit 0   # the tip is already in the promotion branch
# ADR-0164 (#1051, #1461): the dev-to-stg pull request starts both CI tiers on the tip, so promote-stg.sh opens it before the
# quiet window passes and the checks run while it does. Best effort: a failure there leaves the proof below to answer.
SG_PROMOTE_OPEN_ONLY=1 bash "$d/promote-stg.sh" >/dev/null 2>&1
last=$(awk -F'\t' '$1 > m {m = $1} END {print m + 0}' "$state/landed.tsv" 2>/dev/null); last=${last:-0}
# ADR-0164: the guards below protect a local suite from a loaded box. When SG_PROMOTE_PROOF=ci names the dev-to-stg check as
# the proof, no local suite runs, so a recent landing, a loaded box, a low-memory box and a nightly that owns the machine
# refuse nothing: a landing costs a local suite its measurement and costs a job on GitHub's runner nothing.
[ "${SG_PROMOTE_PROOF:-}" = ci ] \
  || [ $(( $(date +%s) - last )) -ge "${SG_PROMOTE_QUIET_SECONDS:-900}" ] || exit 0   # a landing is still recent: wait for the burst to end
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
loaded=0
awk -v l="$load1" -v m="$limit" 'BEGIN { exit !(l + 0 >= m + 0) }' && loaded=1
[ "$memfree" -lt "$memmin" ] && loaded=1
[ -d "$nightly" ] && loaded=1
if [ "$loaded" = 1 ] && [ "${SG_PROMOTE_PROOF:-}" != ci ]; then
  l="promote-pass: $tip not proven: machine loaded (load $load1 on $cores cores, limit $limit, $memfree MB free, nightly $([ -d "$nightly" ] && echo held || echo free), $nlive runs alive)"
  echo "$(date -u +%FT%TZ) $l" >> "$log"; echo "$l"; exit 0
fi
# A promotion leaves a merge commit in stg (#743), so stg is never an ancestor of the next dev tip: only the work stg holds and
# dev lacks decides. Any plain commit there is the owner's to resolve, and no suite is spent on it.
o=$(g rev-list --no-merges "$tip..origin/$stg" 2>/dev/null) || o=
[ -z "$o" ] || { mark "$tip" "not promoted: $stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the $dev tip lacks; the owner resolves it"; exit 1; }
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { mark "$tip" "not promoted: the clean tree could not check out the tip"; exit 1; }
SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SG_PROMOTE_HOLD_SECONDS:-10800} \
  bash "$d/serial.sh" full-proof bash "$0" --hold "$tip" 2>"$state/promote-pass.err"; rc=$?
[ $rc = 73 ] && exit 0   # another proof holds the proof lock: this tick is skipped, never queued
cat "$state/promote-pass.err" >&2; exit $rc
