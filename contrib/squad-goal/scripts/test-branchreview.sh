#!/bin/bash
# seconds: 14.14
# Regression test for the branch review (#357): review.sh --branch, branchreview.sh and branchfix.sh, with stubbed gh and claude
# (no network, no model). The review reads the diff of a work branch against `dev`, never a pull request; its findings go to the
# one status comment of the issue (a queue file of the status kind, which cqflush.sh edits in place); a fix round works on the
# same branch. A child of a wave (#376) sets SG_REVIEW_BASE to the wave branch: the diff is then the child's own commits
# against their merge-base with the wave branch, never the whole wave, and the base is kept beside the verdict.
# The child of a run (#448) is reviewed the same way but from its own worktree: its branch is pushed nowhere, so review.sh
# --worktree reads HEAD and the three-dot diff against the wave ref, and childreview.sh keeps the files the entry reads.
# A child of the loop is reviewed by its own run (#601): runreview.sh wraps childreview.sh as the run's review gate.
# usage: bash test-branchreview.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_REVIEW_BASE SG_CHECKOUT SG_LAND_REMOTE   # each case decides the base and the checkout the review reads; an ambient one from a live loop must not
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$d/review.sh" "$d/loopbug.sh" "$d/revformat.py" "$d/address.sh" "$d/modelreg.py" "$d/registry.json" "$T/s/"; : > "$T/s/codex-reset.py"
cp "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/branchreview.sh" "$d/branchfix.sh" "$d/childreview.sh" "$T/s/" 2>/dev/null
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}\n' > "$T/s/models.json"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_LAND_BRANCH=dev; SG_CHECKOUT=\${SG_CHECKOUT:-$T}; SG_REVIEW_RULES=CLAUDE.md; SG_CODING_RULES=CLAUDE.md
. "$T/s/address.sh"
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "$GHLOG"
case "$*" in
  *commits/gui/7-x*) echo "{\"sha\":\"$(cat "$GHHEAD")\"}";;   # the door reads JSON and applies -q itself
  *commits/gui/8-x*) echo "{\"sha\":\"$(cat "$GHHEAD")\"}";;
  *Accept*compare/wave/run-1...gui/7-x*) printf 'diff --git a/f b/f\n+wave-only-child-diff\n';;
  *compare/wave/run-1...gui/7-x*) echo '{"files":[{"filename":"W/a.cs"}]}';;
  *Accept*compare/dev...gui/7-x*) printf 'diff --git a/f b/f\n+branch-diff-line\n';;
  *compare/dev...gui/7-x*) echo '{"files":[{"filename":"UI/a.cs"}]}';;
  *Accept*compare*) printf 'diff --git a/f b/f\n+delta-line\n';;
  *compare*) echo '{"status":"ahead"}';;
  *issues/7*) echo '{"number":7,"title":"title","body":""}';;
  *issues/8*) echo '{"number":8,"title":"title","body":""}';;
  *) echo stub;;
