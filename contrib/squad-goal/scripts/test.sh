#!/bin/bash
# usage: test.sh <issue> [-C <dir>] -- <command...>   A coordinator test pass (post-merge e2e, a fork suite) the hub can show.
# Writes $SG_STATE/tests/<id>.json (running -> completed|failed) and <id>.log; the hub lists it as "test #<issue>" with the
# log as its panel. Moves the card to Testing at the start; queues the result on the issue. Exits with the command's code.
. "$(dirname "$0")/env.sh"; iss=$1; shift; dir=$PWD
[ "$1" = -C ] && { dir=$2; shift 2; }; [ "$1" = -- ] && shift
T="$SG_STATE/tests"; mkdir -p "$T"; id="test-$iss-$(date +%Y%m%d-%H%M%S)"; j="$T/$id.json"; log="$T/$id.log"
w() { python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p)) if sys.argv[2]!="start" else {"issue":int(sys.argv[3]),"cmd":sys.argv[4],"dir":sys.argv[5],"t0":time.time()}
if sys.argv[2]!="start": d.update(state=sys.argv[2],rc=int(sys.argv[3]),t1=time.time())
else: d["state"]="running"
open(p,"w").write(json.dumps(d))' "$j" "$@"; }
w start "$iss" "$*" "$dir"
$BOARD $SG_OWNER $SG_PROJECT Testing $iss >/dev/null 2>&1
(cd "$dir" && "$@") > "$log" 2>&1; rc=$?
[ $rc = 0 ] && st=completed || st=failed; w $st $rc
printf "Test pass %s on #%s: %s (exit %s) in %s\n\`\`\`\n%s\n%s\n\`\`\`\n" "$id" "$iss" "$st" "$rc" "${dir/#$HOME/~}" "$*" "$(tail -5 "$log")" > "$SG_STATE/cq/$iss-$id.md"
echo "TEST #$iss $st (exit $rc) $id"; exit $rc
