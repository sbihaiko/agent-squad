#!/bin/bash
# Regression test for the fork base gate's suite choice (#1844): the base gate asks the question the candidate gate already asks -- which
# suites can this base's own diff affect -- and answers it from the one table (gate-suites.sh) over the base's diff since the last base
# recorded green. The park and its solo retry reuse the same selection, so a red base is never parked by a suite its diff cannot affect.
# Stub gate, stub gh, throwaway fork repo, no emulator.
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD   # the real e2e checkouts of the caller must never be touched by a stub run
here="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; A="$T/st/auto"
mkdir -p "$T/s" "$T/bin"
cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/gate-suites.sh" "$T/s/"
# The gate stub records the FG_SUITES it was handed and runs the GUI command only for the full gate, like fork-gate.sh does.
printf '#!/bin/bash\ns=${FG_SUITES:-full}; echo "$s" >> %s/suites.log\n[ "$s" = full ] && eval "$1"\n[ -e %s/red ] && exit 1\nexit 0\n' "$T" "$T" > "$T/s/fork-gate.sh"
printf '#!/bin/bash\niss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift; "$@"; rc=$?; echo "TEST #$iss x test-$iss-x"; exit $rc\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"/comments"*) echo "TDD RED (test-x.sh, line): FAIL y";; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
fresh() { rm -rf "$T/fork" "$T/st" "$T/co" "$T/suites.log" "$T/runs" "$T/red"; mkdir -p "$T/fork" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/co/runs/.hub"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo run >> %s/runs"\nexport SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
  echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
  $G init -q -b local/next "$T/fork"
  mkdir -p "$T/fork/contrib/squad-goal" "$T/fork/tests"; echo 0 > "$T/fork/contrib/squad-goal/f"; echo 0 > "$T/fork/tests/test_x.py"; echo 0 > "$T/fork/README.md"
  g add -A; g commit -q -m init; }
green() { touch "$A/basetest-$($G -C "$T/fork" rev-parse --short=9 local/next)"; }   # the last base recorded green: the anchor the base diff is read from
base() { g checkout -q local/next; for p in "$@"; do echo 1 >> "$T/fork/$p"; done; g add -A; g commit -q -m "base moves: $*"; }   # the base moves with no gate having passed on it
br() { g checkout -q -b gui/1 local/next; echo 1 >> "$T/fork/$1"; g add -A; g commit -q -m "work 1"; g checkout -q local/next; }
pass() { FC_NOBATCH=1 bash "$T/s/forkclose.sh" >/dev/null 2>&1; }
suites() { tr '\n' ' ' < "$T/suites.log" 2>/dev/null; }
want() { [ "$(suites)" = "$1" ] && ok "$2" || ko "$2 -- suites: '$(suites)'"; }

# a base whose own diff touched only contrib/squad-goal/ needs the shell suites, not the GUI suite (its diff cannot affect it)
fresh; green; base contrib/squad-goal/g; br contrib/squad-goal/h
pass
want "shell shell " "a base diff confined to contrib/squad-goal/ runs the shell suites only, for the base gate and the branch gate"
[ ! -e "$T/runs" ] && ok "that base never runs the GUI suite" || ko "the GUI suite ran for a shell-only base diff"
[ -e "$A/basetest-$($G -C "$T/fork" rev-parse --short=9 HEAD)" ] && ok "the base that came out green is recorded as a tested base" || ko "the base green run was not recorded"

# a base whose own diff touched tests/ needs the python suites, and the branch gate keeps its own answer
fresh; green; base tests/test_x.py; br contrib/squad-goal/h
pass
want "python shell " "a base diff confined to tests/ runs the python suites only, while the branch's own diff still picks shell"

# any doubt stays the full gate: an unknown path, two suites in one diff, and a base nobody has recorded green yet
fresh; green; base README.md; br contrib/squad-goal/h
pass
want "full shell " "a base diff touching a path the table does not know stays full"
[ -e "$T/runs" ] && ok "the full base gate runs the GUI suite" || ko "the full base gate skipped the GUI suite"

fresh; green; base contrib/squad-goal/g tests/test_x.py; br contrib/squad-goal/h
pass
want "full shell " "a base diff touching two suites stays full"

fresh; br contrib/squad-goal/h   # no base recorded green: nothing to read the base diff from
pass
want "full shell " "a base with no accepted basetest stays full"

fresh; green; g commit -q --allow-empty -m "no tree change"; br contrib/squad-goal/h
pass
want "full shell " "a base diff with no change at all stays full"

# the park and its solo retry reuse the base gate's selection: a red base whose diff is shell-only is never parked by the GUI suite
fresh; green; base contrib/squad-goal/g; br contrib/squad-goal/h
touch "$T/red"; bs=$($G -C "$T/fork" rev-parse --short=9 local/next)
touch -t 202001010000 "$A/basered-$bs"; printf 'FAIL x\n' > "$T/st/basetest-$bs.log"
pass
want "shell " "the solo retry of a parked base runs the suites its diff needs, not the GUI suite"
[ -e "$A/basered-$bs" ] && ok "a red solo retry keeps the park" || ko "the park was lifted by a red retry"
rm -f "$T/red" "$A/basered-$bs.retried"; touch -t 202001010000 "$A/basered-$bs"
pass
want "shell shell " "a green solo retry lifts the park, and the branch gate reuses the same selection"
[ ! -e "$A/basered-$bs" ] && ok "the park is gone after the green retry" || ko "the park survived a green retry"
rm -rf "$T"; exit $fail
