"""The phone's chart screens in a real browser (SPEC 2498, #2504): at 844x390 a chart fills the landscape width, at
390x844 the screen shows one rotate hint, a rotation measures the box again and redraws at the new width, and a tap on a
point shows its value. A chart screen carries no tab bar.

The page is served from a loopback server that answers the hub's own GET routes with the shape each one serves, so every
rule under test is the page's own. The test skips when Playwright or Chrome is missing, as the nightly suite does.
"""
import http.server
import importlib.util
import json
import os
import pathlib
import tempfile
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

DAYS = ["2026-11-18", "2026-11-19", "2026-11-20"]
CFD = {
    "t": 1, "cfd": {
        "start": "2026-11-18", "zone": "UTC", "days": DAYS, "ymax": 4, "yticks": [0, 2, 4],
        "xticks": [{"i": 0, "day": DAYS[0]}, {"i": 1, "day": DAYS[1]}, {"i": 2, "day": DAYS[2]}],
        "bands": [
            {"name": "Todo", "lower": [0, 0, 0], "upper": [2, 2, 1], "count": [2, 2, 1], "held": [0, 0, 0],
             "held_upper": [0, 0, 0]},
            {"name": "Closed", "lower": [0, 0, 1], "upper": [0, 1, 3], "count": [0, 1, 2], "held": [0, 0, 0],
             "held_upper": [0, 0, 1]},
        ],
        "reads": [{"day": d, "counts": [{"name": "Todo", "count": 1}, {"name": "Closed", "count": 1}], "wip": 2,
                   "throughput": 1} for d in DAYS],
        "last": {"day": DAYS[-1], "wip": 2, "throughput": 1},
    },
}
# One lead view: two closed cards, one cluster each, in the timeframe the phone opens.
LEAD_VIEW = {
    "dots": [], "clusters": [], "range": {"start": "2026-11-18T00:00:00Z", "end": "2026-11-20T00:00:00Z", "text": "3 days"},
    "measure": "Todo -> closed", "text": "lead time", "columns": ["Todo", "Closed"],
    "ticks": [{"y": 0, "text": "1h"}, {"y": 1, "text": "4h"}], "xticks": [{"x": 0, "text": "Nov 18"}, {"x": 1, "text": "Nov 20"}],
    "average": {"y": 0.4, "text": "4h", "label": "Average"}, "median": {"y": 0.3, "text": "1h 36m", "label": "Median"},
    "p85": {"y": 0.7, "text": "7h 21m", "label": "p85"}, "mark": {"outline": 1.5},
    "plot_frame": {"width": 672, "height": 212},
}
LEAD = {"t": 1, "plot_frame": {"width": 672, "height": 212},
        "lead": {"measure": "Todo -> closed", "type": "bug", "window": {"size": 20, "unit": "issues"}, "start": "Todo",
                 "excluded": [], "excluded_total": 0, "excluded_text": "", "excluded_cards": {}, "excluded_reasons": {},
                 "series": [], "summary": {"minimum": {"text": "1h"}, "maximum": {"text": "2d"}, "plotted_text": "20 issues"},
                 "control": {k: LEAD_VIEW for k in ("12h", "1", "2", "3", "7", "14", "30", "all")},
                 "columns": ["Todo", "Closed"], "coverage": None}}
BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None, "groups": []}
API = {
    "/api/board": BOARD, "/api/runs": [], "/api/ended": [], "/api/stats": {"buckets": [], "totals": {}, "models": []},
    "/api/chart/lead": LEAD, "/api/chart/cfd": CFD, "/api/profiler": {}, "/api/loop": {}, "/api/load": {}, "/api/live": {},
    "/api/slots": {}, "/api/subs": {}, "/api/models": {}, "/api/columns": {}, "/api/limits": {}, "/api/delivery": {},
    "/api/issue": {}, "/api/status": {}, "/api/leadtime": LEAD_VIEW,
}


def _serve(page_html):
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
def page_html():
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_CHECKOUT", tempfile.mkdtemp())):
        os.environ.setdefault(k, v)
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_chart_browser", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MOBILE_PAGE


def _visible(page, sel):
    return page.evaluate("""sel => {
        const e = document.querySelector(sel);
        if (!e || e.hidden) return false;
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }""", sel)


def _boot(page, base, hash_):
    page.goto(base)
    page.evaluate("h => { location.hash = h; }", hash_)
    page.reload()
    page.wait_for_function("() => !!document.querySelector('nav')")


@pytest.mark.heavy
def test_a_chart_screen_draws_landscape_and_hints_in_portrait(page_html):
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except sync.Error as e:
            pytest.skip(f"Chrome is not available: {' '.join(str(e).split())[:200]}")
        server, port = _serve(page_html)
        base = f"http://127.0.0.1:{port}/p/x/mobile"
        try:
            page = browser.new_page(viewport={"width": 844, "height": 390}, device_scale_factor=3)
            _boot(page, base, "#/now/cfd")
            page.wait_for_function("() => !!document.querySelector('#cfdplot svg')")

            # Landscape: the chart fills the viewport width, no rotate hint, and no tab bar.
            assert page.is_visible('[data-screen="cfd"]')
            assert page.eval_on_selector("#cfdplot svg", "e => e.getBoundingClientRect().width") > 800, \
                "the cumulative flow does not fill the landscape width"
            assert page.is_hidden("#cfd-hint"), "the landscape chart still shows the rotate hint"
            assert page.is_hidden("nav"), "a chart screen carries a tab bar"
            wide = page.eval_on_selector("#cfdplot svg", "e => e.getBoundingClientRect().width")

            # A tap on a day shows that day's value.
            page.click("#cfdplot rect[data-cfd-day='1']")
            assert page.is_visible("#cfdpin")
            assert page.inner_text("#cfdpintext").startswith("2026-11-19:"), page.inner_text("#cfdpintext")

            # Portrait: only the rotate hint, at the same address, and still no tab bar.
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_function("() => getComputedStyle(document.querySelector('[data-screen=\"cfd\"] .chart-body')).display === 'none'")
            assert page.is_visible("#cfd-hint"), "the portrait chart screen shows no rotate hint"
            assert not _visible(page, "#cfdplot"), "the portrait chart screen still draws the plot"
            assert page.is_hidden("nav"), "the portrait chart screen carries a tab bar"

            # A rotation measures the box again and redraws the chart at the new width.
            page.set_viewport_size({"width": 844, "height": 390})
            page.wait_for_function("() => getComputedStyle(document.querySelector('[data-screen=\"cfd\"] .chart-body')).display !== 'none'")
            page.wait_for_function(f"() => document.querySelector('#cfdplot svg').getBoundingClientRect().width > {wide - 5}")
            assert page.eval_on_selector("#cfdplot svg", "e => e.getBoundingClientRect().width") > 800, \
                "a rotation did not redraw the chart at the landscape width"

            # The lead chart draws under the same rules.
            _boot(page, base, "#/now/lead")
            page.wait_for_function("() => !!document.querySelector('#ltplot svg')")
            assert page.eval_on_selector("#ltplot svg", "e => e.getBoundingClientRect().width") > 800, \
                "the lead chart does not fill the landscape width"
            assert page.is_hidden("#lead-hint"), "the landscape lead chart still shows the rotate hint"
            assert page.is_hidden("nav"), "the lead chart screen carries a tab bar"
        finally:
            browser.close()
            server.shutdown()
