#!/bin/bash
# usage: fast-gate.sh      the fast gate of a work-branch tip or of a child that enters a wave (ADR-0145), run in that tip's worktree.
#
# It is the base battery (ruff, mypy and the fast pytest, SG_FAST_CMD) at the `fast` stage, so the pytest runs the delta the
# tip reaches, then the shell suite test-<script>.sh of every .sh or .py
# script under contrib/squad-goal/scripts that the tip changes, and every fixture suite whose `# mirrors:` line names a changed
# file; a changed test-<script>.sh runs itself. The changed files are the diff of HEAD
# against its merge-base with SG_FAST_BASE (default origin/$SG_BASE, else origin/dev), so work that landed on the base after the
# branch point adds no suite. A base that does not resolve fails the gate, never skips the suites. Each suite runs hermetic, as
# shell-suites.sh runs it: no SG_* variable, its own HOME. The promotion gate is the full suite (promote-stg.sh), not this.
# The gate runs under the machine-wide gate lock (gate.sh) and leaves the fast status line the hub reads. Exit 0 when all pass,
# 1 on a defect, 2 when the base does not resolve, 4 when the verdict is unproven: the battery is inconclusive twice (#699) or a
# tool is absent (spec 1163). Exit 0 is pass and 1 is fail; 2 and 4 are unproven and hold landing. The verdict, one of pass, fail
# or unproven with its reason and the checks that ran, goes to the history line through GATE_OUTCOME_FILE.
# A pass is kept under its key (#955), so a second gate at the same key runs no battery; a red is never kept. SG_FAST_MEMO=0 turns it off.
here="$(cd "$(dirname "$0")" && pwd)"
base=${SG_FAST_BASE:-origin/${SG_BASE:-dev}}
# The base battery is one definition (base-battery.sh), the one branchland.sh's landing check and wave.sh's tip check run
# over the same fast gate. Its pytest takes a bounded worker count, SG_GATE_WORKERS, never `auto`.
. "$here/base-battery.sh"
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
fastcmd=${SG_FAST_CMD:-"$(sg_base_battery "$py" "$base" fast)"}
retrycmd=${SG_FAST_RETRY_CMD:-"$py -m pytest -p no:deepeval -p no:xdist -o heavy_cap_seconds=20"}
# The memo key names what the suite reads: the tree's content (HEAD's tree object, never its commit), its uncommitted state,
# the base ref and its commit, the merge-base the delta starts from, and the battery text. The battery runs over the tree, so
# a pass won on a dirty tree must never answer for the commit. A rebase that keeps every file rewrites the commit sha and the
# tree object stays, so the pass still answers (#1205); a commit that changes a file is a new tree and runs the battery. It is
# read before the machine lock, so a memo hit waits for no gate, and again inside the lock, so two gates that miss together
# share one battery.
st=${SG_STATE:-/tmp/sg-state}
memokey=
if [ "${SG_FAST_MEMO:-1}" != 0 ]; then
  bsha=$(git rev-parse --verify -q "$base^{commit}" 2>/dev/null); hsha=$(git rev-parse --verify -q HEAD 2>/dev/null)
  tsha=$(git rev-parse --verify -q "HEAD^{tree}" 2>/dev/null); mb=$(git merge-base HEAD "$base" 2>/dev/null)
  dirt=$( { git status --porcelain; git diff HEAD; git ls-files -o --exclude-standard -z | while IFS= read -r -d '' f; do printf '%s\n' "$f"; cat -- "$f" 2>/dev/null; done; } 2>/dev/null | cksum | cut -d' ' -f1)
  [ -n "$bsha" ] && [ -n "$hsha" ] && [ -n "$tsha" ] && [ -n "$mb" ] \
    && memokey="$tsha ${dirt:-x} $base $bsha $mb $(printf '%s\n%s' "$fastcmd" "$retrycmd" | cksum | cut -d' ' -f1)"
fi
memo_file() {  # <key>: the file that holds a key; the name is a checksum, the key inside it is compared whole
  echo "$st/gate-memo/$(printf '%s' "$1" | cksum | cut -d' ' -f1)"
}
memo_store() {  # <key> <log>: a temp file and a mv, so no reader sees half of it
  local f; mkdir -p "$st/gate-memo" 2>/dev/null || return 0
  f=$(memo_file "$1"); printf '%s\t%s\n' "$1" "$2" > "$f.$$" && mv -f "$f.$$" "$f"
}
if [ -n "$memokey" ] && [ -r "$(memo_file "$memokey")" ]; then
  IFS=$'\t' read -r mk ml < "$(memo_file "$memokey")"
  if [ "$mk" = "$memokey" ]; then
    # The hit writes the pass line itself and no history line: a line with no selection and no run time would read as a whole
    # battery. The line names the current commit, not the tree object: the pass answers for the tip the gate stands on.
    SG_GATE_STATUS=self bash "$here/gate.sh" --status fast pass "$hsha"
    echo "fast-gate: pass ${hsha:0:9} (log $ml) memo: the same tree, base and battery passed, so no battery ran"; exit 0
  fi
