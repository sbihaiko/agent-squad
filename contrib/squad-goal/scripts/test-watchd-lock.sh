#!/bin/bash
# seconds: 6.12
# One supervisor per state directory (#932). `watchd.sh run` wrote its own pid to watchd.pid with no lock, so two `run`
# calls whose `alive` checks both ran before either wrote the file both started a lane, and a supervisor that removed the
# file on its way out let a later `start` add a third beside the two still running. Three cycles, three fast lanes, three
# janitors and three comment flushers then worked one state directory; that happened for hours.
# Offline: a temp state dir, a stub env.sh, a stub watch.sh that marks each cycle.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); . "$d/driver-tree.sh"; trap 'for p in $supA $supB $supC $supD $spvs; do kill -9 $p 2>/dev/null; done; driver_tree_kill "$T"; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME"
. "$d/caps.sh"   # SG_PS: the process table read through squad.procs, the reader the shipped script uses (#1508)
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" "$d" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
# The copy reads the process table through caps.sh beside the real watchd.sh, never the `ps` fallback (#1508): a sandbox
# refuses the setuid table binary, and a copy that fell back to it read its own supervisor as gone (#2781).
t=t.replace('"$(dirname "$0")/caps.sh"','"%s/caps.sh"'%sys.argv[3])
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)',
            'gh_aggregate() { return 1; }')
p.write_text(t)
PY
printf '#!/bin/bash\necho "cycle ran" >> "$SG_STATE/watch.log"\nsleep 60\n' > "$T/watch.sh"
printf '#!/bin/bash\nsleep 60\n' > "$T/fastloop.sh"
printf '#!/bin/bash\nsleep 60\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh"
cycles() { c=$(/usr/bin/grep -c "cycle ran" "$SG_STATE/watch.log" 2>/dev/null); echo "${c:-0}"; }
# drive: a supervised driver, the one form the loop runs (#1587); DRV is the driver pid once it holds the slot.
drive() { bash "$T/watchd.sh" supervise >/dev/null 2>&1 & spv=$!; spvs="$spvs $spv"; DRV=
  for _ in $(seq 1 50); do p=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null && { DRV=$p; return 0; }; sleep 0.2; done; return 1; }

# the first supervisor takes the slot
drive; supA=$DRV; spvA=$spv
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do [ "$(cycles)" -ge 1 ] && break; sleep 0.2; done
[ "$(cycles)" -ge 1 ] && ok "the first run starts its lane" || ko "the first run started no lane"

# a second supervised driver, while the first is alive, must start nothing and say why
bash "$T/watchd.sh" supervise >/dev/null 2>&1 & supB=$!; spvs="$spvs $supB"
for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supB" 2>/dev/null || break; sleep 0.2; done
! kill -0 "$supB" 2>/dev/null && ok "a second run exits instead of supervising" || { ko "the second run is still supervising"; kill -9 "$supB" 2>/dev/null; }
/usr/bin/grep -q "refused" "$SG_STATE/watch.log" 2>/dev/null && ok "the refusal is one line in watch.log" || ko "the second run said nothing"
a=$(cycles); sleep 1.5; [ "$(cycles)" = "$a" ] && ok "the second run starts no second lane" || ko "the second run started another lane"
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$supA" ] && ok "the refused run leaves watchd.pid with the live supervisor" || ko "the refused run took watchd.pid ($(cat "$SG_STATE/watchd.pid" 2>/dev/null), live $supA)"
/usr/bin/grep -qE "WATCHD run [0-9]+ refused: supervisor $supA holds the loop" "$SG_STATE/watch.log" 2>/dev/null \
  && ok "the refusal names the run it refused and the holder" || ko "the refusal does not name the refused run and the holder"

# a run from another copy of the skill (the repository's scripts beside the live ones) is still a second supervisor (#1312):
# its path differs from the holder's, and the liveness proof matched one absolute path, so it read the live holder as
# stale, took the slot and ran a second loop; stopping it then removed watchd.pid under the live supervisor
mkdir -p "$T/other"; cp "$T/watchd.sh" "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/other/"
c=$(cycles); bash "$T/other/watchd.sh" supervise >/dev/null 2>&1 & supD=$!; spvs="$spvs $supD"
for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supD" 2>/dev/null || break; sleep 0.2; done
! kill -0 "$supD" 2>/dev/null && ok "a run from another copy exits instead of supervising" || { ko "a run from another copy took the slot"; kill -TERM "$supD" 2>/dev/null; sleep 1; }
[ "$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null)" = "$supA" ] && ok "the slot still names the live supervisor" || ko "the slot names $(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null), live $supA"
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$supA" ] && ok "watchd.pid still names the live supervisor" || ko "watchd.pid names '$(cat "$SG_STATE/watchd.pid" 2>/dev/null)', live $supA"

# a slot whose recorded pid is not a live watchd.sh run is stale: the next run takes it over
kill -TERM "$supA" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supA" 2>/dev/null || break; sleep 0.3; done; sleep 0.5
wait_sup() { for _ in $(seq 1 30); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.2; done; return 1; }
wait_sup "$spvA"
mkdir -p "$SG_STATE/watchd.lock"; printf '999999\n' > "$SG_STATE/watchd.lock/pid"
b=$(cycles); drive; supC=$DRV
for _ in 1 2 3 4 5 6 7 8 9 10; do [ "$(cycles)" -gt "$b" ] && break; sleep 0.2; done
[ "$(cycles)" -gt "$b" ] && ok "a stale slot is taken over" || ko "a stale slot kept the loop down"
[ "$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null)" = "$supC" ] && ok "the takeover names the new supervisor" || ko "the slot still names $(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null)"
kill -TERM "$supC" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$supC" 2>/dev/null || break; sleep 0.3; done; sleep 0.5
[ ! -e "$SG_STATE/watchd.lock" ] && ok "an exiting supervisor releases the slot" || ko "the slot survived the supervisor"
rm -rf "$T"; exit $fail
