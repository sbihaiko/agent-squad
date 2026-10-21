#!/bin/bash
# The promotion line (ADR-0145, #439): promote-stg.sh moves stg to the dev tip only when the promotion proof is green on that
# tip, and never otherwise. SG_PROMOTE_PROOF names the proof.
#   ci (the default): the dev tip's CI. Green promotes; red, running or a missing run promotes nothing.
#   local: the promotion gate's full-result line, $SG_STATE/gate-status-full, which the run that earns it writes through the existing
#     seam: `SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'` (gate.sh's header names the line and the kind). stg
#     moves only when that line says `full`, `pass`, and names the dev tip exactly.
# The owner fast-forwards main to the stg tip with one command, and it refuses when main is not an ancestor of stg
# (promote-main.sh). No loop script pushes to main.
# Offline: a bare local remote, a stub CI seam and a hand-written status line, no gh. usage: bash test-promote.sh
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so its wait is proved
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# ci <green|red|running>: the stub CI seam. It logs the sha it was read for, so a test proves which proof the script used.
ci() { printf '#!/bin/bash\necho "ci read %s" >> "%s/ci.log"\necho "%s"\n' "$1" "$T" "$1" > "$T/ci"; chmod +x "$T/ci"; export SG_CI_STATE_CMD=$T/ci; }
# line <kind> <result> <sha>: gate.sh keeps the full result apart from the hub's latest status.
line() { printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$1" "$2" "$3" > "$SG_STATE/gate-status"; [ "$1" != full ] || cp "$SG_STATE/gate-status" "$SG_STATE/gate-status-full"; }
# The stub GitHub (#743): the open dev -> stg pull request lives in $T/pr, and merging it moves stg through the same bare
# remote a real merge moves. It logs every call, so a test names the route the promotion read or wrote, and proves no push.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *graphql*) if [ -e "$T/pr" ]; then printf '{"data":{"repository":{"pullRequests":{"nodes":[{"number":%s,"headRefName":"dev","isDraft":false,"labels":{"nodes":[]}}],"pageInfo":{"hasNextPage":false}}}}}\n' "\$(cat "$T/pr")"; else echo '{"data":{"repository":{"pullRequests":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}'; fi;;
  *"-X POST repos/x/emu/pulls"*) echo 7 > "$T/pr"; echo '{"number":7}';;
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/stg; then rm -f "$T/pr"; echo '{"merged":true,"message":"Pull Request successfully merged"}'; else echo '{"merged":false,"message":"Pull Request is not mergeable"}'; fi;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The promotion proof is a profile setting (#439): the default lives in env.sh after the profile, so a profile that names it
# wins. The real env.sh is sourced under a throwaway profile directory, so the value is read from the code the loop runs.
env_proof() {  # $1: the profile's own lines. Prints the SG_PROMOTE_PROOF the real env.sh resolves.
  local pd=$T/prof
  rm -rf "$pd"; mkdir -p "$pd"
  { printf '%s\n' ': "${SG_REPO:=x/emu}"' ': "${SG_OWNER:=x}"' ': "${SG_PROJECT:=1}"' ': "${SG_CHECKOUT:=$HOME/co}"' ': "${SG_BASE:=dev}"'
    [ -n "${1:-}" ] && printf '%s\n' "$1"; } > "$pd/agent-squad.sh"
  env -i PATH="$PATH" HOME="$HOME" SG_PROFILES_DIR="$pd" SG_PROFILE=agent-squad SG_STATE_BASE="$T/pcache" SG_SQUAD="$T/squad" \
    bash -c ". '$d/env.sh' >/dev/null 2>&1 || exit 2; printf '%s\n' \"\${SG_PROMOTE_PROOF:-unset}\""
}
out=$(env_proof ': "${SG_PROMOTE_PROOF:=local}"')
[ "$out" = local ] && ok "a profile's SG_PROMOTE_PROOF wins over the env.sh default" || ko "the profile's proof did not win (got '$out')"
out=$(env_proof '')
[ "$out" = ci ] && ok "with no profile setting it, the promotion proof is ci" || ko "the default promotion proof is not ci (got '$out')"
base=$(tip main)

# The default proof is `ci`: with no SG_PROMOTE_PROOF set the CI seam decides, and a green CI promotes.
unset SG_PROMOTE_PROOF
adv dev one; ci green; rm -f "$T/ci.log" "$T/pr"; : > "$T/gh.log"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ -s "$T/ci.log" ] && ok "the default proof is ci: the CI seam is read with SG_PROMOTE_PROOF unset" || ko "the default proof did not read the CI seam"
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a dev tip whose CI is green is promoted to stg" || ko "green CI: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ok "the promotion opens the dev -> stg pull request (#743)" || ko "no pull request opened: $(cat "$T/gh.log")"
/usr/bin/grep -q -- "-X PUT repos/x/emu/pulls/7/merge" "$T/gh.log" && ok "the promotion merges the pull request once the proof is green" || ko "no merge: $(cat "$T/gh.log")"
/usr/bin/grep -q "refs/heads/stg" "$T/gh.log" && ko "the promotion still names the stg ref on GitHub" || ok "the promotion never writes refs/heads/stg"
# One pull request at a time: an open one is reused, never opened twice.
: > "$T/gh.log"; echo 4 > "$T/pr"; adv dev reuse; ci green; out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] || ko "reuse: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ko "the pass opened a second pull request" || ok "an open dev -> stg pull request is reused"
/usr/bin/grep -q -- "-X PUT repos/x/emu/pulls/4/merge" "$T/gh.log" && ok "the reused pull request is the one merged" || ko "the reused pull request was not merged: $(cat "$T/gh.log")"
[ "$(tip main)" = "$base" ] && ok "the loop left main alone" || ko "the loop moved main"

