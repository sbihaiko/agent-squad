#!/bin/bash
# usage: daysuites.sh   Every shell suite (`contrib/squad-goal/scripts/test-*.sh`) on the dev tip, once a day, outside the
# landing path (#2307).
#
# Why: a landing gate runs only the shell suites its delta selects (base-battery.sh, sg_shell_suites). A suite that no
# ticket touches then stays red on dev for hours, and each red surfaces only when an unrelated ticket's delta selects it.
# This pass runs every suite once a day, so a red is found by the pass and not by the next landing.
#
# Where: a plain clone at $SG_STATE/daysuites-tree, checked out at the dev tip, never the owner's checkout and never a
# child's worktree. $SG_DAYSUITES_TREE names another tree (a test), and it is read as it stands. The suites that run are the
# ones that tree carries, so the pass tests the dev tip's scripts, not the live copy's.
#
# Gates: one suite at a time, each under the machine-wide `gate` lock (serial.sh) and one caps.gates slot (caps.sh), the two
# resources every gate takes (gate.sh). The lock is what keeps the pass from ever running beside a landing gate, and a
# landing's gate that is queued for it is served first (serial.sh, ADR-0174). Both are taken for one suite and never for the
# whole pass, so a landing waits at most one suite. A slot that is not free within $SG_DAYSUITES_SLOT_WAIT seconds (120 by
# default) ends the pass held, with no day marker, and a later cycle tries again.
#
# Once a day: a completed pass writes $SG_STATE/auto/daysuites-<date>, and a later start that day exits at once. A pass the
# lock bound stopped ($SG_DAYSUITES_LOCK_WAIT, 900 s by default) writes no marker, so the day is not spent unmeasured.
#
# Window (#2417, ADR-0153): the pass never runs in the nightly's hour (SG_NIGHTLY_HOUR, 04 by default, America/Sao_Paulo),
# where the full suite owns the machine. It exits 0 with no day marker there, so the first cycle after the hour runs it.
#
# Load (#2417, ADR-0164): a suite that fails with the load at its start over the saturation line (the core count) is
# unproven, never red. It files no bug, spends no day marker, goes to $SG_STATE/auto/daysuites-unproven as
# `unproven: load`, and the pass exits 4. A measured red still wins over an unproven suite (ADR-0164).
#
# Bugs: before a red files, the pass re-reads dev. A moved tip runs that suite on the new tip, and the bug files only when the
# suite stays red there. A red suite files one bug through the loop's one filer (autohunt.sh --file), which opens it in Todo;
# the bug names the suite, its first FAIL line and the sha of the last red run. $SG_STATE/auto/daysuites-red holds the reds of the last completed pass: a
# suite red on two passes in a row files no second bug, and a suite green in between files again on a later red pass.
#
# Exit: 0 every suite green, 1 a suite red, 4 a suite unproven on load, 73 or 75 the pass was held (no day marker).
here="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
. "$here/caps.sh" 2>/dev/null || true
export SG_DAYSUITES_SLOTS=${SG_DAYSUITES_SLOTS:-1} SG_DAYSUITES_SLOT_WAIT=${SG_DAYSUITES_SLOT_WAIT:-120}
# ---- one suite under the gate lock and one slot ---------------------------------------------------------------
# The pass re-invokes itself inside serial.sh: the lock is taken first and the slot second, the one order every gate takes
# them (#1166), and both are released on every exit path.
if [ "${1:-}" = --suite ]; then
  suite=$2
  holder="daysuites-$$"; took=
  trap '[ -z "$took" ] || sg_caps release gates "$holder" >/dev/null 2>&1' EXIT
  sg_caps take gates "$holder" --count "$SG_DAYSUITES_SLOTS" --wait "$SG_DAYSUITES_SLOT_WAIT" --pid $$ --target "$PWD" >/dev/null; rc=$?
  [ "$rc" = 0 ] || { echo "daysuites: held: no caps.gates slot within ${SG_DAYSUITES_SLOT_WAIT}s; $suite did not run (exit $rc)" >&2; exit 73; }
  took=1
  # (#2306) The suite never writes a GIT_DIR its caller exported, as at every other suite runner. A missing scrub refuses the suite.
  sg_git_env_scrub || { echo "daysuites: $here/caps.sh gave no sg_git_env_scrub; $suite did not run" >&2; exit 2; }
  # (#2417) The load at the suite's start, and the core count the saturation line follows. The pass reads both lines from the
  # output and calls a red over the line unproven. SG_DAYSUITES_LOAD and SG_DAYSUITES_CORES name both values (a test).
  load1() { local l; l=$(LC_ALL=C uptime 2>/dev/null | sed -n 's/.*load average[s]*: *\([0-9.]*\).*/\1/p'); echo "${l:--}"; }
  cores() { sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1; }
  echo "daysuites: load-at-start ${SG_DAYSUITES_LOAD:-$(load1)} cores ${SG_DAYSUITES_CORES:-$(cores)}"
  hm=$(mktemp -d) 2>/dev/null || hm=
  # (#2484) The Python user site follows HOME, and the tree holds no .venv, so a suite's python3 lost the packages that live
  # only there (pytest's pygments). The caller's user base is read before HOME moves and kept: it is an interpreter fact,
  # never loop state.
  ub=${PYTHONUSERBASE:-$(python3 -m site --user-base 2>/dev/null)}
  # Hermetic like shell-suites.sh: every inherited SG_* and SERIAL_* is cleared and HOME sits in its own temp dir, so the
  # live loop's state and profile never reach an assertion. The pass's own state is read before this line, never after.
  env $(compgen -e | /usr/bin/grep -E "^(SG_|SERIAL_)" | sed "s/^/-u /") HOME="${hm:-/tmp}" ${ub:+PYTHONUSERBASE="$ub"} bash "$suite"; rc=$?
  [ -z "$hm" ] || rm -rf "$hm"
  exit "$rc"
