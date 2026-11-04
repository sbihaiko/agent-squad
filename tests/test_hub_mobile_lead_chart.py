"""The phone's lead-time chart (#1333, SPEC 1327): the mobile hub draws the payload the desktop page already reads.

The drawing is one client module, run under node alone with no DOM and no server. The page is composed from its files
the way the hub composes it: the chart screen fetches `/api/chart/lead`, the desktop page's own route, never a second
one. On a phone the legend and the reference values sit under the plot with no right gutter, a tap pins one mark's
tooltip, and zoom and reset are explicit buttons instead of a drag.

Every fixture is the shape the hub serves, never a hand-made approximation of it. The pure drawing reads
`board_leadtime.control_chart`'s own output; the page tests read the answer `/api/chart/lead` composes
(`hub.flow_lead` over a seeded history store), so the payload carries `lead.start`, the control charts and the clusters
the route hands the desktop page, and the zoom URL the screen builds is fed back to `hub.lead_zoom` unchanged.
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import time
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
ZOOM_OUT = {"from": "2026-09-30T00:00:00.000Z", "to": "2026-10-04T00:00:00.000Z"}

# The three closed cards the route's payload is composed over: card 1 takes two hours from Todo to closed, cards 2 and 3
# one hour each, closed ten minutes apart, so the hub's screen-space rule merges them into one cluster.
CARDS = {
    1: ("2026-10-01T00:00:00Z", "2026-10-01T02:00:00Z"),
    2: ("2026-10-02T00:00:00Z", "2026-10-02T01:00:00Z"),
    3: ("2026-10-02T00:10:00Z", "2026-10-02T01:10:00Z"),
}


def _card(n, opened, closed):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED", "c": closed, "st": "Closed", "k": None, "l": [],
            "si": {"total": None, "completed": None}, "o": opened}


def _life(opened, closed):
    return [{"t": opened, "type": "added"}, {"t": opened, "type": "status", "status": "Todo"},
            {"t": closed, "type": "status", "status": "Closed"}, {"t": closed, "type": "closed"}]


def served_lead(hub):  # noqa: F811
    """The `/api/chart/lead` answer the route returns: `flow_lead` composes `_lead_payload` over the board read, and
    `control_chart` draws it, so a test reads the route's own output and invents no key of it."""
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    items = []
    for n, (opened, closed) in CARDS.items():
        item = _card(n, opened, closed)
        items.append(item)
        hub._hist.record_issue(path, n, opened, opened, _life(opened, closed), True, now=time.time(),
                               keys=hub.bar_keys(item))
    hub.board_cache["items"] = items
    payload = hub.flow_lead(*hub.board_filters(""))
    assert payload["lead"]["control"]["log"]["all"]["clusters"], "the served payload carries no cluster for the cards"
    return payload


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
    legend swatches carry the same two paints. Both the drawing and its legend are this page's own markup: the chart
    module and its rows and legend are added by this work (the module does not exist on dev)."""
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


def test_a_cluster_serves_the_ranges_the_pin_reads(hub):  # noqa: F811
    """The pin takes a cluster's lead and closed ranges from the payload (board_leadtime.py:250-257 serves them): both
    shapes carry `{min, max, text}` and `{start, end, text}`, so no range is recomputed in the view (ADR-0137)."""
    clusters = served_lead(hub)["lead"]["control"]["log"]["all"]["clusters"]
    lone = next(k for k in clusters if k["count"] == 1)
    many = next(k for k in clusters if k["count"] > 1)
    for k in (lone, many):
        assert {"min", "max", "text"} <= set(k["lead"]), k
        assert {"start", "end", "text"} <= set(k["closed"]), k
        assert k["count"] == len(k["dots"]), k


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
def test_a_tap_on_a_served_cluster_pins_its_text(hub):  # noqa: F811
    """`loadMore` hands the screen the route's own answer; a tap on one of its clusters pins that cluster's text."""
    payload = served_lead(hub)
    index = next(i for i, k in enumerate(payload["lead"]["control"]["log"]["all"]["clusters"]) if k["count"] > 1)
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + f"""
      call = p => Promise.resolve(PAYLOAD);   // the screen's own fetch of /api/chart/lead
      moreScreen = "lead"; await loadMore();
      const el = document.querySelector('[data-screen="lead"]');
      const all = el.querySelector('#ltbars [data-ltf="all"]');
      el.listeners.click.forEach(fn => fn({{ target: all }}));   // the served 'all' view holds the seeded clusters
      const tap = {{ target: {{ closest: sel => sel === "circle[data-lt-k]" ? {{ dataset: {{ ltK: "{index}" }} }} : null }} }};
      el.querySelector("#ltplot").listeners.click.forEach(fn => fn(tap));
      const pin = el.querySelector("#ltpin"), text = el.querySelector("#ltpintext");
      const shown = {{ hidden: pin.hidden, text: text.textContent }};
      leadPin(null);
      shown.after = el.querySelector("#ltpin").hidden;
      return shown;
    """)
    assert out["hidden"] is False, out
    assert out["text"] == "2 issues: #2, #3 · lead 1h · closed Oct 2 · Todo 1h · Todo 1h", out
    assert out["after"] is True, "the dismiss hides the pin again"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_zoom_and_reset_are_explicit_buttons():
    rng = json.dumps(CHART["range"])
    zoom_in = run_js(f"mltZoom({rng}, 'in')")
    zoom_out = run_js(f"mltZoom({rng}, 'out')")
    assert zoom_in == ZOOM_IN
    assert zoom_out == ZOOM_OUT
    assert run_js(f"mltZoom({rng}, 'reset')") is None
    screen = lead_screen()
    for action in ("in", "out", "reset"):
        assert re.search(rf'data-ltzoom="{action}"', screen), action
    assert "/api/leadtime" in page(), "the zoom does not read the desktop's zoom route"
    assert "onpointerdown" not in page(), "the phone keeps drag-to-zoom instead of explicit buttons"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_zoom_url_the_page_builds_is_the_route_it_reads(hub):  # noqa: F811
    """The screen names the span `/api/chart/lead` serves (`lead.start`) and doubles the payload's own range; the URL it
    builds is the one `hub.lead_zoom` accepts, and the answer names the same measure over the same cards."""
    payload = served_lead(hub)
    lead = payload["lead"]
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + """
      const sent = [];
      call = (p, fresh) => { sent.push({ path: p, fresh: !!fresh });
                             return Promise.resolve(p.startsWith("/api/chart/lead") ? PAYLOAD : PAYLOAD.lead.control.log.all); };
      moreScreen = "lead"; await loadMore();
      const el = document.querySelector('[data-screen="lead"]');
      el.listeners.click.forEach(fn => fn({ target: el.querySelector('#ltbars [data-ltf="all"]') }));
      const start = MLT.start;
      await leadZoom("out");
      return { start, sent };
    """)
    assert out["sent"][0]["path"] == "/api/chart/lead", out
    assert out["start"] == lead["start"] == hub.LEAD_START, "the span is the field the payload serves"
    url = out["sent"][1]["path"]
    assert out["sent"][1]["fresh"] is True, "a zoom reads the hub, never the cache"
    query = urllib.parse.urlsplit(url).query
    asked = urllib.parse.parse_qs(query)
    rng = lead["control"]["log"]["all"]["range"]
    start, end = datetime.fromisoformat(rng["start"]), datetime.fromisoformat(rng["end"])
    half = (end - start) / 2
    def moment(key):   # the page's Date#toISOString: UTC, milliseconds, Z
        return datetime.fromisoformat(asked[key][0].replace("Z", "+00:00"))
    assert abs((moment("from") - (start - half)).total_seconds()) < 0.001, (asked, url)
    assert abs((moment("to") - (end + half)).total_seconds()) < 0.001, (asked, url)
    assert asked["scale"] == ["log"] and asked["a"] == [hub.LEAD_START], (asked, url)
    served = hub.lead_zoom(query)   # the exact query the page sends, through the route's own gate
    assert "error" not in served, served
    assert served["measure"] == lead["measure"], (served["measure"], lead["measure"])
    assert sum(k["count"] for k in served["clusters"]) == sum(k["count"] for k in lead["control"]["log"]["all"]["clusters"]), \
        "the zoomed answer plots the cards the base payload plots"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_stale_answer_never_draws_over_a_newer_view(hub):  # noqa: F811
    """Every zoom, timeframe, scale and reset action takes the next sequence number; an answer numbered older never
    draws, whether a later zoom, a timeframe switch or a reset superseded it."""
    payload = served_lead(hub)
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + """
      const BASE = PAYLOAD.lead.control.log.all;
      const held = [];
      let holding = false;
      call = p => holding ? new Promise(r => held.push(r)) : Promise.resolve(PAYLOAD);
      moreScreen = "lead"; await loadMore();
      const el = document.querySelector('[data-screen="lead"]');
      el.listeners.click.forEach(fn => fn({ target: el.querySelector('#ltbars [data-ltf="all"]') }));
      const click = sel => el.listeners.click.forEach(fn => fn({ target: el.querySelector(sel) }));
      holding = true;
      const first = leadZoom("in"), second = leadZoom("out");
      const seqs = MLT.seq;
      held[1]({ ...BASE, tag: "new" }); await second; await __tick(5);
      const drawn = MLT.zc && MLT.zc.tag;
      held[0]({ ...BASE, tag: "mid" }); await first; await __tick(5);
      const after = MLT.zc && MLT.zc.tag;   // the older answer never draws over the newer
      const pending = leadZoom("in");
      click('[data-ltzoom="reset"]');
      const atReset = { zc: MLT.zc, zr: MLT.zr };
      held[2]({ ...BASE, tag: "stale" }); await pending; await __tick(5);
      const afterReset = { zc: MLT.zc, zr: MLT.zr };   // an answer in flight at the reset never draws either
      const pending2 = leadZoom("in");
      click('#ltbars [data-ltf="7"]');
      const tf = MLT.tf;
      held[3]({ ...BASE, tag: "old" }); await pending2; await __tick(5);
      return { seqs, drawn, after, atReset, afterReset, tf, stale: MLT.zc, zc: !!MLT.zc };
    """)
    assert out["seqs"] >= 2, out
    assert out["drawn"] == "new", out
    assert out["after"] == "new", "a stale zoom answer drew over the newer one"
    assert out["atReset"] == {"zc": None, "zr": None}, out
    assert out["afterReset"] == {"zc": None, "zr": None}, "a zoom answer in flight at the reset drew over it"
    assert out["tf"] == "7", out
    assert out["stale"] is None and out["zc"] is False, \
        "a timeframe switch did not outrank the zoom answer in flight: the stale chart drew"
