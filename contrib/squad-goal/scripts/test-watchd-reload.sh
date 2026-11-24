#!/bin/bash
# seconds: 5.96
# `watchd.sh reload` (#873) takes a new plugin install without a stop and start: SIGHUP to the supervisor, which ends the
# fast and comment lanes at their pass boundary, re-reads env.sh so SG_SQUAD names the install the registry names now, and
# starts them again. A live run in the supervisor's group survives, and watch.log names the old and the new install once.
# Offline: a temp skill with a stub env.sh that resolves SG_SQUAD from a temp registry, and stub lanes that log what they see.
# The watch stub never exits by itself, as the real watch.sh never does: only the supervisor's TERM ends it.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME/.claude/plugins"
st=$T/st; mkdir -p "$st"; K=$T/skill; S=$K/scripts; mkdir -p "$S"
repo=$(cd "$d/../../.." && pwd)
. "$d/watchd-fixture.sh"   # the squad package and the process reader a fake install needs (#1514)
old=$T/install-old; new=$T/install-new; mkdir -p "$old" "$new"
fake_squad "$old"; fake_squad "$new"
reader=$(fake_reader "$repo")
registry() { printf '{"plugins":{"agent-squad@agent-squad":[{"installPath":"%s"}]}}\n' "$1" > "$HOME/.claude/plugins/installed_plugins.json"; }
registry "$old"
. "$d/driver-tree.sh"
# The suite ends every process it started, on the pass path and the fail path alike, before its temp folder goes (#1514).
# stopall is idempotent and tolerant of an install the suite never finished building (a fixture error leaves $S partial), so
# the fail path still ends cleanly and reports its own failure; the check at the end calls it before it asserts, so the
# assertion reads the cleaned state on both paths, never a cleanup the trap still owes.
stopall() {
  if [ -f "$S/watchd.sh" ]; then bash "$S/watchd.sh" stop >/dev/null 2>&1; fi
  driver_tree_kill "$T"; return 0
}
trap 'stopall; kill "$(cat "$st/live.pid" 2>/dev/null)" 2>/dev/null; rm -rf "$T"' EXIT
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
cat > "$S/env.sh" <<EOF
SG_STATE=$st; SG_PROFILE=
if [ -z "\${SG_SQUAD:-}" ]; then SG_SQUAD=\$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])"); fi
export SG_STATE SG_PROFILE SG_SQUAD
$reader
EOF
printf '#!/bin/bash\necho $$ > "$SG_STATE/watch.pid"\ntrap "exit 0" TERM\necho "watch $SG_SQUAD" >> "$SG_STATE/lanes.log"\n[ -e "$SG_STATE/live.pid" ] || { sleep 600 & echo $! > "$SG_STATE/live.pid"; }\nwhile :; do sleep 1; done\n' > "$S/watch.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/fastloop.pid"\necho "fast $SG_SQUAD" >> "$SG_STATE/lanes.log"\nsleep 600 & echo $! > "$SG_STATE/fastloop-sleep.pid"\nwait\n' > "$S/fastloop.sh"
printf '#!/bin/bash\necho $$ > "$SG_STATE/cqflush.pid"\necho "cq $SG_SQUAD" >> "$SG_STATE/lanes.log"\nsleep 600 & echo $! > "$SG_STATE/cqflush-sleep.pid"\nwait\n' > "$S/cqflush.sh"
chmod +x "$S"/*.sh
count() { n=$(/usr/bin/grep -c -F -- "$1" "$2" 2>/dev/null); echo "${n:-0}"; }
lanes() { count "$1" "$st/lanes.log"; }
# Each wait is bounded at 10 s, and there are four of them, so the suite's own worst case stays under the harness's 60 s call
# deadline (tests/slowcap.py): a wait that cannot end must fail this suite by its own line, never park the whole run.
waitfor() { for _ in $(seq 1 20); do eval "$1" && return 0; sleep 0.5; done; return 1; }

bash "$S/watchd.sh" reload >"$T/r0" 2>&1; rc=$?
{ [ "$rc" = 0 ] && /usr/bin/grep -q "not running" "$T/r0"; } && ok "a reload on a stopped watchd says it is not running and exits 0" || ko "a reload on a stopped watchd: rc=$rc $(cat "$T/r0")"

out=$(bash "$S/watchd.sh" start 2>&1); rc=$?
[ "$rc" = 0 ] || ko "watchd did not start: $out"
waitfor '[ "$(lanes "watch $old")" -ge 1 ]' && ok "the watch lane starts with the old install" || ko "the watch lane never started: $(cat "$st/lanes.log" 2>/dev/null)"
sup=$(cat "$st/watchd.pid" 2>/dev/null)
registry "$new"
bash "$S/watchd.sh" reload >"$T/r1" 2>&1; rc=$?
[ "$rc" = 0 ] && /usr/bin/grep -q "reload sent" "$T/r1" && ok "a reload on a running watchd exits 0" || ko "a reload on a running watchd: rc=$rc $(cat "$T/r1")"
waitfor '[ "$(lanes "watch $new")" -ge 1 ]' && ok "the watch lane runs with the install the registry names now" || ko "the watch lane kept the old install: $(cat "$st/lanes.log" 2>/dev/null)"
waitfor '[ "$(lanes "fast $new")" -ge 1 ]' && ok "the fast lane runs with the new install" || ko "the fast lane kept the old install"
waitfor '[ "$(lanes "cq $new")" -ge 1 ]' && ok "the comment lane runs with the new install" || ko "the comment lane kept the old install"
[ "$(cat "$st/watchd.pid" 2>/dev/null)" = "$sup" ] && ok "the supervisor keeps its pid across the reload" || ko "the reload replaced the supervisor"

n=$(count "WATCHD RELOAD " "$st/watch.log")
[ "$n" = 1 ] && ok "watch.log has one WATCHD RELOAD line" || ko "watch.log has $n WATCHD RELOAD lines"
/usr/bin/grep "^WATCHD RELOAD " "$st/watch.log" 2>/dev/null | /usr/bin/grep -q "$old.* -> $new$" && ok "the reload line names the old and the new install" || ko "the reload line: $(/usr/bin/grep "^WATCHD RELOAD " "$st/watch.log" 2>/dev/null)"

lp=$(cat "$st/live.pid" 2>/dev/null)
[ -n "$lp" ] && kill -0 "$lp" 2>/dev/null && ok "a live run in the group survives the reload" || ko "the live run died on the reload"

# Nothing the suite started outlives it, on the pass path and the fail path alike (#1514): the cleanup runs first, so the
# assertion below reads the state after it, and the trap's repeat of the same cleanup changes nothing. The supervisor tree
# is found by the temp root its commands name. A lane, or a lane's child, that reparents to pid 1 names no root, so the
# recorded pids are read too, and they are read before the cleanup stops anything: every pid here names a process the suite
# started, and the criterion is that all of them are gone, not only the ones the temp root happens to name.
recorded=$(cat "$st"/*.pid 2>/dev/null)
stopall
left() { driver_tree_pids "$T" | tr '\n' ' ' | sed 's/ $//'; }
alive() { for p in $recorded; do kill -0 "$p" 2>/dev/null && printf '%s ' "$p"; done; }
clean() { for _ in $(seq 1 20); do [ -z "$(left)" ] && [ -z "$(alive)" ] && return 0; sleep 0.25; done; return 1; }
clean && ok "no process of this suite outlives it" || ko "processes of this suite outlived it: tree $(left) recorded $(alive)"
if [ -z "$lp" ] || ! kill -0 "$lp" 2>/dev/null; then ok "the live run ends with the suite"
else ko "the live run outlived the suite (pid $lp)"; kill -9 "$lp" 2>/dev/null; fi

[ $fail = 0 ] && echo "test-watchd-reload: all checks green" || { echo "test-watchd-reload: failed"; exit 1; }
