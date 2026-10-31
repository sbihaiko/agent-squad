"""SPEC 889 (#918): the run dashboard and the editor show a loading, an empty and an error state.

A slow read paints a loading mark in each list and panel that depends on it; a list that legitimately holds
nothing names itself empty instead of a blank box; a read that fails names the file or folder that failed and
the rest of the page keeps working. The three states carry their own class name on one painter (state-loading,
state-empty, state-error), so a check reads the state from the markup and never needs a browser.

The behavioural checks run the page bundle under node, as tests/test_dashboard_narrow.py does: a read that
never settles is a promise that never resolves, and the panel's own markup is what the page painted.
"""
import json
import shutil
import subprocess

import pytest

from tests.dashboard_helpers import OUT, ROOT, core_bundle, engine_data, page_bundle

NODE = shutil.which("node")
FIX = ROOT / "tests" / "fixtures" / "run-subject-22"


def _files(record=None):
    """The files a served run folder holds; `record` replaces the fixture's record."""
    return {
        "run/info.json": json.dumps({"name": "run-subject-22", "ended": True, "issues": 0, "held": 0, "watch_seconds": 0}),
        "run/record.jsonl": record if record is not None else (FIX / "run" / "record.jsonl").read_text(),
        "run/graph.json": (FIX / "graph.json").read_text(),
    }


# A run that landed nothing and merged nothing: the folder carries no issue set, so the bar has nothing to count.
QUIET_RECORD = "".join(json.dumps(e) + "\n" for e in (
    {"ts": 1.0, "event": "run_start", "pos": 1, "run_id": "run-quiet", "graph_name": "subject-issue", "defaults": {}},
    {"ts": 2.0, "event": "verdict", "verdict": "completed", "exit_code": 0, "reasons": [], "pos": 2},
))


