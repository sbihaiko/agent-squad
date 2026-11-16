#!/bin/bash
# (#1735) The hang a deadline case starts sleeps a length only this run names, so the survivor probe and the cleanup
# below read this suite's own processes: `pgrep -f "sleep 2718"` read every suite on the machine, and two gates side
# by side then read each other's hang as a survivor, or ended it.
export HANG_NAP=$(( 271800000 + $$ ))
# The promotion pass (#459, ADR-0145 rule 3): the cycle earns the full proof of the dev tip and promotes it. In one hold of the
# gate lock it runs the suite in a clean tree at the exact tip, writes the full line, writes a commit status on the sha, reads it
# back, and fast-forwards stg through promote-stg.sh. A held gate lock skips the tick; a tip already proved or refused, or a
# landing inside the quiet window, runs no suite. Offline: a bare local remote, a stub gh, a stub suite. usage: bash test-promote-pass.sh
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, and a leaked SG_PROMOTE_PROOF=ci turns every
# local-suite case below into a CI proof that reads a workflow it never meant to read. Drop them all, as test-watch-cycle.sh
# does (#1410); the fixture sets what it needs below.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
# SG_CHILD_SESSION stays unset: the child guard refuses a PR merge, and the stub gh below merges into a local bare remote only.
unset SG_SERIAL_HELD SERIAL_TRY SG_CHILD_SESSION
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SG_GHREAD=true SG_PROMOTE_QUIET_SECONDS=0 SERIAL_POLL_SECONDS=0.2   # (#1171) a throwaway gate budget: the deadline cases run gate.sh
export SG_PROMOTE_LOAD_LIMIT=1000000   # the box the suite runs on is never idle: the load case below names its own bound
# (#1192) The CI wait is disabled unless a case proves it: every fixture tip is seconds old, so the production default would
# make every case wait for a CI that never comes. The wait's own cases name the bound they test.
export SG_PROMOTE_CI_WAIT=0
mkdir -p "$SG_STATE" "$T/bin"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# The stub gh: a status POST is kept per sha; the read-back lists it unless $T/readback-none says GitHub lost it.
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
# \$T/stg-takes-dev: the promotion branch takes the dev tip under the pass, as another pass or the owner would move it.
[ -e "$T/stg-takes-dev" ] && /usr/bin/git -C "$R" update-ref refs/heads/stg "\$(/usr/bin/git -C "$R" rev-parse refs/heads/dev)"
case "\$*" in
  *"-X POST repos/x/emu/statuses/"*) a="\$*"; s=\${a##*statuses/}; s=\${s%% *}; echo '{"state":"success","context":"squad/full-proof"}' > "$T/status-\$s"; echo '{"state":"success"}';;
  *"commits/"*"/statuses"*) a="\$*"; s=\${a##*commits/}; s=\${s%%/*}; if [ -e "$T/readback-none" ] || [ ! -e "$T/status-\$s" ]; then echo '[]'; else echo "[\$(cat "$T/status-\$s")]"; fi;;
  *graphql*) echo '{"data":{"repository":{"pullRequests":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}';;
  *"-X POST repos/x/emu/pulls"*) echo '{"number":11}';;
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/stg; then echo '{"merged":true}'; else echo '{"merged":false,"message":"Pull Request is not mergeable"}'; fi;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The stub suite logs where it ran, the HEAD it ran at, whether the proof lock was held and the landing gate lock was free
# around it (ADR-0145 rule 3: the proof runs in its own tree under its own lock, and no wave waits on it); $T/suite-rc is its exit.
printf '#!/bin/bash\necho "$(pwd -P) $(git rev-parse HEAD) $([ -d "$SG_STATE/locks/full-proof" ] && echo held || echo free)-$([ -d "$SG_STATE/locks/gate" ] && echo gateheld || echo gatefree) $(git status --porcelain | wc -l | tr -d " ")" >> "%s/suite.log"\necho "$([ -e "$SG_STATE/promote-reserve" ] && echo reserved || echo unreserved)" >> "%s/reserve.log"\ns=$(cat "%s/suite-sleep" 2>/dev/null); [ -n "$s" ] && sleep "$s"\n[ -f "%s/suite-out" ] && cat "%s/suite-out"\nexit $(cat "%s/suite-rc" 2>/dev/null || echo 0)\n' "$T" "$T" "$T" "$T" "$T" "$T" "$T" > "$T/suite"
chmod +x "$T/suite"; export SG_PROMOTE_SUITE="$T/suite"
runs() { cat "$T/suite.log" 2>/dev/null | wc -l | tr -d " "; }
lines() { cat "$SG_STATE/promote.log" 2>/dev/null | /usr/bin/grep -c "promote-pass: $1"; }
pass() { bash "$d/promote-pass.sh" > "$T/out" 2>&1; }

# The state directory is always named: with SG_STATE unset the pass refuses, and the machine-wide default is never read.
env -u SG_STATE bash "$d/promote-pass.sh" >/dev/null 2>&1; rc=$?
[ $rc = 2 ] && ok "with SG_STATE unset the pass refuses (exit 2)" || ko "SG_STATE unset: rc $rc"

# A green suite at the dev tip: the full line, the commit status, its read-back, and a fast-forward of stg.
adv dev one; t1=$(tip dev); pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t1" ] && ok "a green suite at the dev tip promotes stg by fast-forward" || ko "green: rc $rc, stg $(tip stg), dev $t1, out '$(cat "$T/out")'"
read -r where at lock dirty < "$T/suite.log"
[ "$at" = "$t1" ] && ok "the suite ran at the exact dev tip" || ko "the suite ran at $at, not $t1"
[ "$lock" = held-gatefree ] && ok "the suite ran inside the proof lock, and the landing gate lock was free" || ko "the suite ran with the proof lock $lock"
[ "$dirty" = 0 ] && ok "the suite ran in a clean tree" || ko "the suite's tree was dirty ($dirty)"
case "$where" in "$HOME"/wt-*|*/wt-*) ko "the suite tree is a wt-* tree, which wtclean removes: $where";; *) ok "the suite tree is not a wt-* tree";; esac

# #739 with ADR-0157 rule 1 rewritten: a reserve stands only when the owner names a bound. The default holds nothing, so a
# proof never takes a run slot from the loop.
[ "$(tail -n 1 "$T/reserve.log" 2>/dev/null)" = unreserved ] && ok "with the default bound a proof holds no dispatch" || ko "the default held the machine: '$(cat "$T/reserve.log" 2>/dev/null)'"
[ ! -e "$SG_STATE/promote-reserve" ] && ok "no reserve stands after the proof" || ko "a reserve outlived the proof"
IFS=$'\t' read -r _ kind res sha < "$SG_STATE/gate-status-full"
[ "$kind $res $sha" = "full pass $t1" ] && ok "the pass wrote the full pass line for the tip" || ko "full line: '$kind $res $sha'"
/usr/bin/grep -q -- "-X POST repos/x/emu/statuses/$t1 .*context=squad/full-proof" "$T/gh.log" && ok "the pass wrote a commit status on the proven sha, under its own context" || ko "no commit status POST: $(cat "$T/gh.log")"
/usr/bin/grep -q "repos/x/emu/commits/$t1/statuses" "$T/gh.log" && ok "the pass read the commit status back" || ko "no read-back"
/usr/bin/grep -qE 'workflow|dispatch|/runs' "$T/gh.log" && ko "the pass started a CI run" || ok "the pass starts no CI run"

# A second pass with no new tip does no work.
n=$(runs); : > "$T/gh.log"; pass
[ "$(runs)" = "$n" ] && [ ! -s "$T/gh.log" ] && ok "a second pass with no new tip runs no suite and calls no gh" || ko "the pass repeated work: runs $(runs), gh '$(cat "$T/gh.log")'"

# A red suite: no promotion, one marker and one line for the tip; the next pass on that tip runs nothing and says nothing new.
adv dev two; t2=$(tip dev); before=$(tip stg); echo 1 > "$T/suite-rc"; : > "$T/gh.log"; pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && ok "a red suite promotes nothing" || ko "red: rc $rc, stg $(tip stg)"
IFS=$'\t' read -r _ kind res sha < "$SG_STATE/gate-status-full"
[ "$kind $res $sha" = "full fail $t2" ] && ok "the red suite wrote the full fail line for its tip" || ko "red full line: '$kind $res $sha'"
/usr/bin/grep -q "statuses/$t2" "$T/gh.log" && ko "a red suite wrote a commit status" || ok "a red suite writes no commit status"
[ "$(lines "$t2")" = 1 ] && ok "the red tip says its reason on one line" || ko "red lines: $(lines "$t2")"
n=$(runs); pass
[ "$(runs)" = "$n" ] && [ "$(lines "$t2")" = 1 ] && ok "a refused tip runs no second suite and says nothing new" || ko "refused tip repeated: runs $(runs), lines $(lines "$t2")"

# A missing read-back: the commit status is what the pass promotes on, so a status GitHub does not return promotes nothing.
adv dev three; t3=$(tip dev); before=$(tip stg); rm -f "$T/suite-rc"; : > "$T/readback-none"; pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && ok "a commit status that does not read back promotes nothing" || ko "no read-back: rc $rc, stg $(tip stg)"
[ "$(lines "$t3")" = 1 ] && /usr/bin/grep "promote-pass: $t3" "$SG_STATE/promote.log" | /usr/bin/grep -q "commit status" && ok "the refusal names the commit status" || ko "read-back line: $(/usr/bin/grep "$t3" "$SG_STATE/promote.log")"
rm -f "$T/readback-none"