# A missing CI run (no check runs) reads as running: nothing is promoted.
adv dev one-absent; before=$(tip stg); ci running
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "a missing CI run promotes nothing" || ko "running CI: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *running*) ok "the refusal says the CI is running";; *) ko "the refusal does not say running: '$out'";; esac

# The local proof: the promotion gate's own status line, and nothing else.
export SG_PROMOTE_PROOF=local
adv dev two; before=$(tip stg); rm -f "$SG_STATE/gate-status"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: no status line promotes nothing" || ko "local no line: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"no local proof"*) ok "local: the refusal names the missing proof";; *) ko "local: the refusal does not name the missing proof: '$out'";; esac

other=$(tip stg)   # an older commit is not the dev tip
adv dev two-other; before=$(tip stg); line full pass "$other"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a proof for another sha promotes nothing" || ko "local other sha: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"$other"*) ok "local: the refusal names the sha the proof has";; *) ko "local: the refusal does not name the sha it has: '$out'";; esac

adv dev three; before=$(tip stg); line full fail "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a failed proof for the dev tip promotes nothing" || ko "local fail: rc $rc, stg $(tip stg), out '$out'"

adv dev four; before=$(tip stg); line full pass "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "local: a full pass for the dev tip promotes, and stg moves" || ko "local pass: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"

# A later fast gate updates the hub's latest status, but must not replace the full proof for this dev tip.
adv dev full-then-fast; before=$(tip stg)
(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" true)
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "local: a full pass still promotes after a later fast gate" || ko "local full then fast: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"

# stg holds a plain commit dev lacks (a promotion leaves merge commits only, #743): nothing moves, and the owner resolves it.
adv stg stg-only; adv dev dev-only; line full pass "$(tip dev)"; before=$(tip stg)
bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "a stg that holds a plain commit dev lacks is not promoted" || ko "stg moved over work dev lacks"
$W checkout -q -B stg origin/dev; $W push -q -f origin stg   # fixture: realign stg with dev

# The owner command.
unset SG_PROMOTE_PROOF
bash "$d/promote-main.sh" >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "the owner command promotes stg to main" || ko "owner: rc $rc, main $(tip main), stg $(tip stg)"
adv main main-only; before=$(tip main)
out=$(bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac && ok "the owner command refuses when main is not an ancestor of stg" || ko "owner divergence: rc $rc, out '$out'"

# No loop script pushes to main: only promote-main.sh names it, and a test fixture is no loop script.
bad=$(cd "$d" && grep -lE 'push[^#]*(refs/heads/main|origin main|HEAD:main)' *.sh *.py 2>/dev/null | grep -v -e '^test-' -e '^promote-main.sh$')
[ -z "$bad" ] && ok "no loop script pushes to main" || ko "pushes to main: $bad"
# The owner's consolidation rule (#743): stg moves by a pull request merge, never by a push from a loop script.
bad=$(cd "$d" && grep -lE '^[^#]*push[^#]*(refs/heads/stg|origin stg|HEAD:stg)' *.sh 2>/dev/null | grep -v -e '^test-')
[ -z "$bad" ] && ok "no loop script pushes stg: the consolidation is a pull request" || ko "pushes to stg: $bad"

# (#392) The promotion reads the machine-wide gate lock: with a holder it waits, says so on one line, then promotes.
export SG_PROMOTE_PROOF=local; adv dev lock; line full pass "$(tip dev)"; rm -f "$T/gl"
(bash "$d/serial.sh" gate sh -c "sleep 5; echo holder >> $T/gl") & for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
out=$(bash "$d/promote-stg.sh" 2>&1); wait
[ "$(printf '%s\n' "$out" | grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "the promotion waits for the holder, on one line" || ko "promotion wait lines: '$out'"
[ "$(tip stg)" = "$(tip dev)" ] && ok "the waiting promotion is not dropped: stg is promoted" || ko "the waiting promotion did not promote"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
