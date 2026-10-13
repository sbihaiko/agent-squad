#!/bin/bash
# Regression test for autofrontier.sh: an issue whose PR already MERGED is delivered, so the frontier must not redispatch
# it into a child with nothing to build (#1808).
# Offline and hermetic: stub gh (fixtures served through jq, the way gh --jq does), stub stack.sh and startability.sh, its own
# temp SG_STATE and HOME. AF_DRY=1 prints the dispatch list and returns before mkreq.sh/launch.py, so nothing is launched
# and no live run state is read or written.
# usage: bash test-autofrontier-merged.sh [path/to/autofrontier.sh]
src=${1:-$(dirname "$0")/autofrontier.sh}; T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)   # SG_SQUAD: squad/caps.py lives in the tooling repo the fixture is testing (#21)
export SG_SQUAD=$repo SQUAD_CAPS_DIR=$T/caps   # (#21) the pass reads the machine-wide ceiling through squad.caps, exported so the reader process sees it; the leases are sandboxed
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL issue 1778 with a merged PR is not redispatched: $src not found"; rm -rf "$T"; exit 1; fi
mkdir -p "$T/bin" "$T/scripts"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# stub gh: serves the read-only api calls autofrontier.sh makes from fixtures, applying its -q through jq (as gh does)
echo "$*" >> "$GHLOG"
q=""; p=""; prev=""
for a in "$@"; do
  [ "$prev" = "-q" ] && q="$a"
  case "$a" in repos/*) p="$a";; esac
  prev="$a"
done
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  [ -n "${GH_CLOSED_FAIL:-}" ] && exit 1; f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES;;
  */issues/*)             f=$GH_ISSUE1;;
  *) exit 1;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"
printf '#!/bin/bash\nsg_fork_only() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%n"; }\nsg_issue_worktree_note() { :; }\n' > "$T/scripts/startability.sh"
chmod +x "$T/scripts/stack.sh"
cp "$src" "$T/scripts/autofrontier.sh"
scene() { # <name>: fresh temp state, its own env.sh, empty gh log
  d=$T/$1; mkdir -p "$d/st/req" "$d/st/cq" "$d/co/runs" "$d/home"
  : > "$d/gh.log"; : > "$d/st/active-runs.txt"
  cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$d/st; SG_CHECKOUT=$d/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_CODING_RULES=rules; BOARD=true
E
  export HOME=$d/home SG_STATE=$d/st SG_CHECKOUT=$d/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier GHLOG=$d/gh.log
  export GH_ISSUES=$d/issues.json GH_OPEN=$d/open.json GH_CLOSED=$d/closed.json GH_ISSUE1=$d/issue1.json
  printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
  printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
}
issues() { printf '%s' "$1" > "$GH_ISSUES"; }
run() { AF_DRY=1 SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1; }
dispatched() { # <number>: the run printed a dispatch list naming it
  case "$r" in *"FRONTIER would dispatch"*) ;; *) return 1;; esac
  printf '%s\n' "$r" | $G -qE "#$1([^0-9]|\$)"
}
closed_calls() { local c; c=$($G -c 'pulls?state=closed' "$GHLOG" 2>/dev/null); echo "${c:-0}"; }

# 1) an issue whose PR already merged (and was never closed) is delivered: it must not be dispatched again
scene merged
issues '[{"number":1778,"title":"t","body":"b","labels":[{"name":"wave:01"}]},
         {"number":1795,"title":"t","body":"b","labels":[{"name":"wave:01"}]}]'
printf '[{"number":1788,"title":"Docs: the feel area gets its PAD-03 case","body":"Refs #1778","merged_at":"2026-10-11T17:20:00Z"}]' > "$GH_CLOSED"
r=$(run)
dispatched 1778 && ko "issue 1778 with a merged PR is not redispatched: '$r'" || ok "issue 1778 with a merged PR is not redispatched"
dispatched 1795 && ok "issue 1795 with no PR is still dispatched" || ko "issue 1795 with no PR is still dispatched: '$r'"

# 2) the negative: a PR that only names the issue while OPEN keeps its behaviour, and a CLOSED-but-unmerged PR is not
#    a delivery -- only a merge is
scene unmerged
issues '[{"number":1801,"title":"t","body":"b","labels":[{"name":"wave:01"}]},
         {"number":1802,"title":"t","body":"b","labels":[{"name":"wave:01"}]},
         {"number":1803,"title":"t","body":"b","labels":[{"name":"wave:01"}]}]'
printf '[{"number":1810,"title":"wip","body":"Refs #1801","merged_at":null}]' > "$GH_OPEN"
printf '[{"number":1811,"title":"abandoned","body":"Refs #1802","merged_at":null}]' > "$GH_CLOSED"
r=$(run)
dispatched 1801 && ko "issue 1801 with an open PR is not redispatched: '$r'" || ok "issue 1801 with an open PR is not redispatched"
dispatched 1802 && ok "issue 1802 with a closed but unmerged PR is still dispatched" || ko "issue 1802 with a closed but unmerged PR is still dispatched: '$r'"
dispatched 1803 && ok "issue 1803 with no PR is still dispatched" || ko "issue 1803 with no PR is still dispatched: '$r'"

# 3) the merged page is cached: a second pass inside the TTL does not ask gh again, and a gh that fails while the cache
#    is warm does not change the pick
scene cached
issues '[{"number":1821,"title":"t","body":"b","labels":[{"name":"wave:01"}]},
         {"number":1822,"title":"t","body":"b","labels":[{"name":"wave:01"}]}]'
printf '[{"number":1825,"title":"x","body":"Refs #1821","merged_at":"2026-10-11T17:20:00Z"}]' > "$GH_CLOSED"
r=$(run)
dispatched 1821 && ko "issue 1821 with a merged PR is not redispatched (first pass): '$r'" || ok "issue 1821 with a merged PR is not redispatched (first pass)"
export GH_CLOSED_FAIL=1; r=$(run)
dispatched 1821 && ko "issue 1821 with a merged PR is not redispatched (second pass, warm cache): '$r'" || ok "issue 1821 with a merged PR is not redispatched (second pass, warm cache)"
dispatched 1822 && ok "issue 1822 is still dispatched (second pass)" || ko "issue 1822 is still dispatched (second pass): '$r'"
[ "$(closed_calls)" = "1" ] && ok "the merged page is fetched once for two passes" || ko "the merged page is fetched once for two passes: $(closed_calls) call(s)"

# 4) a gh that fails on the merged page must not make the frontier skip everything: with no cache yet the pass still
#    dispatches the issues it can see
scene closedfail
issues '[{"number":1823,"title":"t","body":"b","labels":[{"name":"wave:01"}]}]'
export GH_CLOSED_FAIL=1; r=$(run)
dispatched 1823 && ok "a failing merged-page fetch still dispatches" || ko "a failing merged-page fetch still dispatches: '$r'"

rm -rf "$T"
[ "$fail" = 0 ] && { echo "autofrontier merged-PR: all checks green"; exit 0; }
echo "autofrontier merged-PR: FAILED"; exit 1