esac
J
# #2647: a CHANGES answers in JSON with a repro that fails, so its Blocking finding stands; a markdown one would be Debt.
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "$CLAUDE_PROMPT"; if [ -n "$CLAUDE_CHANGES" ]; then printf '%s' '{"result":"","structured_output":{"verdict":"CHANGES","blocking":[{"location":"a.cs:1","defect":"diverges from ADR-0001","failure":"wrong","fix":"fix","repro":"exit 1"}],"debt":[],"resolved":[]},"is_error":false,"subtype":"success"}'; else echo "VERDICT: APPROVE"; fi
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH" GHLOG="$T/gh.log" GHHEAD="$T/head" CLAUDE_PROMPT="$T/prompt"
echo aaa111 > "$T/head"; key=gui_7-x
# 1) a branch with no pull request: the diff is read against dev and no pull request is touched
out=$(CLAUDE_CHANGES=1 bash "$T/s/branchreview.sh" gui/7-x 7 2>&1); rc=$?
[ $rc = 0 ] && ok "the review runs on a branch with no pull request" || ko "branchreview exited $rc: $out"
/usr/bin/grep -q "compare/dev...gui/7-x" "$T/gh.log" && /usr/bin/grep -q "branch-diff-line" "$T/prompt" && ok "the prompt carries the diff of the branch against dev" || ko "no dev diff in the prompt"
! /usr/bin/grep -q "pulls" "$T/gh.log" && ok "no pull request is read" || ko "a pull request call: $(/usr/bin/grep pulls "$T/gh.log")"
head -1 "$T/st/review-$key.md" | /usr/bin/grep -q CHANGES && [ "$(cat "$T/st/review-$key.sha")" = aaa111 ] && ok "the verdict and the reviewed sha are kept under the branch key" || ko "no verdict kept"
[ "$(cat "$T/st/review-$key.base" 2>/dev/null)" = dev ] && ok "the base the review was read against is kept beside the verdict" || ko "no base kept: '$(cat "$T/st/review-$key.base" 2>/dev/null)'"
# 2) the findings go to the status comment of the issue: a status-kind queue file, never a plain comment
n1=$(ls "$T/st/cq/" | wc -l | tr -d ' '); ls "$T/st/cq/"x__y#7-status~* >/dev/null 2>&1 && ok "the findings are queued as the issue's status comment" || ko "no status file: $(ls "$T/st/cq")"
/usr/bin/grep -q "a.cs:1" "$T/st/cq/"x__y#7-status~* && ok "the status comment carries the findings" || ko "findings missing from the status comment"
/usr/bin/grep -q "@ aaa111" "$T/st/cq/"x__y#7-status~* && ok "the status comment names the reviewed sha" || ko "the status comment names no sha: $(head -1 "$T/st/cq/"x__y#7-status~*)"
# 3) a fix round: a new commit on the same branch is reviewed as a delta, and the second round queues a status file again
echo bbb222 > "$T/head"; rm -f "$T/st/cq/"*; : > "$T/gh.log"
out=$(bash "$T/s/branchreview.sh" gui/7-x 7 2>&1)
/usr/bin/grep -q "FIX ROUND" "$T/prompt" && /usr/bin/grep -q "delta-line" "$T/prompt" && ok "a fix round on the same branch is reviewed as a delta" || ko "no fix round prompt"
ls "$T/st/cq/" | /usr/bin/grep -qv -- '-status~' && ko "a round queued a comment that is not the status one: $(ls "$T/st/cq")" || ok "the second round queues the status comment again (cqflush edits it in place), no new comment"
! /usr/bin/grep -qE "issues/7/comments" "$T/gh.log" && ok "the review posts no comment itself" || ko "the review posted a comment"
head -1 "$T/st/review-$key.md" | /usr/bin/grep -q APPROVE && ok "the second round's verdict replaces the first" || ko "old verdict kept"
# 4) the fix request: it works on the same branch, commits on it and opens no pull request
echo '#7 title' > /dev/null; printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — wrong — fix\n' > "$T/st/review-$key.md"
r=$(AF_DRY=1 bash "$T/s/branchfix.sh" gui/7-x 7 2>&1)
case "$r" in *"commit on top of the branch your worktree has checked out (that branch is the tip of gui/7-x), and create no branch of your own; push nothing and open no pull request: once the run ends, the loop pushes the run's head to gui/7-x (fast-forward only)"*) ok "the fix round commits on the work branch, and the loop pushes it (#768)";; *) ko "fix request: '$r'";; esac
case "$r" in *"a.cs:1"*) ok "the fix request carries the findings";; *) ko "no findings in the fix request";; esac
# 5) the child of a wave (#376): the review reads only the child's own commits, against its merge-base with the wave branch,
#    and the verdict still lands in the issue's one status comment. SG_REVIEW_BASE names the branch the diff is taken against.
: > "$T/gh.log"; rm -f "$T/st/cq/"* "$T/st/review-$key".*; echo ccc333 > "$T/head"
out=$(SG_REVIEW_BASE=wave/run-1 CLAUDE_CHANGES=1 bash "$T/s/branchreview.sh" gui/7-x 7 2>&1); rc=$?
[ $rc = 0 ] && ok "wave: the review runs with the wave branch as its base" || ko "wave: branchreview exited $rc: $out"
/usr/bin/grep -q "compare/wave/run-1...gui/7-x" "$T/gh.log" && ok "wave: the diff is read against the wave branch" || ko "wave: no wave compare: $(cat "$T/gh.log")"
! /usr/bin/grep -q "compare/dev...gui/7-x" "$T/gh.log" && ok "wave: the whole wave (a dev compare) is never read" || ko "wave: a dev compare was made"
/usr/bin/grep -q 'wave-only-child-diff' "$T/prompt" && ! /usr/bin/grep -q 'branch-diff-line' "$T/prompt" && ok "wave: the prompt carries only the child's own diff" || ko "wave: the prompt carries the wrong diff"
[ "$(cat "$T/st/review-$key.base" 2>/dev/null)" = wave/run-1 ] && ok "wave: the wave base is kept beside the verdict" || ko "wave: no base kept: '$(cat "$T/st/review-$key.base" 2>/dev/null)'"
ls "$T/st/cq/"x__y#7-status~* >/dev/null 2>&1 && /usr/bin/grep -q "a.cs:1" "$T/st/cq/"x__y#7-status~* && ok "wave: the verdict lands in the issue's one status comment" || ko "wave: no status comment: $(cat "$T/st/cq/"* 2>&1)"
ls "$T/st/cq/" | /usr/bin/grep -qv -- '-status~' && ko "wave: a round queued a comment that is not the status one: $(ls "$T/st/cq")" || ok "wave: the status comment is the only one"
! /usr/bin/grep -qE "issues/7/comments" "$T/gh.log" && ok "wave: the review posts no comment itself" || ko "wave: the review posted a comment"
# 6) the child of a run (#448): its branch is pushed nowhere (#375), so its review is read in its own worktree, against the
#    wave branch, from the commits it holds. Nothing that lists pushed branches can see it, and the files the entry waits for
#    are named by the branch itself, with HEAD as the reviewed head.
: > "$T/gh.log"; rm -f "$T/st/cq/"* "$T/st/review-$key".* "$T/st/review-squad_run-1_ws-child".*
G=/usr/bin/git; $G init -q -b dev "$T/repo"; (cd "$T/repo" && echo base > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m base)
$G clone -q --bare "$T/repo" "$T/remote.git"; $G clone -q "$T/remote.git" "$T/child"
(cd "$T/child" && $G checkout -q -b wave/run-1 && echo wave > w && $G add w && $G -c user.email=a@b -c user.name=t commit -q -m "the wave's own commit" \
  && $G push -q origin HEAD:wave/run-1 && $G checkout -q -b squad/run-1/ws-child && echo child > c && $G add c && $G -c user.email=a@b -c user.name=t commit -q -m "work 7 (Refs #7)")
