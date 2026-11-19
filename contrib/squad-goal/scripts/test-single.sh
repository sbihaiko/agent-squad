#!/bin/bash
# seconds: 16.06
# Regression test for single.sh and fastloop.sh. usage: bash test-single.sh
d=$(dirname "$0"); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
# 1. a second call while the first is alive returns at once and does not run
( bash "$d/single.sh" job sh -c "echo a >> $T/log; sleep 4" & ); sleep 1
t0=$(date +%s); bash "$d/single.sh" job sh -c "echo b >> $T/log"; e=$(( $(date +%s) - t0 ))
[ "$e" -le 1 ] && ok "a busy name returns at once" || ko "waited ${e}s"
sleep 4; [ "$(cat "$T/log")" = "a" ] && ok "the second call did not run" || ko "log: $(tr '\n' ' ' < "$T/log")"
# 2. free after the first finishes; lock released
bash "$d/single.sh" job sh -c "echo c >> $T/log"; [ "$(tr '\n' ' ' < "$T/log")" = "a c " ] && ok "runs again once the lock is free" || ko "no rerun: $(tr '\n' ' ' < "$T/log")"
[ -d "$SG_STATE/locks/single-job" ] && ko "lock left behind" || ok "lock released"
# 3. stale lock (dead pid) is taken over
mkdir -p "$SG_STATE/locks/single-job"; echo 99999999 > "$SG_STATE/locks/single-job/pid"
bash "$d/single.sh" job sh -c "echo d >> $T/log"; /usr/bin/grep -q d "$T/log" && ok "stale lock taken over" || ko "stale lock blocked"
# 4. different names do not block each other
( bash "$d/single.sh" one sleep 3 & ); sleep 1; bash "$d/single.sh" two sh -c "echo e >> $T/log"; /usr/bin/grep -q e "$T/log" && ok "different names are independent" || ko "names collided"
sleep 3
# 5. fastloop: one iteration runs childcc and both dispatch passes, a slow forkclose (own name) does not delay them
F=$T/scripts; mkdir -p "$F"; cp "$d/fastloop.sh" "$d/single.sh" "$F/"
printf '. /dev/null\n' > /dev/null
cat > "$F/env.sh" <<EOF
export SG_STATE=$SG_STATE
EOF
printf 'import sys\nopen("%s/fast.log","a").write("childcc\\n")\n' "$T" > "$F/childcc.py"
# the two dispatch passes ride the fast lane (#1845); they are detached, so the beat is timed without them and the three lines are waited for
printf '#!/bin/bash\necho autofrontier >> %s/fast.log\n' "$T" > "$F/autofrontier.sh"
printf '#!/bin/bash\necho autobug >> %s/fast.log\n' "$T" > "$F/autobug.sh"
chmod +x "$F/autofrontier.sh" "$F/autobug.sh"
( bash "$d/single.sh" forkclose sleep 6 & ); sleep 1
t0=$(date +%s); SG_FASTLOOP_ONCE=1 bash "$F/fastloop.sh"; e=$(( $(date +%s) - t0 ))
for _ in 1 2 3 4 5 6 7 8 9 10; do [ "$(wc -l < "$T/fast.log")" -ge 3 ] && break; sleep 0.3; done
miss=; for s in childcc autofrontier autobug; do /usr/bin/grep -qx "$s" "$T/fast.log" || miss="$miss $s"; done
{ [ -z "$miss" ] && [ "$e" -le 3 ]; } && ok "fastloop runs childcc and both dispatch passes without waiting for forkclose" || ko "fastloop: missing${miss:- nothing}, ${e}s, '$(tr '\n' ' ' < "$T/fast.log")'"
# 6. (#361) the fast lane carries no merge pass: the review, the fix pass and the branch lander are the cycle's, and autopilot.sh is gone
/usr/bin/grep -q 'autopilot' "$d/fastloop.sh" && ko "the fast lane still names autopilot" || ok "the fast lane carries no merge pass"
[ -e "$d/autopilot.sh" ] && ko "autopilot.sh is back" || ok "autopilot.sh is gone (the branch lander is the landing path)"
# 7. (#1057) a single lock whose holder died as a zombie is taken over: its parent has not reaped it, so kill -0 still answers
sh -c 'sleep 0 & echo $! > "$1"; exec sleep 300' sh "$T/zombie" >/dev/null 2>&1 & zpar=$!
for i in $(seq 1 50); do [ -s "$T/zombie" ] && [ "$(/bin/ps -o stat= -p "$(cat "$T/zombie")" 2>/dev/null | cut -c1)" = Z ] && break; sleep 0.1; done
mkdir -p "$SG_STATE/locks/single-zombie"; echo "$(cat "$T/zombie")" > "$SG_STATE/locks/single-zombie/pid"
bash "$d/single.sh" zombie sh -c "echo z >> $T/zlog" 2>"$T/zerr"
[ "$(cat "$T/zlog" 2>/dev/null)" = z ] && ok "a single lock held by a zombie is taken over" || ko "a zombie holder blocked single: $(cat "$T/zerr")"
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null
# 8. (#1290) a lock held by a live holder answers with one distinct code, so a caller tells a wait from the command's own
# failure by the code alone; the command runs only once the holder is gone.
/bin/sleep 60 & lh=$!
mkdir -p "$SG_STATE/locks/single-live"; echo "$lh" > "$SG_STATE/locks/single-live/pid"
bash "$d/single.sh" live sh -c "echo live >> $T/log"; rc=$?
[ "$rc" = 76 ] && ok "a live holder returns the held code 76" || ko "a live holder returned $rc, not 76"
/usr/bin/grep -qx live "$T/log" && ko "the call ran beside the live holder" || ok "the call runs nothing beside the live holder"
SG_SINGLE_WAIT=1 bash "$d/single.sh" live sh -c "echo waited >> $T/log"; rc=$?
[ "$rc" = 76 ] && ok "a bounded wait that ran out returns the held code too" || ko "a wait that ran out returned $rc, not 76"
bash "$d/single.sh" ownfail sh -c 'exit 7'; rc=$?
[ "$rc" = 7 ] && ok "the command's own failure passes through, distinct from the held code" || ko "the command's exit was $rc, not 7"
kill "$lh" 2>/dev/null; wait "$lh" 2>/dev/null
bash "$d/single.sh" live sh -c "echo after >> $T/log"; rc=$?
[ "$rc" = 0 ] && /usr/bin/grep -qx after "$T/log" && ok "the dead holder is reaped and the call runs" || ko "the dead holder blocked the call: rc=$rc"
sleep 5; rm -rf "$T"; exit $fail
