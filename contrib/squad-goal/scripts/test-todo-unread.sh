#!/bin/bash
# seconds: 0.53
# Regression test for sg_todo_cards with a go list (#800): a go-labelled issue whose card the board read does not carry
# is named on stderr and in alerts.log, and a known node id that answers Todo rescues it into the Todo list. Hermetic:
# a stub gh answers the fields, the items route (through the --jq the caller passes), the one-card read and the issue's own
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
[{"id":"PVTI_801","content":{"number":801,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Todo"}}]},{"id":"PVTI_806","content":{"number":806,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Backlog"}}]}]
J
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
jqf=""; prev=""; all="$*"; echo "$all" >> "$T_GHLOG"
for a in "$@"; do [ "$prev" = "--jq" ] && jqf="$a"; prev="$a"; done
case "$all" in
  *"/fields"*) echo '[{"id":"F1","name":"Status","options":[{"id":"o1","name":"Todo"},{"id":"o2","name":"Code Review"}]}]'; exit 0;;
  *"/issues/"*) case "$all" in
      *"/issues/801"*) echo '{"number":801,"state":"open","labels":[{"name":"bug"},{"name":"go"}]}'; exit 0;;
      *"/issues/803"*) echo '{"number":803,"state":"closed","labels":[{"name":"go"}]}'; exit 0;;
      *"/issues/804"*) echo '{"number":804,"state":"open","labels":[{"name":"bug"}]}'; exit 0;;
      *"/issues/805"*) echo '{"number":805,"state":"open","labels":[{"name":"bug"},{"name":"go"}]}'; exit 0;;
      *"/issues/806"*) echo '{"number":806,"state":"open","labels":[{"name":"go"}]}'; exit 0;;
    esac; exit 1;;
  *"/items/PVTI_800"*) echo '{"isArchived":false,"content":{"number":800},"fields":[{"name":"Status","value":{"name":"Todo"}}]}'; exit 0;;   # the one-card read (#1300, #1303)
  *"/items/PVTI_806"*) echo '{"isArchived":false,"content":{"number":806},"fields":[{"name":"Status","value":{"name":"Backlog"}}]}'; exit 0;;   # the card moved after the cache read (#1758)
  *"/items/PVTI_"*) exit 1;;   # a card the fixture does not carry
  *"/items"*) if [ -n "$jqf" ]; then jq -r "$jqf" "$T_ITEMS"; else cat "$T_ITEMS"; fi; exit 0;;
esac
exit 1
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" T_ITEMS="$T/items.json" T_GHLOG="$T/gh.log"
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
sg_todo_cards "803,804,805" >/dev/null 2>&1; [ "$(alerts)" = "$after" ] && ok "an issue already named unread for the same reason is not named again on the next pass" || ko "an issue already named unread is named again (alerts $after -> $(alerts))"
/usr/bin/grep -q "#802 is in the go list, but its issue read did not answer" "$SG_STATE/alerts.log" && ok "an issue whose own read did not answer names that read" || ko "an issue whose own read did not answer names that read"
/usr/bin/grep -q "#803" "$SG_STATE/alerts.log" 2>/dev/null && ko "a closed issue is never named go-labelled in alerts.log" || ok "a closed issue is never named go-labelled in alerts.log"
/usr/bin/grep -q "#804" "$SG_STATE/alerts.log" 2>/dev/null && ko "an issue with no go label is never named go-labelled in alerts.log" || ok "an issue with no go label is never named go-labelled in alerts.log"
echo "$out" | /usr/bin/grep -qx 803 && ko "a closed issue is not listed as Todo" || ok "a closed issue is not listed as Todo"
/usr/bin/grep -q "#803 is closed" "$T/err2" && ok "the closed issue is said once on stderr" || ko "the closed issue is said once on stderr"
/usr/bin/grep -q "#804 is no go label" "$T/err2" && ok "the issue with no go label is said once on stderr" || ko "the issue with no go label is said once on stderr"
# #1031: closing an issue drops it from the go set on the next cycle. The rows are the hub cache, read before the close,
# and still carry #803's Todo card; the issue itself answers closed, so it leaves the rows the pass reads, while #801,
# which answers open and go-labelled, stays.
printf '{"t": %s, "repo": "o/r", "items": [{"n":801,"st":"Todo","r":"o/r"},{"n":803,"st":"Todo","r":"o/r"}]}' "$(date +%s)" > "$T/hub.json"
cached=$(SG_BOARD_CACHE="$T/hub.json" sg_todo_cards "801,803" 2>"$T/err3")
echo "$cached" | /usr/bin/grep -qx 801 && ok "an open go-labelled issue the cache rows carry stays in the go set" || ko "an open go-labelled issue the cache rows carry stays in the go set (rows: $(echo $cached))"
echo "$cached" | /usr/bin/grep -qx 803 && ko "a closed issue the cache rows still carry drops out of the go set" || ok "a closed issue the cache rows still carry drops out of the go set"
/usr/bin/grep -q "#803 is closed" "$T/err3" && ok "the dropped issue is said once on stderr from the cache path" || ko "the dropped issue is said once on stderr from the cache path (stderr: $(tr '\n' ' ' < "$T/err3"))"
# #1758: a card added to the board reads the board's default Todo until the add's own column write lands, and a hub cache
# read inside that window carries it as Todo. The card's column at the source decides: #806 sits in Backlog there, so the
# pass claims no card for it, and #801, whose source column is still Todo, stays in the go set.
printf '{"t": %s, "repo": "o/r", "items": [{"n":801,"st":"Todo","r":"o/r"},{"n":806,"st":"Todo","r":"o/r"}]}' "$(date +%s)" > "$T/hub2.json"
echo '{"800":"PVTI_800","806":"PVTI_806"}' > "$SG_STATE/rest-known-o-7-o_r.json"; : > "$T/gh.log"
turned=$(SG_BOARD_CACHE="$T/hub2.json" sg_todo_cards "801,806" 2>"$T/err4")
echo "$turned" | /usr/bin/grep -qx 801 && ok "a card the source still carries in Todo is listed beside the cache" || ko "a card the source still carries in Todo is listed beside the cache (rows: $(echo $turned))"
echo "$turned" | /usr/bin/grep -qx 806 && ko "a cached Todo card the source reads Backlog is still listed" || ok "a cached Todo card the source reads Backlog is not listed"
/usr/bin/grep -q "PVTI_806" "$T/gh.log" && ok "the cached Todo card #806 is read at the source before the pass claims it" || ko "the cached Todo card #806 is read at the source before the pass claims it (gh: $(tr '\n' ' ' < "$T/gh.log"))"
rm -rf "$T"
[ "$fail" = 0 ] && echo "PASS test-todo-unread" || { echo "FAIL test-todo-unread"; exit 1; }
