#!/bin/bash
# Regression test for the watch cycle's lanes (#1845): the cycle must not pay, inside itself, for work that does not have to be
# synchronous, and a step must not run twice when both lanes are up. usage: bash test-watch-cycle.sh [path/to/watch.sh]
# Fixture: a copy of watch.sh in a temp scripts dir with every step stubbed, a `sleep` that returns at once (a cycle then costs its
# steps, not 90 s of naps) and a `gh` that answers nothing. The last step of the cycle (vendor-guard) ends the run after the number of
# cycles the case asked for, so no case waits for the real cadence. No network, no model, no emulator.
# Hermetic first: the live loop exports its own SG_* to every run it dispatches — a dispatch sets SG_SINGLE_<stage> while it re-execs itself
# through single.sh — and a leaked SG_SINGLE_* would let a stub walk past the lock it is supposed to hold. Drop them, as shell-suites.sh
# does for every suite (#1410); the fixture sets what it needs below.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/watch.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; B=$T/bin
# A source not named watch.sh is refused (#2025): a suite given as the source copied itself in and ran itself at every level.
case "$(basename "$src")" in watch.sh) ;; *) echo "$(basename "$0"): $src is not a watch.sh; refused" >&2; exit 2;; esac
mkdir -p "$S" "$ST" "$B" "$T/co" "$T/fork"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log REC_SLEEP=5
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/"; cp "$d/models.json" "$S/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$S/"   # the cycle's reader and its throttle travel with it since #1893
cp "$d/steprec.sh" "$S/"   # the step record writer the cycle sources, as the real scripts folder holds it (#1750)
cp "$d/promotequiet.sh" "$S/"   # the promotion's debounce, sourced before its step (#1837): without it no promotion starts and no case reads one
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001   # every case below is its own GitHub: a list kept, or a cooldown set, by the last case would answer this one (#45)
EOF
printf '#!/bin/bash\nexit 0\n' > "$B/gh"        # nothing to read, nothing to move
printf '#!/bin/bash\n[ -n "$REAL_SLEEP" ] && exec /bin/sleep "$@"\nexit 0\n' > "$B/sleep"   # REAL_SLEEP=1 keeps a stub slow on purpose
chmod +x "$B/gh" "$B/sleep"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
for s in basecheckpass.sh branchland.sh branchreviewpass.sh branchfixpass.sh autohunt.sh heartbeat.sh wtclean.sh janitor.sh startability.sh live-guard.sh; do
  printf '#!/bin/bash\necho %s >> "%s"\n' "${s%.sh}" "$STEPS" > "$S/$s"; chmod +x "$S/$s"; done
# the nightly full suite (#704) blocks for NIGHT_HOLD s on the REAL sleep, never the stubbed one: the case times the cycle
# around it, and a cycle that waited for the suite would be as long as the suite
cat > "$S/nightly.sh" <<EOF
#!/bin/bash
echo nightly >> "$STEPS"
/bin/sleep "\${NIGHT_HOLD:-0}"
exit 0
EOF
chmod +x "$S/nightly.sh"
printf 'open("%s","a").write("childcc\\n")\n' "$STEPS" > "$S/childcc.py"
printf '#!/bin/bash\nexit 0\n' > "$S/promote-main-pass.sh"; chmod +x "$S/promote-main-pass.sh"   # the stg-to-main pass: nothing to do until case 16
# reconcile.py blocks for REC_SLEEP: the case times the cycle around it (a detached pass costs the cycle nothing) and reads its own log
cat > "$S/reconcile.py" <<'PY'
import os, time
time.sleep(float(os.environ.get("REC_SLEEP", "5")))
open(os.environ["STEPS"], "a").write("reconcile\n")
print("MOVE #1 Todo -> Done")   # watch.sh prefixes the pass's stream with BOARD when it files it
PY
# the dispatch passes self-lock exactly as the real autofrontier.sh/autobug.sh do (single.sh <stage> around the whole pass)
for s in autofrontier autobug; do u=$(echo "$s" | tr a-z A-Z)
  cat > "$S/$s.sh" <<EOF
#!/bin/bash
here="\$(cd "\$(dirname "\$0")" && pwd)"
[ -n "\${SG_SINGLE_$u:-}" ] || exec env SG_SINGLE_$u=1 bash "\$here/single.sh" $s bash "\$here/$s.sh" "\$@"
echo "$s" >> "$STEPS"
[ -n "\${AF_HOLD:-}" ] && REAL_SLEEP=1 sleep "\$AF_HOLD"
exit 0
EOF
  chmod +x "$S/$s.sh"; done