ck=squad_run-1_ws-child
out=$(cd "$T/child" && CLAUDE_CHANGES=1 bash "$T/s/childreview.sh" run-1 7 2>&1); rc=$?
[ $rc = 0 ] && ok "child: the review of a run child runs in its own worktree" || ko "child: childreview exited $rc: $out"
/usr/bin/grep -q '^+child' "$T/prompt" && ! /usr/bin/grep -q '^+wave' "$T/prompt" && ok "child: the prompt carries the child's own commits against the wave branch" || ko "child: the prompt carries the wrong diff"
! /usr/bin/grep -qE 'compare/|commits/squad' "$T/gh.log" && ok "child: no remote read of a branch that is on no remote" || ko "child: a remote branch read: $(cat "$T/gh.log")"
head -1 "$T/st/review-$ck.md" | /usr/bin/grep -q CHANGES && [ "$(cat "$T/st/review-$ck.sha")" = "$(cd "$T/child" && $G rev-parse HEAD)" ] && ok "child: the verdict and the reviewed head are kept under the child's branch key" || ko "child: no verdict kept: $(ls "$T/st")"
[ "$(cat "$T/st/review-$ck.base" 2>/dev/null)" = wave/run-1 ] && ok "child: the base it was read against is the wave branch" || ko "child: base '$(cat "$T/st/review-$ck.base" 2>/dev/null)'"
case "$out" in *"squad/run-1/ws-child"*"VERDICT: CHANGES"*) ok "child: the review prints the branch, its head and the verdict";; *) ko "child: '$out'";; esac
ls "$T/st/cq/"x__y#7-status~* >/dev/null 2>&1 && /usr/bin/grep -q "a.cs:1" "$T/st/cq/"x__y#7-status~* && ok "child: the verdict lands in the issue's one status comment" || ko "child: no status comment: $(cat "$T/st/cq/"* 2>&1)"
# 7) #483: a review that ended with no verdict for the head (superseded, or every model failed: review.sh exits 1 and keeps
#    no reviewed head) is no review. childreview.sh exits non-zero, so childcc.py drops its once-per-head claim and the next
#    pass reviews the head again. It exited 0 on the BLOCKED text alone, and the claim held the child out of its wave for good.
rm -f "$T/st/review-$ck".*; cp "$T/s/review.sh" "$T/s/review.sh.real"
printf '#!/bin/bash\n. "$(dirname "$0")/env.sh"\necho "VERDICT: BLOCKED (worktree x head moved during the review)" > "$SG_STATE/review-%s.md"; exit 1\n' "$ck" > "$T/s/review.sh"
out=$(cd "$T/child" && bash "$T/s/childreview.sh" run-1 7 2>&1); rc=$?
[ $rc != 0 ] && ok "child: a review with no verdict for the head exits non-zero, so its claim goes" || ko "child: a review with no verdict exited 0: $out"
mv "$T/s/review.sh.real" "$T/s/review.sh"
# 8) #601, ADR-0145 rule 9: a child of the loop is reviewed by its own run. runreview.sh, the loop workflow's review gate,
#    wraps childreview.sh in the child's worktree: CHANGES requeues the issue at once with every blocking finding of the
#    review (never the first line only), writes the requeue fact childcc honors, and exits non-zero so the run holds the child.
cp "$d/runreview.sh" "$d/issue_ledger.py" "$d/caps.sh" "$T/s/" 2>/dev/null; mkdir -p "$T/st/auto"; echo wave/run-1 > "$T/st/auto/wave-run-1"
printf '#!/bin/bash\nprintf "wave.sh %%s\\n" "$*" >> "%s/wave.log"\n' "$T" > "$T/s/wave.sh"; rm -f "$T/st/review-$ck".*
out=$(cd "$T/child" && SQUAD_CAPS_DIR="$T/caps" SG_SQUAD="$(cd "$d/../../.." && pwd)" CLAUDE_CHANGES=1 bash "$T/s/runreview.sh" run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && ok "run: CHANGES exits non-zero, so the run holds the child" || ko "run: runreview exited $rc: $out"
/usr/bin/grep -q '^wave.sh requeue 7 ' "$T/wave.log" && /usr/bin/grep -q -- '^- a.cs:1 — diverges from ADR-0001 — .*wrong — fix — repro: exit 1$' "$T/wave.log" \
  && ok "run: the issue is requeued with the review's blocking finding" || ko "run: wave.log '$(cat "$T/wave.log" 2>/dev/null)'"
[ "$(SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" requeued "x/y#7" run-1 2>/dev/null)" = "$(cd "$T/child" && $G rev-parse HEAD)" ] && ok "run: the requeue fact holds the reviewed head" \
  || ko "run: fact '$(SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" requeued "x/y#7" run-1 2>/dev/null)'"
# 9) #655: a child that sits on a dev tip newer than its wave branch (a fix run rebased it there) is reviewed on its own
#    commits only. dev's later commits are in dev already: they are never the child's, so the review never asks to change them.
#    The base file still names the wave branch, so the entry's gate passes for this correctly reviewed child.
rm -f "$T/st/review-squad_run-1_ws-late".* "$T/st/cq/"*
(cd "$T/repo" && echo devlater > dl && $G add dl && $G -c user.email=a@b -c user.name=t commit -q -m "dev's later landing" && $G push -q "$T/remote.git" HEAD:dev)
(cd "$T/child" && $G fetch -q origin && $G checkout -q -b squad/run-1/ws-late origin/dev && echo late > l && $G add l && $G -c user.email=a@b -c user.name=t commit -q -m "late work 7 (Refs #7)")
out=$(cd "$T/child" && CLAUDE_CHANGES=1 bash "$T/s/childreview.sh" run-1 7 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q '^+late' "$T/prompt" && ! /usr/bin/grep -q '^+devlater' "$T/prompt" && ! /usr/bin/grep -q '^+wave' "$T/prompt" \
  && ok "late: a child on a dev tip newer than its wave is reviewed on its own commits only" || ko "late: rc=$rc, the prompt diff: $(/usr/bin/grep '^+[a-z]' "$T/prompt" | tr '\n' ' ') $out"
[ "$(cat "$T/st/review-squad_run-1_ws-late.base" 2>/dev/null)" = wave/run-1 ] && ok "late: the base file still names the wave branch, as the entry requires" || ko "late: base '$(cat "$T/st/review-squad_run-1_ws-late.base" 2>/dev/null)'"
# 10) #772: review.sh prints a progress line before its model= line. The review reads every line, so review.sh is never
#     killed by SIGPIPE in the middle of the call, and the status comment names the model.
mkdir -p "$T/p"; cp "$T/s/branchreview.sh" "$T/s/env.sh" "$T/s/ghcall.sh" "$T/s/ghread.py" "$T/s/address.sh" "$T/p/" 2>/dev/null
cat > "$T/p/review.sh" <<J
#!/bin/bash
echo "review m1 on x still running, 30s of 60s"; sleep 0.2; echo "model=m1 verdict=APPROVE"
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_7-x.md"; : > "$T/p/finished"
J
echo ccc333 > "$T/head"; rm -f "$T/st/cq/"*
bash "$T/p/branchreview.sh" gui/7-x 7 >/dev/null 2>&1
[ -e "$T/p/finished" ] && /usr/bin/grep -q 'review (m1) against' "$T/st/cq/"x__y#7-status~* 2>/dev/null \
  && ok "progress: a progress line before model= never kills review.sh, and the status names the model" || ko "progress: finished=$([ -e "$T/p/finished" ] && echo y || echo n), status '$(head -1 "$T/st/cq/"x__y#7-status~* 2>/dev/null)'"
# 11) #769: the run that coded a child names the coder's model for the review, so review.sh's self-review skip (#762) fires.
#    childreview.sh reads the run record above the worktree: the outcome whose workspace is this worktree names the model,
#    and it goes to $SG_STATE/author-<branch key>. A dispatcher outcome with no workspace is never the author.
rm -f "$T/st/author-"*
printf '%s\n' '{"event": "outcome", "node": "route.router.2", "entry": 1, "item": null, "outcome": "pass", "model": "claude-sonnet-5-5", "workspace": {"name": "worktrees/child", "path": "child"}}' \
  '{"event": "outcome", "node": "router", "entry": 1, "item": null, "outcome": "pass", "model": "claude-opus-5-5"}' > "$T/record.jsonl"
out=$(cd "$T/child" && CLAUDE_CHANGES=1 bash "$T/s/childreview.sh" run-1 7 2>&1)
[ "$(head -1 "$T/st/author-squad_run-1_ws-late" 2>/dev/null)" = claude-sonnet-5-5 ] && ok "author: the coder's model from the run record names the branch author" \
  || ko "author: author file '$(cat "$T/st/author-squad_run-1_ws-late" 2>/dev/null)' $out"
rm -f "$T/record.jsonl"
# 12) #1406: a child with no change of its own has nothing to review. childreview.sh exits 6 before review.sh and any model
#     call: a child at the wave tip, and a child rebased onto a newer dev with no commit of its own. No review record is kept.
for kind in tip rebased; do
  b=squad/run-1/ws-nochange-$kind; k=$(printf '%s' "$b" | tr / _); rm -f "$T/st/review-$k".* "$T/prompt"; : > "$T/gh.log"
  if [ $kind = tip ]; then (cd "$T/child" && $G fetch -q origin && $G checkout -q -b "$b" origin/wave/run-1)
  else (cd "$T/child" && $G fetch -q origin && $G checkout -q -b "$b" origin/dev); fi
  out=$(cd "$T/child" && CLAUDE_CHANGES=1 bash "$T/s/childreview.sh" run-1 7 2>&1); rc=$?
  [ $rc = 6 ] && ok "nochange $kind: childreview exits 6" || ko "nochange $kind: exit $rc: $out"
  case "$out" in *"nothing to review (#7)"*) ok "nochange $kind: the line says nothing to review";; *) ko "nochange $kind: '$out'";; esac
  [ ! -e "$T/prompt" ] && ok "nochange $kind: no model call" || ko "nochange $kind: a model was called"
  ! ls "$T/st/review-$k".* >/dev/null 2>&1 && ok "nochange $kind: no review record" || ko "nochange $kind: $(ls "$T/st" | /usr/bin/grep "review-$k")"
