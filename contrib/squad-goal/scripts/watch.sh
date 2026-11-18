#!/bin/bash
# Monitor stream: one line per run verdict. Run from the target checkout.
# active-runs.txt holds a run id (under ./runs) or an absolute run folder (launch.py writes absolute paths).
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"
# One dated record for every step this cycle starts (#1750), in $SG_STATE/steps.tsv: a start line, then a finish line that
# names the state it ended in. A copy of this script that travels without the writer (a fixture) runs as before: a missing
# record never stops a step.
. "$(dirname "$0")/steprec.sh" 2>/dev/null || { srec_start() { srec_id=; }; srec_end() { :; }; srec_skip() { :; }; }
orphaned() { [ "$($SG_PS -o ppid= -p $$ | tr -d ' ')" = 1 ]; }   # the supervisor is gone (#959): this lane runs no further step
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# One watch.sh per repository. Its lock lives in that repository's own state directory, so one supervisor never pauses another.
wme="$(cd "$(dirname "$0")" && pwd -P)/watch.sh"
wlock="$SG_STATE/locks/watch"
mkdir -p "$(dirname "$wlock")"
if ! mkdir "$wlock" 2>/dev/null; then
  wh=$(cat "$wlock/pid" 2>/dev/null)
  if [ -n "$wh" ] && ! kill -0 "$wh" 2>/dev/null; then rm -rf "$wlock"; mkdir "$wlock" 2>/dev/null || exit 0   # stale: take over
  elif [ -z "$wh" ] && [ $(( $(date +%s) - $(mtime "$wlock") )) -gt 10 ]; then rm -rf "$wlock"; mkdir "$wlock" 2>/dev/null || exit 0   # no pid after 10 s: owner died between mkdir and write
  else echo "watch.sh already running pid=$wh for $wme"; exit 0; fi
