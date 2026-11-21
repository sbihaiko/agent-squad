"""The phone's landscape polish (#2669): the Cumulative Flow content takes its left gutter from the page, and the Loop
chart draws one title, never the page's own Loop heading and a second uppercase kicker over it.

Each test reads the page the hub serves (`MOBILE_PAGE`, what `/mobile` returns). The gutter is the page's own CSS
declaration, resolved to the value the page computes for Spend and Lead Time. The profiler title is the page's own
render, drawn at the phone width through the served page's controller. DESIGN.md holds the colors: this ticket adds no
raw color, only the shared gutter and one fewer heading (ADR-0137 keeps the render free of file and service reads).
"""
import json
import re

import pytest

from tests.hub_mobile_stub import HUB_PY, _module, run_page
from tests.test_hub_mobile_profiler import PHONE_TRIP_VIEW


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_STATE", "SG_PROFILES_DIR"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_mobile_landscape_polish", HUB_PY)


def _rule_blocks(page):
    """Every `selector { decls }` block of the page's own `<style>` CSS, in document order, as (selectors, decls)."""
    css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        yield [s.strip() for s in m.group(1).split(",")], m.group(2)


def _content_left_pad(page, selector):
    """The left padding the page resolves for one content box: the last `padding-left`, else the shorthand's left side."""
    left = None
    for selectors, decls in _rule_blocks(page):
        if selector not in selectors:
            continue
        for decl in decls.split(";"):
            if ":" not in decl:
                continue
            prop, val = (part.strip() for part in decl.split(":", 1))
            if prop == "padding-left":
                left = val
            elif prop == "padding":
                parts = val.split()
                if len(parts) >= 4:
                    left = parts[3]
                elif len(parts) >= 2:
                    left = parts[1]
                elif parts:
                    left = parts[0]
    return left


def test_the_landscape_cfd_takes_the_same_left_gutter_as_spend_and_lead(hub):
    """One page gutter: the CFD content starts where the Spend and the Lead Time content start, never at the screen edge."""
    page = hub.MOBILE_PAGE
    lead = _content_left_pad(page, "#lead-content")
    spend = _content_left_pad(page, "#cost-content")
    cfd = _content_left_pad(page, "#cfd-content")
    assert lead and lead == spend, f"Spend and Lead Time disagree on the gutter: {spend!r} vs {lead!r}"
    assert cfd == lead, f"the CFD content keeps its own gutter: {cfd!r}, not the Spend and Lead Time {lead!r}"
    assert "var(--page-gutter-phone)" in cfd, f"the CFD gutter is not the page's own gutter: {cfd!r}"


@pytest.mark.node_bound
def test_the_served_profiler_draws_one_loop_title_and_no_eyebrow_kicker(hub):
    """The page's header names the chart Loop; the body adds no second uppercase Loop title and no eyebrow kicker."""
    page = hub.MOBILE_PAGE
    article = re.search(r'<article id="profpage"[^>]*>(.*?)</article>', page, re.S)
    assert article and "<h2>Loop</h2>" in article.group(1), "the served page loses its own Loop chart header"
    view = json.dumps(PHONE_TRIP_VIEW)
    driver = (
        f"profV = {view}; profPhone = true; profDraw();\n"
        "return { body: document.getElementById('profb').innerHTML,"
        f" desktop: profHtml({view}, new Set(), false) }};\n"
    )
    out = run_page(page, driver)
    assert out["body"], "the profiler drew no body"
    assert "<h2>Loop</h2>" not in out["body"], "the profiler body repeats the page's Loop title"
    assert "<div class=lph><h2" not in out["body"], "the profiler carries an uppercase eyebrow kicker over the chart"
    assert "<h2>Loop</h2>" in out["desktop"], "the desktop lost its one Loop section title"
