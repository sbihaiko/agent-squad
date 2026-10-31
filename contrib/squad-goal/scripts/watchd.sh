#!/bin/bash
# usage: watchd.sh start|stop|status   (run `start` from the target checkout)
# Keeps watch.sh (the 90 s cycle), fastloop.sh (childcc every SG_FAST_INTERVAL s) and cqflush.sh (the comment queue) alive without a Monitor task: a supervisor in its own session restarts them if they die.
# Events go to $SG_STATE/watch.log (read them with watchtail.sh); pid in $SG_STATE/watchd.pid.
. "$(dirname "$0")/env.sh"; P="$SG_STATE/watchd.pid"; L="$SG_STATE/watch.log"; me="$(cd "$(dirname "$0")" && pwd)/watchd.sh"
# The slot is the one fact a supervisor holds (#932): watchd.pid is what a reader looks at, and watchd.lock/pid is what a
# second `run` is refused by. alive() reads the slot, so a pid file a dying duplicate removed no longer reads as "not running".
alive() { pid=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$pid" ] || { [ -s "$P" ] && pid=$(cat "$P"); }; [ -n "$pid" ] && /bin/ps -p "$pid" -ww -o command= 2>/dev/null | grep -qF "$me run"; }
case "${1:-status}" in
  start) alive && { echo "watchd already running pid=$pid"; exit 0; }
    [ -f "$L" ] && [ "$(wc -c < "$L")" -gt 5000000 ] && mv "$L" "$L.old"
    rm -f "$SG_STATE/token-guard.since" "$SG_STATE/token-over" "$SG_STATE/token-pause"   # a start is a relight: the token guard measures afresh, never an old burn (#515)
    # The drivers run the skill this script sits in: its PUBLISHED sha, else its git HEAD, goes to driver.sha, so live-guard.sh
    # names a later publish to another sha as a restart only the owner makes (#700). No sha known: no stale driver.sha.
    k=$(dirname "$(dirname "$me")"); ds=$(cut -d' ' -f1 "$k/PUBLISHED" 2>/dev/null); [ -n "$ds" ] || ds=$(git -C "$k" rev-parse HEAD 2>/dev/null)
    if [ -n "$ds" ]; then printf '%s\n' "$ds" > "$SG_STATE/driver.sha.$$" && mv "$SG_STATE/driver.sha.$$" "$SG_STATE/driver.sha"; else rm -f "$SG_STATE/driver.sha"; fi
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
      if [ -n "$h" ] && /bin/ps -p "$h" -ww -o command= 2>/dev/null | grep -qF "$me run"; then
        printf 'WATCHD run %s refused: supervisor %s holds the loop\n' "$$" "$h" >> "$L"; exit 0
      fi
      rm -rf "$LK"; mkdir "$LK" 2>/dev/null || { printf 'WATCHD run %s refused: the slot was claimed again\n' "$$" >> "$L"; exit 0; }
    fi
    printf '%s\n' "$$" > "$LK/pid"; printf '%s\n' "$$" > "$P"
    trap '[ -n "$child" ] && kill "$child" 2>/dev/null; rm -f "$P"; [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"; exit 0' TERM INT
    # The stop reaches every pass of both lanes, not the two lanes alone: a stop from inside the group signals this
    # supervisor only (#489), and a lane's foreground pass (childcc and its reviews) would run on and spend (#529). The
    # supervisor ignores the signal itself and sends it to its whole group.
    fast=; cq=; trap 'trap "" TERM INT; kill -TERM -- "-$$" 2>/dev/null; rm -f "$P" "$SG_STATE/fastloop.pid"; [ "$(cat "$LK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LK"; exit 0' TERM INT
    while :; do
      # the fast lane (childcc every SG_FAST_INTERVAL s) lives beside watch.sh, so a slow cycle step never delays a dispatch
      { [ -n "$fast" ] && kill -0 "$fast" 2>/dev/null; } || { bash "$(dirname "$me")/fastloop.sh" >> "$L" 2>&1 & fast=$!; }
      # the comment queue is the only place a child learns why the entry gate rejected its work (#669): a loop that runs
      # without its flusher repeats every rejection blind, so the flusher runs beside the two lanes and restarts with them
      { [ -n "$cq" ] && kill -0 "$cq" 2>/dev/null; } || { bash "$(dirname "$me")/cqflush.sh" >> "$SG_STATE/cqflush.log" 2>&1 & cq=$!; }
      bash "$(dirname "$me")/watch.sh" >> "$L" 2>&1 & child=$!; wait $child; echo "WATCHD watch.sh exited, restarting in 5s" >> "$L"; sleep 5; done;;
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
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive || { rm -f "$P"; echo "watchd stopped"; exit 0; }; sleep 1; done
    echo "watchd did not stop" >&2; exit 1;;
  status) alive && echo "watchd running pid=$pid" || echo "watchd not running";;
esac
