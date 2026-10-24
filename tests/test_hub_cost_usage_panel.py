"""Hub page: cost & usage panel regrouped for a 300 px sidebar (#1497)."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

HUB = pathlib.Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub.py"
SRC = HUB.read_text()
PAGE = SRC[SRC.index("PAGE = r\"\"\""): SRC.index("PAGE = PAGE.replace")]
POLL = SRC[SRC.index("async function pollStats"): SRC.index("pollStats();setInterval")]


def _panel(pid):
    m = re.search(rf"<details id={pid} .*?</details>", SRC, re.S)
    assert m, f"panel {pid} missing"
    return m.group(0)


def _stats():
    return _panel("stats")


def _fn(src, name):
    i = src.index(f"function {name}(")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i : k + 1]
    raise AssertionError(f"function {name} not closed")


def _const(src, name):
    m = re.search(rf"const {name}=([^;]+);", src)
    assert m, f"const {name} missing"
    return f"const {name}={m.group(1)};"


def _stmt(marker):
    """The single statement the page assigns after `marker`, as an expression."""
    i = SRC.index(marker) + len(marker)
    return SRC[i: SRC.index(";\n", i)]


def _prelude():
    usd = re.search(r"const usd=v=>[^;]+;", SRC).group(0)
    usdc = re.search(r"const usdc=v=>[^;]+;", SRC)
    pcol = re.search(r"const PCOL=[^;]+;", SRC).group(0)
    other = re.search(r"const OTHER=[^;]+;", SRC)
    if "tk=" not in pcol:
        tk = re.search(r"(?:const )?tk=n=>[^;]+;", SRC).group(0)
        if not tk.startswith("const "):
            tk = "const " + tk
        pcol = pcol + "\n" + tk
    esc = "const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));"
    return "\n".join([
        usd, usdc.group(0) if usdc else "", pcol, other.group(0) if other else "", esc,
        _fn(SRC, "bars"), _fn(SRC, "pie"), _fn(SRC, "kr"), _fn(SRC, "openRows"),
    ])


def _node(js):
    node = shutil.which("node")
    assert node, "node is required to evaluate hub panel JS"
    p = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr or p.stdout
    return p.stdout


def _render(prelude, expr):
    """Evaluate one assignment's expression in node, with the panel helpers in scope."""
    return _node(_prelude() + "\n" + prelude + f"\nconsole.log({expr});")


def _cash_line():
    return _render("const any=true,d={plans:255,api:27.24,total:282.24};",
                   _stmt('$("#cash").textContent=')).strip()


def usd_texts(html):
    return re.findall(r"<b[^>]*>(\$[\d.,]+)</b>", html)


def _run_js(body):
    return json.loads(_node(_prelude() + "\n" + body + "\nconsole.log(JSON.stringify(out));"))


def test_subscription_card_has_no_info_lines():
    card = SRC[SRC.index("return `<div class=sub>"):]
    card = card[: card.index('}).join("")')]
    for label in ("quota value used", "plan paid so far", "API-equivalent (squad only)"):
        assert label not in card, label
    assert "class=kvs" not in card


def test_totals_tiles_live_in_cost_panel_not_subscriptions():
    assert "tot3" not in _panel("plans")
    assert "id=tot3" not in _panel("stats")
    poll = SRC[SRC.index("async function pollSubs"):]
    poll = poll[: poll.index("const SUBL") if "const SUBL" in poll else len(poll)]
    assert '$("#subs").innerHTML=`<div class=tot3>' not in poll
    assert "$(\"#tot3\")" not in SRC


def test_cost_panel_is_named_cost_and_usage():
    assert "<summary>cost &amp; usage<" in _panel("stats")
    assert "cost &amp; speed" not in SRC and "cost & speed" not in SRC


def test_no_tot3_tile_grid():
    html = _stats()
    assert "id=tot3" not in html and "class=tot3" not in html
    assert re.search(r"<div class=tot id=tot>", html)


def test_section_order_is_spend_chart_speed_open_tokens():
    html = _stats()
    pos = [html.find("Spend"), html.find("Spend over time"), html.find("id=chart"),
           html.find("id=kpi"), html.find("id=open"), html.find("Tokens by model"), html.find("id=pie")]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos), pos


def test_spend_block_holds_the_cash_line():
    html = _stats()
    assert re.search(r"<h4>Spend\b", html)
    cash_at = html.index("id=cash")
    assert html.index("id=tot") < cash_at < html.index("id=per")
    txt = _cash_line()
    assert re.match(r"^at API list prices \(API-equivalent\) · cash this month: plans \$\d.*\+ API \$\d.*= \$\d", txt), txt


def test_src_toggle_reads_all_plans_api():
    html = _render(
        'const src="both";\n',
        _stmt('$("#src").innerHTML='),
    )
    assert re.findall(r">([^<>]+)</button>", html) == ["all", "plans", "API"]


