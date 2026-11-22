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
        _fn(SRC, "barChart"), _fn(SRC, "bars"), _fn(SRC, "pie"), _fn(SRC, "cmp"), _fn(SRC, "kr"), _fn(SRC, "openRows"),
    ])


def _node(js):
    node = shutil.which("node")
    assert node, "node is required to evaluate hub panel JS"
    # The script goes on stdin: the hub's panel code passed Linux's 128 KiB cap on one argument (#2774).
    p = subprocess.run([node, "-"], input=js, capture_output=True, text=True, timeout=15)
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


def _chart(body):
    return json.loads(_node(_prelude() + "\n" + body + "\nconsole.log(JSON.stringify(out));"))


def _gauge_js(body):
    fpct = re.search(r"fpct=p=>[^;]+;", SRC).group(0)
    iq = re.search(r"const iq=[^\n]+;", SRC).group(0)
    return json.loads(_node(_prelude() + "\n" + fpct + "\n" + iq + "\n" + _fn(SRC, "subsGauges") + "\n" + body
                            + "\nconsole.log(JSON.stringify(out));"))


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


def test_the_tokens_heading_toggle_and_tiles_are_gone():
    """Owner, 2026-11-03: the Tokens heading, its all/plans/API toggle and the three tiles leave the Spend panel."""
    html = _stats()
    for gone in ("id=tot3", "class=tot3", "id=tot>", "id=src"):
        assert gone not in html, gone


def test_section_order_is_gauges_in_usage_and_tiles_chart_tokens_in_spend():
    """Owner, 2026-11-01: the speed block and the cash line leave the panel, and the token ring moves out of Usage into
    Spend. Usage reads the subscriptions; Spend reads the tiles, the spend chart, then the token ring."""
    use, spend = _usage(), _stats()
    assert use.find("id=isub") >= 0
    assert use.find("id=pie") < 0 and use.find("id=piet") < 0
    pos = [spend.find("Tokens over time"), spend.find("id=chart"), spend.find("Model tokens · this month"), spend.find("id=pie")]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos), pos
    for gone in ("id=kpi", "id=open", "id=cash", "id=totnote"):
        assert gone not in SRC, gone



def test_the_poll_writes_no_tiles_and_no_source_toggle():
    assert "#tot" not in POLL and "#src" not in POLL


def test_per_toggle_reads_per_hour_day_month():
    html = _render(
        'const per="day";\n',
        _stmt('$("#per").innerHTML='),
    )
    assert re.findall(r">([^<>]+)</button>", html) == ["per hour", "per day", "per month"]
    # the API still keys off hour/day/month
    assert re.findall(r"data-p=(\w+)", html) == ["hour", "day", "month"]
    assert re.search(r'data-p=day class="on"', html)




def test_tokens_read_plain_then_k_then_m_and_b():
    out = _node(_prelude() + "\nconsole.log([tk(999),tk(1000),tk(2500000000)].join(' '));")
    assert out.split() == ["999", "1K", "2.50B"]


def test_chart_has_max_rule_ok_bar_now_and_no_year():
    out = _run_js("""
const rows=[{t:'2026-10-09',tokens:2000,runs:1},{t:'2026-10-10',tokens:14280000,runs:2},{t:'2026-10-11',tokens:5000,runs:1}];
const html=bars(rows);
const texts=[...html.matchAll(/<text[^>]*>([^<]*)<\\/text>/g)].map(m=>m[1]);
const out={html, texts};
""")
    html = out["html"]
    assert "max 14.3M" in html
    assert re.search(r'stroke-dasharray', html)
    assert re.search(r'fill="var\(--chart-ok\)"', html)
    assert out["texts"] and out["texts"][-1].endswith("now")
    for t in out["texts"]:
        assert not re.search(r"\b\d{4}\b", t), t


def test_the_spend_chart_labels_its_y_axis_with_ordered_ticks():
    out = _chart("""
const rows=[{t:'2026-10-09',tokens:2000,runs:1},{t:'2026-10-10',tokens:14280000,runs:2},{t:'2026-10-11',tokens:5000,runs:1}];
const c=barChart(rows);
const out={labels:c.ticks.map(t=>t.label),values:c.ticks.map(t=>t.v),ys:c.ticks.map(t=>t.y)};
""")
    assert out["labels"] == ["0", "7.1M", "14.3M"], out
    assert out["values"] == sorted(out["values"]) and len(set(out["values"])) == 3, out
    assert out["ys"] == sorted(out["ys"], reverse=True), "a higher tick must sit higher on the plot"


