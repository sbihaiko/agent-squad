#!/bin/bash
# Regression test for #85: the land pass is two phases. Phase one merges the branch into the base as soon as it is eligible and
# needs no suite lock; phase two gates the landed head after a child gate that holds the machine-wide `gate` lock (serial.sh)
# instead of beside it, and says so (FORKCLOSE WAIT-GATE naming the holder and how long it has held the lock, FORKCLOSE GATE-LOCK
# when it takes it). The pass holds the lock only around that wait, never across the shell suites (#94). A dirty path in the shared checkout holds only the merge that would touch it, and the WAIT line names the paths
# (five, then a count). A red re-gate still leaves the issue open, reverts the merge and arms the fix run.
# Stub gh/test.sh/mkreq.sh, real fork-gate.sh and serial.sh, throwaway fork repo, no emulator. usage: bash test-forkclose-split.sh
unset "${!SG_@}" FG_SUITES; here="$(cd "$(dirname "$0")" && pwd)"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
setup() {   # setup <issue>...: a fork whose base has f and g; branch 1 adds f1, branch 2 edits g, the rest add their own file
  T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/req" "$T/st/tests" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  export HOME="$T/home"; mkdir -p "$HOME"
  cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/fork-gate.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/"; printf 'echo "held=${SG_SERIAL_HELD:-none}" >> %s/suitelock\n' "$T" > "$T/s/shell-suites.sh"; cp $here/ghread.py "$T/s/"; cp $here/ghthrottle.py $here/ghcall.sh "$T/s/"
  cat > "$T/s/test.sh" <<'EOS'
#!/bin/bash
iss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift
o=$("$@" 2>&1); rc=$?
mkdir -p "$SG_STATE/tests"; [ -n "$TEST_ID" ] && printf '%s\n' "$o" > "$SG_STATE/tests/$TEST_ID.log"
printf '%s\n' "$o" | tail -3; echo "TEST #$iss x test-$iss-x"; exit $rc
EOS
  printf '#!/bin/bash\necho x >> %s/fixruns; echo %s/req.txt\n' "$T" "$T" > "$T/s/mkreq.sh"; printf 'open(__import__("sys").argv[1]+".launched","w")\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo gate >> %s/log; [ ! -e bad ]"\nexport SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
  g init -q -b local/next; echo 0 > "$T/fork/f"; echo 0 > "$T/fork/g"; g add .; g commit -q -m init
  local n items=""; for n in "$@"; do g checkout -q -b gui/$n local/next
    case $n in 1) echo 1 > "$T/fork/f1";; 2) echo edit > "$T/fork/g";; 4) echo x > "$T/fork/bad";; *) echo $n > "$T/fork/f$n";; esac
    g add .; g commit -q -m "work $n"; g checkout -q local/next; items="$items{\"n\":$n,\"st\":\"Code Complete\",\"s\":\"open\"},"; done
  echo "{\"items\":[${items%,}]}" > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in\n  *"issues/"*" -q .state"*) echo open;;\n  *"/comments"*) echo "TDD RED (test-x.sh): FAIL y";;\n  *"-X PATCH"*"issues/"*) echo "$*" >> %s/closed.log;;\n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; }
pass() { FC_NOBATCH=1 bash "$T/s/forkclose.sh" 2>&1; }
nfix() { [ -e "$T/fixruns" ] && wc -l < "$T/fixruns" | tr -d ' ' || echo 0; }

