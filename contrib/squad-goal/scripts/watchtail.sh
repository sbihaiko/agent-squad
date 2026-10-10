#!/bin/bash
# Monitor source: ensures watchd is running, then streams new lines of watch.log (one event per line).
. "$(dirname "$0")/env.sh"; "$(dirname "$0")/watchd.sh" start >/dev/null
exec tail -n 0 -F "$SG_STATE/watch.log"