# A held gate lock: the pass returns at once, runs no suite, leaves no marker; the next free pass proves the tip and carries the
# earlier refused work with it.
adv dev four; t4=$(tip dev); before=$(tip stg)
(bash "$d/serial.sh" full-proof sleep 6) & hp=$!; for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/full-proof/pid" ] && break; sleep 0.1; done
n=$(runs); s0=$(date +%s); pass; rc=$?; el=$(( $(date +%s) - s0 ))
[ "$el" -lt 4 ] && [ "$(runs)" = "$n" ] && ok "a held proof lock makes the pass return instead of wait (${el}s)" || ko "held proof lock: ${el}s, runs $(runs) (was $n)"
[ "$(tip stg)" = "$before" ] && [ "$(lines "$t4")" = 0 ] && ok "a skipped tick marks nothing and promotes nothing" || ko "skipped tick: stg $(tip stg), lines $(lines "$t4")"
wait "$hp"; pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t4" ] && ok "the next free pass proves the tip and promotes it" || ko "after the hold: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
git -C "$R" merge-base --is-ancestor "$t2" "$(tip stg)" && ok "a later proven tip carries the earlier refused work" || ko "stg does not carry $t2"

# The debounce: a landing inside the quiet window runs no suite; once the window passed, the tip is proved.
adv dev five; t5=$(tip dev); printf '%s\tb\t%s\t1\n' "$(date +%s)" "$t5" >> "$SG_STATE/landed.tsv"; n=$(runs)
SG_PROMOTE_QUIET_SECONDS=600 pass
[ "$(runs)" = "$n" ] && [ "$(tip stg)" = "$t4" ] && ok "a landing inside the quiet window runs no suite" || ko "debounce: runs $(runs), stg $(tip stg)"
printf '%s\tb\t%s\t1\n' "$(( $(date +%s) - 700 ))" "$t5" >> "$SG_STATE/landed.tsv"; SG_PROMOTE_QUIET_SECONDS=600 pass
[ "$(tip stg)" = "$t4" ] && ok "the window reads the latest landing, not the last row" || ko "debounce read an older row"
: > "$SG_STATE/landed.tsv"; printf '%s\tb\t%s\t1\n' "$(( $(date +%s) - 700 ))" "$t5" > "$SG_STATE/landed.tsv"; SG_PROMOTE_QUIET_SECONDS=600 pass
[ "$(tip stg)" = "$t5" ] && ok "after the quiet window the tip is proved and promoted" || ko "after the window: stg $(tip stg), out '$(cat "$T/out")'"

# stg holds work dev lacks: no fast-forward, so no suite is spent on the tip, and one line says why.
adv stg stg-only; adv dev dev-only; t6=$(tip dev); before=$(tip stg); n=$(runs); pass
[ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && [ "$(lines "$t6")" = 1 ] && ok "a dev tip that does not descend from stg runs no suite and says so once" || ko "diverged: stg $(tip stg), runs $(runs), lines $(lines "$t6")"

# The proof is a measurement on a machine the loop shares (#738): a loaded box yields "not proven: machine loaded", spends no
# suite and writes no marker, so a later pass retries the same tip. The worker count is bounded by the machine, never auto.
git -C "$R" update-ref refs/heads/stg "$(tip dev)"   # the diverged case above left stg ahead: a fast-forward needs it behind again
# A live run no longer refuses the proof (owner ask, settled by Fable): the ledger's line count was a proxy for load, the load is
# measured directly and again while the suite runs, and the proxy never opened on a busy loop -- stg froze while dev took landings
# all day. Memory left on the box, and a nightly that already owns the machine, still refuse it: two full suites at once saturate
# memory before either shows in the load average.
adv dev live; tl=$(tip dev); printf 'a live run\n' > "$SG_STATE/active-runs.txt"; n=$(runs)
pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" -gt "$n" ] && [ "$(tip stg)" = "$tl" ] && ok "a live run no longer holds the proof" || ko "a live run still held the proof: rc $rc, runs $(runs), stg $(tip stg)"
git -C "$R" update-ref refs/heads/stg "$(tip dev)"; rm -f "$SG_STATE/active-runs.txt"; adv dev loaded; t7=$(tip dev); before=$(tip stg); n=$(runs); SG_PROMOTE_LOAD_LIMIT=0 pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ "$(tip stg)" = "$before" ] && ok "a load average at its bound marks the tip not proven" || ko "load bound: rc $rc, runs $(runs), stg $(tip stg)"
/usr/bin/grep -q "promote-pass: $t7 not proven: machine loaded" "$SG_STATE/promote.log" && ok "the loaded line names the tip and the reason" || ko "loaded line: $(tail -2 "$SG_STATE/promote.log")"
[ "$(/usr/bin/grep -c "^$t7 " "$SG_STATE/promote-pass.tips" 2>/dev/null)" = 0 ] && ok "a loaded skip writes no marker: the tip is retried later" || ko "a loaded skip marked the tip refused"
mkdir -p "$SG_STATE/locks/nightly"; n=$(runs); pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ "$(tip stg)" = "$before" ] && ok "a held nightly refuses the proof: two full suites never share the box" || ko "nightly: rc $rc, runs $(runs), stg $(tip stg)"
rmdir "$SG_STATE/locks/nightly"; SG_PROMOTE_MEM_MIN_MB=99999999 pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ "$(tip stg)" = "$before" ] && ok "too little memory left refuses the proof" || ko "memory: rc $rc, runs $(runs), stg $(tip stg)"
# (#748) A run's removal can leave the last line empty, and a file of blank lines is no live run: the pre-check reads the
# non-empty lines, as its own message does, never the file's size.
printf '\n' > "$SG_STATE/active-runs.txt"; pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t7" ] && ok "an active-runs file of blank lines is no live run" || ko "a blank line held the promotion: rc $rc, stg $(tip stg), dev $t7"
pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t7" ] && ok "the same tip is proved once the machine is free" || ko "retry after load: rc $rc, stg $(tip stg)"
# (#944) The ledger keeps a run with a verdict until its issue closes, and a failed run leaves its issue open, so a busy loop
# never reaches an empty file. A listed run whose record has not moved for the live window and that already recorded a verdict
# is over: it holds no load, so it holds no proof. A line the pass cannot resolve is still read as live, the safe way.
mkdir -p "$T/deadrun"; printf '{"event":"verdict","verdict":"failed"}\n' > "$T/deadrun/record.jsonl"
touch -t 202001010000 "$T/deadrun/record.jsonl"; git -C "$R" update-ref refs/heads/stg "$(tip dev)"
adv dev deadrun; t8=$(tip dev); printf '%s\n' "$T/deadrun" > "$SG_STATE/active-runs.txt"; n=$(runs); pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" -gt "$n" ] && ok "a listed run that is over holds no proof" || ko "an over run held the proof: rc $rc, runs $(runs), dev $t8"
printf '%s\n' "$T/deadrun" "$T" > "$SG_STATE/active-runs.txt"; n=$(runs); git -C "$R" update-ref refs/heads/stg "$(tip dev)"; adv dev unresolvable; pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" -gt "$n" ] && ok "a ledger line the pass cannot resolve does not hold the proof either" || ko "an unresolvable line held the proof: rc $rc, runs $(runs)"
rm -f "$SG_STATE/active-runs.txt"
/usr/bin/grep -q -- '-n auto' "$d/promote-pass.sh" && ko "the default suite still takes every core (-n auto)" || ok "the default suite bounds its workers (#738)"
/usr/bin/grep -q 'sg_battery_workers' "$d/promote-pass.sh" && ok "the worker count comes from the one machine bound" || ko "promote-pass.sh carries its own worker count"

# The dispatchers read the reserve (#739): a standing one holds them in one line, and a marker past the gate lock's own bound
# is a proof that died and holds nothing.
hold() { ( export SG_STATE="$SG_STATE"; . "$d/ghcall.sh"; gh_dispatch_hold ); }
rm -f "$SG_STATE/nightly-result" "$SG_STATE/promote-reserve"
printf '%s 1\n' "$(date +%s)" > "$SG_STATE/promote-reserve"
out=$(hold); rc=$?
[ $rc = 0 ] && case "$out" in *"DISPATCH HOLD: the promotion proof holds the machine"*) true;; *) false;; esac \
  && ok "a standing reserve holds the dispatchers in one line" || ko "reserve: rc $rc, '$out'"
printf '%s 1\n' "$(( $(date +%s) - 8000 ))" > "$SG_STATE/promote-reserve"
hold >/dev/null; [ $? = 1 ] && ok "a reserve past the lock's bound holds nothing" || ko "a dead reserve held the dispatcher"
rm -f "$SG_STATE/promote-reserve"

# The cycle's own promotion step -- the single-flight lock, the CI proof, the debounce and the step's line in cycle.log -- is
# proved by its behavior in test-watch-cycle.sh, never by a grep of watch.sh's source: a grep breaks on a reformatted line and
# still passes a step that runs in the foreground. This suite proves the pass itself (ADR-0145 rule 3).
# ADR-0156: a full pass carries across a covered delta. The proven tip is t1; dev moves by one covered path; the delta carries it
# and the pass promotes without the full suite. A path outside the map runs the full suite again.
before=$(runs)
$W fetch -q origin; $W checkout -q -B dev origin/dev; mkdir -p "$T/w/contrib/squad-goal/scripts"
echo "# delta" >> "$T/w/contrib/squad-goal/scripts/test-nightly.sh"; $W add -A; $W commit -q -m delta-covered; $W push -q origin dev
t2=$(tip dev); SG_DELTA_SUITE=true pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t2" ] && ok "a covered delta promotes the tip that moved past the full pass" || ko "delta carry: rc $rc, stg $(tip stg), dev $t2, out '$(cat "$T/out")'"
[ "$(runs)" = "$before" ] && ok "a covered delta runs no full suite" || ko "the full suite ran for a covered delta: $(cat "$T/suite.log")"
$W fetch -q origin; $W checkout -q -B dev origin/dev; echo x >> "$T/w/pyproject.toml" 2>/dev/null || echo x > "$T/w/pyproject.toml"
$W add -A; $W commit -q -m delta-uncovered; $W push -q origin dev
t3=$(tip dev); rm -f "$SG_STATE/gate-status-delta"; pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$t3" ] && ok "an uncovered delta falls back to the full suite, which promotes" || ko "uncovered delta: rc $rc, stg $(tip stg), dev $t3, out '$(cat "$T/out")'"
[ "$(runs)" -gt "$before" ] && ok "the uncovered delta ran the full suite" || ko "the uncovered delta skipped the full suite: $(cat "$T/suite.log")"

