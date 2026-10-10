#!/bin/bash
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
# 5. fastloop: one iteration runs childcc and autopilot, a slow forkclose (own name) does not delay them
F=$T/scripts; mkdir -p "$F"; cp "$d/fastloop.sh" "$d/single.sh" "$F/"
printf '. /dev/null\n' > /dev/null
cat > "$F/env.sh" <<EOF
export SG_STATE=$SG_STATE
EOF
printf 'import sys\nopen("%s/fast.log","a").write("childcc\\n")\n' "$T" > "$F/childcc.py"
printf '#!/bin/bash\necho autopilot >> %s/fast.log\n' "$T" > "$F/autopilot.sh"
( bash "$d/single.sh" forkclose sleep 6 & ); sleep 1
t0=$(date +%s); SG_FASTLOOP_ONCE=1 bash "$F/fastloop.sh"; e=$(( $(date +%s) - t0 ))
[ "$(tr '\n' ' ' < "$T/fast.log")" = "childcc autopilot " ] && [ "$e" -le 3 ] && ok "fastloop runs childcc and autopilot without waiting for forkclose" || ko "fastloop: '$(tr '\n' ' ' < "$T/fast.log")' in ${e}s"
# 6. a gap of more than 120 s between autopilot starts is logged
echo $(( $(date +%s) - 300 )) > "$SG_STATE/autopilot.last"; SG_FASTLOOP_ONCE=1 bash "$F/fastloop.sh"
/usr/bin/grep -q AUTOPILOT-GAP "$SG_STATE/cycle.log" && ok "autopilot gap logged" || ko "no AUTOPILOT-GAP line"
sleep 5; rm -rf "$T"; exit $fail
