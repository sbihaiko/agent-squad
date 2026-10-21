#!/bin/bash
# Rule (#744): a frontier that holds Todo cards and starts nothing for a bound says so once, in alerts.log, naming the Todo
# count and the reasons of its own last idle line. The frontier writes $SG_STATE/frontier-idle (<epoch> TAB <line>) on an
# idle pass and drops it when a pass dispatches, so the janitor reads that fact and never the frontier again. It repeats at
# most once per bound (SG_FRONTIER_IDLE_SECONDS, default 3600), and it never dispatches.
# Stubbed gh and board; a temp state dir. usage: bash test-janitor-frontier-idle.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" >/dev/null 2>&1; }
alerts() { cat "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -c 'FRONTIER-IDLE'; }
line='FRONTIER idle: 3 todo, 0 startable (2 no go label, 0 held, 0 sibling, 0 no slot, 1 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)'
mark() { printf '%s\t%s\n' "$1" "$line" > "$T/st/frontier-idle"; }   # <epoch>: the streak's start and the frontier's own line
pass env; [ "$(alerts)" = 0 ] && ok "no streak raises nothing" || ko "a pass with no streak alerted ($(alerts))"
mark $(( $(date +%s) - 60 ))
pass env; [ "$(alerts)" = 0 ] && ok "a streak younger than the bound raises nothing" || ko "a short streak alerted ($(alerts))"
mark $(( $(date +%s) - 3601 ))
pass env; [ "$(alerts)" = 1 ] && ok "a streak past the bound raises one alert" || ko "no alert past the bound ($(alerts))"
cat "$T/st/alerts.log" | /usr/bin/grep 'FRONTIER-IDLE' | /usr/bin/grep -q '3 todo, 0 startable (2 no go label' && ok "the alert names the Todo count and the reasons" || ko "the alert does not carry the idle line: $(cat "$T/st/alerts.log")"
pass env; [ "$(alerts)" = 1 ] && ok "the same streak raises nothing again inside the bound" || ko "the same streak alerted twice ($(alerts))"
mark $(( $(date +%s) - 7201 ))
pass env; [ "$(alerts)" = 2 ] && ok "a streak past a second bound raises again" || ko "a second bound raised nothing ($(alerts))"
rm -f "$T/st/frontier-idle"   # a pass that dispatched: the frontier drops the file
mark $(( $(date +%s) - 10 )); pass env; [ "$(alerts)" = 2 ] && ok "a streak that restarted raises nothing" || ko "a restarted streak alerted ($(alerts))"
printf 'no line here\n' > "$T/st/frontier-idle"; pass env; [ "$(alerts)" = 2 ] && ok "a marker with no idle line raises nothing" || ko "a marker with no line alerted ($(alerts))"
JANITOR_DRY=1 pass env; mark $(( $(date +%s) - 7201 )); JANITOR_DRY=1 pass env
[ "$(alerts)" = 2 ] && [ ! -e "$T/st/frontier-idle-alert" ] && ok "a dry pass writes no alert bound" || ko "a dry pass wrote the bound: $(alerts), $(/bin/ls "$T/st")"
[ "$fail" = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
