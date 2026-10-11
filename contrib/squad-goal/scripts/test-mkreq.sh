#!/bin/bash
# Regression test for mkreq.sh's dispatch-time slice gate (ADR-0274, Refs #1400, #1519). usage: bash test-mkreq.sh
# Offline: gh is a stub that reports one open PR touching scripts/shared.py, HOME is a throwaway directory, and the fork is a
# throwaway git repo holding one unmerged gui/* branch over contrib/squad-goal/scripts/hub.py and one over dashboard/page.py.
# The report is buffered so the first line says TDD RED while a fix is missing, TDD GREEN once it is in.
d=$(dirname "$0"); T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
# Hermetic (#1410, the rule of shell-suites.sh): every inherited SG_* is cleared, so the live loop's fork, repo and state can
# never reach an assertion -- SG_FORK_DIR above all, which selects the fork checkout the guard reads the gui/* branches from.
unset $(compgen -e | /usr/bin/grep '^SG_') 2>/dev/null || true
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
S=$T/scripts; mkdir -p "$S" "$T/bin" "$T/home"
cp "$d/mkreq.sh" "$d/models.json" "$d/req-checklist.txt" "$d/req-checklist-fork.txt" "$S/"
# The fork checkout (#1519): gui/1497 is unmerged over the fork's own tree, gui/1498 over dashboard/, gui/1400 is already on
# local/next -- a merged slice is not in flight and must never refuse.
FK=$T/fork; /usr/bin/git init -q -b local/next "$FK"; mkdir -p "$FK/contrib/squad-goal/scripts" "$FK/dashboard" "$FK/scripts"
echo old > "$FK/contrib/squad-goal/scripts/hub.py"; echo old > "$FK/dashboard/page.py"
(cd "$FK" && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m base)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1497 && echo x >> contrib/squad-goal/scripts/hub.py && /usr/bin/git -c user.email=a@b -c user.name=n commit -qam hub && /usr/bin/git checkout -q local/next)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1498 && echo x >> dashboard/page.py && /usr/bin/git -c user.email=a@b -c user.name=n commit -qam page && /usr/bin/git checkout -q local/next)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1400 && echo m > scripts/merged.py && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m merged && /usr/bin/git checkout -q local/next && /usr/bin/git merge -q --ff-only gui/1400)
# (#1794) gui/1510-mine's issue #1510 is CLOSED: it can never land, so it claims no slice. gui/1780's issue is open and its
# branch still refuses. gui/1791's issue is the one GitHub cannot be asked about (gh exits 1) -- that branch stays in flight.
(cd "$FK" && /usr/bin/git checkout -q -b gui/1510-mine && echo x > contrib/squad-goal/scripts/hub2.py && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m hub2 && /usr/bin/git checkout -q local/next)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1780 && echo x > scripts/open1780.py && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m open1780 && /usr/bin/git checkout -q local/next)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1791 && echo x > scripts/unreachable1791.py && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m unreachable1791 && /usr/bin/git checkout -q local/next)
# (#1786) gui/1767 and gui/1768 both edit contrib/squad-goal/scripts/autopilot.sh and neither landed: that pair is what
# deadlocked forkclose. for-each-ref lists gui/1767 first, so gui/1767 is the branch a refusal over that file always cites --
# including the refusal of the rebase run for gui/1768, which repairs the other branch.
(cd "$FK" && /usr/bin/git checkout -q -b gui/1767 && echo x > contrib/squad-goal/scripts/autopilot.sh && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m a1767 && /usr/bin/git checkout -q local/next)
(cd "$FK" && /usr/bin/git checkout -q -b gui/1768 && printf 'x\ny\n' > contrib/squad-goal/scripts/autopilot.sh && /usr/bin/git add -A && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m a1768 && /usr/bin/git checkout -q local/next)
printf 'export SG_REPO=o/r SG_FORK_DIR=%s SG_FORK_BASE=local/next\n' "$FK" > "$S/env.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# gh issue view <N> --repo o/r --json state -q .state is the only issue question asked (#1794). #1510 is CLOSED; #1791 stands
# for a GitHub that cannot be reached -- no output and a non-zero exit -- which must leave its branch in flight, not drop it. An
# issue the stub says nothing about answers with an empty state, which reads as in-flight too: the safe half of the same rule.
case "$*" in
  *"/files"*) echo scripts/shared.py;;
  *pulls\?state=open*) echo 7;;
  *"issue view 1497"*) echo OPEN;;
  *"issue view 1498"*) echo OPEN;;
  *"issue view 1510"*) echo CLOSED;;
  *"issue view 1780"*) echo OPEN;;
  *"issue view 1767"*) echo OPEN;;
  *"issue view 1768"*) echo OPEN;;
  *"issue view 1791"*) exit 1;;
