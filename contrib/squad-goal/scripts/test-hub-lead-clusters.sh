#!/bin/bash
# Regression test for #1331: the lead chart's payload clusters carry one fixed radius, their cards, their count, their
# closed-date range and their lead-time range; a card past p85 never merges; the page draws a lone card as a hollow dot
# and a cluster as one outlined filled dot, with no stacked bar, no count text, and a tooltip naming the mark, the lead
# time, the closed dates and the stage strip. usage: bash test-hub-lead-clusters.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py
dir=$(cd "$(dirname "$0")" && pwd); export HUB_SRC=$src LT_SRC=$dir/board_leadtime.py; T=$(mktemp -d); fail=0
export T
out=$(python3 - <<'PY' 2>&1
import importlib.util, json, os, pathlib, re, subprocess
from datetime import UTC, datetime, timedelta
now = datetime(2026, 11, 4, tzinfo=UTC)
spec = importlib.util.spec_from_file_location("board_leadtime", os.environ["LT_SRC"])
lt = importlib.util.module_from_spec(spec); spec.loader.exec_module(lt)
page = pathlib.Path(os.environ["HUB_SRC"]).read_text()
block = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", page, re.S).group(1)

def card(n, closed, hours, columns=None):
    row = {"n": n, "t": f"card {n}", "o": (closed - timedelta(hours=hours)).isoformat(),
           "c": closed.isoformat(), "column": "Done", "hours": float(hours), "flowing": float(hours),
           "held": 0.0, "approx": False, "unknown_first": False}
    if columns: row["columns"] = columns
    return row

def render(chart, name):
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};",
                         block.split("const drawFlow")[0], 'LT.tf="all";LT.log=false;',
                         f"process.stdout.write(ltChart({json.dumps(chart)},null));"])
    path = pathlib.Path(os.environ["T"]) / name
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout

def check(name, ok, got): print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got}"))

# three cards closed together, one far outlier past p85
closed = now - timedelta(days=2)
chart = lt.control_chart([card(1, closed, 5), card(2, closed, 5.2), card(3, closed, 5.1),
                          card(9, closed, 300)], timeframe=30, now=now)
counts = sorted(c["count"] for c in chart["clusters"])
check("three close cards merge and the p85 outlier stays lone", counts == [1, 3], counts)
cluster = next(c for c in chart["clusters"] if c["count"] == 3)
check("a cluster serves the one fixed radius", cluster["r"] == lt.CLUSTER_RADIUS_PX, cluster["r"])
check("a cluster serves its count and its cards", cluster["count"] == 3 and cluster["cards"] == [1, 2, 3],
      (cluster["count"], cluster["cards"]))
check("a cluster serves its date range and its lead-time range",
      cluster["closed"]["start"] == closed.date().isoformat() and cluster["lead"]["min"]["hours"] == 5.0
      and cluster["lead"]["max"]["hours"] == 5.2, (cluster["closed"], cluster["lead"]))
small = lt.control_chart([card(1, closed, 5), card(2, closed, 5.4)], timeframe=30, now=now,
                         plot={"width": 672, "height": 212})
tall = lt.control_chart([card(1, closed, 5), card(2, closed, 5.4)], timeframe=30, now=now,
                        plot={"width": 672, "height": 106})
check("the caller's plot size recomputes the clusters from the same rule",
      [c["count"] for c in small["clusters"]] == [1, 1] and [c["count"] for c in tall["clusters"]] == [2],
      ([c["count"] for c in small["clusters"]], [c["count"] for c in tall["clusters"]]))

out = render(chart, "lt-dots.js")
circles = re.findall(r"<circle [^>]*>", out)
check("the plot draws one mark per cluster", len(circles) == 2, len(circles))
hollow = next(tag for tag in circles if 'fill="var(--chart-bg)"' in tag)
filled = next(tag for tag in circles if 'fill="var(--chart-ink)"' in tag)
check("a lone card draws a hollow dot", hollow is not None and 'stroke="var(--chart-ink)"' in hollow, circles)
check("a cluster draws one outlined filled dot", filled is not None and 'stroke="var(--chart-ink)"' in filled, circles)
check("the plot draws no stacked bar and no count text", "<rect" not in out and not re.search(r"<text[^>]*>\s*\d+\s*</text>", out), out[:200])
title = re.search(r"<circle[^>]*><title>([^<]*)</title>", out).group(1)
check("the tooltip names the cluster, the lead range and the closed date",
      title.startswith("3 issues · lead 5h to 5h 12m · closed ") and "Done" in title, title)
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
