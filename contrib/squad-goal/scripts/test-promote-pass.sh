#!/bin/bash
# The promotion pass (#459, ADR-0145 rule 3): the cycle earns the full proof of the dev tip and promotes it. In one hold of the
# gate lock it runs the suite in a clean tree at the exact tip, writes the full line, writes a commit status on the sha, reads it
# back, and fast-forwards stg through promote-stg.sh. A held gate lock skips the tick; a tip already proved or refused, or a
# landing inside the quiet window, runs no suite. Offline: a bare local remote, a stub gh, a stub suite. usage: bash test-promote-pass.sh
unset SG_SERIAL_HELD SERIAL_TRY
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/st SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SG_GHREAD=true SG_PROMOTE_QUIET_SECONDS=0 SERIAL_POLL_SECONDS=0.2
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
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The stub suite logs where it ran, the HEAD it ran at, and whether the gate lock was held around it; $T/suite-rc is its exit.
printf '#!/bin/bash\necho "$(pwd -P) $(git rev-parse HEAD) $([ -d "$SG_STATE/locks/gate" ] && echo held || echo free) $(git status --porcelain | wc -l | tr -d " ")" >> "%s/suite.log"\nexit $(cat "%s/suite-rc" 2>/dev/null || echo 0)\n' "$T" "$T" > "$T/suite"
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
[ "$lock" = held ] && ok "the suite ran inside the gate lock" || ko "the suite ran outside the gate lock"
[ "$dirty" = 0 ] && ok "the suite ran in a clean tree" || ko "the suite's tree was dirty ($dirty)"
case "$where" in "$HOME"/wt-*|*/wt-*) ko "the suite tree is a wt-* tree, which wtclean removes: $where";; *) ok "the suite tree is not a wt-* tree";; esac
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
(bash "$d/serial.sh" gate sleep 6) & hp=$!; for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
n=$(runs); s0=$(date +%s); pass; rc=$?; el=$(( $(date +%s) - s0 ))
[ "$el" -lt 4 ] && [ "$(runs)" = "$n" ] && ok "a held gate lock makes the pass return instead of wait (${el}s)" || ko "held lock: ${el}s, runs $(runs) (was $n)"
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

# The owner starts each promotion (#576, ADR-0145 rule 3): no cycle step runs promote-pass.sh.
/usr/bin/grep -vE '^[[:space:]]*#' "$d/watch.sh" | /usr/bin/grep -q 'promote-pass\.sh' && ko "watch.sh still runs the promotion" || ok "watch.sh runs no promotion step: the owner starts it"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
