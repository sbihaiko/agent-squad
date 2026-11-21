"""The phone's lead-time chart (#1333, SPEC 1327): the mobile hub draws the payload the desktop page already reads.

The drawing is one client module, run under node alone with no DOM and no server. The page is composed from its files
the way the hub composes it: the chart screen fetches `/api/chart/lead`, the desktop page's own route, never a second
one. On a phone the legend and the reference values sit under the plot with no right gutter, a tap pins one mark's
tooltip, and zoom and reset are explicit buttons instead of a drag.

Every fixture is the shape the hub serves, never a hand-made approximation of it. The pin tests take a cluster from the
answer `/api/chart/lead` composes (`hub.flow_lead` over a seeded history store), so `mltPin` reads the dot fields and the
range objects the route actually hands the desktop page. The zoom test captures the URL the screen builds, passes it to
`hub.lead_zoom`, and feeds the route's real answer back through the page, so the zoom-response coverage is the route's
answer and not a stand-in. Only the drawing tests (marks, legend, the zoom arithmetic) use `control_chart` directly.
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

import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import hub  # noqa: F401  (hub is a fixture)

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
    assert payload["lead"]["control"]["all"]["clusters"], "the served payload carries no cluster for the cards"
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
    assert "Lead time (log)" in out["svg"] and "Lead time (linear)" not in out["svg"], "the axis title is the desktop's log title"
    served = {k: v for k, v in CHART.items() if k != "scale"}
    assert "Lead time (log)" in run_js(f"mltChart({json.dumps(served)})")["svg"], "the title reads a field the route no longer serves"


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


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_phone_draws_the_small_marks_the_payload_names():
    """#1432: the phone draws a lone card at 3 px, a cluster at 5 px and the outline at 1.5 px, the payload's own sizes."""
    assert CHART["mark"] == {"dot_radius": 3, "cluster_radius": 5, "outline": 1.5}
    svg = run_js(f"mltChart({json.dumps(CHART)})")["svg"]
    circles = re.findall(r"<circle [^>]*>", svg)
    radii = sorted(float(re.search(r"\br=([^\s>]+)", c).group(1)) for c in circles)
    assert radii == [3, 5], f"the phone draws no 3 px and 5 px mark: {radii}"
    widths = [float(re.search(r"stroke-width=([^\s>]+)", c).group(1)) for c in circles]
    assert widths == [1.5, 1.5], f"the phone's outlines are not 1.5 px: {widths}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_phone_applies_the_payloads_outline_rather_than_a_literal():
    """#1432: the outline the page draws is the payload's own `mark.outline`, never a number the view keeps. The fixture
    serves a value no default names, so a hard-coded 1.5 fails here."""
    chart = json.loads(json.dumps(CHART))
    chart["mark"]["outline"] = 2.75

    circles = re.findall(r"<circle [^>]*>", run_js(f"mltChart({json.dumps(chart)})")["svg"])
    widths = [float(re.search(r"stroke-width=([^\s>]+)", c).group(1)) for c in circles]
    assert widths == [2.75, 2.75], f"the phone ignores the payload's outline: {widths}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_payload_without_mark_keeps_the_outline_it_had():
    """#1432: `mark` is new on the wire, so a cached or older payload carries none. The phone then keeps its 1.5 px
    ring instead of drawing `stroke-width=undefined`, which the browser drops."""
    chart = {k: v for k, v in CHART.items() if k != "mark"}

    circles = re.findall(r"<circle [^>]*>", run_js(f"mltChart({json.dumps(chart)})")["svg"])
    assert circles, "the phone drew no mark for a payload without `mark`"
    widths = [re.search(r"stroke-width=([^\s>]+)", c).group(1) for c in circles]
    assert widths == ["1.5", "1.5"], f"a payload without `mark` loses the ring: {widths}"


