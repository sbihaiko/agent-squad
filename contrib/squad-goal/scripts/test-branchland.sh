#!/bin/bash
# Regression test for branchland.sh (#355): the pass that lands a pushed work branch whose check is green in `dev` by a
# fast-forward push. Stub gh (every argv logged), a bare local remote, a stub board, temp state dir. One line per case.
# usage: bash test-branchland.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs"
cp "$d/branchland.sh" "$d/ffland.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$T/s/" 2>/dev/null
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_LAND_REMOTE=%s/origin.git BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
# The remote the landing pushes to: a bare copy with main and dev, and gui/7-x one commit ahead of dev. mkorigin rebuilds it per case.
mkorigin() { rm -rf "$T/origin.git" "$T/w"; $G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
  (cd "$T/w" && $G checkout -q -b gui/7-x origin/dev && echo y > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "work" && $G push -q origin gui/7-x 2>/dev/null); }
mkorigin
# The stub: no open PR unless PRS names one. CHECKS names the check-runs answer.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7}; do printf '{"number":%s,"title":"t","body":"","state":"OPEN"},' "\$x"; done); echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/check-runs"*) echo "\${CHECKS:-{\"check_runs\":[]\}}";;
  *"issues/7/comments"*) cat > "$T/comment.body"; echo '{"id":1}';;
  *"issues/7"*) echo '{"number":7,"state":"closed"}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