# the cycle's last step ends the run: the case asks for N cycles and this counts the cycles the body actually ran (works with or
# without the once-mode, so a RED run against the base terminates too). It reads the supervisor's pid from the file run_cycles
# writes, never the process table: a child session runs under the kernel wall, and /bin/ps there answers EPERM, so a stub that
# read the parent that way never ended a run and every case waited out its bound.
cat > "$S/vendor-guard.sh" <<EOF
#!/bin/bash
echo vendorguard >> "$STEPS"
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && kill -TERM "\$(cat "$T/watchpid" 2>/dev/null)" 2>/dev/null
exit 0
EOF
count() { local c; c=$(/usr/bin/grep -cx "$1" "$STEPS" 2>/dev/null); echo "${c:-0}"; }
waitfor() { local i=0; while [ $i -lt "$2" ]; do /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null && return 0; /bin/sleep 0.1; i=$((i+1)); done; /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null; }
run_cycles() { # run_cycles <n> [VAR=val...]  starts watch.sh; the vendor-guard stub ends it after <n> cycles
  local n=$1; shift; : > "$STEPS"; echo 0 > "$T/cyc"
  env "$@" SG_TEST_CYCLES=$n bash "$S/watch.sh" > "$T/watch.out" 2>&1 & local p=$! i=0
  echo "$p" > "$T/watchpid"
  while kill -0 "$p" 2>/dev/null && [ $i -lt 400 ]; do /bin/sleep 0.1; i=$((i+1)); done
  kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null; rm -f "$ST/fastloop.pid" 2>/dev/null; return 0
}
lane_up() { printf '#!/bin/bash\n/bin/sleep 60\n' > "$T/fakefast.sh"; bash "$T/fakefast.sh" & FPID=$!; echo $FPID > "$ST/fastloop.pid"; }
lane_down() { [ -n "$FPID" ] && kill "$FPID" 2>/dev/null; FPID=; rm -f "$ST/fastloop.pid"; }

# 1) fast lane up: the cycle leaves the dispatch passes to it, so neither pass runs twice
lane_up; run_cycles 1
[ "$(count autofrontier)" = 0 ] && ok "fast lane up: the cycle does not dispatch the frontier" || ko "the cycle dispatched the frontier with the fast lane up ($(count autofrontier) passes)"
[ "$(count autobug)" = 0 ] && ok "fast lane up: the cycle does not dispatch the bugs" || ko "the cycle dispatched the bugs with the fast lane up ($(count autobug) passes)"
[ "$(count childcc)" = 0 ] && ok "fast lane up: the cycle leaves childcc to it" || ko "the cycle ran the fast lane's own steps"

# 2) fast lane down: the standalone cycle still dispatches, once per cycle
lane_down; run_cycles 1
[ "$(count autofrontier)" = 1 ] && ok "fast lane down: the cycle dispatches the frontier" || ko "no frontier dispatch standalone ($(count autofrontier))"
[ "$(count autobug)" = 1 ] && ok "fast lane down: the cycle dispatches the bugs" || ko "no bug dispatch standalone ($(count autobug))"

# 3) wtclean is not a per-cycle step: at most one run in three cycles, while a per-cycle step keeps its cadence
run_cycles 3
[ "$(count wtclean)" = 1 ] && ok "wtclean runs once in three cycles" || ko "wtclean ran $(count wtclean)× in 3 cycles"
for _ in $(seq 1 50); do [ "$(count janitor)" -ge 3 ] && break; /bin/sleep 0.1; done   # the janitor is detached (#1774): its last pass may end after the cycle
[ "$(count janitor)" = 3 ] && ok "a per-cycle step still runs every cycle" || ko "janitor ran $(count janitor)× in 3 cycles"

# 4) reconcile does not hold the cycle, writes its own log, and stays out of cycle.log
t0=$(date +%s); run_cycles 1; el=$(( $(date +%s) - t0 ))
[ "$el" -lt 4 ] && ok "the cycle does not wait for the reconcile pass (${el}s with a 5 s pass)" || ko "the cycle waited for reconcile (${el}s)"
for _ in $(seq 1 150); do /usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && break; /bin/sleep 0.1; done
/usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && ok "the detached pass writes its own log ($ST/reconcile.log)" || ko "no BOARD line in reconcile.log"
/usr/bin/grep -q 'BOARD' "$ST/cycle.log" 2>/dev/null && ko "reconcile output still lands in cycle.log" || ok "cycle.log no longer carries the reconcile pass"

# 5) the fast lane dispatches, and a second pass does not overlap the first
F2=$T/fastscripts; mkdir -p "$F2"; cp "$d/fastloop.sh" "$d/single.sh" "$d/steprec.sh" "$F2/"; cp "$S/env.sh" "$S/childcc.py" "$S/autofrontier.sh" "$S/autobug.sh" "$S/loopbug.sh" "$F2/"
: > "$STEPS"; rm -f "$ST/fastloop.pid"
AF_HOLD=3 SG_FASTLOOP_ONCE=1 bash "$F2/fastloop.sh" > "$T/fast.out" 2>&1
AF_HOLD=3 SG_FASTLOOP_ONCE=1 bash "$F2/fastloop.sh" > "$T/fast2.out" 2>&1
waitfor autofrontier 100; waitfor autobug 100
[ "$(count autofrontier)" = 1 ] && ok "the fast lane dispatches the frontier" || ko "the fast lane dispatched $(count autofrontier) frontier passes"
[ "$(count autobug)" = 1 ] && ok "the fast lane dispatches the bugs" || ko "the fast lane dispatched $(count autobug) bug passes"
[ "$(count childcc)" = 2 ] && ok "the fast lane keeps childcc" || ko "childcc: $(count childcc)"
/bin/sleep 4
[ "$(count autofrontier)" = 1 ] && ok "a pass already running is not started again (single.sh lock)" || ko "the frontier pass overlapped itself ($(count autofrontier))"

