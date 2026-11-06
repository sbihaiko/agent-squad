#!/bin/bash
# Sourced by gate.sh and branchreviewpass.sh: the machine-wide budget's one command (squad/caps.py), never a second lock.
#   sg_caps take <gates|reviews> <holder> [--count N] [--try] [--wait SECONDS] [--pid PID] [--target DIR]
#   sg_caps release <kind> <holder>
# `take` prints the count of slots it took: the count it asked for, the lower ceiling, or the free slots when two or more are free
# (#734). Try mode exits 73, a wait past its bound exits 75, as serial.sh does. A holder that died loses its slots to the next
# count, so a release is a courtesy, never a need.
# The module is the one of the repository this script sits in, so the command and its caller are one version; the live copy is
# not in a repository, and it reads SG_SQUAD (env.sh sets it) instead.
# sg_battery_workers: the pytest worker count a proof may take, bounded by the machine (#738). `auto` takes every core, and
# the promotion proof runs on the box the loop and its children use: a battery of wall-time suites then measures its own load.
# Half the cores, half the memory in 2 GiB steps, never under 2 and never over 6. SG_BATTERY_WORKERS overrides it.
sg_battery_workers() {
  local c m w
  c=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 2)
  m=$(sysctl -n hw.memsize 2>/dev/null || echo 0); m=$(( m / 1073741824 ))
  w=$(( c / 2 )); [ "$m" -gt 0 ] && [ $(( m / 2 )) -lt "$w" ] && w=$(( m / 2 ))
  [ "$w" -lt 2 ] && w=2; [ "$w" -gt 6 ] && w=6
  echo "${SG_BATTERY_WORKERS:-$w}"
}

sg_caps() {
  local root; root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); [ -f "$root/squad/caps.py" ] || root=${SG_SQUAD:-}
  PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.caps "$@"
}

# sg_gate_python: the interpreter that holds the suite's tools (#1086): SG_GATE_PY, else the checkout's venv, else the venv beside
# the worktree's common git directory, else python3. A checkout with no venv must not fall back to a python3 that lacks mypy.
# A caller with no SG_CHECKOUT reads its own tree through git, so a hand gate run from a linked worktree still finds the venv.
sg_gate_python() {
  local py common root
  if [ -n "${SG_GATE_PY:-}" ]; then echo "$SG_GATE_PY"; return; fi
  root=${SG_CHECKOUT:-.}
  py=$root/.venv/bin/python; [ -x "$py" ] && { echo "$py"; return; }
  common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -n "$common" ]; then py=$(dirname "$common")/.venv/bin/python; [ -x "$py" ] && { echo "$py"; return; }; fi
  echo python3
}

# sg_missing_tool <python>: the first of ruff, mypy and pytest the interpreter lacks; prints nothing when it holds all three.
sg_missing_tool() {
  local t; for t in ruff mypy pytest; do "$1" -m "$t" --version >/dev/null 2>&1 || { echo "$t"; return; }; done
}

# sg_ps_default: the process read every shell caller uses (#1508). macOS refuses to exec the setuid process table binary inside the child's
# sandbox-exec, so the read goes through squad.procs (libproc, KERN_PROCARGS2) and prints the ps text. The repository the script
# lives in supplies the module; the live copy reads SG_SQUAD. The interpreter is the battery's (sg_gate_python).
sg_ps_default() {
  local root; root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); [ -f "$root/squad/procs.py" ] || root=${SG_SQUAD:-.}
  echo "env PYTHONPATH=$root $(sg_gate_python) -m squad.procs ps"
}
# SG_PS: one command word for callers to word-split (`$SG_PS -o pid= -p 1`). A caller that sets it keeps its own reader.
: "${SG_PS:=$(sg_ps_default)}"; export SG_PS
