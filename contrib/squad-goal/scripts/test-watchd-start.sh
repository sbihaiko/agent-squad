#!/bin/bash
# seconds: 6.67
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
cp "$d/watchd.sh" "$d/drainpolicy.py" "$T/"   # the start reads the label policy beside the script it runs (#1980)
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
# Each probe reads the module it claims to read: it runs from / and refuses the user site, so the cwd cannot shadow the
# tree, and it prints the module's own path, so a real squad that site-packages hold cannot stand in for the fake.
sigprobe() { ( cd / && PYTHONPATH="$1" python3 -s -c 'import inspect,os,squad.caps as C;print(os.path.realpath(C.__file__));print(inspect.signature(C.free_runs))' ); }
realroot=$(cd "$d/../../.." && pwd -P); fakeroot=$(cd "$T/fakesq" && pwd -P)
real=$(sigprobe "$realroot"); fake=$(sigprobe "$fakeroot")
real_file=$(printf '%s\n' "$real" | sed -n 1p); fake_file=$(printf '%s\n' "$fake" | sed -n 1p)
real_sig=$(printf '%s\n' "$real" | sed -n 2p); fake_sig=$(printf '%s\n' "$fake" | sed -n 2p)
case "$real_file" in "$realroot"/squad/caps.py) ok "the real probe reads the repository's squad.caps";; *) ko "the real probe read $real_file";; esac
case "$fake_file" in "$fakeroot"/squad/caps.py) ok "the fake probe reads the fake install's squad.caps";; *) ko "the fake probe read $fake_file";; esac
[ "$real_sig" = "$fake_sig" ] && ok "the fake install carries the real free_runs signature" || ko "the fake install carries $fake_sig, the real one is $real_sig"

# 8. The fake is derived from the package it mirrors, never a hand-kept guess (#1514): a shim package whose free_runs has a
# signature of its own yields a fake that carries that signature, so a change to squad/caps.py reaches every suite that
# stands a fake install without an edit here.
shim=$T/shimrepo; mkdir -p "$shim/squad"; : > "$shim/squad/__init__.py"
printf 'def free_runs(c, active_runs, root=".", tag=None): return 0\n' > "$shim/squad/caps.py"
fake_squad "$T/derived" "$shim"
derived_sig=$(sigprobe "$T/derived" | sed -n 2p); shim_sig=$(sigprobe "$shim" | sed -n 2p)
[ "$derived_sig" = "$shim_sig" ] && ok "the fake derives free_runs from the package it mirrors" || ko "the fake carries $derived_sig, the shim carries $shim_sig"

# 9. #1696: the run arm refreshes the triage labels once it holds the slot and has written its run record, and a refresh
#    that fails is a warning on stderr and watch.log, never a refusal of the run.
cat > "$T/triage-labels.sh" <<TL
#!/bin/bash
if [ -e "$SG_STATE/watchd.runs/\$PPID" ]; then echo yes >> "$T/run-dir-mark"; else echo no >> "$T/run-dir-mark"; fi
echo "boom: labels" >&2; exit 1
TL
chmod +x "$T/triage-labels.sh"; : > "$SG_STATE/cycle.log"; rm -f "$T/run-dir-mark" "$T/run-stderr"
bash "$T/watchd.sh" supervise 2>"$T/run-stderr" & sv=$!
for _ in $(seq 1 20); do [ -s "$T/run-dir-mark" ] && [ -s "$SG_STATE/cycle.log" ] && break; /bin/sleep 0.5; done
[ "$(cat "$T/run-dir-mark" 2>/dev/null)" = yes ] && ok "the run arm refreshes the triage labels after its run record exists" || ko "the triage labels were refreshed before the run record: '$(cat "$T/run-dir-mark" 2>/dev/null)'"
case "$(cat "$SG_STATE/cycle.log" 2>/dev/null)" in *"boom: labels"*) ok "the failed refresh keeps its own output in cycle.log";; *) ko "cycle.log does not name the failed refresh: '$(cat "$SG_STATE/cycle.log" 2>/dev/null)'";; esac
case "$(cat "$T/run-stderr" 2>/dev/null)" in *"triage labels were not refreshed"*) ok "the failed refresh is a warning on stderr";; *) ko "the failed refresh said nothing on stderr: '$(cat "$T/run-stderr" 2>/dev/null)'";; esac
/usr/bin/grep -q "triage labels were not refreshed" "$SG_STATE/watch.log" 2>/dev/null && ok "the failed refresh is a warning in watch.log" || ko "the failed refresh wrote no watch.log line"
[ -n "$(ls "$SG_STATE/watchd.runs" 2>/dev/null)" ] && ok "a failed refresh never refuses the run" || ko "a failed refresh stopped the run: no run record"
bash "$T/watchd.sh" stop >/dev/null 2>&1; kill -9 "$sv" 2>/dev/null; sv=; rm -f "$T/triage-labels.sh"

[ "$fail" = 0 ] && echo "all cases passed" || echo "FAILURES"
exit "$fail"
