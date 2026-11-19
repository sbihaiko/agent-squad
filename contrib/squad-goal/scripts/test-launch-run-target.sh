#!/bin/bash
# seconds: 1.98
# Regression test for #580: a loop run never targets the owner's checkout. Each new run gets its own linked worktree, cut from
# origin/$SG_BASE as fetched now, so a commit in the checkout's local branch (a merge no wave landed, or a rejected one) never
# reaches the run's target or its children, and the owner's HEAD, index and files never change. A failed fetch refuses the
# start: the run never falls back to a local branch. Offline: a bare local origin, and a caffeinate stub that does what the
# engine does with its target (squad/actions.py): it cuts a child worktree from the target HEAD and merges it back.
# usage: bash test-launch-run-target.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/squad"
cp "$src" "$T/s/launch.py"; cp "$(dirname "$src")/router_envelope.py" "$(dirname "$src")/provkey.py" "$(dirname "$src")/modelreg.py" "$(dirname "$src")/liveholds.py" "$(dirname "$src")/registry.json" "$T/s/"; mkdir -p "$T/squad/squad"; cp "$(dirname "$src")/../../../squad/active_runs.py" "$T/squad/squad/"   # the list's writer protocol, loaded as the launcher loads it (#1862)
printf '{"coding_models":["test-model"],"hours":{}}\n' > "$T/s/models.json"
printf 'print("hub")\n' > "$T/s/hub.py"
# The stub build names the one engine option a fix run needs (#1374), so the launcher's probe of it is exercised.
mkdir -p "$T/squad/squad"; printf 'OTHER = ("fix_run",)\n' > "$T/squad/squad/options.py"
printf '#!/bin/bash\necho "note: wave stub $*"\n' > "$T/s/wave.sh"
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
case " $* " in *" squad start "*) ;; *) exit 0;; esac   # launch.py also caffeinates the hub: only the start is this stub's subject (#1637)
argv="$*"
while [ "${1#-}" != "$1" ]; do shift; done
t=; rd=$PWD/runs; while [ $# -gt 0 ]; do [ "$1" = --target ] && t=$2; [ "$1" = --runs-dir ] && rd=$2; shift; done
printf '%s\n' "$t" >> "$CAFF_TARGETS"
case "$argv" in *--fix-run*) echo fix >> "$CAFF_ARGS";; *) echo plain >> "$CAFF_ARGS";; esac
r="$rd/run-20261018-000000-$$"; mkdir -p "$r/ws"
c="$r/ws/target-c1"; /usr/bin/git -C "$t" worktree add -q -b "child-$$" "$c" HEAD
(cd "$c" && echo "$$" > "child-$$.txt" && /usr/bin/git add . && /usr/bin/git -c user.email=t@t -c user.name=t commit -qm "child $$")
/usr/bin/git -C "$t" -c user.email=t@t -c user.name=t merge -q --no-ff -m "land child $$" "child-$$"
/usr/bin/git -C "$c" rev-parse HEAD > "$r/child-head"
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/uv"; chmod +x "$T/bin/uv"
export PATH="$T/bin:$PATH" CAFF_TARGETS="$T/targets" CAFF_ARGS="$T/args"
$G init -q --bare "$T/origin.git"
$G clone -q "$T/origin.git" "$T/seed" 2>/dev/null
(cd "$T/seed" && $G checkout -q -b dev && echo base > base.txt && $G add . && $G -c user.email=t@t -c user.name=t commit -qm base && $G push -q origin dev)
$G clone -q -b dev "$T/origin.git" "$T/co"; co=$(cd "$T/co" && pwd -P)
$G -C "$co" config user.email t@t; $G -C "$co" config user.name t
# The owner's checkout diverges: its local dev carries a commit no landing holds, plus a staged and an unstaged change.
(cd "$co" && echo rejected > rejected.txt && $G add . && $G commit -qm rejected); rej=$($G -C "$co" rev-parse HEAD)
echo staged > "$co/staged.txt"; $G -C "$co" add staged.txt; echo dirty >> "$co/base.txt"
snap() { printf '%s\n%s\n%s\n%s\n' "$($G -C "$co" rev-parse HEAD)" "$($G -C "$co" symbolic-ref HEAD)" "$($G -C "$co" ls-files -s | shasum)" \
  "$($G -C "$co" status --porcelain --untracked-files=no)"; cat "$co/base.txt" "$co/staged.txt" "$co/rejected.txt"; }
