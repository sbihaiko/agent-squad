"""The top of the phone's Now reads as finished (SPEC 2660, #2664): the Today control is a panel with a thumb target and
a trailing chevron, the header status takes a full second line with no ellipsis, and an empty loops message takes no
height.

The styles are read from the page the hub serves, comments stripped; the behavior runs that served page under node over
the stub DOM of `tests/hub_mobile_stub.py`, which answers `/api/stats` and `/api/hub`.
"""
import json
import re

import pytest

from tests.hub_mobile_stub import hub, run_page  # noqa: F401  (hub is a fixture)

DAY = {"totals": {"tok_today": 1_000_000, "today": 1.45}, "operator": {"tokens_today": 0}}
LONG_ALERT = "tokens 6.4M/24h · 79 over 5M"
ROUTES = {
    "/api/stats": [{"body": json.dumps(DAY)}],
    "/api/hub": [{"body": json.dumps({"repos": [{"slug": "x", "repo": "x/y", "live": [], "alert": LONG_ALERT}]})}],
    "/api/loop": [{"body": json.dumps({"projects": [], "changes": []})}],
}


def _style(page):
    return re.sub(r"/\*.*?\*/", "", page[:page.index("</style>")], flags=re.S)


def _decls(page, selector):
    css = _style(page)
    out = [m.group(2) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
           if selector in [s.strip() for s in m.group(1).split(",")]]
    return ";".join(out)


def test_the_today_control_is_a_44px_panel(hub):  # noqa: F811
    decls = _decls(hub.MOBILE_PAGE, "#now-today")
    m = re.search(r"min-height:(\d+)px", decls)
    assert m and int(m.group(1)) >= 44, f"the Today control has no 44 px target: {decls}"
    assert "background:var(--panel)" in decls, "the Today control has no panel surface"
    assert "border-radius:var(--radius-md)" in decls, "the Today control has no system radius"
    assert re.search(r"font:var\(--fs-body\)", decls), "the Today control is not in the body face"


def test_the_today_control_carries_a_trailing_chevron(hub):  # noqa: F811
    btn = re.search(r'<button id="now-today".*?</button>', hub.MOBILE_PAGE, re.S).group(0)
    assert re.search(r'<span id="now-today-txt"></span>', btn), "the day line has no text node"
    assert re.search(r'<svg class="ico".*?</svg>\s*</button>', btn, re.S), "the chevron does not trail the day line"


@pytest.mark.node_bound
def test_the_today_control_shows_the_day_and_keeps_its_chevron(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadNowToday();
      const b = $("now-today");
      return { hidden: b.hidden, label: $("now-today-txt").textContent,
               chevrons: b.querySelectorAll("svg").length };
    """, routes=ROUTES)
    assert out["hidden"] is False, "the Today control stays hidden after the day read"
    assert out["label"] == "Today · 1.0M tokens · $1.45", out
    assert out["chevrons"] == 1, "the day read wiped the chevron"


@pytest.mark.node_bound
def test_the_today_control_opens_the_day_detail(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadNowToday();
      $("now-today").click();
      return { tab: tabActive, shown: !document.querySelector('[data-screen="cost"]').hidden };
    """, routes=ROUTES)
    assert out == {"tab": "spend", "shown": True}, out


def test_the_header_status_takes_a_full_second_line_with_no_ellipsis(hub):  # noqa: F811
    page = hub.MOBILE_PAGE
    assert "flex-wrap:wrap" in _decls(page, "#profile"), "the profile line cannot wrap"
    status = _decls(page, "#profile span")
    assert re.search(r"flex:1 0 100%|flex-basis:100%", status), "the status does not take a full line"
    assert "white-space:normal" in status, "the status never wraps"
    assert "text-overflow:ellipsis" not in status and "overflow:hidden" not in status, "the status is still cut off"
    assert "text-overflow:ellipsis" not in _decls(page, "#pswm"), "the status carries an ellipsis"


@pytest.mark.node_bound
def test_a_long_header_status_is_written_whole(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      return { status: $("pswm").textContent };
    """, routes=ROUTES)
    assert out["status"] == "none live · alert: " + LONG_ALERT, out


def test_an_empty_loops_message_takes_no_height(hub):  # noqa: F811
    decls = _decls(hub.MOBILE_PAGE, "#loops-msg:empty")
    assert "display:none" in decls, f"an empty loops message still takes height: {decls}"