# 7) One watch.sh per repository state directory. A live holder refuses another supervisor for the same repository,
# while a separate repository uses its own lock. A holder that is gone is taken over.
WL="$ST/locks/watch"
rm -rf "$WL"; mkdir -p "$WL"; echo $$ > "$WL/pid"          # held by this shell: alive, so the next start must refuse
# Drain what the earlier cases' detached passes still owe: the cycle detaches reconcile, the stub sleeps REC_SLEEP and writes
# when it ends, and a case that ran two cycles leaves two of those. One landed inside this case's window in the full gate at
# c3c6274 (`FAIL 7 the refused start still ran: reconcile`) and read as a refused start that ran a step -- it had not. Wait for
# the step log to go quiet instead of for one line: none of them is this case's (#451).
quiet=0; prev=""; while [ $quiet -lt 15 ]; do cur=$(cat "$STEPS" 2>/dev/null); [ "$cur" = "$prev" ] && quiet=$((quiet+1)) || quiet=0; prev=$cur; /bin/sleep 0.1; done
: > "$STEPS"
env SG_TEST_CYCLES=1 bash "$S/watch.sh" > "$T/lock.out" 2>&1
case "$(cat "$T/lock.out")" in *"watch.sh already running pid=$$"*) ok "7 a second watch.sh on the same path refuses by name";; *) ko "7 the second start was not refused: '$(cat "$T/lock.out")'";; esac
[ ! -s "$STEPS" ] && ok "7 and it read nothing and ran no step" || ko "7 the refused start still ran: $(tr '\n' ' ' < "$STEPS")"
echo 999999 > "$WL/pid"                                    # a holder that is gone: the lock is stale
: > "$STEPS"
env SG_TEST_CYCLES=1 bash "$S/watch.sh" > "$T/takeover.out" 2>&1 & local_p=$! i=0
while kill -0 "$local_p" 2>/dev/null && [ $i -lt 100 ]; do /bin/sleep 0.1; i=$((i+1)); done
kill -TERM "$local_p" 2>/dev/null; wait "$local_p" 2>/dev/null
[ -s "$STEPS" ] && ok "7 a stale lock (its holder is gone) is taken over: the cycle runs" || ko "7 a stale lock stopped the loop: '$(cat "$T/takeover.out")'"
rm -rf "$WL"

# 8) (#152) the cycle reads the live skill's drift. The live copy is what the loop and the owner's info bar run, and it
# follows the repository copy by hand: the cycle runs the live-guard pass, which reads the difference through
# publish-live.sh --check, publishes it and names what it read. A cycle that skips it leaves the copies a day apart in
# silence -- the bar kept printing the footer the repository had dropped.
lane_down; run_cycles 1
# the case asks for one cycle, and the seam that ends it is the supervisor's pid the fixture recorded -- never a read of
# the process table, which the kernel wall hides from a child session (#1855). A seam that does not reach the supervisor
# leaves the run going until the case's bound, so the cycle count is the seam's own proof.
[ "$(count vendorguard)" = 1 ] && ok "8 one asked-for cycle ends through the pid seam after one cycle" || ko "8 one asked-for cycle ran $(count vendorguard) cycles: the seam that ends it did not reach the supervisor"
[ "$(count live-guard)" = 1 ] && ok "8 the cycle runs the live-guard pass once" || ko "8 the cycle ran $(count live-guard) live-guard pass(es)"[ ! -e "$ST/locks/single-liveguard" ] && ok "8 the pass's single-flight lock is released when it ends" || ko "8 the live-guard lock stayed behind"
run_cycles 2
[ "$(count live-guard)" = 2 ] && ok "8 a per-cycle step keeps its cadence (two cycles, two reads)" || ko "8 live-guard ran $(count live-guard)× in 2 cycles"

# 9) (#450) the stall watchdog names a live run whose record has not moved, and never names a finished one: a run whose record
# carries its verdict is over, and it waits for its wave to land (childcc keeps it in active-runs.txt until then), so a
# quiet record is not a hang to report. Both records are backdated past stall_timeout_minutes, so only the verdict differs.
mkdir -p "$T/co/runs/run-live" "$T/co/runs/run-done"
printf '{"event": "call_started"}\n' > "$T/co/runs/run-live/record.jsonl"
printf '{"event": "verdict", "verdict": "pass"}\n' > "$T/co/runs/run-done/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-live/record.jsonl" "$T/co/runs/run-done/record.jsonl"
printf '%s\n%s\n' "$T/co/runs/run-live" "$T/co/runs/run-done" > "$ST/active-runs.txt"
run_cycles 1
/usr/bin/grep -q "STALL RUN run-live" "$T/watch.out" && ok "9 a live run whose record stopped is named" || ko "9 no STALL line for a live run: $(tail -3 "$T/watch.out")"
/usr/bin/grep -q "STALL RUN run-done" "$T/watch.out" && ko "9 the watchdog names a finished run as stalled" || ok "9 a run whose record holds its verdict is never named"
: > "$ST/active-runs.txt"

