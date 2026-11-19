"""The phone's Loop entry (SPEC 1749; #1754): a layer, not a link, that draws the desktop's tree from the same route and the
same render function, laid out for a narrow screen. The render and the controller live in `hub-profiler.js`, inlined into both
pages (ADR-0137). The stub DOM of `tests/test_hub_mobile_move.py` cannot parse the tree's markup, so the tree is drawn and
tapped in node against one box, through the shared controller itself.
"""
import json
import pathlib
import re
import shutil
import subprocess
import uuid

import pytest

from tests.test_hub_mobile_move import HUB_PY, _module, run_page
from tests.test_hub_profiler import VIEW

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PROFILER_JS = SCRIPTS / "hub-profiler.js"
EGO = shutil.which("ego-browser")
PHONE_WIDTH = 390
READ_ONLY = "read-only from the tailnet: SQUAD_TAILNET_PROFILER_WRITE is not on (tailnet.profiler_write)"

ESC = 'const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;"})[c]);\n'
SETUP = """
const box = { innerHTML: "", listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } };
const gets = [], posts = [];
globalThis.document = { getElementById: () => box, activeElement: null };
globalThis.fetch = async (url, opts) => {
  if (opts && opts.method === "POST") { posts.push({ url, body: JSON.parse(opts.body) }); return { ok: true, json: async () => ({ ok: true }) }; }
  gets.push(url);
  return { ok: true, json: async () => JSON.parse(JSON.stringify(view)) };
};
profMount("phone");
const tap = target => box.listeners.click({ target });
const rowOf = p => ({ closest: s => (s === "tr[aria-expanded]" ? { dataset: { p } } : null) });
const switchOf = disabled => ({ closest: s => (s === "button[data-prof]" ? { disabled } : null) });
"""


def node_view(view, body):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = ESC + PROFILER_JS.read_text() + f"\nconst view = {json.dumps(view)};\n" + SETUP + \
        "(async () => {\nconst out = {};\n" + body + "\nprocess.stdout.write(JSON.stringify(out));\n})();\n"
    r = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_STATE", "SG_PROFILES_DIR"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_mobile_profiler", HUB_PY)


def routes(view=VIEW):
    return {"/api/profiler": [{"body": json.dumps(view), "status": 200}]}


def test_the_more_entry_is_a_button_that_opens_a_layer_not_a_link(hub):
    assert '<a class="more-link" id="more-loop"' not in hub.MOBILE_PAGE
    assert 'data-more="profiler"' in hub.MOBILE_PAGE


