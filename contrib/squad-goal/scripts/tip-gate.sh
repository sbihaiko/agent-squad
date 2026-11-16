#!/bin/bash
# usage: tip-gate.sh --choose <sha>            which half the promotion's tip gate runs for <sha>: `ci <run id>` or `local`
#        tip-gate.sh --local-half <tip> <id>  run the local half of that gate, and record its verdict
#
# The proof of the dev tip is a hybrid (spec 1782, #1927). Its CI half is the fast tier of the dev-to-stg pull request:
# `ruff`, `mypy` and the fast battery on the GitHub runners, whose verdict per sha is ghread.py's (#1926). Its local half
# is what a Linux runner cannot see: the shell suites of the scripts the tip changes against stg, and each pytest test that
# carries a marker the runner deselects (base-battery.sh names that selection, ADR-0139).
#
# `--choose` answers the half. A profile that names SG_PROOF_RUNNER=local, or any verdict but `pass` from the reader,
# answers `local`: the promotion then runs today's gate, which this script never touches. A refused or unanswered read is
# no pass, so a GitHub outage never takes the local half away.
#
# `--local-half` runs only that half, in the tree at <tip>: each changed script's suite as fast-gate.sh runs one (hermetic:
# no SG_* variable, its own HOME), then one pytest call over the deselected tests. Its verdict goes to the gate history
# line through gate.sh --history, which names runner=ci and the CI run id; a local gate keeps runner=local (gate.sh).
# It never takes the gate lock: the promotion proof holds its own lock and must never hold a landing up (ADR-0145 rule 3).
# Exit 0 is a pass, 1 a measured red, 4 unproven (the base does not resolve, or the bound ended the half). The bound is
# GATE_DEADLINE_EPOCH when a caller hands one down, else SG_TIP_GATE_BOUND_SECONDS (900): the promotion's own clock.
# $SG_CI_FAST_TIER_CMD <sha> replaces the reader in a fixture, as promote-stg.sh's SG_CI_STATE_CMD does; with none, the read
# goes through SG_GHREAD, the one interface module every read of this repository goes through (ghcall.sh), so a profile or a
# fixture that names its own build reads CI through that build and no other.
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/base-battery.sh"
. "$here/caps.sh"
st=${SG_STATE:-/tmp/sg-state}
tip_ci() {  # <sha>: the fast-tier verdict of <sha> and its run id, `<verdict> TAB <run id>`
  if [ -n "${SG_CI_FAST_TIER_CMD:-}" ]; then "$SG_CI_FAST_TIER_CMD" "$1"; return; fi
  ${SG_GHREAD:-python3 "$here/ghread.py"} --fast-tier "$1" 2>/dev/null
}
if [ "${1:-}" = --choose ]; then
  sha=${2:-}; [ -n "$sha" ] || { echo "tip-gate: --choose needs a sha" >&2; exit 2; }
  [ "${SG_PROOF_RUNNER:-ci}" = local ] && { echo local; exit 0; }
  v=; rid=
  IFS=$'\t' read -r v rid < <(tip_ci "$sha") 2>/dev/null || v=
  if [ "$v" = pass ]; then echo "ci ${rid:--}"; else echo local; fi
  exit 0
fi
if [ "${1:-}" != --local-half ] || [ -z "${2:-}" ]; then
  echo "usage: tip-gate.sh --choose <sha> | --local-half <tip> <run id>" >&2; exit 2
fi
tip=$2; runid=${3:--}
now=$(date +%s)
deadline=${GATE_DEADLINE_EPOCH:-$(( now + ${SG_TIP_GATE_BOUND_SECONDS:-900} ))}
left() { local l=$(( deadline - $(date +%s) )); [ "$l" -lt 1 ] && l=1; echo "$l"; }
mkdir -p "$st" 2>/dev/null
log=$st/tip-gate-${tip:0:12}.log
record() {  # <result> <outcome> <selection> <reason>: the one history line this gate writes, then its exit code
  SG_GATE_CALLER=tip bash "$here/gate.sh" --history full "$1" "$tip" "$now" "$3" full "$2" ci "$runid" 2>/dev/null
  [ "$1" = pass ] && return 0
  [ "$2" = unproven ] && return 4
  return 1
}
base=${SG_TIP_GATE_BASE:-origin/${SG_STG_BRANCH:-stg}}
mb=$(/usr/bin/git merge-base HEAD "$base" 2>/dev/null) || {
  echo "tip-gate: the base $base does not resolve, so the changed scripts are unknown; unproven" | tee -a "$log"
  record fail unproven - "the base $base does not resolve"; exit 4; }
top=$(/usr/bin/git rev-parse --show-toplevel 2>/dev/null) || { record fail unproven - "the tree has no top"; exit 4; }
suites=$(sg_shell_suites "$top" "$mb")
py=$(sg_gate_python)
markers=$(sg_linux_deselected_markers)
run_suite() {  # <script> <home>: the suite in its own home, from the repository top, with no SG_* variable
  ( cd "$top" && env $(compgen -e | /usr/bin/grep '^SG_' | sed 's/^/-u /') HOME="$2" bash "$1" )
}
bounded() {  # <seconds> <command...>: 124 says the bound stopped it; never a pass
  local b=$1 p s; shift; s=$(date +%s)
  "$@" & p=$!
  while kill -0 "$p" 2>/dev/null; do
    if [ $(( $(date +%s) - s )) -ge "$b" ]; then
      kill -TERM "$p" 2>/dev/null; sleep 2; kill -KILL "$p" 2>/dev/null; wait "$p" 2>/dev/null; return 124
    fi
    sleep 0.1
  done
  wait "$p"
}
{
  echo "tip-gate: $tip local half against $base ($mb): ${suites:+$suites} and the deselected tests ($markers)"
  : > "$log"
  n=0; red=""
  for name in $suites; do
    s=$top/contrib/squad-goal/scripts/test-$name.sh; [ -f "$s" ] || continue
    n=$(( n + 1 )); hm=$(mktemp -d)
    bounded "$(left)" run_suite "$s" "$hm"; rc=$?
    rm -rf "$hm"
    if [ "$rc" = 0 ]; then echo "tip-gate: ok: test-$name.sh"
    else
      [ "$rc" = 124 ] && { echo "tip-gate: bounded: the ${SG_TIP_GATE_BOUND_SECONDS:-900}s bound ended test-$name.sh; unproven"
        record fail unproven - "local half: $n suite(s), the bound ended test-$name.sh"; exit 4; }
      red="$red test-$name.sh"; echo "tip-gate: FAILED: test-$name.sh"
    fi
  done
  bounded "$(left)" bash -c "cd \"\$1\" && \"\$2\" -m pytest -p no:deepeval -p no:xdist -m \"\$3\" -q" _ "$top" "$py" "$markers"
  rc=$?
  if [ "$rc" = 124 ]; then
    echo "tip-gate: bounded: the ${SG_TIP_GATE_BOUND_SECONDS:-900}s bound ended the deselected tests; unproven"
    record fail unproven - "local half: $n suite(s) and the deselected tests, the bound ended the run"; exit 4
  fi
  [ "$rc" = 0 ] || red="$red deselected-tests"
  if [ -n "$red" ]; then echo "tip-gate: FAILED:$red"; record fail fail "failed:$red" "local half: $n suite(s) and the deselected tests"; exit 1; fi
  record pass pass "local half: $n suite(s) and the deselected tests" -
  echo "tip-gate: pass: the local half is green ($n changed suite(s), the deselected tests)"
} 2>&1 | tee -a "$log"
exit "${PIPESTATUS[0]}"
