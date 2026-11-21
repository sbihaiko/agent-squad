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

from tests.test_hub_mobile_move import route_tabs

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
# The panel each tab draws, and the screen a detail draws inside it.
TAB_PANEL = {"now": "monitor", "work": "board", "specs": "specs", "runs": "runs", "spend": "spend"}

# Enough cards that the Work column scrolls under a phone, and one card the search finds by title.
BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
         "groups": [{"name": "Backlog", "n": 30, "items":
                     [{"n": n, "t": f"Phone card {n}", "l": ["area:phone"]} for n in range(1, 31)]}]}
# One bucket and one model, so the Spend screen draws its chart and its list from the hub's own shape.
STATS = {"buckets": [{"t": "12:00", "spend": 1.5, "runs": 2}],
         "totals": {"today": 1.5, "month": 3, "all": 9}, "models": [{"model": "m", "spend": 1.5, "calls": 3}]}

# The hub's reads, answered with the emptiest shape each one serves. `/api/version` carries no build on purpose: the page
# reloads when a build differs, and a check of the routes never asks for a reload (#1114).
API = {
    "/api/board": BOARD,
    "/api/runs": [], "/api/ended": [], "/api/stats": STATS,
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
    """A direct entry at an address the way a saved address or a pasted one enters it: the page loads from the base URL,
    takes the address, and reloads, so the boot reads the hash with no page state behind it."""
    page.goto(base)
    page.evaluate("h => { location.hash = h; }", hash_)
    page.reload()
    page.wait_for_function("() => !!document.querySelector('nav')")


def _panel_visible(page, sel):
    """The screen draws: it is not hidden and it takes space on the page."""
    return page.evaluate("""sel => {
        const e = document.querySelector(sel);
        if (!e || e.hidden) return false;
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }""", sel)


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
            tabs = [tab for tab, _ in route_tabs(hub.MOBILE_PAGE)]
            assert page.eval_on_selector_all("nav [data-tab]", "els => els.map(e => e.dataset.tab)") == tabs

            # Walk the table: each tab draws its own panel and address, and the first cross-tab tap pushes the one entry
            # every later tap replaces, so Back never walks through the tabs the owner already left.
            entries = page.evaluate("history.length")
            for tab in tabs:
                page.click(f'nav [data-tab="{tab}"]')
                page.wait_for_timeout(100)
                assert _panel_visible(page, f'[data-panel="{TAB_PANEL[tab]}"]'), tab
                assert page.evaluate("location.hash") == f"#/{tab}", tab
                if tab == "now":
                    assert page.evaluate("history.length") == entries, "the opening tap pushed an entry"
                elif tab == "work":
                    entries = page.evaluate("history.length")   # the one pushed entry
                    assert entries == page.evaluate("history.length")
                else:
                    assert page.evaluate("history.length") == entries, f"the tap on {tab} pushed a second tab entry"
            assert page.evaluate("location.hash") == "#/spend"

            # Back reaches the route the owner arrived on, never a tab the owner already left.
            page.go_back()
            page.wait_for_function("() => location.hash === '#/now'")
            assert _panel_visible(page, '[data-panel="monitor"]')

            # Spend draws the desktop cost screen: its chart fills the landscape width at 844x390.
            page.set_viewport_size({"width": 844, "height": 390})
            _boot(page, base, "#/spend")
            page.wait_for_function("() => !!document.querySelector('#cchart svg')")
            assert page.is_visible('[data-screen="cost"]')
            assert page.eval_on_selector("#cchart svg", "e => e.getBoundingClientRect().width") > 700, \
                "the Spend chart does not fill the landscape width"
            page.set_viewport_size({"width": 390, "height": 844})

            # A saved address of a dropped screen opens the screen the table names, and the entry is replaced.
            _boot(page, base, "#/now/usage")
            assert page.evaluate("location.hash") == "#/spend"
            assert _panel_visible(page, '[data-panel="spend"]')
            assert _panel_visible(page, '[data-screen="cost"]')

            # A direct entry on a detail shows one Back that names its parent, and that Back opens it.
            _boot(page, base, "#/now/loops")
            assert page.is_visible('[data-topback="loops"]')
            assert page.inner_text('[data-topback="loops"] .ico-lbl') == "Now"
            page.click('[data-topback="loops"]')
            page.wait_for_timeout(200)
            assert page.evaluate("location.hash") == "#/now"

            # Work: the search pushes no entry and finds a card; Escape closes it before the screen leaves.
            page.click('nav [data-tab="work"]')
            page.wait_for_timeout(200)
            entries = page.evaluate("history.length")
            page.click("#work-search")
            page.fill("#search-input", "Phone card 7")
            assert page.is_visible("#searchbar")
            assert page.eval_on_selector_all("#work-results .runrow", "els => els.length") >= 1
            assert page.evaluate("history.length") == entries, "the search pushed an entry"
            page.keyboard.press("Escape")
            assert page.is_hidden("#searchbar")
            assert page.evaluate("location.hash") == "#/work"

            # Specs: its own search opens on the tab, and Escape closes it.
            page.click('nav [data-tab="specs"]')
            page.wait_for_timeout(100)
            page.click("#specs-search")
            assert page.is_visible("#searchbar")
            page.keyboard.press("Escape")
            assert page.is_hidden("#searchbar")

            # Each screen restores its scroll: Work scrolls, another tab opens, and Work returns to the same place.
            page.click('nav [data-tab="work"]')
            page.wait_for_timeout(100)
            page.evaluate("window.scrollTo(0, 160)")
            scrolled = page.evaluate("window.scrollY")
            assert scrolled > 0, "the Work screen does not scroll under the phone"
            page.click('nav [data-tab="runs"]')
            page.wait_for_timeout(100)
            page.click('nav [data-tab="work"]')
            page.wait_for_timeout(100)
            assert page.evaluate("window.scrollY") == scrolled, "Work did not restore its scroll"
        finally:
            browser.close()
            server.shutdown()
