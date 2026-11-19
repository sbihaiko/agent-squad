#!/bin/bash
# seconds: 5.04
# A driver that dies leaves one line naming how it died (#1439), and the next start brings the loop back.
# Offline: a temp skill with stub lanes that exit once orphaned, as the real lanes do.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
st=$T/st; S=$T/skill/scripts; mkdir -p "$st" "$S"
cp "$d/watchd.sh" "$S/watchd.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"
printf 'SG_STATE=%s; SG_PROFILE=; SG_SQUAD=%s\nexport SG_STATE SG_PROFILE SG_SQUAD\n' "$st" "$T/skill" > "$S/env.sh"
mkdir -p "$T/skill/squad"; : > "$T/skill/squad/__init__.py"; printf 'def free_runs(): return 0\n' > "$T/skill/squad/caps.py"
cat > "$S/watch.sh" <<'EOF'
#!/bin/bash
mkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"
while :; do sleep 0.5; [ "$(ps -o ppid= -p $$ | tr -d ' ')" = 1 ] && exit 0; done
EOF
cat > "$S/fastloop.sh" <<'EOF'
#!/bin/bash
echo $$ > "$SG_STATE/fastloop.pid"
while :; do sleep 0.5; [ "$(ps -o ppid= -p $$ | tr -d ' ')" = 1 ] && exit 0; done
EOF
cat > "$S/cqflush.sh" <<'EOF'
#!/bin/bash
echo $$ > "$SG_STATE/cqflush.pid"
while :; do sleep 0.5; [ "$(ps -o ppid= -p $$ | tr -d ' ')" = 1 ] && exit 0; done
EOF
chmod +x "$S"/*.sh
. "$d/driver-tree.sh"; trap 'bash "$S/watchd.sh" stop >/dev/null 2>&1; driver_tree_kill "$T"; rm -rf "$T"' EXIT
waitfor() { for _ in $(seq 1 40); do eval "$1" && return 0; sleep 0.25; done; return 1; }

bash "$S/watchd.sh" start >/dev/null 2>&1
waitfor 'bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q "^watchd running"' && ok "the driver runs" || ko "the driver did not start"
pid=$(cat "$st/watchd.pid" 2>/dev/null)

kill -9 "$pid" 2>/dev/null
waitfor '! kill -0 '"$pid"' 2>/dev/null' && ok "the driver is dead" || ko "the driver survived SIGKILL"
waitfor '/usr/bin/grep -qF "WATCHD driver '"$pid"' ended: 137 (SIGKILL)" "$st/watch.log" 2>/dev/null' \
  && ok "a killed driver writes its cause and the time" || ko "no cause line for pid $pid: $(cat "$st/watch.log" 2>/dev/null)"

waitfor '! kill -0 "$(cat "$st/fastloop.pid" 2>/dev/null)" 2>/dev/null' || ko "a stub lane outlived its driver"
bash "$S/watchd.sh" start >"$T/restart" 2>&1; rc=$?
new=$(cat "$st/watchd.pid" 2>/dev/null)
{ [ "$rc" = 0 ] && [ -n "$new" ] && [ "$new" != "$pid" ] && bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q "^watchd running"; } \
  && ok "the next pass starts the driver again" || ko "the next pass did not restart it: rc=$rc $(cat "$T/restart")"

[ "$fail" = 0 ] && echo "PASS test-watchd-death" || echo "FAIL test-watchd-death"
exit "$fail"