def test_a_zero_day_draws_a_minimum_muted_mark_apart_from_a_missing_day():
    out = _chart("""
const rows=[{t:'2026-10-09',tokens:0,runs:0},{t:'2026-10-10',tokens:null,runs:0},{t:'2026-10-11',tokens:4000,runs:1}];
const c=barChart(rows);
const out={zero:c.bars[0],missing:c.bars[1],top:c.bars[2],html:bars(rows)};
""")
    zero, missing, top, html = out["zero"], out["missing"], out["top"], out["html"]
    assert zero["kind"] == "zero" and missing["kind"] == "missing", out
    assert zero["kind"] != missing["kind"]
    assert 2 <= zero["h"] < top["h"], "a zero day needs a visible minimum-height mark"
    assert missing["h"] == 0
    assert re.search(r'<rect class=zero [^>]*style="fill:var\(--chart-axis\)"', html), html
    assert re.search(r'<line class=miss ', html), html


def test_the_max_annotation_sits_on_the_bar_it_names():
    out = _chart("""
const rows=[{t:'2026-10-09',tokens:2000,runs:1},{t:'2026-10-10',tokens:14280000,runs:2},{t:'2026-10-11',tokens:5000,runs:1}];
const c=barChart(rows);
const out={max:c.max,bar:c.bars[1],html:bars(rows)};
""")
    assert out["max"]["text"] == "max 14.3M", out
    assert out["max"]["bar"] == 1
    assert out["max"]["x"] == out["bar"]["cx"], "the label must centre on the bar it names"
    assert out["max"]["y"] < out["bar"]["y"], "the label sits above its bar"
    assert re.search(rf'<text x="{out["max"]["x"]}"[^>]*>max 14\.3M</text>', out["html"]), out["html"]


def test_the_spend_chart_keeps_one_bar_per_interval_and_is_never_a_sparkline():
    out = _chart("""
const rows=Array.from({length:30},(_,i)=>({t:'2026-10-'+String(i+1).padStart(2,'0'),tokens:((i%5)+1)*1000,runs:1}));
const c=barChart(rows);
const out={n:c.bars.length,kinds:[...new Set(c.bars.map(b=>b.kind))],ws:c.bars.map(b=>b.w),
  inside:c.bars.every(b=>b.x>=c.L && b.x+b.w<=c.L+c.W),H:c.H,L:c.L,W:c.W,html:bars(rows)};
""")
    assert out["n"] == 30 and out["kinds"] == ["bar"], out
    assert min(out["ws"]) >= 1 and out["inside"], out
    assert out["H"] >= 48, "the bars are the chart, so the plot keeps its height"
    assert "polyline" not in out["html"]
    assert re.search(rf'viewBox="0 0 {out["L"] + out["W"]} ', out["html"]), out["html"][:120]
    assert out["html"].count("<rect class=bar ") == 30


def test_the_cost_chart_takes_the_chart_family_axis_and_title():
    """SPEC 1123 #1141: the cost chart shares the family's grid in `line`, axis text in `mute` at body size, and a title
    inside the plot at the left edge."""
    out = _chart("""
const rows=[{t:'2026-10-09',tokens:2000,runs:1},{t:'2026-10-10',tokens:14280000,runs:2},{t:'2026-10-11',tokens:5000,runs:1}];
const c=barChart(rows);
const out={html:bars(rows),L:c.L,base:c.base};
""")
    html, left, base = out["html"], out["L"], out["base"]
    grid = [ln for ln in re.findall(r"<line [^>]*>", html) if 'stroke="var(--chart-grid)"' in ln]
    assert len(grid) == 3 and all("stroke-width" not in ln for ln in grid), "the grid is not one line stroke"
    texts = re.findall(r"<text ([^>]*)>([^<]*)</text>", html)
    assert texts, "the cost chart draws no text"
    for attrs, body in texts:
        assert 'style="font-size:var(--fs-body)"' in attrs, f"{body!r} is not at body size"
        if not body.startswith("max "):
            assert 'fill="var(--chart-axis)"' in attrs, f"{body!r} is not axis text in mute"
    title = [(attrs, body) for attrs, body in texts if body == "tokens"]
    assert len(title) == 1, "the plot draws no title of its own"
    assert f'x="{left}"' in title[0][0], "the title does not start at the plot's left edge"
    assert float(re.search(r'y="(\d+)"', title[0][0]).group(1)) < base, "the title is not inside the plot"
    assert "stroke-dasharray" in html and "var(--fs-caption)" not in html, "the chart is not the family's"


