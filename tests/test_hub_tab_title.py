"""The hub's tab names the profile it shows: two open hubs are told apart by the tab alone.

A browser shows one title per tab, and every hub page served the same one, so a reader with the agent-squad hub and the
mesenai hub open could not tell which was which before clicking.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


def page_text():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_tab_title", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def test_the_tab_names_the_profile_the_route_shows():
    page = page_text()
    assert "document.title=" in page and "location.pathname" in page, "the page never sets its title from the route"
    assert "`Hub | ${decodeURIComponent(p)}`" in page, "the title does not read `Hub | <profile>`"


def test_the_shipped_title_is_never_the_fixed_name_before_the_script_runs():
    page = page_text()
    assert "<title>Hub</title>" in page, "the fixed title is still served before the script names the profile"
    assert "<title>Squad hub</title>" not in page