def test_per_toggle_reads_per_hour_day_month():
    html = _render(
        'const per="day";\n',
        _stmt('$("#per").innerHTML='),
    )
    assert re.findall(r">([^<>]+)</button>", html) == ["per hour", "per day", "per month"]
    # the API still keys off hour/day/month
    assert re.findall(r"data-p=(\w+)", html) == ["hour", "day", "month"]
    assert re.search(r'data-p=day class="on"', html)


def test_tot_tiles_read_today_this_month_all_time():
    html = _render("const st={totals:{today:20.67,month:612,all:1234}};\nconst same=st.totals.all===st.totals.month;\n",
                   _stmt('$("#tot").innerHTML='))
    assert re.findall(r"<div>([^<]+)<b", html) == ["today", "this month", "all time"]
    assert usd_texts(html) == ["$20.67", "$612", "$1234"]


def test_tot_all_time_equal_to_month_reads_this_month():
    html = _render("const st={totals:{today:1.5,month:612,all:612}};\nconst same=st.totals.all===st.totals.month;\n",
                   _stmt('$("#tot").innerHTML='))
    tiles = re.findall(r"<div>[^<]+<b([^>]*)>([^<]+)</b></div>", html)
    assert tiles[-1] == (" class=m", "= this month"), tiles
    assert tiles[1] == ("", "$612")
    rule = re.search(r"\.tot b\.m\{([^}]*)\}", SRC)
    assert rule and "color:var(--mute)" in rule.group(1), rule
    assert "font-size:15px" not in rule.group(1)  # "= this month" is text, not a value: it must fit the tile


def test_dollars_drop_cents_at_and_above_one_hundred():
    html = _render("const st={totals:{today:99.99,month:100,all:1234.4}};\nconst same=st.totals.all===st.totals.month;\n",
                   _stmt('$("#tot").innerHTML='))
    assert usd_texts(html) == ["$99.99", "$100", "$1234"]


def test_chart_has_max_rule_ok_bar_now_and_no_year():
    out = _run_js("""
const rows=[{t:'2026-10-09',spend:2,runs:1},{t:'2026-10-10',spend:14.28,runs:2},{t:'2026-10-11',spend:5,runs:1}];
const html=bars(rows);
const texts=[...html.matchAll(/<text[^>]*>([^<]*)<\\/text>/g)].map(m=>m[1]);
const out={html, texts};
""")
    html = out["html"]
    assert "max $14.28" in html
    assert re.search(r'stroke-dasharray', html)
    assert re.search(r'fill="(?:var\(--ok\)|#3ecf8e)"', html)
    assert out["texts"] and out["texts"][-1].endswith("now")
    for t in out["texts"]:
        assert not re.search(r"\b\d{4}\b", t), t


SPEED = """const b={cutoff:"2026-10-09T19:04",pr:{median:38,mean:41},run_median:64};
const n=42,cut=b.cutoff.slice(5,16).replace("T"," ");
const sp={baseline:b,pr:{after:{n:n,median:18,mean:22},stale:false},runs:{today_median:12},rounds:{mean:1.5},kills:{count:0}};
"""


def _speed_html(note="42"):
    return _render(SPEED, _stmt('$("#kpi").innerHTML='))


def _rows(html):
    """Each row's visible text, label column first."""
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m)).strip()
            for m in re.findall(r"<div class=kr>.*?</div>", html)]


def test_speed_header_reads_since_and_pr_count():
    header = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r"<h4>Speed <small>.*?</small></h4>", _speed_html()).group(0))).strip()
    assert re.fullmatch(r"Speed since \d\d-\d\d \d\d:\d\d UTC · \d+ PRs?", header), header


def test_speed_header_says_one_pr_in_the_singular():
    html = _render(SPEED.replace("after:{n:n,", "after:{n:1,").replace("const n=42,", "const n=1,"), _stmt('$("#kpi").innerHTML='))
    assert re.search(r"· 1 PR</small>", html), html


def test_speed_rows_are_the_five_the_issue_names():
    html = _speed_html()
    assert "sample size" not in html and "since then" not in html
    assert _rows(html) == [
        "Merge time , median 18 min was 38 min · 53% faster",
        "Merge time , average 22 min was 41 min · 46% faster",
        "Run time , median today 12 min was 64 min · 81% faster",
        "Review rounds , average 1.5",
        "Hung runs killed 0",
    ], _rows(html)


def test_speed_row_delta_is_muted_text_with_only_the_percentage_coloured():
    html = _speed_html()
    delta = re.search(r'<span class=d>(.*?)</span></div>', html).group(1)
    assert re.sub(r"<[^>]+>", "", delta) == "was 38 min · 53% faster"
    assert "background" not in delta
    pct = re.search(r'<span class="(good|bad)">([^<]+)</span>', delta)
    assert pct and pct.group(2) == "53% faster"
    assert not re.search(r'class="d (?:good|bad)"', html)


