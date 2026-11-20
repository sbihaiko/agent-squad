"""The hub's first screen carries no configuration accordion and no History tab.

Configuration lives on the Settings tab: the subscription plans are a panel there, and the sidebar keeps only what a
reader watches. The run history is the bottom of the Runs tab (#1277), never a block of Settings or of the Loop tab. The
tab bar keeps Kanban, Runs, Flow, Loop and Settings.
"""
import importlib.util
import re
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


def test_the_settings_tab_holds_the_plans_and_no_run_history(hub):
    """Re-anchored (#1277): the run history left Settings for the Runs tab. The plans stay here."""
    pane = _settings_pane(hub.PAGE)
    assert "id=subs" in pane, "the subscription plans are not on the Settings tab"
    assert "id=list" not in pane, "the run history is back on the Settings tab"
    assert "Run history" not in pane, "Settings carries a run history block again"


def test_the_history_lives_in_the_runs_tab(hub):
    """Owner, 2026-11-01: Last ended left the sidebar. #1770: Ended today is the Runs tab's one history, under Live."""
    side = hub.PAGE[hub.PAGE.index("<aside id=side>"):hub.PAGE.index("</aside>")]
    assert "Last ended" not in side, "the Last ended card is back on the first screen"
    assert "#ended" not in hub.PAGE and "#enall" not in hub.PAGE, "a script still writes the removed panel"
    loop = hub.PAGE[hub.PAGE.index("<section id=tab-prof"):hub.PAGE.index("</section>", hub.PAGE.index("<section id=tab-prof"))]
    assert "id=histb" not in loop and "id=list" not in loop, "the run history is back in the Loop tab"
    pane = hub.PAGE[hub.PAGE.index("id=tab-runs"):hub.PAGE.index("</section>", hub.PAGE.index("id=tab-runs"))]
    assert "id=main" in pane and "id=list" not in pane, "the Runs tab has a second list again"


def test_the_models_renderer_writes_no_sidebar_card(hub):
    assert '$("#sqm")' not in hub.PAGE, "the models renderer still writes the removed sidebar card"
    assert '$("#sqsr")' not in hub.PAGE, "the models renderer still writes the removed sidebar count"


def test_every_accordion_the_page_names_exists(hub):
    """A name with no element behind it kills the whole page script (#1032): every name is answered by a card."""
    import json
    ids = json.loads(re.search(r"const ACC=(\[[^\]]*\]);", hub.PAGE).group(1))
    assert ids, "the page names no accordion"
    for i in ids:
        assert f"<details id={i}" in hub.PAGE, f"the page names the accordion {i} and carries no element for it"
