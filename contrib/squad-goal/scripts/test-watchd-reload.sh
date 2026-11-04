#!/bin/bash
# `watchd.sh reload` (#873) takes a new plugin install without a stop and start: SIGHUP to the supervisor, which ends the
# fast and comment lanes at their pass boundary, re-reads env.sh so SG_SQUAD names the install the registry names now, and
# starts them again. A live run in the supervisor's group survives, and watch.log names the old and the new install once.
# Offline: a temp skill with a stub env.sh that resolves SG_SQUAD from a temp registry, and stub lanes that log what they see.
# The watch stub never exits by itself, as the real watch.sh never does: only the supervisor's TERM ends it.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME/.claude/plugins"
st=$T/st; mkdir -p "$st"; K=$T/skill; S=$K/scripts; mkdir -p "$S"
old=$T/install-old; new=$T/install-new; mkdir -p "$old" "$new"
registry() { printf '{"plugins":{"agent-squad@agent-squad":[{"installPath":"%s"}]}}\n' "$1" > "$HOME/.claude/plugins/installed_plugins.json"; }
registry "$old"
. "$d/driver-tree.sh"; trap 'bash "$S/watchd.sh" stop >/dev/null 2>&1; kill "$(cat "$st/live.pid" 2>/dev/null)" 2>/dev/null; driver_tree_kill "$T"; rm -rf "$T"' EXIT
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
cat > "$S/env.sh" <<EOF
SG_STATE=$st; SG_PROFILE=
if [ -z "\${SG_SQUAD:-}" ]; then SG_SQUAD=\$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])"); fi
export SG_STATE SG_PROFILE SG_SQUAD
EOF
printf '#!/bin/bash\ntrap "exit 0" TERM\necho "watch $SG_SQUAD" >> "$SG_STATE/lanes.log"\n[ -e "$SG_STATE/live.pid" ] || { sleep 600 & echo $! > "$SG_STATE/live.pid"; }\nwhile :; do sleep 1; done\n' > "$S/watch.sh"
printf '#!/bin/bash\necho "fast $SG_SQUAD" >> "$SG_STATE/lanes.log"\nsleep 600\n' > "$S/fastloop.sh"
printf '#!/bin/bash\necho "cq $SG_SQUAD" >> "$SG_STATE/lanes.log"\nsleep 600\n' > "$S/cqflush.sh"
chmod +x "$S"/*.sh
count() { n=$(/usr/bin/grep -c -F -- "$1" "$2" 2>/dev/null); echo "${n:-0}"; }
lanes() { count "$1" "$st/lanes.log"; }
# Each wait is bounded at 10 s, and there are four of them, so the suite's own worst case stays under the harness's 60 s call
# deadline (tests/slowcap.py): a wait that cannot end must fail this suite by its own line, never park the whole run.
waitfor() { for _ in $(seq 1 20); do eval "$1" && return 0; sleep 0.5; done; return 1; }

bash "$S/watchd.sh" reload >"$T/r0" 2>&1; rc=$?
{ [ "$rc" = 0 ] && /usr/bin/grep -q "not running" "$T/r0"; } && ok "a reload on a stopped watchd says it is not running and exits 0" || ko "a reload on a stopped watchd: rc=$rc $(cat "$T/r0")"

bash "$S/watchd.sh" start >/dev/null 2>&1 || ko "watchd did not start"
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

bash "$S/watchd.sh" stop >/dev/null 2>&1
[ $fail = 0 ] && echo "test-watchd-reload: all checks green" || { echo "test-watchd-reload: failed"; exit 1; }