def test_speed_zero_value_is_muted():
    html = _speed_html()
    assert re.search(r"<b class=m>0</b>", html), html


def test_speed_keeps_the_provisional_note():
    html = _render(SPEED.replace("after:{n:n,", "after:{n:4,").replace("const n=42,", "const n=4,"), _stmt('$("#kpi").innerHTML='))
    assert "Provisional: only 4 PRs so far" in html and "firms up at ~10" in html


def test_open_rows_read_number_and_minutes():
    out = _run_js("const o=[{n:1491,min:28},{n:1502,min:31}];const out={html:openRows(o)};")
    html = out["html"]
    assert re.search(r"<h4>Open branches <small>oldest first</small></h4>", html)
    assert re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip() == "Open branches oldest first #1491 · 28 min (warns at 30) #1502 · 31 min"


def test_open_with_none_reads_none_open():
    out = _run_js("const out={empty:openRows([]),missing:openRows(null)};")
    for html in (out["empty"], out["missing"]):
        assert re.search(r"<h4>Open branches\b", html) and "none open" in html
        assert "#" not in html
    assert "Open now" not in SRC


def test_tokens_by_model_is_always_expanded():
    html = _stats()
    pie_wrap = html[html.index("id=pie") - 120 : html.index("id=pie") + 40]
    assert "<details" not in pie_wrap and "<summary" not in pie_wrap
    assert "Tokens by model" in html
    assert re.search(r"<h4>Tokens by model <small class=m id=piet>all time</small></h4>", html)


def test_tokens_legend_folds_under_one_percent_and_caps_six_rows():
    out = _run_js("""
const models=[
  {model:'big',in:9000,out:0,cache:0,spend:1,calls:1},
  {model:'mid',in:500,out:0,cache:0,spend:1,calls:1},
  {model:'a',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'b',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'c',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'d',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'e',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'f',in:20,out:0,cache:0,spend:1,calls:1},
  {model:'g',in:20,out:0,cache:0,spend:1,calls:1},
];
const html=pie(models);
const legend=[...html.matchAll(/<span class=ln>([^<]+)<\\/span>/g)].map(m=>m[1]);
const shares=[...html.matchAll(/<span class=ln>[^<]+<\\/span><b>([^<]+)<\\/b>/g)].map(m=>m[1]);
const slices=[...html.matchAll(/<path[^>]*fill="([^"]+)"/g)].map(m=>m[1]);
const out={html, legend, shares, slices, n:legend.length, other:(legend.find(x=>x.startsWith('other'))||'')};
""")
    assert out["n"] == 3, out["legend"]  # big, mid, other
    assert out["other"] == "other · 7 models", out["legend"]
    assert len(out["slices"]) == out["n"], out["slices"]  # one slice per legend row
    assert out["shares"] == ["93%", "5%", "1%"], out["shares"]
    for row in out["legend"]:
        if row.startswith("other"):
            continue
        assert row not in list("abcdefg"), row  # no named row under 1 %
    assert out["n"] <= 6


def test_tokens_other_slice_tooltip_lists_each_folded_model():
    out = _run_js("""
const models=[
  {model:'big',in:9900,out:0,cache:0,spend:1,calls:1},
  {model:'small-a',in:60,out:0,cache:0,spend:1,calls:1},
  {model:'small-b',in:40,out:0,cache:0,spend:1,calls:1},
];
const html=pie(models);
const titles=[...html.matchAll(/<title>([^<]*)<\\/title>/g)].map(m=>m[1]);
const out={legend:[...html.matchAll(/<span class=ln>([^<]+)<\\/span>/g)].map(m=>m[1]).find(x=>x.startsWith('other'))||'', tip:titles.find(t=>t.includes('small-a'))||''};
""")
    assert out["legend"] == "other · 2 models", out
    assert "small-a: 0.60%" in out["tip"] and "small-b: 0.40%" in out["tip"], out["tip"]


def test_tokens_donut_centre_reads_the_total_and_tokens():
    out = _run_js("const out={html:pie([{model:'m',in:1690000000,out:0,cache:0,spend:1,calls:1}])};")
    assert re.search(r">1\.69B</text>", out["html"]), out["html"]
    assert re.search(r">tokens</text>", out["html"])


def test_tokens_header_small_text_reads_the_total():
    html = _render("const st={models:[{in:1200000000,out:490000000,cache:0,spend:1,calls:1}]};",
                   _stmt('$("#piet").textContent='))
    assert re.fullmatch(r"[\d.]+[KMB] all time", html.strip()), html


