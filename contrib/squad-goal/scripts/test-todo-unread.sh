#!/bin/bash
# Regression test for sg_todo_cards with a go list (#800): a go-labelled issue whose card the board read does not carry
# is named on stderr and in alerts.log, and a known node id that answers Todo rescues it into the Todo list. Hermetic:
# a stub gh answers the fields, the items route (through the --jq the caller passes) and the node read; its own SG_STATE.
# usage: bash test-todo-unread.sh
here=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_STATE=$T/state SG_OWNER=o SG_PROJECT=7 SG_REPO=o/r SG_TODO=Todo
mkdir -p "$T/bin" "$SG_STATE"
cat > "$T/items.json" <<'J'
[{"id":"PVTI_801","content":{"number":801,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Todo"}}]}]
J
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
jqf=""; prev=""; all="$*"
for a in "$@"; do [ "$prev" = "--jq" ] && jqf="$a"; prev="$a"; done
case "$all" in
  *graphql*) case "$all" in *PVTI_800*) echo '{"data":{"node":{"isArchived":false,"content":{"number":800},"fieldValueByName":{"name":"Todo"}}}}';; *) echo '{"data":{"node":null}}';; esac; exit 0;;
  *"/fields"*) echo '[{"id":"F1","name":"Status","options":[{"id":"o1","name":"Todo"},{"id":"o2","name":"Testing"}]}]'; exit 0;;
  *"/items"*) if [ -n "$jqf" ]; then jq -r "$jqf" "$T_ITEMS"; else cat "$T_ITEMS"; fi; exit 0;;
esac
exit 1
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" T_ITEMS="$T/items.json"
# an earlier read saw #800 with the item id PVTI_800; the current read carries only #801
echo '{"800":"PVTI_800"}' > "$SG_STATE/rest-known-o-7-o_r.json"
. "$here/startability.sh" >/dev/null 2>&1
out=$(sg_todo_cards "800,801,802" 2>"$T/err"); rc=$?
[ "$rc" = 0 ] && ok "a go list reads the board" || ko "a go list reads the board (rc $rc)"
echo "$out" | /usr/bin/grep -qx 801 && ok "a card the read carries in Todo is listed" || ko "a card the read carries in Todo is listed"
echo "$out" | /usr/bin/grep -qx 800 && ok "a go issue the node id answers Todo for is rescued into the list" || ko "a go issue the node id answers Todo for is rescued into the list"
echo "$out" | /usr/bin/grep -qx 802 && ko "an issue nothing reads is not listed" || ok "an issue nothing reads is not listed"
/usr/bin/grep -q "#802" "$T/err" && ok "the unread issue is named on stderr" || ko "the unread issue is named on stderr"
/usr/bin/grep -q "#802" "$SG_STATE/alerts.log" 2>/dev/null && ok "the unread issue is named in alerts.log" || ko "the unread issue is named in alerts.log"
plain=$(sg_todo_cards 2>/dev/null); echo "$plain" | /usr/bin/grep -qx 801 && ok "no go list keeps the plain read" || ko "no go list keeps the plain read"
rm -rf "$T"
[ "$fail" = 0 ] && echo "PASS test-todo-unread" || { echo "FAIL test-todo-unread"; exit 1; }