# ADR-0157: a proof is bounded, and it never blocks the loop for long. Past its reserve bound the reserve lifts and the
# proof keeps running, so the loop dispatches beside it. Past its bound the proof is killed and recorded indeterminate:
# it promotes nothing, writes no gate status, and is not marked refused, so a later pass retries the same tip.
adv dev bound; tb=$(tip dev); echo 6 > "$T/suite-sleep"; rm -f "$T/suite-rc" "$SG_STATE/promote-reserve"
SG_PROMOTE_RESERVE_SECONDS=1 SG_PROMOTE_FALLBACK_SECONDS=0 SG_PROMOTE_BOUND_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out2" 2>&1 & bp=$!
for _ in $(seq 1 40); do sleep 0.25; [ -e "$SG_STATE/promote-reserve" ] && break; done
[ -e "$SG_STATE/promote-reserve" ] && ok "the proof reserves the machine at its start" || ko "the proof never reserved the machine"
for _ in $(seq 1 60); do sleep 0.25; [ ! -e "$SG_STATE/promote-reserve" ] && break; done
{ [ ! -e "$SG_STATE/promote-reserve" ] && kill -0 "$bp" 2>/dev/null; } && ok "past its reserve bound the reserve lifts while the proof still runs" \
  || ko "the reserve did not lift while the proof ran"
wait "$bp"; rc=$?
[ "$rc" = 0 ] && [ "$(tip stg)" = "$tb" ] && ok "a proof that lifted its reserve still promotes its tip" || ko "lifted proof: rc $rc, stg $(tip stg), dev $tb"
rm -f "$T/suite-sleep"
# ADR-0164 (#1046): a bound the delta cannot carry still ships the tip. The proven base P is the tip before the slow commit, and the
# slow commit adds two paths outside the delta map, so the delta refuses and the bound-ended proof promotes the tip with a marker.
p0=$(tip dev); printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$p0" > "$SG_STATE/gate-status-full"; rm -f "$SG_STATE/gate-status-delta"
$W fetch -q origin; $W checkout -q -B dev origin/dev; mkdir -p "$T/w/squad"; echo x > "$T/w/squad/slow.py"; echo x > "$T/w/squad/other.py"
$W add -A; $W commit -q -m slow; $W push -q origin dev
ts=$(tip dev); echo 30 > "$T/suite-sleep"; rm -f "$SG_STATE/promote-indeterminate" "$SG_STATE/promote-bound" "$T/suite-rc"
s0=$(date +%s); SG_PROMOTE_BOUND_SECONDS=1 SG_PROMOTE_FALLBACK_SECONDS=0 SG_PROMOTE_RESERVE_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out3" 2>&1; rc=$?; el=$(( $(date +%s) - s0 ))
[ "$rc" = 0 ] && [ "$el" -lt 15 ] && ok "a proof past its bound is killed, not awaited, and its tip ships (${el}s)" || ko "bound: rc $rc after ${el}s, out '$(cat "$T/out3")'"
[ "$(tip stg)" = "$ts" ] && ok "a bound the delta cannot carry still promotes the dev tip" || ko "bound ship: stg $(tip stg), dev $ts"
IFS=$'\t' read -r _ mt mb mg < "$SG_STATE/promote-bound" 2>/dev/null
[ "$mt" = "$ts" ] && [ "$mb" = 1 ] && ok "the bound marker names the tip and the bound it passed" \
  || ko "bound marker: '$(cat "$SG_STATE/promote-bound" 2>/dev/null)'"
case "$mg" in *"squad/other.py, squad/slow.py"*) ok "the bound marker names every part the delta did not cover";; *) ko "bound marker gap: '$mg'";; esac
/usr/bin/grep -q "^$ts promoted: bound-ended" "$SG_STATE/promote-pass.tips" && ok "the tip is marked bound-ended, not as a full or a delta pass" \
  || ko "tip mark: '$(/usr/bin/grep "^$ts " "$SG_STATE/promote-pass.tips" 2>/dev/null)'"
[ "$(/usr/bin/grep -c "^$ts not promoted" "$SG_STATE/promote-pass.tips" 2>/dev/null)" = 0 ] && ok "a bound-ended tip is not marked refused" || ko "the bound-ended tip was marked refused"
/usr/bin/grep -q "promote-pass: $ts not tested" "$SG_STATE/promote.log" && ok "the log says the tip is not tested until the nightly proves it" || ko "no not-tested line for $ts"
rm -f "$T/suite-sleep"

# An owner-named bound still reserves the machine while the suite runs, and the marker is gone after the proof: a standing
# marker costs run slots, so it stands only when the owner asks for the measurement to be shielded.
adv dev held; th=$(tip dev); : > "$T/reserve.log"; rm -f "$T/suite-rc"
SG_PROMOTE_RESERVE_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out5" 2>&1; rc=$?
[ "$(tail -n 1 "$T/reserve.log")" = reserved ] && ok "an owner-named reserve bound holds the machine while the suite runs" \
  || ko "named bound: '$(tail -n 1 "$T/reserve.log")'"
[ ! -e "$SG_STATE/promote-reserve" ] && ok "the named reserve is gone after the proof" || ko "the named reserve outlived the proof"
[ "$rc" = 0 ] && [ "$(tip stg)" = "$th" ] && ok "a named-reserve proof still promotes its tip" || ko "named reserve promote: rc $rc, stg $(tip stg)"

# CI shards (ADR-0164, #1051, #1518): the full suite's shard jobs of the tip are the full pass. Four green shards write the
# full line through gate.sh --status and promote with no suite; a red shard holds the tip and names it; an unfinished shard
# holds nothing.
export CHECKS_DIR=$T
# The shard names come from a workflow file, as every check name does: this fixture names four shard jobs, the shape the
# repository's own ci.yml carries twelve of.
printf 'jobs:\n  ruff:\n    name: ruff\n  mypy:\n    name: mypy\n  fast-battery:\n    name: fast-battery\n  full-proof-0:\n    name: full-proof shard 0\n  full-proof-1:\n    name: full-proof shard 1\n  full-proof-2:\n    name: full-proof shard 2\n  full-proof-3:\n    name: full-proof shard 3\n' > "$T/ci-shards.yml"
export SG_CI_WORKFLOW="$T/ci-shards.yml"
cat > "$T/bin/ghread" <<'EOF'
#!/bin/bash
case "$1" in
  *check-runs*) s=${1#*commits/}; s=${s%%/*}
    [ -e "$CHECKS_DIR/checks-fail" ] && exit 1   # a read that fails, as a GitHub outage does
    if [ -f "$CHECKS_DIR/checks-$s" ]; then cat "$CHECKS_DIR/checks-$s"
    elif [ -f "$CHECKS_DIR/checks-default" ]; then cat "$CHECKS_DIR/checks-default"   # a tip no case named (#1495): the moved tip
    else echo '{"check_runs":[]}'; fi
    # #1495: reading the proved tip's checks once moves dev, as a landing does while the pass waits for the gate lock.
    if [ -f "$CHECKS_DIR/move-on" ] && [ "$s" = "$(cat "$CHECKS_DIR/move-on")" ]; then rm -f "$CHECKS_DIR/move-on"
      m="git -C $CHECKS_DIR/w -c user.email=a@b -c user.name=t"
      $m fetch -q origin && $m checkout -q -B dev origin/dev && $m commit -q --allow-empty -m moved-during-proof && $m push -q origin dev
    fi;;
  *pulls?state=open*) [ -e "$CHECKS_DIR/read-fail" ] && exit 1   # (#1490) a transient read failure while the file stands
    if [ -e "$CHECKS_DIR/pr-open" ]; then echo '[{"number":11,"head":{"ref":"dev"},"base":{"ref":"stg"}}]'; else echo '{}'; fi;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/ghread"
shards() {  # <sha> <state of shard 0> ... <state of shard 3>: a conclusion, "running" (unfinished) or "none" (missing)
  # CHECK_CONCLUSION names the conclusion of every fast-tier job of the same commit (default success; none: the jobs are missing).
  local s=$1 k=0 out="" c j ck=${CHECK_CONCLUSION:-success}; shift
  for c in "$@"; do
    case "$c" in
      none) ;;
      running) out="$out{\"name\":\"full-proof shard $k\",\"status\":\"in_progress\",\"conclusion\":null},";;
      *) out="$out{\"name\":\"full-proof shard $k\",\"status\":\"completed\",\"conclusion\":\"$c\"},";;
    esac
    k=$((k + 1))
  done
  if [ "$ck" != none ]; then
    for j in ${CHECK_JOBS:-ruff mypy fast-battery}; do out="{\"name\":\"$j\",\"status\":\"completed\",\"conclusion\":\"$ck\"},$out"; done
  fi
  echo "{\"check_runs\":[${out%,}]}" > "$T/checks-$s"
}
adv dev shards-green; tg=$(tip dev); rm -f "$T/suite-rc"; n=$(runs)
shards "$tg" success success success success; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tg" ] && ok "four green shards promote the tip" || ko "green shards: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
[ "$(runs)" = "$n" ] && ok "four green shards run no suite" || ko "green shards ran the suite ($(runs) runs, was $n)"
IFS=$'\t' read -r _ kind res sha < "$SG_STATE/gate-status-full"
[ "$kind $res $sha" = "full pass $tg" ] && ok "four green shards write the full pass line for the tip" || ko "shard full line: '$kind $res $sha'"
/usr/bin/grep -q "promote-pass: $tg promoted: the CI checks carried the full pass" "$SG_STATE/promote.log" && ok "the promotion record names the CI checks as the proof" \
  || ko "shard record: $(/usr/bin/grep "$tg" "$SG_STATE/promote.log")"