fi
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT; trap 'rm -rf "$wlock"; exit 0' INT TERM   # a TERM'd loop stops, it does not keep polling without its lock
# ONE door owns every GitHub read of this cycle (ghcall.sh, #1893, ADR-0277): ghread.py answers in exactly three outcomes --
# the answer in stdout with exit 0, a refusal naming GitHub's own message and request id with exit 2, and a failure before
# GitHub reached a verdict with exit 1 -- and the body of a refusal is never on stdout as data. `gh api` writes the error
# body to stdout and exits non-zero, and it does not honour -q on a failed request, so a bare `gh api … 2>/dev/null`
# treated a 403/404/500 body as a valid answer. The only GitHub call left in this cycle is the quota stretch at its end.
. "$(dirname "$0")/ghcall.sh"
# reported verdicts / stalls persist across restarts ($W); a first start (no file) records current verdicts silently
W="$SG_STATE/watch-seen.txt"; boot=0; [ -e "$W" ] || { boot=1; : > "$W"; }
seen=" $(sed -n 's/^seen //p' "$W" | tr '\n' ' ')"; stalled=" $(sed -n 's/^stall //p' "$W" | tr '\n' ' ')"
# tm <cmd...>: runs a step and appends "HH:MM:SS <step> <secs>s" to $SG_STATE/cycle.log when it took 5 s or more (where the 90 s cycle goes).
# The step's own exit code comes back unchanged, so a caller tells a held lock (single.sh exits 76, #1290) from a step that failed.
# Whatever its length, a step also writes its dated record (#1750): cycle.log's 5 s rule is where the cycle's time went, and
# the record is what says the step ran at all. The finish reads the epoch the step began at plus its own length, so a step
# costs one date call, never two. One line: test-watch-tm.sh evals this line alone.
tm() { orphaned && exit 0; local s r n e sid; n=${1##*/}; [ "$n" = bash ] && { n=${2##*/}; [ "$n" = single.sh ] && n=$3; }; n=${n##*/}; s=$(date +%s); srec_start "$n" "$s"; sid=$srec_id; "$@"; r=$?; e=$(( $(date +%s) - s )); srec_end "$sid" "$n" "$r" $(( s + e )); [ "$e" -ge 5 ] && echo "$(date +%T) ${n##*/} ${e}s" >> "$SG_STATE/cycle.log"; return $r; }
# verdicts: the runs of active-runs.txt whose record carries a verdict, one a line. The closing wait reads local files only (#569).
verdicts() { local r f; for r in $(cat "$SG_STATE/active-runs.txt"); do case "$r" in /*) f=$r/record.jsonl;; *) f=runs/$r/record.jsonl;; esac
  grep -q '"event": *"verdict"' "$f" 2>/dev/null && echo "$r"; done | sort; }
# promote_quiet: the promotion step's own debounce (ADR-0145 rule 3): the latest landing in landed.tsv is older than the
# quiet window, so a burst of landings earns one proof window instead of one pass per landing. The wait is bounded: the
# first cycle the window held leaves its stamp in promote-defer, and a pass starts once that stamp is older than
# SG_PROMOTE_MAX_DEFER (default: twice the window), so a stream of landings that never quiets postpones the proof by that
# bound and no longer. A bound of 0 keeps no bound.
. "$(dirname "$0")/promotequiet.sh"   # the debounce, one definition, read here and by the CI event hook (#1837)
# land_wave <run>: the completion of one finished run -- the run's finished children enter its wave, and then the wave lands
# (#375, ADR-0145 rules 9, 10 and 12). The order is the point: a child enters the wave in childcc's pass, which this cycle
# reaches after this loop, so a landing attempted first finds a wave without the child in it, refuses it, and -- the run
# being read once -- the child that enters afterwards stays on wave/<run> and never reaches $SG_BASE (#375 review). The
# childcc pass runs inline under the childcc lock, the one fastloop.sh takes, and waits there a bounded time (SG_LAND_WAIT).
# The landing then runs in its OWN detached pass (#955): the cycle never waits for a gate, so a landing that queues behind
# another gate costs the cycle nothing. That pass is single-flight per run (its busy marker names it while it lives), and a
# pass still alive is waited for, never doubled. `wland-<run>` is the marker the pass leaves once the landing succeeded; the
# cycle reads it on the next cycle and only then reads the run. So a run is read only when its children had their pass and
# the wave is in $SG_BASE: a pass still running (the run stays unread), a pass that failed (its traceback is in this log), a
# childcc pass that left children for later (#590) and a landing that failed each leave the run unread, never a marker.
land_wave() {   # <run>: 0 the wave is done (landed), 1 the pass that enters its children, or the landing, did not get through, so the run stays unread
  local run=$1 ran pass L h c lk
  ran="$SG_STATE/auto/wland-${run##*/}"; pass="$ran.pass"; L="$SG_STATE/locks/single-childcc"; lk="$ran.busy"
  mkdir -p "$SG_STATE/auto"
  # Only a defect in the landing path stops this landing: a `loop-bug:landing` issue names it, so the wave waits and the
  # run stays unread until it closes (#641, ADR-0146 decision 3). A defect in the dispatch path stops intake only.
  gh_land_stop && return 1
  # The landing pass of this run is still alive: this cycle waits for it and starts no second one.
  h=$(cat "$lk" 2>/dev/null)
  if [ -n "$h" ] && kill -0 "$h" 2>/dev/null; then
    echo "WATCH: ${run##*/} waits: its landing is in its own pass (pid $h), so its wave is read on a later cycle"; return 1
  fi
  rm -f "$lk"
  # The detached pass landed its wave: `wland-<run>` is its success marker, read here once and then cleared.
  if [ -e "$ran" ]; then rm -f "$ran" "$pass" "$pass.left" "$ran.queued"; return 0; fi
  # A run waits in the landing queue (#2058) only after the childcc pass of this cycle got through: the pass below writes it again.
  rm -f "$pass" "$pass.left" "$ran.queued"
  # The pass waits a bounded time (SG_LAND_WAIT one-second polls) for the childcc lock the fast lane may hold. The landing runs
  # under its own lock, never the childcc one: its gate wait can last hours, and the fast lane's pass must not queue behind it (#471).
  # A pass works one child and exits 3 when it left one for a later pass (#590): that is no pass, `$pass.left` says so, and the
  # wave waits, as a 0 would land it without that child.
  SG_SINGLE_WAIT=${SG_LAND_WAIT:-30} tm bash "${wme%/*}/single.sh" childcc sh -c 'python3 "$1" "$2"; r=$?; [ $r = 0 ] && : > "$3"; [ $r = 3 ] && : > "$3.left"; exit $r' landpass \
     "${wme%/*}/childcc.py" "$run" "$pass" 2>&1
  [ -e "$pass.left" ] && { rm -f "$pass.left"
    echo "WATCH: ${run##*/} waits: its landing is deferred, the childcc pass left children of the run for a later pass, so its wave lands on a later cycle"; return 1; }
  if [ -e "$pass" ]; then
    # (#2068) With the batch on, the run joins the landing queue now, by a move so a claiming pass never reads half a marker,
    # and the cycle starts one landing pass after its last run (land_next), so that pass reads every run of this cycle.
    if [ "${SG_LAND_BATCH:-4}" != 1 ]; then
      printf '%s\n' "$run" > "$ran.queued.$$" && mv -f "$ran.queued.$$" "$ran.queued"
      land_pending="$land_pending$run "
      echo "WATCH: ${run##*/} waits: its wave is queued for this cycle's landing pass"; return 1
    fi
    land_fork "$run"; return 1
  fi
  h=$(cat "$L/pid" 2>/dev/null)
  if [ -n "$h" ] && kill -0 "$h" 2>/dev/null; then
    echo "WATCH: ${run##*/} waits: the childcc lock is held by pid $h (another pass is entering children), so its wave lands on a later cycle"
  else
    echo "WATCH: ${run##*/} waits: the pass that enters its children did not get through (it failed; its traceback is in this log), so its wave lands on a later cycle"
  fi
  return 1
}
# land_next: the one landing pass of the cycle (#2068), for the first queued run whose wave is not held red (#1961), else the
# first queued run. The pass claims the other queued runs into its batch (wave.sh land_queue).
land_next() {
  local r first="" lead=""
  for r in $land_pending; do
    [ -n "$first" ] || first=$r
    [ -e "$SG_STATE/auto/tipred-wave_${r##*/}.red" ] || { lead=$r; break; }
  done
  land_pending=""
  lead=${lead:-$first}; [ -n "$lead" ] && land_fork "$lead"
  return 0
}
# land_fork <run>: the run's detached landing pass.
land_fork() {
  local run=$1 ran pass lk
  ran="$SG_STATE/auto/wland-${run##*/}"; pass="$ran.pass"; lk="$ran.busy"
    # The landing runs detached (#955): the cycle returns at once, and the pass writes its own lines to this log. It leaves
    # `wland-<run>` when the wave is in $SG_BASE; a rebase conflict is terminal (#844): the wave is retired and the run is read.
    # The busy marker is the pass's pid, written by this shell from `$!` right after the fork: /bin/bash 3.2 leaves `$BASHPID`
    # empty, so a marker the pass wrote could name no process and the single flight would never fire. The pass removes it when
    # it ends; a cycle that finds it names a dead pid removes it and reads the wave.
    # The batch landing (#2058): the pass that holds the landing lock also lands the runs queued behind it (`wland-<run>.queued`,
    # which names the run's folder), up to SG_LAND_BATCH runs behind one tip gate (wave.sh land_queue). A pass that finds the
    # lock held queues its run. SG_LAND_BATCH=1 writes no queue marker, so each run lands in its own pass, as before.
    (
      export SG_LAND_QUEUE=1
      wout=$(tm bash "${wme%/*}/single.sh" wland sh -c 'bash "$1" land "$2" "$3" && : > "$4"' landwave "${wme%/*}/wave.sh" "${run##*/}" "$run" "$ran" 2>&1); wrc=$?
      [ -n "$wout" ] && printf '%s\n' "$wout"
      rm -f "$pass"
      if [ ! -e "$ran" ]; then
        c=$(printf '%s\n' "$wout" | /usr/bin/grep '^WAVE CONFLICT ' | tail -1)
        if [ -n "$c" ]; then echo "WATCH: ${run##*/} waits: its wave did not land, a rebase conflict (${c#WAVE CONFLICT }); the wave is retired, not retried"
        elif [ "$wrc" = 76 ]; then
          # The landing lock is held by a live holder: name that holder and the instant it took the lock, never a failure (#1290).
          h=$(cat "$SG_STATE/locks/single-wland/pid" 2>/dev/null); c=$(mtime "$SG_STATE/locks/single-wland/pid")
          echo "WATCH: ${run##*/} waits: its landing is held by pid ${h:-none}, held since $( [ "${c:-0}" -gt 0 ] && { date -r "$c" '+%F %T' 2>/dev/null || date -d "@$c" '+%F %T' 2>/dev/null || echo "$c"; } || echo 'an unreadable instant'), so it lands on a later cycle"
        else
          # The landing ran and failed: name its own reason, the last line the landing wrote, never a lock (#1290).
          c=$(printf '%s\n' "$wout" | /usr/bin/grep -E '^(WAVE|STUCK)' | tail -1)
          echo "WATCH: ${run##*/} waits: its landing failed${c:+ ($c)}${c:- (no line of its own; its output is in this log)}, so it lands on a later cycle"
        fi
      fi
      # A held lock keeps the run's queue marker, so the pass that holds the lock can claim it; any other end clears it.
      [ "$wrc" = 76 ] || rm -f "$ran.queued"
      rm -f "$lk"
      ) &
    printf '%s\n' "$!" > "$lk"
}
while :; do
  orphaned && exit 0; c0=$SECONDS
  # The token guard runs at the TOP of the cycle, before any step that spends tokens. It never stops the loop: a burn over
  # the cap is a report and one alert, and a sustained hourly runaway writes token-pause, which the dispatch passes read.
  # The guard keeps its own markers and alerts; its sentence, when it has one, goes to the cycle log and to stdout.
  guard_say=$("$(dirname "$0")/token-guard.sh" 2>/dev/null)
  [ -n "$guard_say" ] && { printf '%s %s\n' "$(date +%T)" "$guard_say" >> "$SG_STATE/cycle.log"; printf '%s\n' "$guard_say"; }
  for r in $(cat "$SG_STATE/active-runs.txt"); do
    # An age-cap requeue waits for the end of its run (#1970): a retirement the landing could not prove is tried again here,
    # each cycle, before the verdict is read, and the issues go back to the queue only once the run is proven ended.
    [ -e "$SG_STATE/auto/retire-${r##*/}" ] && bash "$(dirname "$0")/wave.sh" retire "${r##*/}"
    case "$seen" in *" $r "*) continue;; esac
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
    # A wave that reached its age cap lands as it is (#378, ADR-0145 rule 13): the children it holds land in $SG_BASE, and
    # the children of the run that never entered go back to the queue. The age is read from the wave's own start marker by
    # `wave.sh expired`, so a live run whose wave is old lands without waiting for its verdict. The run stays unread (the
    # landing clears the wave marker), so a failed landing is tried again next cycle, and the verdict path below reads a
    # run with a verdict exactly as it did before.
    if [ -z "$v" ] && [ -e "$SG_STATE/auto/wave-${r##*/}" ] && bash "$(dirname "$0")/wave.sh" expired "${r##*/}"; then
      land_wave "$r"; continue; fi
    [ -n "$v" ] && {
      # A run that ended lands its wave (#375, ADR-0145 rule 10): the branch the run cut at its start is rebased on $SG_BASE
      # and pushed there in one fast-forward push -- one update, one CI run, at the landing. The marker `wave.sh start` left
      # is what says the run has a wave; a run that cut none is left alone, and the landing pass clears the marker once the
      # wave is in $SG_BASE. The landing comes BEFORE the announcement below: a run whose landing failed stays unread and is
      # tried again next cycle, and the announcement, the retry of a failed run, the consult and the per-issue comments are
      # side effects that run once per run, on the cycle that reads it, never on every retry. The landing only happens once
      # the run's finished children are in the wave (land_wave above).
      [ -e "$SG_STATE/auto/wave-${r##*/}" ] && ! land_wave "$r" && continue
      [ $boot = 1 ] || { echo "RUN ${r##*/} verdict=$v"
        # the run's verdict goes on every issue it worked (issue numbers read from the run's first records)
        rd=$r; [ "${r#/}" = "$r" ] && rd=runs/$r
        case "$v" in failed*no_dispatch*) "$(dirname "$0")/consult.sh" "$rd" >> "$SG_STATE/consult.log" 2>&1 & ;; esac   # router hesitated: ask the coding model once (ADR-free loop rule)
        for n in $(head -c 200000 "$rd/record.jsonl" 2>/dev/null | grep -oE '(issue|Refs) #[0-9]{3,5}' | grep -oE '[0-9]+' | sort -u); do
          printf "Squad run %s finished: verdict %s. Refs #%s\n" "${r##*/}" "$v" $n > "$(sg_cq_file "$n-run$$-${r##*-}.md")"; done; }
      seen="$seen$r "; echo "seen $r" >> "$W"; }; done
  land_next   # the cycle's one landing pass, after every run of the cycle joined the queue (#2068)
  boot=0; v0=$(verdicts)   # the closing wait's baseline: the runs with a verdict now. One that lands in a later step is an event; one already here whose landing failed is not (#569)
  # stall watchdog: live run whose record.jsonl has not moved for stall_timeout_minutes. A run whose record already carries
  # its verdict is not a stalled run: it is over, and it waits for its wave to land (childcc keeps it in active-runs until
  # then, so its children can still enter). Reading its quiet record as a stall named three finished runs at 10:41 today and
  # sent the diagnosis after a hang that was never there (#450).
  # The bound is an integer find's -mmin can read: a key left out or set to null prints the string "null", find rejects
  # `-mmin +null` as an illegal value with its stderr dropped, so no run was ever named stalled and every review ended
  # BLOCKED while the run retried once a minute. The fallback is the shipped 15 m.
  cfg="$(dirname "$0")/models.json"; st=$(jq -r '.stall_timeout_minutes // 15' "$cfg" 2>/dev/null)
  case "$st" in ''|*[!0-9]*) st=15 ;; esac   # a missing file or a non-integer reads as the default too, never as a bad -mmin
  for r in $(cat "$SG_STATE/active-runs.txt"); do case "$seen" in *" $r "*) continue;; esac; case "$r" in /*) f=$r/record.jsonl;; *) f=runs/$r/record.jsonl;; esac
    grep -q '"event": *"verdict"' "$f" 2>/dev/null && continue
    [ -e "$f" ] && [ -n "$(find "$f" -mmin +$st 2>/dev/null)" ] && case "$stalled" in *" $r "*) ;; *) echo "STALL RUN ${r##*/} no record.jsonl progress for ${st}m"; stalled="$stalled$r "; echo "stall $r" >> "$W";; esac; done
  tick=$((tick+1))
  # reconcile.py is the most expensive step of the cycle by a wide margin (137 runs, 22,902 s, ~167 s each) and all it does is move cards
  # from GitHub facts: detached under its own single.sh lock, filing its stream in $SG_STATE/reconcile.log
  # instead of holding the cycle (#1845). A pass already running is skipped, not doubled.
  [ $((tick % 8)) -eq 1 ] && { srec_start reconcile; drec=$srec_id; ( set -o pipefail; bash "$(dirname "$0")/single.sh" reconcile env RECONCILE=apply python3 "$(dirname "$0")/reconcile.py" 2>&1 | sed "s/^/BOARD /" >> "$SG_STATE/reconcile.log"; srec_end "$drec" reconcile $? ) 2>/dev/null & }   # card Status from GitHub facts, every ~12 min (GraphQL budget)
  # The full suite runs nightly in both modes, heavy included (#704). It is detached under its own single-flight stage and
  # its own log: a suite of hours must never hold this cycle, and it takes its own `nightly` lock, never the landing one.
  # nightly.sh runs at most once a day by its own marker; each later cycle's start costs one file read and exits.
  { srec_start nightly; drec=$srec_id; ( set -o pipefail; bash "$(dirname "$0")/single.sh" nightly bash "$(dirname "$0")/nightly.sh" 2>&1 | sed "s/^/NIGHTLY /" >> "$SG_STATE/nightly.log"; srec_end "$drec" nightly $? ) 2>/dev/null & }
  # Every shell suite runs on the dev tip once a day (#2307), outside the landing path: a landing gate selects only the suites
  # its delta touches, so a suite that no ticket touches stays red on dev until an unrelated ticket's landing fails on it. The
  # pass runs one suite at a time under the gate lock and one gate slot, never beside a landing gate, and its own day marker
  # makes every later cycle's start one file read. It is detached under its own single-flight stage and log, like the nightly.
  { srec_start daysuites; drec=$srec_id; ( set -o pipefail; bash "$(dirname "$0")/single.sh" daysuites bash "$(dirname "$0")/daysuites.sh" 2>&1 | sed "s/^/DAYSUITES /" >> "$SG_STATE/daysuites.log"; srec_end "$drec" daysuites $? ) 2>/dev/null & }
  # The base's own check (#790): read once per cycle, so a red head that no landing reads is reported. It holds nothing.
  ( tm bash "$(dirname "$0")/single.sh" basecheck bash "$(dirname "$0")/basecheckpass.sh" 2>&1 & ) 2>/dev/null
  # Work-branch landing (#355): a pushed work branch with a green check lands in dev by a fast-forward push.
  [ -n "${SG_LAND_PAUSED:-}" ] || ( tm bash "$(dirname "$0")/single.sh" branchland "$(dirname "$0")/branchland.sh" 2>&1 & ) 2>/dev/null
  # Work-branch review (#397): each pushed work branch is reviewed once per tip, so branchland finds the verdict it waits for.
  [ -n "${SG_LAND_PAUSED:-}" ] || ( tm bash "$(dirname "$0")/single.sh" branchreview bash "$(dirname "$0")/branchreviewpass.sh" 2>&1 & ) 2>/dev/null
  # Work-branch fix round (#401): a reviewed tip whose review says CHANGES gets one fix run on the same branch.
  [ -n "${SG_LAND_PAUSED:-}" ] || ( tm bash "$(dirname "$0")/single.sh" branchfix bash "$(dirname "$0")/branchfixpass.sh" 2>&1 & ) 2>/dev/null
  # The promotion step (ADR-0145 rule 3): this cycle advances the proven dev tip to stg. It runs detached under its own
  # single-flight lock, so a pass in flight costs this cycle nothing and two cycles never overlap a pass; a held lock is
  # named, never waited for. Its own debounce is the quiet window (promote_quiet), bounded by SG_PROMOTE_MAX_DEFER, so a
  # burst of landings earns one proof window and a stream of landings that never quiets postpones the proof by that bound
  # and no longer. The proof is the dev tip's CI (SG_PROMOTE_PROOF=ci), a job on GitHub's runner, never a local suite: a
  # loaded box and a nightly that owns the machine refuse it nothing, and it takes no gate slot, so no landing waits on it.
  # The merge itself runs through promote-stg.sh, which holds the gate lock as every promotion does, for one pull-request
  # merge and never for a suite. Its own lines go to promote.log, its outcome to this cycle log.
  promote_quiet && { srec_start promote; drec=$srec_id; ( o=$(SG_PROMOTE_PROOF=ci bash "$(dirname "$0")/single.sh" promote bash "$(dirname "$0")/promote-pass.sh" 2>&1); rc=$?; srec_end "$drec" promote "$rc"
    if [ "$rc" = 76 ]; then echo "$(date +%T) PROMOTE waits: a pass in flight holds the promote lock (pid $(cat "$SG_STATE/locks/single-promote/pid" 2>/dev/null || echo none))" >> "$SG_STATE/cycle.log"
    else [ -n "$o" ] && printf '%s\n' "$o" >> "$SG_STATE/promote.log"; echo "$(date +%T) PROMOTE pass rc=$rc" >> "$SG_STATE/cycle.log"; fi ) 2>/dev/null & } \
    || promote_defer_note   # a held window names itself once per reason (#2147): a green dev tip that waits is never silent
  # The stg-to-main step (ADR-0173): every cycle, detached under its own single-flight lock, and outside the quiet window
  # above. It asks for the complete CI tier on the stg tip once stg is quiet or its oldest unpromoted commit is old, reads
  # the request it left, and moves main by a fast-forward when the tier is green. A landing costs a job on the runner
  # nothing, so no burst postpones the read. The pass says nothing on a cycle with nothing to do; its outcome goes to this
  # cycle log, its reasons to promote.log and alerts.log. SG_PROMOTE=off makes the pass exit 0 at once.
  { srec_start promote-main; drec=$srec_id; ( o=$(bash "$(dirname "$0")/single.sh" promote-main bash "$(dirname "$0")/promote-main-pass.sh" 2>&1); rc=$?; srec_end "$drec" promote-main "$rc"
    [ "$rc" = 76 ] || { [ "$rc" = 0 ] && [ -z "$o" ]; } || echo "$(date +%T) PROMOTE-MAIN pass rc=$rc: $(printf '%s' "$o" | tail -n 1)" >> "$SG_STATE/cycle.log" ) 2>/dev/null & }
  fast=; [ -s "$SG_STATE/fastloop.pid" ] && kill -0 "$(cat "$SG_STATE/fastloop.pid")" 2>/dev/null && fast=1   # watchd runs fastloop.sh: childcc and both dispatch passes every SG_FAST_INTERVAL s (120 by default) outside this cycle
  # The fast lane owns childcc, autofrontier and autobug; this cycle runs them only when that lane is down, so neither lane
  # runs a step twice. The frontier and bug dispatches ride the fast beat because a dispatch that waits for this cycle's tick appears up
  # to ~6 min late, which is what frontier-idle kept reporting (#1845). Every step moved here keeps its own single.sh lock, so a pass
  # already running in the other lane is skipped rather than started a second time.
  # The cycle's own childcc takes the childcc lock as every other pass does (#590): a bare one ran beside the fast lane's pass.
  [ -n "$fast" ] || { tm bash "$(dirname "$0")/single.sh" childcc python3 "$(dirname "$0")/childcc.py" 2>&1
    tm "$(dirname "$0")/autobug.sh" 2>&1   # open bug issues dispatched automatically, before the frontier (#616)
    tm "$(dirname "$0")/autofrontier.sh" 2>&1; }   # dispatch the frontier as soon as a blocker falls
  tm "$(dirname "$0")/autohunt.sh" 2>&1   # idle loop: one read-only bug hunt that files reproducible defects
  tm "$(dirname "$0")/heartbeat.sh"   # hung test hosts (0% CPU) and dead runs
  # worktree cleanup every third cycle (~4.5 min): the trees are almost always clean and ~17 s of every cycle is 10% of the loop (#1845).
  # An on-demand pass is unchanged: run scripts/wtclean.sh (the step is still self-contained, one pass over the worktrees).
  [ $((tick % 3)) -eq 1 ] && tm "$(dirname "$0")/wtclean.sh" 2>&1
  # The janitor (leftover processes of finished runs, zombie runs, idle dashboards, stale dispatch markers) runs detached under
  # its own single-flight lock, as the promotion does (#1774): one pass of 5046 s held this cycle, and every landing read with
  # it, for 2 h 40 min. A held lock is named, never waited for; the pass bounds itself (SG_JANITOR_DEADLINE in janitor.py) and
  # writes its own time here when it ends. Its summary line still goes to this log's stdout.
  { srec_start janitor; drec=$srec_id; ( j0=$SECONDS; o=$(bash "$(dirname "$0")/single.sh" janitor "$(dirname "$0")/janitor.sh" 2>&1); rc=$?; je=$((SECONDS - j0)); srec_end "$drec" janitor "$rc"
    if [ "$rc" = 76 ]; then echo "$(date +%T) JANITOR skipped: a pass in flight holds the janitor lock (pid $(cat "$SG_STATE/locks/single-janitor/pid" 2>/dev/null || echo none))" >> "$SG_STATE/cycle.log"
    else [ -n "$o" ] && printf '%s\n' "$o"
      { [ "$je" -ge 5 ] || [ "$rc" != 0 ]; } && echo "$(date +%T) janitor.sh ${je}s$([ "$rc" = 0 ] || echo " rc=$rc")" >> "$SG_STATE/cycle.log"; fi ) 2>/dev/null & }
  # The live skill -- the copy this cycle and the owner's info bar actually run -- follows the repository copy through live-guard.sh
  # (publish-live.sh, one explicit file list). It runs before the vendorguard step, so a drift is read, published and named in the same cycle.
  tm bash "$(dirname "$0")/single.sh" liveguard bash "$(dirname "$0")/live-guard.sh" 2>&1   # the live skill follows the repository copy: the difference is read, published, and named
  tm bash "$(dirname "$0")/single.sh" vendorguard bash "$(dirname "$0")/vendor-guard.sh" 2>&1   # re-vendor drifted personal skills (a drifted vendor makes every squad start refuse)
  # The closing wait is one wait of SG_WATCH_WAIT seconds at most (90 by default), polled every SG_WATCH_POLL (3), and a run verdict that the
  # wait's start did not see ends it (#569). A verdict already there when the read loop ended is no event: a run whose landing failed stays unread and must not spin the cycle.
  # childcc runs inline at the wait's first and second third (the 30 s and 60 s points) only while the fast lane is down.
  # wn polls in all; the inline childcc points are the first and second third of them. A fractional poll is fine; a bad value falls back to the default.
  num='^[0-9]+([.][0-9]+)?$'; ww=${SG_WATCH_WAIT:-90}; wp=${SG_WATCH_POLL:-3}
  [[ $ww =~ $num ]] || ww=90; { [[ $wp =~ $num ]] && [ "$wp" != 0 ]; } || wp=3
  read -r wn c1 c2 < <(awk -v w="$ww" -v p="$wp" 'BEGIN{n=int(w/p); if(n<1)n=1; a=int(n/3); if(a<1)a=1; b=int(2*n/3); if(b<1)b=1; print n, a, b}')
  for ((i = 1; i <= wn; i++)); do sleep "$wp"; orphaned && exit 0
    [ -z "$(comm -13 <(echo "$v0") <(verdicts))" ] || { echo "$(date +%T) WAIT ended early: a run verdict"  >> "$SG_STATE/cycle.log"; break; }
    { [ "$i" -eq "$c1" ] || [ "$i" -eq "$c2" ]; } && { [ -n "$fast" ] || tm bash "$(dirname "$0")/single.sh" childcc python3 "$(dirname "$0")/childcc.py" 2>&1; }; done
  # adaptive cycle (#45): once under 20 percent of a pool's limit is left the cycle is longer, so the loop spends less of what remains
  x=$(gh_stretch 90); [ "$x" -gt 0 ] 2>/dev/null && { echo "$(date +%T) QUOTA low: this cycle waits ${x}s more" >> "$SG_STATE/cycle.log"; sleep "$x"; }
  echo "$(date +%T) CYCLE $((SECONDS - c0))s" >> "$SG_STATE/cycle.log"; done   # childcc and both dispatches every SG_FAST_INTERVAL s (fastloop.sh when watchd runs it), the rest every 90 s
