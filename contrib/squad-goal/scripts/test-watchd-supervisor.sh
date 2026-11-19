#!/bin/bash
# seconds: 2.68
# A dead supervisor is not a stopped loop (#959). A `start` while an orphaned lane still runs refuses and names the lane,
# and a `reload` on a supervisor that died relights the loop. A deliberate `stop` leaves it down, as it did.
# Offline: a temp skill with a stub env.sh and stub lanes that sleep in short steps, so no child outlives its lane.
# An orphaned lane is one whose shell exited while the lane ran: pid 1 holds it, as it holds the lanes of a dead supervisor.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
repo=$(cd "$d/../../.." && pwd)
. "$d/watchd-fixture.sh"   # the squad package and the process reader a fake install needs (#1514)
st=$T/st; S=$T/skill/scripts; mkdir -p "$st" "$S"
fake_squad "$T/skill"; reader=$(fake_reader "$repo")
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
printf 'SG_STATE=%s; SG_PROFILE=; SG_SQUAD=%s; SG_LANE_BACKOFF=1; SG_LANE_BACKOFF_MAX=4\nexport SG_STATE SG_PROFILE SG_SQUAD SG_LANE_BACKOFF SG_LANE_BACKOFF_MAX\n%s\n' "$st" "$T/skill" "$reader" > "$S/env.sh"   # #1781: a short back-off keeps the relight cases fast
printf '#!/bin/bash\necho "watch" >> "$SG_STATE/lanes.log"\nmkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"\nwhile :; do sleep 0.5; done\n' > "$S/watch.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/fastloop.pid"\nwhile :; do sleep 0.5; done\n' > "$S/fastloop.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/cqflush.pid"\nwhile :; do sleep 0.5; done\n' > "$S/cqflush.sh"
chmod +x "$S"/*.sh
lp=; la=; lb=; X=
. "$d/driver-tree.sh"
# The suite ends every process it started, on the pass path and the fail path alike, before its temp folder goes (#1514).
# stopall is idempotent: the check at the end calls it before it asserts, so the assertion reads the cleaned state on both
# paths, and the trap's repeat on an early exit changes nothing. The orphaned lanes are held by pid 1, so a stop cannot
# reach them and each recorded pid is killed here.
stopall() {
  [ -f "$S/watchd.sh" ] && bash "$S/watchd.sh" stop >/dev/null 2>&1
  [ -n "$lp" ] && kill -9 "$lp" 2>/dev/null
  [ -n "$X" ] && [ -f "$X/repo/watchd.sh" ] && SG_STATE="$X/a" bash "$X/repo/watchd.sh" stop >/dev/null 2>&1
  [ -n "$la" ] && kill -9 "$la" 2>/dev/null
  [ -n "$lb" ] && kill -9 "$lb" 2>/dev/null
  driver_tree_kill "$T"; return 0
}
trap 'stopall; rm -rf "$T"' EXIT
waitfor() { for _ in $(seq 1 40); do eval "$1" && return 0; sleep 0.25; done; return 1; }
orphan() { SG_STATE="$st" bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$1"; }
# driver-tree.sh keeps the reader it resolved as DT_PS, a name the suite's own `unset "${!SG_@}"` never clears: a bare `ps` is
# refused inside a child's sandbox (#1508). If that coupling ever breaks, say so here rather than read the table with a
# reader the sandbox refuses and let the orphan check compare empty strings.
[ -n "${DT_PS:-}" ] || { echo "test-watchd-supervisor: driver-tree.sh set no DT_PS reader" >&2; exit 1; }
command -v "$DT_PS" >/dev/null 2>&1 || { echo "test-watchd-supervisor: the DT_PS reader $DT_PS does not resolve" >&2; exit 1; }
ppid_of() { "$DT_PS" -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }

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

# #1781: a lane that dies while watch.sh lives is started again after its back-off, with one dated line. Before, the supervisor
# started a lane only at the top of its loop, which it reaches only when watch.sh exits: a dead fast lane stayed dead for hours.
lane_lines() { /usr/bin/grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} WATCHD lane fast [0-9]+ ended: [0-9]+; started again in [0-9]+s$" "$st/watch.log" 2>/dev/null; }
waitfor '[ -s "$st/fastloop.pid" ] && kill -0 "$(cat "$st/fastloop.pid")" 2>/dev/null' || ko "no fast lane under the relit supervisor"
f1=$(cat "$st/fastloop.pid" 2>/dev/null); kill -9 "$f1" 2>/dev/null
waitfor 'f=$(cat "$st/fastloop.pid" 2>/dev/null); [ -n "$f" ] && [ "$f" != "$f1" ] && kill -0 "$f" 2>/dev/null' \
  && ok "a killed fast lane is started again while watch.sh lives" || ko "the killed fast lane was not started again"
lane_lines | /usr/bin/grep -q "WATCHD lane fast $f1 ended: 137; started again in 1s$" \
  && ok "the relight writes one dated line with the lane, its pid, its status and the back-off" || ko "no relight line: $(tail -n 3 "$st/watch.log")"
# A lane that dies at once is started again after a back-off that doubles to its cap, and the third relight in one hour
# writes one alerts.log line; later relights in the same hour write no second one.
printf '#!/bin/bash\nexit 3\n' > "$S/fastloop.sh"; kill -9 "$(cat "$st/fastloop.pid" 2>/dev/null)" 2>/dev/null
for _ in $(seq 1 120); do [ "$(lane_lines | wc -l)" -ge 4 ] && break; sleep 0.25; done
backs=$(lane_lines | sed -E 's/.* in ([0-9]+)s$/\1/' | head -n 4 | tr '\n' ' ')
[ "$backs" = "1 2 4 4 " ] && ok "the back-off doubles to its cap (1 2 4 4)" || ko "back-offs: '$backs'"
lane_lines | sed -n 3,4p | /usr/bin/grep -q "ended: 3;" && ok "a lane that dies at once is named with its status" || ko "no line names status 3: $(lane_lines | tail -n 2)"
na=$(/usr/bin/grep -c "WATCHD lane fast" "$st/alerts.log" 2>/dev/null)
[ "${na:-0}" = 1 ] && ok "the third relight in one hour writes one alert, and the fourth none" || ko "lane alerts: ${na:-0}: $(cat "$st/alerts.log" 2>/dev/null)"
printf '#!/bin/bash\necho $$ > "$SG_STATE/fastloop.pid"\nwhile :; do sleep 0.5; done\n' > "$S/fastloop.sh"

bash "$S/watchd.sh" stop >/dev/null 2>&1
bash "$S/watchd.sh" reload >"$T/r2" 2>&1; rc=$?
{ [ "$rc" = 0 ] && /usr/bin/grep -q "not running" "$T/r2" && # A stop leaves the owner mark (#1708), so the status names it after "watchd not running".
bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q "^watchd not running"; } && ok "reload after a deliberate stop starts nothing" || ko "reload after a stop relit the loop: rc=$rc $(cat "$T/r2"); status: $(bash "$S/watchd.sh" status 2>&1)"

# A lane is one profile's by its state directory's pid file, whichever copy of the skill it runs from (#1313).
X=$T/cross; mkdir -p "$X/a" "$X/b"; fake_squad "$X"
for c in repo live; do
  mkdir -p "$X/$c"; cp "$d/watchd.sh" "$X/$c/"
  printf 'gh_aggregate() { return 1; }\n' > "$X/$c/ghcall.sh"   # the one seam watchd.sh sources beside itself (ADR-0146)
  printf ': "${SG_STATE:=%s}"; SG_PROFILE=; SG_SQUAD=%s\n%s\nexport SG_STATE SG_PROFILE SG_SQUAD\n' "$X/a" "$X" "$reader" > "$X/$c/env.sh"
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
# Nothing the suite started outlives it, on the pass path and the fail path alike (#1514): the cleanup runs first, so the
# assertion below reads the state after it, and the trap's repeat of the same cleanup changes nothing. An orphaned lane is
# held by pid 1 and a lane's children name no root, so the recorded pids are read beside the tree of the temp root.
stopall
left() { driver_tree_pids "$T" | tr '\n' ' ' | sed 's/ $//'; }
clean() { for _ in $(seq 1 20); do [ -z "$(left)" ] && return 0; sleep 0.25; done; return 1; }
clean && ok "no process of this suite outlives it" || ko "processes of this suite outlived it: $(left)"
for p in "$lp" "$la" "$lb"; do
  if [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null; then ok "a recorded lane pid ends with the suite (${p:-none})"
  else ko "a recorded lane outlived the suite (pid $p)"; kill -9 "$p" 2>/dev/null; fi
done

[ $fail = 0 ] && echo "test-watchd-supervisor: all checks green" || { echo "test-watchd-supervisor: failed"; exit 1; }
