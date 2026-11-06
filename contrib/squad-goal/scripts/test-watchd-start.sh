#!/bin/bash
# Guard for #1386: `watchd.sh start` is refused while any loop-bug issue is open, bare or prefixed, and reads the open
# issues once through the one seam (ADR-0146). A read that fails proves nothing and starts the loop, saying so. A start
# on an already-running loop is not a start: it cancels a drain and succeeds, whatever the issues say.
# Offline: a temp state dir, a stub issue list and stub lanes that stay up until the loop stops. No network.
# usage: bash test-watchd-start.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; gp=; sup=
trap 'bash "$T/watchd.sh" stop >/dev/null 2>&1; kill -9 "$sup" "$gp" 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME"
. "$d/watchd-fixture.sh"   # the process reader a fake env.sh needs (#1514)
reader=$(fake_reader "$(cd "$d/../../.." && pwd)")
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" "$d" "$reader" <<'PY'
import pathlib,sys
f,st,d,reader=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE=; export SG_STATE SG_PROFILE; %s'%(st,reader),1)
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)',
            'gh_aggregate() { [ -e "$SG_STATE/gh-fail" ] && return 1; cat "$SG_STATE/issues.json"; }')
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%d)
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/fastloop.sh"'%d)
t=t.replace('"$(dirname "$me")/cqflush.sh"','"%s/cqflush.sh"'%d)
p.write_text(t)
PY
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_SHARED="$T/shared" SG_SQUAD="$(cd "$d/../../.." && pwd)"
printf '#!/bin/bash\nsleep 300\n' > "$T/watch.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/fastloop.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/watchd.sh"
alive_sup() { [ -n "$sup" ] && kill -0 "$sup" 2>/dev/null; }
issues() { printf '%s\n' "$1" > "$SG_STATE/issues.json"; }

# 1. A bare loop-bug refuses the start, names the issue, and starts nothing.
issues '[{"number":42,"title":"a","labels":[{"name":"bug"},{"name":"loop-bug"}]},{"number":9,"title":"b","labels":[]}]'
out=$(bash "$T/watchd.sh" start 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "a bare loop-bug refuses the start" || ko "a bare loop-bug did not refuse the start (rc=$rc)"
case "$out" in *"refused"*"#42"*) ok "the refusal names the issue";; *) ko "the refusal does not name the issue: $out";; esac
[ -e "$SG_STATE/watchd.pid" ] && ko "a refused start left a supervisor pid" || ok "a refused start starts nothing"

# 2. A prefixed loop-bug refuses it too, and a label that only resembles one does not.
issues '[{"number":43,"title":"a","labels":[{"name":"loop-bug:infra"}]}]'
out=$(bash "$T/watchd.sh" start 2>&1); rc=$?
[ "$rc" -ne 0 ] && case "$out" in *"#43"*) ok "a prefixed loop-bug refuses the start and names it";; *) ko "a prefixed loop-bug refuses without naming it: $out";; esac \
  || ko "a prefixed loop-bug did not refuse the start (rc=$rc)"
issues '[{"number":44,"title":"a","labels":[{"name":"loop-buggy"},{"name":"bug"}]}]'
bash "$T/watchd.sh" start >/dev/null 2>&1; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "a label that only resembles loop-bug stops nothing" || ko "a label that only resembles loop-bug refused the start"
bash "$T/watchd.sh" stop >/dev/null 2>&1; sup=

# 3. No loop-bug starts the loop.
issues '[{"number":45,"title":"a","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]'
bash "$T/watchd.sh" start >/dev/null 2>&1; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "no loop-bug open starts the loop" || ko "the loop did not start with no loop-bug open"

# 4. A start on a running loop is not a start: it cancels a drain and succeeds while a loop-bug is open.
issues '[{"number":46,"title":"a","labels":[{"name":"loop-bug"}]}]'
out=$(bash "$T/watchd.sh" start 2>&1); rc=$?
[ "$rc" -eq 0 ] && case "$out" in *"already running"*) ok "a start on a running loop succeeds and says so";; *) ko "a start on a running loop said: $out";; esac \
  || ko "a start on a running loop was refused while a loop-bug is open (rc=$rc)"
bash "$T/watchd.sh" stop >/dev/null 2>&1; sup=

# 5. A read that fails proves nothing: the loop starts, and one line on stderr says the check did not run.
issues '[]'; : > "$SG_STATE/gh-fail"
out=$(bash "$T/watchd.sh" start 2>&1); sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "a failed read starts the loop" || ko "a failed read refused the start"
case "$out" in *"could not be read"*) ok "a failed read says the loop-bug check did not run";; *) ko "a failed read said nothing: $out";; esac
rm -f "$SG_STATE/gh-fail"
bash "$T/watchd.sh" stop >/dev/null 2>&1; sup=

# 6. A SG_SQUAD whose squad.caps lacks free_runs refuses the start with the reinstall command (#1422).
issues '[]'
bad="$T/badsq"; mkdir -p "$bad/squad"; : > "$bad/squad/__init__.py"; printf 'X = 1\n' > "$bad/squad/caps.py"
out=$(SG_SQUAD="$bad" bash "$T/watchd.sh" start 2>&1); rc=$?
[ "$rc" -ne 0 ] && [ ! -e "$SG_STATE/watchd.pid" ] && ok "a SG_SQUAD without free_runs refuses the start" || ko "a SG_SQUAD without free_runs did not refuse (rc=$rc)"
case "$out" in *"claude plugin uninstall agent-squad@agent-squad && claude plugin install agent-squad@agent-squad"*) ok "the refusal carries the reinstall command";; *) ko "the refusal lacks the reinstall command: $out";; esac

# 7. The fake install the watchd suites start from carries the free_runs the real package carries (#1514): the start check
# reads squad.caps.free_runs of the plugin, so a stub with another signature is a guess that drifts from what it mirrors.
fake_squad "$T/fakesq"
# The import runs from / with PYTHONPATH naming the tree, as the start check's own probe does: from the repository root the
# cwd would shadow the fake and the check would compare the real package with itself.
real_sig=$(cd / && PYTHONPATH="$d/../../.." python3 -c 'import inspect,squad.caps as C;print(inspect.signature(C.free_runs))')
fake_sig=$(cd / && PYTHONPATH="$T/fakesq" python3 -c 'import inspect,squad.caps as C;print(inspect.signature(C.free_runs))')
[ "$real_sig" = "$fake_sig" ] && ok "the fake install carries the real free_runs signature" || ko "the fake install carries $fake_sig, the real one is $real_sig"

[ "$fail" = 0 ] && echo "all cases passed" || echo "FAILURES"
exit "$fail"
