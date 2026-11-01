"""The hub's first screen carries no configuration accordion and no History tab.

Configuration lives on the Settings tab: the subscription plans and the run history are panels there, and the sidebar
keeps only what a reader watches. The tab bar keeps Kanban, Charts and Settings.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "st"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_settings_clutter", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub_settings_clutter"] = mod
    spec.loader.exec_module(mod)
    return mod


def _aside(page):
    return page[page.index("<aside id=side>"):page.index("</aside>")]


def _settings_pane(page):
    return page[page.index("id=tab-set"):]


def test_the_sidebar_holds_no_subscription_or_model_accordion(hub):
    side = _aside(hub.PAGE)
    assert "Subscriptions" not in side, "the subscriptions accordion is back on the first screen"
    assert "Squad models" not in side, "the models accordion is back on the first screen"


def test_the_tab_bar_holds_no_history_tab(hub):
    assert "data-t=hist" not in hub.PAGE, "the History tab is back"
    assert "id=tab-hist" not in hub.PAGE, "the History pane is back"


def test_the_settings_tab_holds_the_plans_and_the_run_history(hub):
    pane = _settings_pane(hub.PAGE)
    assert "id=subs" in pane, "the subscription plans are not on the Settings tab"
    assert "id=list" in pane, "the run history is not on the Settings tab"


def test_the_last_ended_history_link_opens_the_settings_loop_theme(hub):
    line = next(x for x in hub.PAGE.splitlines() if '#enall' in x and "addEventListener" in x)
    assert 'setTab("set")' in line, "the History link still opens a tab of its own"
    assert 'setTheme("lop")' in line, "the History link does not open the Loop theme that holds the history"


def test_the_models_renderer_writes_no_sidebar_card(hub):
    assert '$("#sqm")' not in hub.PAGE, "the models renderer still writes the removed sidebar card"
    assert '$("#sqsr")' not in hub.PAGE, "the models renderer still writes the removed sidebar count"
