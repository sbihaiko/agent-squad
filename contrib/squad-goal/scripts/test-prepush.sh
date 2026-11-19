#!/bin/bash
# seconds: 1.28
# Regression test for prepush.sh, the run's own pre-push check (#361: the pull-request path is gone, so the only mode is
# `--local <body-file>`: the run, in its worktree, runs it before every push). Throwaway repo, a bare local remote, a temp
# state dir. usage: bash test-prepush.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st"
cp "$d/prepush.sh" "$d/caps.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_CHECKOUT=%s\n' "$T/st" "$T/co" > "$T/s/env.sh"
$G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }
mkdir -p UI scripts; echo x > UI/a.cs; echo y > scripts/b.py; $G add .; c -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
$G checkout -q -b fix/1-x; echo z > UI/c.cs; $G add .; c -m fix; $G push -q origin fix/1-x 2>/dev/null; SHA=$($G rev-parse HEAD)
# b <body>   writes the body file and prints its path: prepush.sh reads the body the run would put in the push
b() { printf '%s\n' "$1" > "$T/body"; printf '%s' "$T/body"; }
pp() { bash "$T/s/prepush.sh" --local "$@" 2>&1; }
r=$(pp "$(b 'Refs #1200')") && ok "a clean head and body: no output, exit 0" || ko "a clean body was flagged: $r"
r=$(pp "$(b 'Closes #1200')"); case "$r" in *"refs: closing keyword"*) ok "Closes #N flagged";; *) ko "Closes not flagged: '$r'";; esac
r=$(pp "$(b 'Adds `UI/c.cs` and tests in UI/missing_tests.cs')"); case "$r" in *"cited: the body cites UI/missing_tests.cs"*) case "$r" in *UI/c.cs*) ko "present file flagged";; *) ok "cited file missing from the head flagged, present one not";; esac;; *) ko "missing cited file not flagged: '$r'";; esac
r=$(pp "$(b 'runner squad/gui_test_supervise.py and probe tmp/x/run.json; the fork-side scripts/zz.py')") && ok "paths outside the repo's dirs and fork lines skipped" || ko "false positive: $r"
c --allow-empty -m local; r=$(pp "$(b 'Refs #1200')"); case "$r" in *"unpushed: 1 commit"*) ok "an unpushed commit on HEAD is flagged";; *) ko "unpushed not flagged: '$r'";; esac
$G reset -q --hard "$SHA"; r=$(pp "$(b 'Refs #1200')") && ok "a pushed head is clean again" || ko "after the reset: '$r'"
# a body file the run never wrote: the reading is empty, and the head check still runs
$G checkout -q main 2>/dev/null || true
r=$(pp /dev/null) && ok "an empty body file is no failure of its own" || ko "empty body: '$r'"
# #664: the loop's profile lives beside the live scripts, while a worktree ships only profiles/example.sh. A session
# that carries SG_PROFILES_DIR runs the worktree's own prepush against the loop's profile; without it the same copy
# exits 2 and prints nothing, which is the failure a fix run reported.
wt="$T/wt"; mkdir -p "$wt/profiles" "$T/live"
cp "$d/env.sh" "$d/skiplabels.sh" "$d/address.sh" "$d/caps.sh" "$d/prepush.sh" "$wt/"; cp "$d/profiles/example.sh" "$wt/profiles/"
printf 'SG_REPO=x/y\nSG_BASE=main\nSG_CHECKOUT=%s\n' "$T/co" > "$T/live/agent-squad.sh"
ppwt() { ( cd "$T/co" && "$@" bash "$wt/prepush.sh" --local /dev/null 2>&1 ); }
r=$(ppwt env SG_PROFILE=agent-squad SG_PROFILES_DIR="$T/live" SG_STATE="$T/st" SG_SQUAD=/dev/null); rc=$?
[ "$rc" = 0 ] && [ -z "$r" ] && ok "the worktree prepush resolves the loop's profile with SG_PROFILES_DIR set" \
  || ko "with SG_PROFILES_DIR: exit $rc '$r'"
r=$(ppwt env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null); rc=$?
[ "$rc" = 2 ] && ok "without SG_PROFILES_DIR the worktree prepush cannot resolve the profile (exit 2)" \
  || ko "without SG_PROFILES_DIR: exit $rc '$r'"
# The loop's own env.sh resolves the profiles beside it; it must export that directory, so the session it starts carries
# the path and the worktree's prepush resolves the same profile.
lv="$T/livewt"; mkdir -p "$lv/profiles"
cp "$d/env.sh" "$d/skiplabels.sh" "$d/address.sh" "$d/caps.sh" "$wt/prepush.sh" "$lv/"
printf 'SG_REPO=x/y\nSG_BASE=main\nSG_CHECKOUT=%s\n' "$T/co" > "$lv/profiles/agent-squad.sh"
r=$(cd "$T/co" && env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null \
  bash -c '. "$1/env.sh" >/dev/null 2>&1; env' _ "$lv" | sed -n 's/^SG_PROFILES_DIR=//p')
