#!/bin/bash
# mirrors: caps.sh fast-gate.sh gate.sh child-gate.sh shell-suites.sh daysuites.sh
# Regression test for #2306: a git hook or a gate exports GIT_DIR, and a suite under it re-initializes that repository. Each
# gate entry point clears the git location variables before it runs a suite, so the probe's bare repository lands at its own
# path and the checkout keeps core.bare unset. A throwaway repository, a throwaway state dir and slot budget, a probe in
# place of a battery.
# usage: bash test-git-env-scrub.sh [scripts dir]
unset SG_SERIAL_HELD GATE_REUSE_FILE SG_GATE_BOUND_SECONDS GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"
export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2 SG_FAST_LOG_DIR=$T/logs SG_FAST_MEMO=0 SG_CHILD_GATE_WAIT=30
export SG_SQUAD=$(cd "$d/../../.." && pwd); mkdir -p "$SG_STATE" "$SG_FAST_LOG_DIR"
R=$T/r; git init -q -b dev "$R"; echo base > "$R/x.md"; $G -C "$R" add x.md; $G -C "$R" commit -q -m base
# The two shapes a fixture runs under an exported GIT_DIR, measured here on git 2.53. The no-path form `git init --bare`, and
# `git config core.bare true`, act on GIT_DIR. The path form `git init --bare <path>` ignores GIT_DIR and makes <path>.
printf '#!/bin/bash\nmkdir -p "%s/bare-$1" && cd "%s/bare-$1" && git init -q --bare && git config core.bare true\n' "$T" "$T" > "$T/probe.sh"
printf '#!/bin/bash\nmkdir -p "%s/own-$1" && git init -q --bare "%s/own-$1/repo"\n' "$T" "$T" > "$T/pathprobe.sh"
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
cp "$d/caps.sh" "$T/s/caps.sh"
(cd "$R" && GIT_DIR="$R/.git" bash "$T/s/shell-suites.sh") >/dev/null 2>&1
check '' "shell-suites.sh under GIT_DIR"

# daysuites.sh runs one suite per call under one slot of the throwaway budget.
(cd "$R" && GIT_DIR="$R/.git" bash "$d/daysuites.sh" --suite "$T/probe.sh") >/dev/null 2>&1
check '' "daysuites.sh --suite under GIT_DIR"

# A gate entry whose caps.sh has no scrub refuses the run and says so, never runs the suite under the caller's GIT_DIR.
mkdir -p "$T/n"; cp "$d/gate.sh" "$d/serial.sh" "$T/n/"; /usr/bin/grep -v '^sg_git_env_scrub()' "$d/caps.sh" > "$T/n/caps.sh"
cp "$d/shell-suites.sh" "$T/n/shell-suites.sh"; cp "$T/probe.sh" "$T/n/test-probe.sh"
o=$(cd "$R" && GIT_DIR="$R/.git" bash "$T/n/gate.sh" bash "$T/probe.sh" nogate 2>&1); rc=$?
[ "$rc" = 2 ] && [ ! -e "$T/bare-nogate" ] && [ "$(bare)" = false ] && printf '%s' "$o" | /usr/bin/grep -q 'sg_git_env_scrub' \
  && ok "gate.sh with no scrub refuses with a reason and runs nothing" || ko "gate.sh with no scrub: rc $rc, core.bare $(bare): $o"
reset
o=$(cd "$R" && GIT_DIR="$R/.git" bash "$T/n/shell-suites.sh" 2>&1); rc=$?
[ "$rc" = 2 ] && [ ! -e "$T/bare-" ] && [ "$(bare)" = false ] && printf '%s' "$o" | /usr/bin/grep -q 'sg_git_env_scrub' \
  && ok "shell-suites.sh with no scrub refuses with a reason and runs nothing" || ko "shell-suites.sh with no scrub: rc $rc, core.bare $(bare): $o"
reset

# The shape the issue names: `git init --bare <path>` under GIT_DIR makes its own repository, and the scrub keeps it there.
(cd "$R" && GIT_DIR="$R/.git" bash "$d/gate.sh" bash "$T/pathprobe.sh" path) >/dev/null 2>&1
if [ -f "$T/own-path/repo/HEAD" ]; then ok "gate.sh under GIT_DIR: the path-form probe made its own repository"
else ko "gate.sh under GIT_DIR: the path-form probe made no repository at its own path"; fi
rm -rf "$T"/own-*

# Criterion 2 names GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE. The two names it does not list are cleared only beside a hook
# variable, so a gate entry with none of the three exported leaves them as its caller set them.
( export GIT_COMMON_DIR="$R/.git" GIT_OBJECT_DIRECTORY="$R/.git/objects"
  . "$d/caps.sh" >/dev/null 2>&1; sg_git_env_scrub
  [ -n "$GIT_COMMON_DIR" ] && [ -n "$GIT_OBJECT_DIRECTORY" ] ) \
  && ok "no hook variable: the scrub leaves the two names the criteria do not list" \
  || ko "no hook variable: the scrub cleared a name the criteria do not list"

[ $fail = 0 ] && echo "test-git-env-scrub: all checks green" || { echo "test-git-env-scrub: failed"; exit 1; }
