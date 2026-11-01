"""The alert strip is the page footer, under the board, never above the header (owner request)."""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


@pytest.fixture(scope="module")
def page():
    for k, v in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": tempfile.mkdtemp(),
                 "SG_HUB_DIR": tempfile.mkdtemp()}.items():
        os.environ.setdefault(k, v)
    spec = importlib.util.spec_from_file_location("hub_alert_footer", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def test_the_alert_strip_follows_the_board_and_the_header(page):
    at = page.index("<div id=alert role=alert></div>")
    assert at > page.index("<header class=top>"), "the alert strip sits above the header"
    assert at > page.index("<div id=win>"), "the alert strip sits above the board"


def test_the_alert_strip_draws_its_line_on_top_as_a_footer(page):
    rule = page[page.index("#alert{"):].split("}", 1)[0]
    assert "border-top:" in rule and "border-bottom" not in rule
    assert "#alert.calm{background:var(--panel);border-top-color:var(--line)}" in page
