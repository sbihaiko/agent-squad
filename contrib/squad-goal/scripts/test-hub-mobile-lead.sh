#!/bin/bash
# seconds: 0.18
# Guard for #1333 (SPEC 1327): the phone's lead-time screen draws the payload /api/chart/lead serves and computes none of
# its numbers. The tooltip of a tapped mark reads a lone card's weight and date from the served dot, and a cluster's lead
# and closed ranges from the cluster's own served objects — never a range the view recomputes (ADR-0137). The legend and
# the three reference values sit under the plot. Zoom and reset are explicit buttons that read the desktop's
# /api/leadtime, the span they name is the `lead.start` the payload serves, and a reset drops the answer still in flight.
# The cluster here is composed by board_leadtime.control_chart, the module the route reads, so the shape the assertions
# name is the served one. The module is run by node with no DOM and no server; the page checks are static markup.
# usage: bash test-hub-mobile-lead.sh [path/to/hub-mobile.html]
# mirrors: hub-mobile-chart.js hub-mobile.html
d=$(cd "$(dirname "$0")" && pwd); f=${1:-$d/hub-mobile.html}
export MLT_JS=$d/hub-mobile-chart.js LT_SRC=$d/board_leadtime.py
export HUB_PAGE=$f T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the lead chart's drawing is not proved here"; exit 0; fi
python3 - <<'PY'
import importlib.util, json, os, pathlib, re, subprocess, sys
fail = 0
def check(label, ok, got=""):
    global fail
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": got {got}"))
    fail |= not ok

edge = pathlib.Path(os.environ["HUB_PAGE"]).read_text()
chart_js = pathlib.Path(os.environ["MLT_JS"]).read_text()
spec = importlib.util.spec_from_file_location("lt", os.environ["LT_SRC"])
lt = importlib.util.module_from_spec(spec); spec.loader.exec_module(lt)

# One cluster of two cards the payload's own screen-space rule merges, composed by the module the route reads.
def card(n, closed, hours):
    return {"n": n, "t": f"card {n}", "o": closed, "c": closed, "column": "Todo", "hours": float(hours),
            "flowing": float(hours), "held": 0.0, "approx": False, "unknown_first": False}
from datetime import UTC, datetime, timedelta
now = datetime(2026, 11, 4, tzinfo=UTC); closed = now - timedelta(days=2)
chart = lt.control_chart([card(2, closed.isoformat(), 1), card(3, closed.isoformat(), 1)],
                         timeframe=30, now=now, plot={"width": 672, "height": 212})
cluster = next(k for k in chart["clusters"] if k["count"] == 2)

check("a served cluster carries the lead and closed ranges the pin reads",
      {"min", "max", "text"} <= set(cluster["lead"]) and {"start", "end", "text"} <= set(cluster["closed"]), cluster)
check("a served dot carries the number, title, duration and date the pin reads",
      {"n", "title", "text", "closed"} <= set(cluster["dots"][0]), cluster["dots"][0])

path = pathlib.Path(os.environ["T"]) / "pin.js"
path.write_text(chart_js + f"\nprocess.stdout.write(JSON.stringify({{many: mltPin({json.dumps(cluster)}),"
                f"lone: mltPin({json.dumps({**cluster, 'count': 1, 'r': lt.DOT_RADIUS_PX, 'dots': cluster['dots'][:1]})}),"
                f"zoomOut: mltZoom({{start: '2026-10-01T00:00:00+00:00', end: '2026-10-03T00:00:00+00:00'}}, 'out'),"
                f"reset: mltZoom({{start: '2026-10-01T00:00:00+00:00', end: '2026-10-03T00:00:00+00:00'}}, 'reset')}}));")
r = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
check("the chart module runs under node alone", r.returncode == 0, r.stderr)
out = json.loads(r.stdout) if r.returncode == 0 else {}
check("a tapped cluster pins its cards and the ranges its own objects serve",
      out.get("many") == f"2 issues: #2, #3 · lead {cluster['lead']['text']} · closed {cluster['closed']['text']} · Todo 1h · Todo 1h", out.get("many"))
lone = out.get("lone") or ""
check("a tapped lone card pins the duration and date of its one served dot",
      lone.startswith("#2 card 2 · lead 1h · closed ") and "Todo 1h" in lone, lone)
check("zoom out doubles the window around the same middle",
      out.get("zoomOut") == {"from": "2026-09-30T00:00:00.000Z", "to": "2026-10-04T00:00:00.000Z"}, out.get("zoomOut"))
check("reset reads the payload's own window again", out.get("reset") is None, out.get("reset"))

screen = re.search(r'<article data-screen="lead"([^>]*)>(.*?)</article>', edge, re.S)
check("the More tab has a lead-time screen", screen is not None)
head_, body = (screen.group(1), screen.group(2)) if screen else ("", "")
check("the screen reads the desktop's /api/chart/lead, with no second lead route",
      'data-src="/api/chart/lead"' in head_ and edge.count('data-src="/api/chart/lead"') == 1, head_)
check("the legend and the reference values sit under the plot", body.index('id="ltplot"') < body.index('id="ltlegend"'))
check("the phone keeps no right gutter", "lttg" not in body)
check("zoom and reset are explicit buttons", all(f'data-ltzoom="{a}"' in body for a in ("in", "out", "reset")))
check("the phone keeps no drag-to-zoom", "onpointerdown" not in edge)
check("the zoom reads the desktop's /api/leadtime and names the served span",
      re.search(r'const q = "/api/leadtime\?from=" \+ encodeURIComponent\(z\.from\) \+ "&to=" \+ encodeURIComponent\(z\.to\) \+'
                r'\s+"&a=" \+ encodeURIComponent\(MLT\.start', edge) is not None)
check("the span the zoom names is the field the payload serves",
      re.search(r"MLT\.start = L\.start", edge) is not None and '"start": LEAD_START' in "".join(q.read_text() for q in sorted(pathlib.Path(os.environ["LT_SRC"]).parent.glob("hub*.py"))))
check("every view action takes the next sequence number",
      re.search(r"if \(dir === \"reset\"\) \{ MLT\.zr = MLT\.zc = null;[^\n]*\n", edge) is not None
      and "const el = leadEl(), msg = el.querySelector(\".msg\"), seq = ++MLT.seq;" in edge)
check("an answer numbered older than the last action never draws",
      re.search(r"if \(seq !== MLT\.seq\) return;[^\n]*\n[^\n]*if \(d\.error\)", edge) is not None)
check("the timeframe switch takes the sequence number too",
      edge.count("++MLT.seq") >= 2 and "data-lscale" not in edge)
check("the legend names the two mark paints and the three reference swatches",
      all(s in chart_js for s in ("mlthollow", "mltfilled", "mltsolid", "mltdash", "mltdot")))
sys.exit(fail)
PY
rc=$?
[ "$rc" = 0 ] && echo "hub mobile lead: all ok" || echo "hub mobile lead: FAILED"
exit "$rc"
