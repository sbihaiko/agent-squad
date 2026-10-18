#!/bin/bash
# Regression test for the run's completion landing its wave (#375, ADR-0145 rule 10): the cycle that reads a run's verdict
# runs `wave.sh land <run>` for a finished run that cut a wave, and leaves a run that cut none alone. usage: bash test-watch-land.sh [path/to/watch.sh]
# Fixture: a copy of watch.sh in a temp scripts dir with every step stubbed, a `gh` that answers nothing and a `sleep` that
# returns at once, so one cycle costs its steps and not its naps. The cycle's last step (vendor-guard) ends the run after
# the number of cycles a case asked for. No network, no model, no emulator.
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, so an inherited one would reach this
# fixture. Drop them all, as shell-suites.sh does for every suite (#1410); the fixture sets what it needs below.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/watch.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; B=$T/bin
mkdir -p "$S" "$ST/locks" "$B" "$T/co" "$T/fork"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/"; cp "$d/models.json" "$S/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$S/"
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug SG_TODO=Todo SG_BASE=dev
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
EOF
printf '#!/bin/bash\nexit 0\n' > "$B/gh"          # no open PR, no revci summary, nothing to move
printf '#!/bin/bash\nexit 0\n' > "$B/sleep"       # the cycle's own naps cost nothing
chmod +x "$B/gh" "$B/sleep"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
# Every step the cycle runs, stubbed: the cycle costs its own wiring, not a pass of the loop.
for s in autohunt.sh heartbeat.sh wtclean.sh janitor.sh startability.sh live-guard.sh branchland.sh consult.sh; do
  printf '#!/bin/bash\nexit 0\n' > "$S/$s"; chmod +x "$S/$s"; done
# The childcc stub logs its call, and exits 3 (a pass that left children for later, #590) while a `ccinc` file is in the fixture.
printf 'import os,sys\nopen(os.environ["STEPS"],"a").write("childcc\\n")\nsys.exit(3 if os.path.exists("%s/ccinc") else 0)\n' "$T" > "$S/childcc.py"; printf 'pass\n' > "$S/reconcile.py"
# The landing pass is what a run's completion reaches: the stub logs the call, and nothing else is read from it.
# A `landfail` file in the fixture makes the stub land exit 1, as a rebase conflict or a refused push does.
printf '#!/bin/bash\necho "wave $*" >> "%s"\n[ -e "%s/locks/single-childcc" ] && echo "childcc-lock-held-during-land" >> "%s"\n[ -e "%s/landfail" ] && exit 1\nexit 0\n' "$STEPS" "$ST" "$STEPS" "$T" > "$S/wave.sh"; chmod +x "$S/wave.sh"
# The cycle's last step ends the run after the cycles the case asked for.
cat > "$S/vendor-guard.sh" <<EOF
#!/bin/bash
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && kill -TERM "\$(/bin/ps -o ppid= -p "\$PPID" | tr -d ' ')" 2>/dev/null
exit 0
EOF
chmod +x "$S/vendor-guard.sh"
run_cycle() {   # run_cycle [n]: one supervisor over the fixture, ended by the cycle's last step
  : > "$STEPS"; echo 0 > "$T/cyc"
  env SG_TEST_CYCLES="${1:-1}" bash "$S/watch.sh" > "$T/watch.out" 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null && [ $i -lt 400 ]; do /bin/sleep 0.1; i=$((i+1)); done
  kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null; rm -f "$ST/fastloop.pid" 2>/dev/null; return 0
}
# <run> <verdict>: a run folder with the record the cycle reads, and its entry in the list of live runs.
run() { mkdir -p "$T/$1"; printf '{"event":"verdict","verdict":"%s","reasons":[]}\n' "$2" > "$T/$1/record.jsonl"; }

