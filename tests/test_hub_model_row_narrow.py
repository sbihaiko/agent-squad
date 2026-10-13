"""The hub's models panel keeps every control of a row reachable in a narrow pane.

A row's controls -- the effort select, the hours box, up, down, the on/off switch and the remove button -- sit in one
flex line, `.mr .ct`, which clipped its overflow. The line is wider than the pane at the widths the panel actually
takes: a browser window at a third of the screen, or the owner's 312 px pane. The remove button was in the page, was
rendered, and could not be pressed, because the line cut it off last. A row wraps instead: a narrow pane costs a line,
never a control.

The page is one string in `hub.py`, so the guards read it there rather than through a browser, as the neighbours in
`tests/test_hub_model_off_switch.py` and `tests/test_hub_model_add_box.py` read the rules the panel ships.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
CONTROLS = re.compile(r"\.mr \.ct\{([^}]*)\}")
REMOVE = re.compile(r'<button class=x data-rm="\$\{k\}\|\$\{i\}"(.*?)</button>')


@pytest.fixture(scope="module")
def page():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_row_narrow", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def test_the_control_line_wraps_rather_than_clipping_its_last_control(page):
    css = CONTROLS.search(page)
    assert css, "the model row's control line (.mr .ct) is not in the page"
    decl = css.group(1)
    assert "flex-wrap:wrap" in decl, f"the control line does not wrap, so a narrow pane cuts its last control: {decl}"
    assert "overflow:hidden" not in decl, f"the control line still clips what does not fit: {decl}"


def test_the_row_still_ships_the_remove_button_and_its_reason(page):
    m = REMOVE.search(page)
    assert m, "the model row no longer renders a remove button"
    assert 'title="remove from chain"' in m.group(1), m.group(1)
    assert "mdl[k].length>1" in m.group(1), "the last row of a chain must keep its remove button disabled"


def test_the_control_cluster_is_one_unbreakable_group(page):
    """The cluster wraps whole: a wrapped `↑` apart from `↓`, or the switch apart from the remove button, would read as
    two rows of unrelated buttons."""
    css = re.search(r"\.mr \.mb\{([^}]*)\}", page)
    assert css and "flex:none" in css.group(1), css.group(1) if css else "the cluster rule is missing"
