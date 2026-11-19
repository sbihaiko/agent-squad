#!/bin/bash
# seconds: 1.75
# The janitor writes the Run fields of a Doing card only when the holder or its start differs from what the card carries (#327).
# The card's current holder is read once per pass, never once per card; a card whose holder is the run that holds it sends no
# PATCH, and a card whose holder changed is written in one PATCH and proven by exactly one read. A board whose holder read fails
# is written on. The whole stack runs (janitor, board.sh, board.py, issue_state.py) against a stubbed gh that counts every call.
# Stubbed gh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-run-fields-unchanged.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$here/board.sh" "$here/board.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *--include*search/issues*) echo '{"total_count":0,"items":[]}';;
  *--include*issues/*/comments*) echo '[]';;
  *search/issues*) echo 0;;
  *"-X PATCH"*items/ITEM*)
    if /usr/bin/grep -q F_RUN "TMPDIR/patch.in"; then echo PATCH-RUN >> "TMPDIR/events"; python3 "TMPDIR/bin/holder.py" "TMPDIR/patch.in" > "TMPDIR/holder.tsv"
    else echo PATCH-STATUS >> "TMPDIR/events"; fi
    echo '{"id":"ITEM2101"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_DOING","name":{"raw":"Doing"}}]},{"id":"F_RUN","name":"Run"},{"id":"F_START","name":"Run started"}]';;
  *'fields[]=F_STATUS,F_RUN,F_START'*) echo READ-RUN >> "TMPDIR/events"; [ -e "TMPDIR/holders-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }; h=$(cat "TMPDIR/holder.tsv" 2>/dev/null); printf '2101\tDoing\t%s\t%s\thttps://api.github.com/repos/x/y\n' "$(printf '%s' "$h" | cut -f2)" "$(printf '%s' "$h" | cut -f3)";;
  *'fields[]=F_RUN'*) echo READ-RUN >> "TMPDIR/events"; [ -e "TMPDIR/holders-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }; cat "TMPDIR/holder.tsv" 2>/dev/null;;
  *'fields[]=F_STATUS'*) printf '2101\tDoing\thttps://api.github.com/repos/x/y\n';;
  *items?per_page=100*) printf '[2101,"ITEM2101","https://api.github.com/repos/x/y"]\n';;
  *users/o*) echo '{"type":"Organization"}';;
  *graphql*) printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":2101,"title":"Issue 2101","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}]}}}}';;
  *issues/2101*) echo $'open\t\tIssue';;
esac
J
cat > "$T/bin/holder.py" <<'P'
import json, sys
f = {x["id"]: x["value"] for x in json.load(open(sys.argv[1]))["fields"]}
if f.get("F_RUN"): print("2101\t%s\t%s\thttps://api.github.com/repos/x/y" % (f["F_RUN"], f.get("F_START") or ""))
P
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mkdir -p "$T/co/runs/run-live"; echo run-live > "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) 2101:\n' > "$T/co/runs/run-live/request.txt"
: > "$T/co/runs/run-live/record.jsonl"; echo run-live > "$T/st/auto/disp-2101"
cycle() { : > "$T/events"; SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" SG_BOARD_READ_BACKOFF=0 python3 -I "$T/s/janitor.py" 2>&1; }
count() { /usr/bin/grep -c "^$1\$" "$T/events"; }
printf '2101\told-run\t\thttps://api.github.com/repos/x/y\n' > "$T/holder.tsv"   # the card carries another run
o=$(cycle)
[ "$(count PATCH-RUN)" = 1 ] && ok "a changed holder is written in one PATCH" || ko "changed holder: $(count PATCH-RUN) Run PATCH: $o $(cat "$T/events")"
after=$(sed -n '/PATCH-RUN/,$p' "$T/events" | /usr/bin/grep -c READ-RUN)
[ "$after" = 1 ] && ok "the written holder is proven by exactly one read" || ko "reads after the write: $after: $(cat "$T/events")"
/usr/bin/grep -q "run-live" "$T/holder.tsv" && ok "the card then carries the run that holds it" || ko "holder after write: $(cat "$T/holder.tsv")"
o=$(cycle)
[ "$(count PATCH-RUN)" = 0 ] && ok "a cycle over an unchanged Doing card sends no Run-field PATCH" || ko "unchanged cycle: $(count PATCH-RUN) Run PATCH: $o $(cat "$T/events")"
[ "$(count PATCH-STATUS)" = 0 ] && [ "$(count READ-RUN)" = 1 ] && ok "the unchanged cycle reads the holders once and proves nothing" || ko "unchanged cycle events: $(cat "$T/events")"
: > "$T/gh.calls"; o=$(cycle)
b=$(/usr/bin/grep -c 'items?per_page=100&fields' "$T/gh.calls")
[ "$b" = 1 ] && ok "one pass reads the board once: Status and holders ride one read" || ko "board reads in one pass: $b: $(cat "$T/gh.calls") $o"
touch "$T/holders-down"; o=$(cycle)
[ "$(count PATCH-RUN)" = 0 ] && ok "a board read that fails moves nothing: no Run PATCH on a guess" || ko "board down: $(count PATCH-RUN) Run PATCH: $o $(cat "$T/events")"
[ -n "$DBG" ] && { cat "$T/st/cycle.log" "$T/gh.calls"; }
rm -rf "$T"; exit $fail
