#!/bin/bash
# A dead supervisor is not a stopped loop (#959). A `start` while an orphaned lane still runs refuses and names the lane,
# and a `reload` on a supervisor that died relights the loop. A deliberate `stop` leaves it down, as it did.
# Offline: a temp skill with a stub env.sh and stub lanes that sleep in short steps, so no child outlives its lane.
# An orphaned lane is one whose shell exited while the lane ran: pid 1 holds it, as it holds the lanes of a dead supervisor.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
st=$T/st; S=$T/skill/scripts; mkdir -p "$st" "$S"
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
printf 'SG_STATE=%s; SG_PROFILE=; SG_SQUAD=%s\nexport SG_STATE SG_PROFILE SG_SQUAD\n' "$st" "$T/skill" > "$S/env.sh"
printf '#!/bin/bash\necho "watch" >> "$SG_STATE/lanes.log"\nmkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"\nwhile :; do sleep 0.5; done\n' > "$S/watch.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/fastloop.pid"\nwhile :; do sleep 0.5; done\n' > "$S/fastloop.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/cqflush.pid"\nwhile :; do sleep 0.5; done\n' > "$S/cqflush.sh"
chmod +x "$S"/*.sh
lp=
. "$d/driver-tree.sh"; trap 'bash "$S/watchd.sh" stop >/dev/null 2>&1; [ -n "$lp" ] && kill -9 "$lp" 2>/dev/null; driver_tree_kill "$T"; rm -rf "$T"' EXIT
waitfor() { for _ in $(seq 1 40); do eval "$1" && return 0; sleep 0.25; done; return 1; }
orphan() { SG_STATE="$st" bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$1"; }
ppid_of() { ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }

lp=$(orphan "$S/watch.sh")
waitfor '[ "$(ppid_of "$lp")" = 1 ]' && ok "the lane outlives its shell and is held by pid 1" || ko "the lane was not orphaned"
echo 999999 > "$st/watchd.pid"

out=$(bash "$S/watchd.sh" start 2>&1); rc=$?
{ [ "$rc" != 0 ] && echo "$out" | /usr/bin/grep -q "refused" && echo "$out" | /usr/bin/grep -q "$lp"; } && ok "start refuses and names the orphaned lane" || ko "start with an orphan: rc=$rc $out"
[ "$(cat "$st/watchd.pid" 2>/dev/null)" = 999999 ] && ok "the refused start leaves watchd.pid alone" || ko "the refused start changed watchd.pid"
[ "$(bash "$S/watchd.sh" status 2>/dev/null)" = "watchd not running" ] && ok "the refused start starts no supervisor" || ko "the refused start started a supervisor"
bash "$S/watchd.sh" stop >/dev/null 2>&1

kill -9 "$lp" 2>/dev/null; waitfor '! kill -0 '"$lp"' 2>/dev/null' && ok "the orphaned lane is gone" || ko "the orphaned lane stayed"; lp=
echo 999999 > "$st/watchd.pid"; : > "$st/lanes.log"
bash "$S/watchd.sh" reload >"$T/r1" 2>&1; rc=$?
{ [ "$rc" = 0 ] && bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q "^watchd running pid="; } && ok "reload on a dead supervisor relights the loop" || ko "reload on a dead supervisor: rc=$rc $(cat "$T/r1") status: $(bash "$S/watchd.sh" status 2>/dev/null)"
waitfor '/usr/bin/grep -q "^watch$" "$st/lanes.log" 2>/dev/null' && ok "the watch lane starts under the relit supervisor" || ko "no watch lane after the relight"

bash "$S/watchd.sh" stop >/dev/null 2>&1
bash "$S/watchd.sh" reload >"$T/r2" 2>&1; rc=$?
{ [ "$rc" = 0 ] && /usr/bin/grep -q "not running" "$T/r2" && [ "$(bash "$S/watchd.sh" status 2>/dev/null)" = "watchd not running" ]; } && ok "reload after a deliberate stop starts nothing" || ko "reload after a stop relit the loop: $(cat "$T/r2")"

# A lane is one profile's by its state directory's pid file, whichever copy of the skill it runs from (#1313).
X=$T/cross; mkdir -p "$X/a" "$X/b"
for c in repo live; do
  mkdir -p "$X/$c"; cp "$d/watchd.sh" "$X/$c/"
  printf ': "${SG_STATE:=%s}"; SG_PROFILE=; SG_SQUAD=%s\nexport SG_STATE SG_PROFILE SG_SQUAD\n' "$X/a" "$X" > "$X/$c/env.sh"
  printf '#!/bin/bash\nmkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"\nwhile :; do sleep 0.5; done\n' > "$X/$c/watch.sh"
  printf '#!/bin/bash\necho $$ > "$SG_STATE/fastloop.pid"\nwhile :; do sleep 0.5; done\n' > "$X/$c/fastloop.sh"
  printf '#!/bin/bash\necho $$ > "$SG_STATE/cqflush.pid"\nwhile :; do sleep 0.5; done\n' > "$X/$c/cqflush.sh"
  chmod +x "$X/$c"/*.sh
done
cross_orphan() { SG_STATE="$2" bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$1"; }

lb=$(cross_orphan "$X/live/watch.sh" "$X/b")
waitfor '[ "$(ppid_of "$lb")" = 1 ] && [ -s "$X/b/locks/watch/pid" ]' && ok "a live-copy lane of profile b is orphaned" || ko "the live-copy lane of profile b was not orphaned"
out=$(SG_STATE="$X/a" bash "$X/repo/watchd.sh" start 2>&1); rc=$?
[ "$rc" = 0 ] && ok "another profile's orphan does not refuse a start" || ko "a start of profile a was refused by profile b's orphan: rc=$rc $out"
SG_STATE="$X/a" bash "$X/repo/watchd.sh" stop >/dev/null 2>&1
kill -9 "$lb" 2>/dev/null; waitfor '! kill -0 '"$lb"' 2>/dev/null' || ko "the profile b lane stayed"

la=$(cross_orphan "$X/live/watch.sh" "$X/a")
waitfor '[ "$(ppid_of "$la")" = 1 ] && [ -s "$X/a/locks/watch/pid" ]' && ok "a live-copy lane of profile a is orphaned" || ko "the live-copy lane of profile a was not orphaned"
out=$(SG_STATE="$X/a" bash "$X/repo/watchd.sh" start 2>&1); rc=$?
{ [ "$rc" != 0 ] && echo "$out" | /usr/bin/grep -q "refused" && echo "$out" | /usr/bin/grep -q "pid $la"; } && ok "a start from the repository copy refuses and names the live-copy lane of its profile" || ko "a start from the repository copy did not see the live-copy lane: rc=$rc $out"
kill -9 "$la" 2>/dev/null; waitfor '! kill -0 '"$la"' 2>/dev/null' || ko "the profile a lane stayed"

[ $fail = 0 ] && echo "test-watchd-supervisor: all checks green" || { echo "test-watchd-supervisor: failed"; exit 1; }
