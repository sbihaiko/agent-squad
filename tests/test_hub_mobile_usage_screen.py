"""Spend on the phone (SPEC 2498, #2503), booted under node over the stub DOM of the move tests. The Spend tab draws the
desktop Spend panel stacked: the summary line, Tokens over time with its hour, day and month toggle, and the model pie
with the per-model legend. Every fact is read back as the owner sees it: the text of the summary and the legend, the
fills of the slices, and the screen the old addresses open. The served numbers are the fixture's, and the expected text
is written out.

The phone cost list and the Runs Usage view left the page (#2499): the Spend tab owns cost, and every saved address of
the old view opens Spend.
"""
import json
import re

import pytest

import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import hub  # noqa: F401  (hub is a fixture)

BUCKETS = [
    {"t": "2026-11-08 09:00", "spend": 1.0, "tokens": 1000000, "runs": 1, "median_dur": None},
    {"t": "2026-11-08 10:00", "spend": 2.0, "tokens": 2000000, "runs": 2, "median_dur": None},
    {"t": "2026-11-08 11:00", "spend": 4.0, "tokens": 4000000, "runs": 3, "median_dur": None},
]
MODELS = [
    {"model": "claude-sonnet", "consumer": "loop", "month_tokens": 600000000, "spend": 9.0, "calls": 4},
    {"model": "claude-opus", "consumer": "operator", "month_tokens": 500000000, "spend": 0.0, "calls": 2},
    {"model": "deepseek-flash", "consumer": "loop", "month_tokens": 300000000, "spend": 1.0, "calls": 2},
    {"model": "claude-opus", "consumer": "loop", "month_tokens": 100000000, "spend": 5.0, "calls": 1},
]
PACE = {"median_tokens": 9100000, "active_hours": 23, "window_hours": 24, "last60_tokens": 9100000, "unpriced": 0}
TOTALS = {"today": 42.8, "month": 90.0, "all": 120.0, "unpriced": 0, "uncounted": 0,
          "tok_today": 18400000, "tok_month": 182000000, "tok_all": 400000000}
STATS = {"buckets": BUCKETS, "totals": TOTALS, "models": MODELS, "pace": PACE,
         "operator": {"tokens_today": 2000000}}
ROUTES = {"/api/stats": [{"body": json.dumps(STATS)}]}
SUMMARY = ("9.1M tokens/h median, 23 of 24 h with runs (last 24 h) · 9.1M tokens last 60 min"
           " · 18.4M loop tokens today · operator 2.0M tokens today")


def boot(hub, driver):  # noqa: F811
    return move.run_page(hub.MOBILE_PAGE, driver, routes=ROUTES)


def test_the_spend_tab_draws_the_desktop_panel_stacked(hub):  # noqa: F811
    """#2503: the Spend tab draws the desktop panel's own markup and stats, and the phone cost list is gone."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      const el = document.querySelector('[data-screen="cost"]');
      return {
        shown: !el.hidden && !document.querySelector('[data-panel="spend"]').hidden,
        tab: tabActive,
        hash: location.hash,
        day: el.querySelector('[data-period="day"]').getAttribute("aria-pressed"),
        sum: $("cost-sum").textContent,
        caption: $("cost-pie").textContent.includes("tokens · month"),
        total: $("cost-pie").textContent.includes("1.50B"),
        costList: el.querySelector(".list") === null,
        oldRows: ["This month", "All time"].map(t => el.textContent.includes(t)),
      };
    """)
    assert out == {"shown": True, "tab": "spend", "hash": "#/spend", "day": "true", "sum": SUMMARY,
                   "caption": True, "total": True, "costList": True, "oldRows": [False, False]}, out


def test_the_cost_screen_markup_is_the_desktop_panel_stacked(hub):  # noqa: F811
    """#2503: the headings and the subtitle read as the desktop panel's, the toggle sits above the bars, and the phone's
    own cost list is gone. The stub DOM parses no text node, so the static markup is read from the page the hub serves."""
    page = hub.MOBILE_PAGE
    spend = page[page.index('<section data-panel="spend"'):]
    spend = spend[:spend.index("</section>")]
    order = ["Tokens over time", 'data-period="hour"', 'data-period="day"', 'data-period="month"', 'id="cchart"',
             "Model tokens · this month", "in + out", 'id="cost-pie"']
    at = [spend.index(x) for x in order]
    assert at == sorted(at), "the panel stacks in the desktop order"
    assert 'class="list"' not in spend, "the phone cost list is gone"
    assert "Runs Usage" not in page and 'data-screen="usage"' not in page, "the Runs Usage view is gone"


def test_each_legend_row_names_its_consumer(hub):  # noqa: F811
    """#2503: each cost row says loop or operator, so a total never mixes the two in silence."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      const rows = [...$("cost-pie").querySelectorAll(".pie-row")].map(r => r.textContent);
      return { rows, labels: [...$("cost-pie").querySelectorAll(".ln")].map(e => e.textContent) };
    """)
    assert out["labels"] == ["loop · claude-sonnet", "operator · claude-opus", "loop · deepseek-flash", "loop · other · 1 model"], out
    for row in out["rows"]:
        assert row.startswith("loop · ") or row.startswith("operator · "), row


