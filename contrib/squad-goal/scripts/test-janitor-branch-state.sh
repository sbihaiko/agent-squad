#!/bin/bash
# seconds: 1.14
# The card column comes from the work branch's state, never from a pull request. With SG_BOARD_VOCAB=shipped the janitor reads
# the remote work branches (`fix|gui/<n>-...`) and how each stands against the landing branch (dev): a branch ahead of dev puts
# its card in Batching; a branch in dev puts a card that reached Batching in Closed (Done). An open pull request or a
# merged one, whatever its state, moves no card. Stubbed gh and board.sh; a temp state dir, the live board is never touched.
# The janitor loads squad/procs.py, squad/active_runs.py and squad/handhold.py from SG_SQUAD, SG_CHECKOUT, or the tree the
# caller runs in (#1508, #1862, #2548). The daily pass clears SG_* and starts the suite from its own directory, so the suite
# names the repository it lives in: the result never depends on the caller's tree, and the fixture's empty checkout never
# turns into a missing module.
# usage: bash test-janitor-branch-state.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
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
  *is:merged*) echo 1;;
  *search/issues*) echo 1;;
  *pulls\?state=open*)
    printf '31 aaaaaaaaa1 main fix/2206-x 2099-01-01T00:00:00Z\t\t2206\n'
    printf '32 bbbbbbbbb2 main fix/2207-x 2099-01-01T00:00:00Z\t\t2207\n';;
  *--include*repos/x/y/branches/gui%2F2202-landed*) echo '{"commit":{"sha":"sha2202"}}';;
  *--include*commits/sha2202/check-runs*) echo '{"check_runs":[{"name":"test","status":"completed","conclusion":"success"}]}';;
  *--include*repos/x/y/branches/gui%2F2208-landed*) echo '{"commit":{"sha":"sha2208"}}';;
  *--include*commits/sha2208/check-runs*) echo '{"check_runs":[{"name":"test","status":"completed","conclusion":"success"}]}';;
  *repos/x/y/branches*)
    printf 'fix/2201-ahead sha2201\ngui/2202-landed sha2202\ngui/2203-landed sha2203\ngui/2208-landed sha2208\nmain shamain\n';;
  *commits/sha2202/check-runs*) echo 'test: success';;
  *commits/sha2208/check-runs*) echo 'test: success';;
  *compare/dev...sha2201*) echo ahead;;
  *compare/dev...sha2202*) echo behind;;
  *compare/dev...sha2203*) echo behind;;
  *compare/dev...sha2208*) echo behind;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    for n in 2201:Doing 2202:"Batching" 2203:Doing 2206:Doing 2207:"Batching" 2208:"Batching"; do printf '%s\t%s\thttps://api.github.com/repos/x/y\n' "${n%%:*}" "${n#*:}"; done;;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2201 2202 2203 2206 2207 2208; do
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}' "$sep" "$n" "$n"; sep=,; done
    printf ']}}}}';;
  *issues/22*/labels*) echo '[]';;
  *issues/22*) echo $'open\t\tIssue\t';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
o=$(SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$(cd "$here/../../.." && pwd)" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
has '^STATUS o 4 Batching 2201$' && ok "a work branch ahead of dev puts the card in Batching" || ko "#2201 not Batching: $(bc) $o"
has '^STATUS o 4 Closed 2202$' && ok "a branch in dev puts a Batching card in Closed (Done)" || ko "#2202 not Closed: $(bc)"
has '^STATUS o 4 Closed 2208$' && ok "a branch in dev puts a Batching card in Closed (ADR-0145 rule 12)" || ko "#2208 not Closed: $(bc)"
! has '\(Closed\|Batching\) 2203$' && ok "a branch in dev does not close a card still in Doing (a fresh branch is in dev too)" || ko "#2203 moved: $(bc)"
! has '\(Closed\|Batching\) 2206$' && ok "an open pull request moves no card to Batching" || ko "#2206 moved over a PR: $(bc)"
! has '\(Closed\) 2207$' && ok "a merged pull request moves no card to Closed" || ko "#2207 moved over a PR: $(bc)"
/usr/bin/grep -q 'repos/x/y/branches' "$T/gh.calls" && ok "the work branches are read" || ko "no branch read: $(cat "$T/gh.calls")"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
