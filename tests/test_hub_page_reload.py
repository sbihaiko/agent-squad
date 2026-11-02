"""An open hub tab follows the hub build that served it (#1114).

The page compares its own build, written by the process that served it, with /api/version. A hub that restarts before
the page's first poll must still reload it; a failed poll must say so; a back/forward restore must reload from the network.
The poll block runs in node with stubbed fetch, location and timers, against the page text the hub serves.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
MARKER = "// The open page follows the hub build that served it (#1114)"
NEW_BUILD_NS = 1_000_000_000   # JSON numbers in the page are doubles: a build gap under ~256 ns would not compare apart

HARNESS = """
const __steps = %(steps)s;
const __state = {reloads: 0, note: null};
const __timers = [];
const __listeners = {};
let __answer = null;
const location = {reload() { __state.reloads++; }};
const document = {body: {append(n) { __state.note = n; }},
  createElement() { const el = {className: "", textContent: "", remove() { if (__state.note === el) __state.note = null; }}; return el; }};
function addEventListener(type, f) { __listeners[type] = f; }
function setInterval(f) { __timers.push(f); }
async function fetch() {
  if (__answer === "ERR") throw new Error("hub down");
  return {ok: true, json: async () => ({v: __answer})};
}
%(block)s
(async () => {
  for (const s of __steps) {
    if ("poll" in s) { __answer = s.poll; await __timers[0](); }
    else __listeners.pageshow({persisted: s.persisted});
  }
  console.log(JSON.stringify({reloads: __state.reloads, note: __state.note ? __state.note.textContent : null}));
})();
"""


def load_hub(name):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location(name, HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def poll_block(page):
    assert MARKER in page, "the served page has no build poll that names the build serving it"
    start = page.index(MARKER)
    return page[start:page.index("</script>", start)]


def run_page(page, steps):
    script = HARNESS % {"steps": json.dumps(steps), "block": poll_block(page)}
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def hub():
    return load_hub("hub_page_reload")


def test_a_build_change_seen_on_the_first_poll_reloads_the_page(hub):
    page = hub.served_page("")
    assert run_page(page, [{"poll": hub.BOOT + NEW_BUILD_NS}]) == {"reloads": 1, "note": None}


def test_the_same_build_on_every_poll_does_not_reload(hub):
    page = hub.served_page("")
    assert run_page(page, [{"poll": hub.BOOT}, {"poll": hub.BOOT}]) == {"reloads": 0, "note": None}


def test_a_failed_poll_says_so_and_a_later_good_poll_clears_it(hub):
    page = hub.served_page("")
    failed = run_page(page, [{"poll": "ERR"}])
    assert failed["reloads"] == 0
    assert "may be out of date" in failed["note"]
    assert run_page(page, [{"poll": "ERR"}, {"poll": hub.BOOT}]) == {"reloads": 0, "note": None}


def test_a_failed_poll_followed_by_a_new_build_reloads_the_page(hub):
    page = hub.served_page("")
    assert run_page(page, [{"poll": "ERR"}, {"poll": hub.BOOT + NEW_BUILD_NS}]) == {"reloads": 1, "note": None}


def test_a_back_forward_restore_reloads_from_the_network(hub):
    page = hub.served_page("")
    assert run_page(page, [{"persisted": True}]) == {"reloads": 1, "note": None}
    assert run_page(page, [{"persisted": False}]) == {"reloads": 0, "note": None}


def test_a_restarted_hub_reloads_the_page_the_old_process_served(hub):
    old_page = hub.served_page("")
    time.sleep(0.01)
    new = load_hub("hub_page_reload_restarted")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), new.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/version", timeout=10).read()
    finally:
        srv.shutdown()
    answered = json.loads(body)["v"]
    assert answered == new.BOOT and answered != hub.BOOT
    assert run_page(old_page, [{"poll": answered}]) == {"reloads": 1, "note": None}


def test_the_served_page_carries_the_build_of_the_process_that_serves_it(hub):
    page = hub.served_page("")
    assert f"const SERVED_BUILD={hub.BOOT};" in page
    assert "__BUILD__" not in page
