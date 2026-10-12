#!/bin/bash
# Regression test for #1410 cause 1: the e2e rebuild and the fork gate's GUI suite share the gui-suite serial lock, so the suite never launches an emulator that is mid-publish. Stub gh/test.sh, throwaway repos. usage: bash test-forkclose-serial.sh [path/to/forkclose.sh]
# Nested fork-gate.sh honors inherited FG_SUITES; a contrib-only outer gate sets FG_SUITES=shell and would skip the GUI command this suite asserts, so the nested calls pin FG_SUITES=full. They pin SG_FORK_PY_CMD too: the full gate runs the pytest suite (#1822) and this suite is about the gui-suite lock, not about pytest.
src=${1:-$(dirname "$0")/forkclose.sh}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}" FG_SUITES; export HOME="$T/home" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b; mkdir -p "$HOME"
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$here/fork-gate.sh" "$here/serial.sh" "$T/s/"; printf "exit 0\n" > "$T/s/shell-suites.sh"; cp $(dirname "$src")/ghread.py "$T/s/"
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo one > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
$G clone -q "$T/fork" "$T/e2e" 2>/dev/null
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_BASE=local/next SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true SG_E2E_BUILD_DIR=%s SG_E2E_BUILD_CMD="echo build-start >> %s/log; sleep 1; echo build-end >> %s/log"\n' "$T/st" "$T/fork" "$T/co" "$T/e2e" "$T" "$T" > "$T/s/env.sh"
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# 1. a GUI suite holds the lock: the rebuild must wait for it
SG_STATE="$T/st" bash "$T/s/serial.sh" gui-suite sh -c "echo gui-start >> $T/log; sleep 4; echo gui-end >> $T/log" & sleep 1
bash "$T/s/forkclose.sh" > "$T/fc.out" 2>&1; wait
[ "$(tr '\n' ' ' < "$T/log")" = "gui-start gui-end build-start build-end " ] && ok "the e2e rebuild waits for a running GUI suite" || ko "rebuild overlapped the GUI suite: $(tr '\n' ' ' < "$T/log")"
# 2. a rebuild holds the lock: the gate's GUI suite must wait for it
: > "$T/log"; SG_STATE="$T/st" bash "$T/s/serial.sh" gui-suite sh -c "echo build-start >> $T/log; sleep 4; echo build-end >> $T/log" & sleep 1
( cd "$T/fork" && FG_SUITES=full SG_FORK_PY_CMD=true SG_STATE="$T/st" bash "$T/s/fork-gate.sh" "echo gui-start >> $T/log" >/dev/null 2>&1 ); wait
[ "$(tr '\n' ' ' < "$T/log")" = "build-start build-end gui-start " ] && ok "the gate's GUI suite waits for a rebuild in progress" || ko "GUI suite ran mid-rebuild: $(tr '\n' ' ' < "$T/log")"
# 3. re-entrancy: a caller already holding gui-suite (an acceptance command) does not deadlock the gate
SERIAL_WAIT_SECONDS=5 SG_STATE="$T/st" bash "$T/s/serial.sh" gui-suite bash -c "cd $T/fork && FG_SUITES=full SG_FORK_PY_CMD=true SERIAL_WAIT_SECONDS=5 SG_STATE=$T/st bash $T/s/fork-gate.sh true" >/dev/null 2>&1 \
  && ok "the gate inside a gui-suite holder does not deadlock" || ko "nested gui-suite lock deadlocked (exit $?)"
# 4. #1414: the headless tree (SG_E2E_HEADLESS_DIR) follows the same base head, fetched and checked out under the same gui-suite lock
$G clone -q "$T/fork" "$T/hl" 2>/dev/null; g checkout -q -b gui/2 local/next; echo two > "$T/fork/two.py"; g add .; g commit -q -m "work 2"; g checkout -q local/next   # a second issue to close: the first was consumed above
echo '{"items":[{"n":2,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
: > "$T/log"; printf 'export SG_E2E_HEADLESS_DIR=%s\n' "$T/hl" >> "$T/s/env.sh"
hl0=$($G -C "$T/hl" rev-parse HEAD)
SG_STATE="$T/st" bash "$T/s/serial.sh" gui-suite sh -c "echo gui-start >> $T/log; sleep 4; echo gui-end >> $T/log" & sleep 1
bash "$T/s/forkclose.sh" > "$T/fc.out" 2>&1 & sleep 2
[ "$($G -C "$T/hl" rev-parse HEAD)" = "$hl0" ] && ok "the headless tree is not touched while a GUI suite holds the lock" || ko "headless tree moved under a running GUI suite"
wait
[ "$($G -C "$T/hl" rev-parse HEAD)" = "$($G -C "$T/fork" rev-parse local/next)" ] && ok "the headless tree ends at the base head" || ko "headless tree did not reach the base head"
rm -rf "$T"; exit $fail