@pytest.mark.node_bound
def test_the_layer_opens_and_loads_the_profiler_route(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      openLayer("profiler");
      await __tick(10);
      return { open: !$("profpage").hidden, on: profV.on, tree: profV.tree.name };
    """, routes=routes())
    assert out == {"open": True, "on": True, "tree": "loop"}


def test_the_phone_defines_every_name_the_shared_render_function_calls(hub):
    assert re.search(r"^const esc\s*=", hub.MOBILE_PAGE, re.M), "the phone draws the shared render function without its esc"


def test_the_phone_and_the_desktop_inline_the_same_render_function(hub):
    src = PROFILER_JS.read_text()
    assert src in hub.MOBILE_PAGE, "the phone draws the desktop's render function, not a copy"
    assert src in hub.PAGE, "the desktop keeps the one render function"
    assert "function profHtml(" in src and 'fetch("/api/profiler' in src


def test_a_node_opens_and_closes_by_touch():
    out = node_view(VIEW, """
      await profPoll();
      out.before = box.innerHTML;
      tap(rowOf("loop/watchd.sh"));
      out.opened = box.innerHTML;
      tap(rowOf("loop/watchd.sh"));
      out.closed = box.innerHTML;
    """)
    assert 'data-p="loop/watchd.sh/watch.sh"' not in out["before"]
    assert 'data-p="loop/watchd.sh/watch.sh"' in out["opened"], "a press opens the node to its children"
    assert 'data-p="loop/watchd.sh/watch.sh"' not in out["closed"], "a second press closes it again"


def test_a_tailnet_reader_without_the_grant_sees_the_switch_read_only_with_its_reason():
    view = {**VIEW, "write": False, "reason": READ_ONLY}
    out = node_view(view, """
      await profPoll();
      out.html = box.innerHTML;
      tap(switchOf(true));
      out.posts = posts.length;
    """)
    assert "data-prof disabled" in out["html"], "the switch is read-only"
    assert READ_ONLY in out["html"], "the reason is on the screen, not only in a tooltip"
    assert out["posts"] == 0, "a read-only switch writes nothing"


def test_a_switch_write_goes_to_the_profiler_route_as_the_phone(hub):
    out = node_view(VIEW, """
      await profPoll();
      tap(switchOf(false));
      await new Promise(r => setTimeout(r, 5));
      out.posts = posts;
    """)
    assert out["posts"] == [{"url": "/api/profiler", "body": {"on": False, "client": "phone"}}]


def test_the_narrow_layout_fixes_the_columns_and_wraps_the_name(hub):
    page = hub.MOBILE_PAGE
    assert "#profb table{table-layout:fixed" in page
    assert "overflow-wrap:anywhere" in page


def test_the_wall_time_tables_scroll_inside_their_section_on_a_phone(hub):
    """The wall-time view (#1748) draws eight columns of gates; on a 390 px screen they scroll inside their own section, so no
    row widens the page and every column stays reachable."""
    assert "#profwall" not in hub.MOBILE_PAGE, "the removed wall-time panel leaves no rule behind"


# ---------------------------------------------------------------- the phone's trip lanes (#2229)
# The phone draws the trip of an issue from the same payload as the desktop (#2222): a Tokens lane and a Machine lane
# over the trip's columns, no Machine Load chart, and a table with tokens per issue, mean CPU and mean RAM. The lanes
# and the table read only the payload's own series and means.
TRIP = {"label": "The trip of an issue", "window_s": 3600, "deliveries": 4, "bar": True,
        "cores": 8, "mem_gb": 32.0, "peak_load": 4.2, "no_record_minutes": 10,
        "columns": [
            {"column": "todo", "label": "Todo", "min_s": 60.0, "median_s": 60.0, "p85_s": 120.0, "max_s": 120.0, "observations": 2, "parallelism": 1.0, "effective_s": 60.0, "share": 0.1,
             "tokens": {"points": [[0.5625, 480.0]], "mean": 60.0}, "tokens_per_issue": 600.0,
             "cpu": {"points": [[0.5625, 0.5]], "mean": 0.5}, "ram": {"points": [[0.5625, 0.4]], "mean": 0.4},
             "beside": None},
            {"column": "doing", "label": "Doing", "min_s": 600.0, "median_s": 600.0, "p85_s": 900.0, "max_s": 900.0, "observations": 3, "parallelism": 2.0, "effective_s": 300.0, "share": 0.5,
             "tokens": {"points": [[0.1875, 120.0], [0.8125, 240.0]], "mean": 120.0}, "tokens_per_issue": 2400.0,
             "cpu": {"points": [[0.1875, 0.3], [0.8125, 0.6]], "mean": 0.3},
             "ram": {"points": [[0.1875, 0.2], [0.8125, 0.3]], "mean": 0.25}, "beside": None},
            {"column": "reviewing", "label": "Reviewing", "min_s": 300.0, "median_s": 300.0, "p85_s": 420.0, "max_s": 420.0, "observations": 2, "parallelism": 1.0, "effective_s": 300.0, "share": 0.3,
             "tokens": {"points": [[0.5, 80.0]], "mean": 80.0}, "tokens_per_issue": 300.0,
             "cpu": {"points": [[0.5, 0.2]], "mean": 0.2}, "ram": {"points": [[0.5, 0.1]], "mean": 0.1},
             "beside": None},
            {"column": "testing", "label": "Testing", "min_s": 30.0, "median_s": 30.0, "p85_s": 40.0, "max_s": 40.0, "observations": 1, "parallelism": 1.0, "effective_s": 30.0, "share": 0.1,
             "tokens": {"points": [], "mean": None}, "tokens_per_issue": 0.0,
             "cpu": {"points": [], "mean": None}, "ram": {"points": [], "mean": None}, "beside": None}]}
LOAD = {"label": "Machine load", "start": 0, "end": 3600, "peak": 4.2, "cores": 8, "gap_s": 0,
        "segments": [[{"at": 60, "load": 2.5}]], "gaps": []}
PHONE_TRIP_VIEW = {**VIEW, "trip": TRIP, "load": LOAD}


def phone_trip_html(view=PHONE_TRIP_VIEW):
    return node_view(view, "await profPoll(); out.html = box.innerHTML;")["html"]


@pytest.mark.node_bound
def test_the_phone_draws_no_machine_load_chart():
    html = phone_trip_html()
    assert "id=profload" not in html, "the phone draws no Machine Load chart"
    assert "Machine load" not in html


@pytest.mark.node_bound
def test_the_phone_draws_the_tokens_lane_and_the_machine_lane_from_the_payload():
    html = phone_trip_html()
    assert "ptlane-tok" in html and "tokens/min" in html, "the Tokens lane names its own unit"
    assert "ptlane-mach" in html and "CPU" in html and "RAM" in html, "the Machine lane names its two lines"
    assert re.search(r'class="ptl ptl-cpu"', html) and re.search(r'class="ptl ptl-ram"', html), \
        "the machine lane draws the payload's CPU series solid and its RAM series dashed"
    tok = re.findall(r'class="ptl ptl-tok"[^>]*points="([^"]+)"', html)
    assert len(tok) == 1, "one Tokens line joins the three adjacent columns that hold a point, and the empty one breaks it"


@pytest.mark.node_bound
def test_the_phone_labels_each_column_with_its_name_and_median_time():
    html = phone_trip_html()
    labels = html[html.index("ptlabels"):]
    for name, time in (("Todo", "1.0 min · ×1.0"), ("Doing", "5.0 min · ×2.0"), ("Reviewing", "5.0 min · ×1.0"),
                       ("Testing", "30.0 s · ×1.0")):
        assert name in labels and time in labels, f"{name} keeps its name, its effective time and its parallelism"


@pytest.mark.node_bound
def test_the_phone_table_shows_tokens_per_issue_mean_cpu_and_mean_ram():
    html = phone_trip_html()
    table = html[html.index("id=proftt"):]
    for head in ("Stage", "Min", "Time per issue", "p85", "Max", "Parallelism", "Effective time", "Tokens / issue",
                 "CPU mean", "RAM mean"):
        assert head in table, f"the table names {head}"
    assert re.search(r"<td>600</td>", table) and re.search(r"<td>50%</td>", table) and re.search(r"<td>40%</td>", table), \
        "the Todo row carries the payload's tokens per issue and its mean CPU and RAM"
    testing = html[html.index('data-p="trip/testing"'):]
    assert "&#8212;" in testing, "a column with no reading prints a dash, never a zero"


TRIP_EGO = """
const task = await taskSpace(SPACE_NAME);
try {
  const page = task.page("p1");
  await page.cdp("Emulation.setDeviceMetricsOverride",
                 { width: WIDTH, height: HEIGHT, deviceScaleFactor: 2, mobile: true });
  await page.goto(URL);
  const seen = await page.evaluate(DRAW_TRIP, [TRIP_VIEW]);
  console.log("EGO_TRIP:" + JSON.stringify(seen));
} finally {
  await task.finish({ keep: [] });
}
"""

DRAW_TRIP = """
(args) => {
  const [view] = args;
  const fit = el => { const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height, right: r.right, bottom: r.bottom,
             sw: el.scrollWidth, cw: el.clientWidth, text: el.textContent.trim() }; };
  openLayer("profiler");
  profPhone = true;
  profV = view;
  profDraw();
  const trip = document.getElementById("proftrip");
  return { vw: window.innerWidth, docW: document.documentElement.scrollWidth, trip: fit(trip),
           lanes: [...trip.querySelectorAll("svg.ptlane")].map(fit),
           labels: [...trip.querySelectorAll(".ptlab")].map(fit),
           cells: [...document.querySelectorAll("#proftt th, #proftt td")].map(fit),
           table: fit(document.getElementById("proftt")) };
}
"""


def _trip_frame(page_html, base):
    page = base / "hub-mobile.html"
    page.write_text(page_html)
    head = (f"const SPACE_NAME = {json.dumps(f'agent-squad phone trip {uuid.uuid4().hex}')};\n"
            f"const URL = {json.dumps(page.as_uri())};\n"
            f"const WIDTH = {PHONE_WIDTH}; const HEIGHT = 844;\n"
            f"const TRIP_VIEW = {json.dumps(PHONE_TRIP_VIEW)};\n"
            f"const DRAW_TRIP = {DRAW_TRIP};\n")
    run = subprocess.run([EGO, "nodejs"], input=head + TRIP_EGO, capture_output=True, text=True, timeout=180)
    out = run.stdout + run.stderr
    seen = [line for line in out.splitlines() if line.startswith("EGO_TRIP:")]
    assert seen, f"ego-browser laid out no trip: {out[-2000:]}"
    return json.loads(seen[-1][len("EGO_TRIP:"):])


def _overlap(a, b):
    return min(a["right"], b["right"]) - max(a["x"], b["x"]) > 0.5 and min(a["bottom"], b["bottom"]) - max(a["y"], b["y"]) > 0.5


@pytest.mark.heavy
@pytest.mark.skipif(EGO is None, reason="ego-browser is needed to lay out the phone page")
def test_the_phone_trip_fits_390_px_with_no_overlap_and_no_overflow(hub, tmp_path):
    seen = _trip_frame(hub.MOBILE_PAGE, tmp_path)
    assert seen["vw"] == PHONE_WIDTH, f"the phone page rendered at {seen['vw']} px, not {PHONE_WIDTH}"
    assert seen["docW"] <= PHONE_WIDTH + 0.5, f"the page scrolls sideways: {seen['docW']} px wide"
    assert seen["trip"]["right"] <= PHONE_WIDTH + 0.5, f"the trip runs past the screen: {seen['trip']}"
    for i, lane in enumerate(seen["lanes"]):
        assert lane["right"] <= PHONE_WIDTH + 0.5 and lane["w"] > 0, f"lane {i} leaves the screen or draws nothing: {lane}"
    assert len(seen["labels"]) == 4, f"the trip drew {len(seen['labels'])} labels, not four"
    for i, label in enumerate(seen["labels"]):
        assert label["sw"] <= label["cw"] + 0.5, f"label {label['text']!r} overflows its box: {label}"
        assert label["right"] <= PHONE_WIDTH + 0.5, f"label {label['text']!r} runs past the screen: {label}"
        for other in seen["labels"][i + 1:]:
            assert not _overlap(label, other), f"labels {label['text']!r} and {other['text']!r} overlap"
    assert seen["table"]["right"] <= PHONE_WIDTH + 0.5, f"the table runs past the screen: {seen['table']}"
    for cell in seen["cells"]:
        assert cell["sw"] <= cell["cw"] + 0.5, f"cell {cell['text']!r} overflows its column: {cell}"