/usr/bin/grep -q -- "-X POST repos/x/emu/statuses/$tg .*context=squad/full-proof .*every CI job" "$T/gh.log" && ok "the CI route writes the squad/full-proof status, naming the CI jobs" \
  || ko "CI route wrote no full-proof status: $(/usr/bin/grep "statuses/$tg" "$T/gh.log")"
/usr/bin/grep -q "repos/x/emu/commits/$tg/statuses" "$T/gh.log" && ok "the CI route reads the commit status back" || ko "the CI route did not read the status back"

adv dev shard-red; tr=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tr" success success failure success; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a red shard holds the tip and runs no suite" \
  || ko "red shard: rc $rc, stg $(tip stg), runs $(runs) was $n"
/usr/bin/grep "promote-pass: $tr" "$SG_STATE/promote.log" | /usr/bin/grep -q "full-proof shard 2" && ok "the refusal names the red shard" \
  || ko "red shard line: $(/usr/bin/grep "$tr" "$SG_STATE/promote.log")"

# (#1192) A tip whose CI has not answered yet is measured by waiting, never by a local suite: the suite saturates the machine
# for eleven minutes, and the loop's own runs then die at the takeover bound. Past SG_PROMOTE_CI_WAIT the suite answers, so a CI
# that never reports never holds delivery.
adv dev ci-pending; tp=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tp" success success running running; SG_PROMOTE_CI_WAIT=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a tip whose CI is in flight runs no suite: the pass waits" \
  || ko "CI in flight: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"
/usr/bin/grep -q "promote-pass: $tp waiting for CI" "$SG_STATE/promote.log" && ok "the wait is one line, and names the tip" \
  || ko "no wait line: $(/usr/bin/grep "$tp" "$SG_STATE/promote.log")"
echo 0 > "$T/suite-rc"; before=$(tip stg); n=$(runs)
SG_PROMOTE_CI_WAIT=0 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tp" ] && [ "$(runs)" = "$((n + 1))" ] && ok "past the CI wait bound the local suite answers" \
  || ko "past the CI wait bound: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"

# (#1736) The wait runs from the pass that first found the tip's CI pending, never from the commit's own date. A tip that
# lands by fast-forward keeps the date of the child's commit, so it is older than the wait the moment it reaches dev: read
# from that date, the pass starts the local suite beside a CI that began seconds ago.
GIT_COMMITTER_DATE="2020-01-01T00:00:00Z" adv dev ci-pending-old-commit; to=$(tip dev); before=$(tip stg); n=$(runs)
shards "$to" success success running running; SG_PROMOTE_CI_WAIT=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "an old commit whose CI is in flight waits: the wait runs from the first pending pass" \
  || ko "old commit, CI in flight: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"
[ "$(cut -f1 "$SG_STATE/promote-ci-wait" 2>/dev/null)" = "$to" ] && ok "the wait record names the tip" \
  || ko "wait record: '$(cat "$SG_STATE/promote-ci-wait" 2>/dev/null)'"
SG_PROMOTE_CI_WAIT=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && ok "a second pass inside the wait keeps the first pass's start" \
  || ko "second pass inside the wait: rc $rc, runs $(runs) was $n"
printf '%s\t%s\n' "$to" "$(( $(date +%s) - 901 ))" > "$SG_STATE/promote-ci-wait"; echo 0 > "$T/suite-rc"
SG_PROMOTE_CI_WAIT=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$to" ] && [ "$(runs)" = "$((n + 1))" ] && ok "a wait record older than the bound lets the local suite answer" \
  || ko "wait record past the bound: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"
GIT_COMMITTER_DATE="2020-01-01T00:00:00Z" adv dev ci-pending-next-tip; tn=$(tip dev); n=$(runs)
printf '%s\t%s\n' "$to" "$(( $(date +%s) - 5000 ))" > "$SG_STATE/promote-ci-wait"
shards "$tn" success success running running; SG_PROMOTE_CI_WAIT=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ "$(cut -f1 "$SG_STATE/promote-ci-wait")" = "$tn" ] && ok "a new tip starts its own wait" \
  || ko "new tip: rc $rc, runs $(runs) was $n, record '$(cat "$SG_STATE/promote-ci-wait" 2>/dev/null)'"

# The suite is the fallback for a CI that cannot answer: a fixture that has waited past the bound still runs it.
adv dev shard-unfinished; tu=$(tip dev); echo 0 > "$T/suite-rc"; before=$(tip stg); n=$(runs)
shards "$tu" success success running timed_out; SG_PROMOTE_CI_WAIT=0 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tu" ] && [ "$(runs)" = "$((n + 1))" ] && ok "unfinished and timed-out shards hold nothing: the local suite answers" \
  || ko "unfinished shards: rc $rc, stg $(tip stg), runs $(runs) was $n"
/usr/bin/grep -q "promote-pass: $tu promoted: the local full suite answered" "$SG_STATE/promote.log" && ok "the record names the local full suite as the proof" \
  || ko "local record: $(/usr/bin/grep "$tu" "$SG_STATE/promote.log")"

# #1644: the complete tier starts on the `promote` label alone, so the run that event starts carries the complete tier and no
# fast tier: its fast-tier checks report `skipped`. A skipped run is no evidence either way, and the tip's own earlier run is
# the proof of that half (#1644). The complete tier green, the fast tier of an earlier run green, promotes with no suite.
skipped_runs() {  # <sha>: the fast tier of an earlier run and its skipped twin on the labeled run, beside four green shards
  local s=$1 j k n=10 out=""
  for j in ruff mypy fast-battery; do
    out="$out{\"id\":$n,\"name\":\"$j\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"id\":$((n + 1)),\"name\":\"$j\",\"status\":\"completed\",\"conclusion\":\"skipped\"},"
    n=$((n + 2))
  done
  for k in 0 1 2 3; do out="$out{\"id\":$n,\"name\":\"full-proof shard $k\",\"status\":\"completed\",\"conclusion\":\"success\"},"; n=$((n + 1)); done
  echo "{\"check_runs\":[${out%,}]}" > "$T/checks-$s"
}
adv dev labeled-skip; tls=$(tip dev); n=$(runs)
skipped_runs "$tls"; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tls" ] && [ "$(runs)" = "$n" ] \
  && ok "a labeled run's skipped fast tier holds nothing: the tip's own runs prove both halves" \
  || ko "labeled run: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"

# A workflow file that names no shard job is its own checks alone, and a green check set promotes the tip: the pass reads
# the names the file carries, never a shard count of its own.
printf 'jobs:\n  ruff:\n    name: ruff\n  mypy:\n    name: mypy\n  fast-battery:\n    name: fast-battery\n' > "$T/ci-no-shards.yml"
adv dev check-alone; ta=$(tip dev); n=$(runs)
shards "$ta" none none none none; SG_CI_WORKFLOW="$T/ci-no-shards.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$ta" ] && ok "with no shard job named, a green check set promotes the tip" \
  || ko "check alone: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
/usr/bin/grep -q "promote-pass: $ta promoted: the CI checks carried the full pass" "$SG_STATE/promote.log" && ok "the check-alone promotion records its proof" \
  || ko "check-alone record: $(/usr/bin/grep "$ta" "$SG_STATE/promote.log")"
export SG_CI_WORKFLOW="$T/ci-shards.yml"

# #1723: the dev -> stg pass waits on the fast tier alone: the jobs whose `if:` does not ask for the `promote` label. A tip
# whose three fast jobs are green is promoted with no suite and no complete-tier run; the record names the fast tier, and no
# full pass line and no squad/full-proof status is written, because no full suite ran.
LBL="github.head_ref == 'stg' && github.event.label.name == 'promote'"
printf 'jobs:\n  ruff:\n    name: ruff\n  mypy:\n    name: mypy\n  fast-battery:\n    name: fast-battery\n  complete-guard:\n    name: complete-guard\n    if: %s\n  full-xdist:\n    name: full-xdist\n    if: %s\n    needs: [complete-guard]\n  full-serial:\n    name: full-serial\n    if: %s\n    needs: [complete-guard]\n' "$LBL" "$LBL" "$LBL" > "$T/ci-complete.yml"
case "$(bash "$d/promote-pass.sh" --ci-fast-job-names "$T/ci-complete.yml" | tr '\n' ' ')" in
  "ruff mypy fast-battery ") ok "the fast tier is the jobs that do not ask for the promote label (#1723)";;
  *) ko "fast tier names: '$(bash "$d/promote-pass.sh" --ci-fast-job-names "$T/ci-complete.yml" 2>&1 | tr '\n' ' ')'";;
esac
fastchecks() {  # <sha> <conclusion of fast-battery>: the fast tier alone reported; the complete tier never ran
  echo "{\"check_runs\":[{\"name\":\"ruff\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"name\":\"mypy\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"name\":\"fast-battery\",\"status\":\"completed\",\"conclusion\":\"$2\"}]}" > "$T/checks-$1"
}
adv dev fast-red; tfr=$(tip dev); before=$(tip stg); n=$(runs)
fastchecks "$tfr" failure; SG_CI_WORKFLOW="$T/ci-complete.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a red fast-tier job holds the tip and runs no suite" \
  || ko "red fast tier: rc $rc, stg $(tip stg), runs $(runs) was $n"
adv dev fast-green; tfg=$(tip dev); n=$(runs); : > "$T/gh.log"; touch "$T/pr-open"
fastchecks "$tfg" success; SG_CI_WORKFLOW="$T/ci-complete.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tfg" ] && [ "$(runs)" = "$n" ] && ok "a green fast tier promotes the tip with no complete-tier run and no suite (#1723)" \
  || ko "green fast tier: rc $rc, stg $(tip stg), runs $(runs) was $n, out '$(cat "$T/out")'"
