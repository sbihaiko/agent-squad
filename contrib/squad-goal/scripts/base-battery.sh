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
# A child's own gate narrows instead of widening (#2085, ADR-0169 rule 4): it is bounded (ADR-0163) and waits behind its
# siblings' gates, so a change the delta cannot place runs no pytest there, and the gate records the reason and ends unproven.
# The landing gate runs the full battery over the branch, so the narrowed scope defers the proof and never weakens it.
# `ruff check .` is never the battery: it lints this loop's own scripts, which no other gate lints, and it is red at every tip
# (1378 findings, all under contrib/squad-goal/scripts), so a child could never enter a wave (#447).
# The battery's pytest runs with SG_GATE_WORKERS workers, never `auto`: `auto` takes every core, and a gate beside a second
# one saturates the machine the owner works on. env.sh holds the default; a profile may set it. A gate that took fewer slots than
# that (a project file lowered caps.gates, #567) exports the count it took as SQUAD_GATE_WORKERS, and the command reads it when it
# runs, not when it is built: the count never rises above the slots the gate holds. It is not an SG_ name, so a gate command that
# drops the SG_* variables still reads it.

# sg_battery_run <python> <base> <workers> <yes|no> <stage> [<narrow>]: ruff and mypy on the whole tree, then pytest over the
# files that squad/testselect.py answers for <base> at <stage>; `no` runs pytest over the whole tree. A stage the selector
# does not know as a delta stage answers `all`, so the whole tree runs too. An empty selection (`none: <reason>`, or no line)
# runs no pytest and logs `no pytest ran: the selection is empty: <reason>`, which the fast gate records as unproven (#1249).
# The line it logs, and writes to $GATE_SELECT_FILE when a gate set one, says `selected N files` or `all: <reason>`;
# $GATE_SCOPE_FILE says `delta` or `full`, the scope that line names. A delta the selector cannot cover answers `all` with
# its reason, so the full battery runs and the record says why the delta refused: a cheaper scope never narrows a gate
# (ADR-0018). <narrow> `yes` is the child's own gate (#2085): an `all` answer there runs no pytest and keeps the `delta`
# scope, and the line names the change the delta cannot cover, so the gate ends unproven with that reason (ADR-0169 rule 4).
sg_battery_run() {
  local py=$1 base=$2 workers=$3 files line rc= stage=${5:-fast} narrow=${6:-no} scope why="the selector named no test file" \
        out= defs= prefix="the selection is empty: "
  # (#2306) The battery runs the repository's own tests, and a gate started from a git hook inherits GIT_DIR, GIT_WORK_TREE
  # and GIT_INDEX_FILE: `git init --bare` with no path under GIT_DIR then re-initializes the checkout and sets core.bare.
  # The battery drops the three, whatever built its command.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
  "$py" -m ruff check squad tests && "$py" -m mypy squad || return
  if [ "$4" = yes ]; then files=$("$py" -m squad.testselect --stage "$stage" --base "$base") || files="all: the selector failed"
  else files="all: SG_TEST_SELECT=no"; fi
  case $files in none:*) why=${files#none: }; files=;; esac
  case $files in
    all:*) if [ "$narrow" = yes ] && [ "$4" = yes ]; then
             line="selected 0 files"; scope=delta; why=${files#all: }; prefix="the delta cannot cover the change: "
           else line=$files; scope=full; fi;;
    *) line="selected $(printf '%s\n' "$files" | /usr/bin/grep -c .) files"; scope=delta;;
  esac
  echo "$line"
  [ -z "${GATE_SELECT_FILE:-}" ] || echo "$line" > "$GATE_SELECT_FILE"
  [ -z "${GATE_SCOPE_FILE:-}" ] || echo "$scope" > "$GATE_SCOPE_FILE"
  # (#1249) No pytest ran, so nothing is proven: the line names why, and fast-gate.sh reads it as an unproven verdict.
  [ "$line" != "selected 0 files" ] || { echo "no pytest ran: $prefix$why"; return 0; }
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

# sg_base_battery <python> <base> [<stage>] [<narrow>]: the command a gate hands to SG_FAST_CMD, sg_battery_run with its
# definition inlined, so the command needs no function of this file in its shell. The stage is read here and written into the
# command, because the gate runs it with no SG_* variable: SG_GATE_STAGE names it when the caller omits it, and `fast` is the
# default. <narrow> is the child's own gate (#2085), written into the command for the same reason: it is `yes` there and `no`
# everywhere else. SG_TEST_SELECT=no, read when the command is built, runs the whole tree. A gate run by hand, before env.sh is
# read, gets the same worker count env.sh holds by default.
sg_base_battery() {
  local select=yes stage=${3:-${SG_GATE_STAGE:-fast}} narrow=${4:-no}; [ "${SG_TEST_SELECT:-yes}" = no ] && select=no
  printf '%s\nsg_battery_run %q %q %q %s %q %s\n' "$(declare -f sg_battery_run)" "$1" "$2" "${SG_GATE_WORKERS:-6}" "$select" "$stage" "$narrow"
}

# sg_shell_suites <top> <merge-base>: the shell suite names (`test-<n>.sh`, without prefix or suffix) a tree changes against
# <merge-base>: every .sh or .py script under contrib/squad-goal/scripts the tree changes, and every fixture suite whose
# `# mirrors:` line names a changed file. One definition, so the fast gate and the promotion's tip gate read one selection.
sg_shell_suites() {
  local top=$1 mb=$2 changed suites t f
  changed=$(/usr/bin/git -C "$top" diff --name-only --diff-filter=d "$mb" HEAD 2>/dev/null) || { printf '\n'; return 0; }
  suites=$(printf '%s\n' "$changed" | sed -n -e 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' -e 's|^contrib/squad-goal/scripts/\(.*\)\.py$|\1|p' \
    | /usr/bin/grep -v / | sed 's|^test-||')
  for t in "$top"/contrib/squad-goal/scripts/test-*.sh; do [ -f "$t" ] || continue
    for f in $(sed -n 's/^# mirrors: //p' "$t" | head -n 1); do
      printf '%s\n' "$changed" | /usr/bin/grep -qxF "contrib/squad-goal/scripts/$f" \
        && { suites="$suites"$'\n'"$(basename "$t" .sh | sed 's/^test-//')"; break; }
    done
  done
  printf '%s\n' $suites | sort -u
}

# sg_linux_deselected_markers: the pytest markers a Linux runner deselects (tests/conftest.py, ADR-0139), as one `-m`
# expression. Every test that carries one runs on the machine, never on the runner: it is the local half of a hybrid proof
# (spec 1782, #1927). A marker added to the runner's deselection belongs here, and tests/test_tooling.py guards the set.
sg_linux_deselected_markers() {
  printf '%s\n' "macos_bound or sandbox_bound or claude_bound or cc_bound or process_bound or sdk_bound or host_bound or node_bound"
}

# sg_base_gate <base> <python> [<stage>]: the fast gate over that battery, the changed scripts of the tip read against <base>.
sg_base_gate() {
  local dir; dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  printf "SG_FAST_BASE=%q SG_FAST_CMD=%q bash %q" "$1" "$(sg_base_battery "$2" "$1" "${3:-${SG_GATE_STAGE:-fast}}")" "$dir/fast-gate.sh"
}
