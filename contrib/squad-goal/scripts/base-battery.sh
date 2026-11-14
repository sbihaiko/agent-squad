#!/bin/bash
# The base battery in one definition: the repository's own rule -- `ruff check squad tests`, `mypy squad` and the fast pytest
# (whose parallel pass records every unmarked call at or over the project's 2 s line, and runs only those calls once more,
# serially, where the line is enforced) -- that a local gate runs before the shell
# suites of the scripts a tip changes. fast-gate.sh runs it on the tip it is handed,
# wave.sh builds the wave tip check over it, read against origin/$SG_BASE, and branchland.sh builds the landing check over it,
# read against refs/sg-gate/<land>. One change here reaches all three, and no script keeps a copy.
# The stage names the scope: an intermediate stage (fast, wave-entry, wave-tip, prepush) runs the delta the change reaches,
# while the landing, the promotion proof and the nightly run the full battery. A delta the selector cannot cover falls through
# to the full battery with its reason, so no stage ever passes by a narrower check (ADR-0018).
# `ruff check .` is never the battery: it lints this loop's own scripts, which no other gate lints, and it is red at every tip
# (1378 findings, all under contrib/squad-goal/scripts), so a child could never enter a wave (#447).
# The battery's pytest runs with SG_GATE_WORKERS workers, never `auto`: `auto` takes every core, and a gate beside a second
# one saturates the machine the owner works on. env.sh holds the default; a profile may set it. A gate that took fewer slots than
# that (a project file lowered caps.gates, #567) exports the count it took as SQUAD_GATE_WORKERS, and the command reads it when it
# runs, not when it is built: the count never rises above the slots the gate holds. It is not an SG_ name, so a gate command that
# drops the SG_* variables still reads it.

# sg_battery_run <python> <base> <workers> <yes|no> <stage>: ruff and mypy on the whole tree, then pytest over the files that
# squad/testselect.py answers for <base> at <stage>; `no` runs pytest over the whole tree. A stage the selector does not know
# as a delta stage answers `all`, so the whole tree runs too. An empty selection (`none: <reason>`, or no line) runs no pytest
# and logs `no pytest ran: the selection is empty: <reason>`, which the fast gate records as unproven (#1249). The line it logs, and writes
# to $GATE_SELECT_FILE when a gate set one, says `selected N files` or `all: <reason>`; $GATE_SCOPE_FILE says `delta` or
# `full`, the scope that line names. A delta the selector cannot cover answers `all` with its reason, so the full battery
# runs and the record says why the delta refused: a cheaper scope never narrows a gate (ADR-0018).
sg_battery_run() {
  local py=$1 base=$2 workers=$3 files line rc= stage=${5:-fast} scope why="the selector named no test file" out= defs=
  "$py" -m ruff check squad tests && "$py" -m mypy squad || return
  if [ "$4" = yes ]; then files=$("$py" -m squad.testselect --stage "$stage" --base "$base") || files="all: the selector failed"
  else files="all: SG_TEST_SELECT=no"; fi
  case $files in none:*) why=${files#none: }; files=;; esac
  case $files in all:*) line=$files; scope=full;; *) line="selected $(printf '%s\n' "$files" | /usr/bin/grep -c .) files"; scope=delta;; esac
  echo "$line"
  [ -z "${GATE_SELECT_FILE:-}" ] || echo "$line" > "$GATE_SELECT_FILE"
  [ -z "${GATE_SCOPE_FILE:-}" ] || echo "$scope" > "$GATE_SCOPE_FILE"
  # (#1249) No pytest ran, so nothing is proven: the line names why, and fast-gate.sh reads it as an unproven verdict.
  [ "$line" != "selected 0 files" ] || { echo "no pytest ran: the selection is empty: $why"; return 0; }
  # (#1726) One pass, at the project's own line: the parallel run lifts no cap, so the slow-cap guard records every unmarked
  # call at or over that line instead of hiding it under a 20 s threshold. The output is kept in a file the record is read
  # from and tee'd on, so a run the deadline stops still logs what it printed before it.
  set -f   # one word per file or node id: a parametrized id such as t[a.sh] never globs
  case $line in all:*) set -- ;; *) set -- $files ;; esac
  out=$(mktemp "${TMPDIR:-/tmp}/sg-battery.XXXXXX" 2>/dev/null)
  if [ -n "$out" ]; then
    "$py" -m pytest -p no:deepeval -n "${SQUAD_GATE_WORKERS:-$workers}" -m 'not heavy' "$@" 2>&1 | tee "$out"; rc=${PIPESTATUS[0]}
  else
    "$py" -m pytest -p no:deepeval -n "${SQUAD_GATE_WORKERS:-$workers}" -m 'not heavy' "$@" || rc=$?
  fi
  # (#1557, #1726) The serial 2 s line the CI complete tier enforces is proved on the calls that need it and on no others.
  # Under xdist the guard records an unmarked call at or over the project line and does not fail it; those calls and only
  # those run once more, serially, where the line is enforced. An empty record ends the battery there, with no second pytest
  # run, so a green parallel pass costs one pass. A red parallel pass is never followed: the caller's retry names its failed
  # tests (fast-gate.sh), and a full selection is no exception: only a recorded call is repeated, never the whole battery.
  if [ "${rc:-0}" = 0 ] && [ -n "$out" ]; then
    defs=$(sed -n 's/^SLOW-UNMARKED (deferred to the serial run): \(.*\) took [0-9.]* s,.*$/\1/p' "$out" | sort -u)
    if [ -n "$defs" ]; then
      set -- $defs
      echo "serial line: $(printf '%s ' "$@")run once more, serially, at the project line"
      "$py" -m pytest -p no:deepeval -p no:xdist -m 'not heavy' "$@" || rc=$?
    fi
  fi
  set +f
  [ -z "$out" ] || rm -f "$out"
  return "${rc:-0}"
}

# sg_base_battery <python> <base> [<stage>]: the command a gate hands to SG_FAST_CMD, sg_battery_run with its definition
# inlined, so the command needs no function of this file in its shell. The stage is read here and written into the command,
# because the gate runs it with no SG_* variable: SG_GATE_STAGE names it when the caller omits it, and `fast` is the default.
# SG_TEST_SELECT=no, read when the command is built, runs the whole tree. A gate run by hand, before env.sh is read, gets the
# same worker count env.sh holds by default.
sg_base_battery() {
  local select=yes stage=${3:-${SG_GATE_STAGE:-fast}}; [ "${SG_TEST_SELECT:-yes}" = no ] && select=no
  printf '%s\nsg_battery_run %q %q %q %s %q\n' "$(declare -f sg_battery_run)" "$1" "$2" "${SG_GATE_WORKERS:-6}" "$select" "$stage"
}

# sg_base_gate <base> <python> [<stage>]: the fast gate over that battery, the changed scripts of the tip read against <base>.
sg_base_gate() {
  local dir; dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  printf "SG_FAST_BASE=%q SG_FAST_CMD=%q bash %q" "$1" "$(sg_base_battery "$2" "$1" "${3:-${SG_GATE_STAGE:-fast}}")" "$dir/fast-gate.sh"
}