# 10) (#569) the cycle's closing wait is one wait of at most SG_WATCH_WAIT s that ends early on a NEW run verdict in the run
# folders, read from local files only. The stubbed `sleep` returns at once, so each case keeps the real one (REAL_SLEEP=1) and
# runs two cycles: the wait closes the first, and the vendor-guard stub ends the run at the second one's end. A verdict a run
# already carried when the wait began is no event, so a run whose landing failed and stays unread cannot keep the cycle spinning.
lane_down; mkdir -p "$T/co/runs/run-wait"; printf '{"event": "call_started"}\n' > "$T/co/runs/run-wait/record.jsonl"
echo "$T/co/runs/run-wait" > "$ST/active-runs.txt"; rm -f "$ST/cycle.log" "$ST/watch-seen.txt"
( /bin/sleep 2; printf '{"event": "verdict", "verdict": "pass"}\n' >> "$T/co/runs/run-wait/record.jsonl" ) & vp=$!
t0=$(date +%s); run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=40 SG_WATCH_POLL=1; el=$(( $(date +%s) - t0 )); wait "$vp" 2>/dev/null
[ "$el" -lt 20 ] && ok "10 a new run verdict ends the wait early (${el}s of a 40 s wait)" || ko "10 the wait ran on after a verdict (${el}s)"
/usr/bin/grep -q "RUN run-wait verdict=pass" "$T/watch.out" && ok "10 the next cycle reads the verdict that ended the wait" || ko "10 no verdict line: $(tail -3 "$T/watch.out")"
/usr/bin/grep -Eq ' CYCLE [0-9]+s$' "$ST/cycle.log" && ok "10 the cycle log keeps its CYCLE timing line" || ko "10 no CYCLE line in cycle.log: $(cat "$ST/cycle.log" 2>/dev/null)"
printf 'seen %s\n' "$T/co/runs/run-wait" > "$ST/watch-seen.txt"   # the run carries its verdict and is read: no event for the wait
t0=$(date +%s); run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=4 SG_WATCH_POLL=1; el=$(( $(date +%s) - t0 ))
[ "$el" -ge 4 ] && ok "10 with no new verdict the wait runs to its bound (${el}s of a 4 s wait)" || ko "10 the wait ended early with no event (${el}s)"
printf '#!/bin/bash\n[ "$1" = --stretch ] && echo 3\n' > "$B/ghsnap"; chmod +x "$B/ghsnap"; rm -f "$ST/cycle.log"
t0=$(date +%s); run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=2 SG_WATCH_POLL=1 SG_GHSNAP="$B/ghsnap"; el=$(( $(date +%s) - t0 ))
[ "$el" -ge 5 ] && /usr/bin/grep -q "QUOTA low: this cycle waits 3s more" "$ST/cycle.log" && ok "10 the quota stretch still follows the wait (${el}s)" || ko "10 no quota stretch after the wait (${el}s): $(cat "$ST/cycle.log" 2>/dev/null)"
# the inline childcc pass stays inside the wait with the fast lane down: one at the top and one at each third of the wait; lane up, none
run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=6 SG_WATCH_POLL=1
[ "$(count childcc)" = 4 ] && ok "10 fast lane down: childcc runs at the top and at the wait's two thirds" || ko "10 childcc ran $(count childcc)× in 2 cycles (4 expected)"
lane_up; run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=6 SG_WATCH_POLL=1; lane_down
[ "$(count childcc)" = 0 ] && ok "10 fast lane up: the wait runs no childcc pass" || ko "10 childcc ran $(count childcc)× inside the wait with the lane up"
: > "$ST/active-runs.txt"
# a verdict written by a step AFTER the top-of-cycle read loop still ends the wait: the baseline is taken at the read loop, not after the steps
mkdir -p "$T/co/runs/run-late"; printf '{"event": "call_started"}\n' > "$T/co/runs/run-late/record.jsonl"; echo "$T/co/runs/run-late" > "$ST/active-runs.txt"
cat > "$S/janitor.sh" <<EOF
#!/bin/bash
echo janitor >> "$STEPS"
echo '{"event": "verdict", "verdict": "pass"}' >> "$T/co/runs/run-late/record.jsonl"
EOF
rm -f "$ST/watch-seen.txt" "$ST/cycle.log"; run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=6 SG_WATCH_POLL=1
/usr/bin/grep -q "WAIT ended early: a run verdict" "$ST/cycle.log" && ok "11 a verdict written by a step after the read loop ends the wait" || ko "11 the wait ran on after a late verdict: $(cat "$ST/cycle.log" 2>/dev/null)"
printf '#!/bin/bash\necho janitor >> "%s"\n' "$STEPS" > "$S/janitor.sh"; : > "$ST/active-runs.txt"
# the inline childcc points are a third and two thirds of the wait, whatever the poll: a wait of 8 polls is no multiple of 3
lane_down; run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=8 SG_WATCH_POLL=1
[ "$(count childcc)" = 4 ] && ok "11 a wait that is no multiple of 3 polls still runs childcc twice inline" || ko "11 childcc ran $(count childcc)× in 2 cycles (4 expected)"
# a fractional poll does not break the wait
t0=$(date +%s); run_cycles 2 REAL_SLEEP=1 SG_WATCH_WAIT=2 SG_WATCH_POLL=0.5; el=$(( $(date +%s) - t0 ))
[ "$el" -ge 2 ] && [ "$el" -lt 10 ] && ok "11 a fractional SG_WATCH_POLL still waits its bound (${el}s)" || ko "11 fractional poll: ${el}s of a 2 s wait"

