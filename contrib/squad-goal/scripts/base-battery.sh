#!/bin/bash
# The base battery in one definition: the repository's own rule -- `ruff check squad tests`, `mypy squad` and the fast pytest
# -- that a local gate runs before the shell suites of the scripts a tip changes. fast-gate.sh runs it on the tip it is handed,
# wave.sh builds the wave tip check over it, read against origin/$SG_BASE, and branchland.sh builds the landing check over it,
# read against refs/sg-gate/<land>. One change here reaches all three, and no script keeps a copy.
# `ruff check .` is never the battery: it lints this loop's own scripts, which no other gate lints, and it is red at every tip
# (1378 findings, all under contrib/squad-goal/scripts), so a child could never enter a wave (#447).
# The battery's pytest runs with SG_GATE_WORKERS workers, never `auto`: `auto` takes every core, and a gate beside a second
# one saturates the machine the owner works on. env.sh holds the default; a profile may set it. A gate that took fewer slots than
# that (a project file lowered caps.gates, #567) exports the count it took as SQUAD_GATE_WORKERS, and the command reads it when it
# runs, not when it is built: the count never rises above the slots the gate holds. It is not an SG_ name, so a gate command that
# drops the SG_* variables still reads it.

# sg_battery_run <python> <base> <workers> <yes|no>: ruff and mypy on the whole tree, then pytest over the files that
# squad/testselect.py answers for <base>; `no` runs pytest over the whole tree. An empty selection runs no pytest. The line
# it logs, and writes to $GATE_SELECT_FILE when a gate set one, says `selected N files` or `all: <reason>`.
sg_battery_run() {
  local py=$1 base=$2 workers=$3 files line
  "$py" -m ruff check squad tests && "$py" -m mypy squad || return
  if [ "$4" = yes ]; then files=$("$py" -m squad.testselect --base "$base") || files="all: the selector failed"
  else files="all: SG_TEST_SELECT=no"; fi
  case $files in all:*) line=$files;; *) line="selected $(printf '%s\n' "$files" | /usr/bin/grep -c .) files";; esac
  echo "$line"
  [ -z "${GATE_SELECT_FILE:-}" ] || echo "$line" > "$GATE_SELECT_FILE"
  [ "$line" != "selected 0 files" ] || { echo "no pytest ran: the selection is empty"; return 0; }
  case $line in all:*) set -- ;; *) set -f; set -- $files; set +f ;; esac
  "$py" -m pytest -p no:deepeval -o heavy_cap_seconds=20 -n "${SQUAD_GATE_WORKERS:-$workers}" -m 'not heavy' "$@"
}

# sg_base_battery <python> <base>: the command a gate hands to SG_FAST_CMD, sg_battery_run with its definition inlined, so
# the command needs no function of this file in its shell. SG_TEST_SELECT=no, read when the command is built, runs the whole
# tree. A gate run by hand, before env.sh is read, gets the same worker count env.sh holds by default.
sg_base_battery() {
  local select=yes; [ "${SG_TEST_SELECT:-yes}" = no ] && select=no
  printf '%s\nsg_battery_run %q %q %q %s\n' "$(declare -f sg_battery_run)" "$1" "$2" "${SG_GATE_WORKERS:-6}" "$select"
}

# sg_base_gate <base> <python>: the fast gate over that battery, the changed scripts of the tip read against <base>.
sg_base_gate() {
  local dir; dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  printf "SG_FAST_BASE=%q SG_FAST_CMD=%q bash %q" "$1" "$(sg_base_battery "$2" "$1")" "$dir/fast-gate.sh"
}