# 1. the merge lands while a child gate holds the lock; the re-gate waits for it, names the holder, and runs after it
setup 1
SG_STATE="$T/st" bash "$T/s/serial.sh" gate sh -c "echo hold-start >> $T/log; sleep 14; echo hold-end >> $T/log" & hp=$!; sleep 1
hpid=$(cat "$T/st/locks/gate/pid")
pass > "$T/fc.out" 2>&1 & fp=$!
for i in $(seq 24); do g log --oneline local/next 2>/dev/null | grep -q "Merge gui/1" && break; sleep 0.5; done   # the pass's reads take a few seconds; the holder has 14
if g log --oneline local/next | grep -q "Merge gui/1" && kill -0 "$hp" 2>/dev/null && [ -d "$T/st/locks/gate" ]; then ok "the merge landed while the gate lock was held by someone else"; else ko "the merge waited for the gate lock: $(g log --oneline local/next | tr '\n' '|') holder alive: $(kill -0 "$hp" 2>/dev/null && echo yes || echo no)"; fi
[ ! -e "$T/closed.log" ] && ok "the issue is not closed before its re-gate" || ko "closed before the gate ran"
wait "$fp" "$hp"; r=$(cat "$T/fc.out")
case "$r" in *"FORKCLOSE WAIT-GATE: waiting for the gate lock (holder $hpid, held "[0-9]*"s)"*) ok "the wait is named, with the holder serial.sh recorded and how long it has held the lock";; *) ko "no WAIT-GATE line naming pid $hpid and its hold time: $r";; esac
case "$r" in *"FORKCLOSE GATE-LOCK: took the gate lock"*) ok "taking the lock is named";; *) ko "no GATE-LOCK line: $r";; esac
[ "$(tr '\n' ' ' < "$T/log")" = "hold-start hold-end gate " ] && ok "the re-gate ran after the holder ended, never beside it" || ko "gate order: $(tr '\n' ' ' < "$T/log")"
grep -q "state=closed" "$T/closed.log" 2>/dev/null && ok "the issue closes on the green re-gate" || ko "no close after the green re-gate: $r"
[ -e "$T/st/auto/basetest-$(g rev-parse --short=9 local/next)" ] && ok "the gated head is recorded as a tested base" || ko "no basetest for the landed head"
[ ! -d "$T/st/locks/gate" ] && ok "the gate lock is released after the pass" || ko "gate lock left behind"
[ "$(cat "$T/suitelock" 2>/dev/null)" = "held=none" ] && ok "the re-gate's shell suites run outside the gate lock" || ko "the re-gate's shell suites ran under the gate lock: $(cat "$T/suitelock" 2>/dev/null)"
rm -rf "$T"

# 2. a dirty path the merge does not touch holds nothing; the pass lands beside it and never resets it
setup 1
echo dirty >> "$T/fork/f"
r=$(pass)
case "$r" in *"WAIT"*) ko "an unrelated dirty path stopped the merge: $(grep -m1 WAIT <<<"$r")";; *) ok "an unrelated dirty path does not stop the merge";; esac
g log --oneline local/next | grep -q "Merge gui/1" && ok "the branch landed beside the dirty path" || ko "gui/1 did not land: $r"
grep -q dirty "$T/fork/f" && [ -n "$(g status --porcelain --untracked-files=no)" ] && ok "the dirty path is still there, untouched" || ko "the dirty path was reset or committed"
g show local/next:f | grep -q dirty && ko "the dirty path was swept into the merge commit" || ok "the merge commit does not carry the dirty path"
grep -q "state=closed" "$T/closed.log" 2>/dev/null && ok "the issue closes" || ko "no close: $r"
rm -rf "$T"

# 3. a dirty path the merge would touch still stops that merge, and the WAIT line names the path
setup 2 3
echo dirty >> "$T/fork/g"
r=$(pass)
case "$r" in *"FORKCLOSE WAIT #2: "*"uncommitted changes"*" g"*) ok "the merge that would touch g waits and names g";; *) ko "no WAIT naming g for #2: $r";; esac
g log --oneline local/next | grep -q "Merge gui/2" && ko "gui/2 landed over the dirty path" || ok "gui/2 did not land over the dirty path"
g log --oneline local/next | grep -q "Merge gui/3" && ok "the branch the dirty path does not touch lands in the same pass" || ko "gui/3 was held by a path it does not touch: $r"
grep -q dirty "$T/fork/g" && ok "the dirty path is untouched" || ko "the dirty path was reset"
rm -rf "$T"