done
# 13) #2310: a pushed branch whose commits the base already holds by patch has no work of its own. The review refuses it
#     in the shape every reader of review-<key>.md already reads -- VERDICT: CHANGES with a Blocking finding, never a third
#     verdict token -- names the reason on the issue and calls no model: a model would approve an empty branch, and the
#     frontier would skip the issue while that branch stayed open.
$G init -q --bare "$T/rev-remote.git"
$G clone -q "$T/rev-remote.git" "$T/rev-work" 2>/dev/null
(cd "$T/rev-work" && echo b > bb && $G add bb && $G -c user.email=a@b -c user.name=t commit -q -m base && $G push -q origin HEAD:dev \
  && $G checkout -q -b gui/8-x && echo y > y8 && $G add y8 && $G -c user.email=a@b -c user.name=t commit -q -m "work 8" -m "Refs #8" && $G push -q origin gui/8-x)
s8=$($G -C "$T/rev-work" rev-parse gui/8-x)
(cd "$T/rev-work" && $G checkout -q dev && $G -c user.email=a@b -c user.name=t cherry-pick "$s8" >/dev/null 2>&1 && $G push -q origin dev)
$G clone -q "$T/rev-remote.git" "$T/rev-checkout" 2>/dev/null
echo "$s8" > "$T/head"; rm -f "$T/st/review-gui_8-x".* "$T/st/cq/"* "$T/prompt"
out=$(SG_CHECKOUT="$T/rev-checkout" SG_LAND_REMOTE="$T/rev-remote.git" bash "$T/s/branchreview.sh" gui/8-x 8 2>&1)
case "$out" in "gui/8-x ${s8:0:9} VERDICT: CHANGES") ok "by-patch: the review refuses a branch with no work of its own";; *) ko "by-patch: '$out'";; esac
/usr/bin/grep -q "by patch" "$T/st/review-gui_8-x.md" 2>/dev/null && ok "by-patch: the saved review names why the branch is refused" || ko "by-patch: no reason in the review: $(cat "$T/st/review-gui_8-x.md" 2>/dev/null)"
[ "$(/usr/bin/head -1 "$T/st/review-gui_8-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && ok "by-patch: the refusal is the verdict every reader already knows" || ko "by-patch: verdict '$(/usr/bin/head -1 "$T/st/review-gui_8-x.md" 2>/dev/null)'"
# the landing pass reads the saved file through one parser: a refusal must land there as `changes`, never as a malformed
# file (a third verdict token) or an approval (a Blocking finding demoted to Debt).
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_8-x.md" "$T/st/review-gui_8-x.sha" "$s8" 2>/dev/null)" = changes ] \
  && ok "by-patch: the parser the landing pass reads classifies the refusal as changes" || ko "by-patch: classify '$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_8-x.md" "$T/st/review-gui_8-x.sha" "$s8" 2>&1)'"
