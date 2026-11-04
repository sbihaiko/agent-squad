"""The phone's lead-time chart (#1333, SPEC 1327): the mobile hub draws the payload the desktop page already reads.

The drawing is one client module, run under node alone with no DOM and no server. The page is composed from its files
the way the hub composes it: the chart screen fetches `/api/chart/lead`, the desktop page's own route, never a second
one. On a phone the legend and the reference values sit under the plot with no right gutter, a tap pins one mark's
tooltip, and zoom and reset are explicit buttons instead of a drag.

The chart fixtures are the arithmetic's own answer: `board_leadtime.control_chart` runs on rows, so every mark, count
and range a test pins is the shape the hub actually serves, never a hand-made approximation of it.
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import urllib.parse
from datetime import UTC, datetime

import pytest

import tests.test_hub_mobile_move as move
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PAGE = SCRIPTS / "hub-mobile.html"
HUB_PY = SCRIPTS / "hub.py"
CHART_JS = SCRIPTS / "hub-mobile-chart.js"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
BOARD_FILTER_JS = SCRIPTS / "board-filter.js"
LEADTIME_PY = SCRIPTS / "board_leadtime.py"
NODE = shutil.which("node")


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEADTIME = _module("board_leadtime_fixture", LEADTIME_PY)

START = datetime(2026, 10, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _row(n, opened, closed, hours, column, title):
    return {"n": n, "t": title, "o": opened, "c": closed, "hours": hours, "flowing": hours, "held": 0.0,
            "approx": False, "unknown_first": False, "column": column, "columns": {column: hours}, "gate": 0.0}


ROWS = [
    _row(1, "2026-10-01T00:00:00+00:00", "2026-10-01T02:00:00+00:00", 2.0, "Todo", "first card"),
    _row(2, "2026-10-02T00:00:00+00:00", "2026-10-02T01:00:00+00:00", 1.0, "Doing", "second card"),
    _row(3, "2026-10-02T00:10:00+00:00", "2026-10-02T01:10:00+00:00", 1.0, "Todo", "third card"),
]
# The payload one view of those rows carries: card 1 alone, cards 2 and 3 merged by the payload's own screen-space rule.
CHART = LEADTIME.control_chart(ROWS, now=NOW, start=START, end=NOW, plot={"width": 720, "height": 240})
LONE = next(k for k in CHART["clusters"] if k["count"] == 1)
MANY = next(k for k in CHART["clusters"] if k["count"] > 1)
ZOOM_IN = {"from": "2026-10-01T12:00:00.000Z", "to": "2026-10-02T12:00:00.000Z"}


def page():
    return (PAGE.read_text().replace("/*GESTURE*/", GESTURE_JS.read_text())
            .replace("/*BOARD_FILTER*/", BOARD_FILTER_JS.read_text())
            .replace("/*CHART*/", CHART_JS.read_text()))


def lead_screen():
    m = re.search(r'<article data-screen="lead"[^>]*>(.*?)</article>', page(), re.S)
    assert m, "the More tab has no lead-time screen"
    return m.group(0)


def run_js(expr):
    script = f"{CHART_JS.read_text()}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_phone_reads_the_desktop_payload_with_no_second_route():
    """#1333: the screen fetches /api/chart/lead, the route the desktop page draws from; no mobile-only lead route."""
    html = page()
    src = re.search(r'data-src="([^"]+)"', lead_screen()).group(1)
    assert src == "/api/chart/lead"
    desktop = HUB_PY.read_text()
    assert 'self.path.startswith("/api/chart/lead")' in desktop, "the hub serves no lead chart route"
    assert "/api/chart/lead" in desktop, "the desktop page lost its lead route"
    routes = {re.sub(r"\?.*", "", p) for p in re.findall(r'"(/api/[^"]*)"', html)}
    assert {r for r in routes if "lead" in r} <= {"/api/chart/lead", "/api/leadtime"}, routes
    assert '"lead": {"measure"' in desktop, "the served payload no longer carries the lead object"
    assert "d.lead" in html, "the phone draws no answer from the served payload"