# The review verdict of the branch tip, which the lander waits for beside the check (#357): VERDICT=APPROVE|CHANGES|none, kept
# the way branchreview.sh keeps it (review-<key>.md and review-<key>.sha under the state directory).
rev() { rm -f "$T/st/review-gui_7-x".*; [ "${1:-}" = none ] && return; printf 'VERDICT: %s\n' "${1:-APPROVE}" > "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"; }
bl() { bash "$T/s/branchland.sh" 2>&1; }
O="$G -C $T/origin.git"
reset() { rm -f "$T/st/cq"/* "$T/gh.log" "$T/board.log" "$T/st/auto"/branchland-*; mkorigin; rev APPROVE; }
rev APPROVE

r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ko "dev did not move: '$r'" || ok "green: the branch landed in dev"
$O merge-base --is-ancestor main dev && [ "$($O log --format=%s dev | head -1)" = work ] && ok "dev is a fast-forward of its old head" || ko "dev is not a fast-forward"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "a merge commit landed" || ok "history stays linear"
[ "$($O rev-parse main)" = "$($G -C "$T/co" rev-parse main)" ] && ok "main did not move" || ko "main moved"
$O rev-parse -q --verify refs/heads/gui/7-x >/dev/null && ko "branch not deleted" || ok "the landed branch is deleted by push"
case "$r" in *"BRANCHLAND LANDED gui/7-x as $($O rev-parse dev)"*"closed #7"*) ok "the line names the branch, the sha and the issue";; *) ko "no landed line: '$r'";; esac
# the landing ledger the hub's last-landing fact reads (#358): epoch, branch, landed sha, issue; the branch itself is gone from the remote
[ "$(awk -F'\t' '$2=="gui/7-x"{print $3 "/" $4}' "$T/st/landed.tsv" 2>/dev/null)" = "$($O rev-parse dev)/7" ] && ok "the landing is kept as a ledger row: branch, landed sha, issue" || ko "no ledger row: '$(cat "$T/st/landed.tsv" 2>/dev/null)'"
/usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && ok "the issue is closed by the API" || ko "no close call: $(cat "$T/gh.log")"
ls "$T/st/cq/"x__y#7-delivery~* >/dev/null 2>&1 && ok "the one delivery comment is queued" || ko "no delivery file: $(ls "$T/st/cq" 2>&1)"
/usr/bin/grep -q "issues/7/comments" "$T/gh.log" && ko "the pass posted the comment itself" || ok "the pass posts no comment itself (the flush does)"
/usr/bin/grep -q "o 3 Done 7" "$T/board.log" 2>/dev/null && ok "the card moves to Done" || ko "no Done move: $(cat "$T/board.log" 2>&1)"
/usr/bin/grep -qE "pulls/[0-9]+/merge|pr merge" "$T/gh.log" && ko "a merge API call was made" || ok "no merge API call"
# A branch whose issue is closed is old work (merged under a pull request, or dropped): it never lands again (#367).
reset; r=$(OPEN=8 CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a branch whose issue is closed does not land" || ko "a closed issue's branch landed: '$r'"
! /usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a closed issue's branch writes nothing" || ko "a closed issue's branch wrote"

# a red check waits and says why, once per head
reset; r=$(CHECKS="$RED" bl); r2=$(CHECKS="$RED" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "red: nothing landed" || ko "red landed"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"check"*"failure"*) ok "red: says why";; *) ko "red silent: '$r'";; esac
[ -z "$r2" ] && ok "red: says it once per head" || ko "red repeated: '$r2'"
# a running check waits the same way
reset; r=$(CHECKS="$RUNNING" bl); r2=$(CHECKS="$RUNNING" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*running*) ok "running: waits and says why";; *) ko "running silent: '$r'";; esac || ko "running landed"
[ -z "$r2" ] && ok "running: says it once per head" || ko "running repeated: '$r2'"
# no check yet
reset; r=$(CHECKS='{"check_runs":[]}' bl); case "$r" in *"BRANCHLAND WAIT gui/7-x"*"no check run"*) ok "no check run: waits and says why";; *) ko "no check silent: '$r'";; esac
# a green check is not enough: the review must have approved the tip (#357)
reset; rev CHANGES; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a review that asked for changes: nothing lands on a green check" || ko "landed over a CHANGES verdict"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"changes"*) ok "CHANGES: waits and says why";; *) ko "CHANGES silent: '$r'";; esac
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a branch under CHANGES closes nothing" || ko "CHANGES branch wrote"
reset; rev none; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*"review"*) ok "no review yet: waits and says why";; *) ko "no-review silent: '$r'";; esac || ko "landed with no review"
reset; echo 0000000000000000000000000000000000000000 > "$T/st/review-gui_7-x.sha"; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a review of an older tip does not approve the new one" || ko "landed on a stale review"
# a red check never closes the issue or moves the card
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a waiting branch closes nothing and moves no card" || ko "a waiting branch wrote"

# dev moved after the branch was cut: the branch is rebased on top, then lands
reset; (cd "$T/w" && $G checkout -q dev && echo z > h && $G add h && $G -c user.email=a@b -c user.name=t commit -q -m "other" && $G push -q origin dev 2>/dev/null)
r=$(CHECKS="$GREEN" bl); $O log --format=%s dev | tr '\n' ' ' | /usr/bin/grep -q "^work other init" && ok "a moved dev: the branch is rebased on top and lands" || ko "no rebase landing: $($O log --format=%s dev | tr '\n' ' ') $r"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "a merge commit landed" || ok "a moved dev: history stays linear"

# a refused push says so and lands nothing
reset; printf '#!/bin/sh\nwhile read o n ref; do [ "$ref" = refs/heads/dev ] && { echo "GH013: Repository rule violations found" >&2; exit 1; }; done\nexit 0\n' > "$T/origin.git/hooks/pre-receive"; chmod +x "$T/origin.git/hooks/pre-receive"
before=$($O rev-parse dev); r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$before" ] && ok "a refused push lands nothing" || ko "dev moved under a refusal"
case "$r" in *"BRANCHLAND REFUSE gui/7-x"*ruleset*) ok "a refused push says so";; *) ko "refusal silent: '$r'";; esac
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ! ls "$T/st/cq/"* >/dev/null 2>&1 && ok "a refused push closes nothing, queues nothing, moves no card" || ko "a refusal wrote"
r2=$(CHECKS="$GREEN" bl); [ -z "$r2" ] && ok "a refusal is said once per head" || ko "refusal repeated: '$r2'"

# a branch that has an open pull request is the pull request lander's
reset; r=$(PRS='[{"number":41,"head":{"ref":"gui/7-x"}}]' CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a branch with an open PR is left to the PR lander" || ko "landed a PR branch: '$r'"
# a branch that names no issue is not a work branch
reset; (cd "$T/w" && $G push -q origin gui/7-x:refs/heads/scratch 2>/dev/null; $G push -q origin :gui/7-x 2>/dev/null)
r=$(CHECKS="$GREEN" bl); [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a branch naming no issue is ignored" || ko "landed scratch: '$r'"

rm -rf "$T"; exit $fail
