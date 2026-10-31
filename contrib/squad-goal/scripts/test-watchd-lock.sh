#!/bin/bash
# One supervisor per state directory (#932). `watchd.sh run` wrote its own pid to watchd.pid with no lock, so two `run`
# calls whose `alive` checks both ran before either wrote the file both started a lane, and a supervisor that removed the
# file on its way out let a later `start` add a third beside the two still running. Three cycles, three fast lanes, three
# janitors and three comment flushers then worked one state directory; that happened for hours.
# Offline: a temp state dir, a stub env.sh, a stub watch.sh that marks each cycle.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'for p in $supA $supB $supC; do kill -9 $p 2>/dev/null; done; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME"
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
p.write_text(t)
PY
printf '#!/bin/bash\necho "cycle ran" >> "$SG_STATE/watch.log"\nsleep 60\n' > "$T/watch.sh"
printf '#!/bin/bash\nsleep 60\n' > "$T/fastloop.sh"
printf '#!/bin/bash\nsleep 60\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh"
cycles() { /usr/bin/grep -c "cycle ran" "$SG_STATE/watch.log" 2>/dev/null || echo 0; }

# the first supervisor takes the slot
( exec bash "$T/watchd.sh" run ) & supA=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ "$(cycles)" -ge 1 ] && break; sleep 0.2; done
[ "$(cycles)" -ge 1 ] && ok "the first run starts its lane" || ko "the first run started no lane"

# a second run, while the first is alive, must start nothing and say why
( exec bash "$T/watchd.sh" run ) & supB=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supB" 2>/dev/null || break; sleep 0.2; done
! kill -0 "$supB" 2>/dev/null && ok "a second run exits instead of supervising" || { ko "the second run is still supervising"; kill -9 "$supB" 2>/dev/null; }
/usr/bin/grep -q "refused" "$SG_STATE/watch.log" 2>/dev/null && ok "the refusal is one line in watch.log" || ko "the second run said nothing"
a=$(cycles); sleep 1.5; [ "$(cycles)" = "$a" ] && ok "the second run starts no second lane" || ko "the second run started another lane"
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$supA" ] && ok "the refused run leaves watchd.pid with the live supervisor" || ko "the refused run took watchd.pid ($(cat "$SG_STATE/watchd.pid" 2>/dev/null), live $supA)"
/usr/bin/grep -q "$supB" "$SG_STATE/watch.log" 2>/dev/null && ok "the refusal names the run it refused" || ko "the refusal does not name the refused run"

# a slot whose recorded pid is not a live watchd.sh run is stale: the next run takes it over
kill -TERM "$supA" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supA" 2>/dev/null || break; sleep 0.3; done; sleep 0.5
mkdir -p "$SG_STATE/watchd.lock"; printf '999999\n' > "$SG_STATE/watchd.lock/pid"
b=$(cycles); ( exec bash "$T/watchd.sh" run ) & supC=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ "$(cycles)" -gt "$b" ] && break; sleep 0.2; done
[ "$(cycles)" -gt "$b" ] && ok "a stale slot is taken over" || ko "a stale slot kept the loop down"
[ "$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null)" = "$supC" ] && ok "the takeover names the new supervisor" || ko "the slot still names $(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null)"
kill -TERM "$supC" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supC" 2>/dev/null || break; sleep 0.3; done; sleep 0.5
[ ! -e "$SG_STATE/watchd.lock" ] && ok "an exiting supervisor releases the slot" || ko "the slot survived the supervisor"
rm -rf "$T"; exit $fail
