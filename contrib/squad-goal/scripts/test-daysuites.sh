#!/bin/bash
# seconds: 10.05
# Regression test for daysuites.sh (#2307): every shell suite on the dev tip once a day, one suite at a time under the gate
# lock and one gate slot, never beside a live landing gate, one bug per red suite through the loop's one filer, and no
# second bug for a suite red on two passes in a row. usage: bash test-daysuites.sh [path/to/daysuites.sh]
# A throwaway git tree stands in for the dev tip and its test-*.sh are the stubbed suite list; gh and the board are stubs, so
# no case reaches GitHub, a board, a model or the network.
# Hermetic first: the live loop exports its own SG_* and SERIAL_* to every run it dispatches, and a leaked SG_STATE would
# point this suite at the owner's state. Drop them all, as shell-suites.sh does for every suite (#1410).
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/daysuites.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; fail=0
mkdir -p "$S" "$ST/auto" "$ST/locks"
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_MIN_FREE_GB=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
[ -f "$src" ] || { echo "FAIL starts: daysuites.sh does not exist"; rm -rf "$T"; exit 1; }
# The pass and the filer's own files: autohunt.sh is the one filing path, never a second (autobug.sh, refusal-names.sh).
cp "$src" "$S/daysuites.sh"
cp "$d/autohunt.sh" "$d/serial.sh" "$d/caps.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/workbranches.sh" \
   "$d/refusal-names.sh" "$d/envelope-spend.sh" "$d/issue_ledger.py" "$d/priority_backfill.py" "$S/" 2>/dev/null
# The filer sources env.sh; the fixture's copy names this suite's state, board and repository, so nothing resolves the owner's.
printf '#!/bin/bash\nSG_REPO=o/r; SG_OWNER=o; SG_PROJECT=4; SG_STATE=%s; SG_CHECKOUT=%s; SG_BASE=dev; SG_TODO=Todo; BOARD=%s/board.sh\n' "$ST" "$T/co" "$T" > "$S/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\nexit 0\n' "$T" > "$T/board.sh"
mkdir -p "$T/co" "$T/bin"
cat > "$T/bin/gh" <<GH
#!/bin/bash
echo "\$*" >> "$T/gh.log"
for a in "\$@"; do case "\$a" in body=@*) cat "\${a#body=@}" >> "$T/gh.log" 2>/dev/null;; esac; done
case "\$*" in
  *rate_limit*) echo '{"resources":{}}';;
  *POST*|*DELETE*) echo '{"number":42}';;
  *) echo '{}';;
esac
GH
chmod +x "$T/board.sh" "$T/bin/gh"
export PATH="$T/bin:$PATH" GHLOG="$T/gh.log"
GIT="/usr/bin/git -c user.email=a@b -c user.name=t"
mktree() { mkdir -p "$1/contrib/squad-goal/scripts"; $GIT init -q -b dev "$1"; }
suite() {  # <tree> <name> <shell body>
  printf '#!/bin/bash\n%s\n' "$3" > "$1/contrib/squad-goal/scripts/$2"; chmod +x "$1/contrib/squad-goal/scripts/$2"
}
commit() { $GIT -C "$1" add -A >/dev/null && $GIT -C "$1" commit -q -m one; }
newday() { rm -f "$ST"/auto/daysuites-2*; }   # a new day: the marker goes, the last pass's reds stay
newworld() { newday; rm -f "$ST/auto/daysuites-red"; }   # a new fixture: no marker and no red record from another case
# (#2635) The host clock never decides a case: the fixture names an hour outside the nightly hour, and a case that wants
# the nightly hour names it itself. An hour left to the host held every pass when CI ran at 04:00 America/Sao_Paulo.
run() {  # <tree> [extra VAR=val...]: one pass against the fixture; prints its output
  local tree=$1; shift
  env SG_DAYSUITES_LOAD=0.0 SG_DAYSUITES_HOUR=09 "$@" SG_STATE="$ST" SG_DAYSUITES_TREE="$tree" SG_REPO=o/r SG_OWNER=o SG_PROJECT=4 SG_TODO=Todo SG_BASE=dev \
    SG_SQUAD="$SG_SQUAD" SQUAD_CAPS_DIR="$SQUAD_CAPS_DIR" RUNLOG="$T/ran.log" SLOTS_SEEN="$T/slots.log" \
    GHLOG="$T/gh.log" SERIAL_POLL_SECONDS=0.05 bash "$S/daysuites.sh" 2>&1
}
ran() { [ -s "$T/ran.log" ] && $G -c . "$T/ran.log" || echo 0; }
filed() { local n=0; [ -s "$T/gh.log" ] && n=$($G -c '^api --method POST repos/o/r/issues' "$T/gh.log" || true); echo "${n:-0}"; }