fi
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_KIND=fast exec bash "$here/gate.sh" bash "$0" "$@";; esac
# The base the gate read the tree against goes out beside the scope (GATE_BASE_FILE), so an unproven entry's obligation
# names it (gate.sh, spec 1163): the commit when the base resolves, else the ref as it was named.
gbase=$(git rev-parse --verify -q "$base^{commit}" 2>/dev/null)
[ -z "${GATE_BASE_FILE:-}" ] || printf '%s\n' "${gbase:-$base}" > "$GATE_BASE_FILE"
# verdict <outcome> <reason> <ran> <not run>: the verdict the history line carries (spec 1163). gate.sh hands the file over and reads it
# inside the lock; a field is never empty, so a dash stands for none.
verdict() {
  [ -z "${GATE_OUTCOME_FILE:-}" ] || printf '%s\t%s\t%s\t%s\n' "$1" "$(printf '%s' "$2" | tr '\t' ' ')" "$3" "$4" > "$GATE_OUTCOME_FILE"
}
mb=$(git merge-base HEAD "$base" 2>/dev/null) || { verdict unproven "base $base does not resolve, so the changed scripts are unknown" - "ruff mypy pytest"; echo "fast-gate: base $base does not resolve: the changed scripts are unknown, so the gate does not pass"; exit 2; }
top=$(git rev-parse --show-toplevel) || exit 2
# classify <log> <rc>: the class of a red battery (#699), one word, then its detail: lint (ruff's code and path, #670, #671), type
# (mypy's line), collection (the files pytest could not collect), pytest (the failed or errored test ids), absent (ruff, mypy or
# pytest is not installed in the battery's interpreter, spec 1163), saturation (a worker crashed, or a signal killed the battery)
# or unknown (none of these). A red that names no failed test is never read as one cause: it is a lint, type or collection
# defect, or it is inconclusive.
ids_of() { sed -n -e 's/^FAILED \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ ]*::[^ ]*\).*/\1/p' "$1" | sort -u; }
classify() {
  local log=$1 rc=$2 p f
  p=$(sed -n -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \[[^]]*\] \(.*\)$/\1 \2/p' -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \([^[].*\)$/\1 \2/p' "$log" | head -n 1)   # with or without the fix marker (#671)
  if [ -n "$p" ]; then f=$(sed -n 's/^ *--> \(.*\)$/\1/p' "$log" | head -n 1); echo "lint the base battery failed at ruff: ${f:+$f }$p"; return; fi
  p=$(sed -n 's/^\([^ ]*:[0-9][0-9]*\): error: \(.*\)$/\1: \2/p' "$log" | head -n 1)
  [ -n "$p" ] && { echo "type the base battery failed at mypy: $p"; return; }
  p=$(sed -n -e 's/^ERROR collecting \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ :]*\.py\)\( .*\)*$/\1/p' "$log" | sort -u | tr '\n' ' ' | sed 's/ $//')
  if [ -n "$p" ] || /usr/bin/grep -qE 'errors? during collection' "$log"; then echo "collection pytest could not collect ${p:-its tests}"; return; fi
  p=$(ids_of "$log" | tr '\n' ' ' | sed 's/ $//')
  [ -n "$p" ] && { echo "pytest the base battery failed at $p"; return; }
  p=$(/usr/bin/grep -oE 'No module named [^a-z]*(ruff|mypy|pytest)([^A-Za-z0-9_.]|$)' "$log" | head -n 1 | sed -E 's/.*named [^a-z]*([a-z]+).*/\1/')
  [ -n "$p" ] && { echo "absent $p is absent from the battery's interpreter, so its check did not run"; return; }
  if [ "$rc" -ge 128 ] || /usr/bin/grep -qE 'node down|crashed worker|worker .*crashed|^Killed|MemoryError|Cannot allocate memory|Too many open files|Resource temporarily unavailable' "$log"; then
    echo "saturation the battery exited $rc with a crashed worker or a kill, and no test id"; return; fi
  echo "unknown the battery exited $rc and named no lint, type, collection or test failure"
}
# A test that fails under the parallel battery and passes alone is saturation, not a defect (#520): the failed tests the log
# names run once more, serially (pyproject turns the pytest cache off, so --lf would rerun the whole battery). A red there too
# fails the gate. A saturation or unknown red is inconclusive: the battery runs once more, and only once. A pass there goes
# on; a defect there fails as that defect; inconclusive again exits 4, and the landing holds. The battery output stays in a
# log that the failure line names. Exit 1 is a defect, 4 is inconclusive; neither grants a landing.
logdir=${SG_FAST_LOG_DIR:-${TMPDIR:-/tmp}}   # never SG_STATE (#945): the child that ran the gate may not open a file there
mkdir -p "$logdir" 2>/dev/null
gcd=$(git rev-parse --git-common-dir 2>/dev/null); [ -n "$gcd" ] && gcd=$(cd "$top" && cd "$gcd" 2>/dev/null && pwd -P)   # the real repository, never the worktree
# The name is the real repository, the tip and a pid: every child runs in ws/target-<child>, so a worktree basename collides
# across runs, and two runs at one short sha would write this log at the same time (#945).
log=$logdir/fast-gate-$(printf '%s' "${gcd:-$top}" | cksum | cut -d' ' -f1)-$(git rev-parse --short HEAD 2>/dev/null || echo x)-$$.log
for try in 1 2; do
  lg=$log; [ $try = 1 ] || lg=${log%.log}-retry.log
  bash -c "$fastcmd" > "$lg" 2>&1 && break
  rc=$?; tail -n 5 "$lg"
  c=$(classify "$lg" "$rc"); cls=${c%% *}; detail=${c#* }
  case $cls in
    absent)
      case $detail in ruff*) r=- n="ruff mypy pytest";; mypy*) r=ruff n="mypy pytest";; *) r="ruff mypy" n=pytest;; esac
      verdict unproven "$detail" "$r" "$n"; echo "fast-gate: $cls: $detail; not retried ($lg)"; exit 4;;
    saturation|unknown)
      [ $try = 1 ] && { echo "fast-gate: $cls: $detail; inconclusive, so the battery runs once more ($lg)"; continue; }
      verdict unproven "$detail" unknown unknown
      echo "fast-gate: $cls: $detail; inconclusive twice, so the gate does not pass and the landing holds ($lg)"; exit 4;;
    pytest)
      # shellcheck disable=SC2086  # one word per test id; set -f keeps a parametrized id such as t[a.sh] from globbing
      set -f; if bash -c "$retrycmd \"\$@\"" retry $(ids_of "$lg") >> "$lg" 2>&1; then set +f
        echo "fast-gate: the base battery failed under load and its failed tests passed alone: saturation, not a defect ($lg)"; break; fi
      verdict fail "$detail" "ruff mypy pytest" -
      echo "fast-gate: pytest: $detail, and they fail alone too ($lg)"; exit 1;;
    lint|type|collection)
      case $cls in lint) r=ruff n="mypy pytest";; type) r="ruff mypy" n=pytest;; *) r="ruff mypy pytest" n=-;; esac
      verdict fail "$detail" "$r" "$n"; echo "fast-gate: $cls: $detail ($lg)"; exit 1;;
  esac
