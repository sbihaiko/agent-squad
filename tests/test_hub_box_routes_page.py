"""#793: the model pie renders every route a model was called by (stats() returns box.routes), not only the first.

The page is one string in `hub.py`. The pie's tooltip names each route; the guard reads the page text.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def page():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_box_routes_page", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def _pie_source(page):
    """The pie's source, from its opening brace to the brace that closes it. String literals, line and block comments and
    regex literals are text (#798, #799)."""
    start = page.find("function pie(ms){")
    assert start >= 0, "the model pie is not in the page"
    depth, i, quote = 0, start + len("function pie"), None
    while i < len(page):
        c = page[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if page.startswith("//", i):
            i = page.find("\n", i)
            if i < 0:
                break
            continue
        if page.startswith("/*", i):
            i = page.index("*/", i + 2) + 2
            continue
        if c == "/" and (page[:i].rstrip().endswith("return") or page[:i].rstrip()[-1:] in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False
            while page[j] != "/" or in_class:
                if page[j] == "\\":
                    j += 1
                elif page[j] == "[":
                    in_class = True
                elif page[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
            continue
        if c in "\"'`":
            quote = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return page[start:i + 1]
        i += 1
    raise AssertionError("the pie's braces never close")



def test_the_pie_collects_every_route_of_a_model(page):
    pie = _pie_source(page)
    assert "m.box.routes" in pie, "the pie reads box.routes, not one route"
    assert "r.routes" in pie and ".join(" in pie, "the routes are merged per model name and joined"


def test_the_pie_tooltip_names_every_route(page):
    pie = _pie_source(page)
    assert '" via "+m.routes.join(", ")' in pie, "the tooltip text names every route"


def _render_pie(page, models):
    """#795: run the page's own pie() under node, with the page's palette and the helpers it calls, and return its markup."""
    palette = re.search(r"const PCOL=\[[^\]]*\],", page).group(0).rstrip(",") + ";"
    top = re.search(r"^const TOP=.*;$", page, re.M).group(0)
    esc = re.search(r"^const esc=.*;$", page, re.M).group(0)
    usd = re.search(r"^const usd=.*;$", page, re.M).group(0)
    tk = "const " + re.search(r"tk=n=>[^;]+;", page).group(0)
    js = esc + usd + tk + palette + top + _pie_source(page) + "\nprocess.stdout.write(pie(JSON.parse(require('fs').readFileSync(0,'utf8'))));"
    p = subprocess.run(["node", "-e", js], input=json.dumps(models), capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.mark.node_bound
def test_the_rendered_pie_tooltip_names_every_route(page):
    """#795: a model called through cli and gateway is labelled 'via cli, gateway' in the rendered pie."""
    row = {"model": "deepseek-flash", "in": 10, "out": 10, "cache": 0, "spend": 0.1, "month_cost": 0.1, "month_tokens": 20, "calls": 1, "box": {"llm": "deepseek-flash", "routes": ["cli", "gateway"]}}
    html = _render_pie(page, [row])
    assert "via cli, gateway" in html, html


def test_the_pie_matcher_skips_braces_inside_strings():
    """#798: a brace inside a string literal is text, not a block: the matcher reads the whole pie, not the part before it."""
    src = 'function pie(ms){return "}";}\nfunction other(){}'
    assert _pie_source(src) == 'function pie(ms){return "}";}', "the matcher ended at the brace inside the string"


def test_the_pie_matcher_skips_braces_in_block_comments():
    """#799: a brace inside a block comment is text, not a block: the matcher reads the whole pie."""
    src = 'function pie(ms){/* } */return 1;}\nfunction other(){}'
    assert _pie_source(src) == 'function pie(ms){/* } */return 1;}', "the matcher ended at the brace inside the block comment"


def test_the_pie_matcher_skips_braces_in_regex_literals():
    """#799: a brace inside a regex literal is text, not a block: the matcher reads the whole pie."""
    src = 'function pie(ms){return /[}{]/.test(ms)}\nfunction other(){}'
    assert _pie_source(src) == 'function pie(ms){return /[}{]/.test(ms)}', "the matcher ended at the brace inside the regex"


@pytest.mark.node_bound
def test_the_rendered_pie_escapes_a_model_name_with_the_page_esc(page):
    """#798: the pie runs the page's own esc, not a stub: a model name with markup renders as text."""
    row = {"model": "<i>luna</i>", "in": 10, "out": 10, "cache": 0, "spend": 0.1, "month_cost": 0.1, "month_tokens": 20, "calls": 1, "box": {"llm": "<i>luna</i>", "routes": ["cli"]}}
    html = _render_pie(page, [row])
    assert "&lt;i&gt;luna&lt;/i&gt;" in html and "<i>luna" not in html, html
