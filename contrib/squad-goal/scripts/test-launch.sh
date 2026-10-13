#!/bin/bash
# Regression test: launch.py writes the squad's stdout/stderr into the run folder as launch.log, and keeps the copy
# live until the squad exits, because that is when a refused start writes its reason (Refs #1503). Offline: stub
# caffeinate/uv so squad "starts" a folder, refuses after the folder exists, and exits.
# usage: bash test-launch.sh [launch.py]
src=${1:-$(dirname "$0")/launch.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st" "$T/co/runs" "$T/squad"
cp "$src" "$T/s/launch.py"
# models.json: one coding model, no hours window. SG_ALLOW_MODEL skips the gate anyway.
printf '{"coding_models":["test-model"],"hours":{}}\n' > "$T/s/models.json"
printf 'print("hub")\n' > "$T/s/hub.py"
# caffeinate: create a run folder in cwd/runs, then refuse 5 s later (a start that dies long after its folder shows up
# is the observed shape: at folder time the log is still empty, so only a copy that outlives launch.py captures it)
cat > "$T/bin/caffeinate" <<'C'
#!/bin/bash
while [ "${1#-}" != "$1" ]; do shift; done
mkdir -p runs/run-20261011-000000-dead
sleep 5
echo "squad refused: vendor/skills is not clean"
exit 0
C
chmod +x "$T/bin/caffeinate"
printf '#!/bin/bash\necho uv-not-used\n' > "$T/bin/uv"; chmod +x "$T/bin/uv"
export PATH="$T/bin:$PATH"
printf 'IMPORTANT: your first and only action is X\nCoding model: test-model\n--- issue #9\n' > "$T/req.txt"
o=$(cd "$T/co" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$T/squad" SG_ALLOW_MODEL=1 \
  python3 -I "$T/s/launch.py" "$T/req.txt" 2>&1) || true
log="$T/co/runs/run-20261011-000000-dead/launch.log"
[ -f "$log" ] && ok "launch writes launch.log in the run folder" || ko "no run launch.log; out=$o files=$(ls -la "$T/co/runs/run-20261011-000000-dead" 2>/dev/null)"
for _ in $(seq 1 20); do /usr/bin/grep -q 'vendor/skills is not clean' "$log" 2>/dev/null && break; sleep 1; done
/usr/bin/grep -q 'vendor/skills is not clean' "$log" 2>/dev/null && ok "launch.log captures what the squad wrote after the folder appeared" || ko "launch.log contents: $(cat "$log" 2>/dev/null) out=$o"
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
rm -rf "$T"; exit $fail
