#!/bin/bash
# Regression test for #580: a loop run never targets the owner's checkout. Each new run gets its own linked worktree, cut from
# origin/$SG_BASE as fetched now, so a commit in the checkout's local branch (a merge no wave landed, or a rejected one) never
# reaches the run's target or its children, and the owner's HEAD, index and files never change. A failed fetch refuses the
# start: the run never falls back to a local branch. Offline: a bare local origin, and a caffeinate stub that does what the
# engine does with its target (squad/actions.py): it cuts a child worktree from the target HEAD and merges it back.
# usage: bash test-launch-run-target.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/squad"
cp "$src" "$T/s/launch.py"; cp "$(dirname "$src")/router_envelope.py" "$(dirname "$src")/provkey.py" "$T/s/"
printf '{"coding_models":["test-model"],"hours":{}}\n' > "$T/s/models.json"
printf 'print("hub")\n' > "$T/s/hub.py"
printf '#!/bin/bash\necho "note: wave stub $*"\n' > "$T/s/wave.sh"
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
t=; while [ $# -gt 0 ]; do [ "$1" = --target ] && t=$2; shift; done
printf '%s\n' "$t" >> "$CAFF_TARGETS"
r="$PWD/runs/run-20261018-000000-$$"; mkdir -p "$r/ws"
c="$r/ws/target-c1"; /usr/bin/git -C "$t" worktree add -q -b "child-$$" "$c" HEAD
(cd "$c" && echo "$$" > "child-$$.txt" && /usr/bin/git add . && /usr/bin/git -c user.email=t@t -c user.name=t commit -qm "child $$")
/usr/bin/git -C "$t" -c user.email=t@t -c user.name=t merge -q --no-ff -m "land child $$" "child-$$"
/usr/bin/git -C "$c" rev-parse HEAD > "$r/child-head"
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/uv"; chmod +x "$T/bin/uv"
export PATH="$T/bin:$PATH" CAFF_TARGETS="$T/targets"
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
$G -C "$co" merge-base --is-ancestor "$c1" "$($G -C "$t1" rev-parse HEAD)" && ok "the stub merged its child into the run's target" || ko "the stub did not merge into $t1"
check "two-issue run" "$T/req2.txt"; t2=$t
[ "$t1" != "$t2" ] && ok "two runs get two targets" || ko "two runs share the target $t1"
if [ -z "$c1" ] || $G -C "$co" merge-base --is-ancestor "$c1" "$($G -C "$t2" rev-parse HEAD)" 2>/dev/null; then ko "the next run inherits the earlier run's unlanded merge"; else ok "the next run does not inherit the earlier run's unlanded merge"; fi

# Two launches at once get two targets.
: > "$T/targets"; run "$T/repl.txt" > "$T/c1.out" & run "$T/repl.txt" > "$T/c2.out" & wait
[ "$(sort -u "$T/targets" | /usr/bin/grep -c .)" = 2 ] && ok "two concurrent launches get two targets" || ko "concurrent targets: $(cat "$T/targets") $(cat "$T/c1.out" "$T/c2.out")"

# A fetch that fails refuses the start: no run, no target, and never the local branch.
$G -C "$co" remote set-url origin "$T/gone.git"; : > "$T/targets"
o=$(run "$T/repl.txt"); rc=$?
[ $rc -ne 0 ] && ok "a failed fetch refuses the start" || ko "a failed fetch started a run: $o"
[ ! -s "$T/targets" ] && ok "a refused start launches no squad" || ko "squad started on $(cat "$T/targets")"
case "$o" in *fetch*) ok "the refusal names the fetch";; *) ko "the refusal does not name the fetch: $o";; esac
[ "$(snap)" = "$before" ] && ok "a refused start leaves the owner's checkout unchanged" || ko "the owner's checkout changed on a refused start"
rm -rf "$T"; exit $fail