def test_the_largest_slice_and_other_have_distinct_fills(hub):  # noqa: F811
    """#2503: the two never read as one slice."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      const slices = [...$("cost-pie").querySelectorAll("circle")].map(c => ({
        key: c.dataset.slice, other: c.dataset.other, fill: c.getAttribute("stroke") }));
      return { slices };
    """)
    fills = [s["fill"] for s in out["slices"]]
    largest = out["slices"][0]
    other = [s for s in out["slices"] if s["other"] == "true"]
    assert largest["key"] == "loop · claude-sonnet", out
    assert other, "the fold slice is drawn"
    assert largest["fill"] != other[0]["fill"], out
    assert len(set(fills)) == len(fills), "two slices never share a fill"


def test_the_period_switch_changes_only_the_bars(hub):  # noqa: F811
    """#2503: the pie always covers this month, so the toggle changes the bars alone."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      const before = { bars: $("cchart").textContent, pie: $("cost-pie").textContent };
      document.querySelector('[data-screen="cost"] [data-period="month"]').click();
      await __tick(20);
      const month = document.querySelector('[data-screen="cost"] [data-period="month"]').getAttribute("aria-pressed");
      const after = { bars: $("cchart").textContent, pie: $("cost-pie").textContent };
      return { before, after, month };
    """)
    assert "08:" in out["before"]["bars"] and "4.0M" in out["before"]["bars"], out
    assert "08:" not in out["after"]["bars"], "the month toggle redraws the bars at the month scale"
    assert out["after"]["bars"] != out["before"]["bars"], "the month toggle redraws the bars"
    assert out["after"]["pie"] == out["before"]["pie"], "the month toggle leaves the pie alone"
    assert out["month"] == "true", out


def test_the_bars_name_their_peak_and_the_live_end(hub):  # noqa: F811
    """#2503: the bars carry the desktop chart's scale reading, so a spike names its size and the axis ends at now."""
    out = boot(hub, """
      tabTap("spend");
      await __tick(20);
      return { chart: $("cchart").textContent };
    """)
    assert "max 4.0M" in out["chart"], out
    assert "now" in out["chart"], out


def test_the_spend_content_clears_the_project_pill(hub):  # noqa: F811
    """#2503: the floating project pill covers the bottom 57 px, and the last cost row sat under it with no scroll room.
    The content keeps a bottom pad, so every cost row reads."""
    page = hub.MOBILE_PAGE
    rule = re.search(r'#cost-content\{[^}]*padding-bottom:\s*(\d+)px', page)
    assert rule, "the cost content declares its own bottom pad"
    assert int(rule.group(1)) >= 64, f"the pad is {rule.group(1)}px, under the pill's 57 px strip"


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


def test_the_spend_legend_dots_carry_the_bar_and_the_second_series_tokens(hub):  # noqa: F811
    """#2668: the loop dot wears the bar token and the operator dot the second series token, so the legend reads as the
    bars it names. Both tokens are DESIGN.md roles, and the legend draws no color of its own."""
    buckets = [dict(b) for b in BUCKETS]
    buckets[2]["op"] = 500000
    out = move.run_page(hub.MOBILE_PAGE, """
      tabTap("spend");
      await __tick(20);
      const lg = $("cost-chlg");
      return { hidden: lg.hidden, keys: [...lg.children].map(c => c.className) };
    """, routes={"/api/stats": [{"body": json.dumps(dict(STATS, buckets=buckets))}]})
    assert out["hidden"] is False, "an operator segment leaves its legend hidden"
    assert out["keys"] == ["k-loop", "k-operator"], out
    # the stub DOM parses no text node, so the two words are read from the served markup the legend draws
    assert '<span class="k-loop">loop</span><span class="k-operator">operator</span>' in hub.MOBILE_PAGE, out
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", hub.MOBILE_PAGE, flags=re.S))
    assert spent_rule(css, "#cost-chlg .k-loop::before").get("background") == "var(--chart-bar)", \
        "the loop dot does not wear the bar token"
    assert spent_rule(css, "#cost-chlg .k-operator::before").get("background") == "var(--chart-series-2)", \
        "the operator dot does not wear the second series token"


def spent_rule(css, selector):
    """The last declaration body the served stylesheet gives `selector`, as a dict of property to value."""
    bodies = [dict(p.split(":", 1) for p in body.split(";") if ":" in p)
              for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
              if selector in [s.strip() for s in sel.split(",")]]
    assert bodies, f"the served page has no rule for {selector}"
    return {k.strip(): v.strip() for k, v in bodies[-1].items()}


def test_the_today_line_on_now_opens_spend(hub):  # noqa: F811
    """The Today line says the day's spend and hands the detail to the tab that owns it."""
    out = boot(hub, """
      await loadNowToday();
      const label = $("now-today").textContent;
      await $("now-today").onclick();
      return { label, tab: tabActive, shown: !document.querySelector('[data-screen="cost"]').hidden };
    """)
    assert out == {"label": "Today · 18.4M tokens · $42.80 · +2.0M operator", "tab": "spend", "shown": True}, out
