#!/bin/bash
# seconds: 0.66
# One case per rule of the event entry (hookfire.sh, #1837): the branch the CI run reports picks the pass, and only the
# passes this repository promotes; a pass runs under the lock the watch cycle uses; an event that finds the pass in flight
# adds nothing; a failed pass says so on stderr and in the cycle log; and the workflow that fires it names the runner and
# never checks a branch out.
# Offline: a throwaway state directory and a single.sh double that records its argv. usage: bash test-hookfire.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/scripts" "$T/state"
cp "$d/hookfire.sh" "$d/promotequiet.sh" "$T/scripts/"
cat > "$T/scripts/single.sh" <<'EOF'
#!/bin/bash
printf '%s\tproof=%s\n' "$*" "${SG_PROMOTE_PROOF:-none}" >> "$CALLS"
[ -n "${DUMMY_RC:-}" ] && exit "$DUMMY_RC"
exit 0
EOF
export SG_STATE=$T/state SG_REPO=x/emu SG_BASE=dev SG_STG_BRANCH=stg CALLS=$T/calls
run() {  # name, branch, expected rc
  : > "$CALLS"; out=$(bash "$T/scripts/hookfire.sh" "$2" 2>"$T/err"); rc=$?
  [ "$rc" = "$3" ] && ok "$1: rc $rc" || ko "$1: rc $rc, expected $3 (out '$out', err '$(cat "$T/err")')"
}
# 1. The base branch asks the dev-to-stg pass, under the cycle's own lock, with the CI proof.
run "a dev tip fires the dev-to-stg pass" dev 0
[ "$(cat "$CALLS")" = "promote bash $T/scripts/promote-pass.sh	proof=ci" ] \
  && ok "dev: single.sh promote bash promote-pass.sh with the CI proof" || ko "dev: single.sh recorded '$(cat "$CALLS")'"
# 2. The promotion branch asks the stg-to-main pass.
run "an stg tip fires the stg-to-main pass" stg 0
[ "$(cat "$CALLS")" = "promote-main bash $T/scripts/promote-main-pass.sh	proof=ci" ] \
  && ok "stg: single.sh promote-main bash promote-main-pass.sh with the CI proof" || ko "stg: single.sh recorded '$(cat "$CALLS")'"
# 3. Both, in one firing, in this order.
run "both fires the two passes in order" both 0
[ "$(cat "$CALLS")" = "promote bash $T/scripts/promote-pass.sh	proof=ci
promote-main bash $T/scripts/promote-main-pass.sh	proof=ci" ] \
  && ok "both: two passes, the dev one first" || ko "both: single.sh recorded '$(cat "$CALLS")'"
# 4. A branch this repository does not promote is refused by name, and no pass starts.
run "a work branch is refused" feature/thing 2
[ ! -s "$CALLS" ] && ok "a work branch starts no pass" || ko "a work branch started '$(cat "$CALLS")'"
case "$(cat "$T/err")" in *feature/thing*) ok "the refusal names the branch";; *) ko "the refusal does not name the branch: '$(cat "$T/err")'";; esac
# 5. The dev-to-stg debounce is the cycle's own rule: a landing stream earns one proof window and no pass starts inside it.
now=$(date +%s); printf '%s\tgui/x\tdeadbeef\t1\n' "$now" > "$T/state/landed.tsv"
run "a fresh landing closes the proof window" dev 0
[ ! -s "$CALLS" ] && ok "a fresh landing starts no dev-to-stg pass" || ko "a fresh landing started '$(cat "$CALLS")'"
case "$(cat "$T/state/cycle.log")" in *"HOOK promote deferred"*) ok "the deferred proof is recorded";; *) ko "the deferred proof is not recorded: '$(cat "$T/state/cycle.log")'";; esac
# The bound is its own release: a window that never quiets still promotes once SG_PROMOTE_MAX_DEFER has passed since the stamp.
: > "$T/state/landed.tsv"; touch -t 200101010000 "$T/state/promote-defer" 2>/dev/null || : > "$T/state/promote-defer"
SG_PROMOTE_MAX_DEFER=1 run "the deferral bound releases the pass" dev 0
[ -s "$CALLS" ] && ok "past the bound the dev-to-stg pass runs" || ko "past the bound no pass ran"
rm -f "$T/state/landed.tsv" "$T/state/promote-defer"
# 6. A pass already in flight adds nothing and is no failure (76 is the one held-lock code).
DUMMY_RC=76 run "a held lock is no failure" dev 0
case "$(cat "$T/state/cycle.log")" in *"HOOK promote rc=76"*) ok "the held lock is recorded";; *) ko "the held lock is not recorded: '$(cat "$T/state/cycle.log")'";; esac
# 6. A failed pass is named on stderr and in the cycle log, and the firing fails.
DUMMY_RC=1 run "a failed pass fails the firing" dev 1
case "$(cat "$T/err")" in *"promote failed"*) ok "the failure names the pass";; *) ko "the failure does not name the pass: '$(cat "$T/err")'";; esac
case "$(cat "$T/state/cycle.log")" in *"HOOK promote rc=1"*) ok "the failure is recorded";; *) ko "the failure is not recorded: '$(cat "$T/state/cycle.log")'";; esac
# 6b. A credential refusal (rc 3, #2213) is no red: the pass wrote its one alert, so the firing exits 0 and the run stays green.
DUMMY_RC=3 run "a credential refusal on the promotion branch is no failed firing" stg 0
case "$(cat "$T/state/cycle.log")" in *"HOOK promote-main rc=3"*) ok "the refusal is recorded in the cycle log";; *) ko "the refusal is not recorded: '$(cat "$T/state/cycle.log")'";; esac
# 7. The workflow that fires the hook: the event, the runner label, the one fixed command, and no checkout anywhere.
w=$(cd "$(dirname "$d")/../.." 2>/dev/null && pwd)/.github/workflows/squad-hook.yml
[ -f "$w" ] && ok "the hook workflow exists" || ko "no .github/workflows/squad-hook.yml"
if [ -f "$w" ]; then
  grep -q 'workflow_run:' "$w" && ok "the workflow fires on a workflow run" || ko "the workflow names no workflow_run trigger"
  grep -q 'runs-on: \[self-hosted, squad-hook\]' "$w" && ok "the job asks the squad-hook runner" || ko "the job names no self-hosted squad-hook runner"
  grep -q 'hookfire.sh' "$w" && ok "the job runs the hook" || ko "the job never runs hookfire.sh"
  grep -q 'actions/checkout' "$w" && ko "the job checks a branch out" || ok "the job checks nothing out"
fi
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
