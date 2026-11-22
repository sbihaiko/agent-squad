#!/bin/bash
# seconds: 4.92
# Rule W (#959): watchd.pid names no live supervisor and a lane of this scripts dir still runs, so the janitor raises WATCHD-ORPHAN
# in alerts.log, once for the state. A live supervisor, or no lane, raises nothing.
# Stubbed gh and board; a temp state dir and a temp scripts dir holding the janitor and stub lanes. usage: bash test-janitor-watchd-orphan.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); fail=0; lp=; sup=
. "$d/driver-tree.sh"; trap '[ -n "$lp" ] && kill -9 "$lp" 2>/dev/null; [ -n "$sup" ] && kill -9 "$sup" 2>/dev/null; driver_tree_kill "$T"; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/live/scripts"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\nwhile :; do sleep 0.5; done\n' > "$T/s/watch.sh"; cp "$T/s/watch.sh" "$T/s/watchd.sh"; chmod +x "$T/s/watch.sh" "$T/s/watchd.sh"
printf 'skill\n' > "$T/live/SKILL.md"; printf '{}\n' > "$T/live/scripts/models.json"; touch -t 202001010000 "$T/live/SKILL.md" "$T/live/scripts/models.json"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/live" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" >/dev/null 2>&1; }
alerts() { /usr/bin/grep -c 'WATCHD-ORPHAN' "$T/st/alerts.log" 2>/dev/null || echo 0; }
orphan() { bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$1"; }
. "$d/caps.sh"   # SG_PS: the process table read through squad.procs (#1508): a sandbox refuses the setuid table binary
ppid_of() { "$SG_PS" -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }

echo 999999 > "$T/st/watchd.pid"
lp=$(orphan "$T/s/watch.sh"); sleep 0.5
[ "$(ppid_of "$lp")" = 1 ] || ko "the lane was not orphaned"
pass env; [ "$(alerts)" = 1 ] && ok "a dead supervisor with a lane still running raises one WATCHD-ORPHAN" || ko "no alert, or not one ($(alerts))"
/usr/bin/grep 'WATCHD-ORPHAN' "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -q "$lp" && ok "the alert names the lane" || ko "the alert does not name the lane"
pass env; [ "$(alerts)" = 1 ] && ok "the same state raises no second alert" || ko "the same state alerted again ($(alerts))"

kill -9 "$lp" 2>/dev/null; lp=
pass env; pass env; [ "$(alerts)" = 1 ] && ok "no lane left raises nothing" || ko "a state with no lane alerted ($(alerts))"

bash "$T/s/watchd.sh" run >/dev/null 2>&1 & sup=$!; sleep 0.5; echo "$sup" > "$T/st/watchd.pid"
lp=$(orphan "$T/s/watch.sh"); sleep 0.5
pass env; [ "$(alerts)" = 1 ] && ok "a live supervisor raises nothing, lanes or not" || ko "a live supervisor was read as dead ($(alerts))"

[ $fail = 0 ] && echo "test-janitor-watchd-orphan: all checks green" || { echo "test-janitor-watchd-orphan: failed"; exit 1; }
