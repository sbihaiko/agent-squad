#!/bin/bash
# Regression test for sg_todo_cards with a go list (#800): a go-labelled issue whose card the board read does not carry
# is named on stderr and in alerts.log, and a known node id that answers Todo rescues it into the Todo list. Hermetic:
# a stub gh answers the fields, the items route (through the --jq the caller passes), the node read and the issue's own
# read; its own SG_STATE.
# #1031: the go list that names an issue was read at another time, and the rows it is checked against come from a cache
# shared by the owner and project. An issue the list still names go-labelled is named that only when the issue itself
# agrees: one it answers closed, or without the go label, is said once on stderr and never in alerts.log, while a card the
# board really cannot read still alerts.
# usage: bash test-todo-unread.sh
here=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_STATE=$T/state SG_OWNER=o SG_PROJECT=7 SG_REPO=o/r SG_TODO=Todo SG_FRONTIER_LABEL=go
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
  *"/issues/"*) case "$all" in
      *"/issues/801"*) echo '{"number":801,"state":"open","labels":[{"name":"bug"},{"name":"go"}]}'; exit 0;;
      *"/issues/803"*) echo '{"number":803,"state":"closed","labels":[{"name":"go"}]}'; exit 0;;
      *"/issues/804"*) echo '{"number":804,"state":"open","labels":[{"name":"bug"}]}'; exit 0;;
      *"/issues/805"*) echo '{"number":805,"state":"open","labels":[{"name":"bug"},{"name":"go"}]}'; exit 0;;
    esac; exit 1;;
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
# #1031: the go list names #803 (the issue itself answers closed), #804 (open, no go label) and #805 (open and go-labelled,
# with no card). Only #805 is named go-labelled, and the board really cannot read its card.
alerts() { /usr/bin/grep -c "FRONTIER unread" "$SG_STATE/alerts.log" 2>/dev/null; }
before=$(alerts); out=$(sg_todo_cards "803,804,805" 2>"$T/err2"); rc=$?; after=$(alerts)
[ "$rc" = 0 ] && ok "a go list that disagrees with the issues still reads" || ko "a go list that disagrees with the issues still reads (rc $rc)"
/usr/bin/grep -q "go-labelled #805 has no readable card" "$T/err2" && ok "only the issue that answers go-labelled and has no card is named unread" || ko "only the issue that answers go-labelled and has no card is named unread (stderr: $(tr '\n' ' ' < "$T/err2"))"
[ "${after:-0}" = "$(( ${before:-0} + 1 ))" ] && ok "one alert line, for the card the board really cannot read" || ko "one alert line, for the card the board really cannot read (before ${before:-0}, after ${after:-0})"
/usr/bin/grep -q "#805" "$SG_STATE/alerts.log" 2>/dev/null && ok "the alert names the go-labelled issue" || ko "the alert names the go-labelled issue"
/usr/bin/grep -q "#803" "$SG_STATE/alerts.log" 2>/dev/null && ko "a closed issue is never named go-labelled in alerts.log" || ok "a closed issue is never named go-labelled in alerts.log"
/usr/bin/grep -q "#804" "$SG_STATE/alerts.log" 2>/dev/null && ko "an issue with no go label is never named go-labelled in alerts.log" || ok "an issue with no go label is never named go-labelled in alerts.log"
echo "$out" | /usr/bin/grep -qx 803 && ko "a closed issue is not listed as Todo" || ok "a closed issue is not listed as Todo"
/usr/bin/grep -q "#803 is closed" "$T/err2" && ok "the closed issue is said once on stderr" || ko "the closed issue is said once on stderr"
/usr/bin/grep -q "#804 is no go label" "$T/err2" && ok "the issue with no go label is said once on stderr" || ko "the issue with no go label is said once on stderr"
# #1031: closing an issue drops it from the go set on the next cycle. The rows are the hub cache, read before the close,
# and still carry #803's Todo card; the issue itself answers closed, so it leaves the rows the pass reads, while #801,
# which answers open and go-labelled, stays.
printf '{"t": %s, "repo": "o/r", "items": [{"n":801,"st":"Todo"},{"n":803,"st":"Todo"}]}' "$(date +%s)" > "$T/hub.json"
cached=$(SG_BOARD_CACHE="$T/hub.json" sg_todo_cards "801,803" 2>"$T/err3")
echo "$cached" | /usr/bin/grep -qx 801 && ok "an open go-labelled issue the cache rows carry stays in the go set" || ko "an open go-labelled issue the cache rows carry stays in the go set (rows: $(echo $cached))"
echo "$cached" | /usr/bin/grep -qx 803 && ko "a closed issue the cache rows still carry drops out of the go set" || ok "a closed issue the cache rows still carry drops out of the go set"
/usr/bin/grep -q "#803 is closed" "$T/err3" && ok "the dropped issue is said once on stderr from the cache path" || ko "the dropped issue is said once on stderr from the cache path (stderr: $(tr '\n' ' ' < "$T/err3"))"
rm -rf "$T"
[ "$fail" = 0 ] && echo "PASS test-todo-unread" || { echo "FAIL test-todo-unread"; exit 1; }
