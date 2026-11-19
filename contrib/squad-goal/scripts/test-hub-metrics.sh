#!/bin/bash
# seconds: 1.52
# Regression test for #705: the hub's delivery metrics are read-only and come from the loop's own local records -- the issue
# ledgers, the landing ledger, the auto/ markers and the run folders. The hub is loaded in a fresh process, in a fake live
# copy of no repository, with the records handed in; nothing is started and nothing is written. usage: bash test-hub-metrics.sh
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/hub.py}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep

mkdir -p "$T/st/state" "$T/st/auto" "$T/co/runs/run-a" "$T/co/runs/run-b" "$T/hub"
# The records, at the shape their writers keep: two issues landed, one refused and held, one still in its wave, one whose
# landing stuck; the landing ledger; the durable waved-<run> markers; and one fix pass that left the same findings twice.
python3 - "$T" <<'PY'
import json, pathlib, sys
T = pathlib.Path(sys.argv[1])
def led(key, own=(), wave=None, landed=None):
    return {"key": key, "own": {"count": len(own), "runs": [{"run": r, "key": "e", "why": "w"} for r in own], "stopped": None},
            "loop": {"count": 0, "runs": [], "stopped": None}, "wave": wave or {}, "landed": landed or {}, "requeued": {}}
recs = {
    "acme__repo__101.json": led("acme/repo#101", wave={"run-1": {"state": "delivered"}}, landed={"run-1": {"kind": "run", "sha": "a"}}),
    "acme__repo__102.json": led("acme/repo#102", own=["run-2"], wave={"run-2": {"state": "refused"}, "run-0": {"state": "held"}}),
    "acme__repo__103.json": led("acme/repo#103", wave={"run-3": {"state": "entered"}}),
    "acme__repo__105.json": led("acme/repo#105", wave={"run-5": {"state": "stuck", "tries": 3}}),
}
for n, r in recs.items(): (T / "st/state" / n).write_text(json.dumps(r))
(T / "st/landed.tsv").write_text("1700000000\twave/run-1\ta\t101\n1700000200\twave/run-2\tb\t102\n")
for run in ("run-1", "run-4", "run-5"):
    (T / "st/auto" / f"waved-{run}").write_text(f"wave/{run}\n")
(T / "st/auto/wave-run-6").write_text("wave/run-6\n"); (T / "st/auto/waved-run-6").write_text("wave/run-6\n")
(T / "st/auto/branchfix-same-gui_7-x").write_text("2\n")
(T / "st/auto/runfix-same-run-x_child").write_text("0\n")
SESSION = "You've hit your session limit (exit code: 1)"
for run in ("run-a", "run-b"):
    ev = [{"event": "outcome", "node": "r", "entry": 1, "outcome": "fail", "reasons": ["adapter_error"], "spend": 0, "turns": 0,
           "cap": 1, "elapsed": 1, "pos": 1, "model": "claude-gpt-5.6-terra", "diagnostic": {"error": SESSION}},
          {"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["adapter_error"], "remains": [], "folded": [1], "pos": 2}]
    (T / "co/runs" / run / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
PY
before=$(cd "$T" && /usr/bin/find st co/runs -type f -exec /usr/bin/shasum {} \; | sort)

# panel: one JSON line, exactly what the hub's read-only route returns, computed over the records handed in.
out=$(env -u SG_STATE SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_HUB_DIR="$T/hub" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" \
  python3 - "$src" "$T/st" "$T/co/runs" <<'PY' 2>/dev/null
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("hub_metrics_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.project_dirs = lambda: [pathlib.Path(sys.argv[3])]   # the run roots the fixture hands in; the hub reads no live directory
m.STATE = pathlib.Path(sys.argv[2])
print(json.dumps(m.delivery()))
PY
)
[ -n "$out" ] && ok "the hub answers one JSON line" || ko "the hub printed nothing"
printf '%s' "$out" | $G -q '"attempts": 5, "landed": 1, "landed_nothing": 3, "open": 1' && ok "the attempts split into landed, landed nothing and open" || ko "dispatched: $out"
printf '%s' "$out" | $G -q '"by_state": {"refused": 1, "held": 1, "stuck": 1}' && ok "each failed attempt keeps its state" || ko "by_state: $out"
printf '%s' "$out" | $G -q '"noop_waves": {"count": 1, "runs": \["run-4"\]}' && ok "a wave that landed empty counts apart from a failed dispatch" || ko "noop_waves: $out"
printf '%s' "$out" | $G -q '"issue": "acme/repo#102", "attempts": 2, "landed": 0, "landed_nothing": 2' && ok "unique attempts per issue count each run once" || ko "per_issue: $out"
printf '%s' "$out" | $G -q '"model": "gpt-5.6-terra", "backend": "gateway", "cause": "quota", "count": 2' && ok "adapter failures are counted by class" || ko "adapter: $out"
printf '%s' "$out" | $G -q '"repeated": 2' && ok "repeated review findings are counted from the fix markers" || ko "review: $out"
printf '%s' "$out" | $G -q '"landings": {"count": 2, "branches": 2, "issues": 2}' && ok "landings come from the landing ledger" || ko "landings: $out"
printf '%s' "$out" | $G -q '"err": null' && ok "a scan that read every record reports no failure" || ko "err: $out"
after=$(cd "$T" && /usr/bin/find st co/runs -type f -exec /usr/bin/shasum {} \; | sort)
[ "$before" = "$after" ] && ok "the metrics read their records and write nothing" || ko "the fixture changed: $(printf '%s\n' "$before" "$after" | sort | uniq -u | tr '\n' ' ')"

$G -q 'elif self.path.startswith("/api/delivery")' "$src" && ok "the hub serves the metrics on one GET route" || ko "no /api/delivery route"
# The owner hid the panel from the accordion: the route stays, the page neither shows it nor polls it (tests/test_delivery_metrics.py).
$G -q 'async function pollDelivery' "$src" && ko "the page polls the delivery route" || ok "the page polls no delivery route"
$G -q '<details id=dlv class=sp>' "$src" && ko "the page carries the hidden delivery panel" || ok "the page carries no delivery panel"
post=$(sed -n '/def do_POST/,$p' "$src")
printf '%s' "$post" | $G -q '/api/delivery' && ko "a write route names the metrics" || ok "no write route names the metrics"
$G -q 'def do_PUT\|def do_DELETE' "$src" && ko "the hub carries a write verb" || ok "the hub carries no other write verb"

[ "$fail" = 0 ] && echo "PASS" || echo "FAIL"
exit "$fail"
