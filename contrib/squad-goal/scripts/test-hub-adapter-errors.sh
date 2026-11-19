#!/bin/bash
# seconds: 0.14
# Regression test for #698: the hub counts the adapter_error run verdicts by route and cause, and a corrupt record
# counts its valid prefix instead of ending the scan. The hub is loaded in a fresh process, in a fake live copy of no
# repository, with the run folders handed in; the classifier comes from the squad package the profile names. No network,
# no server. usage: bash test-hub-adapter-errors.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/hub.py}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep

mkdir -p "$T/runs/run-a" "$T/runs/run-b" "$T/runs/run-c" "$T/co" "$T/hub"
SESSION_LIMIT="ResultError: Claude Code returned an error result: You've hit your session limit (exit code: 1)"
NO_ACCESS="ResultError: Claude Code returned an error result: It may not exist or you may not have access (exit code: 1)"
# run-a: one adapter_error verdict on a gateway spelling. run-b: the same on a cli spelling, then a truncated line: its
# valid prefix (the outcome and the verdict) is what the scan counts, and the line after it never reaches the scan.
printf '{"event": "outcome", "node": "router", "entry": 1, "outcome": "fail", "reasons": ["adapter_error"], "spend": 0, "turns": 0, "cap": 1, "elapsed": 1, "pos": 1, "model": "claude-gpt-5.6-terra", "diagnostic": {"error": "%s"}}\n{"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["adapter_error"], "remains": [], "folded": [1], "pos": 2}\n' "$SESSION_LIMIT" > "$T/runs/run-a/record.jsonl"
printf '{"event": "outcome", "node": "router", "entry": 1, "outcome": "fail", "reasons": ["adapter_error"], "spend": 0, "turns": 0, "cap": 1, "elapsed": 1, "pos": 1, "model": "gemini-3.8-flash-high", "diagnostic": {"error": "%s"}}\n{"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["adapter_error"], "remains": [], "folded": [1], "pos": 2}\n{"event": "outcome", "trunc\n' "$NO_ACCESS" > "$T/runs/run-b/record.jsonl"
printf '{"event": "verdict", "verdict": "completed", "exit_code": 0, "reasons": [], "remains": [], "folded": [], "pos": 1}\n' > "$T/runs/run-c/record.jsonl"

# panel <runs dir>: one JSON line, exactly what the hub's read-only route returns.
out=$(env -u SG_STATE SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_HUB_DIR="$T/hub" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" \
  python3 - "$src" "$T/runs" <<'PY' 2>/dev/null
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("hub_adapter_errors_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.project_dirs = lambda: [pathlib.Path(sys.argv[2])]   # the run roots the fixture hands in; the hub reads no live directory
print(json.dumps(m.adapter_errors()))
PY
)
[ -n "$out" ] && ok "the hub answers one JSON line" || ko "the hub printed nothing"
printf '%s' "$out" | $G -q '"model": "gpt-5.6-terra", "backend": "gateway", "cause": "quota", "count": 1' && ok "a gateway spelling is counted under its registry id and route" || ko "gateway row: $out"
printf '%s' "$out" | $G -q '"model": "gemini-3.8-flash", "backend": "cli", "cause": "auth", "count": 1' && ok "a cli spelling is counted under its own route" || ko "cli row: $out"
printf '%s' "$out" | $G -q '"runs": 3' && ok "every run folder that exists is scanned" || ko "runs count: $out"
printf '%s' "$out" | $G -q '"err": null' && ok "a scan that classified every folder reports no failure" || ko "err: $out"
printf '%s' "$out" | $G -q 'completed' && ko "a run whose verdict is not adapter_error is not counted" || ok "a run whose verdict is not adapter_error is not counted"

[ "$fail" = 0 ] && echo "PASS" || echo "FAIL"
exit "$fail"
