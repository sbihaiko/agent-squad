#!/bin/bash
# seconds: 2.61
# Regression test for the issue_state door (#1429): the rows it prints are this profile's repository's cards only.
# A project may hold cards of several repositories and an issue number repeats between them, so a card of another
# repository on the same board is never named -- not by the whole-board read, and not by a hub cache row.
# Hermetic: a stub gh answers the Status field and the cards route through the --jq the caller passes; its own SG_STATE.
# The door's repository filter is Python's, not the stub's: the raw projection is asserted to carry the other
# repository's card, so a door that stops filtering is red.
# Scope: both halves of the acceptance criterion. Repository: a card of another repository never reaches the door, by
# the whole-board read or by a hub cache row. Membership: the hub marks a row it wrote for an open issue the board does
# not carry `nc`, and such a row is not a card -- the door neither prints it as one nor counts it as carried, and the
# go read names it UNREAD (#800). A stamp-free cache is not trusted at all. A stamped and fresh cache is the hub's own
# board pass, so a card removed from the board is named no longer than the freshness bound plus one pass; the door
# never reads the board to second-guess a fresh cache, and nothing here claims more than that.
# mirrors: issue_state.py
# usage: bash test-issue-state-door.sh [issue_state.py]
src=${1:-$(dirname "$0")/issue_state.py}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0; trap 'rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_STATE=$T/state SG_OWNER=o SG_PROJECT=7 SG_REPO=o/r SG_BOARD_CACHE=$T/no-cache.json
mkdir -p "$T/bin" "$SG_STATE"
# the board: #10 is this repository's Doing card, #11 the Doing card of another repository on the same project,
# and #1 a Doing card of that other repository whose number is not on this board at all
cat > "$T/items.json" <<'J'
[{"id":"PVTI_A","content":{"number":10,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Doing"}}]},
 {"id":"PVTI_B","content":{"number":11,"repository_url":"https://api.github.com/repos/o/other"},"fields":[{"name":"Status","value":{"name":"Doing"}}]},
 {"id":"PVTI_C","content":{"number":12,"repository_url":"https://api.github.com/repos/o/r"},"fields":[{"name":"Status","value":{"name":"Todo"}}]}]
J
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# route by the one path argument: a /fields read answers the field, a /items? read answers the cards through the
# caller's --jq. The cards route logs the jq it was asked for, so the test can replay the projection itself.
path=""; jqf=""; prev=""
for a in "$@"; do
  [ "$prev" = "--jq" ] && jqf="$a"
  case "$a" in /*/fields*|/*/items*) [ -z "$path" ] && path="$a";; esac
  prev="$a"
done
log="$(dirname "$T_ITEMS")/items.log"
case "$path" in
  */fields*) echo '[{"id":"F1","name":"Status","options":[{"id":"o1","name":"Todo"},{"id":"o2","name":"Doing"}]}]'; exit 0;;
  */items\?*) printf '%s\n' "$jqf" >> "$log"; if [ -n "$jqf" ]; then jq -r "$jqf" "$T_ITEMS"; else cat "$T_ITEMS"; fi; exit 0;;
