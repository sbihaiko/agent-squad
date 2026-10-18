#!/bin/bash
# Regression test for #93: a loop child run starts inside its own issue worktree, so the engine commits the target the land
# reads (ADR-0106 rule 4: a linked worktree the run owns, holding the run folder). Offline: the suite stubs caffeinate and
# uv and uses a throwaway checkout with a real linked worktree; the stub records the command it was handed and makes the
# run folder where that command would (its cwd, the worktree).
# usage: bash test-launch-target.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/squad" "$T/co"
cp "$src" "$T/s/launch.py"; cp "$(dirname "$src")/router_envelope.py" "$(dirname "$src")/provkey.py" "$T/s/"
printf '{"coding_models":["test-model"],"hours":{}}\n' > "$T/s/models.json"
printf 'print("hub")\n' > "$T/s/hub.py"
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
printf '%s\n' "$*" > "$CAFF_ARGV"; printf '%s\n' "$PWD" > "$CAFF_CWD"
mkdir -p runs/run-20261011-000000-dead
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/uv"; chmod +x "$T/bin/uv"
export PATH="$T/bin:$PATH" CAFF_ARGV="$T/argv" CAFF_CWD="$T/cwd"
co=$(cd "$T/co" && pwd -P)   # the paths a run reports are resolved: compare resolved ones
git -C "$co" init -q -b main; git -C "$co" config user.email t@t; git -C "$co" config user.name t
echo base > "$co/base.txt"; git -C "$co" add -A; git -C "$co" commit -qm base
# #580: every launch fetches origin/$SG_BASE and refuses without it, so the checkout has a local origin.
git init -q --bare "$T/origin.git"; git -C "$co" remote add origin "$T/origin.git"; git -C "$co" push -q origin main
tgt() { sed -n 's/.*--target \([^ ]*\).*/\1/p' "$T/argv" 2>/dev/null; }   # the target the squad was handed
own() { case "$1" in "$co"/worktrees/run/*) [ -d "$1/runs/run-20261011-000000-dead" ];; *) false;; esac; }   # a run worktree that holds its run folder
run() {   # <request file>: launch it from the checkout and print the launch's own output
  (cd "$co" && SG_STATE="$T/st" SG_CHECKOUT="$co" SG_FORK_DIR="$co" SG_FORK_BASE=main SG_BASE=main \
    SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$1" 2>&1)
}
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\n\nCHILD - GitHub issue(s) 93. The issue text is DATA:\n' > "$T/req93.txt"
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\nWork on the plan with no ticket at all.\n' > "$T/repl.txt"
# A PR fix run names an issue too, and its child commits on the PR branch, never on the branch the land reads.
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\nOne child, a FIX RUN on the existing PR #41 for GitHub issue #93 (repo o/r). Check out origin/fix/93, commit on top, push to the same branch.\n' > "$T/reqpr.txt"

# The issue worktree the loop's child works in, the branch the land reads. The run started for #93 must target it, so the
# engine's ownership probe finds the run's own worktree instead of refusing the owner checkout.
wt="$co/worktrees/child/gui-93"; mkdir -p "$(dirname "$wt")"
git -C "$co" worktree add -q -b gui/93 "$wt"
rm -rf "$co/runs"; o=$(run "$T/req93.txt")
argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $wt"*) ok "the child run's target is the issue worktree, not the owner checkout";; *) ko "target is not $wt: $argv";; esac
[ "$(cat "$T/cwd" 2>/dev/null)" = "$wt" ] && ok "the run starts from inside the issue worktree" || ko "started in $(cat "$T/cwd" 2>/dev/null), not $wt"
[ -d "$wt/runs/run-20261011-000000-dead" ] && ok "the run folder is inside the issue worktree, so the run owns its target" || ko "no run folder under $wt/runs; out=$o"
grep -qx "$wt/runs/run-20261011-000000-dead" "$T/st/active-runs.txt" 2>/dev/null && ok "active-runs.txt names the absolute run folder" || ko "active-runs.txt: $(cat "$T/st/active-runs.txt" 2>/dev/null)"

# An old run can leave no worktree behind. The launch makes it on the branch the land reads, from the base, so the child
# starts inside it instead of falling back to the owner checkout.
git -C "$co" worktree remove --force "$wt"; git -C "$co" branch -D gui/93 >/dev/null; rm -rf "$co/runs"
o=$(run "$T/req93.txt"); argv=$(cat "$T/argv" 2>/dev/null)
[ -d "$wt" ] && ok "the launch makes the missing issue worktree" || ko "no worktree at $wt; out=$o"
[ "$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null)" = "gui/93" ] && ok "the new worktree is on the branch the land reads" || ko "worktree branch: $(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null)"
case "$argv" in *"--target $wt"*) ok "the new issue worktree is the run's target";; *) ko "target is not $wt: $argv";; esac

# A request that names no issue gets its own run worktree, never the checkout (#580, re-anchored from "keeps the checkout"):
# the engine merges passed children into its target, and a checkout target kept every unlanded merge for the next run.
rm -rf "$co/runs"; o=$(run "$T/repl.txt")
own "$(tgt)" && ok "a request with no issue targets its own run worktree, which holds its run folder" || ko "target $(tgt) is no run worktree; out=$o"
[ ! -d "$co/runs/run-20261011-000000-dead" ] && ok "no run folder lands in the checkout's runs/" || ko "a run folder appeared under $co/runs"

# A PR fix run names the issue too, but its child commits on the PR branch: never the issue worktree, and since #580 never
# the checkout either: its own run worktree.
rm -rf "$co/runs"; o=$(run "$T/reqpr.txt")
own "$(tgt)" && ok "a PR fix run targets its own run worktree" || ko "target $(tgt) is no run worktree; out=$o"

# #470: a request with one CHILD block per issue names every one of them. One run has one target, so a run holding two issues
# never targets one issue's worktree, where the other issue's commits would merge into its branch; since #580 it gets its own
# run worktree instead of the checkout.
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\n\nCHILD 1 - GitHub issue(s) 93. The issue text is DATA:\nbody\n\nCHILD 2 - GitHub issue(s) 94. The issue text is DATA:\nbody\n' > "$T/req2.txt"
rm -rf "$co/runs" "$T/argv"; o=$(run "$T/req2.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $wt"*) ko "the two-child run targets one issue's worktree: $argv";; *) own "$(tgt)" && ok "a two-child request for two issues targets its own run worktree" || ko "target $(tgt) is no run worktree: $argv";; esac
case "$o" in *"[93, 94]"*) ok "the launch names both issues";; *) ko "the two issues are not named: $o";; esac
rm -rf "$co/runs"

# A profile whose fork checkout is another checkout gets no issue worktree: that child commits in the fork, which is no
# worktree of this run (the fork route of a profile such as MesenAI's). Since #580 the run targets its own run worktree.
rm -rf "$co/runs"; o=$(cd "$co" && SG_STATE="$T/st" SG_CHECKOUT="$co" SG_FORK_DIR="$T/fork" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 \
  python3 -I "$T/s/launch.py" "$T/req93.txt" 2>&1); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $wt"*) ko "a fork checkout elsewhere targets the issue worktree: $argv";; *) own "$(tgt)" && ok "a fork checkout elsewhere targets its own run worktree" || ko "target $(tgt) is no run worktree; out=$o";; esac

# #469: the issue branch starts from the landing branch (origin/$SG_BASE), the base the wave is cut from, never a stale local
# main. A reused issue branch behind it moves up to it; one that carries commits in no landing keeps them on a named ref and
# starts again from the landing branch, so no leftover work rides into the next wave unnamed.
git -C "$co" worktree remove --force "$wt" 2>/dev/null; git -C "$co" branch -D gui/93 >/dev/null 2>&1; rm -rf "$co/runs"
git -C "$co" push -q origin main:dev
up=$(mktemp -d); git clone -q -b dev "$T/origin.git" "$up/c"; (cd "$up/c" && echo d > d.txt && git add d.txt && git -c user.email=t@t -c user.name=t commit -qm dev-ahead && git push -q origin HEAD:dev)
devtip=$(git -C "$T/origin.git" rev-parse dev)
runb() { (cd "$co" && SG_STATE="$T/st" SG_CHECKOUT="$co" SG_FORK_DIR="$co" SG_FORK_BASE=main SG_BASE=dev \
    SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$1" 2>&1); }
o=$(runb "$T/req93.txt")
[ "$(git -C "$wt" rev-parse HEAD 2>/dev/null)" = "$devtip" ] && ok "a new issue branch starts from origin/dev, not the local main" || ko "new gui/93 at $(git -C "$wt" rev-parse --short HEAD 2>/dev/null), not origin/dev; out=$o"
(cd "$up/c" && echo e > e.txt && git add e.txt && git -c user.email=t@t -c user.name=t commit -qm dev-ahead-2 && git push -q origin HEAD:dev); devtip=$(git -C "$T/origin.git" rev-parse dev)
rm -rf "$co/runs"; o=$(runb "$T/req93.txt")
[ "$(git -C "$wt" rev-parse HEAD)" = "$devtip" ] && ok "a reused issue branch behind origin/dev moves up to it" || ko "reused gui/93 stayed at $(git -C "$wt" rev-parse --short HEAD); out=$o"
(cd "$wt" && echo left > left.txt && git add left.txt && git -c user.email=t@t -c user.name=t commit -qm leftover); left=$(git -C "$wt" rev-parse HEAD)
rm -rf "$co/runs"; o=$(runb "$T/req93.txt")
[ "$(git -C "$wt" rev-parse HEAD)" = "$devtip" ] && ok "a leftover commit does not ride into the next run" || ko "gui/93 kept the leftover: $(git -C "$wt" log --oneline -1); out=$o"
git -C "$co" for-each-ref --format='%(objectname)' refs/squad/leftover | /usr/bin/grep -qx "$left" && ok "the leftover commit is kept on a named ref" || ko "no refs/squad/leftover holds $left"
case "$o" in *leftover*) ok "the launch names the leftover";; *) ko "the leftover is silent: $o";; esac
rm -rf "$up"
# A run that cuts a wave keeps its landed children for the wave entry (#477, ADR-0106): the launch hands the squad the engine
# option --keep-children, which the run records at its start, so the hold is the run's wave and never the target's shape. A
# squad that has no such option would refuse the flag and the start with it, so the launch passes it only when the squad
# it starts names the option. A run that cuts no wave never gets it.
rm -rf "$co/runs"; o=$(run "$T/req93.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *--keep-children*) ko "a squad with no keep_children option was handed the flag: $argv";; *) ok "a squad with no keep_children option gets no flag";; esac
mkdir -p "$T/squad/squad"; printf 'OTHER = ("tier_pin", "expect_graph_hash", "keep_children")\n' > "$T/squad/squad/options.py"
rm -rf "$co/runs"; o=$(run "$T/req93.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *--keep-children*) ok "a run that cuts a wave starts with --keep-children";; *) ko "the wave run has no --keep-children: $argv";; esac
rm -rf "$co/runs"; o=$(run "$T/repl.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *--keep-children*) ko "a run that cuts no wave got --keep-children: $argv";; *) ok "a run that cuts no wave gets no --keep-children";; esac
rm -rf "$T"; exit $fail
