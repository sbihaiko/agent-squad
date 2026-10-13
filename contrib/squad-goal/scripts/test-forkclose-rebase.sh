#!/bin/bash
# Regression test for #11: a candidate lands on the base as it is NOW, not on the one it forked from.
# The evidence (2026-10-12): ten gui/* branches landed by hand -- 9 minutes to merge, 53 minutes to make the merged tree green
# (13 files, +148/-87). The three deepest branches (87, 119 and 122 commits behind) produced most of the 18 conflict hunks and
# every post-merge failure. Rebasing those three tips onto the base they were merged into (same commits, same bases) left 1 hunk
# to resolve against 15. So the land rebases a candidate whose tip is behind the current base, refuses the stale merge by name,
# runs the gate on the REBASED tree, and says which way it went when a rebase cannot be resolved (the merge path takes it).
# Stub gh/test.sh, throwaway fork repos, offline. usage: bash test-forkclose-rebase.sh [path/to/forkclose.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/forkclose.sh}; case $src in /*) ;; *) src="$PWD/$src";; esac
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
A="$T/st/auto"; export GHLOG="$T/gh.log"; : > "$GHLOG"
# The fork child's checklist ends with the rebase and then both gate commands: the last step the child is told to take is the
# rebase, and the two commands it then runs are the gate's own, verbatim (#1891 keeps the gate reading the same lines).
ck="$d/req-checklist-fork.txt"
case "$(tail -3 "$ck" | head -1)" in "REBASE COMMAND:"*) ok "the checklist ends with the rebase";; *) ko "the checklist's third-to-last line is '$(tail -3 "$ck" | head -1)'";; esac
case "$(tail -2 "$ck" | head -1)" in "GATE LINT COMMAND: "*"ruff check squad tests") ok "the rebase is followed by the gate's ruff half";; *) ko "the lint line is '$(tail -2 "$ck" | head -1)'";; esac
case "$(tail -1 "$ck")" in "GATE SUITE COMMAND: "*"pytest tests"*"--ignore-glob"*) ok "and by the gate's suite as its last line";; *) ko "the last line is '$(tail -1 "$ck")'";; esac
mkfix() {   # fresh state, stub gh/test.sh/env.sh, and an empty fork repo: each case builds the history it is about
  rm -rf "$T/s" "$T/st" "$T/co" "$T/bin" "$T/fork"; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$d"/{forkonly.sh,prland.sh,ghcall.sh,address.sh,fork-gate.sh,serial.sh,gate-suites.sh} "$T/s/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$T/s/"
  printf 'exit 0\n' > "$T/s/shell-suites.sh"; printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo "$*" >> "$GHLOG"
case "$*" in
  *"--method POST repos/"*"/pulls -f"*) echo '{"number":7}';;   # one route (#32): the land opens the PR, its checks are green, GitHub merges it
  *"/check-runs"*) echo '{"check_runs":[{"status":"completed","conclusion":"success"}]}';;
  *"/pulls/7/merge"*) echo '{"merged":true}';;
  *"pulls?state=open"*) ;;
  *"/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";;
  *" -q .body"*) ;;
  *" -q .state"*) echo open;;
  *"graphql"*) echo "[]";;                                            # the aggregate reads (#5): the list answers empty, so each candidate falls back to its own read
  *"--include"*) echo "{\"state\":\"open\"}";;
esac
J
  chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; }
board() { printf '{"items":[%s]}\n' "$1" > "$T/co/runs/.hub/hub-board.json"; }
# The branch forks from the base and the base then moves on: that is the stale candidate the ticket is about.
stale() { g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
  g checkout -q -b gui/1 local/next; echo one > "$T/fork/one.py"; g add .; g commit -q -m "work 1"
  g checkout -q local/next; echo moved > "$T/fork/base-moved.txt"; g add .; g commit -q -m "the base moved on after the branch forked"; rm -rf "$T/gh"; mkdir -p "$T/gh/x"; $G init -q --bare "$T/gh/x/tool.git"; g remote add origin "$T/gh/x/tool.git"; g push -q origin local/next   # the land pushes the branch and merges by PR (#32)
}

