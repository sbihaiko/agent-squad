"""#793: the model pie renders every route a model was called by (stats() returns box.routes), not only the first.

The page is one string in `hub.py`. The pie's tooltip names each route; the guard reads the page text.
"""
import importlib.util
import json
import os
import subprocess
import re
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


def _pie(page):
    """The page's pie() source, from its opening brace to the brace that closes it (#795)."""
    start = page.find("function pie(ms){")
    assert start >= 0, "the model pie is not in the page"
    depth = 0
    for i in range(start + len("function pie"), len(page)):
        if page[i] == "{":
            depth += 1
        elif page[i] == "}":
            depth -= 1
            if depth == 0:
                return page[start:i + 1]
    raise AssertionError("the model pie has no closing brace")


def test_the_pie_collects_every_route_of_a_model(page):
    pie = _pie(page)
    assert "m.box.routes" in pie, "the pie reads box.routes, not one route"
    assert "r.routes" in pie and ".join(" in pie, "the routes are merged per model name and joined"


def test_the_pie_tooltip_names_every_route(page):
    pie = _pie(page)
    assert '" via "+m.routes.join(", ")' in pie, "the tooltip text names every route"


def _render_pie(page, models):
    """#795: run the page's own pie() under node, with the page's palette and the helpers it calls, and return its markup."""
    palette = re.search(r"^const PCOL=.*;$", page, re.M).group(0)
    other = re.search(r"^const OTHER=.*;$", page, re.M).group(0)
    prelude = 'const esc=t=>String(t);const usd=v=>"$"+v;'
    js = prelude + palette + other + _pie(page) + "\nprocess.stdout.write(pie(JSON.parse(require('fs').readFileSync(0,'utf8'))));"
    p = subprocess.run(["node", "-e", js], input=json.dumps(models), capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.mark.node_bound
def test_the_rendered_pie_tooltip_names_every_route(page):
    """#795: a model called through cli and gateway is labelled 'via cli, gateway' in the rendered pie."""
    row = {"model": "deepseek-flash", "in": 10, "out": 10, "cache": 0, "spend": 0.1, "calls": 1, "box": {"llm": "deepseek-flash", "routes": ["cli", "gateway"]}}
    html = _render_pie(page, [row])
    assert "via cli, gateway" in html, html