def test_the_cost_panel_draws_no_implementation_small_print():
    assert "id=totnote" not in _stats()
    assert '$("#totnote")' not in SRC


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


def test_open_branches_say_none_open_rather_than_an_empty_heading():
    out = _run_js("out={empty:openRows([]),undef:openRows(undefined)};")
    assert "none open" in out["empty"] and "none open" in out["undef"]
    assert "#" not in out["empty"]


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
    assert "$" not in html, html   # tokens and shares of tokens, never dollars (#1035)


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
const PAYLOAD = { frac: 0.34, plans: 255, api: 28, total: 283, file: "/p/plans.json",
  presets: { grok: { g1: { name: "SuperGrok", usd: 30 } }, claude: { c1: { name: "Max 20x", usd: 200 } } },
  gauges: [ { key: "grok", label: "Grok", pct: null, resets: null, why: "cli probe failed" },
            { key: "claude", label: "Claude Max 20x", pct: 77, resets: 1792807174, window: "weekly" } ],
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
    """The page slice holding pollSubs and every helper it reaches, plus the `esc` defined after it and the Now/Settings
    builders the panel draws through (#1278). The shell's own boot runs here, so `save` and the read-only flag are stubbed.
    The quota rows come from the sidebar card's own `subsGauges` (#1281), so its helpers travel too. The slice is bounded
    by explicit markers and asserted to carry each helper by name inside them, so a refactor of the sidebar cannot widen
    or truncate it silently (#1281 review)."""
    END = "// ---- subscriptions slice end"
    assert END in SRC, "the subscriptions slice lost its end marker"
    i = SRC.index("const usd=v=>")
    j = SRC.index(END)
    assert i < j, "the subscriptions slice markers are out of order"
    body = SRC[i:j].replace("__CAPS__", "{}")
    for need in ("function subsGauges(", "const iq=", "fpct="):
        assert need in body, f"the subscriptions slice lost {need}"
    for mark in ("// SETT{", "// }SETT"):
        assert mark in SRC, f"the settings builder lost its marker {mark}"
    esc = SRC[SRC.index("const esc=t=>"):]
    sett = SRC[SRC.index("// SETT{"): SRC.index("// }SETT")]
    return (body + "\n" + esc[: esc.index("\n")] + "\n" + sett + "\nfunction save(){}\n")   # iq and fpct travel in the page slice


def test_a_quota_probe_with_no_windows_still_renders_the_subscriptions_panel():
    """#1512: a provider's quota probe answers `unavailable` with no `windows` at all (grok did),
    and `aq.windows[0]` threw inside pollSubs. The throw is caught, so the page kept its header
    totals while `#subs` stayed empty: the group opened onto nothing and read as broken."""
    out = json.loads(_node(_PRE + _subs_slice() + _POST).split("RESULT")[-1])
    assert not out["err"], out["err"]
    assert "SuperGrok" in out["subs"] and "cli probe failed" in out["subs"], out["subs"][:300]   # re-anchored (#1281): the card's own row, not a local chip
    assert "Claude Max 20x" in out["subs"] and "Claude Team" not in out["subs"], out["subs"][:300]   # the name comes from the row (#511)
    assert "77%" in out["subs"], out["subs"][:300]


def test_the_now_block_is_read_only_and_the_settings_block_keeps_the_plans():
    """#1278: the Subscriptions theme reads its facts in tiles and changes its plans in the Settings block."""
    post = _POST.replace("{ subs:", '{ now: (__els["#subnow"] || {}).innerHTML || "", subs:')
    out = json.loads(_node(_PRE + _subs_slice() + post).split("RESULT")[-1])
    assert not out["err"], out["err"]
    now, settings = out["now"], out["subs"]
    assert re.findall(r"<span class=tk>(.*?)</span>", now) == ["At limit", "Cash this month", "Month passed", "Plans file"], now   # re-anchored (#1281): the theme names its file
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in now, f"the Subscriptions Now block carries {ctl}"
    assert settings.count("<select data-plan=") == 2 and "data-f=plan" in settings, settings[:300]


def test_the_pie_merges_the_spellings_of_one_model_name():
    """One model answers under several spellings (deepseek-flash, deepseek-flash[1m], claude-deepseek-flash), so keying
    the slices on the raw id drew the same label three times. The rows merge by the name, and the tooltip keeps every
    spelling that went into the slice."""
    body = _fn(SRC, "pie")
    assert "byName" in body and "byName.set(k,r)" in body, body
    assert "ids.join" in body and "ids.includes(m.model)" in body, body


def test_a_subscription_gauge_states_name_percentage_and_reset_as_three_fields():
    out = _gauge_js("""
const d={subs:[{label:'Claude weekly',pct:72.4,window:'weekly',resets:Date.now()/1000+1.7*86400}]};
const out={html:subsGauges(d)};
""")
    html = out["html"]
    assert re.search(r"<span class=sn>Claude weekly</span>", html), html
    assert re.search(r"<b class=sp2>72%</b>", html), html
    assert re.search(r"<small class=sr2>resets in 1\.7 d</small>", html), html


def test_a_gauge_fill_token_is_not_its_track_token_on_any_returned_state():
    """The sidebar card and the Subscriptions theme share `.isub`, so the card's element must carry the class as well as
    its id; the rules then apply to both (#1281 review)."""
    assert re.search(r'class="[^"]*\bisub\b[^"]*"\s+id=isub', SRC), "the sidebar usage card lost its .isub class"
    track = re.search(r"\.isub \.sb2\{[^}]*?background:(var\(--[\w-]+\))", SRC).group(1)
    fills = {
        "ok": re.search(r"\.isub \.sb2 i\{[^}]*?background:(var\(--[\w-]+\))", SRC).group(1),
        "warn": re.search(r"\.isub \.srow\.warn \.sb2 i\{[^}]*?background:(var\(--[\w-]+\))", SRC).group(1),
        "red": re.search(r"\.isub \.srow\.red \.sb2 i\{[^}]*?background:(var\(--[\w-]+\))", SRC).group(1),
    }
    html = _gauge_js("""
const now=Date.now()/1000+86400;
const out={html:subsGauges({subs:[{label:'a',pct:10,resets:now},{label:'b',pct:80,resets:now},{label:'c',pct:95,resets:now}]})};
""")["html"]
    assert re.findall(r'<div class="(srow[^"]*)"', html) == ["srow", "srow warn", "srow red"], html
    for state, fill in fills.items():
        assert fill != track, f"the {state} fill reads as its own track"

def test_the_chart_draws_from_the_stats_read_alone():
    """A failing companion read emptied the whole panel, chart and ring included, and the catch hid it (owner, 2026-11-01). The
    panel reads one route, and a failure names itself."""
    i = SRC.index("async function pollStats")
    body = SRC[i:SRC.index("pollStats();setInterval", i)]
    assert "/api/stats?period=" in body
    assert "/api/speed" not in body, "the chart never waits on a second route"
    assert 'console.warn("stats panel:",e)' in body
    assert "$(\"#chart\").innerHTML=bars(" in body


def test_the_largest_slice_and_other_take_distinct_fills():
    """#2503: a model's colour never lands on the fold's own role, so the largest slice and "other" never read as one."""
    out = _run_js("""
const models=[
  {model:'b',month_tokens:9000,in:1,out:0,cache:0,spend:9,calls:1},
  {model:'c',month_tokens:5000,in:1,out:0,cache:0,spend:5,calls:1},
  {model:'d',month_tokens:3000,in:1,out:0,cache:0,spend:3,calls:1},
  {model:'e',month_tokens:1000,in:1,out:0,cache:0,spend:1,calls:1},
];
const html=pie(models);
const out={fills:[...html.matchAll(/<(?:path|circle)[^>]*data-k="([^"]+)"[^>]*fill="([^"]+)"/g)].map(m=>[m[1],m[2]])};
""")
    fills = out["fills"]
    assert [f[0] for f in fills] == ["b", "c", "d", "other"], fills
    assert fills[0][1] != fills[-1][1], f"the largest slice and other share {fills[0][1]}"
    assert fills[-1][1] == "var(--chart-series-other)", fills


def test_two_slices_never_share_a_fill():
    """#2503: two models whose names hash to the same series take different roles, so a legend never shows one colour
    twice. The desktop visual review found DeepSeek and Claude Opus on one green."""
    out = _run_js("""
const models=[
  {model:'DeepSeek V4.1 Flash',month_tokens:9000,in:1,out:0,cache:0,spend:9,calls:1},
  {model:'Claude Haiku 5.5',month_tokens:5000,in:1,out:0,cache:0,spend:5,calls:1},
  {model:'Claude Opus 5.5',month_tokens:3000,in:1,out:0,cache:0,spend:3,calls:1},
  {model:'other-model',month_tokens:1000,in:1,out:0,cache:0,spend:1,calls:1},
];
const html=pie(models);
const out={fills:[...html.matchAll(/<(?:path|circle)[^>]*data-k="([^"]+)"[^>]*fill="([^"]+)"/g)].map(m=>[m[1],m[2]])};
""")
    fills = [f[1] for f in out["fills"]]
    assert len(fills) == 4, out
    assert len(set(fills)) == len(fills), f"two slices share a fill: {out['fills']}"


def test_a_folded_tail_never_exhausts_the_palette_under_the_drawn_slices():
    """#2503: the drawn slices take their roles first. A ten-model pie whose nine-name tail took all eight roles under
    the top three put DeepSeek and Claude Opus on one green in the desktop render."""
    out = _run_js("""
const models=[
  {model:'DeepSeek V4.1 Flash',month_tokens:9000,in:1,out:0,cache:0,spend:9,calls:1},
  {model:'Claude Haiku 5.5',month_tokens:5000,in:1,out:0,cache:0,spend:5,calls:1},
  {model:'Claude Opus 5.5',month_tokens:3000,in:1,out:0,cache:0,spend:3,calls:1},
  ...Array.from({length:7},(_,i)=>({model:'tail-'+i,month_tokens:100,in:1,out:0,cache:0,spend:1,calls:1})),
];
const html=pie(models);
const out={fills:[...html.matchAll(/<(?:path|circle)[^>]*data-k="([^"]+)"[^>]*fill="([^"]+)"/g)].map(m=>[m[1],m[2]])};
""")
    fills = [f[1] for f in out["fills"]]
    assert [f[0] for f in out["fills"]] == ["DeepSeek V4.1 Flash", "Claude Haiku 5.5", "Claude Opus 5.5", "other"], out
    assert len(set(fills)) == len(fills), f"two drawn slices share a fill: {out['fills']}"


def test_the_centre_caption_stands_clear_of_the_ring():
    """#2503: the caption sits inside a hole painted behind it, and the hole is wide enough for the whole line, so no
    character vanishes where the line meets a slice. Mute text over a light fill was the defect the mockup showed."""
    out = _run_js("const out={html:pie([{model:'m',month_tokens:1234567,in:1,out:0,cache:0,spend:1234.5,calls:1}])};")
    html = out["html"]
    m = re.search(r'<circle cx=(\d+) cy=(\d+) r=(\d+) fill="var\(--chart-bg\)"', html)
    assert m, html
    c, hole = int(m.group(1)), int(m.group(3))
    assert hole >= 45, f"the caption is wider than the hole it sits on, and the hole is {hole} units"
    cap = re.search(r'<text x=\d+ y=(\d+)[^>]*fill="(var\(--[\w-]+\))"[^>]*>tokens · month</text>', html)
    assert cap, html
    assert int(cap.group(1)) == c + 12, f"the caption sits at {cap.group(1)}, not under the total"
    assert cap.group(2) == "var(--fg)", f"the caption is drawn in {cap.group(2)}, so a slice hides part of it"
