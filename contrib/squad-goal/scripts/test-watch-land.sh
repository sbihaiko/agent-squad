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
# A source not named watch.sh is refused (#2025): a suite given as the source copied itself in and ran itself at every level.
case "$(basename "$src")" in watch.sh) ;; *) echo "$(basename "$0"): $src is not a watch.sh; refused" >&2; exit 2;; esac
mkdir -p "$S" "$ST/locks" "$B" "$T/co" "$T/fork"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/"; cp "$d/models.json" "$S/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/drainpolicy.py" "$d/ghcall.sh" "$S/"
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug SG_TODO=Todo SG_BASE=dev
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
EOF
# No open PR, no revci summary, nothing to move. The open-issue list answers the file `openissues` when a case writes one (#641).
printf '#!/bin/bash\ncase "$*" in *"issues(states: OPEN"*) [ -e "%s/openissues" ] && cat "%s/openissues";; esac\nexit 0\n' "$T" "$T" > "$B/gh"
# The cycle's own naps cost nothing, but a nap waits while a landing is still in its own detached pass (#955), so the next
# cycle reads that pass's result the way a live supervisor's closing wait gives it time to. A case that holds a landing by
# hand writes `nosettle` first.
printf '#!/bin/bash\n[ -e "%s/nosettle" ] && exit 0\nfor f in "%s"/auto/wland-*.busy; do [ -e "$f" ] && { /bin/sleep 0.05; exec "$0" "$@"; }; done\nexit 0\n' "$T" "$ST" > "$B/sleep"
chmod +x "$B/gh" "$B/sleep"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
# Every step the cycle runs, stubbed: the cycle costs its own wiring, not a pass of the loop.
for s in autohunt.sh heartbeat.sh wtclean.sh janitor.sh startability.sh live-guard.sh branchland.sh consult.sh; do
  printf '#!/bin/bash\nexit 0\n' > "$S/$s"; chmod +x "$S/$s"; done