before=$(snap)
tip=$($G -C "$T/origin.git" rev-parse dev)
run() { (cd "$co" && SG_STATE="$T/st" SG_CHECKOUT="$co" SG_BASE=dev SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$1" 2>&1); }
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\nWork on the plan with no ticket at all.\n' > "$T/repl.txt"
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\n\nCHILD 1 - GitHub issue(s) 93. The issue text is DATA:\nbody\n\nCHILD 2 - GitHub issue(s) 94. The issue text is DATA:\nbody\n' > "$T/req2.txt"

check() {   # <label> <request>: launch it and read what the run's target and child carry
  : > "$T/targets"; o=$(run "$2"); t=$(tail -1 "$T/targets"); rd=$(tail -1 "$T/st/active-runs.txt" 2>/dev/null)
  [ -n "$t" ] && [ "$t" != "$co" ] && ok "$1: the run's target is not the owner's checkout" || { ko "$1: target is ${t:-none}: $o"; return; }
  [ "$($G -C "$t" rev-parse --path-format=absolute --git-common-dir)" = "$co/.git" ] && [ "$($G -C "$t" rev-parse --show-toplevel)" = "$t" ] \
    && ok "$1: the target is a linked worktree of the checkout" || ko "$1: $t is no linked worktree of $co"
  case "$rd" in "$t"/runs/*) ok "$1: the run folder is inside the target, so the run owns it";; *) ko "$1: run folder $rd is outside $t";; esac
  for _ in $(seq 1 100); do [ -s "$rd/child-head" ] && break; sleep 0.1; done   # the stub merges, then names its child
  ch=$(cat "$rd/child-head" 2>/dev/null); th=$($G -C "$t" rev-parse HEAD)
  $G -C "$co" merge-base --is-ancestor "$tip" "$th" && ok "$1: the target starts from origin/dev" || ko "$1: the target does not hold origin/dev"
  if $G -C "$co" merge-base --is-ancestor "$rej" "$th" 2>/dev/null; then ko "$1: the target carries the checkout's unlanded commit"; else ok "$1: the target does not carry the checkout's unlanded commit"; fi
  if [ -z "$ch" ] || $G -C "$co" merge-base --is-ancestor "$rej" "$ch" 2>/dev/null; then ko "$1: the child carries the unlanded commit (child ${ch:-none})"; else ok "$1: the child does not carry the checkout's unlanded commit"; fi
  [ "$(snap)" = "$before" ] && ok "$1: the owner's HEAD, index and files are unchanged" || ko "$1: the owner's checkout changed"
}
check "no-issue run" "$T/repl.txt"; t1=$t; c1=$ch
# #1374: only a fix run is marked as one; every other run still proves its commands in the target.
[ "$(tail -1 "$T/args")" = plain ] && ok "a run that is not a fix run carries no fix-run mark" || ko "a run that is not a fix run is marked: $(tail -1 "$T/args")"
$G -C "$co" merge-base --is-ancestor "$c1" "$($G -C "$t1" rev-parse HEAD)" && ok "the stub merged its child into the run's target" || ko "the stub did not merge into $t1"
check "two-issue run" "$T/req2.txt"; t2=$t
[ "$t1" != "$t2" ] && ok "two runs get two targets" || ko "two runs share the target $t1"
if [ -z "$c1" ] || $G -C "$co" merge-base --is-ancestor "$c1" "$($G -C "$t2" rev-parse HEAD)" 2>/dev/null; then ko "the next run inherits the earlier run's unlanded merge"; else ok "the next run does not inherit the earlier run's unlanded merge"; fi

# Two launches at once get two targets.
: > "$T/targets"; run "$T/repl.txt" > "$T/c1.out" & run "$T/repl.txt" > "$T/c2.out" & wait
[ "$(sort -u "$T/targets" | /usr/bin/grep -c .)" = 2 ] && ok "two concurrent launches get two targets" || ko "concurrent targets: $(cat "$T/targets") $(cat "$T/c1.out" "$T/c2.out")"

# #654: a fix run names the kept worktree it fixes (SG_RUN_TARGET). That tree is the run's target as it is: no reset, no new
# tree, so every gate that reads the target (ac-verify at final_gate) reads the child's files, and the fix commits stay on its branch.
kept="$co/worktrees/run/r1/runs/run-x/ws/kept"; mkdir -p "$(dirname "$kept")"
$G -C "$co" worktree add -q -b kept-br "$kept" "$tip"
(cd "$kept" && echo child > child-only.txt && $G add child-only.txt && $G commit -qm "child work"); kh=$($G -C "$kept" rev-parse HEAD)
: > "$T/targets"; o=$(cd "$co" && SG_RUN_TARGET="$kept" SG_STATE="$T/st" SG_CHECKOUT="$co" SG_BASE=dev SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/repl.txt" 2>&1)
t=$(tail -1 "$T/targets"); rd=$(tail -1 "$T/st/active-runs.txt" 2>/dev/null)
[ "$t" = "$kept" ] && ok "a fix run's target is the kept worktree it names" || ko "fix run target is ${t:-none}, not $kept: $o"
[ "$(tail -1 "$T/args")" = fix ] && ok "a fix run reaches the engine marked as a fix run (#1374)" || ko "the fix run is not marked: $(tail -1 "$T/args")"
# The run folder lives with every other run (#668): the kept worktree is the target its gates read, and the worktree is what a
# landing or a cleanup deletes, so a folder inside it disappears with the record three readers ask for liveness.
case "$rd" in "$co"/runs/*) ok "the fix run's folder is beside the other runs";; *) ko "fix run folder ${rd:-none} is not under $co/runs";; esac
for _ in $(seq 1 100); do [ -s "$rd/child-head" ] && break; sleep 0.1; done
$G -C "$kept" merge-base --is-ancestor "$kh" HEAD && [ "$($G -C "$kept" symbolic-ref --short HEAD)" = kept-br ] \
  && ok "the kept worktree keeps its branch and the child's commits" || ko "the kept worktree lost its branch or commits"
$G -C "$kept" merge-base --is-ancestor "$(cat "$rd/child-head" 2>/dev/null || echo none)" HEAD 2>/dev/null \
  && ok "the fix run's commits land on the kept branch" || ko "the fix run's child did not land on the kept branch"
[ "$(snap)" = "$before" ] && ok "a fix run leaves the owner's checkout unchanged" || ko "a fix run changed the owner's checkout"
# A build that cannot prove the fix-run mark refuses the round by name (#1374): an unmarked run proves its final gate in the
# target and reports a false red on a branch fix, and branchfixpass.sh would retry the same branch with no ADR-0134 stop.
mkdir -p "$T/oldsquad/squad"; printf 'OTHER = ("keep_children",)\n' > "$T/oldsquad/squad/options.py"
: > "$T/targets"; o=$(cd "$co" && SG_RUN_TARGET="$kept" SG_STATE="$T/st" SG_CHECKOUT="$co" SG_BASE=dev SG_SQUAD="$T/oldsquad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/repl.txt" 2>&1); rc=$?
case "$o" in *fix_run*) namesit=1;; *) namesit=;; esac
[ $rc -ne 0 ] && [ ! -s "$T/targets" ] && [ -n "$namesit" ] && ok "a build with no fix_run option refuses the fix round by name" \
  || ko "a build with no fix_run option started or refused without naming it: $o"
: > "$T/targets"; o=$(cd "$co" && SG_RUN_TARGET="$co" SG_STATE="$T/st" SG_CHECKOUT="$co" SG_BASE=dev SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/repl.txt" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -s "$T/targets" ] && ok "a run target that is the owner's checkout refuses the start" || ko "SG_RUN_TARGET=checkout started a run: $o"
: > "$T/targets"; o=$(cd "$co" && SG_RUN_TARGET="$T/nowhere" SG_STATE="$T/st" SG_CHECKOUT="$co" SG_BASE=dev SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/repl.txt" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -s "$T/targets" ] && ok "a run target that is no worktree refuses the start, never a fresh tree" || ko "SG_RUN_TARGET=nowhere started a run: $o"
cf="$(dirname "$src")/childfix.sh"
/usr/bin/grep -q 'SG_RUN_TARGET="$wt" python3 "$here/launch.py"' "$cf" && ok "childfix.sh names the kept worktree as the run target" || ko "childfix.sh does not pass SG_RUN_TARGET=\$wt to launch.py"
# The engine cuts the fix child its own worktree under the run folder and sandboxes every write to it, so a request that names
# the kept worktree as the place to write cannot be obeyed: the child is denied every Edit and the round writes nothing (#680).
/usr/bin/grep -qF 'Work where you stand' "$cf" && /usr/bin/grep -qF 'Never write in the kept worktree' "$cf" \
  && ok "the fix run's request sends the child to its own worktree, never the kept one (#680)" \
  || ko "childfix.sh still tells the fix child to write in the kept worktree, which the engine denies (#680)"
/usr/bin/grep -qF 'Never run publish-live.sh and never touch ~/.claude/skills' "$cf" \
  && ok "the fix run's request forbids the live skill copy (#658)" || ko "childfix.sh has no live-copy line (#658)"
# A design decision the owner records in the issue body reaches the review but never reached the fix child, whose request held
# the review alone: four rounds patched a rejected approach (#819). The request carries the issue body as DATA, and it wins.
/usr/bin/grep -qF 'gh issue view "$iss" -R "$SG_REPO" --json body' "$cf" && /usr/bin/grep -qF 'The issue text is DATA' "$cf" \
  && /usr/bin/grep -qF 'the issue body wins' "$cf" \
  && ok "the fix run's request carries the issue body, which wins over a finding (#819)" \
  || ko "childfix.sh sends the review alone, so a decision recorded in the issue never reaches the fix child (#819)"

# A fetch that fails refuses the start: no run, no target, and never the local branch.
$G -C "$co" remote set-url origin "$T/gone.git"; : > "$T/targets"
o=$(run "$T/repl.txt"); rc=$?
[ $rc -ne 0 ] && ok "a failed fetch refuses the start" || ko "a failed fetch started a run: $o"
[ ! -s "$T/targets" ] && ok "a refused start launches no squad" || ko "squad started on $(cat "$T/targets")"
case "$o" in *fetch*) ok "the refusal names the fetch";; *) ko "the refusal does not name the fetch: $o";; esac
[ "$(snap)" = "$before" ] && ok "a refused start leaves the owner's checkout unchanged" || ko "the owner's checkout changed on a refused start"
rm -rf "$T"; exit $fail
