"""The mobile More tab: its read-only screens, each on its own, reading only the hub's existing GET routes (#832).

The page is read from its files and composed the way the hub composes it. No server runs. The More tab is read-only: the
page's one write is the owner's move on the Board tab (#829).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
PAGE = SCRIPTS / "hub-mobile.html"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
BOARD_FILTER_JS = SCRIPTS / "board-filter.js"
SCREEN_ROUTES = {"cost": "/api/stats", "lead": "/api/chart/lead"}   # #1028: the plans and the models left the More tab for Settings; the lead chart reads the desktop route (#1333)
PERIODS = ["hour", "day", "month"]
HUB_GET_ROUTES = {
    "/api/adapter-errors", "/api/board", "/api/chart/cfd", "/api/columns", "/api/chart/lead", "/api/delivery", "/api/ended", "/api/issue", "/api/leadtime", "/api/limits", "/api/live", "/api/load", "/api/models",
    "/api/runs", "/api/slack", "/api/slots", "/api/speed", "/api/stats", "/api/status",
    "/api/subs", "/api/version",
}   # /api/columns: the Spend & limits theme reads the card limit of each column (#2432); /api/slots: its Machine slots sub-group reads the machine slots (#2435)


def page():
    return (PAGE.read_text().replace("/*GESTURE*/", GESTURE_JS.read_text())
            .replace("/*BOARD_FILTER*/", BOARD_FILTER_JS.read_text()))


def block(html, tag, attr, name):
    m = re.search(rf'<{tag} {attr}="{name}"[^>]*>(.*?)</{tag}>', html, re.S)
    assert m, f'no <{tag} {attr}="{name}">'
    return m.group(0)


def open_tag(html, screen):
    m = re.search(rf'<\w+ data-screen="{screen}"[^>]*>', html)
    assert m, f"no screen {screen}"
    return m.group(0)


def test_more_tab_opens_its_screens_each_on_its_own():
    html = page()
    more = block(html, "section", "data-panel", "more")
    for name in SCREEN_ROUTES:
        assert f'data-more="{name}"' in more
        assert "hidden" in open_tag(html, name)


def test_each_screen_declares_its_existing_get_route():
    html = page()
    for name, route in SCREEN_ROUTES.items():
        m = re.search(r'data-src="([^"]+)"', open_tag(html, name))
        assert m and m.group(1) == route


def test_every_api_path_the_page_names_is_an_existing_get_route():
    literals = {re.sub(r"\?.*", "", p) for p in re.findall(r'"(/api/[^"]*)"', page())}
    assert literals
    assert literals <= HUB_GET_ROUTES | {"/api/promote", "/api/loop"}, literals - HUB_GET_ROUTES   # /api/loop: read and write, matched whole (ADR-0162)
    assert '"/api/promote"' in HUB_PY.read_text(), "the owner's move route the page names is not served"   # #829


def test_hub_serves_no_route_beyond_the_existing_get_routes():
    served = set(re.findall(r'self\.path\.startswith\("(/api/[\w/-]+)"\)', HUB_PY.read_text()))
    assert served == HUB_GET_ROUTES


def test_cost_screen_offers_the_hub_period_switch_as_buttons():
    cost = block(page(), "article", "data-screen", "cost")
    for period in PERIODS:
        assert re.search(rf'<button[^>]*data-period="{period}"', cost), period


def test_cost_chart_fits_the_screen():
    html = page()
    assert 'id="cchart"' in block(html, "article", "data-screen", "cost")
    assert re.search(r"#cchart svg\{[^}]*width:100%", html)


def test_more_tab_holds_no_form_no_post_and_no_write_control():
    """The More tab is read-only: no form and no write of its own. The page's one write is the owner's move on the
    Board tab, whose body and route the board suite proves (#829)."""
    more = block(page(), "section", "data-panel", "more").lower()
    for tag in ("<form", "<input", "<select", "<textarea", 'type="submit"', "type=submit"):
        assert tag not in more, tag
    assert not re.search(r"\bpost\b", more)
    assert "method" not in more and "fetch(" not in more