def test_the_legend_and_the_reference_values_sit_under_the_plot():
    """#1333: the plot comes first, the legend and the three reference values follow it, with no right gutter."""
    screen = lead_screen()
    assert screen.index('id="ltplot"') < screen.index('id="ltlegend"'), "the legend is not under the plot"
    assert "lttg" not in screen, "the phone screen keeps the desktop's gutter tag"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_drawn_legend_names_who_is_who_and_each_reference_value():
    out = run_js(f"mltChart({json.dumps(CHART)})")
    assert "<svg" in out["svg"] and "data-lt" in out["svg"]
    assert "Card" in out["legend"] and "Cluster" in out["legend"] and "Rolling mean" in out["legend"]
    for name, value in (("Average", "2h"), ("Median", "1h 20m"), ("p85", "2h")):
        assert name in out["legend"] and value in out["legend"], (name, value)
    assert "mltsolid" in out["legend"] and "mltdash" in out["legend"] and "mltdot" in out["legend"]
    assert "lttg" not in out["svg"], "the phone chart draws the desktop gutter"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_marks_and_the_legend_match_the_desktop_fill():
    """The desktop draws a lone card hollow and a cluster filled with ink (hub.py:5718, board_leadtime.py:163); the two
    legend swatches carry the same two paints. A mark is read by the cluster the payload hands it."""
    out = run_js(f"mltChart({json.dumps(CHART)})")
    fills = re.findall(r'data-lt-k="(\d+)"[^>]*fill="([^"]+)"', out["svg"])
    assert len(fills) == len(CHART["clusters"]), out["svg"]
    by_key = {int(i): fill for i, fill in fills}
    assert by_key[CHART["clusters"].index(LONE)] == "var(--chart-bg)", "a lone card is the desktop's hollow dot"
    assert by_key[CHART["clusters"].index(MANY)] == "var(--chart-ink)", "a cluster is the desktop's filled dot"
    assert '<i class=mlthollow></i>Card' in out["legend"], out["legend"]
    assert '<i class=mltfilled></i>Cluster' in out["legend"], out["legend"]
    desktop = HUB_PY.read_text()
    assert 'fill="${m>1?"var(--chart-ink)":"var(--chart-bg)"}"' in desktop, "the desktop fill changed under the phone"
    assert LEADTIME.CLUSTER_RADIUS_PX == MANY["r"] and LEADTIME.DOT_RADIUS_PX == LONE["r"], (LONE["r"], MANY["r"])


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_pins_a_lone_card_from_its_cluster_dot():
    """A real cluster carries no `n`, `title`, `text` or string `closed` of its own: they live on its one dot."""
    pinned = run_js(f"mltPin({json.dumps(LONE)})")
    assert pinned == "#1 first card · lead 2h · closed 2026-10-01 · Todo 2h", pinned


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_lists_every_card_in_a_cluster():
    pinned = run_js(f"mltPin({json.dumps(MANY)})")
    assert pinned == "2 issues: #2, #3 · lead 1h · closed Oct 2 · Doing 1h · Todo 1h", pinned


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_page_pins_the_tapped_cluster_text(hub):  # noqa: F811
    """`leadPin` writes the cluster's own tooltip into the pin the phone shows, through the stub DOM."""
    out = move.run_page(hub.MOBILE_PAGE, f"const CLUSTER = {json.dumps(MANY)};" + """
      leadPin(CLUSTER);
      const pin = document.querySelector("#ltpin"), text = document.querySelector("#ltpintext");
      const shown = { hidden: pin.hidden, text: text.textContent };
      leadPin(null);
      shown.after = document.querySelector("#ltpin").hidden;
      return shown;
    """)
    assert out["hidden"] is False, out
    assert out["text"] == "2 issues: #2, #3 · lead 1h · closed Oct 2 · Doing 1h · Todo 1h", out
    assert out["after"] is True, "the dismiss hides the pin again"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_zoom_and_reset_are_explicit_buttons():
    rng = json.dumps(CHART["range"])
    zoom_in = run_js(f"mltZoom({rng}, 'in')")
    zoom_out = run_js(f"mltZoom({rng}, 'out')")
    assert zoom_in == ZOOM_IN
    assert zoom_out == {"from": "2026-09-30T00:00:00.000Z", "to": "2026-10-04T00:00:00.000Z"}
    assert run_js(f"mltZoom({rng}, 'reset')") is None
    screen = lead_screen()
    for action in ("in", "out", "reset"):
        assert re.search(rf'data-ltzoom="{action}"', screen), action
    assert "/api/leadtime" in page(), "the zoom does not read the desktop's zoom route"
    assert "onpointerdown" not in page(), "the phone keeps drag-to-zoom instead of explicit buttons"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_zoom_query_names_the_span_and_the_columns_the_payload_serves(hub):  # noqa: F811
    """The `a` the zoom names is `lead.start`, the field `/api/chart/lead` serves (hub.py:3873), set from the payload
    by the screen (hub.py serves LEAD_START, the column the desktop's own default span uses)."""
    out = move.run_page(hub.MOBILE_PAGE, f"const CHART = {json.dumps(CHART)};" + """
      const sent = [];
      call = (p, fresh) => { sent.push({ path: p, fresh: !!fresh }); return Promise.resolve(CHART); };
      const el = document.querySelector('[data-screen="lead"]');
      MORE.lead(el, { lead: { start: "Todo", measure: "Todo -> closed", window: { size: 5, unit: "issues" },
                             control: { log: { "2": CHART }, jira: { "2": CHART } } } });
      const start = MLT.start;
      await leadZoom("in");
      return { start, sent };
    """)
    assert out["start"] == "Todo", "the span comes from the payload's own `lead.start`"
    assert out["sent"] == [{
        "path": "/api/leadtime?from=2026-10-01T12%3A00%3A00.000Z&to=2026-10-02T12%3A00%3A00.000Z&scale=log&a=Todo",
        "fresh": True,
    }], out["sent"]
    assert '"start": LEAD_START' in HUB_PY.read_text(), "the payload no longer serves the span the zoom names"


