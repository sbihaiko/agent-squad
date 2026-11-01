"""The page has no alert strip (owner request): the Monitor card carries its facts."""
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


def test_the_page_draws_no_alert_strip(page):
    """The owner removed the strip: the Monitor card carries the loop and the gate, and /api/status keeps the warnings."""
    assert "id=alert" not in page and "#alert" not in page
    assert "alertPoll" not in page and "alertDraw" not in page and "subWarn" not in page