/usr/bin/grep -q "promote-pass: $tfg promoted: the CI fast tier carried the pass" "$SG_STATE/promote.log" && ok "the record names the fast tier as the proof" \
  || ko "fast record: $(/usr/bin/grep "$tfg" "$SG_STATE/promote.log")"
/usr/bin/grep "promote-pass: $tfg CI fast tier green:" "$SG_STATE/promote.log" | tail -n 1 | /usr/bin/grep -q "ruff, mypy, fast-battery$" \
  && ok "the log names the fast jobs it read and no other" || ko "fast job log: $(/usr/bin/grep "$tfg" "$SG_STATE/promote.log")"
/usr/bin/grep -q -- "statuses/$tfg .*context=squad/full-proof" "$T/gh.log" && ko "a fast-tier pass wrote the squad/full-proof status" \
  || ok "a fast-tier pass writes no squad/full-proof status"
IFS=$'\t' read -r _ kind res sha < "$SG_STATE/gate-status-full"
[ "$sha" = "$tfg" ] && ko "a fast-tier pass wrote the full pass line" || ok "a fast-tier pass writes no full pass line"
/usr/bin/grep -q -- "/labels" "$T/gh.log" && ko "the pass asked for the complete tier on the dev -> stg pull request: $(/usr/bin/grep labels "$T/gh.log")" \
  || ok "the pass never labels the dev -> stg pull request (#1723)"
IFS=$'\t' read -r _ pk pres psha < "$SG_STATE/gate-status-promote" 2>/dev/null
[ "$pk" = fast ] && [ "$pres" = pass ] && [ "$psha" = "$tfg" ] && ok "a fast-tier pass writes the fast proof word for its tip" \
  || ko "fast proof word: '$pk $pres $psha' for $tfg"
# #1723 (plan item): a tiered tip whose CI never reports is proven by the local fast battery, never the full suite.
adv dev fb; tfb=$(tip dev); before=$(tip stg); : > "$T/suite.log"; rm -f "$SG_STATE/gate-status-full" "$SG_STATE/promote-ci-wait" "$T/pr-open"
SG_PROMOTE_CI_WAIT=0 SG_PROMOTE_SUITE="echo fast >> $T/suite.log" SG_CI_WORKFLOW="$T/ci-complete.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tfb" ] && grep -qx fast "$T/suite.log" && ok "a missing fast-tier answer is proven by the local fast battery and promotes" \
  || ko "fallback: rc $rc, stg $(tip stg), dev $tfb, suite '$(cat "$T/suite.log")', out '$(cat "$T/out")'"
IFS=$'\t' read -r _ pk pres psha < "$SG_STATE/gate-status-promote" 2>/dev/null
[ "$pk" = fast ] && [ "$pres" = pass ] && [ "$psha" = "$tfb" ] && ok "the fast battery writes the fast proof word for its tip" \
  || ko "fallback proof word: '$pk $pres $psha'"
/usr/bin/grep -q "$tfb" "$SG_STATE/gate-status-full" 2>/dev/null && ko "the fast battery wrote a full line" || ok "the fast battery writes no full line"
adv dev fr; tfr=$(tip dev); before=$(tip stg); rm -f "$SG_STATE/promote-ci-wait" "$T/pr-open"
SG_PROMOTE_CI_WAIT=0 SG_PROMOTE_SUITE="false" SG_CI_WORKFLOW="$T/ci-complete.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && ok "a red fast battery holds the tip" || ko "red fast battery: rc $rc, stg $(tip stg)"
rm -f "$T/pr-open"

# The Statuses API rejects a description past 140 characters with a 422, so a green set whose names do not fit would never
# write its status and never promote (#1518). The description is asserted on the repository's own workflow, where the shard
# jobs live, and on a fixture of long names: the status carries the names that fit and counts the rest, and the log carries
# the whole list.
desc=$(bash "$d/promote-pass.sh" --ci-description "$d/../../../.github/workflows/ci.yml")
[ -n "$desc" ] && ok "the real workflow's job list yields a status description" || ko "no description from the real workflow"
[ "${#desc}" -le 140 ] && ok "the description of the real workflow's jobs fits the 140 characters of the Statuses API (${#desc})" \
  || ko "the real workflow's description is ${#desc} characters, past the 140 the API takes: $desc"
printf 'jobs:\n' > "$T/ci-long.yml"
for k in $(seq 1 20); do printf '  long-shard-%s:\n    name: the long full-suite shard number %s of the complete tier\n' "$k" "$k" >> "$T/ci-long.yml"; done
ldesc=$(bash "$d/promote-pass.sh" --ci-description "$T/ci-long.yml")
[ "${#ldesc}" -le 140 ] && ok "a workflow of long job names still yields a bounded description (${#ldesc})" \
  || ko "the long-name description is ${#ldesc} characters, past the 140 the API takes: $ldesc"
case "$ldesc" in *"more"*) ok "the bounded description counts the names it left out";; *) ko "the description drops names silently: $ldesc";; esac

# ADR-0164: when the proof is the dev-to-stg check (SG_PROMOTE_PROOF=ci), no local suite runs, so the guards that protect a
# local suite from a loaded box refuse nothing: the load average, the free memory and a nightly that owns the machine. A green
# CI tip promotes even while the box is loaded and a nightly runs, which is the state the loop is in every evening.
adv dev ci-proof-loaded; tcl=$(tip dev); before=$(tip stg); n=$(runs)
mkdir -p "$SG_STATE/locks/nightly"
shards "$tcl" success success success success
SG_PROMOTE_PROOF=ci SG_PROMOTE_LOAD_LIMIT=0 SG_PROMOTE_MEM_MIN_MB=99999999 SG_GHREAD="$T/bin/ghread" pass; rc=$?
rmdir "$SG_STATE/locks/nightly"
[ $rc = 0 ] && [ "$(tip stg)" = "$tcl" ] && [ "$(runs)" = "$n" ] \
  && ok "a CI proof promotes on a loaded box while a nightly runs, and runs no local suite" \
  || ko "CI proof under load: rc $rc, stg $(tip stg), was $before, runs $(runs) was $n, out '$(cat "$T/out")'"

# A landing inside the quiet window holds a local proof, and it never holds one whose proof is already in (ADR-0164): the
# window names a tip so a burst earns one request, and a fast tier that has completed green is promotable at once, with no
# wait for the window and no local suite. A landing costs a local suite its measurement, and a tip whose proof is already in
# spends nothing.
printf '%s\tlanded\n' "$(date +%s)" > "$SG_STATE/landed.tsv"
adv dev ci-proof-quiet; tcq=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tcq" success success running running
SG_PROMOTE_QUIET_SECONDS=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a tip inside the quiet window whose fast tier is unfinished runs no suite and promotes nothing" \
  || ko "quiet window: rc $rc, stg $(tip stg), was $before, runs $(runs) was $n"
shards "$tcq" success success success success
SG_PROMOTE_QUIET_SECONDS=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tcq" ] && [ "$(runs)" = "$n" ] && ok "a green tip inside the quiet window is promoted at once, with no local suite" \
  || ko "green in the quiet window: rc $rc, stg $(tip stg), was $before, runs $(runs) was $n"
# A read that fails is its own fact, never "not green": a GitHub outage inside the quiet window is written to promote.log as a
# distinct line, so a pass that never promotes under an outage is visible instead of silent.
adv dev ci-proof-outage; tou=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tou" success success success success
: > "$T/checks-fail"; SG_PROMOTE_QUIET_SECONDS=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
rm -f "$T/checks-fail"
[ $rc = 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a check read that fails inside the quiet window promotes nothing and runs no suite" \
  || ko "outage: rc $rc, stg $(tip stg), was $before, runs $(runs) was $n"
/usr/bin/grep -q "promote-pass: $tou not proven: the checks could not be read" "$SG_STATE/promote.log" && ok "the failed check read is written as its own line" \
  || ko "outage line: $(/usr/bin/grep "$tou" "$SG_STATE/promote.log")"
# A CI proof never waits for the window either.
adv dev ci-proof-quiet2; tcq2=$(tip dev); shards "$tcq2" success success success success
SG_PROMOTE_PROOF=ci SG_PROMOTE_QUIET_SECONDS=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tcq2" ] && ok "a recent landing holds no CI proof" \
  || ko "quiet window on a CI proof: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
rm -f "$SG_STATE/landed.tsv"

# The proof takes no gate slot: a CI proof beside a held gate lock still promotes, and runs no local suite. The holder is a
# real one (serial.sh's `gate` lock around a sleeper), never a bare pid file; the merge waits for the lock as every
# promotion does, for that one merge and never for a suite.
adv dev ci-proof-gated; tcg=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tcg" success success success success
( SERIAL_HOLD_SECONDS=30 bash "$d/serial.sh" gate /bin/sleep 3 ) & gp=$!
for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
SG_PROMOTE_PROOF=ci SG_GHREAD="$T/bin/ghread" pass; rc=$?
wait "$gp" 2>/dev/null
[ $rc = 0 ] && [ "$(tip stg)" = "$tcg" ] && [ "$(runs)" = "$n" ] \
  && ok "a CI proof promotes beside a held gate lock, and runs no local suite" \
  || ko "CI proof beside a held gate lock: rc $rc, stg $(tip stg), was $before, runs $(runs) was $n, out '$(cat "$T/out")'"

# The check job of the same commit (ruff, mypy, the fast battery) is part of the CI proof: a red one holds the tip, and no
# suite runs for it (ADR-0164: a red the proof measured holds delivery).
adv dev check-red; tc=$(tip dev); before=$(tip stg); n=$(runs)
CHECK_CONCLUSION=failure shards "$tc" success success success success; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a red check job holds the tip and runs no suite" \
  || ko "red check: rc $rc, stg $(tip stg), runs $(runs) was $n"
/usr/bin/grep "promote-pass: $tc" "$SG_STATE/promote.log" | /usr/bin/grep -q "CI ruff is red" && ok "the refusal names the red check job" \
  || ko "red check line: $(/usr/bin/grep "$tc" "$SG_STATE/promote.log")"

# #1322: one red fast-tier job holds the tip even when the other two are green, and the refusal names that job.
adv dev mypy-red; tm=$(tip dev); before=$(tip stg); n=$(runs)
shards "$tm" success success success success
sed 's/"name":"mypy","status":"completed","conclusion":"success"/"name":"mypy","status":"completed","conclusion":"failure"/' "$T/checks-$tm" > "$T/checks-$tm.x" && mv "$T/checks-$tm.x" "$T/checks-$tm"
SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] && ok "a red mypy job holds the tip and runs no suite" \
  || ko "red mypy: rc $rc, stg $(tip stg), runs $(runs) was $n"
