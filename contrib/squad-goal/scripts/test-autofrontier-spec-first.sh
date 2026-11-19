#!/bin/bash
# Rule (#1962): a ticket whose closure closes its spec (every other open sub-issue already in dev) is dispatched before an
# ordinary ticket of an earlier wave, after bugs. With no such ticket the earlier wave still goes first.
# Stubbed gh, stack.sh, startability and mkreq; a temp checkout whose origin/dev holds the landed tickets.
# usage: bash test-autofrontier-spec-first.sh [path/to/autofrontier.sh]
src=${1:-$(dirname "$0")/autofrontier.sh}; T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL the closing ticket goes first: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done
export SG_MIN_FREE_GB=0 SG_SQUAD=$repo SQUAD_CAPS_DIR=$T/caps
export SG_NIGHTLY_OFF=1   # the dispatch hold reads the wall clock: the nightly hour must not hold a fixture pass (#2397); a case that tests the hold sets its own hour
mkdir -p "$T/bin" "$T/scripts" "$T/co/docs/squad/workflows" "$T/st"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
q=""; p=""; prev=""
for a in "$@"; do
  [ "$prev" = "-q" ] && q="$a"
  case "$a" in repos/*) p="$a";; esac
  prev="$a"
done
case "$*" in *"issues(states: OPEN"*)
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",parent:((.parent // null)|if . == null then null else {number:.} end),labels:{nodes:(.labels//[])},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$GH_ISSUES"; exit 0;; esac
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES;;
  */issues/*)             f=$GH_ISSUE1;;
  *) exit 1;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"
cp "$(dirname "$src")/startability.sh" "$T/scripts/startability-real.sh"; cp "$(dirname "$src")/skiplabels.sh" "$T/scripts/skiplabels.sh"
printf '#!/bin/bash\n. "$(dirname "$0")/startability-real.sh"\nsg_fork_only() { return 1; }\nsg_not_startable() { return 1; }\nsg_ledger_held() { return 1; }\nsg_ac_refused() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\nsg_issue_worktree_note() { :; }\nsg_board_rows() { jq -r ".[]|\\"\\(.number)\\tTodo\\"" "$GH_ISSUES"; }\n' > "$T/scripts/startability.sh"
printf '#!/bin/bash\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\necho x >> "$MKLOG"; cp "$2" "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
cp "$(dirname "$src")/mkreq.sh" "$T/scripts/mkreq-real.sh"; cp "$(dirname "$src")"/{models.json,req-checklist.txt} "$T/scripts/"
cat > "$T/scripts/launch.py" <<'PY'
import os
open(os.environ["LAUNCHLOG"], "a").write("launch\n")
PY
chmod +x "$T/scripts/mkreq.sh"
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"; cp "$(dirname "$src")"/{ghcall.sh,drainpolicy.py,ghread.py,ghthrottle.py,workbranches.sh,issue_ledger.py,siblings.py,cluster.py,loopbug.sh,basered.sh,specstall.py,board_columns.py,liveholds.py,slicecut.py,columnlimit.sh} "$T/scripts/"
cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$T/st; SG_CHECKOUT=$T/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_CODING_RULES=rules; BOARD=$T/board.sh
sg_cq_file() { printf '%s/cq/o__r#%s\n' "\$SG_STATE" "\$1"; }
E
mkdir -p "$T/st/req" "$T/st/cq"; : > "$T/st/active-runs.txt"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
printf '{"run":{"max_parallel":20},"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":1,"max_parallel":2,"budget":{"stalls":{"min":2,"max":5},"spend":{"min":0.05,"max":0.75}}}]}]}' > "$T/co/docs/squad/workflows/dynamic.graph.json"
git -C "$T/co" init -q
dev() { git -C "$T/co" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1"; git -C "$T/co" update-ref refs/remotes/origin/dev HEAD; git -C "$T/co" update-ref refs/remotes/origin/main HEAD; }
dev "Start the spec"
export HOME=$T/home SG_STATE=$T/st SG_CHECKOUT=$T/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier MKLOG=$T/mk.log LAUNCHLOG=$T/launch.log BOARDLOG=$T/board.log
export GH_ISSUES=$T/issues.json GH_OPEN=$T/open.json GH_CLOSED=$T/closed.json GH_ISSUE1=$T/issue1.json
export SG_GH_AGGREGATE_WINDOW=0
mkdir -p "$HOME" "$T/st/auto"; printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
# The spec 30 holds tickets 31 and 32; 5 is an ordinary ticket of an earlier wave.
cat > "$GH_ISSUES" <<'J'
[{"number":30,"title":"Spec","body":"","labels":[{"name":"spec"},{"name":"frontier"}],"parent":null},
 {"number":31,"title":"Closes the spec","body":"","labels":[{"name":"wave:02"},{"name":"frontier"}],"parent":30},
 {"number":32,"title":"Landed in dev","body":"","labels":[{"name":"wave:02"},{"name":"frontier"}],"parent":30},
 {"number":5,"title":"Ordinary ticket","body":"","labels":[{"name":"wave:01"},{"name":"frontier"}],"parent":null}]
J
dev "Refs #32: land the second ticket"
run() { rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"; : > "$MKLOG"; SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" >"$T/run.log" 2>&1; }
run
[ -f "$T/st/auto/disp-31" ] && [ ! -f "$T/st/auto/disp-5" ] && ok "the ticket that closes its spec goes before an earlier-wave ticket" || ko "dispatched: $(ls "$T/st/auto" | tr '\n' ' ') | $(tail -3 "$T/run.log")"
git -C "$T/co" update-ref -d refs/remotes/origin/dev 2>/dev/null
git -C "$T/co" update-ref refs/remotes/origin/dev "$(git -C "$T/co" rev-list --max-parents=0 HEAD)"
run
[ -f "$T/st/auto/disp-5" ] && [ ! -f "$T/st/auto/disp-31" ] && ok "with no closing ticket the earlier wave still goes first" || ko "without a closer dispatched: $(ls "$T/st/auto" | tr '\n' ' ') | $(tail -3 "$T/run.log")"
[ "$fail" = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
