#!/bin/bash
# seconds: 2.48
# (#1999) The reopen rule (ADR-0143 rule 2) returns a Closed card to Todo only for an issue GitHub reports open on a
# FRESH read. The open-issue list a pass reads is a snapshot up to 90 s old, and issue-facts.json (the one cached issue
# read, #1744) can still hold the open state GitHub already changed: a hand landing closed #1972, and that pass read the
# issue as open again and moved its card out of Closed while GitHub reported the issue CLOSED before and after. The stub
# answers the pass's cached read with the stale open state and the live REST read with GitHub's truth, so the two are
# told apart. The control issue is closed in no cache and open on the live read: its card still moves to Todo.
# usage: bash test-janitor-reopen-fresh.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
GREP=$(command -v grep) || { echo "FAIL no grep on PATH" >&2; rm -rf "$T"; exit 1; }
# The janitor loads squad/procs.py from the tree it runs in: it walks up from its own directory, then SG_SQUAD, SG_CHECKOUT
# and cwd (#3094). The daily pass clears every SG_* and may run from a cwd outside the repository, so the copy gets the
# repository's squad package beside it; the product walk-up then answers, with no SG_SQUAD to stand in for the tree.
repo=$(cd "$(dirname "$0")/../../.." && pwd -P)
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; ln -s "$repo/squad" "$T/s/squad"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
# subst <file>: replace the TMPDIR placeholder with this run's temp dir, in place, with no sed -i and its BSD/GNU split.
subst() { python3 - "$1" "$T" <<'P'
import sys
p, t = sys.argv[1], sys.argv[2]
s = open(p).read()
open(p, "w").write(s.replace("TMPDIR", t))
P
}
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields|--clear-run) :;;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls"; if [ -n "$SG_MOVE_REASON" ]; then echo "REASON $SG_MOVE_REASON $4" >> "TMPDIR/board.calls"; fi;;
esac
J
subst "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; printf '%s\n' "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *'issues?state=all&since='*) printf '[]\n';;   # the facts list answers, names nothing: the seeded file holds the stale open state
  *projectsV2/4/fields*) printf 'FID4\n';;
  *projectsV2/4/items*)
    printf '6001\tClosed\thttps://api.github.com/repos/x/y\n'
    if [ -f "TMPDIR/board.calls" ] && grep -q '^STATUS o 4 Todo 6002$' "TMPDIR/board.calls"; then c2=Todo; else c2=Closed; fi   # the board keeps the move: a second pass reads Todo
    printf '6002\t%s\thttps://api.github.com/repos/x/y\n' "$c2";;
  *graphql*)
    printf '%s\n' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    printf '%s' '{"number":6001,"title":"Issue 6001","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf '%s' ',{"number":6002,"title":"Issue 6002","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf '%s\n' ']}}}}';;
  *'repos/x/y/issues/6001 -q'*) printf 'closed\tcompleted\tIssue 6001\t\t\n';;   # GitHub's truth: the hand landing closed it
  *'repos/x/y/issues/6002 -q'*) printf 'open\t\tIssue 6002\t\t\n';;               # the control: open on every read
  *repos/x/y/branches*) :;;
  *search/issues*) printf '0\n';;
esac
J
subst "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
git -C "$T/co" init -q
# The one cached read (#1744) holds the stale open state for both issues, written seconds ago so its age never excuses a
# second read: GitHub's answer to the pass is the file, never the issue itself.
python3 - "$T/st/issue-facts.json" <<'P'
import json, sys, time
json.dump({"repo": "x/y", "synced": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "issues": {
    "6001": {"f": ["open", "", "Issue 6001", "", ""], "at": time.time()},
    "6002": {"f": ["open", "", "Issue 6002", "", ""], "at": time.time()}}}, open(sys.argv[1], "w"))
P
# The daily pass clears every SG_* and may run from a cwd outside the repository (#2307), so each pass runs from the
# fixture's own folder: a green that needs the caller's cwd to resolve the janitor's root is no green on dev.
run() { (cd "$T" && SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1); }
# A helper never answers "no match" for a missing file or a missing tool: both are loud, since a silent no-match made an
# assertion pass without reading anything.
has() { [ -f "$T/board.calls" ] && "$GREP" -q "$1" "$T/board.calls"; }
live() { if [ -f "$T/gh.calls" ]; then "$GREP" -c "repos/x/y/issues/$1 -q" "$T/gh.calls"; else printf '0\n'; fi; }
moves() { if [ -f "$T/board.calls" ]; then "$GREP" -c "^STATUS o 4 Todo $1\$" "$T/board.calls"; else printf '0\n'; fi; }
loghas() { [ -f "$T/st/cycle.log" ] && "$GREP" -q "$1" "$T/st/cycle.log"; }
logof() { [ -f "$T/st/cycle.log" ] && "$GREP" "$1" "$T/st/cycle.log"; }
fact() { python3 -c 'import json,sys; print((json.load(open(sys.argv[1]))["issues"].get(sys.argv[2]) or {}).get("f", ["none"])[0])' "$T/st/issue-facts.json" "$1"; }
o=$(run); l1=$(live 6001); : > "$T/gh.calls"; o2=$(run); l2=$(live 6001)
# The closed issue's card never moves, and the rule never claims it did.
! has '^STATUS o 4 Todo 6001$' && ok "a stale cached open state never sends a closed issue's Closed card to Todo" || ko "#6001 moved: $(logof ' 6001$')"
! loghas 'TERMINAL-REOPENED #6001' && ok "the closed issue is never named reopened" || ko "#6001 named: $(logof 6001)$o$o2"
# The state check reads GitHub itself: one live read of the issue per pass, never the cache's answer.
[ "$l1" -ge 1 ] && [ "$l2" -ge 1 ] && ok "the state check before the reopen write reads GitHub live on every pass" || ko "#6001 live reads: first pass $l1, second pass $l2: $o$o2"
[ "$(fact 6001)" = closed ] && ok "the live answer corrects the cached open state" || ko "#6001 fact after the pass: '$(fact 6001)'"
# The control: an issue open on the live read still returns its Closed card to Todo, once.
[ "$(moves 6002)" = 1 ] && ok "an issue GitHub reports open still moves Closed to Todo once over two passes" || ko "#6002 moves: $(moves 6002), board: $(cat "$T/board.calls" 2>/dev/null)"
has '^REASON reopened 6002$' && ok "the control's move names SG_MOVE_REASON=reopened" || ko "#6002 move has no reopened reason"
loghas 'TERMINAL-REOPENED #6002' && ok "the control's reopen is named in cycle.log" || ko "#6002 not named: $(logof 6002)"
rm -rf "$T"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
