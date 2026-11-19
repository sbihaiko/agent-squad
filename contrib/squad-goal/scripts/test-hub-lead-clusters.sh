#!/bin/bash
# seconds: 0.09
# Regression test for #1331: the lead chart's payload clusters carry one fixed radius, their cards, their count, their
# closed-date range and their lead-time range; a card past p85 never merges; the page draws a lone card as a hollow dot
# and a cluster as one outlined filled dot, with no stacked bar, no count text, and a tooltip naming the mark, the lead
# time, the closed dates and the stage strip. #1355: the chart always draws the log axis, so no Scale control renders.
# usage: bash test-hub-lead-clusters.sh [path/to/hub.py]
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
                         block.split("const drawFlow")[0], 'LT.tf="all";',
                         f"process.stdout.write(ltChart({json.dumps(chart)},null));"])
    path = pathlib.Path(os.environ["T"]) / name
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout

def check(name, ok, got): print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got}"))

# seven cards at p85 (10h) and one outlier one step over it (10.2h), within DOT_PX on the plot frame
closed = now - timedelta(days=2)
spread = [card(n, closed, 10) for n in range(1, 8)]
chart = lt.control_chart([card(9, closed, 10.2)] + spread, timeframe=30, now=now)
counts = sorted(c["count"] for c in chart["clusters"])
check("the cards at p85 merge and the outlier that closes first stays lone", counts == [1, 7], counts)
late = lt.control_chart(spread + [card(9, closed, 10.2)], timeframe=30, now=now)
counts = sorted(c["count"] for c in late["clusters"])
check("the outlier that closes last stays lone too", counts == [1, 7], counts)
chart = late
cluster = next(c for c in chart["clusters"] if c["count"] == 7)
check("a cluster serves the one fixed radius", cluster["r"] == lt.CLUSTER_RADIUS_PX, cluster["r"])
check("a cluster serves its count and its cards", cluster["count"] == 7 and cluster["cards"] == [1, 2, 3, 4, 5, 6, 7],
      (cluster["count"], cluster["cards"]))
check("a cluster serves its date range and its lead-time range",
      cluster["closed"]["start"] == closed.date().isoformat() and cluster["lead"]["min"]["hours"] == 10.0
      and cluster["lead"]["max"]["hours"] == 10.0, (cluster["closed"], cluster["lead"]))
small = lt.control_chart([card(1, closed, 5), card(2, closed, 5.2)], timeframe=30, now=now,
                         plot={"width": 672, "height": 212})
tall = lt.control_chart([card(1, closed, 5), card(2, closed, 5.2)], timeframe=30, now=now,
                        plot={"width": 672, "height": 106})
check("the caller's plot size recomputes the clusters from the same rule",
      [c["count"] for c in small["clusters"]] == [1, 1] and [c["count"] for c in tall["clusters"]] == [2],
      ([c["count"] for c in small["clusters"]], [c["count"] for c in tall["clusters"]]))
# #1355: the one axis is log, so the fixed-radius rule holds in both directions on it: a nearby pair merges and a far
# pair splits. A dense pair that re-split on log, or a far pair that re-merged, fails the one check.
# #2311: the axis fits the plotted hours, and one fitted 4h..24h axis holds both pairs: the (5, 5.02) pair sits inside
# one dot, and the (5, 8) pair spans about 56 px of the 212 px frame, so this one view proves the rule both ways.
both = lt.control_chart([card(1, closed, 5), card(2, closed, 5.02), card(3, closed, 8)], timeframe=30, now=now,
                        plot={"width": 672, "height": 212})
counts = [c["count"] for c in both["clusters"]]
check("one fitted log axis merges the nearby pair and splits the far pair", counts == [2, 1], counts)

