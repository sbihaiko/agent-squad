#!/bin/bash
# Regression test for stack.sh's Blocked-by parse (#1803): the section is a LIST of bullets, so reading it stops
# at the first line that is neither blank nor a bullet. A trailing prose line that merely names another issue
# ("Blocks #N: ...") is information, not an edge -- reading it as one turns two issues that mention each other
# into a dependency cycle in which neither is ever dispatched, and health.sh never reports the stall.
# usage: bash test-stack.sh [path/to/stack.sh]
src=${1:-$(dirname "$0")/stack.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/co/runs/.hub"; cp "$src" "$T/s/stack.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"   # stack.sh reads the fork branch through it (#1836)
printf 'export SG_REPO=x/y SG_CHECKOUT=%s SG_HUB_DIR=%s SG_BASE=main\n' "$T/co" "$T/co/runs/.hub" > "$T/s/env.sh"
echo '{"items":[]}' > "$T/co/runs/.hub/hub-board.json"
# #1792 and #1793 are each blocked only by #1791 (closed); each one's trailing prose names the other one.
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"issues/1792 -q .body"*) printf 'Fix the colour map.\n\n## Blocked by\n\n- #1791 -- the map lands first\n\nBlocks #1793: the strip reads this map, so this lands first.\n';;
  *"issues/1793 -q .body"*) printf 'Fix the strip.\n\n## Blocked by\n\n- #1791 -- the map lands first\n\nBlocks #1792: it reads the numbering this normalises.\n';;
  *"issues/1800 -q .body"*) printf 'Work.\n\n## Blocked by\n\n- #1791 -- closed one\n- #1797 -- open one\n- #1798 -- open two\n';;
  *"issues/1801 -q .body"*) printf 'Work.\n\n## Blocked by\n\n- #1791 -- closed one\n\n## Order\n\n- #1797 -- a note about sequencing, not an edge\n';;
  *"issues/"*" -q .body"*) ;;
  *"issues/1791 -q .state"*) echo closed;;
  *"issues/"*" -q .state"*) echo open;;
  *"pulls?state=open"*) echo '[]';;
  *"check-runs"*) echo 'false';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { bash "$T/s/stack.sh" "$1" 2>&1; }
r=$(run 1793)
case "$r" in READY*) ok "#1793: the prose line naming #1792 is not a blocker";; *) ko "#1793 did not read READY: '$r'";; esac
r=$(run 1792)
case "$r" in READY*) ok "#1792: the prose line naming #1793 is not a blocker";; *) ko "#1792 did not read READY: '$r'";; esac
r=$(run 1800)
case "$r" in *"2 open blockers: #1797 #1798"*) ok "every bulleted blocker is still read, and only those";; *) ko "the bullet list was not read whole: '$r'";; esac
r=$(run 1801)
case "$r" in READY*) ok "the section still ends at the next '## ' heading";; *) ko "a later section leaked in: '$r'";; esac
rm -rf "$T"; exit $fail
