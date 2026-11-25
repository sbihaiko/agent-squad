#!/bin/bash
# seconds: 6.08
# Regression test: launch.py writes the squad's stdout/stderr into the run folder as launch.log, and keeps the copy
# live until the squad exits, because that is when a refused start writes its reason (Refs #1503). Offline: stub
# caffeinate/uv so squad "starts" a folder, refuses after the folder exists, and exits.
# usage: bash test-launch.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The fixture owns its origin (main) and its markers: an inherited loop profile must not redirect the base branch or close a model.
unset SG_BASE SG_WORKFLOW SG_SHARED
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/co/runs" "$T/squad"
cp "$src" "$T/s/launch.py"; cp "$(dirname "$src")/router_envelope.py" "$(dirname "$src")/provkey.py" "$(dirname "$src")/modelreg.py" "$(dirname "$src")/liveholds.py" "$(dirname "$src")/registry.json" "$T/s/"; mkdir -p "$T/squad/squad"; cp "$(dirname "$src")/../../../squad/active_runs.py" "$T/squad/squad/"   # the list's writer protocol, loaded as the launcher loads it (#1862)
# models.json: one coding model, no hours window, one declared context window. SG_ALLOW_MODEL skips the gate anyway.
printf '{"schema_version":2,"chains":{"coding":[{"id":"test-model","route":"gateway","ctx":"128k"}]}}\n' > "$T/s/models.json"
printf 'print("hub")\n' > "$T/s/hub.py"
# caffeinate: create a run folder in cwd/runs, then refuse 5 s later (a start that dies long after its folder shows up
# is the observed shape: at folder time the log is still empty, so only a copy that outlives launch.py captures it)
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
# Only the squad start is this stub's subject: launch.py also caffeinates the hub, and a stub that records whatever argv it
# is handed let the hub's line overwrite the start's, so the argv checks read the wrong call (#1637).
case " $* " in *" squad start "*) ;; *) exec sleep 1;; esac
printf '%s ' "$@" > "${SG_ARGV:?}"
mkdir -p runs/run-20261011-000000-dead
sleep 5
echo "squad refused: vendor/skills is not clean"
exit 0
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\necho uv-not-used\n' > "$T/bin/uv"; chmod +x "$T/bin/uv"
export PATH="$T/bin:$PATH" SG_ARGV="$T/argv"
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\n--- issue #9\n' > "$T/req.txt"
# #580: a run targets its own worktree, cut from origin/$SG_BASE as fetched now, so the checkout is a repository with an origin,
# and the run folder sits in that worktree: active-runs.txt names it.
G=/usr/bin/git; $G -C "$T/co" init -q -b main; $G -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
$G init -q --bare "$T/origin.git"; $G -C "$T/co" remote add origin "$T/origin.git"; $G -C "$T/co" push -q origin main
o=$(cd "$T/co" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 \
  python3 -I "$T/s/launch.py" "$T/req.txt" 2>&1) || true
