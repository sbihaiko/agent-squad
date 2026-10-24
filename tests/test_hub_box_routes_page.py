"""#793: the model pie renders every route a model was called by (stats() returns box.routes), not only the first.

The page is one string in `hub.py`. The pie's tooltip names each route; the guard reads the page text.
"""
import importlib.util
import os
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
    m = re.search(r"function pie\(ms\)\{.*?\nconst ago", page, re.S)
    assert m, "the model pie is not in the page"
    return m.group(0)


def test_the_pie_collects_every_route_of_a_model(page):
    pie = _pie(page)
    assert "m.box.routes" in pie, "the pie reads box.routes, not one route"
    assert "r.routes" in pie and ".join(" in pie, "the routes are merged per model name and joined"


def test_the_pie_tooltip_names_every_route(page):
    pie = _pie(page)
    assert '" via "+m.routes.join(", ")' in pie, "the tooltip text names every route"
