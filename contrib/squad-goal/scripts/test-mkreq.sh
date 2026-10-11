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
printf 'export SG_REPO=o/r SG_FORK_DIR=%s SG_FORK_BASE=local/next\n' "$FK" > "$S/env.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
case "$*" in *"/files"*) echo scripts/shared.py;; *pulls\?state=open*) echo 7;; esac
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
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