# 1) every suite runs on the dev tip, and a later pass the same day runs none
tree=$T/w1; mktree "$tree"
suite "$tree" test-alpha.sh 'echo "$0" >> "$RUNLOG"; exit 0'
suite "$tree" test-beta.sh 'echo "$0" >> "$RUNLOG"; exit 0'
commit "$tree"; sha=$($GIT -C "$tree" rev-parse HEAD)
: > "$T/ran.log"
out=$(run "$tree"); rc=$?
[ "$rc" = 0 ] && ok "a green pass exits 0" || ko "a green pass exited $rc: $out"
[ "$(ran)" = 2 ] && ok "every suite ran" || ko "a pass ran $(ran) suites, expected 2"
$G -qF "contrib/squad-goal/scripts/test-alpha.sh" "$T/ran.log" && ok "a suite ran from the dev-tip tree" \
  || ko "a suite ran outside the dev-tip tree: $(cat "$T/ran.log")"
out=$(run "$tree"); rc=$?
[ "$rc" = 0 ] && [ "$(ran)" = 2 ] && ok "a later pass the same day runs no suite" || ko "a second pass ran the suites again (exit $rc)"

# 2) a suite runs while the pass holds a gate slot, and the pass releases it
newworld; tree=$T/w2; mktree "$tree"
suite "$tree" test-alpha.sh '/bin/ls "$SQUAD_CAPS_DIR/gates" >> "$SLOTS_SEEN" 2>/dev/null; exit 0'
commit "$tree"; : > "$T/slots.log"
run "$tree" >/dev/null
[ -s "$T/slots.log" ] && ok "a suite ran while the pass held a gate slot" || ko "no gate slot was held while the suite ran"
[ -z "$(/bin/ls -A "$SQUAD_CAPS_DIR/gates" 2>/dev/null)" ] && ok "the pass released its gate slot" || ko "the pass left a gate slot held"

# 3) a live landing gate (the gate lock) keeps the pass from running a suite beside it; no day marker is spent
newworld; tree=$T/w3; mktree "$tree"
suite "$tree" test-alpha.sh 'echo "$0" >> "$RUNLOG"; exit 0'
commit "$tree"; : > "$T/ran.log"
sleep 60 & holder=$!
mkdir -p "$ST/locks/gate"; echo "$holder" > "$ST/locks/gate/pid"
out=$(run "$tree" SG_DAYSUITES_LOCK_WAIT=1); rc=$?
[ "$rc" = 75 ] && ok "a live landing gate holds the pass (exit 75)" || ko "a held pass exited $rc: $out"
[ "$(ran)" = 0 ] && ok "no suite ran beside the live landing gate" || ko "a suite ran beside the live landing gate"
[ -z "$(/bin/ls -A "$ST/auto" 2>/dev/null | $G '^daysuites-2')" ] && ok "a held pass spends no day marker" || ko "a held pass spent the day marker"
kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
run "$tree" >/dev/null; [ "$(ran)" = 1 ] && ok "the pass runs once the landing gate is gone" || ko "the pass did not run after the landing gate ended"

# 4) a red suite files one bug, in Todo, naming the suite, its first FAIL line and the dev sha
newworld; tree=$T/w4; mktree "$tree"
suite "$tree" test-red.sh 'echo "$0" >> "$RUNLOG"; echo "FAIL the widget did not settle"; echo "and a second line"; exit 1'
commit "$tree"; sha=$($GIT -C "$tree" rev-parse HEAD)
: > "$T/gh.log"; : > "$T/board.log"
out=$(run "$tree"); rc=$?
[ "$rc" = 1 ] && ok "a red suite fails the pass" || ko "a red pass exited $rc: $out"
[ "$(filed)" = 1 ] && ok "one bug filed for one red suite" || ko "filed $(filed) bugs for one red suite"
$G -qE '^api --method POST repos/o/r/issues .*labels\[\]=bug' "$T/gh.log" && ok "the bug carries the bug label" || ko "the bug carries no bug label"
$G -qF "shell suite test-red.sh" "$T/gh.log" && ok "the title names the suite" || ko "the title does not name the suite"
$G -qF "FAIL the widget did not settle" "$T/gh.log" && ok "the body names the first FAIL line" || ko "the body does not name the first FAIL line"
$G -qF "and a second line" "$T/gh.log" && ko "the body names a second FAIL line" || ok "the body names only the first FAIL line"
$G -qF "$sha" "$T/gh.log" && ok "the body names the dev sha" || ko "the body does not name the dev sha"
$G -qF -- "--add o 4 Todo 42" "$T/board.log" && ok "the bug's card opens in Todo" || ko "the card did not open in Todo: $(cat "$T/board.log" 2>/dev/null)"