[ "$r" = "$lv/profiles" ] && ok "env.sh exports the resolved SG_PROFILES_DIR to the session it starts" || ko "env.sh did not export SG_PROFILES_DIR: '${r:-unset}'"
r=$(cd "$T/co" && env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null bash "$lv/prepush.sh" --local /dev/null 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$r" ] && ok "a prepush beside the loop's profiles resolves with no SG_PROFILES_DIR in the caller" \
  || ko "resolved-default prepush: exit $rc '$r'"
# #674: a child runs ruff before it hands in. A tree with squad/ and tests/ (the base battery's paths) runs
# `ruff check --fix` and then `ruff check squad tests`; a red blocks the hand-in with ruff's own line. The stub python logs
# its argv, so a wrong argv shows. A tree with no squad/ runs no ruff.
rr="$T/rr"; $G init -q -b main "$rr"; ( cd "$rr" && mkdir -p squad tests && echo 'x = 1' > squad/a.py && echo 'y = 1' > tests/t.py && $G add . && c -m init )
cat > "$T/py" <<EOF
#!/bin/bash
echo "\$*" >> "$T/py.log"
[ "\$1 \$2 \$3" = "-m ruff check" ] || exit 9
case " \$* " in *" --fix "*) [ -f "$T/fixable" ] && echo 'x = 2' > squad/a.py; exit 0;; esac
[ -f "$T/red" ] && { echo 'F811 Redefinition of unused \`f\` from line 1'; echo ' --> tests/t.py:3:5'; exit 1; }
exit 0
EOF
chmod +x "$T/py"
pr() { ( cd "$rr" && SG_GATE_PY="$T/py" bash "$T/s/prepush.sh" --local /dev/null 2>&1 ); }
rm -f "$T/py.log"; r=$(pr); rc=$?
[ "$rc" = 0 ] && [ -z "$r" ] && [ "$(cat "$T/py.log" 2>/dev/null)" = "$(printf -- '-m ruff check --fix squad tests\n-m ruff check squad tests')" ] \
  && ok "a clean tree runs ruff --fix, then ruff check squad tests, and passes" || ko "clean ruff: exit $rc '$r' log '$(cat "$T/py.log" 2>/dev/null)'"
touch "$T/red"; r=$(pr); rc=$?
case "$rc:$r" in 1:*"ruff: "*F811*tests/t.py*) ok "a ruff red blocks the hand-in with ruff's line";; *) ko "ruff red not flagged: exit $rc '$r'";; esac
rm -f "$T/red"; touch "$T/fixable"; r=$(pr); rc=$?
case "$rc:$r" in 1:*"ruff: --fix changed"*squad/a.py*) ok "a fix ruff made and nobody committed blocks the hand-in";; *) ko "uncommitted fix not flagged: exit $rc '$r'";; esac
rm -f "$T/fixable" "$T/py.log"; r=$(SG_GATE_PY="$T/py" pp /dev/null); [ ! -e "$T/py.log" ] && ok "a tree with no squad/ runs no ruff" || ko "ruff ran in a tree with no squad/"
# (#1086) The interpreter is sg_gate_python's answer (caps.sh): SG_GATE_PY, else the checkout's venv, else the venv beside the
# worktree's common git directory, else python3. The stub in the checkout's venv logs each call, so its argv names the exact
# interpreter prepush used: a hand-rolled lookup would have sent a child's worktree to a python3 that lacks mypy.
co="$T/co-venv"; mkdir -p "$co/.venv/bin"
printf '#!/bin/bash\necho "$0 $*" >> "%s"\nexit 0\n' "$T/co.log" > "$co/.venv/bin/python"; chmod +x "$co/.venv/bin/python"
# env.sh sets SG_CHECKOUT, so it answers first: a copy beside this case names the checkout the resolver must read.
s2="$T/s2"; mkdir -p "$s2"; cp "$d/prepush.sh" "$d/caps.sh" "$s2/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_CHECKOUT=%s\n' "$T/st" "$co" > "$s2/env.sh"
rm -f "$T/co.log"; r=$( cd "$rr" && env -u SG_GATE_PY bash "$s2/prepush.sh" --local /dev/null 2>&1 ); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$T/co.log" 2>/dev/null)" = "$(printf -- '%s -m ruff check --fix squad tests\n%s -m ruff check squad tests' "$co/.venv/bin/python" "$co/.venv/bin/python")" ] \
  && ok "prepush runs ruff through sg_gate_python: the checkout's venv answers both calls" || ko "prepush interpreter: exit $rc '$r' log '$(cat "$T/co.log" 2>/dev/null)'"
/usr/bin/grep -q 'sg_gate_python' "$d/prepush.sh" && ok "prepush names sg_gate_python" || ko "prepush keeps its own interpreter lookup"
/usr/bin/grep -q 'py=python3' "$d/prepush.sh" && ko "prepush keeps a hand-rolled interpreter fallback" || ok "prepush keeps no hand-rolled interpreter fallback"
rm -rf "$T"; exit $fail
