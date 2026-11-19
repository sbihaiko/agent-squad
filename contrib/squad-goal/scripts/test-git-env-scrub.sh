#!/bin/bash
# mirrors: git-env-scrub.sh fast-gate.sh gate.sh child-gate.sh shell-suites.sh
# Regression test for #2306: a git hook or a gate exports GIT_DIR, and a suite under it runs `git init --bare <path>`. Git then
# re-initializes GIT_DIR, not <path>, and sets core.bare = true on the checkout that runs the gate. Each gate entry point clears
# the git location variables before it runs a suite, so the probe's bare repository lands at its own path and the checkout
# keeps core.bare unset. A throwaway repository, a throwaway state dir and slot budget, a probe in place of a battery.
# usage: bash test-git-env-scrub.sh [scripts dir]
unset SG_SERIAL_HELD GATE_REUSE_FILE SG_GATE_BOUND_SECONDS GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"
export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2 SG_FAST_LOG_DIR=$T/logs SG_FAST_MEMO=0 SG_CHILD_GATE_WAIT=30
export SG_SQUAD=$(cd "$d/../../.." && pwd); mkdir -p "$SG_STATE" "$SG_FAST_LOG_DIR"
R=$T/r; git init -q -b dev "$R"; echo base > "$R/x.md"; $G -C "$R" add x.md; $G -C "$R" commit -q -m base
# The probe is what a fixture does: it makes a bare repository in a folder of its own and sets that folder's config. Under an
# exported GIT_DIR both commands act on GIT_DIR instead (git 2.53 checked; `git init --bare <path>` with a path does not).
printf '#!/bin/bash\nmkdir -p "%s/bare-$1" && cd "%s/bare-$1" && git init -q --bare && git config core.bare true\n' "$T" "$T" > "$T/probe.sh"
bare() { git config --file "$R/.git/config" --get core.bare; }
reset() { git config --file "$R/.git/config" core.bare false; rm -rf "$T"/bare-*; }
check() {   # <name> <what>: the checkout keeps core.bare false, and the probe's repository is at its own path
  if [ "$(bare)" = false ]; then ok "$2 keeps the checkout's core.bare false"; else ko "$2 set core.bare = $(bare) on the checkout"; fi
  if [ -f "$T/bare-$1/HEAD" ]; then ok "$2 lets the probe create its own bare repository"; else ko "$2: the probe's bare repository is missing"; fi
  reset
}
reset

# A git hook or a worktree command exports GIT_DIR as an absolute path, with the work tree as the working directory.
(cd "$R" && GIT_DIR="$R/.git" bash "$d/gate.sh" bash "$T/probe.sh" gate) >/dev/null 2>&1
check gate "gate.sh under GIT_DIR"

(cd "$R" && GIT_DIR="$R/.git" SG_GATE_SLOTS_HELD=1 bash "$d/child-gate.sh" "$T/probe.sh" child) >/dev/null 2>&1
check child "child-gate.sh under GIT_DIR"

(cd "$R" && GIT_DIR="$R/.git" SG_FAST_BASE=dev SG_GATE_CALLER=entry SG_FAST_CMD="bash $T/probe.sh fast" SG_FAST_RETRY_CMD=false \
  bash "$d/fast-gate.sh") >/dev/null 2>&1
check fast "fast-gate.sh under GIT_DIR"

# Every location variable a git hook can export is cleared, not GIT_DIR alone: a GIT_WORK_TREE left behind makes the
# probe's `git init --bare` refuse to run.
(cd "$R" && GIT_DIR="$R/.git" GIT_WORK_TREE="$R" GIT_INDEX_FILE="$R/.git/index" GIT_COMMON_DIR="$R/.git" \
  GIT_OBJECT_DIRECTORY="$R/.git/objects" bash "$d/gate.sh" bash "$T/probe.sh" all) >/dev/null 2>&1
check all "gate.sh under all five git location variables"

# shell-suites.sh runs every test-*.sh beside it; a copy beside one probe suite runs that suite alone.
mkdir -p "$T/s"; cp "$d/shell-suites.sh" "$T/s/shell-suites.sh"; cp "$T/probe.sh" "$T/s/test-probe.sh"
[ ! -f "$d/git-env-scrub.sh" ] || cp "$d/git-env-scrub.sh" "$T/s/git-env-scrub.sh"
(cd "$R" && GIT_DIR="$R/.git" bash "$T/s/shell-suites.sh") >/dev/null 2>&1
check '' "shell-suites.sh under GIT_DIR"

[ $fail = 0 ] && echo "test-git-env-scrub: all checks green" || { echo "test-git-env-scrub: failed"; exit 1; }