esac
GH
chmod +x "$T/bin/gh"
export HOME=$T/home PATH="$T/bin:$PATH"; R=$HOME/.cache/squad-goal/req
body() { printf 'IMPORTANT: your first and only action is X\nPANEL: %s\nEdit %s\n' "$1" "$2" > "$T/$3.body"; }
run() { bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }
runf() { SG_SLICE_FORCE=1 bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }
# 1. a same-panel overlap refuses, names both requests and the shared path, and writes no request
body p1 scripts/a.py a; body p1 scripts/a.py b
run a || ko "first sibling refused"; [ -f "$R/a.txt" ] && ok "first sibling builds" || ko "no request for a"
run b && ko "same-panel overlap launched" || ok "same-panel overlap refuses"
/usr/bin/grep -q 'request b and request a' "$T/b.err" && /usr/bin/grep -q 'scripts/a.py' "$T/b.err" && ok "refusal names both requests and the path" || ko "refusal text: $(cat "$T/b.err")"
[ -f "$R/b.txt" ] && ko "a refused request left a file" || ok "refused request wrote no file"
# 2. a disjoint panel launches
body p2 scripts/c.py c; run c && ok "disjoint panel launches" || ko "disjoint refused: $(cat "$T/c.err")"
# 3. an overlap with an open PR is refused, not warned about (#1519): it names the PR and the path and writes nothing
body p3 scripts/shared.py e; run e && ko "open-PR overlap launched" || ok "open-PR overlap refuses"
/usr/bin/grep -q 'PR #7' "$T/e.err" && /usr/bin/grep -q 'scripts/shared.py' "$T/e.err" && ok "open-PR refusal names the PR and the path" || ko "open-PR refusal text: $(cat "$T/e.err")"
/usr/bin/grep -q 'SG_SLICE_FORCE=1' "$T/e.err" && ok "refusal names the SG_SLICE_FORCE=1 override" || ko "refusal hides the override: $(cat "$T/e.err")"
[ -f "$R/e.txt" ] && ko "open-PR refusal left a file" || ok "open-PR refusal wrote no file"
runf e && [ -f "$R/e.txt" ] && ok "SG_SLICE_FORCE=1 dispatches the same body" || ko "override refused: $(cat "$T/e.err")"
/usr/bin/grep -q 'SLICE FORCE' "$T/e.err" && ok "a forced overlap is announced on stderr" || ko "forced overlap silent: $(cat "$T/e.err")"
# 4. a queued request of ANOTHER panel refuses too: the rule is not panel-scoped (ADR-0274 is its narrower half)
body p4 scripts/a.py f; run f && ko "another panel's pending request launched" || ok "a pending request of another panel refuses"
/usr/bin/grep -q 'request f and request a' "$T/f.err" && ok "cross-panel refusal names the holder request" || ko "cross-panel text: $(cat "$T/f.err")"
[ -f "$R/f.txt" ] && ko "cross-panel refusal left a file" || ok "cross-panel refusal wrote no file"
# 5. the fork's own tree: an unmerged gui/* branch that touches the file refuses, naming the branch (#1497/#1498/#1502 landed
# three branches on contrib/squad-goal/scripts/hub.py because every guard was blind to it)
printf 'IMPORTANT: your first and only action is X\nEdit contrib/squad-goal/scripts/hub.py\n' > "$T/h.body"
run h && ko "unmerged gui/* slice launched" || ok "an unmerged gui/* branch's file refuses"
/usr/bin/grep -q 'gui/1497' "$T/h.err" && /usr/bin/grep -q 'contrib/squad-goal/scripts/hub.py' "$T/h.err" && ok "fork refusal names the branch and the file" || ko "fork refusal text: $(cat "$T/h.err")"
[ -f "$R/h.txt" ] && ko "fork refusal left a file" || ok "fork refusal wrote no file"
# 5b. dashboard/... matches like UI/...: the old regex knew only UI|UI.<x>|Core|scripts|docs|tests
printf 'IMPORTANT: your first and only action is X\nEdit dashboard/page.py\n' > "$T/i.body"
run i && ko "a dashboard/ path in flight launched" || ok "dashboard/... matches the path regex"
/usr/bin/grep -q 'gui/1498' "$T/i.err" && ok "dashboard refusal names its branch" || ko "dashboard text: $(cat "$T/i.err")"
# 5c. a branch already merged into the base is not in flight
printf 'IMPORTANT: your first and only action is X\nEdit scripts/merged.py\n' > "$T/j.body"
run j && [ -f "$R/j.txt" ] && ok "a merged gui/* branch does not refuse" || ko "merged branch refused: $(cat "$T/j.err")"
# 5d. the accepted half: nothing holds this file, so the request is built
printf 'IMPORTANT: your first and only action is X\nEdit dashboard/free.py\n' > "$T/k.body"
run k && [ -f "$R/k.txt" ] && ok "an unclaimed fork-tree body launches" || ko "accepted case refused: $(cat "$T/k.err")"
# 6. fork-only checklist + emitted request tell the child to commit incrementally (Refs #1505)
printf 'IMPORTANT: your first and only action is X\nFork-only work: no PR.\nEdit scripts/x.py\n' > "$T/fork.body"
run fork && [ -f "$R/fork.txt" ] && ok "fork-only request builds" || ko "fork-only request: $(cat "$T/fork.err" 2>/dev/null)"
G=/usr/bin/grep
for w in "commit early and often" "as soon as a slice compiles or its test runs" "commit again after each further step" "squash"; do
  $G -qF -- "$w" "$S/req-checklist-fork.txt" && $G -qF -- "$w" "$R/fork.txt" && ok "fork request carries '$w'" || ko "fork request lacks '$w'"
