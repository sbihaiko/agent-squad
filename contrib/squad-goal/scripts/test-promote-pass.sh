#!/bin/bash
# The promotion pass (#459, ADR-0145 rule 3): the cycle earns the full proof of the dev tip and promotes it. In one hold of the
# gate lock it runs the suite in a clean tree at the exact tip, writes the full line, writes a commit status on the sha, reads it
# back, and fast-forwards stg through promote-stg.sh. A held gate lock skips the tick; a tip already proved or refused, or a
# landing inside the quiet window, runs no suite. Offline: a bare local remote, a stub gh, a stub suite. usage: bash test-promote-pass.sh
unset SG_SERIAL_HELD SERIAL_TRY
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/st SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SG_GHREAD=true SG_PROMOTE_QUIET_SECONDS=0 SERIAL_POLL_SECONDS=0.2
export SG_PROMOTE_LOAD_LIMIT=1000000   # the box the suite runs on is never idle: the load case below names its own bound
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
printf '#!/bin/bash\necho "$(pwd -P) $(git rev-parse HEAD) $([ -d "$SG_STATE/locks/full-proof" ] && echo held || echo free)-$([ -d "$SG_STATE/locks/gate" ] && echo gateheld || echo gatefree) $(git status --porcelain | wc -l | tr -d " ")" >> "%s/suite.log"\necho "$([ -e "$SG_STATE/promote-reserve" ] && echo reserved || echo unreserved)" >> "%s/reserve.log"\ns=$(cat "%s/suite-sleep" 2>/dev/null); [ -n "$s" ] && sleep "$s"\nexit $(cat "%s/suite-rc" 2>/dev/null || echo 0)\n' "$T" "$T" "$T" "$T" > "$T/suite"
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

# The promotion is the loop's, on the proof of the dev tip, and never a cycle step (#576, ADR-0145 rule 3): the proof held the gate lock and no wave landed meanwhile.
/usr/bin/grep -vE '^[[:space:]]*#' "$d/watch.sh" | /usr/bin/grep -q 'promote-pass\.sh' && ko "watch.sh still runs the promotion" || ok "watch.sh runs no promotion step: the loop promotes on the proof, never from the cycle"
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
# proof keeps running, so the loop dispatches beside it. Past its deadline the proof is killed and recorded indeterminate:
# it promotes nothing, writes no gate status, and is not marked refused, so a later pass retries the same tip.
adv dev bound; tb=$(tip dev); echo 6 > "$T/suite-sleep"; rm -f "$T/suite-rc" "$SG_STATE/promote-reserve"
SG_PROMOTE_RESERVE_SECONDS=1 SG_PROMOTE_DEADLINE_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out2" 2>&1 & bp=$!
for _ in $(seq 1 40); do sleep 0.25; [ -e "$SG_STATE/promote-reserve" ] && break; done
[ -e "$SG_STATE/promote-reserve" ] && ok "the proof reserves the machine at its start" || ko "the proof never reserved the machine"
for _ in $(seq 1 60); do sleep 0.25; [ ! -e "$SG_STATE/promote-reserve" ] && break; done
{ [ ! -e "$SG_STATE/promote-reserve" ] && kill -0 "$bp" 2>/dev/null; } && ok "past its reserve bound the reserve lifts while the proof still runs" \
  || ko "the reserve did not lift while the proof ran"
wait "$bp"; rc=$?
[ "$rc" = 0 ] && [ "$(tip stg)" = "$tb" ] && ok "a proof that lifted its reserve still promotes its tip" || ko "lifted proof: rc $rc, stg $(tip stg), dev $tb"
rm -f "$T/suite-sleep"
adv dev slow; ts=$(tip dev); before=$(tip stg); echo 30 > "$T/suite-sleep"; rm -f "$SG_STATE/promote-indeterminate" "$T/suite-rc"
s0=$(date +%s); SG_PROMOTE_DEADLINE_SECONDS=1 SG_PROMOTE_RESERVE_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out3" 2>&1; rc=$?; el=$(( $(date +%s) - s0 ))
[ "$rc" != 0 ] && [ "$el" -lt 15 ] && ok "a proof past its deadline is killed, not awaited (${el}s)" || ko "deadline: rc $rc after ${el}s"
[ "$(tip stg)" = "$before" ] && ok "a killed proof promotes nothing" || ko "a killed proof promoted stg to $(tip stg)"
IFS=$'\t' read -r _ mt mb < "$SG_STATE/promote-indeterminate" 2>/dev/null
[ "$mt" = "$ts" ] && [ "$mb" = 1 ] && ok "the indeterminate record names the tip and the bound it passed" \
  || ko "indeterminate record: '$(cat "$SG_STATE/promote-indeterminate" 2>/dev/null)'"
[ "$(/usr/bin/grep -c "^$ts " "$SG_STATE/promote-pass.tips" 2>/dev/null)" = 0 ] && ok "an indeterminate tip is not marked refused, so a later pass retries it" \
  || ko "the indeterminate tip was marked refused"
/usr/bin/grep -q "promote-pass: $ts not tested" "$SG_STATE/promote.log" && ok "the log says the tip is not tested" || ko "no not-tested line for $ts"
rm -f "$T/suite-sleep"

# An owner-named bound still reserves the machine while the suite runs, and the marker is gone after the proof: a standing
# marker costs run slots, so it stands only when the owner asks for the measurement to be shielded.
adv dev held; th=$(tip dev); : > "$T/reserve.log"; rm -f "$T/suite-rc"
SG_PROMOTE_RESERVE_SECONDS=600 bash "$d/promote-pass.sh" > "$T/out5" 2>&1; rc=$?
[ "$(tail -n 1 "$T/reserve.log")" = reserved ] && ok "an owner-named reserve bound holds the machine while the suite runs" \
  || ko "named bound: '$(tail -n 1 "$T/reserve.log")'"
[ ! -e "$SG_STATE/promote-reserve" ] && ok "the named reserve is gone after the proof" || ko "the named reserve outlived the proof"
[ "$rc" = 0 ] && [ "$(tip stg)" = "$th" ] && ok "a named-reserve proof still promotes its tip" || ko "named reserve promote: rc $rc, stg $(tip stg)"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