esac
exit 1
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" T_ITEMS="$T/items.json"
door() { python3 -I "$here/issue_state.py" "$@" 2>/dev/null; }
reads() { wc -l < "$T/items.log" 2>/dev/null | tr -d ' '; }
jqo() { tail -n 1 "$T/items.log" 2>/dev/null; }
# ---- the whole-board read: the board route delivers every card, and the door drops the other repository's ----
: > "$T/items.log"; out=$(door)
[ "$(reads)" -ge 1 ] && ok "the door reads the board's cards route" || ko "the door reads the board's cards route: $(reads) reads"
raw=$(jq -r "$(jqo)" "$T/items.json" 2>/dev/null)
printf '%s\n' "$raw" | /usr/bin/grep -q $'^11\tDoing\t' && ok "the raw board projection carries another repository's card" || ko "the raw board projection carries another repository's card (raw: $raw)"
printf '%s\n' "$raw" | /usr/bin/grep -q $'^10\tDoing\t' && ok "the raw board projection carries this repository's card" || ko "the raw board projection carries this repository's card (raw: $raw)"
printf '%s\n' "$out" | /usr/bin/grep -qx $'10\tDoing' && ok "the profile's own Doing card is read" || ko "the profile's own Doing card is read (out: $out)"
printf '%s\n' "$out" | /usr/bin/grep -qx $'12\tTodo' && ok "the profile's own Todo card is read" || ko "the profile's own Todo card is read (out: $out)"
printf '%s\n' "$out" | /usr/bin/grep -q $'^11\t' && ko "another repository's card is named by the door" || ok "another repository's card is not named by the door"
[ -z "$(door --state 11)" ] && ok "another repository's card carries no state for this profile" || ko "another repository's card carries no state: $(door --state 11)"
[ "$(door --state 10)" = Doing ] && ok "--state answers this repository's card" || ko "--state answers this repository's card: $(door --state 10)"
# ---- the hub cache: a stamped row that names another repository is not this board's card ----
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing","r":"o/r"},{"n":11,"st":"Doing","r":"o/other"},{"n":12,"st":"Todo","r":"o/r"}]}' "$(date +%s)" > "$T/hub.json"
before=$(reads); cached=$(SG_BOARD_CACHE="$T/hub.json" door); after=$(reads)
[ "$before" = "$after" ] && ok "a stamped fresh cache answers with no board read" || ko "a stamped fresh cache answers with no board read ($before then $after reads)"
printf '%s\n' "$cached" | /usr/bin/grep -qx $'10\tDoing' && ok "a real Doing card with no live run stays reported" || ko "a real Doing card with no live run stays reported (rows: $cached)"
printf '%s\n' "$cached" | /usr/bin/grep -qx $'12\tTodo' && ok "a real Todo row stays reported" || ko "a real Todo row stays reported (rows: $cached)"
printf '%s\n' "$cached" | /usr/bin/grep -q $'^11\t' && ko "a cache row of another repository is named" || ok "a cache row of another repository is not named"
# the same row through --kinds: another repository's kind never reaches the dispatcher
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing","k":"bug","r":"o/r"},{"n":11,"st":"Doing","k":"bug","r":"o/other"}]}' "$(date +%s)" > "$T/kinds.json"
kinds=$(SG_BOARD_CACHE="$T/kinds.json" door --kinds)
printf '%s\n' "$kinds" | /usr/bin/grep -qx $'10\tbug' && ok "--kinds names this repository's kind" || ko "--kinds names this repository's kind (rows: $kinds)"
printf '%s\n' "$kinds" | /usr/bin/grep -q $'^11\t' && ko "--kinds names another repository's card" || ok "--kinds names another repository's card: never"
# a row the writer did not stamp proves no repository, so the cache is not trusted: the board answers instead
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing","r":"o/r"},{"n":1,"st":"Doing"},{"n":11,"st":"Doing","r":"o/other"}]}' "$(date +%s)" > "$T/legacy.json"
before=$(reads); legacy=$(SG_BOARD_CACHE="$T/legacy.json" door); after=$(reads)
[ "$after" -gt "$before" ] && ok "a cache with an unstamped card is not trusted, the board answers" || ko "a cache with an unstamped card is not trusted ($before then $after reads)"
printf '%s\n' "$legacy" | /usr/bin/grep -q $'^1\t' && ko "an unstamped cache row is named" || ok "an unstamped cache row is not named"
printf '%s\n' "$legacy" | /usr/bin/grep -q $'^11\t' && ko "an unstamped cache of another repository names its card" || ok "an unstamped cache of another repository names no card"
printf '%s\n' "$legacy" | /usr/bin/grep -qx $'10\tDoing' && ok "a real Doing card still reported from the board" || ko "a real Doing card still reported from the board (rows: $legacy)"
# an open issue the board does not carry is not a board card: the hub writes it with `nc`, and the door never prints it
# as a row. The go read still names it UNREAD, which is what a go-labelled issue whose card no read carries gets (#800).
printf '{"t": %s, "repo": "o/r", "items": [{"n":10,"st":"Doing","r":"o/r"},{"n":999,"r":"o/r","st":null,"nc":true}]}' "$(date +%s)" > "$T/nocard.json"
before=$(reads); nocard=$(SG_BOARD_CACHE="$T/nocard.json" door --go 999,10); after=$(reads)
[ "$before" = "$after" ] && ok "a fresh cache answers the go read with no board read" || ko "a fresh cache answers the go read with no board read ($before then $after reads)"
printf '%s\n' "$nocard" | /usr/bin/grep -q $'^999\t' && ko "an issue the board does not carry is printed as a board row" || ok "an issue the board does not carry is not printed as a board row"
printf '%s\n' "$nocard" | /usr/bin/grep -q $'^UNREAD\t.*999' && ok "a go issue the board does not carry is named UNREAD" || ko "a go issue the board does not carry is named UNREAD (rows: $nocard)"
printf '%s\n' "$nocard" | /usr/bin/grep -qx $'10\tDoing' && ok "a real Doing card with no live run stays reported" || ko "a real Doing card with no live run stays reported (rows: $nocard)"
# a cache file of another board is never this board's: the board answers, and claims no card of its own
printf '{"t": %s, "repo": "o/other", "items": [{"n":11,"st":"Doing","r":"o/other"},{"n":1,"st":"Doing","r":"o/other"}]}' "$(date +%s)" > "$T/foreign.json"
before=$(reads); foreign=$(SG_BOARD_CACHE="$T/foreign.json" door); after=$(reads)
[ "$after" -gt "$before" ] && ok "a cache file of another repository is not read" || ko "a cache file of another repository is not read ($before then $after reads)"
printf '%s\n' "$foreign" | /usr/bin/grep -q $'^1\t' && ko "another board's card is named" || ok "another board's card is not named"
[ "$fail" = 0 ] && echo "PASS test-issue-state-door" || { echo "FAIL test-issue-state-door"; exit 1; }