# 1) the run that ended lands its wave, and the run that cut none is never sent to the landing pass.
mkdir -p "$ST/auto"; run run-1 pass; run run-2 pass
printf 'wave/run-1\n' > "$ST/auto/wave-run-1"
printf '%s\n%s\n' "$T/run-1" "$T/run-2" > "$ST/active-runs.txt"
run_cycle
[ "$(/usr/bin/grep -cxE 'wave land run-1 /.*/run-1' "$STEPS")" = 1 ] && ok "the run that ended lands its wave, once" \
  || ko "wave.sh was called $(/usr/bin/grep -cxE 'wave land run-1 /.*/run-1' "$STEPS")× for a finished wave run: '$(cat "$STEPS")'"
/usr/bin/grep -q 'wave land run-2' "$STEPS" && ko "a run that cut no wave was sent to the landing pass" || ok "a run that cut no wave is left alone"

# 2) one landing per finished run: a later cycle of the same supervisor reads the run as seen, and a restart reads it from
# watch-seen.txt, so a run that already ended is not landed twice.
run_cycle 2
[ "$(/usr/bin/grep -cxE 'wave land run-1 /.*/run-1' "$STEPS")" = 0 ] && ok "a run already read is not landed again" \
  || ko "the landing pass ran again for a run already read: '$(cat "$STEPS")'"
run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-1 /.*/run-1' "$STEPS")" = 0 ] && ok "and a restarted supervisor does not land it either" \
  || ko "a restart landed a run already read: '$(cat "$STEPS")'"

# 3) the marker the run's wave left is what says the run has one: a run whose wave is already in the base carries no
# marker, and the landing pass is not sent to it.
rm -f "$ST/auto/wave-run-1"; run run-3 pass
printf '%s\n%s\n' "$T/run-1" "$T/run-3" > "$ST/active-runs.txt"
run_cycle
[ -z "$(/usr/bin/grep '^wave ' "$STEPS")" ] && ok "no run without a wave marker reaches the landing pass" \
  || ko "a run with no wave was landed: '$(/usr/bin/grep '^wave ' "$STEPS")'"

# 4) a landing that fails is not a landing: the run stays unread and the next tick lands it again (#375 review). The marker
# the completion leaves means "the pass got through and the landing succeeded", so a land that exits non-zero leaves none.
rm -f "$ST/auto/wave-run-1"; run run-4 pass; printf 'wave/run-4\n' > "$ST/auto/wave-run-4"; printf '%s\n' "$T/run-4" > "$ST/active-runs.txt"
: > "$T/landfail"
run_cycle 2
[ "$(/usr/bin/grep -cxE 'wave land run-4 /.*/run-4' "$STEPS")" = 2 ] && ok "a landing that failed is retried on the next tick" \
  || ko "a failed landing was tried $(/usr/bin/grep -cxE 'wave land run-4 /.*/run-4' "$STEPS")x in 2 cycles: '$(cat "$STEPS")'"
/usr/bin/grep -q "run-4" "$ST/watch-seen.txt" 2>/dev/null && ko "a run whose landing failed was read as seen" || ok "a run whose landing failed stays unread"
rm -f "$T/landfail"
run_cycle 2
[ "$(/usr/bin/grep -cxE 'wave land run-4 /.*/run-4' "$STEPS")" = 1 ] && ok "the retry that succeeds lands the run once, and the run is read" \
  || ko "the run was landed $(/usr/bin/grep -cxE 'wave land run-4 /.*/run-4' "$STEPS")x after the failure cleared: '$(cat "$STEPS")'"
/usr/bin/grep -q "run-4" "$ST/watch-seen.txt" 2>/dev/null && ok "a landed run is recorded as seen" || ko "the landed run is not in watch-seen.txt"

# 5) the side effects of a finished run run once per run, never on every retry of a landing that fails (#375 review): the
# announcement and the per-issue comment follow the landing, so a failing landing repeats none. (#361: the retry that re-armed
# a failed run read the run's pull request, and it went with the pull-request path: the janitor frees the issue instead.)
run run-5 failed; printf '{"event":"spawn_accepted","child":"c","definition":"GitHub issue #5001"}\n' >> "$T/run-5/record.jsonl"
printf 'wave/run-5\n' > "$ST/auto/wave-run-5"; printf '%s\n' "$T/run-5" > "$ST/active-runs.txt"
: > "$T/landfail"; run_cycle 3
[ "$(/usr/bin/grep -c 'RUN run-5 verdict' "$T/watch.out")" = 0 ] && ok "a landing that keeps failing repeats no announcement" \
  || ko "side effects repeated while the landing failed: announced $(/usr/bin/grep -c 'RUN run-5 verdict' "$T/watch.out")x"
