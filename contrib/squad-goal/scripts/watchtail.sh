#!/bin/bash
# Monitor source: streams new lines of watch.log (one event per line). It reads and never starts the loop: only the owner
# starts it (watchd.sh start), and a Monitor re-armed by a session restarted a loop the owner had stopped. A stopped loop is
# named in one line, and the log is still read.
. "$(dirname "$0")/env.sh"
pid=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
{ [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; } || echo "WATCHTAIL: watchd is not running; this Monitor only reads $SG_STATE/watch.log (the owner starts the loop: watchd.sh start)"
exec tail -n 0 -F "$SG_STATE/watch.log"
