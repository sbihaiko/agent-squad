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


def _run_js(body):
    node = shutil.which("node")
    assert node, "node is required to evaluate hub panel JS"
    usd = re.search(r"const usd=v=>[^;]+;", SRC).group(0)
    pcol = re.search(r"const PCOL=[^;]+;", SRC).group(0)
    other = re.search(r"const OTHER=[^;]+;", SRC)
    if "tk=" not in pcol:
        tk = re.search(r"(?:const )?tk=n=>[^;]+;", SRC).group(0)
        if not tk.startswith("const "):
            tk = "const " + tk
        pcol = pcol + "\n" + tk
    esc = "const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));"
    js = "\n".join([
        usd, pcol, other.group(0) if other else "",
        esc, _fn(SRC, "bars"), _fn(SRC, "pie"), _fn(SRC, "kr"),
        body, "console.log(JSON.stringify(out));",
    ])
    p = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr or p.stdout
    return json.loads(p.stdout)


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


def test_spend_section_order_and_cash_line():
    html = _stats()
    assert re.search(r"<h4>Spend\b", html)
    tot_html = SRC[SRC.index("$(\"#tot\").innerHTML"): SRC.index("$(\"#tot\").innerHTML") + 800]
    assert "today" in tot_html and "this month" in tot_html and "all time" in tot_html
    cash = re.search(r"plans \$\d.*\+ API \$\d.*= \$\d", SRC)
    assert cash, "cash line template missing"
    assert "at API list prices (API-equivalent)" in SRC


def test_src_and_per_button_copy():
    src = re.search(r'\$\("#src"\)\.innerHTML=([^;]+);', SRC).group(1)
    per = re.search(r'\$\("#per"\)\.innerHTML=([^;]+);', SRC).group(1)
    assert '"all"' in src and '"plans"' in src and '"API"' in src
    assert "subscription" not in src.lower() or '"plans"' in src
    assert "per hour" in per and "per day" in per and "per month" in per
    assert re.search(r">hour<", per) is None
    assert re.search(r">day<", per) is None


def test_all_time_equals_month_reads_this_month():
    tot = SRC[SRC.index("$(\"#tot\").innerHTML"): SRC.index("$(\"#kpi\")") if "$(\"#kpi\")" in SRC else SRC.index("const b=sp.baseline")]
    assert "= this month" in tot


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


def test_speed_header_and_delta_copy():
    kpi = POLL
    assert "sample size" not in kpi
    assert "since then" not in kpi
    assert "Merge time median" in kpi
    assert "Merge time average" in kpi
    assert "Run time median today" in kpi
    assert "Review rounds average" in kpi
    assert "Hung runs killed" in kpi
    header = re.search(r"<h4>Speed <small>.*?</small></h4>", kpi).group(0)
    plain = re.sub(r"<[^>]+>", " ", header)
    plain = re.sub(r"\$\{[^}]+\}", "10-09 19:00", plain, count=1)
    plain = re.sub(r"\$\{n===1\?\"\":\"s\"\}", "s", plain)
    plain = re.sub(r"\$\{[^}]+\}", "42", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    assert re.search(r"^Speed since \d\d-\d\d \d\d:\d\d UTC · \d+ PRs?$", plain)
    assert "vs. before" not in header
    out = _run_js("const out={html:kr('Merge time median','',18,' min',38), zero:kr('Hung runs killed','',0,'',null)};")
    assert "was 38 min" in out["html"]
    assert "background" not in out["html"]
    assert re.search(r'class="[^"]*\b(good|bad)\b[^"]*"', out["html"])
    assert re.search(r"<b[^>]*class=m", out["zero"]) or 'class="m"' in out["zero"]


def test_open_prs_header_and_empty_state():
    html = _stats()
    assert re.search(r'id=open', html)
    assert "Open PRs" in POLL
    assert "oldest first" in POLL
    assert "none open" in POLL
    assert "warns at 30" in POLL
    assert "Open now" not in POLL


def test_tokens_by_model_always_open_with_folded_other():
    html = _stats()
    pie_wrap = html[html.index("id=pie") - 80 : html.index("id=pie") + 40]
    assert "<details" not in pie_wrap
    assert "Tokens by model" in html
    assert re.search(r"all time", html)
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
const out={html, legend, n:legend.length, other:(legend.find(x=>x.startsWith('other'))||'')};
""")
    assert "other" in out["html"]
    assert re.search(r"other · \d+ models", out["other"] or " ".join(out["legend"]))
    for row in out["legend"]:
        if row.startswith("other"):
            continue
        # named rows are the ones kept; none of the <1% models (a–g) may appear
        assert row not in list("abcdefg")
    assert out["n"] <= 6
    assert "tokens" in out["html"]
    assert "a:" in out["html"] or "a " in out["html"]  # tooltip lists folded models


def test_closed_summary_shape():
    stsr = re.search(r'\$\("#stsr"\)\.textContent=([^;]+);', SRC).group(1)
    assert "today" in stsr and "merge" in stsr
    assert "subscription" not in stsr


def test_section_order_spend_chart_speed_open_tokens():
    html = _stats()
    pos = [
        html.find("Spend"),
        html.find("Spend over time"),
        html.find("id=kpi"),
        html.find("id=open"),
        html.find("Tokens by model"),
        html.find("id=pie"),
    ]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_named_constant_folds_models_under_one_percent():
    assert re.search(r"const OTHER=0\.01", SRC)
