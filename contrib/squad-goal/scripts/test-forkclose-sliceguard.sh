#!/bin/bash
# Regression test for #1801: forkclose.sh is the sole owner of the branch its fix request repairs (forkred-<N>-<sha> /
# forkrebase-<N>-...), so that request must dispatch past mkreq.sh's slice guard (SG_SLICE_FORCE=1) -- otherwise the guard
# refuses it (the branch it must fix, gui/<N>, is itself unmerged and touches exactly the files the fix has to edit) and
# nothing is launched. And the log must stop lying: the "fix run <name>" line may print only when mkreq.sh exited 0; a
# refusal is stopped work, recorded in cq/ naming the issue.
# Stub mkreq.sh (guard honoured, or forced to refuse), stub test.sh/gh, throwaway fork repo; one fresh fixture per scenario.
# usage: bash test-forkclose-sliceguard.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; here=$(cd "$(dirname "$src")" && pwd); own=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR
# mkfix <dir>: a fork repo with one branch, gui/1, ahead of local/next, and a fork gate that goes red on it.
mkfix() { T=$1; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "${here}/gate-suites.sh" "$T/s/" 2>/dev/null || cp "$own/gate-suites.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py $(dirname "$src")/ghcall.sh "$T/s/"
  printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"
  cat > "$T/s/test.sh" <<'J'
#!/bin/bash
id=${TEST_ID:-noid}
printf 'FAIL tests/test_pilot.py::case_1 (fork suite)\n' > "$SG_STATE/tests/$id.log"   # the red the fix run is meant to repair
echo "$id"; exit 1
J
  cat > "$T/s/mkreq.sh" <<'J'
#!/bin/bash
# the real mkreq.sh: its slice guard refuses a request whose files an unmerged fork branch touches, unless SG_SLICE_FORCE=1
# ($FCT/refuse forces a refusal even then, to exercise "any other refusal"); on success it prints the request path.
[ -n "${SG_SLICE_FORCE:-}" ] && echo "$SG_SLICE_FORCE" >> "$FCT/force"
if [ "${SG_SLICE_FORCE:-}" != 1 ] || [ -e "$FCT/refuse" ]; then
  echo "refusing: request $1 names tests/test_pilot.py, which the unmerged fork branch gui/1 touches (that is where a fork-only child's slice lives). Set SG_SLICE_FORCE=1 to dispatch anyway." >&2
  exit 1
fi
echo "$1" >> "$FCT/fixruns"; echo "$FCT/req/$1.txt"
J
  printf 'import sys\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  echo ". \"$here/address.sh\"" >> "$T/s/env.sh"   # the writers name a queued comment through the seam, as the real env.sh loads it
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t init -q -b local/next
  echo 0 > "$T/fork/f"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add f
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m init
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q -b gui/1 local/next
  echo 1 > "$T/fork/f1"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add .
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m "work 1"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q local/next
  echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"issues/1/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
}
# 1: a red fork gate on a branch the guard would refuse must still launch the fix run
T1=$(mktemp -d); mkfix "$T1"; export FCT=$T1 PATH="$T1/bin:$PATH"
nfix() { [ -e "$FCT/fixruns" ] && wc -l < "$FCT/fixruns" | tr -d ' ' || echo 0; }
r=$(bash "$T1/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE RED #1 (test-1-"*) ok "the red gate is reported with its test pass";; *) ko "no RED line: '$r'";; esac
[ "$(nfix)" = 1 ] && ok "the fix request dispatched past the slice guard (mkreq.sh ran and wrote the request)" || ko "no fix run behind the report: $(nfix) request(s) written"
[ "$(cat "$T1/force" 2>/dev/null)" = 1 ] && ok "SG_SLICE_FORCE=1 is set on that one mkreq.sh call" || ko "SG_SLICE_FORCE not set: '$(cat "$T1/force" 2>/dev/null)'"
case "$r" in *"fix run forkred-1-"*) [ "$(nfix)" = 1 ] && ok "the report names the fix run it launched" || ko "the report claims a fix run nothing launched: '$r'";; *) ko "no fix-run line: '$r'";; esac
[ -z "$(ls "$T1/st/cq/"*slicerefus* 2>/dev/null)" ] && ok "no refusal card when the dispatch succeeded" || ko "a refusal card was written on a successful dispatch"
# 2: any other refusal (the stub refuses even under the force) must never read as a launched run
T2=$(mktemp -d); mkfix "$T2"; export FCT=$T2; touch "$T2/refuse"
r=$(bash "$T2/s/forkclose.sh" 2>&1)
[ "$(nfix)" = 0 ] && ok "the refusing stub launches nothing" || ko "the stub was bypassed: $(nfix)"
case "$r" in *"FORKCLOSE RED"*"#1 (test-1-"*"exit 1"*) ok "the red gate is still reported with its test pass and exit code";; *) ko "the red gate vanished from the report: '$r'";; esac
case "$r" in *"fix run forkred-1-"*) ko "the log claims a fix run that never launched: '$r'";; *) ok "no 'fix run' line when mkreq.sh refused";; esac
c=$(ls "$T2/st/cq/x__y#"*slicerefus* 2>/dev/null | head -1)
[ -n "$c" ] && grep -q "#1" "$c" && grep -q "refus" "$c" && ok "a cq/ card names the issue and the refusal reason" || ko "no cq/ card for the refused fix request: '$(cat "$c" 2>/dev/null)'"
rm -rf "$T1" "$T2"; exit $fail