# 5) a suite red on two passes in a row files no second bug; a green pass in between re-arms the filing
newworld; tree=$T/w5; mktree "$tree"
suite "$tree" test-red.sh 'echo "FAIL still broken"; exit 1'
commit "$tree"; : > "$T/gh.log"
run "$tree" >/dev/null; [ "$(filed)" = 1 ] && ok "the first red pass files a bug" || ko "the first red pass filed $(filed) bugs"
newday; run "$tree" >/dev/null
[ "$(filed)" = 1 ] && ok "a suite red on two passes in a row files no second bug" || ko "a suite red on two passes in a row filed a second bug"
newday; suite "$tree" test-red.sh 'exit 0'; commit "$tree"; run "$tree" >/dev/null
[ "$(filed)" = 1 ] && ok "a green pass files nothing" || ko "a green pass filed a bug"
newday; suite "$tree" test-red.sh 'echo "FAIL broken again"; exit 1'; commit "$tree"; run "$tree" >/dev/null
[ "$(filed)" = 2 ] && ok "a suite green in between files again on a later red pass" || ko "a red pass after a green one filed no bug"

# 6) dev moves between the run and the filing: the suite runs on the new tip, and green there files no bug
# 7) dev moves and the suite is still red on the new tip: one bug, naming the new tip's sha
# The dev tip comes from a bare origin, as the loop reads it. The first run of the suite lands a fix on dev, then fails.
moving_world() {  # <exit code of the suite on the new tip>
  newworld; rm -rf "$ST/daysuites-tree" "$T/moved" "$T/heads.log" "$T/feed" "$T/origin.git" "$T/land.sh"
  local origin=$T/origin.git feed=$T/feed
  /usr/bin/git init -q --bare "$origin"
  mktree "$feed"; $GIT -C "$feed" remote add origin "$origin"
  cat > "$feed/contrib/squad-goal/scripts/test-moved.sh" <<SUITE
#!/bin/bash
/usr/bin/git -C "\$(dirname "\$0")" rev-parse HEAD >> "$T/heads.log"
if [ ! -e "$T/moved" ]; then : > "$T/moved"; bash "$T/land.sh"; echo "FAIL the old tip"; exit 1; fi
exit $1
SUITE
  chmod +x "$feed/contrib/squad-goal/scripts/test-moved.sh"
  cat > "$T/land.sh" <<LAND
#!/bin/bash
echo landed > "$feed/fix" && $GIT -C "$feed" add -A && $GIT -C "$feed" commit -q -m fix && $GIT -C "$feed" push -q "$origin" HEAD:refs/heads/dev
LAND
  chmod +x "$T/land.sh"
  commit "$feed"; $GIT -C "$feed" push -q "$origin" HEAD:refs/heads/dev
}
run_from_origin() {  # the pass with the dev tip read from the bare origin, and no tree override
  env SG_DAYSUITES_LOAD=0.0 SG_DAYSUITES_HOUR=09 SG_STATE="$ST" SG_DAYSUITES_REMOTE="$T/origin.git" SG_REPO=o/r SG_OWNER=o SG_PROJECT=4 SG_TODO=Todo SG_BASE=dev \
    SG_SQUAD="$SG_SQUAD" SQUAD_CAPS_DIR="$SQUAD_CAPS_DIR" GHLOG="$T/gh.log" SERIAL_POLL_SECONDS=0.05 \
    bash "$S/daysuites.sh" 2>&1
}

moving_world 0; : > "$T/gh.log"
out=$(run_from_origin); rc=$?
new=$(/usr/bin/git -C "$T/origin.git" rev-parse dev); old=$(head -1 "$T/heads.log" 2>/dev/null)
[ "$rc" = 0 ] && ok "a suite green on the new tip exits 0" || ko "a pass whose suite is green on the new tip exited $rc: $out"
[ "$(filed)" = 0 ] && ok "dev moved and the suite is green on the new tip: no bug is filed" \
  || ko "dev moved and the suite is green on the new tip, yet $(filed) bug(s) were filed"
