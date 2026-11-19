#!/bin/bash
# Rule B (#2306): the profile's checkout carries core.bare = true, so every git command in its work tree fails. The janitor
# raises CHECKOUT-BARE in alerts.log, once for the episode, and names the checkout. A checkout that is not bare raises nothing
# and ends the episode, so the next bare state alerts again.
# Stubbed gh and board; a temp state dir and a temp checkout. usage: bash test-janitor-checkout-bare.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/live"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
git init -q "$T/co"; mkdir -p "$T/co/runs"; co=$(cd "$T/co" && pwd -P)
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/live" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" >/dev/null 2>&1; }
alerts() { /usr/bin/grep -c 'CHECKOUT-BARE' "$T/st/alerts.log" 2>/dev/null || echo 0; }
bare() { git config --file "$T/co/.git/config" core.bare "$1"; }

pass env; [ "$(alerts)" = 0 ] && ok "a checkout that is not bare raises nothing" || ko "a work-tree checkout alerted ($(alerts))"
bare true
pass env; [ "$(alerts)" = 1 ] && ok "core.bare = true on the checkout raises one CHECKOUT-BARE" || ko "no alert, or not one ($(alerts))"
/usr/bin/grep 'CHECKOUT-BARE' "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -qF "$co" && ok "the alert names the checkout" || ko "the alert does not name $co: $(cat "$T/st/alerts.log" 2>/dev/null)"
pass env; [ "$(alerts)" = 1 ] && ok "the same episode raises no second alert" || ko "the same episode alerted again ($(alerts))"
pass env JANITOR_DRY=1; [ "$(alerts)" = 1 ] && ok "a dry pass raises nothing" || ko "a dry pass alerted ($(alerts))"
bare false
pass env; [ "$(alerts)" = 1 ] && ok "a repaired checkout raises nothing" || ko "a repaired checkout alerted ($(alerts))"
bare true
pass env; [ "$(alerts)" = 2 ] && ok "a new episode raises a new alert" || ko "a new episode did not alert ($(alerts))"

[ $fail = 0 ] && echo "test-janitor-checkout-bare: all checks green" || { echo "test-janitor-checkout-bare: failed"; exit 1; }
