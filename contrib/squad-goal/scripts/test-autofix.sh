#!/bin/bash
# Regression test for autofix.sh's [conflict] text with stubbed gh (no network, no model). usage: bash test-autofix.sh [path/to/autofix.sh]
# 1) a PR based on main: rebase onto origin/main (unchanged).  2) a PR stacked on an OPEN parent (base != main): rebase onto the parent's branch, never onto main.
src=${1:-$(dirname "$0")/autofix.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/autofix.sh"; : > "$T/s/mkreq.sh"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *'.head.ref+" "'*) echo "feat/child aaaaaaaaaaaa dirty${GH_BASE:+ $GH_BASE}" ;;
  *graphql*) echo 0 ;;
  *issues/*) echo "${GH_ISSUE:-open - Some bug}" ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { AF_DRY=1 bash "$T/s/autofix.sh" 9 8 2>&1 | /usr/bin/grep '^\[conflict\]'; }
unset GH_BASE; r=$(run); case "$r" in *"Rebase onto origin/main"*) ok "main base: rebase onto origin/main";; *) ko "main base: '$r'";; esac
export GH_BASE=feat/parent; r=$(run)
case "$r" in *"Rebase onto origin/feat/parent"*) ok "stacked on an open parent: rebase onto the parent's branch";; *) ko "stacked: '$r'";; esac
case "$r" in *"onto origin/main"*) ko "stacked: still mentions rebasing onto main";; *) ok "stacked: no rebase onto main";; esac
case "$r" in *"only this PR's own commits (message says Refs #8"*) ok "stacked: replays only the PR's own commits";; *) ko "stacked: no own-commits hint";; esac
# 3) a review round hands EVERY finding of the review to the child: the verdict that armed the round, and each bullet under it.
# The fork-only scope note left with the local land route (#115); what remains is the review the child has to answer.
echo "#9 aaaaaaaaa clean CI=success:1 VERDICT: CHANGES" > "$T/st/revci-9.out"; printf 'VERDICT: CHANGES\n1. rename the local helper\n' > "$T/st/review-9.md"; unset GH_BASE
r=$(AF_DRY=1 bash "$T/s/autofix.sh" 9 8 2>&1)
case "$r" in *"VERDICT: CHANGES"*) ok "review round: the request carries the verdict that armed the round";; *) ko "review round: no verdict in the request";; esac
case "$r" in *"rename the local helper"*) ok "review round: the request carries the review's own findings";; *) ko "review round: no finding in the request: '$r'";; esac
# 4) an issue closed as a duplicate / not planned: no fix run (janitor rule I would kill it and retryfix would re-arm it forever)
for v in "closed not_planned Some bug" "closed completed [duplicate of #1] Some bug"; do
  r=$(GH_ISSUE="$v" AF_DRY=1 bash "$T/s/autofix.sh" 9 8 2>&1); case "$r" in "AUTOFIX SKIPPED #9"*) ok "dup issue ($v): skipped";; *) ko "dup issue ($v): '$r'";; esac; done
GH_ISSUE="closed completed Some bug" bash "$T/s/autofix.sh" --dupissue 8 && ko "completed issue counted as a duplicate" || ok "completed issue: not a duplicate"
# 5) (#1770) a dispatch mkreq.sh refuses must not keep the claim autopilot.sh took for the head. autopilot.sh touches
# auto/queued-<pr>-<sha9> BEFORE calling this script and skips the head while that marker exists, and this script charges
# the round before it builds the request: a refusal left the head queued forever with its round spent and nothing working
# it (#1706 @ 27aaaf5b5 kept a BLOCKING verdict, green CI and no fix run until a human pushed). The refusal frees what it
# wrote and says so with its own exit code, so a round that was never spent is not charged (#1529 for autobug/autofrontier).
export GH_ISSUE="open - Some bug"; M="$T/st/auto"; mkdir -p "$M" "$T/co"
printf 'export SG_REPO=x/y SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s\n' "$T/st" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\necho "refusing: request $1 names tests/a.py, which open PR #7 already changes." >&2; exit 1\n' > "$T/s/mkreq.sh"
printf 'open("%s/launched","w")\nprint("run-4242")\n' "$T" > "$T/s/launch.py"; chmod +x "$T/s/mkreq.sh"
echo "#9 aaaaaaaaa clean CI=success:1 VERDICT: CHANGES" > "$T/st/revci-9.out"; printf 'VERDICT: CHANGES\n1. fix it\n' > "$T/st/review-9.md"
touch "$M/queued-9-aaaaaaaaa"
r=$(bash "$T/s/autofix.sh" 9 8 2>&1); rc=$?
[ -e "$M/queued-9-aaaaaaaaa" ] && ko "5a a refused dispatch left auto/queued-9-aaaaaaaaa: the next pass skips the head" || ok "5a a refused dispatch frees the queued marker"
[ -e "$M/fix-9-aaaaaaaaa" ] && ko "5a a refused dispatch left auto/fix-9-aaaaaaaaa behind" || ok "5a a refused dispatch frees the fix marker too"
[ -s "$M/rounds-9" ] && ko "5a a refused dispatch charged a round ($(cat "$M/rounds-9"))" || ok "5a a refused dispatch returns the round"
[ -e "$T/launched" ] && ko "5a a refused dispatch launched a run" || ok "5a a refused dispatch launches no run"
[ "$rc" = 75 ] && ok "5a a refused dispatch exits 75 (distinct from a failed run)" || ko "5a refused dispatch exited $rc, not 75: '$r'"
# 6) the successful dispatch keeps the claim and the round (the marker is what stops a second fix run on the same head)
printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "$SG_STATE/req/$1.txt"\n' > "$T/s/mkreq.sh"; chmod +x "$T/s/mkreq.sh"
rm -f "$M/fix-9-aaaaaaaaa" "$M/rounds-9"; touch "$M/queued-9-aaaaaaaaa"
r=$(bash "$T/s/autofix.sh" 9 8 2>&1); rc=$?
[ -e "$M/queued-9-aaaaaaaaa" ] && ok "6 a dispatched fix keeps the queued marker" || ko "6 a dispatched fix dropped its own claim: '$r'"
[ "$(cat "$M/rounds-9" 2>/dev/null)" = aaaaaaaaa ] && ok "6 a dispatched fix charges its round" || ko "6 a dispatched fix charged no round ($(cat "$M/rounds-9" 2>/dev/null))"
[ "$rc" = 0 ] && ok "6 a dispatched fix exits 0" || ko "6 a dispatched fix exited $rc: '$r'"
case "$r" in *"run-4242"*) ok "6 the run id is reported";; *) ko "6 no run id in '$r'";; esac
# 7) #1777: the reply the fix run posts is ONE comment in the review's checklist shape -- one bullet per finding,
# fixed|not fixed with the one-line reason and the check that proves it, naming the thread it answers, ending with Refs #N
echo "#9 aaaaaaaaa clean CI=success:1 VERDICT: CHANGES" > "$T/st/revci-9.out"
printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$T/st/review-9.md"
r=$(AF_DRY=1 bash "$T/s/autofix.sh" 9 8 2>&1)
miss=""; for needle in 'ONE PR comment' '## Blocking' '## Debt' 'fixed|not fixed' 'the check that proves it' 'the thread it answers' 'Refs #8'; do
  case "$r" in *"$needle"*) ;; *) miss="$miss '$needle'";; esac; done
[ -z "$miss" ] && ok "the reply is one comment in the checklist shape, ending with Refs #N" || ko "the reply instruction lacks$miss"
case "$r" in *"every finding"*) ok "the review finding is addressed as a bullet, not a numbered line";; *) ko "the review finding is not addressed as a bullet";; esac

# 8) #1820: autofix.sh takes the PR's own repository as its first argument -- the fix run is told which repository it
# fixes and which one it pushes to, and the reads (head, threads, check runs) go there. No argument: the profile's one.
export GHLOG="$T/ghlog"; : > "$GHLOG"; unset GH_BASE
r=$(AF_DRY=1 bash "$T/s/autofix.sh" o/tool 9 8 2>&1)
case "$r" in *"repo o/tool"*) ok "the request names the PR's own repository";; *) ko "the request does not name o/tool: $(printf '%s' "$r" | head -2)";; esac
case "$r" in *"repo x/y"*) ko "the request still names the profile's repository";; *) ok "the request names no other repository";; esac
/usr/bin/grep -q 'repos/o/tool/pulls/9' "$GHLOG" && ok "the reads go to the PR's own repository" || ko "no read of o/tool: $(head -3 "$GHLOG")"
/usr/bin/grep -q 'repos/x/y/' "$GHLOG" && ko "a read stayed on the profile's repository ($(/usr/bin/grep -m1 'repos/x/y/' "$GHLOG"))" || ok "nothing read the profile's repository"
: > "$GHLOG"; bash "$T/s/autofix.sh" o/tool --threads 9 >/dev/null 2>&1
/usr/bin/grep -q 'owner:"o"' "$GHLOG" && /usr/bin/grep -q 'name:"tool"' "$GHLOG" && ok "the thread read asks the PR's own repository" || ko "thread read: $(cat "$GHLOG")"
: > "$GHLOG"; AF_DRY=1 bash "$T/s/autofix.sh" 9 8 >/dev/null 2>&1
/usr/bin/grep -q 'repos/x/y/pulls/9' "$GHLOG" && ok "with no argument the profile's repository is the one read" || ko "default repo: $(head -3 "$GHLOG")"

rm -rf "$T"; exit $fail
