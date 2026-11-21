"""Spend and the Today line of Now on the phone (SPEC 2498, #2499), booted under node over the stub DOM of the move
tests. Every fact is read back as the owner sees it: the tab the old addresses open, the screen the cost address draws,
and the screen the Today line opens. The served numbers are the fixture's, and the expected text is written out.

The Runs Usage view left the page (#2499): the Spend tab owns cost, and every saved address of the old view opens Spend.
"""
import json
import re

import pytest

import tests.test_hub_mobile_move as move
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is a fixture)

BUCKETS = [
    {"t": "2026-11-08 09:00", "spend": 1.0, "tokens": 1000000, "runs": 1, "median_dur": None},
    {"t": "2026-11-08 10:00", "spend": 2.0, "tokens": 2000000, "runs": 2, "median_dur": None},
    {"t": "2026-11-08 11:00", "spend": 4.0, "tokens": 4000000, "runs": 3, "median_dur": None},
]
MODELS = [
    {"model": "claude-sonnet", "month_tokens": 600, "spend": 9.0, "calls": 4},
    {"model": "deepseek-flash", "month_tokens": 300, "spend": 1.0, "calls": 2},
    {"model": "claude-opus", "month_tokens": 100, "spend": 5.0, "calls": 1},
]
TOTALS = {"today": 42.8, "month": 90.0, "all": 120.0, "unpriced": 0, "uncounted": 0,
          "tok_today": 18400000, "tok_month": 182000000, "tok_all": 400000000}
STATS = {"buckets": BUCKETS, "totals": TOTALS, "models": MODELS, "pace": None}
ROUTES = {"/api/stats": [{"body": json.dumps(STATS)}]}


def boot(hub, driver):  # noqa: F811
    return move.run_page(hub.MOBILE_PAGE, driver, routes=ROUTES)


def test_the_spend_tab_draws_the_cost_screen_for_a_day(hub):  # noqa: F811
    """SPEC 2498 (#2499): the Spend tab draws the cost screen, and the day period is the screen's first view."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      const el = document.querySelector('[data-screen="cost"]');
      return {
        shown: !el.hidden && !document.querySelector('[data-panel="spend"]').hidden,
        tab: tabActive,
        hash: location.hash,
        day: el.querySelector('[data-period="day"]').getAttribute("aria-pressed"),
        models: ["claude-sonnet", "deepseek-flash", "claude-opus"].map(m => el.textContent.includes(m)),
      };
    """)
    assert out == {"shown": True, "tab": "spend", "hash": "#/spend", "day": "true", "models": [True, True, True]}, out


def test_the_old_usage_addresses_open_spend_and_replace_the_entry(hub):  # noqa: F811
    """SPEC 2498 (#2499): `usage` and `tokens` are legacy — both open Spend, and the entry is replaced, so Back never
    returns to the removed address."""
    out = boot(hub, """
      return {
        usage: routeFromHash("#/now/usage"),
        tokens: routeFromHash("#/runs/tokens"),
        runs: routeFromHash("#/runs/usage"),
      };
    """)
    assert out["usage"] == {"tab": "spend", "layer": None, "data": {}, "replace": True}, out
    assert out["tokens"] == {"tab": "spend", "layer": None, "data": {}, "replace": True}, out
    assert out["runs"] == {"tab": "spend", "layer": None, "data": {}, "replace": True}, out


@pytest.mark.node_bound
def test_the_page_boots_with_no_cost_markup(hub):  # noqa: F811
    """A page served without the cost screen still boots: every cost read finds no element and does no work (#2499)."""
    page = re.sub(r'<article data-screen="cost".*?</article>', "", hub.MOBILE_PAGE, flags=re.S)
    out = move.run_page(page, "return { booted: true };", routes=ROUTES)
    assert out == {"booted": True}, out


def test_the_today_line_on_now_opens_spend(hub):  # noqa: F811
    """The Today line says the day's spend and hands the detail to the tab that owns it."""
    out = boot(hub, """
      await loadNowToday();
      const label = $("now-today").textContent;
      await $("now-today").onclick();
      return { label, tab: tabActive, shown: !document.querySelector('[data-screen="cost"]').hidden };
    """)
    assert out == {"label": "Today · 18.4M tokens · $42.80", "tab": "spend", "shown": True}, out