/usr/bin/grep "promote-pass: $tm" "$SG_STATE/promote.log" | /usr/bin/grep -q "CI mypy is red" && ok "the refusal names the red mypy job" \
  || ko "red mypy line: $(/usr/bin/grep "$tm" "$SG_STATE/promote.log")"

# #1322: the check names are read from the job block alone. A step name, a `with:` key, a four-space name: before jobs:, or a
# job-shaped key after the block is not a check run: the answer is exactly the workflow's job names, at their own indent.
printf 'env:\n    name: not-a-job\njobs:\n  ruff:\n    name: ruff\n    steps:\n      - name: Ruff\n      - uses: actions/setup-python@v5\n        with:\n          name: step-with\n  mypy:\n    name: mypy\n  fast-battery:\n    name: fast-battery\nnot-a-job:\n  name: outside-jobs\n' > "$T/ci-step-names.yml"
out=$(bash "$d/promote-pass.sh" --ci-job-names "$T/ci-step-names.yml" 2>&1)
[ "$out" = $'ruff\nmypy\nfast-battery' ] && ok "the check names come from the job block, never a step, with: key or a name: outside it (#1322)" \
  || ko "job names: '$out'"

# #1461: every job of the workflow is a check the dev tip waits on, the complete tier included, whichever line of the job
# carries its gate. A job gated to another pull request never runs on the dev-to-stg one, so the pass refuses the promotion
# and names it, rather than recording a full pass over the jobs that did run.
printf 'jobs:\n  ruff:\n    name: ruff\n  full-xdist:\n    if: github.base_ref == '"'"'main'"'"'\n    name: full-xdist\n  full-serial:\n    name: full-serial\n    if: github.base_ref == '"'"'main'"'"'\n  mypy:\n    name: mypy\n  fast-battery:\n    name: fast-battery\n' > "$T/ci-tiers.yml"
out=$(bash "$d/promote-pass.sh" --ci-job-names "$T/ci-tiers.yml" 2>&1)
[ "$out" = $'ruff\nfull-xdist\nfull-serial\nmypy\nfast-battery' ] && ok "the check names are every job of the workflow, a gated one included (#1461)" \
  || ko "tier job names: '$out'"
adv dev gated-job; tgj=$(tip dev); before=$(tip stg); n=$(runs)
SG_CI_WORKFLOW="$T/ci-tiers.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$(runs)" = "$n" ] \
  && ok "a job gated to another pull request refuses the promotion and runs no suite" \
  || ko "gated job: rc $rc, stg $(tip stg), runs $(runs) was $n"
/usr/bin/grep "promote-pass: $tgj" "$SG_STATE/promote.log" | /usr/bin/grep -q "gates full-xdist, full-serial to another pull request" \
  && ok "the refusal names the jobs the dev tip could never prove" \
  || ko "gated job line: $(/usr/bin/grep "$tgj" "$SG_STATE/promote.log")"

# #1322: the promotion record names the jobs the pass read, so a renamed job set is recorded as it is, never a stale literal.
printf 'jobs:\n  lint:\n    name: lint\n  types:\n    name: types\n  battery:\n    name: battery\n' > "$T/ci-renamed.yml"
adv dev renamed; tx=$(tip dev)
CHECK_JOBS="lint types battery" shards "$tx" none none none none
SG_CI_WORKFLOW="$T/ci-renamed.yml" SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tx" ] && ok "a renamed fast tier promotes on its own check names (#1322)" \
  || ko "renamed tier: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
/usr/bin/grep -q -- "description=full suite, both modes, every CI job: lint, types, battery" "$T/gh.log" && ok "the full-proof status names the jobs the pass read, never a literal" \
  || ko "status description: $(/usr/bin/grep "statuses/$tx" "$T/gh.log")"

# A tip inside the quiet window opens the dev-to-stg pull request, so its four shards start before the proof reads them.
adv dev shard-open; to=$(tip dev); n=$(runs); : > "$T/gh.log"
printf '%s\t%s\n' "$(date +%s)" "$to" > "$SG_STATE/landed.tsv"
SG_PROMOTE_QUIET_SECONDS=900 SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ "$(tip stg)" != "$to" ] && ok "a tip inside the quiet window runs no suite and promotes nothing" \
  || ko "quiet window: rc $rc, runs $(runs) was $n"
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ok "a tip inside the quiet window opens the dev-to-stg pull request" \
  || ko "no pull request opened in the quiet window: $(cat "$T/gh.log")"
rm -f "$SG_STATE/landed.tsv"
shards "$to" success success success success; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$to" ] && ok "the next pass reads the shards that ran on the opened pull request, and promotes by CI" \
  || ko "next pass after the pull request: rc $rc, stg $(tip stg), dev $to"

# #1495: the CI path promotes through the CI proof of the tip promote-stg.sh reads, never through the proved tip. A landing that
# moves dev between the proof of A and the promotion makes B the tip: B promotes when its CI is green, waits (no marker, exit 0)
# when its CI is pending, and a refusal names B, never A.
moved() {  # <B's checks: green, pending or red>: prove A by CI, move dev to B while the pass runs, then pass
  adv dev "moved-$1"; ma=$(tip dev); before=$(tip stg)
  shards "$ma" success success success success
  case "$1" in
    green) shards default success success success success;;
    pending) printf '{"check_runs":[{"name":"ruff","status":"in_progress","conclusion":null}]}' > "$T/checks-default";;
    red) printf '{"check_runs":[{"name":"ruff","status":"completed","conclusion":"failure"}]}' > "$T/checks-default";;
  esac
  printf '%s' "$ma" > "$T/move-on"; : > "$SG_STATE/promote-pass.tips"
  SG_GHREAD="$T/bin/ghread" pass; rc=$?; mb=$(tip dev); rm -f "$T/checks-default" "$T/move-on"; }
moved green
[ $rc = 0 ] && [ "$mb" != "$ma" ] && [ "$(tip stg)" = "$mb" ] && ok "CI green on A and dev moved to B with CI green: the pass promotes B (#1495)" \
  || ko "moved green: rc $rc, A $ma, B $mb, stg $(tip stg), out '$(cat "$T/out")'"
/usr/bin/grep -q "stale" "$SG_STATE/promote.log" && ko "a moved tip wrote a stale refusal: $(/usr/bin/grep stale "$SG_STATE/promote.log")" || ok "a moved tip writes no stale refusal (#1495)"
moved pending
[ $rc = 0 ] && [ "$mb" != "$ma" ] && [ "$(tip stg)" = "$before" ] && ok "dev moved to B with CI pending: the pass exits 0 and promotes nothing (#1495)" \
  || ko "moved pending: rc $rc, A $ma, B $mb, stg $(tip stg), out '$(cat "$T/out")'"
[ ! -s "$SG_STATE/promote-pass.tips" ] && ok "a pending B leaves no marker for A or B, so a later pass retries (#1495)" \
  || ko "moved pending marker: $(cat "$SG_STATE/promote-pass.tips")"
moved red
[ $rc != 0 ] && [ "$(tip stg)" = "$before" ] && ok "dev moved to B with CI red: the pass refuses (#1495)" || ko "moved red: rc $rc, stg $(tip stg)"
/usr/bin/grep -q "^$mb " "$SG_STATE/promote-pass.tips" && ! /usr/bin/grep -q "^$ma " "$SG_STATE/promote-pass.tips" \
  && ok "the refusal marker names B, never the proved tip A (#1495)" || ko "moved red marker: A $ma, B $mb: $(cat "$SG_STATE/promote-pass.tips")"
# The local-suite and delta invocations keep the local proof; the CI path names its proof word (fast for a tiered workflow, #1723,
# else ci), and the fast battery fallback calls the fast proof.
nl=$(/usr/bin/grep -c 'SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh"' "$d/promote-pass.sh")
nw=$(/usr/bin/grep -c 'SG_PROMOTE_PROOF=$proof_word bash "$d/promote-stg.sh"' "$d/promote-pass.sh")
nf=$(/usr/bin/grep -c 'SG_PROMOTE_PROOF=fast bash "$d/promote-stg.sh"' "$d/promote-pass.sh")
na=$(/usr/bin/grep -c 'bash "$d/promote-stg.sh"' "$d/promote-pass.sh")
[ "$nl" = 3 ] && [ "$nw" = 1 ] && [ "$nf" = 1 ] && [ "$na" = 7 ] && ok "the local-suite and delta calls pass the local proof, the CI path the fast or ci word, the fallback the fast word (#1723)" \
  || ko "promote-stg.sh calls: local $nl, word $nw, fast $nf, all $na"

# #1086: an interpreter that lacks a tool the suite runs refuses the pass before any suite runs. The refusal names the tool,
# writes no marker and no status, and the same tip is proved by a later pass once an interpreter holds the tool.
mkpy() {  # <path> <tool it lacks, or none>: a stand-in interpreter that logs its path and fails any probe of that tool
  mkdir -p "$(dirname "$1")"
  cat > "$1" <<EOF
#!/bin/bash
echo "\$0" >> "$T/py.log"
case "\$*" in *"-m $2 "*|*"import $2"*) exit 1;; esac
exit 0
EOF
  chmod +x "$1"; }
