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

# sg_base_battery <python>: the command a gate hands to SG_FAST_CMD.
sg_base_battery() {
  # A gate run by hand, before env.sh is read, gets the same count env.sh holds by default.
  printf "%q -m ruff check squad tests && %q -m mypy squad && %q -m pytest -p no:deepeval -o heavy_cap_seconds=20 -n \${SQUAD_GATE_WORKERS:-%s} -m 'not heavy'" \
    "$1" "$1" "$1" "${SG_GATE_WORKERS:-6}"
}

# sg_base_gate <base> <python>: the fast gate over that battery, the changed scripts of the tip read against <base>.
sg_base_gate() {
  local dir; dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  printf "SG_FAST_BASE=%q SG_FAST_CMD=%q bash %q" "$1" "$(sg_base_battery "$2")" "$dir/fast-gate.sh"
}
