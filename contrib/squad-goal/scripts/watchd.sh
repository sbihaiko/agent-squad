#!/bin/bash
# usage: watchd.sh start|stop [--force]|drain|restart|reload|status   (`stop` drains the live runs first, #1662; run `start` from the target checkout; `reload` takes a new plugin install without a stop, #873; `restart` drains the live runs first, #1471)
# Keeps watch.sh (the 90 s cycle), fastloop.sh (childcc every SG_FAST_INTERVAL s) and cqflush.sh (the comment queue) alive without a Monitor task: a supervisor in its own session restarts them if they die.
# Events go to $SG_STATE/watch.log (read them with watchtail.sh); pid in $SG_STATE/watchd.pid.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"; P="$SG_STATE/watchd.pid"; L="$SG_STATE/watch.log"; me="$(cd "$(dirname "$0")" && pwd)/watchd.sh"
. "$(dirname "$0")/ghcall.sh"   # the one seam the start's loop-bug read goes through (ADR-0146)
# The slot is the one fact a supervisor holds (#932): watchd.pid is what a reader looks at, and watchd.lock/pid is what a
# second `run` is refused by. alive() reads the slot, so a pid file a dying duplicate removed no longer reads as "not running".
# A live supervisor is a `watchd.sh run` of any copy of the skill: the repository's scripts and the live ones are two paths
# to one loop, and a proof that matched one absolute path read the other copy's holder as stale and ran a second loop (#1312).
is_watchd() { $SG_PS -p "$1" -ww -o command= 2>/dev/null | grep -qE '(^|[ /])watchd\.sh run$'; }
alive() { pid=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$pid" ] || { [ -s "$P" ] && pid=$(cat "$P"); }; [ -n "$pid" ] && is_watchd "$pid"; }
# orphans: the lanes of this profile's state directory that pid 1 holds, so their supervisor is gone, one "<pid> <lane>" a line (#959).
# A lane is named by its own pid file under $SG_STATE, whichever copy of the skill it runs from (#1313), so a profile never reads another's lanes.
orphans() { $SG_PS -axwwo pid=,ppid=,command= 2>/dev/null | awk '$2 == 1 { for (i = 3; i <= NF; i++) if ($i ~ /(^|\/)(watch|fastloop|cqflush)\.sh$/) { print $1, $i; break } }' | while read -r p l; do
  case ${l##*/} in watch.sh) f=$SG_STATE/locks/watch/pid;; fastloop.sh) f=$SG_STATE/fastloop.pid;; cqflush.sh) f=$SG_STATE/cqflush.pid;; esac
  [ "$(cat "$f" 2>/dev/null)" = "$p" ] && printf '%s %s\n' "$p" "${l##*/}"; done; }
# Every live driver of this state directory (#1587). A `watchd.sh run` writes its pid under $SG_STATE/watchd.runs on its way
# up and removes the file on its way out, so a driver outside the slot -- a supervisor that died, a slot removed by hand --
# is still visible to `start` and `stop`, which the slot alone never reached. A file whose pid no longer answers as a live
# driver is dropped, so a killed driver leaves no record behind. A driver with no record there is named by its lanes.
RU="$SG_STATE/watchd.runs"
# A driver that wrote no record there -- a build older than the record, or a reader that dropped it -- still launched the
# lanes as its own children (#1634): each lane writes its pid under this state directory, so a live lane names its driver
# by its parent, whatever the driver wrote.
lane_drivers() { local f p q; for f in "$SG_STATE/locks/watch/pid" "$SG_STATE/fastloop.pid" "$SG_STATE/cqflush.pid"; do
  p=$(cat "$f" 2>/dev/null); [ -n "$p" ] || continue
  q=$($SG_PS -o ppid= -p "$p" 2>/dev/null | tr -d ' '); [ -n "$q" ] || continue
  is_watchd "$q" && printf '%s\n' "$q"; done; }
drivers() { local f p; { for f in "$RU"/*; do [ -e "$f" ] || continue; p=${f##*/}; if is_watchd "$p"; then printf '%s\n' "$p"; else rm -f "$f"; fi; done; lane_drivers; } | LC_ALL=C sort -u; }
# A driver is one `run` under a live `watchd.sh supervise` (#1587): `start` launches `supervise`, and `supervise` launches
# `run`, so a legitimate driver has that parent.
is_supervise() { $SG_PS -p "$1" -ww -o command= 2>/dev/null | grep -qE '(^|[ /])watchd\.sh supervise$'; }
# One signal path for a driver (#1587): a stop from inside the driver's group cannot signal that group, so it hands the
# signal to a helper outside it; any other caller signals the group, so the lanes go with the driver (#489).
sig_driver() { if [ "$($SG_PS -o pgid= -p $$ | tr -d ' ')" = "$1" ]; then python3 -I -c 'import os,signal,sys; os.kill(int(sys.argv[1]), signal.SIGTERM)' "$1"
  else kill -TERM -- "-$1" 2>/dev/null || kill -TERM "$1"; fi; }
# The drivers run the skill this script sits in: its PUBLISHED sha, else its git HEAD, goes to driver.sha, so live-guard.sh
# names a later publish to another sha as a restart only the owner makes (#700). No sha known: no stale driver.sha.
record_sha() { k=$(dirname "$(dirname "$me")"); ds=$(cut -d' ' -f1 "$k/PUBLISHED" 2>/dev/null); [ -n "$ds" ] || ds=$(git -C "$k" rev-parse HEAD 2>/dev/null)
  if [ -n "$ds" ]; then printf '%s\n' "$ds" > "$SG_STATE/driver.sha.$$" && mv "$SG_STATE/driver.sha.$$" "$SG_STATE/driver.sha"; else rm -f "$SG_STATE/driver.sha"; fi; }
# The live work a drain waits for: a run in active-runs.txt that still runs, or a gate whose lock pid is alive (#923).
drain_busy() {
  local g v rc e f k n r s root
  # A live run is the one reader's rule (squad/run_liveness.py, which the hub reads too): only the events after a record's
  # last run_start or resume count (ADR-0167); the run's engine pid is the last call_start or gate_start of that invocation,
  # live as a squad run that did not start after the call named it; before its first call, the record moved within the
  # launch window. A run that died with no verdict never holds the drain (#1228).
  # The reader runs from the first root that holds it: this script's repository, SG_SQUAD, then SG_CHECKOUT. It is bounded
  # (SG_DRAIN_READ_S), and its answer counts only on exit 0 with exactly `live` or `idle` on stdout; its stderr goes to a
  # file, never into the answer. Any other result is unknown, and unknown holds the drain: a drain that waits is safe, one
  # that stops under a live run is not. Each distinct failure is one watch.log line; SG_DRAIN_ALERT_AFTER failures in a row
  # are one alerts.log line; drain_pause backs off while they last.
  [ -s "$SG_STATE/active-runs.txt" ] && {
    root=; for r in "$(cd "$(dirname "$me")/../../.." 2>/dev/null && pwd)" "${SG_SQUAD:-}" "${SG_CHECKOUT:-}"; do
      [ -n "$r" ] && [ -f "$r/squad/run_liveness.py" ] && { root=$r; break; }; done
    e="$SG_STATE/drain-reader.err"; f="$SG_STATE/drain-reader.fail"
    if [ -n "$root" ]; then
      v=$(cd "$root" && perl -e 'alarm shift; exec @ARGV' "${SG_DRAIN_READ_S:-30}" python3 -m squad.run_liveness drain "$SG_STATE/active-runs.txt" "${SG_CHECKOUT:-}" 2>"$e"); rc=$?
    else v=; rc=127; printf 'no squad/run_liveness.py in this repository, SG_SQUAD or SG_CHECKOUT\n' > "$e"; fi
    if [ "$rc" = 0 ] && { [ "$v" = live ] || [ "$v" = idle ]; }; then
      [ -e "$f" ] && { printf '%s drain: the run reader answers again after %s failed reads\n' "$(date '+%F %T')" "$(head -n 1 "$f")" >> "$L"; rm -f "$f"; }
      [ "$v" = live ] && return 0
    else
      case $rc in 0) k="exit 0 with no answer: $(printf '%s' "$v" | head -c 80 | tr '\n' ' ')";; 142) k="no answer in ${SG_DRAIN_READ_S:-30}s";; *) k="exit $rc: $(tail -n 1 "$e" 2>/dev/null | cut -c1-300)";; esac
      n=$(head -n 1 "$f" 2>/dev/null); case $n in ''|*[!0-9]*) n=0;; esac; n=$((n + 1))
      s=$(tail -n +2 "$f" 2>/dev/null)
      printf '%s\n' "$s" | grep -qxF -- "$k" || { printf '%s drain: the run reader failed, the drain waits: %s\n' "$(date '+%F %T')" "$k" >> "$L"; s=${s:+$s$'\n'}$k; }
      printf '%s\n%s\n' "$n" "$s" > "$f.$$" && mv "$f.$$" "$f"
      [ "$n" = "${SG_DRAIN_ALERT_AFTER:-12}" ] && printf '%s DRAIN the run reader failed %s reads in a row, the drain holds the loop up: %s\n' "$(date '+%F %T')" "$n" "$k" >> "$SG_STATE/alerts.log"
      return 0
    fi; }
  g=$(cat "$SG_STATE/locks/gate/pid" 2>/dev/null)
  [ -n "$g" ] && kill -0 "$g" 2>/dev/null && [ "$($SG_PS -o stat= -p "$g" 2>/dev/null | cut -c1)" != Z ] && return 0
  return 1
}
# The wait before the next drain read: SG_DRAIN_POLL, doubled for each failed read in a row, at most SG_DRAIN_BACKOFF_MAX.
drain_pause() {
  local p=${SG_DRAIN_POLL:-5} n m=${SG_DRAIN_BACKOFF_MAX:-60}
  n=$(head -n 1 "$SG_STATE/drain-reader.fail" 2>/dev/null); case $n in ''|*[!0-9]*) n=0;; esac
  case $p in ''|*[!0-9]*) echo "$p"; return;; esac
  [ "$n" -gt 6 ] && n=6; p=$((p << n)); [ "$p" -gt "$m" ] && p=$m; echo "$p"
}
# A start during a drain cancels it (#1316): the waiter ends, and the pause the drain set lifts with its marker. A start
# cancels a restart's drain the same way, because it reaches the supervisor the loop already runs and carries no new copy.
drain_cancel() { w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null); [ -n "$w" ] && kill -TERM "$w" 2>/dev/null
  rm -f "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-waiter" "$SG_STATE/auto/restart"; [ -e "$SG_STATE/auto/drain-pause" ] && rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain-pause"; }