for tool in ruff mypy pytest; do
  adv dev "no-$tool"; tt=$(tip dev); before=$(tip stg); : > "$T/gh.log"
  mkpy "$T/no-$tool/bin/python" "$tool"
  env -u SG_PROMOTE_SUITE SG_GATE_PY="$T/no-$tool/bin/python" bash "$d/promote-pass.sh" > "$T/out" 2>&1; rc=$?
  [ $rc = 2 ] && /usr/bin/grep -q "holds no $tool," "$T/out" && [ "$(tip stg)" = "$before" ] \
    && ! /usr/bin/grep -q "statuses/$tt" "$T/gh.log" && ! /usr/bin/grep -q "^$tt " "$SG_STATE/promote-pass.tips" 2>/dev/null \
    && ! /usr/bin/grep -q "$tt" "$SG_STATE/gate-status-delta" 2>/dev/null \
    && ok "an interpreter with no $tool refuses the pass, names $tool, and leaves the tip unmarked" \
    || ko "no $tool: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
done
mkpy "$T/all-tools/bin/python" none
env -u SG_PROMOTE_SUITE SG_GATE_PY="$T/all-tools/bin/python" bash "$d/promote-pass.sh" > "$T/out" 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tt" ] && ok "the refused tip is proved by a later pass once an interpreter holds every tool" \
  || ko "retry after refusal: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"

# #1086: a checkout with no venv of its own resolves the venv beside its common git directory, not the system python3.
$W worktree add -q "$T/wt-novenv" -b novenv; mkpy "$T/w/.venv/bin/python" none
adv dev venv-beside; tv=$(tip dev); rm -f "$T/py.log" "$SG_STATE/gate-carried" "$SG_STATE/gate-status-delta"
env -u SG_GATE_PY -u SG_PROMOTE_SUITE SG_CHECKOUT="$T/wt-novenv" bash "$d/promote-pass.sh" > "$T/out" 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tv" ] && ok "a checkout with no venv of its own promotes its tip" \
  || ko "no venv of its own: rc $rc, stg $(tip stg), out '$(cat "$T/out")'"
/usr/bin/grep -q "w/.venv/bin/python" "$T/py.log" 2>/dev/null && ok "the suite's interpreter is the venv beside the common git directory" \
  || ko "interpreter used: '$(cat "$T/py.log" 2>/dev/null)'"

# ADR-0164 (#1046): promote-stg.sh takes the bound-ended proof only for the tip promote-pass.sh recorded indeterminate, and never for a
# tip whose last proof was red. A red the delta measured holds the tip, and writes no indeterminate record.
adv dev nobound; tn=$(tip dev); rm -f "$SG_STATE/promote-indeterminate"; sb=$(tip stg)
SG_PROMOTE_PROOF=bound bash "$d/promote-stg.sh" > "$T/out7" 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(tip stg)" = "$sb" ] && /usr/bin/grep -q "no bound-ended proof" "$T/out7" && ok "a bound-ended proof for a tip with no indeterminate record is refused" \
  || ko "no indeterminate record: rc $rc, stg $(tip stg), out '$(cat "$T/out7")'"
adv dev redlast; tr=$(tip dev); printf '%s\t%s\t1\n' "$(date +%s)" "$tr" > "$SG_STATE/promote-indeterminate"
printf '%s\tfull\tfail\t%s\n' "$(date +%s)" "$tr" > "$SG_STATE/gate-status-full"
SG_PROMOTE_PROOF=bound bash "$d/promote-stg.sh" > "$T/out8" 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(tip stg)" = "$sb" ] && /usr/bin/grep -q "is a fail" "$T/out8" && ok "a bound-ended proof for a tip whose last full proof was red is refused" \
  || ko "red full proof: rc $rc, stg $(tip stg), out '$(cat "$T/out8")'"
printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$ts" > "$SG_STATE/gate-status-full"; printf '%s\tdelta\tfail\t%s\t%s\tx\n' "$(date +%s)" "$ts" "$tr" > "$SG_STATE/gate-status-delta"
SG_PROMOTE_PROOF=bound bash "$d/promote-stg.sh" > "$T/out9" 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(tip stg)" = "$sb" ] && /usr/bin/grep -q "delta proof of the dev tip is a fail" "$T/out9" && ok "a bound-ended proof for a tip whose last delta was red is refused" \
  || ko "red delta: rc $rc, stg $(tip stg), out '$(cat "$T/out9")'"
rm -f "$SG_STATE/gate-status-delta"
$W fetch -q origin; $W checkout -q -B dev origin/dev; p1=$(tip dev); printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$p1" > "$SG_STATE/gate-status-full"
echo x > "$T/w/contrib/squad-goal/scripts/test-nightly.sh"; $W add contrib/squad-goal/scripts/test-nightly.sh; $W commit -q -m covered-red; $W push -q origin dev
td=$(tip dev); sb=$(tip stg); rm -f "$SG_STATE/promote-indeterminate" "$SG_STATE/promote-bound" "$SG_STATE/gate-status-delta"; echo 30 > "$T/suite-sleep"
SG_DELTA_SUITE=false SG_PROMOTE_BOUND_SECONDS=1 SG_PROMOTE_FALLBACK_SECONDS=0 bash "$d/promote-pass.sh" > "$T/out10" 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(tip stg)" = "$sb" ] && [ ! -e "$SG_STATE/promote-indeterminate" ] && [ ! -e "$SG_STATE/promote-bound" ] \
  && ok "a red the delta measured holds the tip and writes no bound-ended record" || ko "red delta in pass: rc $rc, stg $(tip stg), out '$(cat "$T/out10")'"
/usr/bin/grep -q "^$td not promoted: the delta suite failed" "$SG_STATE/promote-pass.tips" && ok "the delta's red is marked refused for its tip" \
  || ko "delta red mark: '$(/usr/bin/grep "^$td " "$SG_STATE/promote-pass.tips" 2>/dev/null)'"
rm -f "$T/suite-sleep"

# (spec 1163, #1174) The promotion reads the gate's three outcomes with the wave entry's meaning: pass promotes, fail holds,
# and an unfinished proof never rides the full line. A full line that says `unproven` is refused by its own name, because the
# bound-ended proof is the one route an unfinished proof takes, and it carries its obligation marker.
adv dev vocab-unproven; tv=$(tip dev); sb=$(tip stg)
printf '%s\tfull\tunproven\t%s\n' "$(date +%s)" "$tv" > "$SG_STATE/gate-status-full"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/promote-indeterminate"
SG_PROMOTE_PROOF=local bash "$d/promote-stg.sh" > "$T/out12" 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(tip stg)" = "$sb" ] && /usr/bin/grep -q "is unproven, not a pass" "$T/out12" \
  && ok "an unproven local proof is refused by its own word, never read as a pass" \
  || ko "unproven local proof: rc $rc, stg $(tip stg), out '$(cat "$T/out12")'"

rm -f "$T/suite-rc" "$T"/checks-*

# (#1171) One deadline covers the whole attempt, and the delta keeps a reserved slice of it. The clock starts at the proof
# lock; the suite is bounded at what is left once the delta's slice is kept, the delta at what is left then, and the clock
# never restarts. A delta that hangs past its slice is stopped and the attempt is bounded, never a pass; a delta that answers
# inside its slice still promotes; and a defect the suite reported before the deadline holds the tip as a red.
deltahold() {  # <seconds>: hold the delta proof's lock so the first delta attempt is skipped, never queued
  SERIAL_HOLD_SECONDS="$1" bash "$d/serial.sh" full-proof-delta sleep "$1" 2>/dev/null & dh=$!
}
adead() {  # one bounded attempt: <suite> <bound> <fallback> <delta suite> <tip before the change is p0>
  rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/promote-indeterminate" "$SG_STATE/promote-bound"
  local s0=$(date +%s)
  SG_PROMOTE_SUITE="$1" SG_PROMOTE_BOUND_SECONDS="$2" SG_PROMOTE_FALLBACK_SECONDS="$3" SG_DELTA_SUITE="$4" SG_PROMOTE_RESERVE_SECONDS=0 \
    bash "$d/promote-pass.sh" > "$T/outd" 2>&1; rc=$?
  el=$(( $(date +%s) - s0 ))
}
# The gate uses the rest of the budget and the delta keeps its slice: the suite runs to its bound, the hanging delta gets
# only its reserved slice, and the attempt is recorded bounded.
adv dev deadline-a; ta=$(tip dev); p0=$(git -C "$R" rev-parse refs/heads/dev^); before=$(tip stg)
printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$p0" > "$SG_STATE/gate-status-full"; echo 30 > "$T/suite-sleep"
deltahold 3; adead "$T/suite" 7 3 "trap '' TERM; sleep $HANG_NAP"; wait "$dh" 2>/dev/null
[ "$el" -lt 18 ] && ok "gate plus delta cannot exceed one budget (${el}s for a 7s budget)" \
  || ko "the attempt ran past its one budget: ${el}s, out '$(cat "$T/outd")'"
/usr/bin/grep -q "the delta's 3s slice of the 7s budget is reserved" "$T/outd" \
  && ok "the delta's slice is reserved before the gate starts" \
  || ko "no reserved-slice line: '$(tail -n 3 "$T/outd")'"
[ -e "$SG_STATE/promote-bound" ] && [ "$(tip stg)" = "$ta" ] && ok "a delta that outran its slice is bounded, and the tip ships as bounded" \
  || ko "bounded attempt: stg $(tip stg), dev $ta, marker $([ -e "$SG_STATE/promote-bound" ] && echo yes || echo no)"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the delta's tree survived the deadline" || ok "no descendant process survives the deadline"
