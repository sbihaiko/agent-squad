"""The run dashboard on a narrow screen: one column of node cards, the ended run's info, and the ended text (#830).

The layout runs under node alone, with no DOM. Prior art for the page checks: tests/test_dashboard_page.py.
"""
import json
import shutil
import subprocess

import pytest

from squad.dashboard_server import RunServer
from tests.dashboard_helpers import OUT, ROOT, core_bundle, engine_data, page_bundle

NODE = shutil.which("node")
DOC = {"root": "a", "nodes": [
    {"name": "a", "children": ["b", "c"]},
    {"name": "b", "children": ["d"]},
    {"name": "c", "children": ["d"]},
    {"name": "d", "children": []},
]}


def layout(narrow):
    flag = "true" if narrow else "false"
    script = f"{core_bundle()}\nconst L = layoutModule();\nconsole.log(JSON.stringify(L.autoLayout({json.dumps(DOC)}, {flag})));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_narrow_screen_lays_every_node_in_one_column():
    pos = layout(narrow=True)
    assert len({p["x"] for p in pos.values()}) == 1
    assert len({p["y"] for p in pos.values()}) == len(DOC["nodes"])


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_narrow_screen_keeps_a_parent_above_its_children():
    pos = layout(narrow=True)
    assert pos["a"]["y"] < pos["b"]["y"] < pos["d"]["y"]
    assert pos["a"]["y"] < pos["c"]["y"] < pos["d"]["y"]


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_wide_screen_keeps_the_layered_columns():
    pos = layout(narrow=False)
    assert pos["b"]["x"] == pos["c"]["x"] != pos["a"]["x"]


def test_the_run_info_says_the_run_ended(tmp_path):
    folder = tmp_path / "run-x"
    folder.mkdir()
    (folder / "record.jsonl").write_text(
        json.dumps({"ts": 1.0, "event": "run_start", "pos": 1}) + "\n"
        + json.dumps({"ts": 2.0, "event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0, "pos": 2}) + "\n")
    srv = RunServer(folder)
    try:
        assert srv.info()["ended"] is True
    finally:
        srv.server_close()


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
  getElementById: (id) => (els[id] ||= stub()), createElement: () => stub(), createElementNS: () => stub(), querySelector: () => stub(), querySelectorAll: () => stub(),
  addEventListener() {}, removeEventListener() {}, createTextNode: () => stub(),
};
async function fetch(url) {
  const body = input.files[String(url).split("?")[0]];
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
try {
  vm.runInContext("const ENGINE = " + JSON.stringify(input.engine) + "; const SERVED = {tools: [], models: [], efforts: [], skills: [], hash: '', sources: []}; const EXAMPLE = null;", ctx);
  vm.runInContext(input.core, ctx);
  vm.runInContext(input.page, ctx);
} catch (e) { errors.push(String(e && e.stack || e)); }
setTimeout(() => {
  console.log(JSON.stringify({ status: els.status ? String(els.status.textContent) : null, errors }));
  process.exit(0);
}, 300);
"""


def page_status(files):
    payload = json.dumps({"engine": engine_data(), "core": core_bundle(), "page": page_bundle(), "files": files})
    p = subprocess.run([NODE, "-e", PAGE_HARNESS], input=payload, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.skipif(NODE is None, reason="node is needed for the page")
def test_an_ended_run_still_says_it_ended_after_the_page_loads_it():
    fixture = ROOT / "tests" / "fixtures" / "run-subject-22"
    files = {
        "run/info.json": json.dumps({"name": "run-subject-22", "ended": True, "issues": 0, "held": 0, "watch_seconds": 0}),
        "run/record.jsonl": (fixture / "run" / "record.jsonl").read_text(),
        "run/graph.json": (fixture / "graph.json").read_text(),
    }
    got = page_status(files)
    assert got["errors"] == [], got
    assert "This run ended" in got["status"], got


def test_the_page_switches_to_one_column_under_a_narrow_width():
    html = OUT.read_text()
    assert '"(max-width: 600px)"' in html


def test_a_refused_control_request_for_an_ended_run_shows_as_ended():
    html = OUT.read_text()
    assert "r.status === 409" in html
    assert "The run has ended" in html