done
# (#885) The pytest tally and the code-quality reading print together; the pipe's status is tee's, so the reading never changes the exit.
tally=$(/usr/bin/grep -E '[0-9]+ (passed|failed|errors?)' "$lg" | tail -n 1)
{ echo "fast-gate: pytest tally: ${tally:-none: no pytest count in the battery log}"; "$py" "$here/quality-reading.py" "$base"; } 2>&1 | tee -a "$lg"
changed=$(git diff --name-only --diff-filter=d "$mb" HEAD)
suites=$(printf '%s\n' "$changed" | sed -n -e 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' -e 's|^contrib/squad-goal/scripts/\(.*\)\.py$|\1|p' | grep -v / \
  | sed 's|^test-||')
# A fixture suite that copies a module list names it in its `# mirrors:` line; a change to any listed file runs that suite.
for t in "$top"/contrib/squad-goal/scripts/test-*.sh; do [ -f "$t" ] || continue
  for f in $(sed -n 's/^# mirrors: //p' "$t" | head -n 1); do
    printf '%s\n' "$changed" | /usr/bin/grep -qxF "contrib/squad-goal/scripts/$f" && { suites="$suites"$'\n'"$(basename "$t" .sh | sed 's/^test-//')"; break; }
  done
done
suites=$(printf '%s\n' $suites | sort -u)
bran="ruff mypy pytest"; bnot=-
/usr/bin/grep -q '^no pytest ran' "$lg" && { bran="ruff mypy"; bnot=pytest; }
failed=""; ranS=""
for n in $suites; do s=$top/contrib/squad-goal/scripts/test-$n.sh; [ -f "$s" ] || continue
  echo "=== test-$n.sh"; ranS="$ranS test-$n.sh"
  hm=$(mktemp -d); if (cd "$top" && env $(compgen -e | grep '^SG_' | sed 's/^/-u /') HOME="$hm" bash "$s"); then echo "--- ok: test-$n.sh"
  else echo "--- FAILED: test-$n.sh"; failed="$failed test-$n.sh"; fi; rm -rf "$hm"; done
[ -z "$failed" ] || { verdict fail "failed suite:$failed" "$bran$ranS" "$bnot"; echo "fast-gate: FAILED:$failed"; exit 1; }
verdict pass - "$bran$ranS" "$bnot"
# The verdict the child reads: an exit 0 is a pass, but the one line names it and the log the child may open, so a session that
# saw a lock wait on stderr still knows the gate passed (#945).
[ -z "$memokey" ] || memo_store "$memokey" "$lg"
sha=$(git rev-parse HEAD 2>/dev/null)
echo "fast-gate: pass ${sha:0:9} (log $lg)"