mkfix; stale; board '{"n":1,"st":"Code Complete","s":"open"}'
[ "$(g rev-list --count local/next..gui/1)" = 0 ] || true
r=$(bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE STALE #1"*"gui/1"*"is behind"*) ok "the stale candidate is refused by name (no stale merge)";; *) ko "no STALE refusal naming gui/1: '$r'";; esac
case "$r" in *"FORKCLOSE REBASE #1"*) ok "the branch is rebased onto the current base before its gate";; *) ko "no REBASE line: '$r'";; esac
[ "$(g rev-parse gui/1)" = "$(g rev-parse local/next)" ] && ok "the candidate landed ON the current base (the rebased tip is the base head)" || ko "the base head is not the rebased tip"
[ -e "$T/fork/one.py" ] && [ -e "$T/fork/base-moved.txt" ] && ok "the landed tree carries the branch's work and the base's own commits" || ko "the landed tree is missing a side: $(ls "$T/fork" | tr '\n' ' ')"
[ -z "$(g log --format=%s -1 local/next | $(command -v grep) '^Merge ')" ] && ok "a rebased landing carries no 'Merge <branch>' subject" || ko "the landing carried a merge commit: $(g log --format=%s -1 local/next)"
[ "$(cat "$A/landed-$(g rev-parse --short=9 local/next)" 2>/dev/null)" = "gui/1" ] && ok "the landing records the branch that produced the head, for the park that has to attribute it" || ko "no landing record for gui/1"
$(command -v grep) -q "state=closed" "$GHLOG" && ok "the issue closes on the rebased tree's green gate" || ko "the issue was not closed: $(tail -3 "$GHLOG")"
[ -z "$(g status --porcelain)" ] && ok "the fork checkout is left clean" || ko "dirty checkout: $(g status --porcelain)"
# A replayed landing carries no `Merge <branch>` subject, so a later pass finds the branch's commits already in the base, greps for
# the merge commit that is not there and skips the issue forever: the close that pass owes is lost (#11). The record the landing
# wrote is what names the branch, and the pass closes it late -- by name.
m9=$(g rev-parse --short=9 local/next); rm -f "$A/basetest-$m9" "$T/st/basetest-$m9.log" "$A/fork-1-"*
r=$(bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"closed late"*"gui/1"*) ok "a later pass still names the branch a replayed landing came from";; *) ko "the late close cannot attribute the replay: '$r'";; esac
# The branch's own worktree is the one the child kept: the rebase happens there, and it is the tree that lands.
mkfix; stale; board '{"n":1,"st":"Code Complete","s":"open"}'; g worktree add -q "$T/w1" gui/1
r=$(bash "$T/s/forkclose.sh" 2>&1)
[ "$($G -C "$T/w1" rev-parse HEAD)" = "$(g rev-parse local/next)" ] && ok "the branch's own worktree is the one rebased and landed" || ko "the branch's worktree is at $($G -C "$T/w1" rev-parse HEAD)"
[ -z "$($G -C "$T/w1" status --porcelain)" ] && ok "that worktree is left clean" || ko "the branch's worktree is dirty: $($G -C "$T/w1" status --porcelain)"
case "$r" in *"REBASE #1"*) ok "and the rebase is reported there too";; *) ko "no REBASE line with a worktree: '$r'";; esac
# A rebase that cannot be resolved still lands through the merge path, and says which way it went: nothing is rewritten and the
# branch stays exactly as the child left it, ready for the conflict fix the merge path dispatches.
mkfix; g init -q -b local/next; printf 'a\nb\nc\n' > "$T/fork/shared.sh"; g add .; g commit -q -m init
g checkout -q -b gui/1 local/next; printf 'a\nbranch\nc\n' > "$T/fork/shared.sh"; g add .; g commit -q -m "work 1"
g checkout -q local/next; printf 'a\nbase\nc\n' > "$T/fork/shared.sh"; echo moved > "$T/fork/base-moved.txt"; g add .; g commit -q -m "the base moved on"
board '{"n":1,"st":"Code Complete","s":"open"}'; before=$(g rev-parse gui/1)
r=$(bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE STALE #1"*) ok "the stale candidate is named here too";; *) ko "no STALE line: '$r'";; esac
case "$r" in *"which way it went"*) ok "an unresolvable rebase says which way the landing went";; *) ko "no 'which way it went' line: '$r'";; esac
[ "$(g rev-parse gui/1)" = "$before" ] && ok "the unrebasable branch is left exactly as the child committed it" || ko "the branch moved to $(g rev-parse gui/1)"
[ ! -d "$T/fork/.git/rebase-merge" ] && [ -z "$(g status --porcelain)" ] && ok "no rebase is left in progress (the merge path took it)" || ko "a rebase was left behind: $(g status --porcelain)"
[ -z "$(g log --format=%s -1 local/next | $(command -v grep) '^Merge ')" ] && ok "nothing stale was merged either" || ko "a stale merge landed: $(g log --format=%s -1 local/next)"
rm -rf "$T"; [ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'; exit $fail
