#!/bin/bash
# Regression test for prland.sh (#83): the pass that closes a pull request whose check failed on nothing but the known
# baseline. Stub gh (every argv logged), throwaway checkout, temp state dir, a baseline file of its own. One line per case.
# usage: bash test-prland.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs"
cp "$d/prland.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$T/s/" 2>/dev/null
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_CHECKOUT=%s SG_CI_BASELINE=%s/baseline.txt\n' "$T/st" "$T/co" "$T" > "$T/s/env.sh"
printf 'tests/test_e2e.py::test_a  # needs MESENAI_REPO\ntests/test_e2e.py::test_b  # needs MESENAI_REPO\ntests/test_guard.py::test_reap  # macOS only\n' > "$T/baseline.txt"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q fix/7-x)
# The stub: one open PR #41 (head SHA1, Refs #7). CHECKS names the check-runs answer, LOG the failed job's log, DRAFT the draft flag.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?per_page"*) echo "[{\"number\":41,\"draft\":\${DRAFT:-false},\"head\":{\"ref\":\"fix/7-x\",\"sha\":\"\${SHA:-1111111111111111111111111111111111111111}\"},\"base\":{\"ref\":\"main\"},\"body\":\"Work.\\\\n\\\\nRefs #7\"}]";;
  *"check-runs"*) echo "\${CHECKS:-{\"check_runs\":[]\}}";;
  *"actions/jobs/"*"/logs"*) printf '%b' "\${LOG:-}";;
  *"pulls/41/merge"*) echo '{"merged":true,"sha":"abcdef1234567890"}';;
  *"pulls/41"*) echo "{\"number\":41,\"state\":\"\${STATE:-open}\",\"draft\":\${DRAFT:-false},\"mergeable_state\":\"\${MS:-clean}\",\"merge_commit_sha\":\"abcdef1234567890\"}";;
  *"issues/7/comments"*) cat >/dev/null; echo '{"id":1}';;
  *"issues/7"*) echo '{"number":7,"state":"closed"}';;
  "pr merge 41"*) echo "merged";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
BASE_LOG='2026-10-13T11:44:53.4Z FAILED tests/test_e2e.py::test_a - AssertionError: set MESENAI_REPO\n2026-10-13T11:44:53.5Z FAILED tests/test_e2e.py::test_b - AssertionError\n'
pl() { bash "$T/s/prland.sh" 2>&1; }
merged() { /usr/bin/grep -q "^pr merge 41 " "$T/gh.log" 2>/dev/null; }
reset() { rm -f "$T/gh.log" "$T/st/auto"/prland-*; }

