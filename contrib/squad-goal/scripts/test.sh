#!/bin/bash
# usage: test.sh <issue> [-C <dir>] -- <command...>   A coordinator test pass (post-merge e2e, a gate suite) the hub can show.
# Writes $SG_STATE/tests/<id>.json (running -> completed|failed) and <id>.log; the hub lists it as "test #<issue>" with the
# log as its panel. $TEST_ID fixes the id up front (the caller can name the log even if this script is killed). Moves the card to Testing at the start; queues the result on the issue. Exits with the command's code.
# A pass has a deadline (TEST_DEADLINE, default 7200 s): a gate can hang -- one worker that spins in a subprocess loop never
# returns -- and a pass with no bound then sits at state=running for ever, keeps the machine busy and holds every lock its tree
# took. The command runs in a process group of its own (monitor mode: the lock holder is a grandchild of this shell), so the
# deadline stops the whole tree at once. The record then leaves `running` and names the deadline, the tree's own trap removes
# its lock, and the exit is 143 -- the code a pass killed by a signal carries, so a reader sees the environment fail, not a red gate.
. "$(dirname "$0")/env.sh"; iss=$1; shift; dir=$PWD
[ "$1" = -C ] && { dir=$2; shift 2; }; [ "$1" = -- ] && shift
dl=${TEST_DEADLINE:-7200}
T="$SG_STATE/tests"; mkdir -p "$T"; id=${TEST_ID:-test-$iss-$(date +%Y%m%d-%H%M%S)}; j="$T/$id.json"; log="$T/$id.log"
w() { python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p)) if sys.argv[2]!="start" else {"issue":int(sys.argv[3]),"cmd":sys.argv[4],"dir":sys.argv[5],"t0":time.time()}
if sys.argv[2]!="start": d.update(state=sys.argv[2],rc=int(sys.argv[3]),t1=time.time())
else: d["state"]="running"
open(p,"w").write(json.dumps(d))' "$j" "$@"; }
kw() { python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p));d.update(state="failed",rc=143,reason="killed/deadline",deadline=int(sys.argv[2]),t1=time.time())
open(p,"w").write(json.dumps(d))' "$j" "$dl"; }
w start "$iss" "$*" "$dir"
$BOARD $SG_OWNER $SG_PROJECT Testing $iss >/dev/null 2>&1
set -m
(cd "$dir" && "$@") > "$log" 2>&1 & job=$!
set +m
t0=$(date +%s); k=0
while kill -0 "$job" 2>/dev/null; do
  sleep 5
  [ $(( $(date +%s) - t0 )) -lt "$dl" ] || { k=1; break; }
done
if [ $k = 1 ]; then   # TERM lets a lock holder's own trap release what it took; KILL takes what a trap cannot
  echo "--- test pass: the deadline of ${dl}s passed; stopping the command and everything it started" >> "$log"
  kill -TERM -"$job" 2>/dev/null; sleep 5; kill -KILL -"$job" 2>/dev/null
fi
wait "$job" 2>/dev/null; rc=$?
if [ $k = 1 ]; then st="killed by the deadline of ${dl}s"; kw; rc=143
elif [ $rc = 0 ]; then st=completed; w $st $rc
else st=failed; w $st $rc; fi
printf "Test pass %s on #%s: %s (exit %s) in %s\n\`\`\`\n%s\n%s\n\`\`\`\n" "$id" "$iss" "$st" "$rc" "${dir/#$HOME/~}" "$*" "$(tail -5 "$log")" > "$(sg_cq_file "$iss-$id.md")"
echo "TEST #$iss $st (exit $rc) $id"; exit $rc