# The childcc stub logs its call, and exits 3 (a pass that left children for later, #590) while a `ccinc` file is in the fixture.
printf 'import os,sys\nopen(os.environ["STEPS"],"a").write("childcc\\n")\nsys.exit(3 if os.path.exists("%s/ccinc") else 0)\n' "$T" > "$S/childcc.py"; printf 'pass\n' > "$S/reconcile.py"
# The landing pass is what a run's completion reaches: the stub logs the call, and nothing else is read from it.
# A `landfail` file in the fixture makes the stub land exit 1, as a rebase conflict or a refused push does.
printf '#!/bin/bash\necho "wave $*" >> "%s"\n[ -e "%s/locks/single-childcc" ] && echo "childcc-lock-held-during-land" >> "%s"\n[ -e "%s/landfail" ] && exit 1\n[ "$1" = land ] && rm -f "%s/auto/wave-$2"\nexit 0\n' "$STEPS" "$ST" "$STEPS" "$T" "$ST" > "$S/wave.sh"; chmod +x "$S/wave.sh"
# The cycle's last step ends the run after the cycles the case asked for.
cat > "$S/vendor-guard.sh" <<EOF
#!/bin/bash
echo guard >> "$STEPS"
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && { i=0; while [ ! -s "$T/watchpid" ] && [ "\$i" -lt 50 ]; do /bin/sleep 0.1; i=\$((i+1)); done; kill -TERM "\$(cat "$T/watchpid" 2>/dev/null)" 2>/dev/null; }
exit 0
EOF
chmod +x "$S/vendor-guard.sh"
# landing_busy: a landing of some run is still in its own detached pass (#955): its busy marker is there.
landing_busy() { local f; for f in "$ST"/auto/wland-*.busy; do [ -e "$f" ] && return 0; done; return 1; }
run_cycle() {   # run_cycle [n]: one supervisor over the fixture, ended by the cycle's last step
  : > "$STEPS"; echo 0 > "$T/cyc"; rm -f "$T/watchpid"
  env SG_TEST_CYCLES="${1:-1}" bash "$S/watch.sh" > "$T/watch.out" 2>&1 & local p=$! i=0
  echo "$p" > "$T/watchpid"
  while kill -0 "$p" 2>/dev/null && [ $i -lt 400 ]; do /bin/sleep 0.1; i=$((i+1)); done
  kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null; rm -f "$ST/fastloop.pid" 2>/dev/null
  while [ ! -e "$T/nosettle" ] && landing_busy; do /bin/sleep 0.05; done; return 0
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
# The landing runs in its own detached pass (#955), so the run is read on the cycle after the one that starts it.
rm -f "$T/ccinc"; run_cycle 2
[ "$(/usr/bin/grep -cxE 'wave land run-7 /.*/run-7' "$STEPS")" = 1 ] && ok "the pass that completes lands the wave" || ko "run-7 not landed after a complete pass: '$(cat "$STEPS")'"
/usr/bin/grep -q "run-7" "$ST/watch-seen.txt" 2>/dev/null && ok "and the run is read" || ko "run-7 not in watch-seen.txt"

# 8) (#1980, ADR-0146) the drain policy maps a loop-bug's label class to what it holds: a bare `loop-bug` holds every path,
# the landing included, and a `loop-bug:publish` holds only the publish, so an approved wave still lands. A
# `loop-bug:landing` issue names the landing path alone: no wave lands while it is open, the run stays unread, the log names
# the issue, and closing it lands the wave.
oi() { printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":641,"title":"t","body":"","state":"OPEN","labels":{"nodes":[{"name":"%s"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}' "$1" > "$T/openissues"; }
oi loop-bug; run run-8 pass; printf 'wave/run-8\n' > "$ST/auto/wave-run-8"; printf '%s\n' "$T/run-8" > "$ST/active-runs.txt"
run_cycle 1
/usr/bin/grep -q 'wave land run-8' "$STEPS" && ko "a bare loop-bug let the wave land" || ok "a bare loop-bug holds the landing too (#1980)"
rm -f "$T/openissues"
oi loop-bug:publish; run run-20 pass; printf 'wave/run-20\n' > "$ST/auto/wave-run-20"; printf '%s\n' "$T/run-20" > "$ST/active-runs.txt"
run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-20 /.*/run-20' "$STEPS")" = 1 ] && ok "a loop-bug:publish leaves the landing alone" || ko "a publish-only class held the landing: '$(cat "$STEPS")'"
oi loop-bug:landing; run run-9 pass; printf 'wave/run-9\n' > "$ST/auto/wave-run-9"; printf '%s\n' "$T/run-9" > "$ST/active-runs.txt"
run_cycle 1
/usr/bin/grep -q 'wave land run-9' "$STEPS" && ko "a loop-bug:landing issue let the wave land" || ok "a loop-bug:landing issue stops the landing"
/usr/bin/grep -q "run-9" "$ST/watch-seen.txt" 2>/dev/null && ko "a stopped landing marked the run seen" || ok "a stopped landing leaves the run unread"
/usr/bin/grep -qF "LOOP STOP: #641 is a loop defect in the landing path; no wave lands until it closes" "$T/watch.out" && ok "the log names the landing stop and its issue" || ko "no landing stop line: '$(cat "$T/watch.out")'"
rm -f "$T/openissues"; run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-9 /.*/run-9' "$STEPS")" = 1 ] && ok "closing the issue lands the wave with no other action" || ko "run-9 not landed after the close: '$(cat "$STEPS")'"

# 9) (#844) a landing that fails on a rebase conflict is terminal for its wave: the landing runs once, the wave marker goes with
# the conflict (wave.sh does that), so the next cycle does not call the landing again, and the wait line names the conflict,
# never a lock. The stub is the real landing's shape: one WAVE CONFLICT line, the marker removed, exit 1.
cp "$S/wave.sh" "$T/wave.stub"
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$*" >> "$STEPS"
[ -e "$T/conflictfail" ] || exit 0
rm -f "$ST/auto/wave-\$2"
echo "WAVE CONFLICT wave/\$2: the rebase on dev conflicts at c9c5ccbf; #5 requeued on the current base"
exit 1
EOS
chmod +x "$S/wave.sh"
run run-c pass; printf 'wave/run-c\n' > "$ST/auto/wave-run-c"; printf '%s\n' "$T/run-c" > "$ST/active-runs.txt"
: > "$T/conflictfail"; run_cycle 2
[ "$(/usr/bin/grep -cxE 'wave land run-c /.*/run-c' "$STEPS")" = 1 ] && ok "a conflicting landing is not retried on the next cycle" \
  || ko "the conflicting landing ran $(/usr/bin/grep -cxE 'wave land run-c /.*/run-c' "$STEPS")x in 2 cycles: '$(cat "$STEPS")'"
/usr/bin/grep -E 'WATCH: run-c waits' "$T/watch.out" | /usr/bin/grep -q 'conflict' \
  && ok "the wait line names the conflict" || ko "the wait line does not name the conflict: '$(/usr/bin/grep 'WATCH: run-c' "$T/watch.out")'"
/usr/bin/grep -q 'pid none' "$T/watch.out" && ko "the wait line blames a lock that is not held (pid none)" || ok "the wait line never blames pid none for a conflict"
[ "$(/usr/bin/grep -c 'RUN run-c verdict' "$T/watch.out")" = 1 ] && ok "the retired wave's run is read on the next cycle" \
  || ko "the retired run was not read: '$(cat "$T/watch.out")'"
cp "$T/wave.stub" "$S/wave.sh"; rm -f "$T/conflictfail"

# 10) (#955) the landing is a detached single-flight pass: a landing that takes time does not hold the cycle. The cycle reaches
# its last step (vendor-guard) while the landing still runs, so the guard is logged before the landing ends.
run run-10 pass; printf 'wave/run-10\n' > "$ST/auto/wave-run-10"; printf '%s\n' "$T/run-10" > "$ST/active-runs.txt"
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$*" >> "$STEPS"
[ -e "$T/slowland" ] && { echo land-begin >> "$STEPS"; /bin/sleep 3; echo land-end >> "$STEPS"; }
exit 0
EOS
: > "$T/slowland"; run_cycle 1
g=$(/usr/bin/grep -n '^guard$' "$STEPS" | head -1 | cut -d: -f1); e=$(/usr/bin/grep -n '^land-end$' "$STEPS" | head -1 | cut -d: -f1)
[ -n "$g" ] && [ -n "$e" ] && [ "$g" -lt "$e" ] && ok "a slow landing does not hold the cycle" \
  || ko "the cycle waited for a slow landing: '$(cat "$STEPS")'"
rm -f "$T/slowland"; cp "$T/wave.stub" "$S/wave.sh"

# 11) (#955) a landing whose own pass is still alive is waited for, never doubled: the cycle starts no second pass and says so.
# The busy marker names a live process that is no pass of this cycle, so the case holds the settle back with `nosettle`.
run run-11 pass; printf 'wave/run-11\n' > "$ST/auto/wave-run-11"; printf '%s\n' "$T/run-11" > "$ST/active-runs.txt"
: > "$T/nosettle"; /bin/sleep 60 & hl=$!; printf '%s\n' "$hl" > "$ST/auto/wland-run-11.busy"
run_cycle 1
[ -z "$(/usr/bin/grep -E '^wave land run-11' "$STEPS")" ] && ok "a landing in its own pass is not doubled" || ko "a second landing started beside the live pass: '$(cat "$STEPS")'"
/usr/bin/grep -q "WATCH: run-11 waits: its landing is in its own pass (pid $hl)" "$T/watch.out" \
  && ok "the log names the landing pass it waits for" || ko "no wait line for run-11: '$(cat "$T/watch.out")'"
/usr/bin/grep -q "run-11" "$ST/watch-seen.txt" 2>/dev/null && ko "a landing still in its pass was read as seen" || ok "a landing still in its pass leaves the run unread"
kill "$hl" 2>/dev/null; wait "$hl" 2>/dev/null; rm -f "$ST/auto/wland-run-11.busy" "$T/nosettle"

# 12) (#955) the busy marker is written by the cycle from a real pass pid, never by the pass: a second cycle starts while the
# first landing still sleeps, waits on the marker the first cycle wrote, and starts no second landing. /bin/bash 3.2 leaves
# `$BASHPID` empty, so a marker the pass wrote could name no process and this wait would never fire.
run run-12 pass; printf 'wave/run-12\n' > "$ST/auto/wave-run-12"; printf '%s\n' "$T/run-12" > "$ST/active-runs.txt"
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$*" >> "$STEPS"
echo land-begin >> "$STEPS"; /bin/sleep 3; echo land-end >> "$STEPS"
rm -f "$ST/auto/wave-\$2"
exit 0
EOS
chmod +x "$S/wave.sh"
: > "$T/nosettle"; run_cycle 1
mp=$(cat "$ST/auto/wland-run-12.busy" 2>/dev/null)
[ -n "$mp" ] && kill -0 "$mp" 2>/dev/null && ok "the cycle writes the landing pass's own pid in its busy marker" \
  || ko "the busy marker names no live pass: '$mp'"
run_cycle 1
[ -z "$(/usr/bin/grep -xE '^wave land run-12 ' "$STEPS")" ] && ok "the second cycle starts no second landing" || ko "landings: '$(cat "$STEPS")'"
/usr/bin/grep -q "WATCH: run-12 waits: its landing is in its own pass (pid $mp)" "$T/watch.out" \
  && ok "the second cycle waits on the pass its marker names" || ko "no wait line for run-12: '$(/usr/bin/grep 'run-12' "$T/watch.out")'"
rm -f "$T/nosettle"; cp "$T/wave.stub" "$S/wave.sh"

# 13) (#1290) a landing whose lock is held by a live holder says which fact it waits on: the holder's pid and the instant it
# took the lock, never "the landing failed". The landing runs no second pass beside the holder, and it lands once the holder ends.
run run-13 pass; printf 'wave/run-13\n' > "$ST/auto/wave-run-13"; printf '%s\n' "$T/run-13" > "$ST/active-runs.txt"
/bin/sleep 60 & wh=$!; mkdir -p "$ST/locks/single-wland"; echo "$wh" > "$ST/locks/single-wland/pid"; touch -t 202001010000 "$ST/locks/single-wland/pid"
run_cycle 1
[ -z "$(/usr/bin/grep -E '^wave land run-13' "$STEPS")" ] && ok "a landing waits while its lock is held" || ko "a landing ran beside the holder: '$(cat "$STEPS")'"
/usr/bin/grep -E 'WATCH: run-13 waits' "$T/watch.out" | /usr/bin/grep -q "held by pid $wh" \
  && ok "the wait line names the landing's holder" || ko "no holder named: '$(/usr/bin/grep 'run-13' "$T/watch.out")'"
/usr/bin/grep -E 'WATCH: run-13 waits' "$T/watch.out" | /usr/bin/grep -q 'held since 2020-01-01' \
  && ok "the wait line names the instant the lock was taken" || ko "no held-since instant: '$(/usr/bin/grep 'run-13' "$T/watch.out")'"
/usr/bin/grep -E 'WATCH: run-13 waits' "$T/watch.out" | /usr/bin/grep -q 'landing failed' \
  && ko "a held landing reads as a failure: '$(/usr/bin/grep 'run-13' "$T/watch.out")'" || ok "a held landing does not read as a failure"
/usr/bin/grep -q "run-13" "$ST/watch-seen.txt" 2>/dev/null && ko "a held landing marked the run seen" || ok "a held landing leaves the run unread"
kill "$wh" 2>/dev/null; wait "$wh" 2>/dev/null; rm -rf "$ST/locks/single-wland"
run_cycle 1
[ "$(/usr/bin/grep -cxE 'wave land run-13 /.*/run-13' "$STEPS")" = 1 ] && ok "the landing runs once the holder is gone" \
  || ko "run-13 not landed after the holder ended: '$(cat "$STEPS")'"

# 14) (#1290) a landing that failed names its own reason, never a lock: the two facts no longer share one sentence.
cp "$S/wave.sh" "$T/wave.stub2"
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$*" >> "$STEPS"
[ -e "$T/plainfail" ] || exit 0
echo "WAVE-ERROR the push of wave/\$2 was refused by the base"
exit 1
EOS
chmod +x "$S/wave.sh"
run run-14 pass; printf 'wave/run-14\n' > "$ST/auto/wave-run-14"; printf '%s\n' "$T/run-14" > "$ST/active-runs.txt"
: > "$T/plainfail"; run_cycle 1
l=$(/usr/bin/grep -E 'WATCH: run-14 waits' "$T/watch.out")
printf '%s' "$l" | /usr/bin/grep -q 'its landing failed' && ok "a failed landing says it failed" || ko "no failure word: '$l'"
printf '%s' "$l" | /usr/bin/grep -q 'the push of wave/run-14 was refused by the base' \
  && ok "the wait line carries the failure's own reason" || ko "no failure reason: '$l'"
printf '%s' "$l" | /usr/bin/grep -q 'held since' && ko "a failed landing blamed a held lock: '$l'" || ok "a failed landing names no lock"
rm -f "$T/plainfail"; cp "$T/wave.stub2" "$S/wave.sh"

# 15) (#2058) a landing that finds the landing lock held queues its run for the batch: `wland-<run>.queued` names the run's
# folder, and the pass that holds the lock lands it. The landing pass runs with SG_LAND_QUEUE=1. SG_LAND_BATCH=1 queues nothing.
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$* queue=\${SG_LAND_QUEUE:-}" >> "$STEPS"
exit 0
EOS
chmod +x "$S/wave.sh"
run run-15 pass; printf 'wave/run-15\n' > "$ST/auto/wave-run-15"; printf '%s\n' "$T/run-15" > "$ST/active-runs.txt"
/bin/sleep 60 & wh=$!; mkdir -p "$ST/locks/single-wland"; echo "$wh" > "$ST/locks/single-wland/pid"
run_cycle 1
[ "$(cat "$ST/auto/wland-run-15.queued" 2>/dev/null)" = "$T/run-15" ] && ok "a held landing queues its run for the batch" || ko "no queue marker: '$(ls "$ST/auto")'"
rm -f "$ST/auto/wland-run-15.queued"
SG_LAND_BATCH=1 run_cycle 1
[ -e "$ST/auto/wland-run-15.queued" ] && ko "SG_LAND_BATCH=1 queued the run" || ok "SG_LAND_BATCH=1 queues no run"
kill "$wh" 2>/dev/null; wait "$wh" 2>/dev/null; rm -rf "$ST/locks/single-wland"
run_cycle 1
/usr/bin/grep -qE "^wave land run-15 /.*/run-15 queue=1$" "$STEPS" && ok "the landing pass runs with SG_LAND_QUEUE=1" || ko "steps: '$(cat "$STEPS")'"
[ -e "$ST/auto/wland-run-15.queued" ] && ko "a landed run keeps its queue marker" || ok "a run whose pass got the lock is not queued"
cp "$T/wave.stub2" "$S/wave.sh"

# 16) (#2068) every run that is ready in one cycle joins one batch: the cycle writes each run's queue marker, and then starts
# one landing pass, so the pass reads the markers of every run of that cycle. A run whose wave is held red never leads it.
cat > "$S/wave.sh" <<EOS
#!/bin/bash
echo "wave \$1 \$2 sees \$(cd "$ST/auto" && /bin/ls *.queued 2>/dev/null | tr '\n' ' ')" >> "$STEPS"
exit 0
EOS
chmod +x "$S/wave.sh"; rm -f "$ST"/auto/wland-*
run run-16a pass; run run-16b pass; run run-16c pass
for x in a b c; do printf 'wave/run-16%s\n' "$x" > "$ST/auto/wave-run-16$x"; done
printf '%s\n%s\n%s\n' "$T/run-16a" "$T/run-16b" "$T/run-16c" > "$ST/active-runs.txt"
printf 'sha\n' > "$ST/auto/tipred-wave_run-16a.red"
run_cycle 1
[ "$(/usr/bin/grep -c '^wave land' "$STEPS")" = 1 ] && ok "one cycle starts one landing pass" || ko "landing passes: '$(cat "$STEPS")'"
/usr/bin/grep -q '^wave land run-16b sees wland-run-16a.queued wland-run-16b.queued wland-run-16c.queued $' "$STEPS" \
  && ok "the pass reads the queue marker of every run of its cycle, led by a run not held red" || ko "steps: '$(cat "$STEPS")'"
ls "$ST/auto" | /usr/bin/grep -q '\.queued\.' && ko "a queue marker's temporary file stays" || ok "no temporary queue file stays"
/usr/bin/grep -qE '> *"\$ran\.queued"' "$S/watch.sh" && ko "watch.sh writes the queue marker in place" || ok "watch.sh writes the queue marker by a move"
rm -f "$ST"/auto/wland-* "$ST/auto/tipred-wave_run-16a.red"; cp "$T/wave.stub2" "$S/wave.sh"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
