#!/bin/bash
# seconds: 7.76
# Guard for #1587: one driver per state directory, and a stop that reaches every driver.
# A driver outside the slot kept a loop of its own: it was visible to neither `alive` nor `stop`, so two loops ran the
# janitor, the frontier and the dispatch on one state directory and burned the GitHub REST budget. Five cases, each in
# its own temp state directory:
#   1. a driver with no supervisor ends itself and launches no lane;
#   2. a second supervised driver refuses while the holder runs, naming the holder;
#   3. a live driver outside the slot refuses the start/relight path, naming the driver's pid;
#   4. a stop reaches every driver of the state directory, including a driver whose supervisor was killed, and never
#      reaches another state directory's loop;
#   5. a driver whose script file is gone ends within 10 s with the refusal line.
# Offline: a temp skill with a stub env.sh, stub lanes, a stub issue list and a fake squad install. No network.
# usage: bash test-watchd-singleton.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; spvs=; DRV=; spv=
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
repo=$(cd "$d/../../.." && pwd)
. "$d/watchd-fixture.sh"   # the fake install and the process reader (#1514)
. "$d/driver-tree.sh"      # the tree kill, and DT_PS, the reader a sandbox holds (#989, #1508)
[ -n "${DT_PS:-}" ] || { echo "test-watchd-singleton: driver-tree.sh set no DT_PS reader" >&2; exit 1; }
command -v "$DT_PS" >/dev/null 2>&1 || { echo "test-watchd-singleton: the DT_PS reader $DT_PS does not resolve" >&2; exit 1; }
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
waitfor() { for _ in $(seq 1 40); do eval "$1" && return 0; sleep 0.25; done; return 1; }
ppid_of() { "$DT_PS" -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }
lanes() { c=$(/usr/bin/grep -c '^' "$1/lanes.log" 2>/dev/null); echo "${c:-0}"; }

