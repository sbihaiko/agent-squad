#!/bin/bash
# Regression test for the issue_state door (#1429): the rows it prints are this profile's repository's cards only.
# A project may hold cards of several repositories and an issue number repeats between them, so a card of another
# repository on the same board is never named -- not by the whole-board read, and not by a cache row that names it.
# Hermetic: a stub gh answers the Status field and the items route through the --jq the caller passes; its own SG_STATE.
# mirrors: issue_state.py
# usage: bash test-issue-state-door.sh [issue_state.py]
src=${1:-$(dirname "$0")/issue_state.py}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0; trap 'rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_STATE=$T/state SG_OWNER=o SG_PROJECT=7 SG_REPO=o/r SG_BOARD_CACHE=$T/no-cache.json
mkdir -p "$T/bin" "$SG_STATE"
# the board: #10 is this repository's Doing card, #11 the Doing card of another repository on the same project
cat > "$T/items.json" <<'J'
[{"id":"PVTI_A","content":{"number":10,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Doing"}}]},
 {"id":"PVTI_B","content":{"number":11,"repository_url":"https://api.github.com/repos/o/other"},"fields":[{"name":"Status","value":{"name":"Doing"}}]},
 {"id":"PVTI_C","content":{"number":12,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Todo"}}]}]
J
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
jqf=""; prev=""; all="$*"
for a in "$@"; do [ "$prev" = "--jq" ] && jqf="$a"; prev="$a"; done
case "$all" in
  *"/fields"*) echo '[{"id":"F1","name":"Status","options":[{"id":"o1","name":"Todo"},{"id":"o2","name":"Doing"}]}]'; exit 0;;
  *"/items"*) if [ -n "$jqf" ]; then jq -r "$jqf" "$T_ITEMS"; else cat "$T_ITEMS"; fi; exit 0;;
esac
exit 1
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" T_ITEMS="$T/items.json"
door() { python3 -I "$here/issue_state.py" "$@" 2>/dev/null; }
# ---- the whole-board read: another repository's card is not this board ----
out=$(door)
printf '%s\n' "$out" | /usr/bin/grep -qx $'10\tDoing' && ok "the profile's own Doing card is read" || ko "the profile's own Doing card is read (out: $out)"
printf '%s\n' "$out" | /usr/bin/grep -qx $'12\tTodo' && ok "the profile's own Todo card is read" || ko "the profile's own Todo card is read (out: $out)"
printf '%s\n' "$out" | /usr/bin/grep -q $'^11\t' && ko "another repository's card is named by the door" || ok "another repository's card is not named by the door"
[ -z "$(door --state 11)" ] && ok "another repository's card carries no state for this profile" || ko "another repository's card carries no state: $(door --state 11)"
[ "$(door --state 10)" = Doing ] && ok "--state answers this repository's card" || ko "--state answers this repository's card: $(door --state 10)"
# ---- the hub cache: a row that names another repository is not this board's card ----
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing"},{"n":11,"st":"Doing","r":"o/other"},{"n":12,"st":"Todo"}]}' "$(date +%s)" > "$T/hub.json"
cached=$(SG_BOARD_CACHE="$T/hub.json" door)
printf '%s\n' "$cached" | /usr/bin/grep -qx $'10\tDoing' && ok "a real Doing card with no live run stays reported" || ko "a real Doing card with no live run stays reported (rows: $cached)"
printf '%s\n' "$cached" | /usr/bin/grep -qx $'12\tTodo' && ok "a real Todo row stays reported" || ko "a real Todo row stays reported (rows: $cached)"
printf '%s\n' "$cached" | /usr/bin/grep -q $'^11\t' && ko "a cache row of another repository is named" || ok "a cache row of another repository is not named"
# the same row through --kinds: another repository's kind never reaches the dispatcher
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing","k":"bug"},{"n":11,"st":"Doing","k":"bug","r":"o/other"}]}' "$(date +%s)" > "$T/kinds.json"
kinds=$(SG_BOARD_CACHE="$T/kinds.json" door --kinds)
printf '%s\n' "$kinds" | /usr/bin/grep -qx $'10\tbug' && ok "--kinds names this repository's kind" || ko "--kinds names this repository's kind (rows: $kinds)"
printf '%s\n' "$kinds" | /usr/bin/grep -q $'^11\t' && ko "--kinds names another repository's card" || ok "--kinds names another repository's card: never"
[ "$fail" = 0 ] && echo "PASS test-issue-state-door" || { echo "FAIL test-issue-state-door"; exit 1; }
