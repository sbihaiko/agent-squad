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

# sg_ps: the process read every shell caller uses (#1508). macOS refuses to exec the setuid process table binary inside the child's
# sandbox-exec, so the read goes through squad.procs (libproc, KERN_PROCARGS2) and prints the ps text. The repository the script
# lives in supplies the module; the live copy reads SG_SQUAD. The interpreter is the battery's (sg_gate_python). Both paths are
# kept as quoted words, so a checkout or an interpreter whose path holds a space still reads the table.
#
# The poll shapes go to a small compiled reader first (#1511): an interpreter per read cost about 0.7 s on a loaded loop box, and
# the reader this replaced took 10 ms. psfast.c serves one `-p` pid over pid, ppid, pgid and stat -- the one question the pollers
# ask -- and exits 3 on anything else, and then the module answers, still the one reader of the command line and the whole table.
# The reader is built on the first read of a shell, from the source beside this file (or the repository this script lives in), into
# a per-user build folder named for the source's checksum; a cached binary is run only when its recorded checksum still matches
# it, and a build that crashes stops this shell trying it. No compiler, an unreadable build folder or a non-macOS host keeps the
# module alone.
# SG_PS_FAST=0 turns the compiled reader off, so an operator can read the table through the module alone.
_sg_ps_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); [ -f "$_sg_ps_root/squad/procs.py" ] || _sg_ps_root=${SG_SQUAD:-.}
# The interpreter is found on the first read, never at source time: a script that never reads the table pays no git call.
_sg_ps_py=
# The compiled reader, or empty when this shell has none; tried once, on the first read. `3` is psfast's "not my shape".
_sg_ps_fast=; _sg_ps_fast_tried=
_sg_ps_build() {  # prints the compiled reader's path, or nothing
  local dir src sum bin ok tmp bsum
  if [ "${SG_PS_FAST:-1}" = 0 ]; then return 0; fi
  [ "$(uname -s 2>/dev/null)" = Darwin ] || return 0
  src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/psfast.c
  [ -f "$src" ] || src=$_sg_ps_root/contrib/squad-goal/scripts/psfast.c
  [ -f "$src" ] || return 0
  # A per-user folder, never a world-shared /tmp name: another account cannot plant a binary this shell would run.
  dir=${TMPDIR:-/tmp}; dir=${dir%/}/agent-squad-psfast-$(id -u 2>/dev/null)
  sum=$(cksum < "$src" 2>/dev/null); sum=${sum%% *}
  [ -n "$sum" ] || return 0
  bin=$dir/psfast-$sum; ok=$bin.cksum
  # A cached reader is trusted only when its recorded checksum still matches it: a partial or corrupt file from an
  # interrupted build is never run, and the build below replaces it.
  if [ -x "$bin" ] && [ -f "$ok" ] && [ "$(cksum < "$bin" 2>/dev/null)" = "$(cat "$ok" 2>/dev/null)" ]; then
    echo "$bin"; return 0
  fi
  mkdir -p -m 700 "$dir" 2>/dev/null || return 0
  command -v cc >/dev/null 2>&1 || return 0
  tmp=$bin.$$
  if cc -O2 -o "$tmp" "$src" >/dev/null 2>&1 && [ -x "$tmp" ]; then
    bsum=$(cksum < "$tmp" 2>/dev/null)
    if [ -n "$bsum" ] && mv -f "$tmp" "$bin" 2>/dev/null \
       && printf '%s\n' "$bsum" > "$ok.$$" 2>/dev/null && mv -f "$ok.$$" "$ok" 2>/dev/null; then
      echo "$bin"; return 0
    fi
  fi
  rm -f "$tmp" "$ok.$$" 2>/dev/null
  return 0
}
sg_ps() {
  [ -n "$_sg_ps_fast_tried" ] || { _sg_ps_fast_tried=1; _sg_ps_fast=$(_sg_ps_build); }
  if [ -n "$_sg_ps_fast" ]; then
    "$_sg_ps_fast" "$@"; _sg_ps_rc=$?
    # ps's own answers: 0 read, 1 no live pid, 2 refused. 3 is "not my shape": the module answers and the reader stays.
    # Anything else is a crash or a vanished binary: the module answers and this shell stops trying the reader, so a
    # broken build costs one read, never every poll.
    if [ "$_sg_ps_rc" -le 2 ]; then return "$_sg_ps_rc"; fi
    [ "$_sg_ps_rc" -eq 3 ] || _sg_ps_fast=
  fi
  [ -n "$_sg_ps_py" ] || { _sg_ps_py=$(sg_gate_python); case $_sg_ps_py in /*) ;; */*) _sg_ps_py=$PWD/$_sg_ps_py;; esac; }
  # A module that answers with no row is no reader: a live pid is a non-empty answer, and a table is never empty, so
  # rc 0 with nothing is a stale or absent squad.procs (a plugin cache older than the live copy). ps answers then,
  # and the loop never reads an empty table as "no process runs" (#1647).
  local out rc
  out=$(PYTHONPATH="$_sg_ps_root${PYTHONPATH:+:$PYTHONPATH}" "$_sg_ps_py" -m squad.procs ps "$@"); rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
  command ps "$@"
}
# SG_PS: the one word callers expand (`$SG_PS -o pid= -p 1`): the sg_ps function, unless the caller names its own reader.
# It is not exported: a shell function does not reach a child, and each script sources this file for its own.
: "${SG_PS:=sg_ps}"