[ -n "$old" ] && [ "$old" != "$new" ] && ok "the old tip ran first" || ko "the first run did not run on the old tip"
[ "$(tail -1 "$T/heads.log" 2>/dev/null)" = "$new" ] && [ "$(wc -l < "$T/heads.log" | tr -d ' ')" = 2 ] \
  && ok "the suite ran again on the new tip" || ko "the suite did not run on the new tip: $(cat "$T/heads.log" 2>/dev/null)"

moving_world 1; : > "$T/gh.log"
out=$(run_from_origin); rc=$?
new=$(/usr/bin/git -C "$T/origin.git" rev-parse dev); old=$(head -1 "$T/heads.log" 2>/dev/null)
[ "$rc" = 1 ] && ok "a suite red on the new tip fails the pass" || ko "a suite red on the new tip exited $rc: $out"
[ "$(filed)" = 1 ] && ok "a suite red on the new tip files one bug" || ko "a suite red on the new tip filed $(filed) bugs"
$G -qF "$new" "$T/gh.log" && ok "the bug names the sha of the last red run" || ko "the bug does not name the new tip's sha"
$G -qF "$old" "$T/gh.log" && ko "the bug names the old tip's sha" || ok "the bug does not name the old tip's sha"

# 8) the nightly hour of the profile holds the pass: no suite runs, no day marker is spent, and a later hour runs the list
newworld; tree=$T/w8; mktree "$tree"
suite "$tree" test-alpha.sh 'echo "$0" >> "$RUNLOG"; exit 0'
commit "$tree"; : > "$T/ran.log"
out=$(run "$tree" SG_NIGHTLY_HOUR=04 SG_DAYSUITES_HOUR=04); rc=$?
[ "$rc" = 0 ] && ok "the nightly hour holds the pass (exit 0)" || ko "the nightly hour exited $rc: $out"
[ "$(ran)" = 0 ] && ok "no suite ran in the nightly hour of the profile" || ko "a suite ran in the nightly hour"
case "$out" in *nightly*) ok "the pass names the nightly hour that held it";; *) ko "the hold is silent: $out";; esac
[ -z "$(/bin/ls -A "$ST/auto" 2>/dev/null | $G '^daysuites-2')" ] && ok "a pass held by the nightly hour spends no day marker" \
  || ko "a pass held by the nightly hour spent the day marker"
out=$(run "$tree" SG_NIGHTLY_HOUR=04 SG_DAYSUITES_HOUR=09); rc=$?
[ "$rc" = 0 ] && [ "$(ran)" = 1 ] && ok "the pass runs outside the nightly hour" || ko "the pass outside the hour exited $rc: $out"

# 9) a suite that fails with the load at its start over the saturation line ends unproven: no bug, no day marker, a record
newworld; tree=$T/w9; mktree "$tree"
suite "$tree" test-red.sh 'echo "$0" >> "$RUNLOG"; echo "FAIL the widget did not settle"; exit 1'
commit "$tree"; : > "$T/gh.log"; : > "$T/ran.log"
out=$(run "$tree" SG_DAYSUITES_LOAD=32.0 SG_DAYSUITES_CORES=8); rc=$?
[ "$rc" = 4 ] && ok "a suite over the saturation line ends unproven (exit 4)" || ko "the loaded red pass exited $rc: $out"
case "$out" in *"unproven: load"*) ok "the pass records 'unproven: load'";; *) ko "the pass does not record 'unproven: load': $out";; esac
[ "$(filed)" = 0 ] && ok "a suite over the saturation line files no bug" || ko "a loaded red row filed $(filed) bug(s)"
$G -qF "test-red.sh" "$ST/auto/daysuites-unproven" 2>/dev/null && ok "the unproven record names the suite" \
  || ko "the unproven record does not name the suite"
[ -z "$(/bin/ls -A "$ST/auto" 2>/dev/null | $G '^daysuites-2')" ] && ok "an unproven suite spends no day marker" \
  || ko "an unproven suite spent the day marker"

