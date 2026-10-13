#!/bin/bash
# Regression test for #55: a fork fix request (a conflict fix, a post-merge suite fix) tells the child to work in
# `git worktree add ~/wt-fr-<N> gui/<N>`, and the router turned that path into the spawn `workspace` field
# ('worktrees/child/gui-<N>'). The graph allows only the logical workspace, so the envelope rejected the request whole
# (workspace_outside_allow) and the run ended no_dispatch. The request text must keep the branch worktree an instruction
# path and say that the spawn `workspace` field is an allowed workspace, `worktrees/child` here.
# Stub mkreq.sh keeps the request body, stub test.sh/gh, throwaway fork repo; one fresh fixture per scenario.
# usage: bash test-forkclose-workspace.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; here=$(cd "$(dirname "$src")" && pwd); own=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
# mkfix <dir> <conflict|red>: a fork repo with gui/1 ahead of local/next; conflict: both sides changed f, red: the fork gate fails on the merge.
mkfix() { T=$1; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "${here}/gate-suites.sh" "$T/s/" 2>/dev/null || cp "$own/gate-suites.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py "$T/s/"; cp $(dirname "$src")/ghcall.sh "$T/s/"; cp $(dirname "$src")/address.sh "$T/s/"
  printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"
  cat > "$T/s/test.sh" <<'J'
#!/bin/bash
id=${TEST_ID:-noid}
printf 'FAIL tests/test_pilot.py::case_1 (fork suite)\n' > "$SG_STATE/tests/$id.log"
echo "$id"; exit 1
J
  cat > "$T/s/mkreq.sh" <<'J'
#!/bin/bash
cp "$SG_STATE/req/$1.body" "$FCT/$1.body"; echo "$FCT/req/$1.txt"
J
  printf 'import sys\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
  g checkout -q -b gui/1 local/next; echo 1 > "$T/fork/$([ "$2" = conflict ] && echo f || echo f1)"; g add .; g commit -q -m "work 1"
  g checkout -q local/next; [ "$2" = conflict ] && { echo base > "$T/fork/f"; g add .; g commit -q -m "base moved f"; }
  echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"issues/1/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
}
check() { # <label> <body file>
  local b=$2
  [ -s "$b" ] || { ko "$1: no request body was written"; return; }
  grep -q 'worktree add ~/wt-fr-1\|worktree of gui/1' "$b" && ok "$1: the branch worktree stays a path in the text" || ko "$1: the worktree path left the text"
  grep -q 'spawn `workspace` field' "$b" && ok "$1: the text speaks of the spawn workspace field" || ko "$1: no word on the spawn workspace field"
  grep -q '`worktrees/child`' "$b" && ok "$1: the text names the allowed workspace" || ko "$1: the allowed workspace is not named"
  grep -qi 'never the branch worktree\|never a worktree path\|never the worktree' "$b" && ok "$1: the text forbids the worktree path as the workspace" || ko "$1: the text does not forbid the worktree path as the workspace"
}
T1=$(mktemp -d); mkfix "$T1" conflict; export FCT=$T1 PATH="$T1/bin:$PATH"
r=$(bash "$T1/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE CONFLICT #1"*) ok "the conflict is reported";; *) ko "no CONFLICT line: '$r'";; esac
check "conflict fix" "$(ls "$T1"/forkrebase-1-*.body 2>/dev/null | head -1)"
T2=$(mktemp -d); mkfix "$T2" red; export FCT=$T2 PATH="$T2/bin:$PATH"
r=$(bash "$T2/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE RED #1"*) ok "the red gate is reported";; *) ko "no RED line: '$r'";; esac
check "suite fix" "$(ls "$T2"/forkred-1-*.body 2>/dev/null | head -1)"
rm -rf "$T1" "$T2"; exit $fail