def test_the_route_serves_each_cluster_range_the_pin_reads(hub):  # noqa: F811
    """The pin takes a cluster's lead and closed ranges from the payload (board_leadtime.py:254-255 sets them): every
    cluster of the route's own answer carries `{min, max, text}` and `{start, end, text}`, and its dots carry `n`,
    `title`, `text` and a date string `closed`, so no range is recomputed in the view (ADR-0137)."""
    clusters = served_lead(hub)["lead"]["control"]["all"]["clusters"]
    lone = next(k for k in clusters if k["count"] == 1)
    many = next(k for k in clusters if k["count"] > 1)
    for k in (lone, many):
        assert {"min", "max", "text"} <= set(k["lead"]), k
        assert {"start", "end", "text"} <= set(k["closed"]), k
        assert k["count"] == len(k["dots"]) == len(k["cards"]), k
        assert all({"n", "title", "closed", "text", "stack"} <= set(d) and isinstance(d["closed"], str) for d in k["dots"]), k


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_pins_a_lone_card_from_the_served_cluster(hub):  # noqa: F811
    """A lone cluster carries no `n`, `title`, `text` or string `closed` of its own: they live on its one dot."""
    lone = next(k for k in served_lead(hub)["lead"]["control"]["all"]["clusters"] if k["count"] == 1)
    pinned = run_js(f"mltPin({json.dumps(lone)})")
    assert pinned == "#1 card 1 · lead 2h · closed 2026-10-01 · Todo 2h", pinned


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_lists_every_card_in_the_served_cluster(hub):  # noqa: F811
    """A cluster lists its cards from the dots it serves and takes its ranges from its own `lead` and `closed`."""
    many = next(k for k in served_lead(hub)["lead"]["control"]["all"]["clusters"] if k["count"] > 1)
    pinned = run_js(f"mltPin({json.dumps(many)})")
    assert pinned == "2 issues: #2, #3 · lead 1h · closed Oct 2 · Todo 1h · Todo 1h", pinned


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_on_a_served_cluster_pins_its_text(hub):  # noqa: F811
    """`loadLead` hands the screen the route's own answer; a tap on one of its clusters pins that cluster's text."""
    payload = served_lead(hub)
    index = next(i for i, k in enumerate(payload["lead"]["control"]["all"]["clusters"]) if k["count"] > 1)
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + f"""
      call = p => Promise.resolve(PAYLOAD);   // the screen's own fetch of /api/chart/lead
      await loadLead();
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
    builds is the one `hub.lead_zoom` accepts, and the route's real answer is what the page draws."""
    payload = served_lead(hub)
    lead = payload["lead"]
    # Pass one: the screen builds its URL from the served view. The other path answers nothing here; this pass reads the
    # request alone, so no synthetic chart ever stands in for the route's answer.
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + """
      const sent = [];
      call = (p, fresh) => { sent.push({ path: p, fresh: !!fresh }); return Promise.resolve(p.startsWith("/api/chart/lead") ? PAYLOAD : {}); };
      await loadLead();
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
    asked = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    rng = lead["control"]["all"]["range"]

    def ms(m):   # the page reads the payload's stamps through Date, whose resolution is the millisecond
        return m.replace(microsecond=m.microsecond // 1000 * 1000)

    start, end = ms(datetime.fromisoformat(rng["start"])), ms(datetime.fromisoformat(rng["end"]))

    def moment(key):   # the page's Date#toISOString: UTC, milliseconds, Z, so 1ms is the page's own resolution
        return datetime.fromisoformat(asked[key][0].replace("Z", "+00:00"))

    frm, to = moment("from"), moment("to")
    # Date truncates each endpoint to its 1ms resolution, so each comparison may carry up to 1ms of rounding.
    assert abs(((to - frm) - 2 * (end - start)).total_seconds()) <= 0.002, (asked, url)   # the window doubles
    assert abs(((frm - start) + (to - end)).total_seconds()) <= 0.002, (asked, url)   # around the same middle
    assert "scale" not in asked and asked["a"] == [hub.LEAD_START], (asked, url)
    answer = hub.lead_zoom(urllib.parse.urlsplit(url).query)   # the exact query the page sends, through the route's gate
    assert "error" not in answer, answer
    assert answer["measure"] == lead["measure"], (answer["measure"], lead["measure"])
    assert sum(k["count"] for k in answer["clusters"]) == sum(k["count"] for k in lead["control"]["all"]["clusters"]), \
        "the zoomed answer plots the cards the base payload plots"
    assert datetime.fromisoformat(answer["range"]["start"]) == frm, (answer["range"], asked)
    assert datetime.fromisoformat(answer["range"]["end"]) == to, (answer["range"], asked)
    # Pass two: the page draws the route's own answer, not a stand-in of the test's own making.
    drawn = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)}; const ANSWER = {json.dumps(answer)};" + """
      call = p => Promise.resolve(p.startsWith("/api/chart/lead") ? PAYLOAD : ANSWER);
      await loadLead();
      const el = document.querySelector('[data-screen="lead"]');
      el.listeners.click.forEach(fn => fn({ target: el.querySelector('#ltbars [data-ltf="all"]') }));
      await leadZoom("out");
      return { zc: MLT.zc, zr: MLT.zr, msg: el.querySelector(".msg").textContent };
    """)
    assert drawn["msg"] == "", drawn
    assert drawn["zc"] is not None and drawn["zc"]["measure"] == answer["measure"], drawn["zc"]
    assert drawn["zc"]["range"] == answer["range"], (drawn["zc"]["range"], answer["range"])
    assert [d["n"] for d in drawn["zc"]["dots"]] == [d["n"] for d in answer["dots"]], drawn["zc"]
    assert [k["count"] for k in drawn["zc"]["clusters"]] == [k["count"] for k in answer["clusters"]], drawn["zc"]


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_stale_answer_never_draws_over_a_newer_view(hub):  # noqa: F811
    """Every zoom, timeframe and reset action takes the next sequence number; an answer numbered older never
    draws, whether a later zoom, a timeframe switch or a reset superseded it."""
    payload = served_lead(hub)
    out = move.run_page(hub.MOBILE_PAGE, f"const PAYLOAD = {json.dumps(payload)};" + """
      const BASE = PAYLOAD.lead.control.all;
      const held = [];
      let holding = false;
      call = p => holding ? new Promise(r => held.push(r)) : Promise.resolve(PAYLOAD);
      await loadLead();
      const el = document.querySelector('[data-screen="lead"]');
      el.listeners.click.forEach(fn => fn({ target: el.querySelector('#ltbars [data-ltf="all"]') }));
      const click = sel => el.listeners.click.forEach(fn => fn({ target: el.querySelector(sel) }));
      const draw0 = leadDraw; let draws = 0; leadDraw = (...a) => { draws++; return draw0(...a); };
      holding = true;
      const first = leadZoom("in"), second = leadZoom("out");
      const seqs = MLT.seq;   // one number per zoom action
      held[1]({ ...BASE, tag: "new" }); await second; await __tick(5);
      const drawn = MLT.zc && MLT.zc.tag;
      held[0]({ ...BASE, tag: "mid" }); await first; await __tick(5);
      const after = MLT.zc && MLT.zc.tag;   // the older answer never draws over the newer
      const pending = leadZoom("in");
      click('[data-ltzoom="reset"]');
      const atReset = { zc: MLT.zc, zr: MLT.zr }, seqAfter = MLT.seq, drawsAtReset = draws;
      held[2]({ ...BASE, tag: "stale" }); await pending; await __tick(5);
      const afterReset = { zc: MLT.zc, zr: MLT.zr, draws };   // an answer in flight at the reset never draws either
      const pending2 = leadZoom("in");
      click('#ltbars [data-ltf="7"]');
      const tf = MLT.tf;
      held[3]({ ...BASE, tag: "old" }); await pending2; await __tick(5);
      return { seqs, seqAfter, drawsAtReset, drawn, after, atReset, afterReset, tf, stale: MLT.zc, zc: !!MLT.zc };
    """)
    assert out["seqs"] == 3, out   # the served 'all' view, then the zoom in and the zoom out
    assert out["drawn"] == "new", out
    assert out["after"] == "new", "a stale zoom answer drew over the newer one"
    assert out["seqAfter"] == out["seqs"] + 2, "the reset took no sequence number of its own"   # the zoom in flight, then the reset
    assert out["atReset"] == {"zc": None, "zr": None}, out
    assert out["afterReset"]["zc"] is None and out["afterReset"]["zr"] is None, \
        "a zoom answer in flight at the reset drew over it"
    assert out["afterReset"]["draws"] == out["drawsAtReset"], "the in-flight answer's resolution drew after the reset"
    assert out["tf"] == "7", out
    assert out["stale"] is None and out["zc"] is False, \
        "a timeframe switch did not outrank the zoom answer in flight: the stale chart drew"