# 12) (#704) the full suite runs nightly in both modes, and a suite of hours never holds the cycle: the step is detached under
# its own single-flight stage, so the cycle starts it and goes on. Its own once-a-day rule and its own lock are nightly.sh's.
lane_down; t0=$(date +%s); run_cycles 1 NIGHT_HOLD=3; el=$(( $(date +%s) - t0 ))
[ "$(count nightly)" = 1 ] && ok "12 the cycle starts the nightly full suite" || ko "12 the cycle started $(count nightly) nightly pass(es)"
[ "$el" -lt 3 ] && ok "12 the cycle does not wait for the nightly suite (${el}s with a 3 s suite)" || ko "12 the cycle waited for the nightly (${el}s)"
for _ in $(seq 1 100); do [ -d "$ST/locks/single-nightly" ] || break; /bin/sleep 0.1; done   # the previous case's suite drains
: > "$STEPS"; run_cycles 2 NIGHT_HOLD=15   # the hold outlasts both cycles on a loaded box: the claim is the lock, not the clock
[ "$(count nightly)" = 1 ] && ok "12 a suite still running is not started again (single.sh lock)" || ko "12 the nightly overlapped itself ($(count nightly) passes)"
# 13) the stall bound is an integer find can read. A models.json that leaves stall_timeout_minutes out, or sets it null, used to
# reach the watchdog as the literal string "null": find rejected `-mmin +null` as an illegal value with its stderr dropped, so no
# run was ever named stalled. In production every review ended BLOCKED while the run retried once a minute. The fallback is the
# shipped 15 m; a key that IS set still wins, which says the fallback is a floor and not a hardcoded 15.
lane_down; mkdir -p "$T/co/runs/run-nokey" "$T/co/runs/run-null" "$T/co/runs/run-set"
for c in nokey null set; do printf '{"event": "call_started"}\n' > "$T/co/runs/run-$c/record.jsonl"
  touch -t 202001010000 "$T/co/runs/run-$c/record.jsonl"; echo "$T/co/runs/run-$c" >> "$ST/active-runs.txt"; done
printf '{"review_stall_minutes": 60}\n' > "$S/models.json"      # the key absent
rm -f "$ST/watch-seen.txt"; run_cycles 1
/usr/bin/grep -q "STALL RUN run-nokey no record.jsonl progress for 15m" "$T/watch.out" && ok "13 a keyless models.json falls back to the 15 m default" || ko "13 no stall line for a keyless models.json: $(tail -3 "$T/watch.out")"
/usr/bin/grep -q "progress for nullm" "$T/watch.out" && ko "13 the word null reached the stall line" || ok "13 the stall line carries a number, never the string null"
printf '{"stall_timeout_minutes": null}\n' > "$S/models.json"   # the key explicitly null
rm -f "$ST/watch-seen.txt"; run_cycles 1
/usr/bin/grep -q "STALL RUN run-null no record.jsonl progress for 15m" "$T/watch.out" && ok "13 an explicit null falls back to the 15 m default" || ko "13 no stall line for a null stall_timeout_minutes: $(tail -3 "$T/watch.out")"
printf '{"stall_timeout_minutes": 3}\n' > "$S/models.json"      # a configured value still wins
rm -f "$ST/watch-seen.txt"; run_cycles 1
/usr/bin/grep -q "STALL RUN run-set no record.jsonl progress for 3m" "$T/watch.out" && ok "13 a configured stall_timeout_minutes still wins (3m)" || ko "13 the configured value was ignored: $(tail -3 "$T/watch.out")"
cp "$d/models.json" "$S/"; : > "$ST/active-runs.txt"; rm -f "$ST/watch-seen.txt"
# 13c) a configured 0 is a real bound, never the fallback: jq's `//` must not swallow it
printf '{"stall_timeout_minutes": 0}\n' > "$S/models.json"
echo "$T/co/runs/run-set" >> "$ST/active-runs.txt"   # the case above emptied the list
rm -f "$ST/watch-seen.txt"; run_cycles 1
/usr/bin/grep -q "STALL RUN run-set no record.jsonl progress for 0m" "$T/watch.out" && ok "13c a configured 0 stays 0, the 15 m fallback does not fire" || ko "13c zero bound: $(tail -3 "$T/watch.out")"

# 14) (#790) The base's own check is read once per cycle, with or without a landing: the cycle runs basecheckpass once.
rm -f "$ST/fastloop.pid"; : > "$ST/active-runs.txt"; run_cycles 1
waitfor basecheckpass 30
[ "$(count basecheckpass)" = 1 ] && ok "14 the cycle reads the base's check once, with nothing to land" || ko "14 the base's check ran $(count basecheckpass) times in one cycle"

