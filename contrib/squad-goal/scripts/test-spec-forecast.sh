#!/bin/bash
# seconds: 0.53
# The spec forecast: host-free (no hub, no GitHub client), repeatable across processes under a fixed seed, and it names its gates.
# Fixtures and a temp dir only: never the network, never the live hub state.
# usage: bash test-spec-forecast.sh [spec_forecast.py]
here="$(cd "$(dirname "$0")" && pwd)"; py=python3; [ -n "${SG_CHECKOUT:-}" ] && [ -x "$SG_CHECKOUT/.venv/bin/python" ] && py="$SG_CHECKOUT/.venv/bin/python"; src=${1:-$here/spec_forecast.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/spec_forecast.py" 2>/dev/null || { ko "spec_forecast.py is missing"; rm -rf "$T"; exit 1; }

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests|socket' "$T/s/spec_forecast.py" \
  && ko "the module imports the hub, a GitHub client or the network" || ok "the module imports no hub, no GitHub client and no network"

"$py" -c "import sys; sys.path.insert(0, '$T/s'); import spec_forecast; assert 'hub' not in sys.modules" 2>/dev/null \
  && ok "the module loads with no hub" || ko "the module failed to load alone"

cat >"$T/run.py" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from datetime import date, timedelta
import spec_forecast as sf
as_of = date(2026, 11, 1)
days = [{"day": (as_of - timedelta(days=60 - i)).isoformat(), "active": True, "done": 1 + i % 3, "reserved": 0}
        for i in range(60)]
out = sf.forecast({"days": days, "coverage": 1.0}, [{"spec": 1018, "scope": 9}, {"spec": 1019, "scope": 14}], as_of)
print("p50", out[1018]["p50"], out[1019]["p50"], "p85", out[1018]["p85"], out[1019]["p85"])
print("badge", sf.lane_badge("2026-11-10", "2026-11-07"))
PY
"$py" "$T/run.py" "$T/s" >"$T/one.txt" 2>&1
"$py" "$T/run.py" "$T/s" >"$T/two.txt" 2>&1
grep -q '^p50 ' "$T/one.txt" && ok "a forecast is produced for each spec" || ko "no forecast: $(cat "$T/one.txt")"
cmp -s "$T/one.txt" "$T/two.txt" && ok "a fixed seed repeats across processes" || ko "the forecast differs between processes"
grep -q '^badge +3d$' "$T/one.txt" && ok "the lane badge reads calendar days later as plus" || ko "badge: $(cat "$T/one.txt")"

rm -rf "$T"; exit $fail
