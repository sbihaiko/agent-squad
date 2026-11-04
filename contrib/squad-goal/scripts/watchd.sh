#!/bin/bash
# usage: watchd.sh start|stop|drain|reload|status   (run `start` from the target checkout; `reload` takes a new plugin install without a stop, #873)
# Keeps watch.sh (the 90 s cycle), fastloop.sh (childcc every SG_FAST_INTERVAL s) and cqflush.sh (the comment queue) alive without a Monitor task: a supervisor in its own session restarts them if they die.
# Events go to $SG_STATE/watch.log (read them with watchtail.sh); pid in $SG_STATE/watchd.pid.
. "$(dirname "$0")/env.sh"; P="$SG_STATE/watchd.pid"; L="$SG_STATE/watch.log"; me="$(cd "$(dirname "$0")" && pwd)/watchd.sh"
# The slot is the one fact a supervisor holds (#932): watchd.pid is what a reader looks at, and watchd.lock/pid is what a
# second `run` is refused by. alive() reads the slot, so a pid file a dying duplicate removed no longer reads as "not running".
# A live supervisor is a `watchd.sh run` of any copy of the skill: the repository's scripts and the live ones are two paths
# to one loop, and a proof that matched one absolute path read the other copy's holder as stale and ran a second loop (#1312).
is_watchd() { /bin/ps -p "$1" -ww -o command= 2>/dev/null | grep -qE '(^|[ /])watchd\.sh run$'; }
alive() { pid=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$pid" ] || { [ -s "$P" ] && pid=$(cat "$P"); }; [ -n "$pid" ] && is_watchd "$pid"; }
# orphans: the lanes of this profile's state directory that pid 1 holds, so their supervisor is gone, one "<pid> <lane>" a line (#959).
# A lane is named by its own pid file under $SG_STATE, whichever copy of the skill it runs from (#1313), so a profile never reads another's lanes.
orphans() { /bin/ps -axwwo pid=,ppid=,command= 2>/dev/null | awk '$2 == 1 { for (i = 3; i <= NF; i++) if ($i ~ /(^|\/)(watch|fastloop|cqflush)\.sh$/) { print $1, $i; break } }' | while read -r p l; do
  case ${l##*/} in watch.sh) f=$SG_STATE/locks/watch/pid;; fastloop.sh) f=$SG_STATE/fastloop.pid;; cqflush.sh) f=$SG_STATE/cqflush.pid;; esac
  [ "$(cat "$f" 2>/dev/null)" = "$p" ] && printf '%s %s\n' "$p" "${l##*/}"; done; }
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
  [ -n "$g" ] && kill -0 "$g" 2>/dev/null && [ "$(/bin/ps -o stat= -p "$g" 2>/dev/null | cut -c1)" != Z ] && return 0
  return 1
}
# The wait before the next drain read: SG_DRAIN_POLL, doubled for each failed read in a row, at most SG_DRAIN_BACKOFF_MAX.
drain_pause() {
  local p=${SG_DRAIN_POLL:-5} n m=${SG_DRAIN_BACKOFF_MAX:-60}
  n=$(head -n 1 "$SG_STATE/drain-reader.fail" 2>/dev/null); case $n in ''|*[!0-9]*) n=0;; esac
  case $p in ''|*[!0-9]*) echo "$p"; return;; esac
  [ "$n" -gt 6 ] && n=6; p=$((p << n)); [ "$p" -gt "$m" ] && p=$m; echo "$p"
}
case "${1:-status}" in
  start) alive && { echo "watchd already running pid=$pid"; exit 0; }
    o=$(orphans); [ -n "$o" ] && { printf 'watchd start refused: orphaned lanes still run, their supervisor is gone: %s\n' "$(printf '%s\n' "$o" | awk '{ printf "%s%s pid %s", (NR > 1 ? ", " : ""), $2, $1 }')" >&2; exit 1; }
    [ -f "$L" ] && [ "$(wc -c < "$L")" -gt 5000000 ] && mv "$L" "$L.old"
    rm -f "$SG_STATE/token-guard.since" "$SG_STATE/token-over" "$SG_STATE/token-pause"   # a start is a relight: the token guard measures afresh, never an old burn (#515)
    # A drain's pause is the drain's own (#923): a start lifts it, from the hub or the terminal, so the loop never reads on
    # while it dispatches nothing. A pause the owner set before the drain has no drain-pause mark, and stays.
    rm -f "$SG_STATE/auto/drain"; [ -e "$SG_STATE/auto/drain-pause" ] && rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain-pause"
    record_sha
    date +%s > "$SG_STATE/watchd.started"   # the hub drops an older failed start once a later start ran (#923)
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"run"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me"
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive && { echo "watchd started pid=$pid"; exit 0; }; sleep 1; done
    echo "watchd failed to start" >&2; exit 1;;
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
    trap '[ -n "$child" ] && kill "$child" 2>/dev/null; [ "$(cat "$P" 2>/dev/null)" = "$$" ] && rm -f "$P"; [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"; exit 0' TERM INT
    # The stop reaches every pass of both lanes, not the two lanes alone: a stop from inside the group signals this
    # supervisor only (#489), and a lane's foreground pass (childcc and its reviews) would run on and spend (#529). The
    # supervisor ignores the signal itself and sends it to its whole group.
    fast=; cq=; trap 'trap "" TERM INT; kill -TERM -- "-$$" 2>/dev/null; [ "$(cat "$P" 2>/dev/null)" = "$$" ] && rm -f "$P" "$SG_STATE/fastloop.pid"; [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"; exit 0' TERM INT
    reload=; trap 'reload=1' HUP
    while :; do
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
      while kill -0 "$child" 2>/dev/null; do wait "$child"; [ -n "$reload" ] && kill -TERM "$child" 2>/dev/null; done
      [ -n "$reload" ] || { echo "WATCHD watch.sh exited, restarting in 5s" >> "$L"; sleep 5; }; done;;
  reload) alive || { [ -s "$P" ] || { echo "watchd not running"; exit 0; }; echo "watchd supervisor $(cat "$P") is not running: relighting"; bash "$me" start; exit $?; }
    kill -HUP "$pid" || { echo "watchd reload not sent: pid=$pid" >&2; exit 1; }; echo "watchd reload sent pid=$pid";;
  stop) alive || { echo "watchd not running"; rm -f "$P"; exit 0; }
    # The stop must reach the supervisor, and a signal a process sends to its own group is not delivered to itself: the
    # guard path runs this stop from inside watch.sh, a child of the supervisor and a member of its group, so
    # `kill -- -$pid` would spare the supervisor and it would restart the cycle (#489). A stop from outside the group
    # keeps the group-wide signal; a stop from inside it is handed to a helper that is not in the group.
    if [ "$(ps -o pgid= -p $$ | tr -d ' ')" = "$pid" ]; then
      python3 -I -c 'import os,signal,sys; os.kill(int(sys.argv[1]), signal.SIGTERM)' "$pid"
    else
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid"
    fi
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive || { rm -f "$P" "$SG_STATE/auto/drain"; echo "watchd stopped"; exit 0; }; sleep 1; done
    echo "watchd did not stop" >&2; exit 1;;
  drain) alive || { echo "watchd not running"; exit 0; }
    # A drain pauses dispatch and leaves the lanes running; a detached waiter stops the loop once no run or gate is live (#923).
    mkdir -p "$SG_STATE/auto"; [ -e "$SG_STATE/auto/off" ] || { : > "$SG_STATE/auto/drain-pause"; : > "$SG_STATE/auto/off"; }; : > "$SG_STATE/auto/drain"
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"drain-wait"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me"
    echo "watchd draining pid=$pid";;
  drain-wait) rm -f "$SG_STATE/drain-reader.fail"   # a failure streak is one drain's own
    while alive && drain_busy; do sleep "$(drain_pause)"; done
    alive && bash "$me" stop >/dev/null; rm -f "$SG_STATE/auto/drain";;
  status) alive && echo "watchd running pid=$pid" || echo "watchd not running";;
esac