/usr/bin/grep -q "by patch" "$T/st/cq/"x__y#8-status~* 2>/dev/null && ok "by-patch: the issue reads why the branch is refused" || ko "by-patch: no reason on the issue: $(cat "$T/st/cq/"* 2>/dev/null)"
[ ! -e "$T/prompt" ] && ok "by-patch: no model call" || ko "by-patch: a model was called"
[ "$(cat "$T/st/review-gui_8-x.sha" 2>/dev/null)" = "$s8" ] && ok "by-patch: the refused tip is the tip kept beside the verdict" || ko "by-patch: no sha kept"
# 14) #2310: a read that fails proves nothing. When the by-patch read cannot reach its remote, the review runs as it did
#     before and keeps its own verdict: a transient failure is no accusation that the branch carries no work.
echo aaa111 > "$T/head"; rm -f "$T/st/review-gui_7-x".* "$T/st/cq/"* "$T/prompt"; : > "$T/gh.log"
out=$(SG_CHECKOUT="$T/no-such-checkout" SG_LAND_REMOTE="$T/rev-remote.git" bash "$T/s/branchreview.sh" gui/7-x 7 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *"VERDICT: APPROVE"*) ok "unreadable: a by-patch read that cannot run leaves the normal review in charge";; *) ko "unreadable: '$out'";; esac \
  || ko "unreadable: exit $rc: '$out'"
[ -e "$T/prompt" ] && ok "unreadable: the model still reviews the branch" || ko "unreadable: no model call"
rm -rf "$T"; exit $fail