rm -f "$T/suite-sleep"
# The delta answers inside its reserved slice even though the gate used the rest, and the attempt promotes.
adv dev deadline-b; tb=$(tip dev); p0=$(git -C "$R" rev-parse refs/heads/dev^); before=$(tip stg)
printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$p0" > "$SG_STATE/gate-status-full"; echo 30 > "$T/suite-sleep"
deltahold 3; adead "$T/suite" 20 8 "sleep 2; true"; wait "$dh" 2>/dev/null
[ "$el" -lt 28 ] && [ "$(tip stg)" = "$tb" ] && ok "the delta's slice is available even when the gate used the rest (${el}s)" \
  || ko "delta slice: stg $(tip stg), dev $tb, ${el}s, out '$(cat "$T/outd")'"
IFS=$'\t' read -r _ dk dres dP dT _ < "$SG_STATE/gate-status-delta" 2>/dev/null
[ "$dk" = delta ] && [ "$dres" = pass ] && [ "$dT" = "$tb" ] && ok "the delta answered inside its slice and the record names its pass" \
  || ko "delta record: '$(cat "$SG_STATE/gate-status-delta" 2>/dev/null)'"
rm -f "$T/suite-sleep"
# A bounded attempt with a defect the suite reported is a fail, never bounded: the tip is held and no bound marker written.
adv dev deadline-red; tc=$(tip dev); p0=$(git -C "$R" rev-parse refs/heads/dev^); before=$(tip stg)
printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$p0" > "$SG_STATE/gate-status-full"
printf '#!/bin/bash\necho "FAILED tests/test_x.py::test_one - boom"\ntrap "" TERM\nsleep $HANG_NAP\n' > "$T/defect-suite"
deltahold 3; adead "$T/defect-suite" 4 1 true; wait "$dh" 2>/dev/null
[ "$rc" != 0 ] && [ "$(tip stg)" = "$before" ] && [ "$el" -lt 15 ] && ok "a bounded attempt that reported a defect holds the tip (${el}s)" \
  || ko "bounded defect: rc $rc, stg $(tip stg), ${el}s, out '$(cat "$T/outd")'"
IFS=$'\t' read -r _ kind res dsha < "$SG_STATE/gate-status-full" 2>/dev/null
[ "$kind $res $dsha" = "full fail $tc" ] && ok "a bounded attempt with a reported defect is recorded as fail" \
  || ko "bounded defect record: '$kind $res $dsha'"
[ ! -e "$SG_STATE/promote-indeterminate" ] && [ ! -e "$SG_STATE/promote-bound" ] && ok "a bounded attempt with a reported defect writes no bound marker" \
  || ko "a red the bound ended was marked bounded"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the defect suite's tree survived the deadline" || ok "the defect suite's tree is stopped at the deadline"

# (#1490) A transient failure is unproven, never a refusal: the pass writes no tip marker, and the next pass promotes the tip.
# (a) the open dev -> stg pull request cannot be read. The read fails for the whole of this pass (the open-only call reads it
# too), and the next pass reads it again.
adv dev read-fail; tr=$(tip dev); rm -f "$T/suite-rc" "$T/suite-out" "$T/checks-"* "$T/read-fail"; : > "$T/read-fail"
SG_GHREAD="$T/bin/ghread" pass; rc=$?
! /usr/bin/grep -q "^$tr " "$SG_STATE/promote-pass.tips" 2>/dev/null && [ "$(tip stg)" != "$tr" ] \
  && ok "a pull request that cannot be read is pending: no tip marker, stg not moved (rc $rc)" \
  || ko "read failure marked the tip: rc $rc, tips '$(cat "$SG_STATE/promote-pass.tips" 2>/dev/null)', stg $(tip stg)"
rm -f "$T/read-fail"; SG_GHREAD="$T/bin/ghread" pass; rc=$?
[ "$(tip stg)" = "$tr" ] && ok "the next pass promotes the tip once the pull request reads (rc $rc)" \
  || ko "retry after read failure: rc $rc, stg $(tip stg), dev $tr, out '$(cat "$T/out")'"
# (b) the suite waits on the full machine's gate slots: exit 73 and the cap line name the wait, so the tip is retried, not failed.
adv dev cap-full; tf=$(tip dev); sf=$(tip stg); rm -f "$T/suite-rc" "$T"/checks-*
printf 'caps: caps.gates is full (2 slots), held by run-9: gate skipped, not queued (try)\n' > "$T/suite-out"; echo 73 > "$T/suite-rc"
pass; rc=$?
! /usr/bin/grep -q "^$tf " "$SG_STATE/promote-pass.tips" 2>/dev/null && [ "$(tip stg)" = "$sf" ] \
  && ! /usr/bin/grep -q "^$tf full fail" "$SG_STATE/gate-status-full" 2>/dev/null \
  && ok "a full gate cap is not proven: no tip marker, no fail line (rc $rc)" \
  || ko "full cap marked the tip: rc $rc, tips '$(cat "$SG_STATE/promote-pass.tips" 2>/dev/null)', full '$(cat "$SG_STATE/gate-status-full" 2>/dev/null)'"
rm -f "$T/suite-out" "$T/suite-rc"; pass; rc=$?
[ "$(tip stg)" = "$tf" ] && ok "the next pass proves and promotes the tip once the cap is free (rc $rc)" \
  || ko "retry after full cap: rc $rc, stg $(tip stg), dev $tf, out '$(cat "$T/out")'"
# (c) promote-stg.sh itself is refused by the gate cap: it re-execs under gate.sh in try mode and exits 73 with the cap line. The
# pass treats that as a wait, as it treats 75: no tip marker, stg not moved, and the next pass promotes the same tip.
rm -rf "$T/scripts"; cp -R "$d" "$T/scripts"
printf '#!/bin/bash\necho "caps: caps.gates is full (2 slots), held by run-9: gate skipped, not queued (try)"\nexit 73\n' > "$T/scripts/promote-stg.sh"
rm -f "$T/suite-rc" "$T/suite-out" "$T/checks-"*
adv dev stg-cap; tc=$(tip dev); sc=$(tip stg)
bash "$T/scripts/promote-pass.sh" > "$T/out" 2>&1; rc=$?
! /usr/bin/grep -q "^$tc " "$SG_STATE/promote-pass.tips" 2>/dev/null && [ "$(tip stg)" = "$sc" ] \
  && ok "a promote-stg.sh refused by the gate cap writes no tip marker and does not move stg (rc $rc)" \
  || ko "promote-stg cap marked the tip: rc $rc, tips '$(cat "$SG_STATE/promote-pass.tips" 2>/dev/null)', stg $(tip stg), out '$(cat "$T/out")'"
pass; rc=$?
[ "$(tip stg)" = "$tc" ] && ok "the next pass promotes the same tip once the cap is free (rc $rc)" \
  || ko "retry after promote-stg cap: rc $rc, stg $(tip stg), dev $tc, out '$(cat "$T/out")'"

# (#1723) Before a local battery starts, and when it ends, the pass reads stg again: a tip stg already holds runs no battery and
# writes no verdict. (a) stg takes the tip after the pass's first read: the stub gh moves it when the pass opens the pull request.
adv dev held-before; th=$(tip dev); rm -f "$T/suite-rc" "$T/suite-out" "$T"/checks-* "$SG_STATE/delta-pass.log"; n=$(runs)
: > "$T/stg-takes-dev"; pass; rc=$?; rm -f "$T/stg-takes-dev"
[ $rc = 0 ] && [ "$(runs)" = "$n" ] && [ ! -e "$SG_STATE/delta-pass.log" ] && ok "a tip stg took before the battery runs no battery" || ko "held before: rc $rc, runs $(runs) (was $n), delta log $([ -e "$SG_STATE/delta-pass.log" ] && echo written || echo none), out '$(cat "$T/out")'"
! /usr/bin/grep -q "^$th " "$SG_STATE/promote-pass.tips" 2>/dev/null && ! /usr/bin/grep -q "$th" "$SG_STATE/gate-status-full" 2>/dev/null \
  && ok "a tip stg took before the battery gets no verdict and no marker" || ko "held before wrote a verdict: tips '$(/usr/bin/grep "^$th " "$SG_STATE/promote-pass.tips")', full '$(cat "$SG_STATE/gate-status-full" 2>/dev/null)'"
/usr/bin/grep -q "promote-pass: $th stg already holds the tip" "$SG_STATE/promote.log" && ok "the pass says stg already holds the tip" || ko "held before: no line: $(tail -n 2 "$SG_STATE/promote.log")"
# (b) stg takes the tip while the battery runs: the battery ends red, the pass reads stg, and no verdict is written.
adv dev held-after; ta=$(tip dev)
printf '#!/bin/bash\necho moved >> "%s/suite.log"\n/usr/bin/git -C "%s" update-ref refs/heads/stg "%s"\nexit 1\n' "$T" "$R" "$ta" > "$T/suite-moves"; chmod +x "$T/suite-moves"
n=$(runs); SG_PROMOTE_SUITE="$T/suite-moves" pass; rc=$?
[ "$(runs)" = "$((n + 1))" ] && ok "the battery ran on a tip stg did not hold yet" || ko "held after: runs $(runs) (was $n), out '$(cat "$T/out")'"
[ $rc = 0 ] && ! /usr/bin/grep -q "^$ta " "$SG_STATE/promote-pass.tips" 2>/dev/null && ! /usr/bin/grep -q "$ta" "$SG_STATE/gate-status-full" 2>/dev/null \
  && ok "a tip stg took while the battery ran gets no verdict and no marker" || ko "held after wrote a verdict: rc $rc, tips '$(/usr/bin/grep "^$ta " "$SG_STATE/promote-pass.tips")', full '$(cat "$SG_STATE/gate-status-full" 2>/dev/null)'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
pkill -KILL -f "sleep $HANG_NAP" 2>/dev/null   # a red run leaves the hang its case started
rm -rf "$T"; exit $fail
