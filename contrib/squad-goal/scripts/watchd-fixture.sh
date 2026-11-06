#!/bin/bash
# Sourced by a suite that stands a fake watchd skill under a temp root (#1514): a stub env.sh, stub lanes and the two things
# watchd.sh needs from an install that a bare directory does not give it.
#   fake_squad <install>   the smallest squad package `watchd.sh start` accepts: the stale-cache probe imports
#                          squad.caps and reads free_runs (#1422), so an install without it is refused, never started.
#                          free_runs carries the real signature and returns 0: the probe reads the attribute, never calls
#                          it, and a signature that drifts from squad/caps.py would make the stub a stale guess.
#   fake_reader <repo>     the env.sh line that names the process reader every liveness read goes through. The skill's own
#                          caps.sh is absent, and the `ps` it falls back to is refused inside a child's sandbox (#1508), so
#                          the fake env.sh names the reader the real caps.sh runs: the repository's squad.procs.
fake_squad() {
  mkdir -p "$1/squad"; : > "$1/squad/__init__.py"
  printf 'def free_runs(c, active_runs, root="."): return 0\n' > "$1/squad/caps.py"
}

fake_reader() {
  printf 'SG_PS="env PYTHONPATH=%s python3 -m squad.procs ps"; export SG_PS\n' "$1"
}
