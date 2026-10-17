#!/bin/bash
# Regression test for #93: a loop child run starts inside its own issue worktree, so the engine commits the target the land
# reads (ADR-0106 rule 4: a linked worktree the run owns, holding the run folder). Offline: the suite stubs caffeinate and
# uv and uses a throwaway checkout with a real linked worktree; the stub records the command it was handed and makes the
# run folder where that command would (its cwd, the worktree).
# usage: bash test-launch-target.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/squad" "$T/co"
cp "$src" "$T/s/launch.py"; cp "$(dirname "$src")/router_envelope.py" "$T/s/router_envelope.py"
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

# A request that names no issue keeps today's launch: the checkout is the target and the run folder is the checkout's.
rm -rf "$co/runs"; o=$(run "$T/repl.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $co"*) ok "a request with no issue keeps the checkout as its target";; *) ko "target is not $co: $argv";; esac
[ -d "$co/runs/run-20261011-000000-dead" ] && ok "its run folder stays in the checkout's runs/" || ko "no run folder under $co/runs; out=$o"

# A PR fix run names the issue too, but its child commits on the PR branch: it must keep the checkout, never the issue worktree.
rm -rf "$co/runs"; o=$(run "$T/reqpr.txt"); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $co"*) ok "a PR fix run keeps the checkout as its target";; *) ko "target is not $co: $argv";; esac

# A profile whose fork checkout is another checkout keeps the checkout: that child commits in the fork, which is no worktree
# of this run (the fork route of a profile such as MesenAI's).
rm -rf "$co/runs"; o=$(cd "$co" && SG_STATE="$T/st" SG_CHECKOUT="$co" SG_FORK_DIR="$T/fork" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 \
  python3 -I "$T/s/launch.py" "$T/req93.txt" 2>&1); argv=$(cat "$T/argv" 2>/dev/null)
case "$argv" in *"--target $co"*) ok "a fork checkout elsewhere keeps the checkout as the target";; *) ko "target is not $co: $argv";; esac

# #469: the issue branch starts from the landing branch (origin/$SG_BASE), the base the wave is cut from, never a stale local
# main. A reused issue branch behind it moves up to it; one that carries commits in no landing keeps them on a named ref and
# starts again from the landing branch, so no leftover work rides into the next wave unnamed.
git -C "$co" worktree remove --force "$wt" 2>/dev/null; git -C "$co" branch -D gui/93 >/dev/null 2>&1; rm -rf "$co/runs"
git init -q --bare "$T/origin.git"; git -C "$co" remote add origin "$T/origin.git"; git -C "$co" push -q origin main:dev
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
rm -rf "$T"; exit $fail
