"""GitHub issue #78: the models panel row controls wrap in a narrow pane.

The models panel drew each row's controls on one flex line, `.mr .ct`, which clipped its overflow. At narrow
widths the remove button was present in the markup but outside the pressable area. A narrow pane may cost a line; it
must never cost a control.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
CONTROLS = re.compile(r"\.mr \.ct\{([^}]*)\}")
BUTTONS = re.compile(r"\.mr \.mb\{([^}]*)\}")


def _decls(css):
    return {k.strip(): v.strip() for k, v in (item.split(":", 1) for item in css.split(";") if ":" in item)}


def _model_row_source():
    src = HUB_PY.read_text()
    return src[src.index("function mdlRender()"):src.index("async function mdlLoad()")]


def test_control_line_wraps_instead_of_clipping():
    page = HUB_PY.read_text()
    css = CONTROLS.search(page)
    assert css, "the model row's control line (.mr .ct) is not in the page"
    decls = _decls(css.group(1))

    assert decls.get("display") == "flex"
    assert decls.get("flex-wrap") == "wrap"
    assert decls.get("overflow") != "hidden"


def test_remove_button_stays_in_the_row_with_its_reason_and_last_model_disabled():
    row = _model_row_source()
    assert '<button class=x data-rm="${k}|${i}"' in row
    assert 'title="remove from chain"' in row
    assert '${mdl[k].length>1?"":"disabled"}' in row


def test_wrapped_row_keeps_the_arrow_switch_remove_cluster_whole():
    row = _model_row_source()
    controls = row[row.index("<span class=mb>"):row.index("</span></span></div>", row.index("<span class=mb>"))]
    assert controls.index('title="move up"') < controls.index('title="move down"')
    assert controls.index('data-sw="${k}|${esc(m)}"') < controls.index('data-rm="${k}|${i}"')

    page = HUB_PY.read_text()
    css = BUTTONS.search(page)
    assert css, "the model row's button cluster (.mr .mb) is not in the page"
    decls = _decls(css.group(1))
    assert decls.get("flex") == "none"
    assert decls.get("display") == "flex"
