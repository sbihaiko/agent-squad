"""Runs › Usage and the Today line of Now on the phone (SPEC 1596, #1604), booted under node over the stub DOM of the move
tests. Every fact is read back as the owner sees it: the bars drawn, the legend under the pie, the period the owner
picked, and the screen the Today line opens. The served numbers are the fixture's, and the expected text is written out.
"""
import json

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
LEGEND = ["claude-sonnet · 60% · 600 tokens", "deepseek-flash · 30% · 300 tokens", "claude-opus · 10% · 100 tokens"]


def boot(hub, driver):  # noqa: F811
    return move.run_page(hub.MOBILE_PAGE, driver, routes=ROUTES)


def test_runs_usage_draws_one_bar_per_bucket_and_the_model_legend(hub):  # noqa: F811
    out = boot(hub, """
      tabTap("runs");
      $("runs-usage").onclick();
      await loadUsage();
      return {
        shown: !$("usage-screen").hidden,
        bars: $("usage-bars").querySelectorAll("rect").length,
        legend: $("usage-legend").children.map(c => c.textContent),
      };
    """)
    assert out["shown"], out
    assert out["bars"] == 3, out
    assert out["legend"] == LEGEND, out


def test_the_phone_draws_the_operator_segment_in_the_charts_own_colour(hub):  # noqa: F811
    """#1756: the phone and the desktop name the operator consumer in one colour, so the two readings agree."""
    stats = dict(STATS, buckets=[{"t": "2026-11-08 10:00", "spend": 2.0, "tokens": 1000000, "op": 3000000, "runs": 1, "median_dur": None}])
    out = move.run_page(hub.MOBILE_PAGE, """
      await openLayer("usage");
      await loadUsage();
      const rs = [...$("usage-bars").querySelectorAll("rect")];
      return {fills: rs.map(r => r.getAttribute("fill")), titles: rs.map(r => r.querySelector("title").textContent)};
    """, routes={"/api/stats": [{"body": json.dumps(stats)}]})
    assert out["fills"] == ["var(--acc)", "var(--chart-series-2)"], out
    assert "operator" in out["titles"][1], out


def test_the_period_buttons_select_hour_day_or_month_and_mark_the_one_picked(hub):  # noqa: F811
    out = boot(hub, """
      await openLayer("usage");
      await loadUsage();
      await $("usage-screen").querySelector('[data-usage-period="hour"]').onclick();
      return {
        hour: $("usage-screen").querySelector('[data-usage-period="hour"]').getAttribute("aria-pressed"),
        day: $("usage-screen").querySelector('[data-usage-period="day"]').getAttribute("aria-pressed"),
        month: $("usage-screen").querySelector('[data-usage-period="month"]').getAttribute("aria-pressed"),
        period: usageState.period,
      };
    """)
    assert out == {"hour": "true", "day": "false", "month": "false", "period": "hour"}, out


def test_the_runs_usage_screen_has_its_own_hash_and_cost_and_usage_keeps_usage(hub):  # noqa: F811
    # `/usage` stays the More › Cost and usage screen it has always named, so an old address opens the same screen; the
    # runs screen takes a token of its own, and its round trip through the address lands back on itself (ADR-0137 debt).
    out = boot(hub, """
      return {
        runs: routeHash({ tab: "runs", layer: "usage" }),
        back: routeFromHash(routeHash({ tab: "runs", layer: "usage" })),
        cost: routeFromHash("#/now/usage"),
        legacy: routeFromHash("#/runs/usage"),
      };
    """)
    assert out["runs"] == "#/runs/tokens", out
    assert out["back"] == {"tab": "runs", "layer": "usage", "data": {}}, out
    assert out["cost"] == {"tab": "now", "layer": "cost", "data": {}}, out
    assert out["legacy"] == {"tab": "runs", "layer": "cost", "data": {}}, out


def test_the_today_line_on_now_opens_runs_usage(hub):  # noqa: F811
    out = boot(hub, """
      await loadNowToday();
      const label = $("now-today").textContent;
      await $("now-today").onclick();
      return { label, tab: tabActive, shown: !$("usage-screen").hidden };
    """)
    assert out == {"label": "Today · 18.4M tokens · $42.80", "tab": "runs", "shown": True}, out
