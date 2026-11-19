#!/bin/bash
# seconds: 4.61
# The bare-checkout rule (#2306): the profile's checkout carries core.bare = true, so every git command in its work tree fails. The janitor
# raises CHECKOUT-BARE in alerts.log, once for the episode, and names the checkout. A checkout that is not bare raises nothing
# and ends the episode, so the next bare state alerts again.
# Stubbed gh and board; a temp state dir and a temp checkout. usage: bash test-janitor-checkout-bare.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The janitor copy runs from a temp dir, never the real scripts dir: its process scans name the folder it runs in, and the live
# loop's processes must not match. Every sibling module it may import comes with it, so a new import never breaks the suite.
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/live"
cp "$(dirname "$src")"/*.py "$(dirname "$src")"/board-definition.json "$T/s/"; cp "$src" "$T/s/janitor.py"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
git init -q "$T/co"; mkdir -p "$T/co/runs"; co=$(cd "$T/co" && pwd -P)
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/live" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" >/dev/null 2>&1; }
# grep -c prints 0 and exits 1 when no line matches, and prints nothing when the log is absent: one count either way.
alerts() { local n; n=$(/usr/bin/grep -c 'CHECKOUT-BARE' "$T/st/alerts.log" 2>/dev/null); echo "${n:-0}"; }
mk=$T/st/checkout-bare-alert
bare() { git config --file "$T/co/.git/config" core.bare "$1"; }

pass env; [ "$(alerts)" = 0 ] && ok "a checkout that is not bare raises nothing" || ko "a work-tree checkout alerted ($(alerts))"
bare true
# A dry pass on a bare checkout with no marker: the alert would fire on a real pass, so only the dry flag holds it back.
pass env JANITOR_DRY=1; [ "$(alerts)" = 0 ] && [ ! -e "$mk" ] && ok "a dry pass raises nothing and writes no marker" || ko "a dry pass alerted or marked ($(alerts))"
pass env; [ "$(alerts)" = 1 ] && ok "core.bare = true on the checkout raises one CHECKOUT-BARE" || ko "no alert, or not one ($(alerts))"
# SG_CHECKOUT is the variable janitor.py resolves the module-global co from, and $co is its resolved path: the alert must name it.
/usr/bin/grep 'CHECKOUT-BARE' "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -qF "$co" && ok "the alert names the checkout" || ko "the alert does not name $co: $(cat "$T/st/alerts.log" 2>/dev/null)"
pass env; [ "$(alerts)" = 1 ] && ok "the same episode raises no second alert" || ko "the same episode alerted again ($(alerts))"
# A git call that fails reads nothing: it keeps the marker, so the next good read raises no second alert for the same episode.
mkdir -p "$T/failgit"; printf '#!/bin/bash\nexit 1\n' > "$T/failgit/git"; chmod +x "$T/failgit/git"
pass env PATH="$T/failgit:$PATH"; [ -e "$mk" ] && ok "a failed git read keeps the episode's marker" || ko "a failed git read cleared the marker"
pass env; [ "$(alerts)" = 1 ] && ok "the episode a failed read interrupted raises no second alert" || ko "a failed read restarted the episode ($(alerts))"
bare false
pass env; [ "$(alerts)" = 1 ] && ok "a repaired checkout raises nothing" || ko "a repaired checkout alerted ($(alerts))"
bare true
pass env; [ "$(alerts)" = 2 ] && ok "a new episode raises a new alert" || ko "a new episode did not alert ($(alerts))"
# A linked worktree of a bare repository carries core.bare = true in the shared config, and its work tree works: no alert.
rm -f "$mk"; git init -q --bare "$T/b.git"; git -C "$T/b.git" worktree add -q --orphan "$T/wt" >/dev/null 2>&1
mkdir -p "$T/wt/runs"
SG_STATE="$T/st" SG_CHECKOUT="$T/wt" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" \
  SG_LIVE_DIR="$T/live" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" >/dev/null 2>&1
[ "$(git -C "$T/wt" config --bool --get core.bare)" = true ] || ko "the fixture worktree does not carry core.bare = true"
[ "$(alerts)" = 2 ] && ok "a linked worktree of a bare repository raises nothing" || ko "a linked worktree of a bare repository alerted ($(alerts))"

[ $fail = 0 ] && echo "test-janitor-checkout-bare: all checks green" || { echo "test-janitor-checkout-bare: failed"; exit 1; }
