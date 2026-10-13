#!/bin/bash
# Monitor stream: one line per run verdict, new PR, or review+CI summary. Run from the target checkout.
# active-runs.txt holds a run id (under ./runs) or an absolute run folder (launch.py writes absolute paths).
. "$(dirname "$0")/env.sh"
# One watch.sh per checkout, whoever started it (#1872). The lock is keyed on this script's resolved path, NOT on
# $SG_STATE: `watchd.sh start` under a temp SG_STATE walks past watchd's own pidfile guard and would otherwise spawn a
# second watch.sh on the live path, which polls the real repository beside the supervised one and doubles every read.
# Same shape as single.sh: a directory holding the holder's pid, stale when the pid is gone. Taken before the first gh
# call, so a refused process never reads GitHub at all.
wme="$(cd "$(dirname "$0")" && pwd -P)/watch.sh"
wlock="$SG_STATE_BASE/watch-locks/$(printf '%s' "$wme" | tr -c 'A-Za-z0-9' '_')"   # the seam, never the path: the lock is keyed on this script, not on $SG_STATE -- a second start under a temp SG_STATE would otherwise walk past the holder and poll the same repository beside it
mkdir -p "$(dirname "$wlock")"
if ! mkdir "$wlock" 2>/dev/null; then
  wh=$(cat "$wlock/pid" 2>/dev/null)
  if [ -n "$wh" ] && ! kill -0 "$wh" 2>/dev/null; then rm -rf "$wlock"; mkdir "$wlock" 2>/dev/null || exit 0   # stale: take over
  elif [ -z "$wh" ] && [ $(( $(date +%s) - $(stat -f %m "$wlock" 2>/dev/null || echo 0) )) -gt 10 ]; then rm -rf "$wlock"; mkdir "$wlock" 2>/dev/null || exit 0   # no pid after 10 s: owner died between mkdir and write
  else echo "watch.sh already running pid=$wh for $wme"; exit 0; fi