done
# 7. a child that follows that instruction then dies at the spend cap leaves git log <base>..gui/<N> non-empty
F=$T/cap; /usr/bin/git init -q -b local/next "$F"
(cd "$F" && /usr/bin/git -c user.email=a@b -c user.name=n commit -q --allow-empty -m base \
  && /usr/bin/git checkout -q -b gui/42)
echo work > "$F/x.py"
if $G -qF -- "commit early and often" "$R/fork.txt" && $G -qF -- "as soon as a slice compiles or its test runs" "$R/fork.txt" && $G -qF -- "commit again after each further step" "$R/fork.txt"; then
  (cd "$F" && /usr/bin/git add x.py && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m slice)
fi
n=$(/usr/bin/git -C "$F" log --oneline local/next..gui/42 | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "cap-death: git log local/next..gui/42 is non-empty" || ko "cap-death: gui/42 empty after spend-cap death"
# 8. the request of a PR-bound child carries the literal `Refs #<N>` line it has to paste into the PR body (Refs #1507):
# a PR with no such line is skipped by the autopilot, so the child has to be handed the line, not only the rule.
body p7 scripts/z.py g; run g && [ -f "$R/g.txt" ] && ok "PR request builds" || ko "PR request: $(cat "$T/g.err" 2>/dev/null)"
$G -qF -- 'Refs #<N>' "$R/g.txt" && ok "PR request carries the literal Refs #<N> line" || ko "PR request lacks the literal Refs #<N> line"
# 9. (#1529) a request whose work is over is history, not in flight. Request files are never deleted, so reading every one of
# them as in flight made the guard refuse forever, naming runs that ended days ago (auto-fix-1203-3fbfa13ca, PR #1203 closed
# 2026-10-10): the in-flight set only grew. The run folder is what says whether a request's work is still going -- launch.py
# copies the request into runs/<run>/request.txt and record.jsonl's verdict ends it.
mkdir -p "$T/co/runs" "$T/home/.cache/squad-goal"
fin() { printf 'IMPORTANT: your first and only action is X\nPANEL: p9\nEdit %s\n' "$1" > "$R/$2.txt"; touch -t 202610010000 "$R/$2.txt"; }
# 9a. an old request whose run recorded a verdict is history: it must not refuse a slice naming the same file
fin scripts/gone.py dead-req; mkdir -p "$T/co/runs/run-9001"
cp "$R/dead-req.txt" "$T/co/runs/run-9001/request.txt"; echo '{"event": "verdict"}' > "$T/co/runs/run-9001/record.jsonl"
echo "$T/co/runs/run-9001" >> "$T/home/.cache/squad-goal/active-runs.txt"
body p9 scripts/gone.py m
run m && [ -f "$R/m.txt" ] && ok "a finished run's request does not refuse" || ko "finished run's request still refuses: $(cat "$T/m.err")"
# 9b. a request no run ever carried is in flight only inside the launch window, then it is history too
fin scripts/abandoned.py lost-req
body p9 scripts/abandoned.py o
run o && [ -f "$R/o.txt" ] && ok "an old request with no run behind it does not refuse" || ko "abandoned request still refuses: $(cat "$T/o.err")"
# 9c. the accepted half of the same rule: the run is live (no verdict yet), so a slice naming its file still refuses
fin scripts/live.py live-req; mkdir -p "$T/co/runs/run-9002"
cp "$R/live-req.txt" "$T/co/runs/run-9002/request.txt"; echo '{"event": "spawn_accepted"}' > "$T/co/runs/run-9002/record.jsonl"
echo "$T/co/runs/run-9002" >> "$T/home/.cache/squad-goal/active-runs.txt"
printf 'IMPORTANT: your first and only action is X\nEdit scripts/live.py\n' > "$T/n.body"
run n && ko "a live run's request launched" || ok "a live run's request still refuses"
$G -q 'request live-req' "$T/n.err" && $G -q 'scripts/live.py' "$T/n.err" && ok "the refusal names the live request and the path" || ko "live refusal text: $(cat "$T/n.err")"
[ -f "$R/n.txt" ] && ko "the live refusal left a file" || ok "the live refusal wrote no file"
# 9d. a run that recorded a verdict seconds ago is history at once: age alone must not decide it (the request file is fresh)
printf 'IMPORTANT: your first and only action is X\nPANEL: p9\nEdit scripts/justdone.py\n' > "$R/just-req.txt"; mkdir -p "$T/co/runs/run-9003"
cp "$R/just-req.txt" "$T/co/runs/run-9003/request.txt"; echo '{"event": "verdict"}' > "$T/co/runs/run-9003/record.jsonl"
echo "$T/co/runs/run-9003" >> "$T/home/.cache/squad-goal/active-runs.txt"
body p9 scripts/justdone.py p
run p && [ -f "$R/p.txt" ] && ok "a verdict makes the request history at once" || ko "fresh request of a finished run refuses: $(cat "$T/p.err")"
# 10. (#1769) a FIX RUN is not a second slice. autofix.sh names its request `auto-fix-<pr>-<sha9>` and the body lists the files
# that PR's own review asked to change -- which the open PR already changes, so the guard refused the launch of the only run
# that could repair it. PR #1706 (2026-10-11): after a BLOCKING review on 27aaaf5b5, `refusing: request auto-fix-1706-27aaaf5b5
# names scripts/checks/verify_report_bug_prefix.sh, which open PR #1706 already changes`; autofix.sh then exited 1 and the
# verdict sat on a mergeable, CI-green PR with nothing working it. ADR-0274 refuses an overlap between two SLICES; a slice and
# its own repair are one unit of work, and the claim record already carries the number the request name carries.
body p10 scripts/shared.py auto-fix-7-27aaaf5b5
run auto-fix-7-27aaaf5b5 && [ -f "$R/auto-fix-7-27aaaf5b5.txt" ] && ok "a fix run for the PR holding the file launches" || ko "fix run for its own PR refused: $(cat "$T/auto-fix-7-27aaaf5b5.err")"
/usr/bin/grep -q 'refusing' "$T/auto-fix-7-27aaaf5b5.err" && ko "the fix run for its own PR was still refused" || ok "the fix run on its own PR prints no refusal"
# 10b. the cross-slice rule is intact: the same request shape naming a file a DIFFERENT open PR changes is refused as before
body p10 scripts/shared.py auto-fix-8-27aaaf5b5
run auto-fix-8-27aaaf5b5 && ko "an auto-fix request naming another PR's file launched" || ok "an auto-fix request for another PR still refuses"
/usr/bin/grep -q 'open PR #7' "$T/auto-fix-8-27aaaf5b5.err" && /usr/bin/grep -q 'scripts/shared.py' "$T/auto-fix-8-27aaaf5b5.err" && ok "the cross-PR refusal still names the PR and the path" || ko "cross-PR refusal text: $(cat "$T/auto-fix-8-27aaaf5b5.err")"
[ -f "$R/auto-fix-8-27aaaf5b5.txt" ] && ko "the cross-PR refusal left a file" || ok "the cross-PR refusal wrote no file"
# 10c. a plain request (not a fix run) naming the same file of the same open PR is still refused: the exemption is the fix-run
# class, not "the PR mentions this path somewhere"
body p10 scripts/shared.py plain-overlap
run plain-overlap && ko "a plain request on an open PR's file launched" || ok "a plain request on an open PR's file still refuses"
/usr/bin/grep -q 'open PR #7' "$T/plain-overlap.err" && ok "the plain refusal names the PR" || ko "plain refusal text: $(cat "$T/plain-overlap.err")"
# 11. (#1774) a path named only inside quoted or code context is not a slice. A frontier/autobug request embeds the full text of
# every issue it dispatches, so a path quoted as evidence counted as a file the request would edit: issue #1769's body indents
# `refusing: request auto-fix-1706-27aaaf5b5 names scripts/checks/verify_report_bug_prefix.sh` as a code block, PR #1706 was
# open on exactly that file, and every frontier and autobug pass embedding that body was refused -- on 2026-10-11 the whole
# wave:01 panel (#1765-#1770) could not dispatch. Only prose counts: fenced blocks, lines indented by 4+ spaces and blockquote
# lines are dropped before matching. Each claimed path below is named ONLY inside one of those three contexts.
cat > "$T/quo.body" <<'B'
IMPORTANT: your first and only action is X
Edit scripts/quoted-free.py

--- issue #9001
Evidence from an earlier pass:

    refusing: request auto-fix-1706-27aaaf5b5 names scripts/shared.py, which open PR #7 already changes.

> refusing: request frontier-x names contrib/squad-goal/scripts/hub.py, which the unmerged fork branch gui/1497 touches.

```
refusing: request autobug-y names dashboard/page.py, which the unmerged fork branch gui/1498 touches.
```
B
run quo && [ -f "$R/quo.txt" ] && ok "a path only quoted as evidence does not claim a slice" || ko "quoted prose refused the request: $(cat "$T/quo.err")"
$G -q 'refusing' "$T/quo.err" && ko "the quoted-context body was still refused: $(cat "$T/quo.err")" || ok "the quoted-context body prints no refusal"
$G -qF -- 'scripts/quoted-free.py' "$R/quo.txt" && ok "the prose path of the same body is still the slice" || ko "the accepted body lost its prose path"
# 11b. the prose scan is intact: the same three paths named at column 0 refuse exactly as before (test 5 and 5b are that case)
# 11c. (#1790) prose inside an EMBEDDED ISSUE is not a slice either. Every automatic request embeds the full text of the issues
# it dispatches under a `--- issue #N` marker line, so a path named in the plain prose of a quoted body counted as a file the
# request would edit: the pass that dispatched it claimed every path its issues' prose named, the next pass embedding the same
# issues was refused, and the two passes took turns failing while the issue stayed open. The slice a request claims is what its
# OWN instructions name, so the scan stops at the first marker line: the quoted region is what the body tells the child about
# its ticket, not what it asks it to edit.
cat > "$T/issue-prose.body" <<'B'
IMPORTANT: your first and only action is X
PANEL: p11
Edit scripts/issue-prose-free.py

--- issue #9002
An earlier pass was refused because open PR #7 already changes scripts/shared.py, and a sibling had
claimed contrib/squad-goal/scripts/hub.py while gui/1497 touched dashboard/page.py.
B
run issue-prose && [ -f "$R/issue-prose.txt" ] && ok "a path in an embedded issue's prose does not claim a slice" || ko "embedded-issue prose refused the request: $(cat "$T/issue-prose.err")"
$G -q 'refusing' "$T/issue-prose.err" && ko "the embedded-issue prose body was still refused: $(cat "$T/issue-prose.err")" || ok "the embedded-issue prose body prints no refusal"
$G -qF -- 'scripts/issue-prose-free.py' "$R/issue-prose.txt" && ok "the instruction path of the same body is still the slice" || ko "the accepted body lost its instruction path"
# 11d. the instruction part still claims its slice: the same path named ABOVE the first marker refuses exactly as before
cat > "$T/issue-own.body" <<'B'
IMPORTANT: your first and only action is X
PANEL: p11
Edit scripts/shared.py

--- issue #9002
Fix the bug the issue describes.
B
run issue-own && ko "an instruction path already in flight launched" || ok "a path the instructions name still refuses"
$G -q 'open PR #7' "$T/issue-own.err" && $G -q 'scripts/shared.py' "$T/issue-own.err" && ok "the refusal names the PR and the instruction path" || ko "instruction-path refusal text: $(cat "$T/issue-own.err")"
[ -f "$R/issue-own.txt" ] && ko "the instruction-path refusal left a file" || ok "the instruction-path refusal wrote no file"
# 12. (#1786) a rebase of an already-written branch is not a second writer. forkclose.sh names its request
# `forkrebase-<issue>-<sha9>` and its body names the conflicting files, which the branch it repairs holds by definition -- with
# gui/1767 and gui/1768 both editing contrib/squad-goal/scripts/autopilot.sh, the guard refused each rebase run citing the other
# branch, and the only request that could break the deadlock was the one refused. The file of the branch this request repairs is
# exempt, exactly as #1769 exempts a fix run's own PR (#1769): the closes are serialized, not deadlocked.
# 12a. another branch's file still refuses, naming that branch
body p12 contrib/squad-goal/scripts/hub.py forkrebase-1600-abc123456
run forkrebase-1600-abc123456 && ko "a rebase naming another branch's file launched" || ok "a rebase naming another branch's file refuses"
$G -q 'gui/1497' "$T/forkrebase-1600-abc123456.err" && ok "the rebase refusal names the holding branch" || ko "rebase refusal text: $(cat "$T/forkrebase-1600-abc123456.err")"
[ -f "$R/forkrebase-1600-abc123456.txt" ] && ko "the rebase refusal left a file" || ok "the rebase refusal wrote no file"
# 12b. another request's file still refuses too: the exemption is the rebase's own branch, not the request name
body p12 scripts/a.py forkrebase-1497-def456789
run forkrebase-1497-def456789 && ko "a rebase naming a queued request's file launched" || ok "a rebase naming a request's file refuses"
$G -q 'request a' "$T/forkrebase-1497-def456789.err" && ok "the rebase-vs-request refusal names the request" || ko "rebase-vs-request text: $(cat "$T/forkrebase-1497-def456789.err")"
# 12c. the rebase's own branch is exempt: gui/1497 holds the file this request repairs
body p12 contrib/squad-goal/scripts/hub.py forkrebase-1497-abc123456
run forkrebase-1497-abc123456 && [ -f "$R/forkrebase-1497-abc123456.txt" ] && ok "a rebase of the branch holding the file launches" || ko "the rebase of its own branch was refused: $(cat "$T/forkrebase-1497-abc123456.err")"
$G -q 'refusing' "$T/forkrebase-1497-abc123456.err" && ko "the rebase of its own branch was still refused" || ok "the rebase of its own branch prints no refusal"
# 12d. (#1786) TWO unmerged branches on ONE file. gui/1767 and gui/1768 both edit contrib/squad-goal/scripts/autopilot.sh, so
# the rebase request for either one names a file the other also touches, and for-each-ref lists gui/1767 first: the branch a
# refusal cites is then never the branch the request repairs. A rebase takes its own branch's side and merges the base into it --
# it writes nothing the other branch is writing -- so the closes are serialized, not deadlocked, and each rebase is exempt for a
# file its own branch claims even when another branch claims it too.
for rb in 1768 1767; do
  body p12 contrib/squad-goal/scripts/autopilot.sh "forkrebase-$rb-abc123456"
  run "forkrebase-$rb-abc123456" && [ -f "$R/forkrebase-$rb-abc123456.txt" ] && ok "the rebase of gui/$rb, one of two branches on one file, launches" || ko "the rebase of gui/$rb was refused: $(cat "$T/forkrebase-$rb-abc123456.err")"
  $G -q 'refusing' "$T/forkrebase-$rb-abc123456.err" && ko "the rebase of gui/$rb was still refused" || ok "the rebase of gui/$rb prints no refusal"
done
# 13. (#1794) a fork branch whose issue is closed claims no slice. gui/1510-mine (#1510, closed) sat one commit ahead of
# local/next touching contrib/squad-goal/scripts/hub2.py, so the guard refused every request naming that file while five
# startable issues waited and the loop ran zero live runs -- and a closed issue can never land a branch. Such a branch is
# dropped, and named on stderr, because retiring it is the operator's call and nothing else said so.
printf 'IMPORTANT: your first and only action is X\nEdit contrib/squad-goal/scripts/hub2.py\n' > "$T/q.body"
run q && [ -f "$R/q.txt" ] && ok "a closed issue's branch does not refuse" || ko "closed-issue branch refused: $(cat "$T/q.err")"
$G -q 'refusing' "$T/q.err" && ko "the closed-issue body was still refused: $(cat "$T/q.err")" || ok "the closed-issue body prints no refusal"
$G -qF -- 'slice guard: gui/1510-mine skipped, its issue #1510 is closed' "$T/q.err" && ok "the skipped branch is named on stderr" || ko "the skip line is missing: $(cat "$T/q.err")"
# 13b. the accepted half: the same shape over an OPEN issue still refuses, naming its branch and the file, and writes nothing
printf 'IMPORTANT: your first and only action is X\nEdit scripts/open1780.py\n' > "$T/r.body"
run r && ko "an open issue's branch launched" || ok "an open issue's branch still refuses"
$G -q 'gui/1780' "$T/r.err" && $G -q 'scripts/open1780.py' "$T/r.err" && ok "the refusal names the open branch and the file" || ko "open-branch refusal text: $(cat "$T/r.err")"
[ -f "$R/r.txt" ] && ko "the open-branch refusal left a file" || ok "the open-branch refusal wrote no file"
$G -qF -- 'gui/1780 skipped' "$T/r.err" && ko "an open issue's branch was reported as skipped" || ok "an open issue's branch prints no skip line"
# 13c. GitHub unreachable for a branch: today's behaviour, the branch stays in flight -- an API that cannot answer must never
# silently drop a live slice, and it prints no skip line either
printf 'IMPORTANT: your first and only action is X\nEdit scripts/unreachable1791.py\n' > "$T/s.body"
run s && ko "an unanswerable branch's file launched" || ok "a branch GitHub cannot answer for stays in flight"
$G -q 'gui/1791' "$T/s.err" && ok "the unreachable-branch refusal still names the branch" || ko "unreachable refusal text: $(cat "$T/s.err")"
$G -qF -- 'gui/1791 skipped' "$T/s.err" && ko "the unreachable branch was reported as skipped" || ok "the unreachable branch prints no skip line"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