r=$(CHECKS="$GREEN" pl); merged && ok "green: merged" || ko "green not merged: '$r'"
case "$r" in *"PRLAND MERGED #41"*abcdef123*"#7"*) ok "the merge line names the PR, the sha and the issue";; *) ko "no merge line: '$r'";; esac
/usr/bin/grep -q "pr merge 41 --repo x/y --squash --delete-branch" "$T/gh.log" && ok "squash merge, branch deleted" || ko "merge flags: $(cat "$T/gh.log")"
/usr/bin/grep -q "issues/7/comments" "$T/gh.log" && /usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && ok "the issue got its comment and closed" || ko "no comment/close: $(cat "$T/gh.log")"
reset; r=$(CHECKS="$RED" LOG="$BASE_LOG" pl); merged && ok "red on the baseline only: merged" || ko "baseline red not merged: '$r'"
reset; r=$(CHECKS="$RED" LOG="${BASE_LOG}2026-10-13T11:44:53.6Z FAILED tests/test_new.py::test_c - boom\n" pl)
merged && ko "a failure outside the baseline was merged" || ok "a failure outside the baseline is not merged"
case "$r" in *"PRLAND REFUSE #41"*"tests/test_new.py::test_c"*) ok "the refusal names the failure";; *) ko "refusal silent on the failure: '$r'";; esac
reset; r=$(CHECKS="$RED" LOG="" pl); merged && ko "a red check naming no test was merged" || { case "$r" in *"PRLAND REFUSE #41"*) ok "a red check naming no test is refused";; *) ko "no refusal: '$r'";; esac; }
reset; r=$(CHECKS="$RUNNING" pl); merged && ko "merged while the check runs" || { case "$r" in *"PRLAND WAIT #41"*) ok "a running check waits";; *) ko "no wait: '$r'";; esac; }
r=$(CHECKS="$RUNNING" pl); case "$r" in *"PRLAND WAIT #41"*) ko "WAIT repeated for the same head: '$r'";; *) ok "one WAIT per head";; esac
r=$(SHA=2222222222222222222222222222222222222222 CHECKS="$RUNNING" pl); case "$r" in *"PRLAND WAIT #41"*) ok "a new head gets its own WAIT";; *) ko "new head silent: '$r'";; esac
reset; r=$(DRAFT=true CHECKS="$GREEN" pl); merged && ko "a draft was merged" || { case "$r" in *"PRLAND REFUSE #41"*draft*) ok "a draft is refused";; *) ko "draft: '$r'";; esac; }
reset; r=$(CHECKS="$GREEN" MS=dirty pl); merged && ko "a conflicting PR was merged" || { case "$r" in *"PRLAND REFUSE #41"*) ok "a conflicting PR is refused";; *) ko "conflict: '$r'";; esac; }
# a live run names the head branch: active-runs.txt + a fresh record
mkdir -p "$T/co/runs/run-1"; echo "Fix #7 on fix/7-x" > "$T/co/runs/run-1/request.txt"; touch "$T/co/runs/run-1/record.jsonl"; echo run-1 > "$T/st/active-runs.txt"
reset; r=$(CHECKS="$GREEN" pl); merged && ko "merged under a live run" || { case "$r" in *"PRLAND WAIT #41"*"live run"*) ok "a live run on the branch waits";; *) ko "live run: '$r'";; esac; }
rm -rf "$T/co/runs/run-1"; : > "$T/st/active-runs.txt"
# the branch is checked out somewhere with uncommitted changes
$G -C "$T/co" worktree add -q "$T/wt" fix/7-x; echo dirty >> "$T/wt/f"
reset; r=$(CHECKS="$GREEN" pl); merged && ko "merged with the branch dirty in a worktree" || { case "$r" in *"PRLAND WAIT #41"*uncommitted*) ok "a dirty worktree on the branch waits";; *) ko "dirty: '$r'";; esac; }
$G -C "$T/co" worktree remove --force "$T/wt"
reset; r=$(CHECKS="$GREEN" pl); merged && ok "released: merged once the branch is free" || ko "still held: '$r'"
# the throttle is closed: no gh call at all
reset; echo "{\"until\": $(( $(date +%s) + 3600 )), \"base\": 900, \"message\": \"rate limit\"}" > "$T/st/gh-cooldown.json"
r=$(CHECKS="$GREEN" pl); [ -e "$T/gh.log" ] && ko "gh was called under a cooldown: $(cat "$T/gh.log")" || ok "no gh call while the cooldown holds"
rm -f "$T/st/gh-cooldown.json"
# the repo's own baseline: every entry carries a reason
bl="$d/../ci-baseline.txt"; [ -f "$bl" ] && [ "$(sed 's/#.*//' "$bl" | awk 'NF' | wc -l)" -ge 26 ] && ! sed 's/#.*//' "$bl" | awk 'NF' | /usr/bin/grep -vq '^tests/.*::' && ok "the repo baseline lists 26 node ids" || ko "repo baseline missing or malformed"
[ -f "$bl" ] && [ -z "$(/usr/bin/grep -v '^#' "$bl" | awk 'NF && !/#/')" ] && ok "every baseline entry carries a reason" || ko "a baseline entry has no reason"
rm -rf "$T"; exit $fail
