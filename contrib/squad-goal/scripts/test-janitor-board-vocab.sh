#!/bin/bash
# The reconciler writes the shipped vocabulary (ADR-0141): with SG_BOARD_VOCAB=shipped the janitor writes Backlog, Todo,
# Doing, Code Complete, Landing, Testing, Closed and Abandoned, and never Blocked, In Progress, Done, Open Specs or Merging. An owner
# label is a freeze marker over the column the work reached (the frozen:owner label), never a column of its own, and a spec
# opens in Backlog. Stubbed gh and board.sh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-board-vocab.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
python3 - "$T/s/board-definition.json" <<'PY'
import json
import sys

statuses = {status["name"]: status["fact"] for status in json.load(open(sys.argv[1]))["statuses"]}
expected = {
    "Doing": "A live run holds it; a review wait is marked here",
    "Code Complete": "A child is in the wave branch",
    "Landing": "The wave is approved and the push to dev is in progress",
    "Testing": "CI runs on the dev tip",
    "Closed": "In dev",
}
if any(statuses.get(name) != fact for name, fact in expected.items()) or "Merging" in statuses or any("pull request" in fact.lower() for fact in statuses.values()):
    print("FAIL ADR-0145 board vocabulary", file=sys.stderr)
    sys.exit(1)
PY
[ $? = 0 ] && ok "ADR-0145 columns name the wave and dev path" || ko "ADR-0145 board vocabulary"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *--include*search/issues*) echo '{"total_count":0,"items":[]}';;   # the terminal comment's reads (#245)
  *--include*issues/2105/events*) echo '[{"event":"closed","actor":{"login":"o"}}]';;
  *--include*issues/2106/events*) echo '[{"event":"labeled","label":{"name":"wontfix"},"actor":{"login":"o"}}]';;
  *--include*issues/*/comments*) echo '[]';;
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    for n in 2101 2102 2103 2104 2105 2107; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done
    printf '2108\tBacklog\thttps://api.github.com/repos/x/y\n'; printf '2110\tDoing\thttps://api.github.com/repos/x/y\n'; printf '2106\tTodo\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2101 2102 2103 2104 2106 2107 2108 2109 2110; do
      kids='[]'; case $n in
        2109) labs='{"nodes":[{"name":"spec"}]}'; kids='[{"number":2110},{"number":2107}]';;   # a spec with a child in Doing folds to Doing
        2102|2108) labs='{"nodes":[{"name":"spec"}]}';;
        2103|2104) labs='{"nodes":[{"name":"needs-owner"}]}';;
        2106) labs='{"nodes":[{"name":"wontfix"}]}';;
        *) labs='{"nodes":[]}';;
      esac
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":%s},"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs" "$kids"; sep=,; done
    printf ']}}}}';;
  *issues/2103/labels*|*issues/2104/labels*) echo '[]';;
  *issues/2103*|*issues/2104*) echo $'open\t\tIssue\tneeds-owner';;
  *issues/2105*) echo $'closed\t\tIssue 2105';;
  *issues/2106*) echo $'open\t\tIssue\twontfix';;
  *issues/21*) echo $'open\t\tIssue';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"; }
rec() { printf '%s\n' "$2" > "$T/co/runs/$1/record.jsonl"; }
mk run-live 2101; : > "$T/co/runs/run-live/record.jsonl"; echo run-live > "$T/st/auto/disp-2101"
mk run-unlanded 2104; rec run-unlanded '{"event":"run_start","run_id":"run-unlanded","ts":"2026-10-13T08:02:00Z"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-closed 2105; rec run-closed '{"event":"run_start","run_id":"run-closed","ts":"2026-10-13T08:07:00Z"}'
o=$(SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
has '^STATUS o 4 Doing 2101$' && ok "a live run holds the card: Doing" || ko "#2101 not Doing: $(bc) $o"
has '^RUN o 4 2101 run-live ' && ok "the Run fields are written while the card is Doing" || ko "#2101 Run fields missing: $(bc)"
has '^ADD o 4 Doing 2109$' && ok "a spec with a child in Doing folds to Doing" || ko "#2109 not Doing: $(bc)"
! has '^RUN o 4 2109 ' && ok "a spec folded to Doing gets no Run fields: no run holds it" || ko "#2109 Run written: $(bc)"
has '^STATUS o 4 Backlog 2102$' && ok "a spec opens in Backlog, not Open Specs" || ko "#2102 not Backlog: $(bc)"
has '^STATUS o 4 Backlog 2103$' && ok "an owner-label card with no work reached sits in Backlog" || ko "#2103 not Backlog: $(bc)"
has '^STATUS o 4 Code Complete 2104$' && ok "an owner-label card keeps the column its work reached" || ko "#2104 lost its column: $(bc)"
/usr/bin/grep -q 'issues/2103/labels.*frozen:owner' "$T/gh.calls" && ok "an owner-label card carries the owner freeze" || ko "#2103 freeze not written: $(cat "$T/gh.calls")"
/usr/bin/grep -q 'issues/2104/labels.*frozen:owner' "$T/gh.calls" && ok "a frozen card with a reached column carries the owner freeze" || ko "#2104 freeze not written"
has '^STATUS o 4 Closed 2105$' && ok "a closed issue: Closed" || ko "#2105 not Closed: $(bc)"
has '^STATUS o 4 Abandoned 2106$' && ok "a wontfix issue: Abandoned" || ko "#2106 not Abandoned: $(bc)"
! has ' 2107$' && ok "a startable card already in Todo is not written" || ko "#2107 rewritten: $(bc)"
! has ' 2108$' && ok "a spec already in Backlog is not written" || ko "#2108 rewritten: $(bc)"
! has 'Blocked\|In Progress\|Done\|Open Specs\|Merging' && ok "no write names Blocked, In Progress, Done, Open Specs or Merging" || ko "an old option was written: $(bc)"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