# 4. a staged change holds every merge (git refuses to merge over a dirty index), and the WAIT line names it
setup 1
echo staged >> "$T/fork/f"; g add f
r=$(pass)
case "$r" in *"FORKCLOSE WAIT"*"staged"*" f"*) ok "a staged path is named as what holds the queue";; *) ko "no WAIT naming the staged path: $r";; esac
g log --oneline local/next | grep -q "Merge gui/1" && ko "merged over a staged change" || ok "nothing merged over a staged change"
g diff --cached --quiet && ko "the staged change is gone" || ok "the staged change is still there"
rm -rf "$T"

# 5. the WAIT line caps the list: five paths, then a count
setup 1
for i in 1 2 3 4 5 6 7; do echo $i > "$T/fork/h$i"; done; g add .; g commit -q -m "base h files"
g checkout -q -B gui/1 local/next; for i in 1 2 3 4 5 6 7; do echo edit >> "$T/fork/h$i"; done; g add .; g commit -q -m "edit seven"; g checkout -q local/next
for i in 1 2 3 4 5 6 7; do echo dirty >> "$T/fork/h$i"; done
r=$(pass); w=$(grep -m1 "FORKCLOSE WAIT #1" <<<"$r")
case "$w" in *"h1"*"h5"*"and 2 more"*) ok "the WAIT line names five paths and counts the rest";; *) ko "no capped list: $w";; esac
case "$w" in *"h6"*|*"h7"*) ko "the list is not capped: $w";; *) ok "the sixth and seventh path are counted, not named";; esac
rm -rf "$T"

# 6. a red re-gate on a base recorded green: the issue stays open, the merge is reverted, the fix run arms (as before #85)
setup 4
touch "$T/st/auto/basetest-$(g rev-parse --short=9 local/next)"
r=$(pass)
case "$r" in *"FORKCLOSE LANDED #4"*) ok "the landing is named before its gate";; *) ko "no LANDED line: $r";; esac
case "$r" in *"FORKCLOSE RED #4"*) ok "the red re-gate is named";; *) ko "no RED #4: $r";; esac
[ ! -e "$T/closed.log" ] && ok "the issue did not close" || ko "closed on a red re-gate"
[ ! -e "$T/fork/bad" ] && g log -1 --format=%s local/next | grep -q "^Revert" && ok "the merge is reverted" || ko "the merge stands: $(g log --oneline -3 local/next | tr '\n' '|')"
[ "$(nfix)" = 1 ] && ok "the fix run arms" || ko "fix runs: $(nfix)"
rm -rf "$T"

# 7. a red re-gate on a base nobody recorded green: the merge is taken back and the base is gated alone before the branch is blamed
setup 4
r=$(pass)
case "$r" in *"FORKCLOSE RED #4"*) ok "the base was green on its own, so the branch is blamed";; *) ko "no RED #4: $r";; esac
[ "$(nfix)" = 1 ] && ok "the fix run arms once" || ko "fix runs: $(nfix)"
[ "$(grep -c gate "$T/log")" = 2 ] && ok "two gate runs: the landed head, then the base on its own" || ko "gate runs: $(grep -c gate "$T/log")"
[ -e "$T/st/auto/basetest-$(g rev-parse --short=9 local/next)" ] && ok "the base that came out green is recorded" || ko "the base green run was not recorded"
rm -rf "$T"
setup 4
printf 'export SG_FORK_TEST_CMD="echo gate >> %s/log; false"\n' "$T" >> "$T/s/env.sh"; printf 'export SG_FORK_BASERED_TRIES=1\n' >> "$T/s/env.sh"
r=$(pass)
case "$r" in *"FORKCLOSE RED #4"*) ko "a red base blamed the branch: $r";; *) ok "a red base does not blame the branch";; esac
case "$r" in *"BASE-RED"*) ok "the base's own red is named";; *) ko "no BASE-RED: $r";; esac
[ "$(nfix)" = 0 ] && ok "no fix run for a red base" || ko "fix runs: $(nfix)"
[ ! -e "$T/closed.log" ] && [ ! -e "$T/fork/bad" ] && ok "the issue stays open and the merge is reverted" || ko "closed or not reverted"
rm -rf "$T"; exit $fail