# 15) (#1462) The cycle promotes the proven dev tip by itself: one promotion step, detached under its own single-flight lock
# (`single.sh promote`), with the CI proof, and its own quiet window so a burst of landings earns one proof window instead of
# one pass per landing. The pass itself is a stub here; it records the proof it was given in its own file, so a detached line
# never lands in $STEPS after a later case cleared it. The lock's pid is this shell while a "pass is in flight", so the skip is
# the real single.sh answer, not a fixture.
cat > "$S/promote-pass.sh" <<EOF
#!/bin/bash
echo "promote \${SG_PROMOTE_PROOF:-unset}" >> "$T/promote.steps"
exit 0
EOF
chmod +x "$S/promote-pass.sh"
PROM=$T/promote.steps; pcount() { local c; c=$(/usr/bin/grep -cx "$1" "$PROM" 2>/dev/null); echo "${c:-0}"; }
pwait() { local i=0; while [ $i -lt 100 ]; do /usr/bin/grep -qx "$1" "$PROM" 2>/dev/null && return 0; /bin/sleep 0.1; i=$((i+1)); done; return 1; }
lane_down; rm -f "$ST/landed.tsv" "$ST/cycle.log"; rm -rf "$ST/locks/single-promote"; : > "$PROM"
run_cycles 1; pwait "promote ci"
[ "$(pcount "promote ci")" = 1 ] && ok "15 the cycle runs one promotion step with the CI proof" || ko "15 the promotion step ran $(pcount 'promote ci')×, proof '$(tr '\n' ' ' < "$PROM")'"
/usr/bin/grep -q "PROMOTE pass rc=0" "$ST/cycle.log" 2>/dev/null && ok "15 the step writes its own line to cycle.log" || ko "15 no PROMOTE line in cycle.log: $(cat "$ST/cycle.log" 2>/dev/null)"
# a pass in flight holds the lock: the next cycle starts no second one, waits for nothing, and names the holder. The holder
# is real -- single.sh itself, around a sleeper -- so the skip is single.sh's own answer, never a fabricated pid file.
: > "$PROM"; : > "$ST/cycle.log"; rm -f "$ST/promote-defer"; rm -rf "$ST/locks/single-promote"
SG_STATE=$ST bash "$S/single.sh" promote /bin/sleep 5 & hp=$!
for _ in $(seq 1 50); do [ -s "$ST/locks/single-promote/pid" ] && break; /bin/sleep 0.1; done
run_cycles 1; /bin/sleep 0.3
[ "$(pcount "promote ci")" = 0 ] && ok "15 a pass in flight is skipped, never doubled" || ko "15 the step ran beside a held pass ($(pcount 'promote ci'))"
/usr/bin/grep -q "PROMOTE waits" "$ST/cycle.log" 2>/dev/null && ok "15 the skip names the pass that holds the lock" || ko "15 no PROMOTE waits line in cycle.log"
kill -TERM "$hp" 2>/dev/null; wait "$hp" 2>/dev/null; rm -rf "$ST/locks/single-promote"
# the debounce: a burst of landings earns one proof window, and the window closes after the last landing of the burst
: > "$PROM"; : > "$ST/cycle.log"; rm -f "$ST/promote-defer"
now=$(date +%s); for i in 1 2 3 4; do printf '%s\twave\n' "$(( now - i ))"; done > "$ST/landed.tsv"
run_cycles 1; /bin/sleep 0.3; run_cycles 1; /bin/sleep 0.3
[ "$(pcount "promote ci")" = 0 ] && ok "15 a burst of landings inside the quiet window runs no pass" || ko "15 the burst ran $(pcount 'promote ci') pass(es)"
for i in 1 2 3 4; do printf '%s\twave\n' "$(( now - 1000 - i ))"; done > "$ST/landed.tsv"
run_cycles 1 SG_PROMOTE_QUIET_SECONDS=900; pwait "promote ci"
[ "$(pcount "promote ci")" = 1 ] && ok "15 past the quiet window the burst earns exactly one pass" || ko "15 the burst earned $(pcount 'promote ci') passes"
# a stream of landings that never quiets postpones the proof by the deferral bound and no longer: the first blocked cycle
# stamps the bound, and the step starts once the stamp is older than SG_PROMOTE_MAX_DEFER, even with a fresh landing.
: > "$PROM"; rm -f "$ST/promote-defer"
printf '%s\twave\n' "$(date +%s)" > "$ST/landed.tsv"; run_cycles 1
for _ in $(seq 1 50); do [ -e "$ST/promote-defer" ] && break; /bin/sleep 0.1; done   # the step's own pass is detached: wait for its stamp
[ "$(pcount "promote ci")" = 0 ] && [ -e "$ST/promote-defer" ] && ok "15 the first blocked cycle stamps the deferral bound" || ko "15 no deferral stamp: '$(ls "$ST" | tr '\n' ' ')' landed '$(cat "$ST/landed.tsv" 2>/dev/null)'"
printf '%s\twave\n' "$(date +%s)" > "$ST/landed.tsv"; run_cycles 1 SG_PROMOTE_QUIET_SECONDS=900 SG_PROMOTE_MAX_DEFER=1800; /bin/sleep 0.3
[ "$(pcount "promote ci")" = 0 ] && ok "15 a continuing stream inside the bound still starts no pass" || ko "15 the stream ran a pass before the bound"
touch -t 202001010000 "$ST/promote-defer"   # the stamp is older than the bound
printf '%s\twave\n' "$(date +%s)" > "$ST/landed.tsv"; run_cycles 1 SG_PROMOTE_QUIET_SECONDS=900 SG_PROMOTE_MAX_DEFER=1800; pwait "promote ci"
[ "$(pcount "promote ci")" = 1 ] && ok "15 past the deferral bound a continuing stream still promotes once" || ko "15 the stream starved the pass ($(pcount 'promote ci'); steps '$(tr '\n' ' ' < "$PROM")'; cycle.log '$(tr '\n' '|' < "$ST/cycle.log" 2>/dev/null)'; stamp '$(cat "$ST/promote-defer" 2>/dev/null)' landed '$(tr '\n' '|' < "$ST/landed.tsv" 2>/dev/null)')"
rm -f "$ST/landed.tsv" "$ST/promote-defer"