fi
[ -n "${SG_DAYSUITES_OFF:-}" ] && exit 0
# (#2417, ADR-0153) The nightly's full suite owns the machine in its hour (SG_NIGHTLY_HOUR, 04 by default, America/Sao_Paulo).
# The pass starts no suite there and spends no day marker, so the first cycle after the hour runs the whole list. A profile
# that switches the nightly off has no such hour (SG_NIGHTLY_OFF, ADR-0153). SG_DAYSUITES_HOUR names the hour (a test).
nhour=$((10#${SG_NIGHTLY_HOUR:-04})); hnow=$((10#${SG_DAYSUITES_HOUR:-$(TZ=America/Sao_Paulo date +%H)}))
if [ -z "${SG_NIGHTLY_OFF:-}" ] && [ "$hnow" -eq "$nhour" ]; then
  echo "daysuites: held: the nightly hour ($(printf '%02d' "$nhour"):00, America/Sao_Paulo) owns the machine; no day marker is written, the next cycle after it runs the pass"
  exit 0
fi
mkdir -p "$st/auto" "$st/locks" 2>/dev/null
day=$st/auto/daysuites-$(date +%F)
[ -e "$day" ] && exit 0
# ---- the dev tip ----------------------------------------------------------------------------------------------
tree=${SG_DAYSUITES_TREE:-$st/daysuites-tree}
remote=${SG_DAYSUITES_REMOTE:-https://github.com/${SG_REPO:-}.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
# The tip as the tree reads it now: the default tree fetches origin first, a named tree is read as it stands.
read_tip() {
  if [ -n "${SG_DAYSUITES_TREE:-}" ]; then
    /usr/bin/git -C "$tree" rev-parse HEAD 2>/dev/null || { echo "daysuites: $tree is not a git tree" >&2; return 1; }
    return 0
  fi
  local o; o=$(/usr/bin/git -C "$tree" fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) \
    || { echo "daysuites: fetch of $base failed: $(printf '%s' "$o" | tail -1)" >&2; return 1; }
  /usr/bin/git -C "$tree" rev-parse "origin/$base" 2>/dev/null
}
# The default tree checks out the tip it reads; a named tree stays as it stands.
move_tree() {  # <sha>
  [ -n "${SG_DAYSUITES_TREE:-}" ] && return 0
  /usr/bin/git -C "$tree" checkout -q -f --detach "$1" && /usr/bin/git -C "$tree" clean -q -ffdx
}
if [ -z "${SG_DAYSUITES_TREE:-}" ]; then
  [ -d "$tree/.git" ] || { mkdir -p "$tree" && /usr/bin/git -C "$tree" init -q . \
    && /usr/bin/git -C "$tree" remote add origin "$remote"; } || { echo "daysuites: the tree $tree could not be created" >&2; exit 1; }
  /usr/bin/git -C "$tree" remote set-url origin "$remote" 2>/dev/null
fi
tip=$(read_tip) || exit 1
move_tree "$tip" || { echo "daysuites: the tree could not check out $tip" >&2; exit 1; }
sdir=$tree/contrib/squad-goal/scripts
shopt -s nullglob; suites=("$sdir"/test-*.sh); shopt -u nullglob
[ ${#suites[@]} -gt 0 ] || { echo "daysuites: the dev tip carries no suite under $sdir" >&2; exit 1; }
n_suites=${#suites[@]}
prev=$st/auto/daysuites-red
cur=$(mktemp) || exit 1; : > "$cur"
# one bug, through the loop's one filer: the [SQUAD] title prefix, the bug label, the Todo card and its Priority are that
# filer's contract (autohunt.sh --file), so the pass keeps no copy of them.
daysuites_file() {  # <suite path> <first FAIL line> <dev sha>
  local suite=$1 fail=$2 sha=$3 name body title
  name=$(basename "$suite")
  body=$(mktemp) || return 1
  {
    printf 'The daily shell-suite pass found this suite red on the dev tip (#2307).\n\n'
    printf '```\nsuite: contrib/squad-goal/scripts/%s\ndev sha: %s\nfirst FAIL line:\n%s\n```\n\n' "$name" "$sha" "$fail"
    printf 'A landing gate runs only the shell suites its delta selects, so a suite that no ticket touches can stay red on dev.\n'
    printf 'This pass runs every suite once a day. A suite red on two passes in a row files no second bug.\n'
  } > "$body"
  title="shell suite $name red on dev@${sha:0:7}"
  # One pass files at most one bug per suite: the ceiling is the suite count, so a broad breakage still names every suite
  # that broke, and this pass execution's own ledger holds each cause to one issue. The ledger names the execution (its
  # pid), so a pass stopped and run again the same day files for the suites it finds red, never behind the last one's count.
  SG_FILED_BY=daysuites SG_FILED_LABELS="${SG_DAYSUITES_LABELS:-ready-for-agent}" \
  SG_FILE_PASS="daysuites-$day-$$" SG_FILE_CEILING="$n_suites" SG_GH_PASS=daysuites \
    bash "$here/autohunt.sh" --file "$title" "$body"
  rm -f "$body"
}
hold() {  # <reason> <output file>: the pass stops with no day marker, so a later cycle runs the whole list again
  rm -f "$2" "$cur"
  echo "daysuites: held: $1; no day marker is written, the next cycle retries"
  exit 75
}
run_suite() {  # <suite path> <output file> <name>: one suite under the gate lock and one slot; sets rc
  SERIAL_WAIT_SECONDS=${SG_DAYSUITES_LOCK_WAIT:-900} \
  SERIAL_HOLD_SECONDS=${SG_DAYSUITES_SUITE_HOLD:-1800} \
  SERIAL_POLL_SECONDS=${SERIAL_POLL_SECONDS:-3} \
    bash "$here/serial.sh" gate bash "$0" --suite "$1" > "$2" 2>&1
  rc=$?
  cat "$2"
  # (#2417) The load and the core count the --suite run read at the suite's start; the last run's lines are the ones that count.
  read -r load0 cores0 <<< "$(awk '/^daysuites: load-at-start /{ l=$3; c=$5 } END { print (l==""?"-":l), (c==""?"0":c) }' "$2")"
  # Held, never a red: serial.sh's own bound line, or the slot line the --suite run wrote, names the resource that was not free.
  /usr/bin/grep -qE '^serial: waited [0-9]+s for lock|^daysuites: held: no caps\.gates slot' "$2" \
    && hold "the gate lock or its slot was not free for $3" "$2"
  return 0
}
# (#2417, ADR-0164) A red under a load at its start over the saturation line is the load's and not the code's, so the suite
# ends unproven: no bug, no red record, and the pass retries. The line is the core count; a load it cannot read is no line.
over_line() {  # <load> <cores>
  case "$1" in ''|-*|*[!0-9.]*) return 1;; esac
  awk -v l="$1" -v c="${2:-0}" 'BEGIN { n=(c+0)<1?1:(c+0); exit !((l+0)>n) }'
}
first_fail() {  # <output file>: the suite's first FAIL line, else its last line
  local f; f=$(/usr/bin/grep -m1 '^FAIL' "$1")
  [ -n "$f" ] || f=$(awk 'NF { l = $0 } END { print l }' "$1")
  [ -n "$f" ] || f="(the suite named no FAIL line; it exited $rc)"
  printf '%s' "$f"
}
# A red is filed against the tip it is red on. When dev moved during the run, the suite runs on the new tip, and a tip that
# moves again runs again, up to three moves; a tip that keeps moving holds the pass rather than file against a stale sha.
settle_red() {  # <suite path> <name> <output file>: sets rc, fail and tip
  local moves=0 now
  while :; do
    now=$(read_tip) || hold "dev could not be read for $2" "$3"
    [ "$now" = "$tip" ] && return 0
    moves=$((moves + 1)); [ "$moves" -le 3 ] || hold "dev kept moving during the re-runs of $2" "$3"
    tip=$now
    move_tree "$tip" || { echo "daysuites: the tree could not check out $tip" >&2; exit 1; }
    [ -f "$1" ] || { rc=0; return 0; }
    run_suite "$1" "$3" "$2"
    [ "$rc" = 0 ] && return 0
    fail=$(first_fail "$3")
  done
}
reds=0; filed=0; unproven=0
unp=$(mktemp) || exit 1; : > "$unp"
for s in "${suites[@]}"; do
  name=$(basename "$s"); out=$(mktemp) || continue
  run_suite "$s" "$out" "$name"
  if [ "$rc" != 0 ] && over_line "$load0" "$cores0"; then
    # (#2417, ADR-0164) The load at the suite's start was over the saturation line: the suite ends unproven, not red.
    printf '%s\tunproven: load\t%s\t%s\n' "$name" "$load0" "$cores0" >> "$unp"
    echo "daysuites: $name unproven: load (load $load0 at start over $cores0 cores); no bug filed, no day marker, the next cycle retries"
    unproven=$((unproven + 1)); rm -f "$out"; continue
  fi
  if [ "$rc" != 0 ]; then
    fail=$(first_fail "$out")
    settle_red "$s" "$name" "$out"
  fi
  if [ "$rc" != 0 ]; then
    reds=$((reds + 1)); printf '%s\n' "$name" >> "$cur"
    if /usr/bin/grep -qxF "$name" "$prev" 2>/dev/null; then
      echo "daysuites: $name red on two passes in a row; no second bug"
    else
      daysuites_file "$s" "$fail" "$tip"; filed=$((filed + 1))
    fi
  fi
  rm -f "$out"
done
# (#2417) This pass's unproven suites replace the last record in one rename (ADR-0030); a clean pass clears it. An unproven
# pass keeps the last red record as it stands, so a suite red before it still files no second bug on the retry.
if [ "$unproven" -gt 0 ]; then
  mv -f "$unp" "$st/auto/daysuites-unproven.$$" && mv -f "$st/auto/daysuites-unproven.$$" "$st/auto/daysuites-unproven" \
    || { rm -f "$unp" "$st/auto/daysuites-unproven.$$"; echo "daysuites: the unproven record could not be written" >&2; exit 1; }
else
  rm -f "$unp" "$st/auto/daysuites-unproven"
fi
# The record of this pass's reds replaces the last one in one rename (ADR-0030), so a stopped pass never leaves half of it.
if [ "$unproven" -eq 0 ]; then
  mv -f "$cur" "$prev.$$" && mv -f "$prev.$$" "$prev" || { rm -f "$cur" "$prev.$$"; echo "daysuites: the red record could not be written" >&2; exit 1; }
else
  rm -f "$cur"
fi
[ "$unproven" -gt 0 ] || : > "$day"
echo "daysuites: $n_suites suites on dev@${tip:0:7}: $reds red, $filed bug(s) filed, $unproven unproven on load"
[ "$reds" -eq 0 ] || exit 1   # a measured red wins over an unproven suite (ADR-0164)
[ "$unproven" -eq 0 ] || exit 4
exit 0