def test_the_hub_accepts_the_zoom_query_the_phone_asks(hub, monkeypatch):  # noqa: F811
    """The exact query the screen sends passes `lead_zoom`'s own validation and names the measure it serves."""
    assert hub.LEAD_START in hub.ORDER, "the default span is not a board column the route accepts"
    query = ("from=2026-10-01T12%3A00%3A00.000Z&to=2026-10-02T12%3A00%3A00.000Z&scale=log&a="
             + urllib.parse.quote(hub.LEAD_START, safe=""))
    monkeypatch.setattr(hub, "lead_rows", lambda a=None, b=None, now=None: [])   # no board read: the route's own gate
    out = hub.lead_zoom(query)
    assert "error" not in out, out
    assert out["measure"] == f"{hub.LEAD_START} -> closed", out


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_stale_answer_never_draws_over_a_newer_view(hub):  # noqa: F811
    """Every zoom, timeframe and scale action takes the next sequence number; an answer numbered older is dropped."""
    out = move.run_page(hub.MOBILE_PAGE, f"const CHART = {json.dumps(CHART)};" + """
      const old = { ...CHART, tag: "old" }, mid = { ...CHART, tag: "mid" }, news = { ...CHART, tag: "new" };
      const held = [];
      call = p => new Promise(r => held.push(r));
      MLT.base = { log: { "2": CHART }, jira: { "2": CHART } };
      MLT.start = "Todo"; MLT.log = true; MLT.tf = "2";
      const first = leadZoom("in"), second = leadZoom("out");
      const seqs = [MLT.seq];
      held[1](news); await second; await __tick(5);
      const drawn = MLT.zc && MLT.zc.tag;
      held[0](mid); await first; await __tick(5);
      const after = MLT.zc && MLT.zc.tag;   // the older answer never draws over the newer
      const pending = leadZoom("in");
      const el = document.querySelector('[data-screen="lead"]');
      const week = el.querySelector('[data-ltf="7"]');
      el.listeners.click.forEach(fn => fn({ target: week }));
      const beforeStale = MLT.seq, tf = MLT.tf;
      held[2](old); await pending; await __tick(5);
      return { seqs, drawn, after, tf, seqAfter: beforeStale, stale: MLT.zc && MLT.zc.tag, zc: !!MLT.zc };
    """)
    assert out["drawn"] == "new", out
    assert out["after"] == "new", "a stale zoom answer drew over the newer one"
    assert out["tf"] == "7", out
    assert out["stale"] is None and out["zc"] is False, \
        "a timeframe switch did not outrank the zoom answer in flight: the stale chart drew"
    assert out["seqAfter"] > out["seqs"][0], out
