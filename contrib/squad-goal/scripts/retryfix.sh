#!/bin/bash
# usage: retryfix.sh <run-id|run-folder> [--stalled]
# An autofix run that ended `failed`, or (--stalled) whose last started call has no live process, re-arms its PR's head sha:
# the fix-/queued- markers, the round and the soft-round line are dropped so autopilot dispatches the fix again (max 2 retries per sha).
. "$(dirname "$0")/env.sh"; A="$SG_STATE/auto"; id=${1##*/}
if [ "${2:-}" = --stalled ]; then
  rd=$1; [ "${rd#/}" = "$rd" ] && rd="${SG_CHECKOUT:-.}/runs/$rd"
  python3 - "$rd/record.jsonl" <<'PY' || exit 0
import json, os, sys
pid = None
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d.get("event") == "call_start" and d.get("pid"): pid = d["pid"]
    if d.get("event") == "verdict": sys.exit(1)
try: os.kill(pid, 0) if pid else sys.exit(1)
except ProcessLookupError: sys.exit(0)
sys.exit(1)
PY
fi
for f in "$A"/runof-*; do [ -e "$f" ] && [ "$(cat "$f")" = "$id" ] || continue
  k=${f##*/runof-}; pr=${k%%-*}; s9=${k#*-}
  n=$(cat "$A/retry-$k" 2>/dev/null || echo 0); [ "$n" -lt 2 ] || { echo "RETRY FIX #$pr @ $s9: run $id failed, 2 retries spent"; continue; }
  echo $((n+1)) > "$A/retry-$k"; rm -f "$A/fix-$k" "$A/queued-$k" "$f"
  for r in rounds softfix; do [ -e "$A/$r-$pr" ] && sed -i '' "/^$s9\$/d" "$A/$r-$pr"; done
  echo "RETRY FIX #$pr @ $s9: run $id ${2:-failed}, markers cleared for re-dispatch ($((n+1))/2)"
done
exit 0
