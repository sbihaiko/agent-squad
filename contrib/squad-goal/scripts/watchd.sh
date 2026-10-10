#!/bin/bash
# usage: watchd.sh start|stop|status   (run `start` from the target checkout)
# Keeps watch.sh alive without a Monitor task: a supervisor in its own session restarts it if it dies.
# Events go to $SG_STATE/watch.log (read them with watchtail.sh); pid in $SG_STATE/watchd.pid.
. "$(dirname "$0")/env.sh"; P="$SG_STATE/watchd.pid"; L="$SG_STATE/watch.log"; me="$(cd "$(dirname "$0")" && pwd)/watchd.sh"
alive() { [ -s "$P" ] && pid=$(cat "$P") && /bin/ps -p "$pid" -o command= 2>/dev/null | grep -qF "$me run"; }
case "${1:-status}" in
  start) alive && { echo "watchd already running pid=$pid"; exit 0; }
    [ -f "$L" ] && [ "$(wc -c < "$L")" -gt 5000000 ] && mv "$L" "$L.old"
    python3 -I -c 'import subprocess,sys;subprocess.Popen(["bash",sys.argv[1],"run"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)' "$me"
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive && { echo "watchd started pid=$pid"; exit 0; }; sleep 1; done
    echo "watchd failed to start" >&2; exit 1;;
  run) echo $$ > "$P"; child=
    trap '[ -n "$child" ] && kill "$child" 2>/dev/null; rm -f "$P"; exit 0' TERM INT
    while :; do bash "$(dirname "$me")/watch.sh" >> "$L" 2>&1 & child=$!; wait $child; echo "WATCHD watch.sh exited, restarting in 5s" >> "$L"; sleep 5; done;;
  stop) alive || { echo "watchd not running"; rm -f "$P"; exit 0; }
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do alive || { rm -f "$P"; echo "watchd stopped"; exit 0; }; sleep 1; done
    echo "watchd did not stop" >&2; exit 1;;
  status) alive && echo "watchd running pid=$pid" || echo "watchd not running";;
esac