rm -f "$T/landfail"; run_cycle 3
[ "$(/usr/bin/grep -c 'RUN run-5 verdict' "$T/watch.out")" = 1 ] && ok "the cycle that lands the run announces it once" \
  || ko "announced $(/usr/bin/grep -c 'RUN run-5 verdict' "$T/watch.out")x after the landing"

# 6) (#471) the fast lane holds the childcc lock: the due landing is not skipped in silence. The cycle waits a bounded time for
# the lock (SG_LAND_WAIT polls), then names the run and the holder, leaves the run unread, and lands it on a later pass. The
# landing itself holds no childcc lock, so a gate wait inside it never blocks the fast lane's pass.
run run-6 pass; printf 'wave/run-6\n' > "$ST/auto/wave-run-6"; printf '%s\n' "$T/run-6" > "$ST/active-runs.txt"
/bin/sleep 60 & holder=$!; mkdir -p "$ST/locks/single-childcc"; echo "$holder" > "$ST/locks/single-childcc/pid"
SG_LAND_WAIT=2 run_cycle 1
/usr/bin/grep -q 'wave land run-6' "$STEPS" && ko "the landing ran while the childcc lock was held: '$(cat "$STEPS")'" || ok "a held childcc lock defers the landing"
/usr/bin/grep -E 'WATCH: run-6 waits' "$T/watch.out" | /usr/bin/grep -q "childcc lock.*pid $holder" \
  && ok "the log names the run and the holder of the lock" || ko "no line names run-6 and the lock holder: '$(cat "$T/watch.out")'"
/usr/bin/grep -q "run-6" "$ST/watch-seen.txt" 2>/dev/null && ko "a deferred run was read as seen" || ok "a deferred run stays unread"
/usr/bin/grep -qx childcc "$STEPS" && ko "the cycle's own childcc ran beside the held lock" || ok "the cycle's own childcc goes through the childcc lock (#590)"
kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null; rm -rf "$ST/locks/single-childcc"
run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-6 /.*/run-6' "$STEPS")" = 1 ] && ok "the next pass lands the deferred run" || ko "the deferred run was not landed on the next pass: '$(cat "$STEPS")'"
/usr/bin/grep -q 'childcc-lock-held-during-land' "$STEPS" && ko "the landing ran inside the childcc lock" || ok "the landing holds no childcc lock"

# 7) (#590) a childcc pass that left children for a later pass (exit 3) is no pass: the wave does not land and the run stays
# unread, and the log says the landing is deferred. The pass that leaves none lands it.
run run-7 pass; printf 'wave/run-7\n' > "$ST/auto/wave-run-7"; printf '%s\n' "$T/run-7" > "$ST/active-runs.txt"
: > "$T/ccinc"; run_cycle 1
/usr/bin/grep -q 'wave land run-7' "$STEPS" && ko "a wave landed on an incomplete childcc pass" || ok "an incomplete childcc pass does not land the wave"
/usr/bin/grep -q "run-7" "$ST/watch-seen.txt" 2>/dev/null && ko "an incomplete pass marked the run seen" || ok "an incomplete pass leaves the run unread"
/usr/bin/grep -E 'WATCH: run-7 waits' "$T/watch.out" | /usr/bin/grep -q deferred && ok "the log names the landing deferred" || ko "no deferred line for run-7: '$(cat "$T/watch.out")'"
rm -f "$T/ccinc"; run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-7 /.*/run-7' "$STEPS")" = 1 ] && ok "the pass that completes lands the wave" || ko "run-7 not landed after a complete pass: '$(cat "$STEPS")'"
/usr/bin/grep -q "run-7" "$ST/watch-seen.txt" 2>/dev/null && ok "and the run is read" || ko "run-7 not in watch-seen.txt"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
