#!/bin/bash
# A card in Done over a branch the base never took is a false delivery (#97).
#
# The run's own `land` event says the run finished; the base takes the merge only when the close pass lands the branch. The
# janitor reads the run's landing, so the card can sit in Done while gui/<n> is still one commit ahead of the base -- and
# this pass selected its candidates from the card column (Code Complete), so it never looked at that branch. Measured
# 2026-10-13: #89, #93, #94 and #96 sat in Done with gui/89, gui/93, gui/94 and gui/96 one commit ahead of main and no
# merge in the history, and this pass named none of them for over an hour.
#
# The card is state THIS script owns (In Progress on a red, Done on a green): derived, never the gate. A branch whose run
# recorded its verdict is the state that run delivered, so the next pass lands it whatever column the card sits in -- and
# the card is written Done only once the merge is in the base. Stub gh and board.sh, a temp fork repository, a temp state
# dir.
# usage: bash test-forkclose-done-card.sh [forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD   # the real e2e checkouts of the caller must never be touched by a stub run
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"
export HOME="$T/home"; mkdir -p "$HOME"   # serial.sh's durable GUI kill switch is $HOME/.squad-gui-tests-off, so a private home keeps the machine's switch out of this fixture's gate decision
cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf 'exit 0\n' > "$T/s/shell-suites.sh"; cp "$(dirname "$src")/ghread.py" "$T/s/"; cp "$(dirname "$src")/ghthrottle.py" "$(dirname "$src")/ghcall.sh" "$T/s/"
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
# The board the pass writes: it records every write, and whether the branch was still ahead of the base when the column was
# set. `Done` over a branch the base never took is the false delivery this suite is about, so the stub answers it here
# instead of leaving it to the eye.
cat > "$T/s/board.sh" <<'EOS'
#!/bin/bash
if [ "$3" = Done ]; then
  a=$(/usr/bin/git -C "$SG_FORK_DIR" rev-list --count "$SG_FORK_BASE..gui/$4" 2>/dev/null)
  [ "${a:-1}" = 0 ] && echo "DONE-AFTER-LAND $4" >> "$SG_STATE/board.calls" || echo "DONE-OVER-UNLANDED-BRANCH $4" >> "$SG_STATE/board.calls"
fi
echo "$*" >> "$SG_STATE/board.calls"
EOS
chmod +x "$T/s/board.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=%s/s/board.sh\nsg_cq_file() { echo "$SG_STATE/cq/$1"; }\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo "the run's work" > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
# The card the janitor wrote over the run's own landing: Done, while gui/1 is still ahead of the base. The run that
# delivered the branch is over -- its record carries a verdict -- and its request names the issue and the branch.
echo '{"items":[{"n":1,"st":"Done","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
mkdir -p "$T/co/runs/run-1"
printf 'CHILD - GitHub issue(s) 1: work only in worktrees/child/gui-1 on branch gui/1.\n' > "$T/co/runs/run-1/request.txt"
printf '{"event":"run_start","run_id":"run-1"}\n{"event":"land","outcome":"pass"}\n{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n' > "$T/co/runs/run-1/record.jsonl"
[ "$(g rev-list --count local/next..gui/1)" = 1 ] && [ ! -e "$T/fork/one.py" ] && ok "setup: the card is Done over gui/1, one commit ahead, with no merge in the base" || ko "setup wrong"
out=$(bash "$T/s/forkclose.sh" 2>&1); [ -n "${FC_VERBOSE:-}" ] && echo "$out"
bc() { cat "$T/st/board.calls" 2>/dev/null; }
[ "$(g rev-list --count local/next..gui/1)" = 0 ] && ok "the branch the card was Done over is landed, whatever column the card sits in" || ko "gui/1 is still $(( $(g rev-list --count local/next..gui/1) )) commit(s) ahead of local/next: $out"
[ -e "$T/fork/one.py" ] && ok "and the work itself is on the base" || ko "the landing wrote nothing: $(g show --stat --oneline -1 local/next)"
case "$out" in *"FORKCLOSE CARD #1"*) ok "the pass names the branch it lands over the card's column" ;; *) ko "no FORKCLOSE CARD line: $out";; esac
case "$(bc)" in *"DONE-OVER-UNLANDED-BRANCH"*) ko "the card was moved to Done over a branch the base never took: $(bc)";; *) ok "a card whose branch is ahead of the base is never Done" ;; esac
case "$(bc)" in *"DONE-AFTER-LAND 1"*) ok "the card is Done once the merge is in the base" ;; *) ko "no Done write after the merge: $(bc)";; esac
[ -z "$(g status --porcelain)" ] && ok "the pass left the checkout clean" || ko "dirty checkout: $(g status --porcelain)"
rm -rf "$T"
[ "$fail" = 0 ] && echo "test-forkclose-done-card: PASS" || echo "test-forkclose-done-card: FAIL"
exit "$fail"