def test_closed_summary_reads_the_month_cash():
    txt = _render("const d={plans:254.99,api:27.24,total:282.23};",
                  _stmt('$("#stsr").textContent=')).strip()
    assert re.fullmatch(r"month \$[\d.,]+", txt), txt
    assert txt == "month $282.23", txt
    assert "today" not in txt


def test_closed_summary_reads_the_api_part_without_a_plan_price():
    txt = _render("const d={plans:0,api:27.24,total:27.24};",
                  _stmt('$("#stsr").textContent=')).strip()
    assert txt == "month $27.24", txt
    assert "today" not in txt


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_named_constant_folds_models_under_one_percent():
    assert re.search(r"const OTHER=0\.01", SRC)


# ---- the subscriptions panel renders with a quota probe that has no windows (#1512) ----

_PRE = """
const __els = {};
const $ = s => (__els[s] = __els[s] || { textContent: "", innerHTML: "", addEventListener(){},
  querySelector(){return null}, querySelectorAll(){return []}, closest(){return null},
  classList:{toggle(){}}, dataset:{} });
globalThis.__els = __els;
globalThis.localStorage = { getItem(){return null}, setItem(){} };
const ls = { acc: "" };
globalThis.document = { activeElement: null, querySelector: $, querySelectorAll: () => [],
  addEventListener(){}, body:{classList:{add(){},remove(){}}} };
globalThis.window = globalThis;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
globalThis.location = { hash: "" };
"""

_POST = """
const PAYLOAD = { frac: 0.34, plans: 255, api: 28, total: 283,
  presets: { grok: { g1: { name: "SuperGrok", usd: 30 } }, claude: { c1: { name: "Max 20x", usd: 200 } } },
  subs: {
    grok:  { name: "SuperGrok", saved: null, usd: null, rate: 1, noRate: false, eq: 0, plan: null,
             quota: { unavailable: true, err: "cli probe failed", asof: 1 } },
    claude:{ name: "Claude Max 20x", saved: 10, usd: 200, rate: 1, noRate: false, eq: 250,
             plan: { plan: "max20", amount: 200, currency: "USD" },
             quota: { windows: [ { label: "weekly", pct: 77, resets: 2 } ], asof: 1 } } } };
globalThis.fetch = async () => ({ json: async () => PAYLOAD, ok: true });
pollSubs().then(() => console.log("RESULT" + JSON.stringify(
  { subs: (__els["#subs"] || {}).innerHTML || "", err: null })))
  .catch(e => console.log("RESULT" + JSON.stringify({ err: String(e && e.stack || e) })));
"""


def _subs_slice():
    """The page slice holding pollSubs and every helper it reaches, plus the `esc` defined after it."""
    i = SRC.index("const usd=v=>")
    j = SRC.index("async function pollStats(")
    esc = SRC[SRC.index("const esc=t=>"):]
    return SRC[i:j].replace("__CAPS__", "{}") + "\n" + esc[: esc.index("\n")] + "\n"


def test_a_quota_probe_with_no_windows_still_renders_the_subscriptions_panel():
    """#1512: a provider's quota probe answers `unavailable` with no `windows` at all (grok did),
    and `aq.windows[0]` threw inside pollSubs. The throw is caught, so the page kept its header
    totals while `#subs` stayed empty: the group opened onto nothing and read as broken."""
    out = json.loads(_node(_PRE + _subs_slice() + _POST).split("RESULT")[-1])
    assert not out["err"], out["err"]
    assert "SuperGrok" in out["subs"] and "unavailable" in out["subs"], out["subs"][:300]
    assert "Claude Max 20x" in out["subs"] and "Claude Team" not in out["subs"], out["subs"][:300]   # the name comes from the row (#511)
    assert "77%" in out["subs"], out["subs"][:300]


def test_the_pie_merges_the_spellings_of_one_model_name():
    """One model answers under several spellings (deepseek-flash, deepseek-flash[1m], claude-deepseek-flash), so keying
    the slices on the raw id drew the same label three times. The rows merge by the name, and the tooltip keeps every
    spelling that went into the slice."""
    body = _fn(SRC, "pie")
    assert "byName" in body and "byName.set(k,r)" in body, body
    assert "ids.join" in body and "ids.includes(m.model)" in body, body


def test_a_floor_note_names_unpriced_and_uncounted_review_calls():
    """The totals are a floor: the note beside the tiles says how many calls priced at 0.0 or had no usage count (#789)."""
    html = _render("const st={totals:{today:1,month:2,all:2,unpriced:2,uncounted:1}};\n",
                   _stmt('$("#totnote").innerHTML='))
    assert "2" in html and "unpriced" in html and "1" in html and "no usage" in html and "floor" in html, html
    clean = _render("const st={totals:{today:1,month:2,all:2,unpriced:0,uncounted:0}};\n",
                    _stmt('$("#totnote").innerHTML='))
    assert clean.strip() in ("", "''", '""'), clean