fi
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT; trap 'rm -rf "$wlock"; exit 0' INT TERM   # a TERM'd loop stops, it does not keep polling without its lock
# ONE reader owns every GitHub read of this cycle (#1893, ADR-0277): ghread.py answers in exactly three outcomes -- the
# answer in stdout with exit 0, a refusal naming GitHub's own message and request id with exit 2, and a failure before
# GitHub reached a verdict with exit 1 -- and the body of a refusal is never on stdout as data. `gh api` writes the error
# body to stdout and exits non-zero, and it does not honour -q on a failed request, so every `gh api … 2>/dev/null` here
# used to treat a 403/404/500 body as a valid answer: on 2026-10-12 a rate-limit body became the open-PR list and the
# loop below read each word of the JSON message as a PR number.
#
# The outcome travels in the exit code with its line in stdout, never in a variable: every call site below reads the
# list inside `$( )`, and a subshell cannot set a variable in its caller, so a refusal kept in `$gh_api_err` reached the
# log as `the open-PR list is not a list: ;` -- GitHub's own words lost, the empty tail read as "no PRs".
gh_api() {
  local route=$1; shift
  local q=""; [ "${1:-}" = "-q" ] && q=${2:-}
  local o rc
  o=$(python3 "$(dirname "$0")/ghread.py" "$route"); rc=$?
  if [ "$rc" -eq 0 ]; then
    if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q"; else printf '%s' "$o"; fi
    return 0
  fi
  printf '%s' "$o"   # the refusal/failure line: stdout is the only channel a subshell has
  return "$rc"
}
# reported verdicts / stalls persist across restarts ($W); a first start (no file) records current verdicts silently
W="$SG_STATE/watch-seen.txt"; boot=0; [ -e "$W" ] || { boot=1; : > "$W"; }
seen=" $(sed -n 's/^seen //p' "$W" | tr '\n' ' ')"; stalled=" $(sed -n 's/^stall //p' "$W" | tr '\n' ' ')"
# the set of PRs already announced is only ever built from an answer (#1870, #1893): a read that did not answer leaves it
# as it was instead of filling it with the words of the error body, which is what silently swallowed the real PRs for the
# life of the process. The refusal is named in the log from the module's own line, which carries GitHub's message and
# request id -- a caller that only ever learns from an answer cannot clear what it already knew.
prs=" "
if pl=$(gh_api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number'); then prs=" $(printf '%s\n' "$pl" | tr '\n' ' ')"
else echo "WATCH: the open-PR list is not readable, keeping the last known set: $pl"; fi
for f in "$SG_STATE"/revci-*.out; do grep '^#' "$f" 2>/dev/null | tail -1; done | sort > "$SG_STATE/.rs"
# tm <cmd...>: runs a step and appends "HH:MM:SS <step> <secs>s" to $SG_STATE/cycle.log when it took 5 s or more (where the 90 s cycle goes)
tm() { local s=$(date +%s); "$@"; local e=$(( $(date +%s) - s )); [ "$e" -ge 5 ] && echo "$(date +%T) ${1##*/} ${e}s" >> "$SG_STATE/cycle.log"; return 0; }
while :; do
  c0=$SECONDS
  for r in $(cat "$SG_STATE/active-runs.txt"); do case "$seen" in *" $r "*) continue;; esac
    v=$(python3 -c "import json,sys
landed=0; v=None
for l in open((sys.argv[1] if sys.argv[1].startswith('/') else 'runs/'+sys.argv[1])+'/record.jsonl'):
  d=json.loads(l)
  if d.get('event')=='land' and d.get('outcome')=='pass': landed=1
  if d.get('event')=='verdict': v=d
if v:
  rs=v.get('reasons') or []
  if v['verdict']=='failed' and landed and rs and all(x.startswith('ac-verify:no_ac_commands') for x in rs): print('landed (gate: no_ac_commands)')
  else: print(v['verdict'], ','.join(rs))" $r 2>/dev/null | tail -1)
    [ -n "$v" ] && { [ $boot = 1 ] || { echo "RUN ${r##*/} verdict=$v"
        # the run's verdict goes on every issue it worked (issue numbers read from the run's first records)
        rd=$r; [ "${r#/}" = "$r" ] && rd=runs/$r
        case "$v" in failed*) "$(dirname "$0")/retryfix.sh" "$rd";; esac   # a failed autofix run re-arms its PR sha (max 2)
        case "$v" in failed*no_dispatch*) "$(dirname "$0")/consult.sh" "$rd" >> "$SG_STATE/consult.log" 2>&1 & ;; esac   # router hesitated: ask the coding model once (ADR-free loop rule)
        for n in $(head -c 200000 "$rd/record.jsonl" 2>/dev/null | grep -oE '(issue|Refs) #[0-9]{3,5}' | grep -oE '[0-9]+' | sort -u); do
          printf "Squad run %s finished: verdict %s. Refs #%s\n" "${r##*/}" "$v" $n > "$SG_STATE/cq/$n-run$$-${r##*-}.md"; done; }
      seen="$seen$r "; echo "seen $r" >> "$W"; }; done
  # a refusal is not an open-PR list (#1870): the words of an error body are never PR numbers, the per-PR follow-up is
  # read only after the list answered, and a PR the follow-up could not read stays unannounced for the next cycle
  if lp=$(gh_api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number'); then
    for n in $lp; do case "$n" in ''|*[!0-9]*) continue;; esac; case "$prs" in *" $n "*) ;; *)
      t=$(gh_api repos/$SG_REPO/pulls/$n -q '.head.ref+" "+.title+"\t"+(.body//"")') \
        || { echo "WATCH: PR #$n is not readable: $t"; continue; }
      echo "NEW PR #$n ${t%%$'\t'*}"; prs="$prs$n "
      for i in $(printf '%s' "$t" | grep -oE 'Refs #[0-9]{3,5}' | grep -oE '[0-9]+' | sort -u); do printf "PR #%s opened: %s. Refs #%s\n" $n "${t%%$'\t'*}" $i > "$SG_STATE/cq/$i-pr$n.md"; done;; esac; done
  else echo "WATCH: the open-PR list is not readable, keeping the last known set: $lp"; fi
  for f in "$SG_STATE"/revci-*.out; do grep '^#' "$f" 2>/dev/null | tail -1; done | sort > "$SG_STATE/.rn"
  comm -13 "$SG_STATE/.rs" "$SG_STATE/.rn" | sed 's/^/REVIEW /'; cp "$SG_STATE/.rn" "$SG_STATE/.rs"
  # stall watchdog: live run whose record.jsonl has not moved for stall_timeout_minutes; review with no summary after review_stall_minutes
  cfg="$(dirname "$0")/models.json"; st=$(jq -r .stall_timeout_minutes "$cfg"); rs=$(jq -r .review_stall_minutes "$cfg")
  for r in $(cat "$SG_STATE/active-runs.txt"); do case "$seen" in *" $r "*) continue;; esac; case "$r" in /*) f=$r/record.jsonl;; *) f=runs/$r/record.jsonl;; esac
    [ -e "$f" ] && [ -n "$(find "$f" -mmin +$st 2>/dev/null)" ] && case "$stalled" in *" $r "*) ;; *) echo "STALL RUN ${r##*/} no record.jsonl progress for ${st}m"; "$(dirname "$0")/retryfix.sh" "$r" --stalled; stalled="$stalled$r "; echo "stall $r" >> "$W";; esac; done
  for f in "$SG_STATE"/revci-*.out; do [ -e "$f" ] || continue; grep -q '^#' "$f" && continue; k=$(basename "$f" .out)
    [ -n "$(find "$f" -mmin +$rs 2>/dev/null)" ] && case "$stalled" in *" $k "*) ;; *) echo "STALL REVIEW ${k#revci-} no result for ${rs}m"; stalled="$stalled$k "; echo "stall $k" >> "$W";; esac; done
  tick=$((tick+1))
  # reconcile.py is the most expensive step of the cycle by a wide margin (137 runs, 22,902 s, ~167 s each) and all it does is move cards
  # from GitHub facts: detached exactly as forkclose.sh below, under its own single.sh lock, filing its stream in $SG_STATE/reconcile.log
  # instead of holding the cycle (#1845). A pass already running is skipped, not doubled.
  [ $((tick % 8)) -eq 1 ] && ( bash "$(dirname "$0")/single.sh" reconcile env RECONCILE=apply python3 "$(dirname "$0")/reconcile.py" 2>&1 | sed "s/^/BOARD /" >> "$SG_STATE/reconcile.log" & ) 2>/dev/null   # card Status from GitHub facts, every ~12 min (GraphQL budget)
  # fork-only Code Complete: merge into the fork base, fork suite, close. In the background under single.sh: the fork suite waits for the GUI lock for minutes and must not hold the cycle (#1239)
  [ -n "${SG_FORKCLOSE_PAUSED:-}" ] || ( tm bash "$(dirname "$0")/single.sh" forkclose "$(dirname "$0")/forkclose.sh" 2>&1 & ) 2>/dev/null
  tm python3 "$(dirname "$0")/forkrescue.py" 2>&1   # a fork-only run that ended needs-human/fail with its commit off gui/<N>: re-point it, Code Complete (#1411)
  tm python3 "$(dirname "$0")/forkstrand.py" 2>&1   # after the rescue: a fork commit on a branch no landing path reads, for a card at the landing stage, is reported once per head (#1837)
  fast=; [ -s "$SG_STATE/fastloop.pid" ] && kill -0 "$(cat "$SG_STATE/fastloop.pid")" 2>/dev/null && fast=1   # watchd runs fastloop.sh: childcc, autopilot and both dispatch passes every 30 s outside this cycle
  # The fast lane owns childcc, autopilot, autofrontier and autobug; this cycle runs them only when that lane is down, so neither lane
  # runs a step twice. The frontier and bug dispatches ride the fast beat because a dispatch that waits for this cycle's tick appears up
  # to ~6 min late, which is what frontier-idle kept reporting (#1845). Every step moved here keeps its own single.sh lock, so a pass
  # already running in the other lane is skipped rather than started a second time.
  [ -n "$fast" ] || { tm python3 "$(dirname "$0")/childcc.py" 2>&1; tm "$(dirname "$0")/autopilot.sh" 2>&1
    tm "$(dirname "$0")/autofrontier.sh" 2>&1   # dispatch the frontier as soon as a blocker falls
    tm "$(dirname "$0")/autobug.sh" 2>&1; }   # open bug issues dispatched automatically
  tm "$(dirname "$0")/autohunt.sh" 2>&1   # idle loop: one read-only bug hunt that files reproducible defects
  tm "$(dirname "$0")/heartbeat.sh"   # hung test hosts (0% CPU) and dead runs
  # worktree cleanup every third cycle (~4.5 min): the trees are almost always clean and ~17 s of every cycle is 10% of the loop (#1845).
  # An on-demand pass is unchanged: run scripts/wtclean.sh (the step is still self-contained, one pass over the worktrees).
  [ $((tick % 3)) -eq 1 ] && tm "$(dirname "$0")/wtclean.sh" 2>&1
  tm "$(dirname "$0")/janitor.sh" 2>&1   # leftover processes of finished runs, zombie runs, idle dashboards, stale dispatch markers   # finished, pushed child worktrees
  tm bash "$(dirname "$0")/single.sh" vendorguard bash "$(dirname "$0")/vendor-guard.sh" 2>&1   # re-vendor drifted personal skills (a drifted vendor makes every squad start refuse)
  boot=0; for _ in 1 2; do sleep 30; [ -n "$fast" ] || { tm python3 "$(dirname "$0")/childcc.py" 2>&1; tm "$(dirname "$0")/autopilot.sh" 2>&1; }; done; sleep 30
  echo "$(date +%T) CYCLE $((SECONDS - c0))s" >> "$SG_STATE/cycle.log"; done   # childcc, autopilot and both dispatches every 30 s (fastloop.sh when watchd runs it), the rest every 90 s
