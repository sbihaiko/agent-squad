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
mkdir -p "$S" "$ST" "$B" "$T/co" "$T/fork"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log REC_SLEEP=5
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/"; cp "$d/models.json" "$S/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$S/"   # the cycle's reader and its throttle travel with it since #1893
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001   # every case below is its own GitHub: a list kept, or a cooldown set, by the last case would answer this one (#45)
EOF
printf '#!/bin/bash\nexit 0\n' > "$B/gh"        # nothing to read, nothing to move
printf '#!/bin/bash\n[ -n "$REAL_SLEEP" ] && exec /bin/sleep "$@"\nexit 0\n' > "$B/sleep"   # REAL_SLEEP=1 keeps a stub slow on purpose
chmod +x "$B/gh" "$B/sleep"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
for s in branchland.sh branchreviewpass.sh branchfixpass.sh autohunt.sh heartbeat.sh wtclean.sh janitor.sh startability.sh live-guard.sh; do
  printf '#!/bin/bash\necho %s >> "%s"\n' "${s%.sh}" "$STEPS" > "$S/$s"; chmod +x "$S/$s"; done
printf 'open("%s","a").write("childcc\\n")\n' "$STEPS" > "$S/childcc.py"
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
# without the once-mode, so a RED run against the base terminates too)
cat > "$S/vendor-guard.sh" <<EOF
#!/bin/bash
echo vendorguard >> "$STEPS"
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && kill -TERM "\$(/bin/ps -o ppid= -p "\$PPID" | tr -d ' ')" 2>/dev/null
exit 0
EOF
count() { local c; c=$(/usr/bin/grep -cx "$1" "$STEPS" 2>/dev/null); echo "${c:-0}"; }
waitfor() { local i=0; while [ $i -lt "$2" ]; do /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null && return 0; /bin/sleep 0.1; i=$((i+1)); done; /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null; }
run_cycles() { # run_cycles <n> [VAR=val...]  starts watch.sh; the vendor-guard stub ends it after <n> cycles
  local n=$1; shift; : > "$STEPS"; echo 0 > "$T/cyc"
  env "$@" SG_TEST_CYCLES=$n bash "$S/watch.sh" > "$T/watch.out" 2>&1 & local p=$! i=0
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
[ "$(count janitor)" = 3 ] && ok "a per-cycle step still runs every cycle" || ko "janitor ran $(count janitor)× in 3 cycles"

# 4) reconcile does not hold the cycle, writes its own log, and stays out of cycle.log
t0=$(date +%s); run_cycles 1; el=$(( $(date +%s) - t0 ))
[ "$el" -lt 4 ] && ok "the cycle does not wait for the reconcile pass (${el}s with a 5 s pass)" || ko "the cycle waited for reconcile (${el}s)"
for _ in $(seq 1 150); do /usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && break; /bin/sleep 0.1; done
/usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && ok "the detached pass writes its own log ($ST/reconcile.log)" || ko "no BOARD line in reconcile.log"
/usr/bin/grep -q 'BOARD' "$ST/cycle.log" 2>/dev/null && ko "reconcile output still lands in cycle.log" || ok "cycle.log no longer carries the reconcile pass"

# 5) the fast lane dispatches, and a second pass does not overlap the first
F2=$T/fastscripts; mkdir -p "$F2"; cp "$d/fastloop.sh" "$d/single.sh" "$F2/"; cp "$S/env.sh" "$S/childcc.py" "$S/autofrontier.sh" "$S/autobug.sh" "$F2/"
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
[ "$(count live-guard)" = 1 ] && ok "8 the cycle runs the live-guard pass once" || ko "8 the cycle ran $(count live-guard) live-guard pass(es)"
[ ! -e "$ST/locks/single-liveguard" ] && ok "8 the pass's single-flight lock is released when it ends" || ko "8 the live-guard lock stayed behind"
run_cycles 2
[ "$(count live-guard)" = 2 ] && ok "8 a per-cycle step keeps its cadence (two cycles, two reads)" || ko "8 live-guard ran $(count live-guard)× in 2 cycles"

rm -rf "$T"; exit $fail