# 16) (ADR-0173) The cycle runs the stg-to-main pass by itself: one step per cycle, detached under its own single-flight lock
# (`single.sh promote-main`) and outside the promotion's quiet window, so a burst of landings never postpones the read of a
# complete tier that is already in flight. The pass is a stub; it writes its own file, as the promotion stub does.
cat > "$S/promote-main-pass.sh" <<EOF
#!/bin/bash
echo "main" >> "$T/promote-main.steps"
exit \${PM_RC:-0}
EOF
chmod +x "$S/promote-main-pass.sh"
MAINP=$T/promote-main.steps; mcount() { local c; c=$(/usr/bin/grep -cx main "$MAINP" 2>/dev/null); echo "${c:-0}"; }
mwait() { local i=0; while [ $i -lt 100 ]; do [ "$(mcount)" -ge "$1" ] && return 0; /bin/sleep 0.1; i=$((i+1)); done; return 1; }
lane_down; : > "$MAINP"; : > "$PROM"; : > "$ST/cycle.log"; rm -f "$ST/promote-defer"; rm -rf "$ST/locks/single-promote-main"
printf '%s\twave\n' "$(date +%s)" > "$ST/landed.tsv"   # a landing inside the quiet window: the dev-to-stg step waits
run_cycles 1 SG_PROMOTE_QUIET_SECONDS=900; mwait 1
[ "$(mcount)" = 1 ] && [ "$(pcount "promote ci")" = 0 ] && ok "16 the cycle runs the stg-to-main pass once, inside the quiet window that holds the stg promotion" || ko "16 the stg-to-main pass ran $(mcount)x, the stg promotion $(pcount 'promote ci')x"
/usr/bin/grep -q "PROMOTE-MAIN" "$ST/cycle.log" 2>/dev/null && ko "16 a quiet pass wrote a cycle.log line: $(/usr/bin/grep PROMOTE-MAIN "$ST/cycle.log")" || ok "16 a pass with nothing to say writes no cycle.log line"
: > "$MAINP"; : > "$ST/cycle.log"; run_cycles 1 PM_RC=1 SG_PROMOTE_QUIET_SECONDS=900; mwait 1
for _ in $(seq 1 50); do /usr/bin/grep -q "PROMOTE-MAIN pass rc=1" "$ST/cycle.log" 2>/dev/null && break; /bin/sleep 0.1; done
/usr/bin/grep -q "PROMOTE-MAIN pass rc=1" "$ST/cycle.log" 2>/dev/null && ok "16 a pass that held main writes its outcome to cycle.log" || ko "16 no PROMOTE-MAIN line: $(cat "$ST/cycle.log" 2>/dev/null)"
: > "$MAINP"; rm -rf "$ST/locks/single-promote-main"
SG_STATE=$ST bash "$S/single.sh" promote-main /bin/sleep 5 & hp=$!
for _ in $(seq 1 50); do [ -s "$ST/locks/single-promote-main/pid" ] && break; /bin/sleep 0.1; done
run_cycles 1 SG_PROMOTE_QUIET_SECONDS=900; /bin/sleep 0.3
[ "$(mcount)" = 0 ] && ok "16 a stg-to-main pass in flight is skipped, never doubled" || ko "16 the step ran beside a held pass ($(mcount))"
kill -TERM "$hp" 2>/dev/null; wait "$hp" 2>/dev/null; rm -rf "$ST/locks/single-promote-main"; rm -f "$ST/landed.tsv" "$ST/promote-defer"

# 17) (#1774) the janitor never holds the cycle: it runs detached under its own single-flight lock, as the promotion does.
# A pass slower than the cycle lets the cycle write CYCLE and go on; the next cycle starts no second pass, names the holder
# once, and the pass writes its own time to cycle.log when it ends. The stub sleeps on the real sleep for JAN_HOLD seconds.
cat > "$S/janitor.sh" <<EOF
#!/bin/bash
echo janitor >> "$STEPS"
/bin/sleep "\${JAN_HOLD:-0}"
exit 0
EOF
chmod +x "$S/janitor.sh"
lane_down; rm -rf "$ST/locks/single-janitor"; : > "$ST/cycle.log"; : > "$ST/active-runs.txt"
t0=$(date +%s); run_cycles 2 JAN_HOLD=8; el=$(( $(date +%s) - t0 ))
[ "$el" -lt 8 ] && ok "17 the cycle does not wait for a slow janitor pass (${el}s for two cycles beside an 8 s pass)" || ko "17 the cycle waited for the janitor (${el}s)"   # the bound is the work it observes, the pass's own 8 s hold: a one-second margin made the case red at load 70 on a cycle that never waited, so it fails only when two cycles outlast one hold
/usr/bin/grep -Eq ' CYCLE [0-9]+s$' "$ST/cycle.log" && ! /usr/bin/grep -Eq ' janitor.sh [0-9]+s$' "$ST/cycle.log" && ok "17 the cycle wrote CYCLE while the pass still ran" || ko "17 cycle.log: $(tr '\n' '|' < "$ST/cycle.log")"
[ "$(count janitor)" = 1 ] && ok "17 a pass in flight is never doubled" || ko "17 the janitor ran $(count janitor)x in two cycles beside a held pass"
/usr/bin/grep -q "JANITOR skipped: a pass in flight holds the janitor lock (pid [0-9]" "$ST/cycle.log" && ok "17 the skip names the holder" || ko "17 no JANITOR skipped line: $(tr '\n' '|' < "$ST/cycle.log")"
for _ in $(seq 1 120); do [ -d "$ST/locks/single-janitor" ] || break; /bin/sleep 0.1; done
for _ in $(seq 1 30); do /usr/bin/grep -Eq ' janitor.sh [0-9]+s$' "$ST/cycle.log" && break; /bin/sleep 0.1; done
/usr/bin/grep -Eq ' janitor.sh [0-9]+s$' "$ST/cycle.log" && ok "17 the pass writes its own time when it ends" || ko "17 no janitor.sh time line: $(tr '\n' '|' < "$ST/cycle.log")"
printf '#!/bin/bash\necho janitor >> "%s"\n' "$STEPS" > "$S/janitor.sh"; rm -rf "$ST/locks/single-janitor"

