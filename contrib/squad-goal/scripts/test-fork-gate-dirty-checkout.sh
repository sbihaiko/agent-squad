#!/bin/bash
# Regression test for #99: the landing gate grades the merge result in a tree of its own, never the checkout beside it.
# forkclose.sh runs the gate in $SG_FORK_DIR -- the fork checkout, with the merge already landed there -- so every suite used
# to read that working tree plus whatever the owner or a child had edited and not committed, and the verdict was about the
# checkout instead of the tree that was landing. Three merges were reverted over work no branch carried (Revert "Merge
# gui/72", gui/83, gui/84): the #84 gate log failed two tests/test_hub_machine_load.py and six tests/test_model_fallthrough.py
# tests while gui/84 touched squad/engine.py and two test files, and the base 3b5186d passed both files. So the gate checks
# HEAD out into a detached worktree of its own and runs every suite there, and it reports the checkout's uncommitted paths
# once, by path, as a fact -- never as a verdict. A stub shell suite and a stub pytest command that both read `flag` from
# their own tree, a stub GUI command, a throwaway repository, no emulator. usage: bash test-fork-gate-dirty-checkout.sh
unset "${!SG_@}" FG_SUITES
here="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; repo=$T/repo; export HOME="$T/home" SG_STATE="$T/lk"; mkdir -p "$HOME"
# The three suites the gate runs, each reporting the value of `flag` it was handed: `ok` passes, anything else (a committed
# break, or a committed value with an uncommitted edit appended) fails. The shell suite sits in the repository, so the gate
# finds it in whichever tree it grades; the pytest command is one the gate is handed, as the live loop hands it SG_FORK_PY_CMD.
printf 'printf "%%s\\n" "$(cat flag 2>/dev/null)" >> "$FG_TEST_LOG"\n[ "$(cat flag 2>/dev/null)" = ok ]\n' > "$T/shell-body"
printf '#!/bin/bash\nprintf "%%s\\n" "$(cat flag 2>/dev/null)" >> "$FG_TEST_LOG"\n[ "$(cat flag 2>/dev/null)" = ok ]\n' > "$T/py.sh"; chmod +x "$T/py.sh"
gui() { printf 'printf "%%s\\n" "$(cat flag 2>/dev/null)" >> "$FG_TEST_LOG"; [ "$(cat flag 2>/dev/null)" = ok ]'; }
mk() {   # mk <committed flag> [<uncommitted flag>]: the repository at HEAD, plus the edit left beside it when given
  rm -rf "$repo"; mkdir -p "$repo/contrib/squad-goal/scripts"
  cp "$here/fork-gate.sh" "$here/serial.sh" "$repo/contrib/squad-goal/scripts/"
  { echo '#!/bin/bash'; cat "$T/shell-body"; } > "$repo/contrib/squad-goal/scripts/shell-suites.sh"
  $G init -q -b local/next "$repo"; printf '%s\n' "$1" > "$repo/flag"
  $G -C "$repo" -c user.email=a@b -c user.name=t add -A; $G -C "$repo" -c user.email=a@b -c user.name=t commit -q -m init
  rm -f "$T/log" "$T/out"; [ $# -lt 2 ] || printf '%s\n' "$2" > "$repo/flag"; }
gate() { (cd "$repo" && FG_TEST_LOG="$T/log" FG_SUITES=$1 SG_FORK_PY_CMD="bash $T/py.sh" \
  bash contrib/squad-goal/scripts/fork-gate.sh "${2:-true}" > "$T/out" 2>&1); }
dirty_fact() { grep -c -- '^=== fork gate: NOTE .*flag' "$T/out"; }

# 1. the reported bug: a clean HEAD (flag ok) with an uncommitted edit beside it (flag ok, broken). The gate grades HEAD, so it
# is green and its suite is handed the committed value -- before #99 the suite read the edit and the landing was reverted.
mk ok $'ok\nbroken'
gate shell; rc=$?
[ $rc = 0 ] && ok "an uncommitted edit that breaks a suite does not turn the gate red" || ko "the checkout's edit decided the verdict (exit $rc): $(cat "$T/log")"
[ "$(cat "$T/log")" = ok ] && ok "the shell suite was handed the committed value, never the edit" || ko "the suite read $(cat "$T/log")"
[ "$(dirty_fact)" = 1 ] && ok "the checkout's uncommitted path is reported once, by path" || ko "the report is not one line, by path: $(grep 'fork gate: NOTE' "$T/out")"

# 2. the mirror image: HEAD is broken (flag broken) while the edit beside it reads ok. The gate must stay red -- a fix that
# grades something other than the landing (an empty tree, the checkout, a constant) passes case 1 and fails here.
mk broken ok
gate shell; rc=$?
[ $rc != 0 ] && ok "a break committed at HEAD still reds the gate" || ko "the gate graded the edit instead of HEAD (exit $rc)"
[ "$(dirty_fact)" = 1 ] && ok "the dirty checkout beside that red gate is still reported, by path" || ko "no report line: $(grep 'fork gate: NOTE' "$T/out")"

# 3. the pytest half of the reported failure (#84: tests/test_hub_machine_load.py, tests/test_model_fallthrough.py)
mk ok $'ok\nbroken'
gate python; rc=$?
[ $rc = 0 ] && ok "the pytest suite is handed the tree of its own, not the dirty checkout" || ko "the pytest suite read the checkout (exit $rc): $(cat "$T/log")"
[ "$(cat "$T/log")" = ok ] && ok "the python command was handed the committed value" || ko "the python command read $(cat "$T/log")"

# 4. the full gate: the GUI suite grades the same tree as the shell and pytest suites
mk ok $'ok\nbroken'
gate full "$(gui)"; rc=$?
[ $rc = 0 ] && ok "the full gate is green on a dirty checkout whose HEAD is green" || ko "the full gate read the checkout (exit $rc): $(cat "$T/log")"
[ "$(sort "$T/log" | tr '\n' ' ')" = "ok ok ok " ] && ok "all three suites were handed the committed value" || ko "the suites read: $(cat "$T/log" | tr '\n' '|')"

# 5. the checkout beside the landing is never touched, and the gate leaves no tree behind
mk ok $'ok\nbroken'; b=$($G -C "$repo" rev-parse HEAD)
gate shell
[ "$($G -C "$repo" status --porcelain --untracked-files=no)" = " M flag" ] && ok "the uncommitted edit is still there, untouched" || ko "the checkout was changed: $($G -C "$repo" status --porcelain)"
[ "$(cat "$repo/flag")" = $'ok\nbroken' ] && ok "the edit's content is unchanged" || ko "the edit's content changed: $(cat "$repo/flag" | tr '\n' '|')"
[ "$($G -C "$repo" rev-parse HEAD)" = "$b" ] && [ "$($G -C "$repo" branch --show-current)" = local/next ] && ok "the checkout stays on its branch at the tip it had" || ko "HEAD moved: $($G -C "$repo" rev-parse HEAD) on $($G -C "$repo" branch --show-current)"
[ "$($G -C "$repo" worktree list | wc -l | tr -d ' ')" = 1 ] && ok "the gate's own tree is gone when the gate ends" || ko "a tree was left behind: $($G -C "$repo" worktree list)"

# 6. a gate with no repository to check out (a bare fixture, as the other suites use) grades where it stands, as it always did
rm -rf "$T/bare"; mkdir -p "$T/bare/contrib/squad-goal/scripts"; cp "$here/fork-gate.sh" "$here/serial.sh" "$T/bare/contrib/squad-goal/scripts/"
{ echo '#!/bin/bash'; cat "$T/shell-body"; } > "$T/bare/contrib/squad-goal/scripts/shell-suites.sh"; echo ok > "$T/bare/flag"; rm -f "$T/log" "$T/out"
(cd "$T/bare" && FG_TEST_LOG="$T/log" FG_SUITES=shell SG_FORK_PY_CMD=true bash contrib/squad-goal/scripts/fork-gate.sh true > "$T/out" 2>&1) && ok "a tree with no repository is graded where it stands" || ko "the gate failed with no repository: $(cat "$T/out")"
[ "$(cat "$T/log")" = ok ] && ok "that suite ran in the fixture it was given" || ko "the suite read $(cat "$T/log")"

rm -rf "$T"; exit $fail
