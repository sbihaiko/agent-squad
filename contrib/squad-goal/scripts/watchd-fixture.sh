#!/bin/bash
# Sourced by a suite that stands a fake watchd skill under a temp root (#1514): a stub env.sh, stub lanes and the two things
# watchd.sh needs from an install that a bare directory does not give it.
#   fake_squad <install> [package-root]
#                          the smallest squad package `watchd.sh start` accepts: the stale-cache probe imports
#                          squad.caps and reads free_runs (#1422), so an install without it is refused, never started.
#                          free_runs is derived from the package it mirrors, never a hand-kept string: the stub carries
#                          the signature inspect reads from `package-root`, which defaults to this repository. A signature
#                          change in squad/caps.py then reaches every suite that stands a fake install, with no edit here.
#                          `package-root` is a parameter so the derivation itself can be tested against a shim package.
#   fake_reader <repo>     the env.sh lines that name the process reader every liveness read goes through. The skill's own
#                          caps.sh is absent, and the `ps` it falls back to is refused inside a child's sandbox (#1508), so
#                          the fake env.sh names the reader the real caps.sh runs: the repository's squad.procs, run by
#                          ${PYTHON:-python3}. The module root rides PYTHONPATH as a quoted shell word, a line env.sh can
#                          source, so a checkout whose path holds a space still reads; SG_PS itself stays a word sequence
#                          its callers expand, never a path.
fake_squad() {
  local install=$1 root=${2:-} sig
  [ -n "$root" ] || root=$(cd "${BASH_SOURCE[0]%/*}/../../.." && pwd)
  sig=$(cd / && PYTHONPATH="$root" "${PYTHON:-python3}" -c 'import inspect,squad.caps as C;print(inspect.signature(C.free_runs))') || {
    printf 'watchd-fixture: cannot read squad.caps.free_runs from %s\n' "$root" >&2; return 1; }
  [ -n "$sig" ] || { printf 'watchd-fixture: squad.caps.free_runs in %s has no signature\n' "$root" >&2; return 1; }
  mkdir -p "$install/squad"; : > "$install/squad/__init__.py"
  printf 'def free_runs%s: return 0\n' "$sig" > "$install/squad/caps.py"
}

fake_reader() {
  printf 'PYTHONPATH=%q; export PYTHONPATH; SG_PS="%s -m squad.procs ps"; export SG_PS\n' "$1" "${PYTHON:-python3}"
}
