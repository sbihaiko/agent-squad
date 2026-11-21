"""The phone's route table in a real browser: Playwright with Chrome walks five tabs, a legacy address, a tab tap that
replaces the entry, a direct entry on a detail, and the search that pushes no entry (SPEC 2498, #2499).

The page is served from a loopback server that answers the hub's own GET routes with empty facts, so every rule under
test is the page's, never a stub of it. The test skips when Playwright or Chrome is missing, as the nightly suite does.
"""
import http.server
import json
import pathlib
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
TABS = ["now", "work", "specs", "runs", "spend"]

# The hub's reads, answered with the emptiest shape each one serves. `/api/version` carries no build on purpose: the page
# reloads when a build differs, and a check of the routes never asks for a reload (#1114).
API = {
    "/api/board": {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None, "groups": []},
    "/api/runs": [], "/api/ended": [], "/api/stats": {"buckets": [], "totals": {}, "models": []},
    "/api/chart/lead": {"lead": {"control": {"all": {"clusters": [], "range": ""}}, "legend": "", "start": ""}},
    "/api/chart/cfd": {"columns": [], "rows": []}, "/api/profiler": {}, "/api/loop": {},
    "/api/load": {}, "/api/live": {}, "/api/slots": {}, "/api/subs": {}, "/api/models": {},
    "/api/columns": {}, "/api/limits": {}, "/api/delivery": {}, "/api/issue": {}, "/api/status": {},
}


def _serve(page_html):
    """One loopback server for the page and the hub's reads: the page runs at a real origin, so history and the address
    behave as they do on the phone."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/api/"):
                body = json.dumps(API.get(path, {})).encode()
                kind = "application/json"
            else:
                body, kind = page_html.encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.fixture(scope="module")
def hub():
    import importlib.util
    import os
    import tempfile

    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_routes_browser", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _boot(page, base, hash_):
    """A direct entry at an address: a blank document first, so the page loads at a clean history and reads the hash the
    way a saved address or a pasted one enters it."""
    page.goto("about:blank")
    page.goto(base + hash_)
    page.wait_for_function("() => !!document.querySelector('nav')")


@pytest.mark.heavy
def test_a_real_browser_walks_the_route_table(hub):
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except sync.Error as e:   # no Chrome on this machine: the walked check cannot run here
            pytest.skip(f"Chrome is not available: {' '.join(str(e).split())[:200]}")
        server, port = _serve(hub.MOBILE_PAGE)
        base = f"http://127.0.0.1:{port}/p/x/mobile"
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3)
            page.goto(base)
            page.wait_for_function("() => !!document.querySelector('nav')")

            # The tab bar: five tabs, in the order the route table names them.
            assert page.eval_on_selector_all("nav [data-tab]", "els => els.map(e => e.dataset.tab)") == TABS

            # A saved address of a dropped screen opens the screen the table names, and the entry is replaced.
            _boot(page, base, "#/now/usage")
            assert page.evaluate("location.hash") == "#/spend"
            assert page.evaluate("!document.querySelector('[data-panel=\"spend\"]').hidden")
            assert page.evaluate("!document.querySelector('[data-screen=\"cost\"]').hidden")

            # A tab tap replaces the entry: the stack keeps its length, so Back never walks through a tab the owner left.
            entries = page.evaluate("history.length")
            for tab in ("work", "specs", "runs"):
                page.click(f'nav [data-tab="{tab}"]')
                assert page.evaluate("history.length") == entries, "the tap pushed a tab entry"
            assert page.evaluate("location.hash") == "#/runs"

            # A direct entry on a detail shows one Back that names its parent, and that Back opens it.
            _boot(page, base, "#/now/loops")
            assert page.is_visible('[data-topback="loops"]')
            assert page.inner_text('[data-topback="loops"] .ico-lbl') == "Now"
            page.click('[data-topback="loops"]')
            page.wait_for_timeout(200)
            assert page.evaluate("location.hash") == "#/now"

            # The search pushes no entry, and Escape closes it before it leaves the screen.
            page.click('nav [data-tab="work"]')
            page.wait_for_timeout(100)
            entries = page.evaluate("history.length")
            page.click("#work-search")
            page.fill("#search-input", "phone")
            assert page.is_visible("#searchbar")
            assert page.evaluate("history.length") == entries
            page.keyboard.press("Escape")
            assert page.is_hidden("#searchbar")
            assert page.evaluate("location.hash") == "#/work"
        finally:
            browser.close()
            server.shutdown()
