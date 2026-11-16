#!/bin/bash
# (#1999) The reopen rule (ADR-0143 rule 2) returns a Closed card to Todo only for an issue GitHub reports open on a
# FRESH read. The open-issue list a pass reads is a snapshot up to 90 s old, and issue-facts.json (the one cached issue
# read, #1744) can still hold the open state GitHub already changed: a hand landing closed #1972, and that pass read the
# issue as open again and moved its card out of Closed while GitHub reported the issue CLOSED before and after. The stub
# answers the pass's cached read with the stale open state and the live REST read with GitHub's truth, so the two are
# told apart. The control issue is closed in no cache and open on the live read: its card still moves to Todo.
# usage: bash test-janitor-reopen-fresh.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields|--clear-run) :;;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls"; if [ -n "$SG_MOVE_REASON" ]; then echo "REASON $SG_MOVE_REASON $4" >> "TMPDIR/board.calls"; fi;;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; printf '%s\n' "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *'issues?state=all&since='*) printf '[]\n';;   # the facts list answers, names nothing: the seeded file holds the stale open state
  *projectsV2/4/fields*) printf 'FID4\n';;
  *projectsV2/4/items*)
    printf '6001\tClosed\thttps://api.github.com/repos/x/y\n'
    /usr/bin/grep -q '^STATUS o 4 Todo 6002$' "TMPDIR/board.calls" 2>/dev/null && c2=Todo || c2=Closed   # the board keeps the move: a second pass reads Todo
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
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
git -C "$T/co" init -q
# The one cached read (#1744) holds the stale open state for both issues, written seconds ago so its age never excuses a
# second read: GitHub's answer to the pass is the file, never the issue itself.
python3 - "$T/st/issue-facts.json" <<'P'
import json, sys, time
json.dump({"repo": "x/y", "synced": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "issues": {
    "6001": {"f": ["open", "", "Issue 6001", "", ""], "at": time.time()},
    "6002": {"f": ["open", "", "Issue 6002", "", ""], "at": time.time()}}}, open(sys.argv[1], "w"))
P
run() { SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
live() { /usr/bin/grep -c "repos/x/y/issues/$1 -q" "$T/gh.calls" 2>/dev/null; }
fact() { python3 -c 'import json,sys; print((json.load(open(sys.argv[1]))["issues"].get(sys.argv[2]) or {}).get("f", ["none"])[0])' "$T/st/issue-facts.json" "$1" 2>/dev/null; }
o=$(run); l1=$(live 6001); : > "$T/gh.calls"; o2=$(run); l2=$(live 6001)
# The closed issue's card never moves, and the rule never claims it did.
! has '^STATUS o 4 Todo 6001$' && ok "a stale cached open state never sends a closed issue's Closed card to Todo" || ko "#6001 moved: $(/usr/bin/grep ' 6001$' "$T/board.calls")"
! /usr/bin/grep -q 'TERMINAL-REOPENED #6001' "$T/st/cycle.log" && ok "the closed issue is never named reopened" || ko "#6001 named: $(/usr/bin/grep 6001 "$T/st/cycle.log")$o$o2"
# The state check reads GitHub itself: one live read of the issue per pass, never the cache's answer.
[ "$l1" -ge 1 ] && [ "$l2" -ge 1 ] && ok "the state check before the reopen write reads GitHub live on every pass" || ko "#6001 live reads: first pass $l1, second pass $l2: $o$o2"
[ "$(fact 6001)" = closed ] && ok "the live answer corrects the cached open state" || ko "#6001 fact after the pass: '$(fact 6001)'"
# The control: an issue open on the live read still returns its Closed card to Todo, once.
[ "$(/usr/bin/grep -c '^STATUS o 4 Todo 6002$' "$T/board.calls")" = 1 ] && ok "an issue GitHub reports open still moves Closed to Todo once over two passes" || ko "#6002 moves: $(cat "$T/board.calls")"
/usr/bin/grep -q '^REASON reopened 6002$' "$T/board.calls" && ok "the control's move names SG_MOVE_REASON=reopened" || ko "#6002 move has no reopened reason"
/usr/bin/grep -q 'TERMINAL-REOPENED #6002' "$T/st/cycle.log" && ok "the control's reopen is named in cycle.log" || ko "#6002 not named: $(/usr/bin/grep 6002 "$T/st/cycle.log")"
rm -rf "$T"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