PAGE_HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const errors = [];
process.on("unhandledRejection", (e) => errors.push(String(e && e.stack || e)));
const els = {};
function stub(store = {}) {
  return new Proxy(function () {}, {
    get(t, k) {
      if (k === Symbol.iterator) return () => [][Symbol.iterator]();
      if (typeof k === "symbol" || k === "then") return undefined;
      if (!(k in store)) store[k] = stub();
      return store[k];
    },
    set(t, k, v) { store[k] = v; return true; },
    apply() { return stub(); },
  });
}
const location = { protocol: "http:", href: "http://127.0.0.1/dash/run-x/", pathname: "/dash/run-x/", search: "", hash: "" };
const window = { location, matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerWidth: 800, innerHeight: 600 };
const document = {
  title: "Run dashboard", body: stub(), documentElement: stub(), readyState: "complete",
  getElementById: (id) => (els[id] ||= stub()),
  createElement: () => stub(), createElementNS: () => stub(), querySelector: () => stub(), querySelectorAll: () => stub(),
  addEventListener() {}, removeEventListener() {}, createTextNode: () => stub(),
};
// A read that is named in `pending` never settles: it is a promise that never resolves, as a slow read is.
async function fetch(url) {
  const key = String(url).split("?")[0];
  if (input.pending.includes(key)) return new Promise(() => {});
  const body = input.files[key];
  if (body === undefined) return { ok: false, status: 404, json: async () => ({}), text: async () => "" };
  return { ok: true, status: 200, json: async () => JSON.parse(body), text: async () => body };
}
class Inert { observe() {} unobserve() {} disconnect() {} addEventListener() {} removeEventListener() {} close() {} }
const ctx = vm.createContext({
  document, window, location, fetch, console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams, TextEncoder, TextDecoder,
  EventSource: Inert, ResizeObserver: Inert, MutationObserver: Inert, localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: {}, requestAnimationFrame: () => 0, cancelAnimationFrame() {}, crypto: globalThis.crypto, performance: globalThis.performance,
  alert() {}, confirm: () => false, prompt: () => null, getComputedStyle: () => stub(), addEventListener() {}, removeEventListener() {},
});
const html = (id) => (els[id] && typeof els[id].innerHTML === "string" ? els[id].innerHTML : "");
try {
  vm.runInContext("const ENGINE = " + JSON.stringify(input.engine) + "; const SERVED = {tools: [], models: [], efforts: [], skills: [], hash: '', sources: []}; const EXAMPLE = null;", ctx);
  vm.runInContext(input.core, ctx);
  vm.runInContext(input.page, ctx);
} catch (e) { errors.push(String(e && e.stack || e)); }
setTimeout(() => {
  console.log(JSON.stringify({ side: html("side"), progress: html("progress"), totals: html("totals"), status: els.status ? String(els.status.textContent) : null, errors }));
  process.exit(0);
}, 400);
"""


def page_reads(files, pending=()):
    """The panel, the bar and the status line after the page reads the served run; a pending read never settles."""
    payload = json.dumps({"engine": engine_data(), "core": core_bundle(), "page": page_bundle(), "files": files, "pending": list(pending)})
    p = subprocess.run([NODE, "-e", PAGE_HARNESS], input=payload, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.skipif(NODE is None, reason="node is needed for the page")
def test_a_slow_read_marks_the_panel_and_the_bar_as_loading():
    got = page_reads(_files(), ["run/record.jsonl"])
    assert got["errors"] == [], got
    assert "state-loading" in got["side"], got          # the panel says it is reading, not a blank page
    assert "Loading" in got["side"] and "run/record.jsonl" in got["side"], got   # and it names what it reads
    assert "state-loading" in got["progress"], got      # the bar depends on the same read
    assert "state-loading" in got["totals"], got        # and so does the totals strip


@pytest.mark.skipif(NODE is None, reason="node is needed for the page")
def test_a_failed_read_shows_an_error_that_names_what_failed():
    files = _files()
    del files["run/record.jsonl"]                        # the server has no record: the read comes back empty
    got = page_reads(files)
    assert got["errors"] == [], got
    assert "state-error" in got["side"], got
    assert "run/record.jsonl" in got["side"], got        # the error names the file that failed
    assert "No record.jsonl" in got["status"], got       # the status line keeps its own message


@pytest.mark.skipif(NODE is None, reason="node is needed for the page")
def test_a_run_folder_with_no_issue_set_says_the_bar_is_empty():
    got = page_reads(_files(QUIET_RECORD))
    assert got["errors"] == [], got
    assert "state-empty" in got["progress"], got
    assert "no issue set" in got["progress"], got


# --- one painter, one class name per state, and a look for each in the stylesheet
def _states_src():
    return (ROOT / "dashboard" / "page" / "states.js").read_text()


def test_one_painter_names_the_three_states_and_the_stylesheet_paints_them():
    src = _states_src()
    for cls in ("state-loading", "state-empty", "state-error"):
        assert cls in src, cls
        assert f".{cls}" in OUT.read_text(), cls
    assert 'aria-busy="true"' in src and "esc(" in src   # a loading mark is busy to a screen reader, and text is escaped


def test_every_page_source_is_still_bundled_once_with_the_painter():
    from build_dashboard import PAGE_DIR, PAGE_ORDER

    assert "states.js" in PAGE_ORDER
    assert sorted(PAGE_ORDER) == sorted(p.name for p in PAGE_DIR.glob("*.js"))


# --- every list and panel that can legitimately hold nothing says so
def test_the_lists_that_can_hold_nothing_name_themselves_empty():
    src = page_bundle()
    for token in (
        'emptyHtml("This dispatch started no child',      # the roster of a dispatch
        "no findings",                                     # a review that reported none
        "no registered gate",                              # the add-node check list
        "Nothing to undo yet",                             # the history
        "Nothing to redo yet",
        "Nothing selected",                                # the panel with no card to show
    ):
        assert token in src, token


# --- a read that fails leaves a named error in the place that depends on it
@pytest.mark.skipif(NODE is None, reason="node is needed for the page")
def test_a_graph_that_fails_to_load_names_the_file_in_an_error_state():
    files = _files()
    files["run/graph.json"] = "{ not a graph"
    got = page_reads(files)
    assert got["errors"] == [], got
    assert "state-error" in got["side"], got
    assert "run/graph.json" in got["side"], got      # the panel names the file that failed, not "an error"


def test_the_panels_name_a_read_through_the_one_painter():
    src = page_bundle()
    for token in ('readKey("record", "graph")', 'readKey("issues", "record")', "paintRead("):
        assert token in src, token