log="$(tail -1 "$T/st/active-runs.txt" 2>/dev/null)/launch.log"
[ -f "$log" ] && ok "launch writes launch.log in the run folder" || ko "no run launch.log; out=$o log=$log"
for _ in $(seq 1 20); do /usr/bin/grep -q 'vendor/skills is not clean' "$log" 2>/dev/null && break; sleep 1; done
/usr/bin/grep -q 'vendor/skills is not clean' "$log" 2>/dev/null && ok "launch.log captures what the squad wrote after the folder appeared" || ko "launch.log contents: $(cat "$log" 2>/dev/null) out=$o"
# The launch picks by the models.json coding chain, so the run reads the same order for its own fallback (#617).
/usr/bin/grep -q -- '--model-order test-model ' "$SG_ARGV" 2>/dev/null && ok "the coding chain's order goes to the run" || ko "no --model-order in the argv: $(cat "$SG_ARGV" 2>/dev/null) out=$o"
# A chain entry's declared context window goes to the run too (#1470): the engine skips an entry the prompt would overrun.
/usr/bin/grep -q -- '--model-ctx test-model=128k ' "$SG_ARGV" 2>/dev/null && ok "the coding chain's context window goes to the run" || ko "no --model-ctx in the argv: $(cat "$SG_ARGV" 2>/dev/null) out=$o"
# A node of the graph whose model (an alias resolved to its provider id) the panel switched off refuses the start, and no run folder
# appears (#60). The request's own "Coding model:" line is fine here: the gate that read only that line is what let this through.
rm -rf "$T/co/runs"; mkdir -p "$T/co/runs" "$T/co/docs/squad/workflows"
printf '{"coding_models":["claude-sonnet-5-5","test-model"],"off":{"coding_models":{"claude-sonnet-5-5":true}},"hours":{}}\n' > "$T/s/models.json"
printf '{"root":"main","nodes":[{"name":"router","kind":"agent","model":"sonnet","tools":["Read"]}]}\n' > "$T/co/docs/squad/workflows/default.graph.json"
o=$(cd "$T/co" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" python3 -I "$T/s/launch.py" "$T/req.txt" 2>&1); rc=$?
[ $rc -ne 0 ] && echo "$o" | /usr/bin/grep -q "sonnet -> claude-sonnet-5-5" && ok "a node on a switched-off model refuses the start" || ko "off-model node started or refused without naming it (rc=$rc): $o"
[ -z "$(ls "$T/co/runs")" ] && ok "a refused start leaves no run folder" || ko "run folder exists after a refusal"
o=$(cd "$T/co" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/req.txt" 2>&1); rc=$?
[ $rc -eq 0 ] && ok "SG_ALLOW_MODEL=1 starts it anyway, with a warning" || ko "override still refused (rc=$rc): $o"
# #865: a start refuses an issue a live run already works, and names the holder. The check reads the loop's own state (active-runs.txt
# and each live run's request), never GitHub: a card or list read right after a write is eventually consistent. A run with a verdict
# holds nothing, so the same issue starts again once its run has ended.
printf '{"coding_models":["test-model"],"hours":{}}\n' > "$T/s/models.json"
rm -f "$T/co/docs/squad/workflows/default.graph.json"
H="$T/co/runs/run-20261029-124510-af3898"; mkdir -p "$H" "$T/st2"
printf 'Coding model: test-model\nCHILD — GitHub issue(s) #133 (a): do it\n' > "$H/request.txt"; printf '{"event": "run_start"}\n' > "$H/record.jsonl"
printf '%s\n' "$H" > "$T/st2/active-runs.txt"
printf 'Coding model: test-model\nCHILD — GitHub issue(s) #133 (a): do it\n' > "$T/req133.txt"
rm -f "$SG_ARGV"
o=$(cd "$T/co" && SG_STATE="$T/st2" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/req133.txt" 2>&1); rc=$?
{ echo "$o" | /usr/bin/grep -q "#133" && echo "$o" | /usr/bin/grep -q "run-20261029-124510-af3898"; } && [ $rc -ne 0 ] && ok "a start for an issue a live run works is refused, and names the holder" || ko "a clashing start was not refused by name (rc=$rc): $o"
[ ! -e "$SG_ARGV" ] && ok "a refused clashing start launches nothing" || ko "the squad was started for a clashing issue"
printf '{"event": "verdict"}\n' >> "$H/record.jsonl"
o=$(cd "$T/co" && SG_STATE="$T/st2" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/req133.txt" 2>&1); rc=$?
[ $rc -eq 0 ] && ok "an issue whose holder has a verdict starts again" || ko "an ended holder still blocks the start (rc=$rc): $o"
# #1637: a workspace removed while the dispatch starts is a refusal, never a traceback. The run folder exists when launch.py
# finds it, then goes with the culled tree; writing launch.log into it raised an uncaught FileNotFoundError, and the crash left
# the issue claimed by a marker no pass frees. The wave cut is the long step, so a tree culled beside it refuses by name.
mkdir -p "$T/st3"
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
mkdir -p runs/run-20261109-105033-gone
sleep 1
rm -rf runs/run-20261109-105033-gone
sleep 5
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\nsleep 2\n' > "$T/s/wave.sh"
printf 'Coding model: test-model\nCHILD — GitHub issue(s) #9 (a): do it\n' > "$T/reqgone.txt"
o=$(cd "$T/co" && SG_STATE="$T/st3" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/reqgone.txt" 2>&1); rc=$?
{ [ $rc -ne 0 ] && echo "$o" | /usr/bin/grep -q "launch refused: the run folder" && echo "$o" | /usr/bin/grep -q "run-20261109-105033-gone"; } \
  && ok "a workspace removed under the dispatch refuses by name, with no traceback" || ko "the removed workspace did not refuse by name (rc=$rc): $o"
case "$o" in *Traceback*) ko "the removed workspace still raises a traceback: $o";; *) ok "the removed workspace raises no traceback";; esac
[ -z "$(cat "$T/st3/active-runs.txt" 2>/dev/null)" ] && ok "a refused dispatch adds no live run" || ko "a refused dispatch holds a live run"

# #2847: the launch lists the run before `wave.sh start` writes its wave markers. The janitor drops a wave marker no line
# names, so a pass between the cut and a later append dropped the wave of a live run. The stub cut records the listing it saw.
mkdir -p "$T/st4"
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
case " $* " in *" squad start "*) ;; *) exec sleep 1;; esac
mkdir -p runs/run-20261125-101010-order
sleep 3
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\n/usr/bin/grep "/$2$" "$SG_STATE/active-runs.txt" > "%s" 2>&1 || echo "no line" > "%s"\n' "$T/wave-saw" "$T/wave-saw" > "$T/s/wave.sh"
printf 'Coding model: test-model\nCHILD — GitHub issue(s) #11 (a): do it\n' > "$T/reqorder.txt"
rm -f "$T/wave-saw"
o=$(cd "$T/co" && SG_STATE="$T/st4" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 python3 -I "$T/s/launch.py" "$T/reqorder.txt" 2>&1); rc=$?
[ -f "$T/wave-saw" ] && ok "the launch cuts the wave of a child run" || ko "wave.sh start never ran (rc=$rc): $o"
/usr/bin/grep -q '/run-20261125-101010-order$' "$T/wave-saw" 2>/dev/null && ok "the run's line is in active-runs.txt when wave.sh start runs" || ko "wave.sh start ran before the run was listed: $(cat "$T/wave-saw" 2>/dev/null) out=$o"
[ "$(/usr/bin/grep -c '/run-20261125-101010-order$' "$T/st4/active-runs.txt" 2>/dev/null)" = 1 ] && ok "the launch lists the run once" || ko "the run is listed $(/usr/bin/grep -c '/run-20261125-101010-order$' "$T/st4/active-runs.txt" 2>/dev/null) times"

rm -rf "$T"; exit $fail
