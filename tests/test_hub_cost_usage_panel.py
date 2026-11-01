"""Hub page: cost & usage panel regrouped for a 300 px sidebar (#1497)."""
import json
import pathlib
import re
import shutil
import subprocess

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


def _usage():
    """The Usage panel: the subscription gauges, split out of Cost & usage (owner, 2026-11-01)."""
    return _panel("usage")


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
    top = re.search(r"const TOP=[^;]+;", SRC)
    other = re.search(r"const OTHER=[^;]+;", SRC)
    if "tk=" not in pcol:
        tk = re.search(r"(?:const )?tk=n=>[^;]+;", SRC).group(0)
        if not tk.startswith("const "):
            tk = "const " + tk
        pcol = pcol + "\n" + tk
    esc = "const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));"
    return "\n".join([
        usd, usdc.group(0) if usdc else "", pcol, other.group(0) if other else "", top.group(0) if top else "", esc,
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
    assert "tot3" not in _panel("sub")   # re-anchored: the plans panel moved to Settings > Subscriptions (#1028)
    assert "id=tot3" not in _panel("stats")
    poll = SRC[SRC.index("async function pollSubs"):]
    poll = poll[: poll.index("const SUBL") if "const SUBL" in poll else len(poll)]
    assert '$("#subs").innerHTML=`<div class=tot3>' not in poll
    assert "$(\"#tot3\")" not in SRC


def test_cost_and_usage_is_two_panels_usage_and_spend():
    """Owner, 2026-11-01: Cost & usage splits in two. Usage holds the subscription gauges; Spend holds the spend tiles, the
    spend chart and the token ring."""
    assert "<summary>Usage<" in _usage(), _usage()[:200]
    assert "<summary>Spend<" in _stats(), _stats()[:200]
    assert "Cost &amp; usage" not in SRC and "cost &amp; speed" not in SRC and "cost & speed" not in SRC


def test_no_tot3_tile_grid():
    html = _stats()
    assert "id=tot3" not in html and "class=tot3" not in html
    assert re.search(r"<div class=tot id=tot>", html)


def test_section_order_is_gauges_in_usage_and_tiles_chart_tokens_in_spend():
    """Owner, 2026-11-01: the speed block and the cash line leave the panel, and the token ring moves out of Usage into
    Spend. Usage reads the subscriptions; Spend reads the tiles, the spend chart, then the token ring."""
    use, spend = _usage(), _stats()
    assert use.find("id=isub") >= 0
    assert use.find("id=pie") < 0 and use.find("id=piet") < 0
    pos = [spend.find("id=tot"), spend.find("Spend over time"), spend.find("id=chart"), spend.find("Model tokens · this month"), spend.find("id=pie")]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos), pos
    for gone in ("id=kpi", "id=open", "id=cash", "id=totnote"):
        assert gone not in SRC, gone



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


def test_the_speed_block_and_the_open_list_left_the_panel():
    """Owner, 2026-11-01: remove the Speed block. The PR speed figures stay on the hub's own reading, never on this panel."""
    html = _stats()
    assert "Speed" not in html and "Provisional" not in html and "Open branches" not in html, html


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


def test_cost_donut_sits_under_its_title_and_subtitle():
    html = _stats()
    assert "Tokens by model" not in html
    assert "<h4>Model tokens · this month</h4>" in html
    assert html.index("<h4>Model tokens · this month</h4>") < html.index('<div class=m id=piet>in + out</div>') < html.index("id=pie")
    pie_wrap = html[html.index("Model tokens") - 40 : html.index("id=pie") + 40]
    assert "<details" not in pie_wrap and "<summary" not in pie_wrap


def test_cost_donut_names_three_models_then_other_largest_first():
    out = _run_js("""
const models=[
  {model:'a',month_tokens:9000,in:1,out:0,cache:0,spend:9,calls:1},
  {model:'b',month_tokens:7000,in:1,out:0,cache:0,spend:7,calls:1},
  {model:'c',month_tokens:5000,in:1,out:0,cache:0,spend:5,calls:1},
  {model:'d',month_tokens:3000,in:1,out:0,cache:0,spend:3,calls:1},
  {model:'e',month_tokens:1000,in:1,out:0,cache:0,spend:1,calls:1},
  {model:'f',month_tokens:500,in:1,out:0,cache:0,spend:0.5,calls:1},
];
const html=pie(models);
const out={html, slices:[...html.matchAll(/<(?:path|circle)[^>]*data-k="([^"]+)"/g)].map(m=>m[1])};
""")
    html = out["html"]
    assert out["slices"] == ["a", "b", "c", "other"], out["slices"]
    legend = re.findall(r'<span class=ln>([^<]+)</span><span class=usd>([^<]+)</span>', html)
    # re-anchored (#1035): the legend counts tokens, and the tail sums them
    assert legend == [("a", "9K"), ("b", "7K"), ("c", "5K"), ("other · 3 models", "5K"),
                      ("d", "3K"), ("e", "1K"), ("f", "500")], legend
    assert re.search(r'<details[^>]*data-k="other"', html), html
    assert html.index("<details") < html.index("<span class=ln>d</span>") < html.index("</details>"), html
    assert "%" not in html and "$" not in html, html   # no share of a price list: tokens, and no dollars (#1035)


def test_cost_donut_other_sums_the_unrounded_costs():
    out = _run_js("""
const models=[
  {model:'a',month_tokens:10,in:1,out:0,cache:0,spend:10,calls:1},
  {model:'b',month_tokens:9,in:1,out:0,cache:0,spend:9,calls:1},
  {model:'c',month_tokens:8,in:1,out:0,cache:0,spend:8,calls:1},
  {model:'d',month_tokens:1,in:1,out:0,cache:0,spend:0.33,calls:1},
  {model:'e',month_tokens:1,in:1,out:0,cache:0,spend:0.33,calls:1},
  {model:'f',month_tokens:1,in:1,out:0,cache:0,spend:0.33,calls:1},
];
const out={html:pie(models)};
""")
    # re-anchored (#1035): the pie counts tokens, so the tail sums tokens exactly
    assert re.search(r'<span class=ln>other · 3 models</span><span class=usd>3</span>', out["html"]), out["html"]


def test_the_month_window_draws_only_cost_this_month():
    out = _run_js("""
const models=[
  {model:'paid',month_tokens:100,in:100,out:0,cache:0,spend:2,calls:1},
  {model:'free',month_tokens:0,in:90000,out:0,cache:0,spend:0,calls:1},
  {model:'old',month_tokens:0,in:5000,out:0,cache:0,spend:40,calls:1},
];
const out={html:pie(models)};
""")
    html = out["html"]
    assert re.findall(r'<span class=ln>([^<]+)</span>', html) == ["paid"], html
    assert "free" not in html and "old" not in html, html
    assert ">100</text>" in html, html


def test_cost_donut_centre_reads_the_month_total_and_this_month():
    out = _run_js("const out={html:pie([{model:'m',month_tokens:1234567,in:1,out:0,cache:0,spend:1234.5,calls:1}])};")
    html = out["html"]
    assert re.search(r">1\.2M</text>", html), html
    assert re.search(r">tokens · month</text>", html), html
    assert not re.search(r">\$", html), html   # dollars left the donut (#1035)


def test_named_constant_keeps_the_top_three_models():
    assert re.search(r"const TOP=3;", SRC)


def test_the_subtitle_is_static_and_pollstats_leaves_it_alone():
    assert '<div class=m id=piet>in + out</div>' in _stats()
    assert '$("#piet")' not in POLL


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


