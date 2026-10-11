#!/bin/bash
# Regression test for health.sh: a down watchd alerts once, a live one clears it, a silent cycle.log is reported. Stubbed env.sh/gh/notifier. usage: bash test-health.sh [health.sh]
src=${1:-$(dirname "$0")/health.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# hermetic: a fork gate (or any shell) exports SG_FORK_*; health.sh would then scan the real fork repo and add a fork-parked cause
unset SG_FORK_DIR SG_FORK_BRANCH SG_FORK_BASE SG_FORK_ORPHAN_MINUTES
mkdir -p "$T/s" "$T/st/auto" "$T/co" "$T/bin"; cp "$src" "$T/s/health.sh"
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y\n' "$T/st" "$T/co" > "$T/s/env.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"; export HEALTH_NOTIFY="$T/notify"
touch "$T/st/cycle.log" "$T/st/active-runs.txt"; git -C "$T/co" init -q 2>/dev/null
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH watchd-down' && ok "watchd down -> alert" || ko "no watchd-down: $o"
[ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "one notification per cause (watchd, fastloop)" || ko "notifications: $(cat "$T/notify.calls")"
bash "$T/s/health.sh" >/dev/null; [ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "same cause: no second alert" || ko "alerted twice"
printf '#!/bin/bash\nsleep 60\n' > "$T/s/watchd.sh"; bash "$T/s/watchd.sh" run & wp=$!; echo $wp > "$T/st/watchd.pid"; bash "$T/s/fastloop.sh" 2>/dev/null; printf '#!/bin/bash\nsleep 60\n' > "$T/s/fastloop.sh"; bash "$T/s/fastloop.sh" & fp=$!; echo $fp > "$T/st/fastloop.pid"; sleep 1
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'watchd-down cleared' && ok "watchd back -> cleared" || ko "not cleared: $o"
touch -t 202001010000 "$T/st/cycle.log"; o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH watch-stalled' && ok "silent cycle.log -> watch-stalled" || ko "no stall: $o"
mkdir -p "$T/co/runs/run-n"; echo r > "$T/co/runs/run-n/request.txt"; touch -t 202001010000 "$T/co/runs/run-n/request.txt"; echo run-n > "$T/st/active-runs.txt"
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH never-started-runs' && ok "record-less run -> never-started-runs" || ko "no never-started: $o"
kill $wp $fp 2>/dev/null; rm -rf "$T"; exit $fail
