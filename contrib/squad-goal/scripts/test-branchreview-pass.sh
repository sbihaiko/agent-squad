#!/bin/bash
# Regression test for the review pass (#397): branchreviewpass.sh reviews each pushed work branch once per tip, so branchland.sh
# finds an APPROVE at the tip. Stub gh (every argv logged), stub review.sh (writes the verdict and the sha, logs the call), a bare
# local remote, temp state dir. One line per case. usage: bash test-branchreview-pass.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"
cp "$d/branchreviewpass.sh" "$d/workbranches.sh" "$d/branchreview.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$T/s/" 2>/dev/null
cat > "$T/s/env.sh" <<J
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=main SG_LAND_BRANCH=dev SG_CHECKOUT=$T/co SG_LAND_REMOTE=\${SG_LAND_REMOTE:-$T/origin.git}
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_CACHE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
. "$T/s/address.sh"; . "$T/s/ghcall.sh"
J
# The review stub: one log line per call, then the verdict and the reviewed sha under the branch key, as review.sh keeps them.
cat > "$T/s/review.sh" <<J
#!/bin/bash
br=\$3; key=\$(printf '%s' "\$br" | tr / _); echo "\$br \$4" >> "$T/review.log"
[ -n "\${FAILREV:-}" ] && { echo "model failed"; exit 1; }   # a failed review keeps no verdict and no sha
echo "VERDICT: APPROVE" > "$T/st/review-\$key.md"; $G -C "$T/origin.git" rev-parse "\$br" > "$T/st/review-\$key.sha"
echo "model=stub"
J
O="$G -C $T/origin.git"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
$G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
# push <branch>: one new commit on the branch, cut from dev the first time.
push() { (cd "$T/w" && { $G checkout -q "$1" 2>/dev/null || $G checkout -q -b "$1" origin/dev; } && date +%s%N >> "g-$(printf '%s' "$1" | tr / _)" && $G add -A && $G -c user.email=a@b -c user.name=t commit -q -m "work $1" && $G push -q origin "$1" 2>/dev/null); }
for b in gui/7-x fix/8-y gui/9-z feature/7-q; do push "$b"; done
# gh: no open PR unless PRS names one; OPEN names the open issues; a commit read answers the remote tip of the branch.
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\${*//\$'\n'/ }"; echo "\$a" >> "$T/gh.log"
case "\$a" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7 8}; do printf '{"number":%s,"title":"t","body":"","state":"OPEN"},' "\$x"; done); echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/commits/"*) b=\${a#*commits/}; b=\${b%% *}; echo "{\"sha\":\"\$($O rev-parse "\$b")\"}";;
  *) echo '{}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
rp() { bash "$T/s/branchreviewpass.sh" 2>&1; }
n() { cat "$T/review.log" 2>/dev/null | /usr/bin/grep -c "^$1 "; }

# 1) one review for a work branch with an open issue and no PR; the PR branch, the closed-issue branch and a non-work branch get none
PRS='[{"number":41,"head":{"ref":"fix/8-y"}}]'; export PRS
r=$(rp)
[ "$(n gui/7-x)" = 1 ] && ok "a work branch with no review at its tip is reviewed once" || ko "gui/7-x reviewed $(n gui/7-x) times: '$r' $(cat "$T/review.log" 2>/dev/null)"
/usr/bin/grep -q "^gui/7-x 7$" "$T/review.log" && ok "the review names the branch and its issue" || ko "review call: $(cat "$T/review.log" 2>/dev/null)"
[ "$(n fix/8-y)" = 0 ] && ok "a branch with an open PR is left to the PR lander" || ko "the PR branch was reviewed"
[ "$(n gui/9-z)" = 0 ] && ok "a branch that names no open issue is not reviewed" || ko "the closed-issue branch was reviewed"
[ "$(n feature/7-q)" = 0 ] && ok "a branch that is not a work branch is not reviewed" || ko "a non-work branch was reviewed"
[ "$(cat "$T/st/review-gui_7-x.sha" 2>/dev/null)" = "$($O rev-parse gui/7-x)" ] && ok "the reviewed sha is the tip" || ko "no reviewed sha at the tip"
# 2) the same tip is never reviewed twice
r=$(rp); [ "$(n gui/7-x)" = 1 ] && ok "a second pass on the same tip reviews nothing" || ko "the same tip was reviewed again: $(n gui/7-x)"
# 3) a new tip is reviewed once more
push gui/7-x; r=$(rp); r=$(rp)
[ "$(n gui/7-x)" = 2 ] && ok "a new tip is reviewed once" || ko "after a new push gui/7-x reviewed $(n gui/7-x) times"
# 4) the pass is bounded: a few branches per pass, the rest on the next passes
for i in 10 11 12 13; do push "gui/$i-b"; done; : > "$T/review.log"
r=$(OPEN="7 8 10 11 12 13" SG_REVIEW_PASS_MAX=2 rp)
[ "$(wc -l < "$T/review.log" | tr -d ' ')" = 2 ] && ok "one pass reviews at most SG_REVIEW_PASS_MAX branches" || ko "one pass reviewed $(wc -l < "$T/review.log" | tr -d ' ')"
r=$(OPEN="7 8 10 11 12 13" SG_REVIEW_PASS_MAX=2 rp)
[ "$(wc -l < "$T/review.log" | tr -d ' ')" = 4 ] && [ "$(sort -u "$T/review.log" | wc -l | tr -d ' ')" = 4 ] && ok "the next pass reviews the next branches" || ko "second bounded pass: $(cat "$T/review.log")"
# 5) a read that fails stops the pass and reviews nothing
: > "$T/review.log"; push gui/7-x; r=$(SG_LAND_REMOTE=$T/none.git rp)
[ ! -s "$T/review.log" ] && ok "an unreadable branch list reviews nothing" || ko "a review ran with no branch list"
case "$r" in *"BRANCHREVIEW: the remote branch list is not readable"*) ok "the failed read is named";; *) ko "no read failure line: '$r'";; esac
# 6) a failed review is retried, but at most 3 failed tries at one tip (ADR-0134); the stop is said once; a new tip resets the count
: > "$T/review.log"; o=; for _ in 1 2 3 4 5; do o+=$(FAILREV=1 rp)$'\n'; done
[ "$(n gui/7-x)" = 3 ] && ok "a failed review is retried, and stops after 3 failed tries at one tip" || ko "failed tip tried $(n gui/7-x) times"
[ "$(printf '%s' "$o" | /usr/bin/grep -c '^BRANCHREVIEW STOP gui/7-x: ')" = 1 ] && ok "the stop is said once" || ko "stop lines: '$o'"
push gui/7-x; r=$(FAILREV=1 rp)
[ "$(n gui/7-x)" = 4 ] && ok "a new tip resets the failed-try count" || ko "after a new tip gui/7-x tried $(n gui/7-x) times"
# 7) the cycle runs the pass under its own single.sh lock, gated like the landers
w=$(/usr/bin/grep -n 'single.sh" branchreview ' "$d/watch.sh")
case "$w" in *'SG_PRLAND_PAUSED'*'branchreviewpass.sh'*) ok "watch.sh runs the pass under its own single.sh lock, gated by SG_PRLAND_PAUSED";; *) ko "watch.sh does not run the review pass: '$w'";; esac
exit $fail