# 19) (#1750) Both lanes write one dated record for every step they start, in $ST/steps.tsv, whatever the step's length: a
# start line, then a finish line carrying the state (`done` with the exit status, or `skipped` with the reason). A step under
# 5 s takes no line in cycle.log, and it still takes its record -- the log says where the cycle went, the records say what ran
# at all. A pass the fast lane starts detached writes its start at the fork and its finish at its real end, so a pass still in
# flight holds a start with no finish. cycle.log keeps its own shape (case 10 reads its CYCLE line).
recs() { awk -F'\t' -v k="$1" -v n="$2" '$1 == k && $3 == n { c++ } END { print c + 0 }' "$ST/steps.tsv" 2>/dev/null; }
lane_down; rm -f "$ST/steps.tsv" "$ST/cycle.log"; run_cycles 1
[ "$(recs start heartbeat.sh)" = 1 ] && ok "19 a step under 5 s writes its start record" || ko "19 heartbeat.sh starts: $(recs start heartbeat.sh) in '$(cat "$ST/steps.tsv" 2>/dev/null | tr '\n' '|')'"
awk -F'\t' '$1 == "start" && $3 == "heartbeat.sh" { s = $4 } $1 == "end" && $3 == "heartbeat.sh" { e = $4; st = $5; d = $6 }
  END { exit !(s ~ /^[0-9]+$/ && e ~ /^[0-9]+$/ && e >= s && st == "done" && d == "0") }' "$ST/steps.tsv" 2>/dev/null \
  && ok "19 its finish carries the state done, exit status 0, and an epoch after its start" || ko "19 heartbeat.sh records: $(/usr/bin/grep heartbeat.sh "$ST/steps.tsv" 2>/dev/null | tr '\n' '|')"
/usr/bin/grep -q heartbeat "$ST/cycle.log" 2>/dev/null && ko "19 a step under 5 s took a cycle.log line" || ok "19 cycle.log keeps its 5 s rule, so the record is the only account of that step"
# a pass the fast lane starts detached: its start lands before the pass ends, and its finish lands when it really ends
rm -f "$ST/steps.tsv"; : > "$STEPS"; F3=$T/fastrec; mkdir -p "$F3"
cp "$d/fastloop.sh" "$d/single.sh" "$d/steprec.sh" "$F3/"; cp "$S/env.sh" "$S/childcc.py" "$S/autofrontier.sh" "$S/autobug.sh" "$F3/"
rm -f "$ST/fastloop.pid"
AF_HOLD=5 SG_FASTLOOP_ONCE=1 bash "$F3/fastloop.sh" > "$T/fastrec.out" 2>&1 & rp=$!
for _ in $(seq 1 150); do [ "$(recs start autofrontier)" = 1 ] && break; /bin/sleep 0.1; done
[ "$(recs start autofrontier)" = 1 ] && [ "$(recs end autofrontier)" = 0 ] \
  && ok "19 a detached pass in flight holds a start and no finish, so it reads as running" \
  || ko "19 autofrontier records while it holds: '$(cat "$ST/steps.tsv" 2>/dev/null | tr '\n' '|')'"
wait "$rp" 2>/dev/null; kill "$rp" 2>/dev/null
for _ in $(seq 1 150); do [ "$(recs end autofrontier)" = 1 ] && break; /bin/sleep 0.1; done
awk -F'\t' '$1 == "end" && $3 == "autofrontier" && $5 == "done" && $6 == "0" { c++ } END { exit !(c == 1) }' "$ST/steps.tsv" 2>/dev/null \
  && ok "19 the detached pass writes its finish at its real end" || ko "19 no done record for the detached pass: '$(cat "$ST/steps.tsv" 2>/dev/null | tr '\n' '|')'"
[ "$(recs start childcc)" = 1 ] && [ "$(recs end childcc)" = 1 ] && ok "19 the lane's childcc pass writes its pair" || ko "19 childcc records: $(recs start childcc)/$(recs end childcc)"
# the file is cut by size, so it never grows without bound: a cap the cycle's own steps pass in one run trims it to its
# newest half, and the records it keeps are still whole lines
rm -f "$ST/steps.tsv"; run_cycles 1 SG_STEPREC_MAX=200
for _ in $(seq 1 50); do [ "$(wc -c < "$ST/steps.tsv" 2>/dev/null || echo 0)" -le 200 ] && break; /bin/sleep 0.1; done
sz=$(wc -c < "$ST/steps.tsv" 2>/dev/null || echo 0)
[ "$sz" -le 200 ] && [ "$sz" -gt 0 ] && ok "19 a file past its cap is cut by size (${sz} bytes held)" || ko "19 the step file grew to ${sz} bytes past a 200 byte cap"
# The case started a cycle, and a cycle leaves passes detached behind it. Wait until no process of this fixture is alive, so
# nothing this case started is still there when case 18 asks whether a process of this fixture outlived the suite (#527).
for _ in $(seq 1 60); do [ -z "$(/usr/bin/pgrep -f "$S/" 2>/dev/null)" ] && break; /bin/sleep 0.1; done

# 18) No process of this fixture outlives the suite. A step that outlived its stub kept watch.sh alive and orphaned, and a
# leaked lane ran full cycles against this temp state for up to 41 minutes, burning CPU and GitHub calls until the owner
# reaped it by hand. The reap names this fixture's own scripts folder, so no other suite and no live loop can be reached.
leaked=$(/usr/bin/pgrep -f "$S/" 2>/dev/null)
if [ -n "$leaked" ]; then
  ko "18 a process of this fixture outlived the suite: $(echo "$leaked" | wc -l | tr -d ' ') alive, first: $(/usr/bin/pgrep -fl "$S/" | head -1)"
  /usr/bin/pkill -KILL -f "$S/" 2>/dev/null   # the machine is still protected: the case reports the leak, the reap ends it
else ok "18 no process of this fixture outlived the suite"; fi
rm -rf "$T"; exit $fail