out = render(chart, "lt-dots.js")
circles = re.findall(r"<circle [^>]*>", out)
check("the plot draws one mark per cluster", len(circles) == 2, len(circles))
hollow = next(tag for tag in circles if 'fill="var(--chart-bg)"' in tag)
filled = next(tag for tag in circles if 'fill="var(--chart-ink)"' in tag)
check("a lone card draws a hollow dot", hollow is not None and 'stroke="var(--chart-ink)"' in hollow, circles)
check("a cluster draws one outlined filled dot", filled is not None and 'stroke="var(--chart-ink)"' in filled, circles)
check("the plot draws no stacked bar and no count text", "<rect" not in out and not re.search(r"<text[^>]*>\s*\d+\s*</text>", out), out[:200])
check("no Scale control renders", "id=lts" not in out and ">Scale <" not in out, out[:300])   # #1355: the chart is always log
check("the y axis states the log scale", ">Lead time (log)</text>" in out, out[:300])
title = re.search(r"<circle[^>]*><title>([^<]*)</title>", out).group(1)
check("the tooltip names the cluster, its cards, the lead range and the closed date",
      title.startswith("7 issues: #1, #2, #3, #4, #5 +2 more · lead 10h · closed ") and "Done" in title, title)
# #1354: every exclusion names its reason beside its count, and a spec measured from its board entry says so, in the
# head line and in its dot's tooltip.
def render_lead(chart, name, excluded=()):
    lead = {"type": "spec", "measure": chart["measure"], "window": {"size": 20, "unit": "issues"},
            "excluded": [{"reason": r, "count": n} for r, n in excluded],
            "excluded_cards": {r: [9] for r, _ in excluded}, "excluded_text": lt.excluded_text(dict(excluded)),
            "excluded_reasons": lt.exclusion_words(dict(excluded)),
            "excluded_total": sum(n for _, n in excluded),
            "control": {"all": chart}, "columns": []}
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};",
                         block.split("const drawFlow")[0], 'LT.tf="all";',
                         f"process.stdout.write(flowLead({json.dumps(lead)}));"])
    path = pathlib.Path(os.environ["T"]) / name
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout

named = lt.control_chart([card(1, closed, 5), {"n": 9, "column": "Backlog", "c": closed.isoformat(), "o": None,
                                               "hours": 0.0, "reason": lt.NO_START}], timeframe=30, now=now)
check("an exclusion states its reason beside its count",
      named["excluded_text"] == "1 never entered the start column", named["excluded_text"])
named_out = render_lead(named, "lt-exclusion.js", excluded=[(lt.NO_START, 1)])
check("the page head states an exclusion's reason", "excluded 1: 1 never entered the start column" in named_out,
      named_out[:300])
fallback_row = card(3, closed, 12)
fallback_row.update(fallback=True, columns={"Backlog": 12.0})
fallback = lt.control_chart([fallback_row], timeframe=30, now=now)
check("a card's fallback measure is named in the chart text",
      fallback["fallback"]["count"] == 1 and "1 card measured from its first time on the board" in fallback["text"],
      fallback["text"])
fallback_out = render_lead(fallback, "lt-fallback.js")
check("the page head states the fallback measure", "1 card measured from its first time on the board" in fallback_out,
      fallback_out[:300])
titles = re.findall(r"<circle[^>]*><title>([^<]*)</title>", fallback_out)
check("the dot's tooltip states the fallback measure",
      bool(titles) and "measured from its first time on the board" in titles[0], titles)

hub_spec = importlib.util.spec_from_file_location("hub_lead", os.environ["HUB_SRC"])
hub = importlib.util.module_from_spec(hub_spec); hub_spec.loader.exec_module(hub)
check("a resize names the rendered plot size, and a bad size falls back to the default frame",
      hub._lead_plot({"pw": ["500"], "ph": ["106"]}) == {"width": 500, "height": 106}
      and hub._lead_plot({"pw": ["x"], "ph": ["106"]}) == hub._lead_plot(),
      (hub._lead_plot({"pw": ["500"], "ph": ["106"]}), hub._lead_plot()))
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