# 10) a suite runs under its own HOME and keeps the caller's Python user site (#2484): pytest's own imports (pygments) may
# live only there, and a moved HOME took them off sys.path, so every pytest-backed suite ended red on an import. The pass
# names the interpreter the suites run (#3058), so the user base it keeps is that interpreter's.
newworld; tree=$T/w10; mktree "$tree"
suite "$tree" test-site.sh 'echo "home=$HOME" >> "$RUNLOG"; echo "base=$(python3 -m site --user-base)" >> "$RUNLOG"; exit 0'
commit "$tree"; : > "$T/ran.log"
run "$tree" >/dev/null
want=$("$(bash -c '. "$1/caps.sh"; sg_gate_python' _ "$d")" -m site --user-base)
$G -q '^home=' "$T/ran.log" && ! $G -qxF "home=$HOME" "$T/ran.log" && ok "the suite ran under its own HOME" || ko "the suite ran under the caller's HOME: $(cat "$T/ran.log")"
$G -qxF "base=$want" "$T/ran.log" && ok "the suite keeps the caller's Python user site" \
  || ko "the suite lost the caller's Python user site ($want): $(cat "$T/ran.log")"

# 11) (#2798) A day suite holds the gate lock for less than a landing waits for it: the hold is derived from the landing's lock
# wait (SG_TIP_LOCK_WAIT) and stays under it, even when SG_DAYSUITES_SUITE_HOLD names more. A suite stopped at that hold is held,
# never a red: the pass files no bug and spends no day marker.
newworld; tree=$T/w11; mktree "$tree"
suite "$tree" test-slow.sh 'echo "$0" >> "$RUNLOG"; sleep 30; exit 0'
commit "$tree"; : > "$T/ran.log"; : > "$T/gh.log"
s0=$(date +%s); out=$(run "$tree" SG_TIP_LOCK_WAIT=3 SG_DAYSUITES_SUITE_HOLD=60); rc=$?; el=$(( $(date +%s) - s0 ))
[ "$rc" = 75 ] && ok "2798 a suite past its gate-lock hold holds the pass (exit 75)" || ko "2798 a suite past its hold exited $rc in ${el}s: $out"
[ "$el" -lt 20 ] && ok "2798 the hold stays under the landing's lock wait, whatever the suite hold names (${el}s)" || ko "2798 the pass held the lock ${el}s"
[ "$(filed)" = 0 ] && ok "2798 a suite stopped at its hold files no bug" || ko "2798 a held suite filed $(filed) bugs"
[ -z "$(/bin/ls -A "$ST/auto" 2>/dev/null | $G '^daysuites-2')" ] && ok "2798 a held suite spends no day marker" || ko "2798 a held suite spent the day marker"

# 12) (#3058) The pass hands the suites the project's interpreter, never the bare python3 of its own PATH: a suite runs the
# project's Python code, and the inherited python3 may be an older interpreter with none of the project's dependencies.
# With no interpreter answer (a bare python3), the pass moves nothing.
newworld; tree=$T/w12; mktree "$tree"
mkdir -p "$T/decoy" "$T/co12/.venv/bin"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$(bash -c '. "$1/caps.sh"; sg_gate_python' _ "$d")" > "$T/decoy/python3"; chmod +x "$T/decoy/python3"
printf '#!/bin/sh\necho venv-python\n' > "$T/co12/.venv/bin/python"; chmod +x "$T/co12/.venv/bin/python"
printf '#!/bin/sh\necho venv-python\n' > "$T/co12/.venv/bin/python3"; chmod +x "$T/co12/.venv/bin/python3"
suite "$tree" test-py.sh 'echo "py=$(command -v python3)" >> "$RUNLOG"; exit 0'
commit "$tree"; : > "$T/ran.log"
out=$(run "$tree" SG_CHECKOUT="$T/co12" PATH="$T/decoy:$PATH")
$G -qxF "py=$T/co12/.venv/bin/python3" "$T/ran.log" && ok "3058 a suite runs the project's python3, never the caller's" \
  || ko "3058 a suite ran $(cat "$T/ran.log"): expected $T/co12/.venv/bin/python3, pass said: $out"
newworld; : > "$T/ran.log"
out=$(run "$tree" SG_GATE_PY=python3 PATH="$T/decoy:$PATH")
$G -qxF "py=$T/decoy/python3" "$T/ran.log" && ok "3058 a bare python3 answer moves no suite's interpreter" \
  || ko "3058 the pass moved a suite's python3 with no venv answer: $(cat "$T/ran.log"), pass said: $out"

rm -rf "$T"
[ "$fail" = 0 ] && echo "daysuites: every case green"
exit "$fail"
