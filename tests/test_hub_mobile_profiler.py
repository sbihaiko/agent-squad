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

import pytest

from tests.test_hub_mobile_move import HUB_PY, _module, run_page
from tests.test_hub_profiler import VIEW

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PROFILER_JS = SCRIPTS / "hub-profiler.js"
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
