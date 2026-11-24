#!/bin/bash
# seconds: 9.07
# Regression test for redispatching a clean issue worktree (#65).
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
export SG_NIGHTLY_OFF=1   # the dispatch hold reads the wall clock: the nightly hour must not hold a fixture pass (#2397); a case that tests the hold sets its own hour
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/co/docs/squad"
: > "$T/st/active-runs.txt"
cp "$d"/{autobug.sh,columnlimit.sh,loopbug.sh,cluster.py,startability.sh,skiplabels.sh,ghcall.sh,drainpolicy.py,address.sh,envelope-spend.sh,ghread.py,ghthrottle.py,refusal-names.sh,workbranches.sh,issue_ledger.py} "$T/s/"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"   # the blocker gate (#479): #65 has no blocker
printf '#!/bin/bash\nexit 0\n' > "$T/s/board.sh"; chmod +x "$T/s/board.sh"   # the card door lands: a dispatch writes its card before its launch, and a pending write holds the next pass (ADR-0165)
printf 'import sys\nif sys.argv[1:]: sys.exit(1)\nprint("65\\tTodo")\n' > "$T/s/issue_state.py"   # the seam's door, stubbed: #65's card sits in Todo (#550)
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
/usr/bin/git init -q -b main "$T/co"
/usr/bin/git -C "$T/co" -c user.email=a@b -c user.name=t commit -q --allow-empty -m base
mkdir -p "$T/co/worktrees/child"
/usr/bin/git -C "$T/co" worktree add -q -b gui/65 "$T/co/worktrees/child/gui-65" main
cat > "$T/s/env.sh" <<EOF
export SG_REPO=x/y SG_STATE=$T/st SG_BASE=main SG_CHECKOUT=$T/co SG_BUG_LABEL=bug SG_CODING_RULES=rules
. "$T/s/address.sh"
EOF
cat > "$T/bin/gh" <<'EOF'
#!/bin/bash
case "$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) echo '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":65,"title":"Issue 65","body":"body","state":"OPEN","labels":{"nodes":[{"name":"bug"},{"name":"ready-for-agent"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}' ;;   # the blocker gate's one list read (#479)
  *issues?state=open*labels=bug*) echo '[{"number":65,"title":"Issue 65","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;   # the candidate list, as GitHub answers it (JSON)
  *issues/65/comments*) echo "[]" ;;
  *issues/65\ -q*) printf '"Issue 65"\nbody\n' ;;
  *issues/65*) printf '{"number":65,"title":"Issue 65","body":"body","created_at":"2026-10-13T00:00:00Z"}\n' ;;
  *"issue(number: 65)"*) echo '{"data":{"repository":{"i65":{"number":65,"title":"Issue 65","body":"body","state":"OPEN","createdAt":"2026-10-13T00:00:00Z","labels":{"nodes":[{"name":"bug"}]},"blockedBy":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}}}}' ;;   # the single-issue read, as the GraphQL fact answers it
  *) echo "" ;;
esac
EOF
cat > "$T/s/mkreq.sh" <<EOF
#!/bin/bash
cp "\$2" "$T/body"
echo "\$2"
EOF
cat > "$T/s/launch.py" <<'EOF'
pass
EOF
chmod +x "$T/bin/gh" "$T/s/mkreq.sh"
export PATH="$T/bin:$PATH" SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps" SG_SINGLE_AUTOBUG=1 BOARD="$T/s/board.sh"
run() { rm -f "$T/body" "$T/st/auto/bug-65"; bash "$T/s/autobug.sh" >"$T/run.out" 2>&1; }
run
/usr/bin/grep -qF 'REUSE ISSUE WORKTREE: Use the clean existing worktree' "$T/body" \
  && ok "a clean issue worktree is reused" \
  || ko "a clean issue worktree was not offered for reuse: $(cat "$T/run.out")"
printf 'unfinished work\n' > "$T/co/worktrees/child/gui-65/left.txt"
run
/usr/bin/grep -qF 'PRESERVE ISSUE WORKTREE: The existing worktree' "$T/body" \
  && /usr/bin/grep -qF 'Do not reuse, reset, delete, or replace it.' "$T/body" \
  && ok "a dirty issue worktree is preserved and reported" \
  || ko "a dirty issue worktree was not preserved and reported"
rm -rf "$T"
exit $fail