# A pid file a dead drain-wait left behind names no waiter: joining it would drop the drain or the restart into a process
# that already ended. The waiter's own state directory is part of its command line, so a waiter serving another state
# directory is no waiter here (#1471).
is_waiter() { $SG_PS -p "$1" -ww -o command= 2>/dev/null | grep -qF -- "watchd.sh drain-wait $SG_STATE"; }
# loop_bug_open   The owner's rule: the loop is never restarted while a loop-bug issue is open, Backlog included (#1386,
# ADR-0146). The three halt labels stop the passes; a bare `loop-bug` stops nothing there, and no label stops a start. So
# the start itself reads the open issues, once, through the one seam. Returns 0 and prints the number when one is open,
# 1 when none is, and 2 when the read failed: a read that proves nothing refuses nothing (ADR-0146 decision 6).
loop_bug_open() {
  local j n
  j=$(gh_aggregate "repos/$SG_REPO/issues?state=open") || return 2
  n=$(printf '%s' "$j" | jq -r '[.[]|select(any(.labels[]?; ((.name? // .) as $l | $l == "loop-bug" or ($l | startswith("loop-bug:")))))][0].number // empty' 2>/dev/null) || return 2
  [ -n "$n" ] || return 1
  printf '%s\n' "$n"
}
# #1662: the WIP snapshot of every run the loop still lists without a verdict, before a hard stop kills its trees. One line per
# tree goes to cycle.log. A tree that could not be saved is named there, and the stop goes on: the owner asked for it.
wip_live() {
  [ -s "$SG_STATE/active-runs.txt" ] || return 0
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    case "$r" in /*) f=$r;; *) f="$SG_CHECKOUT/runs/$r";; esac
    grep -q '"event": *"verdict"' "$f/record.jsonl" 2>/dev/null && continue
    python3 -I "$(dirname "$me")/wip_snapshot.py" "$f" >> "$SG_STATE/cycle.log" 2>&1 || printf 'WIP failed run %s: a tree could not be saved; the stop goes on\n' "$f" >> "$SG_STATE/cycle.log"
  done < "$SG_STATE/active-runs.txt"
}
# #1662: the hard stop. It reaches every driver of this state directory and waits up to ten seconds for them to end. It takes
# no snapshot: `stop --force` snapshots the live runs first (wip_live), and the drained waiter ends with `halt`.
hard_stop() {
  alive || { [ -n "$(drivers)" ] || { echo "watchd not running"; rm -f "$P"; return 0; }; }
    # #1587: the stop reaches every driver of this state directory, not only the group of the slot holder. A driver whose
    # supervisor died holds no slot, and a slot removed by hand leaves its driver invisible to `alive`, so both survive a
    # stop that reads the slot alone. The slot is removed last, after the drivers are gone.
    ds=$(drivers); [ -n "$pid" ] && is_watchd "$pid" && case " $ds " in *" $pid "*) ;; *) ds="$ds $pid";; esac
    for d in $ds; do is_watchd "$d" && sig_driver "$d"; done
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      ! alive && [ -z "$(drivers)" ] && { rm -rf "$SG_STATE/watchd.lock"; rm -f "$P" "$SG_STATE/auto/drain" "$SG_STATE/auto/restart"
        echo "watchd stopped"; return 0; }
      sleep 1
    done
    echo "watchd did not stop" >&2; return 1
}

case "${1:-status}" in
  start) # #1587: a driver that does not hold the slot is never a second loop this start may sit beside: it refuses, and
    # names the drivers, before it spawns a supervisor.
    ds=$(drivers); h=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$h" ] && is_watchd "$h" || h=
    x=; for p in $ds; do [ "$p" = "$h" ] || x="$x $p"; done
    [ -n "$x" ] && { printf 'watchd start refused: a driver still runs outside the slot: %s\n' \
      "$(printf '%s\n' $x | awk '{ printf "%s%s", (NR > 1 ? ", " : ""), "pid " $1 }')" >&2; exit 1; }
    alive && { drain_cancel; echo "watchd already running pid=$pid"; exit 0; }
    o=$(orphans); [ -n "$o" ] && { printf 'watchd start refused: orphaned lanes still run, their supervisor is gone: %s\n' "$(printf '%s\n' "$o" | awk '{ printf "%s%s pid %s", (NR > 1 ? ", " : ""), $2, $1 }')" >&2; exit 1; }
    # The live scripts call squad.caps.free_runs. A plugin cache older than the live copy lacks it, and every beat then prints
    # a traceback and dispatches nothing (#1422): one probe refuses the start and names the recovery.
    if [ -n "${SG_SQUAD:-}" ] && ! ( cd / && PYTHONPATH="$SG_SQUAD" python3 -c 'import squad.caps as C; C.free_runs' ) >/dev/null 2>&1; then
      printf 'watchd start refused: SG_SQUAD %s has no squad.caps.free_runs; reinstall the plugin: claude plugin uninstall agent-squad@agent-squad && claude plugin install agent-squad@agent-squad\n' "$SG_SQUAD" >&2; exit 1
    fi
    lb=$(loop_bug_open); lb_rc=$?
    case $lb_rc in
      0) printf 'watchd start refused: #%s is a loop defect; the fix lands outside the loop and its publish closes it (ADR-0146)\n' "$lb" >&2; exit 1;;
      2) echo "watchd start: the open issues could not be read, so the loop-bug check did not run" >&2;;
    esac
    [ -f "$L" ] && [ "$(wc -c < "$L")" -gt 5000000 ] && mv "$L" "$L.old"
    rm -f "$SG_STATE/token-guard.since" "$SG_STATE/token-over" "$SG_STATE/token-pause"   # a start is a relight: the token guard measures afresh, never an old burn (#515)
    # A drain's pause is the drain's own (#923): a start lifts it, from the hub or the terminal, so the loop never reads on
    # while it dispatches nothing. A pause the owner set before the drain has no drain-pause mark, and stays.
    rm -f "$SG_STATE/auto/drain" "$SG_STATE/auto/restart"; [ -e "$SG_STATE/auto/drain-pause" ] && rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain-pause"
    record_sha
    date +%s > "$SG_STATE/watchd.started"   # the hub drops an older failed start once a later start ran (#923)
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"supervise"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me"
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive && { echo "watchd started pid=$pid"; exit 0; }; sleep 1; done
    echo "watchd failed to start" >&2; exit 1;;
  # Logs how the driver ended (#1439): a SIGKILL cannot be trapped, and set -m keeps the driver the group `stop` signals.
  supervise) set -m; bash "$me" run & d=$!; set +m; trap '' TERM INT HUP
    wait "$d"; rc=$?; sig=; [ "$rc" -gt 128 ] && sig=" (SIG$(kill -l $((rc - 128)) 2>/dev/null))"
    printf '%s WATCHD driver %s ended: %s%s\n' "$(date '+%F %T')" "$d" "$rc" "$sig" >> "$L"; exit "$rc";;
  run) LK="$SG_STATE/watchd.lock"; child=
    # One supervisor per state directory (#932). `mkdir` is the claim: two runs that both passed `start`'s `alive` check
    # before either wrote the pid cannot both hold it, and the pid inside says who does. A slot whose pid is not a live
    # `watchd.sh run` is stale -- the owner killed a supervisor with -9, or its trap never ran -- so the next run takes it
    # over instead of staying down. Only the holder writes watchd.pid, and only the holder removes the slot.
    if ! mkdir "$LK" 2>/dev/null; then
      h=$(cat "$LK/pid" 2>/dev/null)
      if [ -n "$h" ] && is_watchd "$h"; then
        printf 'WATCHD run %s refused: supervisor %s holds the loop\n' "$$" "$h" >> "$L"; exit 0
      fi
      rm -rf "$LK"; mkdir "$LK" 2>/dev/null || { printf 'WATCHD run %s refused: the slot was claimed again\n' "$$" >> "$L"; exit 0; }
    fi
    printf '%s\n' "$$" > "$LK/pid"; printf '%s\n' "$$" > "$P"
    # #1587: a run whose parent is not a live `watchd.sh supervise` has no supervisor to restart it, and it would hold a
    # loop of its own -- it took the slot only because no other driver held it. It releases the slot and leaves.
    if ! is_supervise "$PPID"; then
      printf 'WATCHD run %s refused: no live supervisor\n' "$$" >> "$L"
      [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"
      [ "$(cat "$P" 2>/dev/null)" = "$$" ] && rm -f "$P"
      exit 0
    fi
    mkdir -p "$RU" && printf '%s\n' "$$" > "$RU/$$"
    # The driver's own end, shared by the stop, a deleted script and the exit path (#1587): the lanes go with the group,
    # and only the slot holder removes the files it owns. The stop reaches every pass of both lanes, not the two lanes
    # alone: a stop from inside the group signals this supervisor only (#489), and a lane's foreground pass (childcc and
    # its reviews) would run on and spend (#529). The supervisor ignores the signal itself and sends it to its whole group.
    fast=; cq=
    shutdown() { trap '' TERM INT; kill -TERM -- "-$$" 2>/dev/null
      [ "$(cat "$P" 2>/dev/null)" = "$$" ] && rm -f "$P" "$SG_STATE/fastloop.pid"
      [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"
      rm -f "$RU/$$"; exit 0; }
    trap shutdown TERM INT
    # #1587: a driver whose script file is gone can never restart its lanes, so a deleted test copy must not sleep for
    # ever: it says so and ends the way a stop ends it.
    gone() { [ -r "$me" ] || { printf 'WATCHD run %s refused: this script is gone\n' "$$" >> "$L"; shutdown; }; }
    reload=; trap 'reload=1' HUP
    while :; do
      gone
      # A reload lands here, once the cycle has ended: the fast and comment lanes end their current pass, SG_SQUAD is read
      # again from the registry, and the lanes start under that install. The supervisor and the live runs keep their pids.
      [ -n "$reload" ] && { for p in $fast $cq; do kill -TERM "$p" 2>/dev/null; done
        for p in $fast $cq; do while kill -0 "$p" 2>/dev/null; do wait "$p"; done; done
        fast=; cq=; old=$SG_SQUAD; unset SG_SQUAD; . "$(dirname "$0")/env.sh"; reload=; record_sha
        printf 'WATCHD RELOAD %s -> %s\n' "$old" "$SG_SQUAD" >> "$L"; }
      # the fast lane (childcc every SG_FAST_INTERVAL s) lives beside watch.sh, so a slow cycle step never delays a dispatch
      { [ -n "$fast" ] && kill -0 "$fast" 2>/dev/null; } || { bash "$(dirname "$me")/fastloop.sh" >> "$L" 2>&1 & fast=$!; }
      # the comment queue is the only place a child learns why the entry gate rejected its work (#669): a loop that runs
      # without its flusher repeats every rejection blind, so the flusher runs beside the two lanes and restarts with them
      { [ -n "$cq" ] && kill -0 "$cq" 2>/dev/null; } || { bash "$(dirname "$me")/cqflush.sh" >> "$SG_STATE/cqflush.log" 2>&1 & cq=$!; }
      bash "$(dirname "$me")/watch.sh" >> "$L" 2>&1 & child=$!
      # watch.sh never exits by itself, so a reload ends this lane too: its TERM trap leaves after the step in flight.
      # The wait is bounded to one second so a deleted script is noticed while the lane still runs (#1587).
      while kill -0 "$child" 2>/dev/null; do
        [ -n "$reload" ] && kill -TERM "$child" 2>/dev/null
        gone
        sleep 1
      done
      [ -n "$reload" ] || { echo "WATCHD watch.sh exited, restarting in 5s" >> "$L"; sleep 5; }; done;;
  reload) alive || { [ -s "$P" ] || { echo "watchd not running"; exit 0; }; echo "watchd supervisor $(cat "$P") is not running: relighting"; bash "$me" start; exit $?; }
    kill -HUP "$pid" || { echo "watchd reload not sent: pid=$pid" >&2; exit 1; }; echo "watchd reload sent pid=$pid";;
  stop) # #1662: a stop drains by default. Under live work it hands off to the drain's waiter and returns, so the run finishes;
    # `stop --force` is the hard stop: it snapshots the live runs' trees (wip_live) before it kills, and it does not wait.
    if [ "${2:-}" != --force ] && alive && drain_busy; then bash "$me" drain; exit $?; fi
    [ "${2:-}" = --force ] && wip_live
    hard_stop; exit $?;;
  halt) hard_stop; exit $?;;   # the drained waiter's end: no live work is left, so nothing is snapshotted
  drain) alive || { echo "watchd not running"; exit 0; }
    # A drain pauses dispatch and leaves the lanes running; a detached waiter stops the loop once no run or gate is live (#923).
    # A drain that finds a live waiter joins it and drops the restart mark: a drain means stop, so the stop intent is never
    # silently turned into a restarted loop (#1471).
    mkdir -p "$SG_STATE/auto"; [ -e "$SG_STATE/auto/off" ] || { : > "$SG_STATE/auto/drain-pause"; : > "$SG_STATE/auto/off"; }; : > "$SG_STATE/auto/drain"
    rm -f "$SG_STATE/auto/restart"
    w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null); [ -n "$w" ] && is_waiter "$w" && { echo "watchd draining pid=$pid"; exit 0; }
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"drain-wait",sys.argv[2]],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me" "$SG_STATE"
    echo "watchd draining pid=$pid";;
  # A restart follows a publish (#1471, ADR-0162 decision 3). A publish reaches the drivers only through a restart, and a
  # stop under a live run kills the run and its gate; so the restart pauses dispatch at once, marks its drain, and a
  # detached waiter stops the supervisor only once no run and no gate is live, then starts it again. The lanes keep their
  # beat under the pause, so the takeover bound still ends a stuck run and the drain lets go of it (ADR-0166).
  restart) alive || { echo "watchd not running: starting"; bash "$me" start; exit $?; }
    # A plain drain is a stop the owner asked for: a restart that finds that drain running refuses to upgrade it, and
    # names the drain (#1471).
    w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null)
    if [ -n "$w" ] && is_waiter "$w" && [ ! -e "$SG_STATE/auto/restart" ]; then
      echo "watchd restart refused: a drain the owner asked for is in progress, and it stops the loop" >&2; exit 1
    fi
    mkdir -p "$SG_STATE/auto"; [ -e "$SG_STATE/auto/off" ] || { : > "$SG_STATE/auto/drain-pause"; : > "$SG_STATE/auto/off"; }; : > "$SG_STATE/auto/drain"; : > "$SG_STATE/auto/restart"
    [ -n "$w" ] && is_waiter "$w" && { echo "watchd restarting: draining pid=$pid"; exit 0; }
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"drain-wait",sys.argv[2]],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me" "$SG_STATE"
    echo "watchd restarting: draining pid=$pid";;
  drain-wait) rm -f "$SG_STATE/drain-reader.fail"   # a failure streak is one drain's own
    echo "$$" > "$SG_STATE/auto/drain-waiter"
    while alive && [ -e "$SG_STATE/auto/drain" ] && drain_busy; do sleep "$(drain_pause)"; done
    # The restart's decision is read before the stop, because the stop itself clears both marks (#1471).
    r=; [ -e "$SG_STATE/auto/drain" ] && [ -e "$SG_STATE/auto/restart" ] && alive && r=1
    if [ -e "$SG_STATE/auto/drain" ] && alive; then
      if bash "$me" halt >/dev/null; then
        if [ -n "$r" ]; then
          printf '%s WATCHD RESTART the live work ended, the supervisor restarts\n' "$(date '+%F %T')" >> "$L"
          # The lanes die with the supervisor's group, and a lane still listed under pid 1 is an orphan the start refuses:
          # give the group the moment it needs before the start reads the table.
          for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(orphans)" ] && break; sleep 1; done
          out=$(bash "$me" start 2>&1); rc=$?; printf '%s\n' "$out" >> "$L"
          # A refused start (an open loop-bug, an orphan lane, a missing squad.caps) leaves the loop off; one alerts.log
          # line names the reason, so a publish never ends in a silently down loop.
          [ "$rc" = 0 ] || printf '%s WATCHD RESTART the start after the drain was refused, the loop stays off: %s\n' \
            "$(date '+%F %T')" "$(printf '%s' "$out" | tail -n 1)" >> "$SG_STATE/alerts.log"
        fi
      else printf '%s WATCHD RESTART the supervisor did not stop, so the restart is not made\n' "$(date '+%F %T')" >> "$L"
        # This waiter set the drain's pause, and it is the only one that lifts it: a supervisor that survives the stop would
        # otherwise stay live with dispatch paused and no waiter left to read the work, so the publish would hold the loop
        # up for good. The waiter's own marks go in the exit below; the pause goes here, while this waiter still holds the
        # file. A later drain or restart that took the file over keeps its own pause (ADR-0162 decision 3, #1471).
        if [ "$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null)" = "$$" ] && [ -e "$SG_STATE/auto/drain-pause" ]; then
          rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain-pause"
        fi
      fi
    fi
    # The marks are the waiter's own while it holds the waiter file: a later drain or restart that took the file over keeps
    # its own marks, so an old waiter's exit never cancels the restart it was too late to serve.
    if [ "$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null)" = "$$" ]; then rm -f "$SG_STATE/auto/drain-waiter" "$SG_STATE/auto/drain" "$SG_STATE/auto/restart"; fi;;
  status) alive && echo "watchd running pid=$pid" || echo "watchd not running";;
esac