S=$T/skill/scripts; mkdir -p "$S"
fake_squad "$T/skill"; reader=$(fake_reader "$repo")
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { echo "[]"; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read answers "none"
# The drain reads run liveness from squad/run_liveness.py under SG_CHECKOUT; with no active runs it answers idle (#2488).
printf ': "${SG_STATE:=%s}"; SG_PROFILE=; SG_SQUAD=%s; SG_CHECKOUT=%s\n%s\nexport SG_STATE SG_PROFILE SG_SQUAD SG_CHECKOUT\n' "$T/default" "$T/skill" "$repo" "$reader" > "$S/env.sh"
printf '#!/bin/bash\necho "watch" >> "$SG_STATE/lanes.log"\nmkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"\nwhile :; do sleep 0.5; done\n' > "$S/watch.sh"
printf '#!/bin/bash\necho "fast" >> "$SG_STATE/lanes.log"\necho $$ > "$SG_STATE/fastloop.pid"\nwhile :; do sleep 0.5; done\n' > "$S/fastloop.sh"
printf '#!/bin/bash\necho "cq" >> "$SG_STATE/lanes.log"\necho $$ > "$SG_STATE/cqflush.pid"\nwhile :; do sleep 0.5; done\n' > "$S/cqflush.sh"
chmod +x "$S"/*.sh
# drive <watchd.sh>: a supervised driver on $SG_STATE; DRV is its pid once it holds the slot, spv its supervisor's.
drive() {
  bash "$1" supervise >/dev/null 2>&1 & spv=$!; spvs="$spvs $spv"; DRV=
  for _ in $(seq 1 60); do p=$(cat "$SG_STATE/watchd.lock/pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null && { DRV=$p; return 0; }; sleep 0.2; done
  return 1
}
lane_pid() { cat "$1/$2" 2>/dev/null; }
supervises() { "$DT_PS" -axwwo command= 2>/dev/null | PAT="$1 supervise" /usr/bin/awk 'index($0, ENVIRON["PAT"]) { n++ } END { print n + 0 }'; }
lanes_up() { local s=$1; for _ in $(seq 1 60); do [ "$(lanes "$s")" = 3 ] && return 0; sleep 0.25; done; return 1; }
stopall() {
  for st in "$T"/st*; do [ -d "$st" ] && SG_STATE="$st" bash "$S/watchd.sh" stop >/dev/null 2>&1; done
  for p in $spvs; do kill -9 "$p" 2>/dev/null; done
  driver_tree_kill "$T"; return 0
}
trap 'stopall; rm -rf "$T"' EXIT

# 1. A driver whose parent is not a live `watchd.sh supervise` ends itself and starts no lane.
st=$T/st1; export SG_STATE=$st; mkdir -p "$st"
bash "$S/watchd.sh" run >/dev/null 2>&1 & rp=$!
waitfor '! kill -0 '"$rp"' 2>/dev/null' && ok "a driver with no supervisor exits" || { ko "a driver with no supervisor kept running"; kill -9 "$rp" 2>/dev/null; }
/usr/bin/grep -qF "WATCHD run $rp refused: no live supervisor" "$st/watch.log" 2>/dev/null \
  && ok "the driver says it has no live supervisor" || ko "no refusal line: $(cat "$st/watch.log" 2>/dev/null)"
{ [ ! -e "$st/fastloop.pid" ] && [ ! -e "$st/cqflush.pid" ] && [ ! -e "$st/locks/watch/pid" ]; } \
  && ok "the refused driver launches no lane" || ko "the refused driver launched a lane"
[ ! -e "$st/watchd.lock" ] && ok "the refused driver releases the slot it took" || ko "the refused driver left the slot"

# 2. A second supervised driver refuses while the holder runs, and names the holder.
st=$T/st2; export SG_STATE=$st; mkdir -p "$st"
drive "$S/watchd.sh" && ok "a supervised driver takes the slot" || ko "the first driver did not take the slot"
h=$DRV; lanes_up "$st" || ko "the first driver's three lanes did not start"
bash "$S/watchd.sh" supervise >/dev/null 2>&1 & spv2=$!; spvs="$spvs $spv2"
waitfor '! kill -0 '"$spv2"' 2>/dev/null' && ok "a second supervised driver exits" || { ko "a second driver kept running"; kill -9 "$spv2" 2>/dev/null; }
/usr/bin/grep -qF "refused: supervisor $h holds the loop" "$st/watch.log" 2>/dev/null \
  && ok "the second driver refuses and names the holder's pid" || ko "the refusal does not name the holder: $(cat "$st/watch.log" 2>/dev/null)"
[ "$(cat "$st/watchd.lock/pid" 2>/dev/null)" = "$h" ] && ok "the slot still names the holder" || ko "the slot names $(cat "$st/watchd.lock/pid" 2>/dev/null), the holder is $h"
[ "$(lanes "$st")" = 3 ] && ok "the second driver starts no second lane" || ko "the second driver started a lane ($(lanes "$st") lane starts)"
SG_STATE=$st bash "$S/watchd.sh" stop >/dev/null 2>&1

# 3. A live driver outside the slot refuses the start/relight path and names its pid (#1587 debt a).
st=$T/st3; export SG_STATE=$st; mkdir -p "$st"
drive "$S/watchd.sh" || ko "the driver did not start for the outside-the-slot case"
lanes_up "$st" || ko "the driver's three lanes did not start"
live=$DRV; rm -rf "$st/watchd.lock"
{ kill -0 "$live" 2>/dev/null && [ ! -e "$st/watchd.lock" ]; } && ok "the driver is alive with no slot" || ko "the driver did not survive the deleted slot"
before=$(supervises "$S/watchd.sh"); bw=$(cat "$st/locks/watch/pid" 2>/dev/null)
out=$(SG_STATE=$st bash "$S/watchd.sh" start 2>&1); rc=$?
{ [ "$rc" != 0 ] && echo "$out" | /usr/bin/grep -q "refused" && echo "$out" | /usr/bin/grep -q "$live"; } \
  && ok "start refuses and names the driver outside the slot" || ko "start with a driver outside the slot: rc=$rc $out"
[ "$(supervises "$S/watchd.sh")" = "$before" ] && ok "the refused start creates no second supervisor" || ko "the refused start created a supervisor"
n=0; for f in "$st"/watchd.runs/*; do [ -e "$f" ] && n=$((n + 1)); done
[ "$n" = 1 ] && ok "the refused start creates no second driver" || ko "the refused start created a driver ($n registered)"
[ -z "$(cat "$st/watchd.lock/pid" 2>/dev/null)" ] && ok "the refused start creates no slot" || ko "the refused start created a slot"
[ "$(cat "$st/locks/watch/pid" 2>/dev/null)" = "$bw" ] && ok "the refused start creates no lane" || ko "the refused start restarted a lane"
SG_STATE=$st bash "$S/watchd.sh" stop >/dev/null 2>&1

# 4. A stop reaches a driver whose supervisor was killed, ends its lanes and never reaches another state directory (#1587
#    debt b). Nothing relaunches the stopped loop.
st=$T/st4; export SG_STATE=$st; mkdir -p "$st"
drive "$S/watchd.sh" || ko "the driver did not start for the stop case"
lanes_up "$st" || ko "the driver's three lanes did not start"
live=$DRV; kill -9 "$spv" 2>/dev/null
waitfor '[ "$(ppid_of '"$live"')" = 1 ]' && ok "the driver is alive under pid 1" || ko "the driver was not orphaned (parent $(ppid_of "$live"))"
rm -rf "$st/watchd.lock"
{ kill -0 "$live" 2>/dev/null && [ ! -e "$st/watchd.lock" ]; } && ok "the driver is alive and holds no slot before the stop" || ko "the driver is not alive and slotless before the stop"
for l in locks/watch/pid fastloop.pid cqflush.pid; do [ -n "$(lane_pid "$st" "$l")" ] && kill -0 "$(lane_pid "$st" "$l")" 2>/dev/null && : || ko "the lane $l is not live before the stop"; done
b4=$(lanes "$st")
st2=$T/st5; mkdir -p "$st2"
SG_STATE=$st2 drive "$S/watchd.sh" || ko "the driver of the second state directory did not start"
other=$DRV
out=$(SG_STATE=$st bash "$S/watchd.sh" stop 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the stop succeeds with a driver outside the slot" || ko "the stop failed: rc=$rc $out"
waitfor '! kill -0 '"$live"' 2>/dev/null' && ok "the stop ends the orphaned driver" || ko "the orphaned driver survived the stop"
gone=1; for l in locks/watch/pid fastloop.pid cqflush.pid; do p=$(lane_pid "$st" "$l"); [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null || gone=0; done
[ "$gone" = 1 ] && ok "the stop ends every lane of that driver" || ko "a lane of the stopped driver survived"
{ kill -0 "$other" 2>/dev/null && [ "$(cat "$st2/watchd.lock/pid" 2>/dev/null)" = "$other" ]; } \
  && ok "the stop never reaches another state directory's driver" || ko "the stop reached the other state directory's driver"
sleep 3
c=$(lanes "$st")
{ [ "$c" = "$b4" ] && [ ! -e "$st/watchd.lock" ]; } && ok "nothing relaunches the stopped loop" || ko "the stopped loop relaunched ($c lane starts, was $b4)"
SG_STATE=$st2 bash "$S/watchd.sh" stop >/dev/null 2>&1

# 5. A driver whose script file is gone ends within 10 s with the refusal line (#1587 debt c).
cp -R "$T/skill" "$T/copy"
st=$T/st6; export SG_STATE=$st; mkdir -p "$st"
drive "$T/copy/scripts/watchd.sh" || ko "the driver did not start from the temp copy"
lanes_up "$st" || ko "the temp copy's three lanes did not start"
live=$DRV; rm -f "$T/copy/scripts/watchd.sh"
waitfor '! kill -0 '"$live"' 2>/dev/null' && ok "a driver whose script is gone exits within the bound" || { ko "the deleted-script driver kept running"; kill -9 "$live" 2>/dev/null; }
/usr/bin/grep -qF "WATCHD run $live refused: this script is gone" "$st/watch.log" 2>/dev/null \
  && ok "the driver says its script is gone" || ko "no deleted-script refusal line: $(cat "$st/watch.log" 2>/dev/null)"
gone=1; for l in locks/watch/pid fastloop.pid cqflush.pid; do p=$(lane_pid "$st" "$l"); [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null || gone=0; done
[ "$gone" = 1 ] && ok "the deleted-script driver takes its lanes with it" || ko "a lane of the deleted-script driver survived"

# 6. A driver that wrote no record under watchd.runs is still named through its lanes (#1634): `start` refuses it and
#    `stop` ends it, so a state directory never keeps a loop the driver list does not read.
st=$T/st7; export SG_STATE=$st; mkdir -p "$st"
drive "$S/watchd.sh" || ko "the driver did not start for the unrecorded case"
lanes_up "$st" || ko "the unrecorded driver's three lanes did not start"
live=$DRV; kill -9 "$spv" 2>/dev/null
waitfor '[ "$(ppid_of '"$live"')" = 1 ]' && ok "the unrecorded driver is orphaned under pid 1" || ko "the unrecorded driver was not orphaned (parent $(ppid_of "$live"))"
rm -rf "$st/watchd.lock" "$st/watchd.runs"; rm -f "$st/watchd.pid"
{ kill -0 "$live" 2>/dev/null && [ ! -e "$st/watchd.runs" ] && [ ! -e "$st/watchd.pid" ]; } && ok "the driver is alive with no slot, no record and no pid file" || ko "the driver did not survive the deleted record"
out=$(SG_STATE=$st bash "$S/watchd.sh" start 2>&1); rc=$?
{ [ "$rc" != 0 ] && echo "$out" | /usr/bin/grep -q "refused" && echo "$out" | /usr/bin/grep -q "$live"; } \
  && ok "start refuses and names a driver the record does not list" || ko "start with an unrecorded driver: rc=$rc $out"
out=$(SG_STATE=$st bash "$S/watchd.sh" stop 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the stop succeeds with an unrecorded driver" || ko "the stop failed: rc=$rc $out"
waitfor '! kill -0 '"$live"' 2>/dev/null' && ok "the stop ends the unrecorded driver" || ko "the unrecorded driver survived the stop"
gone=1; for l in locks/watch/pid fastloop.pid cqflush.pid; do p=$(lane_pid "$st" "$l"); [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null || gone=0; done
[ "$gone" = 1 ] && ok "the stop ends every lane of the unrecorded driver" || ko "a lane of the unrecorded driver survived"

# Nothing this suite started outlives it, on the pass path and the fail path alike (#1514).
stopall
left() { driver_tree_pids "$T" | tr '\n' ' ' | sed 's/ $//'; }
clean() { for _ in $(seq 1 20); do [ -z "$(left)" ] && return 0; sleep 0.25; done; return 1; }
clean && ok "no process of this suite outlives it" || ko "processes of this suite outlived it: $(left)"

[ $fail = 0 ] && echo "test-watchd-singleton: all checks green" || { echo "test-watchd-singleton: failed"; exit 1; }
