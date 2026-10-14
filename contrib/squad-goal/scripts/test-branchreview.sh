#!/bin/bash
# Regression test for the branch review (#357): review.sh --branch, branchreview.sh and branchfix.sh, with stubbed gh and claude
# (no network, no model). The review reads the diff of a work branch against `dev`, never a pull request; its findings go to the
# one status comment of the issue (a queue file of the status kind, which cqflush.sh edits in place); a fix round works on the
# same branch. usage: bash test-branchreview.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$d/review.sh" "$d/revformat.py" "$d/address.sh" "$T/s/"; : > "$T/s/codex-reset.py"
cp "$d/branchreview.sh" "$d/branchfix.sh" "$T/s/" 2>/dev/null
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}\n' > "$T/s/models.json"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_LAND_BRANCH=dev; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md; SG_CODING_RULES=CLAUDE.md
. "$T/s/address.sh"
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "$GHLOG"
case "$*" in
  *commits/gui/7-x*) cat "$GHHEAD";;
  *compare/dev...gui/7-x*'.files'*) echo UI/a.cs;;
  *compare/dev...gui/7-x*) printf 'diff --git a/f b/f\n+branch-diff-line\n';;
  *compare*'.status'*) echo ahead;;
  *compare*) printf 'diff --git a/f b/f\n+delta-line\n';;
  *issues/7*) echo '#7 title';;
  *) echo stub;;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "$CLAUDE_PROMPT"; if [ -n "$CLAUDE_CHANGES" ]; then printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n'; else echo "VERDICT: APPROVE"; fi
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH" GHLOG="$T/gh.log" GHHEAD="$T/head" CLAUDE_PROMPT="$T/prompt"
echo aaa111 > "$T/head"; key=gui_7-x
# 1) a branch with no pull request: the diff is read against dev and no pull request is touched
out=$(CLAUDE_CHANGES=1 bash "$T/s/branchreview.sh" gui/7-x 7 2>&1); rc=$?
[ $rc = 0 ] && ok "the review runs on a branch with no pull request" || ko "branchreview exited $rc: $out"
/usr/bin/grep -q "compare/dev...gui/7-x" "$T/gh.log" && /usr/bin/grep -q "branch-diff-line" "$T/prompt" && ok "the prompt carries the diff of the branch against dev" || ko "no dev diff in the prompt"
! /usr/bin/grep -q "pulls" "$T/gh.log" && ok "no pull request is read" || ko "a pull request call: $(/usr/bin/grep pulls "$T/gh.log")"
head -1 "$T/st/review-$key.md" | /usr/bin/grep -q CHANGES && [ "$(cat "$T/st/review-$key.sha")" = aaa111 ] && ok "the verdict and the reviewed sha are kept under the branch key" || ko "no verdict kept"
# 2) the findings go to the status comment of the issue: a status-kind queue file, never a plain comment
n1=$(ls "$T/st/cq/" | wc -l | tr -d ' '); ls "$T/st/cq/"x__y#7-status~* >/dev/null 2>&1 && ok "the findings are queued as the issue's status comment" || ko "no status file: $(ls "$T/st/cq")"
/usr/bin/grep -q "a.cs:1" "$T/st/cq/"x__y#7-status~* && ok "the status comment carries the findings" || ko "findings missing from the status comment"
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
case "$r" in *"Check out origin/gui/7-x, commit on top, push to the SAME branch (git push origin HEAD:gui/7-x); no pull request"*) ok "the fix round commits on the work branch";; *) ko "fix request: '$r'";; esac
case "$r" in *"a.cs:1"*) ok "the fix request carries the findings";; *) ko "no findings in the fix request";; esac
rm -rf "$T"; exit $fail
