"""Hub board: a visible column holding no cards is not drawn at all, and it is accounted for by one thin quiet line
at the foot of the `hidden:` strip (#1809).

The contract under test is the one the server actually paints: the declarations served in the page's stylesheet, the
HTML the docked render builds, and the derivation the page runs on every poll. Every assertion names its seam.
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
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
ROOT_SCRIPTS = ROOT / "contrib/squad-goal/scripts"
LINE = ".hidbar .hidline"       # the thin line an auto-hidden column gets, at the end of the hidden: strip (#1809)
CHIP = ".hidbar .chip"          # the chip a column the user hid gets, above it


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_empty_column_strip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _css(hub, selector):
    """Every declaration the served page paints for `selector`, joined: comments stripped, a selector may repeat."""
    page = hub.PAGE[:hub.PAGE.index("</style>")]
    css = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    return ";".join(m.group(2).strip() for m in re.finditer(r"([^{}\n;]+)\{([^{}]*)\}", css)
                    if selector in [s.strip() for s in m.group(1).split(",")])


def _prop(decls, name):
    """The value served for `name`, or None when the rule does not set it."""
    m = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*:\s*([^;]+)", decls)
    return m.group(1).strip() if m else None


def _page_line(hub, prefix):
    """The one line of the board's script that starts with `prefix`."""
    m = re.search(rf"^{re.escape(prefix)}.*$", hub.PAGE, re.M)
    assert m, f"the board's script has no line starting with {prefix!r}"
    return m.group(0)


def _lum(hexcolour):
    """Relative luminance of a `#rrggbb`, so `dimmer than` is measured rather than asserted by eye."""
    ch = [int(hexcolour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _derivation(hub):
    """The bare derivation the page runs on every render: which columns are laid out, which lines the strip carries."""
    page = hub.PAGE
    return page[page.index("const kept="): page.index("const badge=")]


def _run_derivation(hub, *, kept=(), hidden=(), filled=(), bottom=True):
    """The page's own derivation, run by node over a stubbed board: every column `filled` does not name holds nothing."""
    names = ["Todo", "Doing", "Code Review", "Closed Issues"]
    assert set(filled) <= set(names)
    stub = (f"const bo={json.dumps({'kept': list(kept)})},hid=new Set({json.dumps(list(hidden))}),"
            f"bottom={json.dumps(bottom)},all={json.dumps([{'name': n} for n in names])},"
            f"fits={json.dumps({n: ([{'n': 1}] if n in filled else []) for n in names})};\n")
    script = stub + _derivation(hub) + "\nconsole.log(JSON.stringify({vis:vis.map(g=>g.name),autos:autos.map(g=>g.name)}))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- what the board draws ---------------------------------------------------------------------------------------

def test_a_visible_column_holding_no_cards_is_not_drawn_at_all(hub):
    for gone in ("--col-empty-w", ".col.empty", "colCls", "col empty"):
        assert gone not in hub.PAGE, f"the rejected strip is still painted by the board: {gone!r} (#1809)"
    col = _css(hub, "body.dockb #bbody>.col")
    assert _prop(col, "flex") == "1 1 0", "only the columns holding cards are laid out, and they take the width"
    assert "min-width:var(--col-w)" in col


def test_the_rotated_name_is_gone_with_the_strip(hub):
    assert "writing-mode" not in hub.PAGE, "the rotated column name is what the owner rejected (#1809)"
    assert "rotate(180deg)" not in hub.PAGE


def test_an_empty_visible_column_leaves_the_board_and_lands_on_the_strip(hub):
    d = _run_derivation(hub, filled=["Doing", "Closed Issues"])
    assert d["vis"] == ["Doing", "Closed Issues"], "the columns that have cards take the freed width"
    assert d["autos"] == ["Todo", "Code Review"], "each collapsed column is accounted for by one line"


def test_the_strip_is_derived_on_every_render_and_never_stored(hub):
    block = _derivation(hub)
    assert "!hid.has(g.name)&&!kept.has(g.name)&&!fits[g.name].length" in block, "the line's set, derived and named"
    assert "bo.autos" not in hub.PAGE and "bo.empty" not in hub.PAGE, "the auto-hidden set is never user state (#1809)"
    script = ("const bo={kept:[]},hid=new Set(),bottom=true,all=[{name:\"Todo\"}],fits={Todo:[]};\n"
              + block + "\nconsole.log(JSON.stringify(Object.keys(bo)))")
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["kept"], "a render writes nothing back: the set is derived, not kept"


def test_a_column_the_user_hid_keeps_its_chip_and_gets_no_line(hub):
    d = _run_derivation(hub, hidden=["Closed Issues"], filled=["Todo", "Doing", "Code Review"])
    assert d["vis"] == ["Todo", "Doing", "Code Review"], "a column the user hid is not laid out at all"
    assert "Closed Issues" not in d["autos"], "... and it is a chip, never one of the strip's lines"


def test_the_lines_eye_keeps_the_column_shown_even_at_zero(hub):
    board = dict(filled=["Todo", "Doing", "Closed Issues"])     # Code Review is the column holding nothing
    without = _run_derivation(hub, **board)
    with_kept = _run_derivation(hub, kept=["Code Review"], **board)
    assert without["vis"] == ["Todo", "Doing", "Closed Issues"] and without["autos"] == ["Code Review"]
    assert with_kept["vis"] == ["Todo", "Doing", "Code Review", "Closed Issues"], \
        "a column the user asked to keep comes back, header and all"
    assert with_kept["autos"] == [], "... and is never auto-hidden again"


def test_a_card_appearing_returns_the_column_and_its_line_leaves(hub):
    assert _run_derivation(hub, filled=["Todo", "Doing", "Code Review", "Closed Issues"])["autos"] == []


def test_the_non_docked_accordion_has_no_empty_state_at_all(hub):
    d = _run_derivation(hub, filled=["Doing"], bottom=False)
    assert d["autos"] == [], "the accordion lays out every column, empty or not"
    assert d["vis"] == ["Todo", "Doing", "Code Review", "Closed Issues"]


# --- the strip's lines ------------------------------------------------------------------------------------------

def test_the_strip_carries_a_line_per_auto_hidden_column_below_the_chips(hub):
    html = _page_line(hub, "const html=")
    assert "class=hidbar" in html
    assert "hid.size||autos.length" in html, \
        "the strip now exists for an auto-hidden column alone, with nothing the user hid"
    assert html.index("map(hidchip)") < html.index("hidsep") < html.index("class=hidline"), \
        "the quiet lines come after the chips the user hid, behind a divider"
    assert "autos.map(" in html


def test_the_line_carries_the_name_the_count_and_the_headers_own_eye_slashed(hub):
    html = _page_line(hub, "const html=")
    line = html[html.index("class=hidline"):]
    assert "<span>${esc(g.name)}</span>" in line, "the line names the column"
    assert "${badge(g)}" in line, "the line carries the number the header would have shown"
    assert "${eye(g,true)}" in line, "the line ends with the header's own eye, drawn slashed"


def test_one_eye_and_one_badge_serve_the_header_and_the_line(hub):
    m = re.search(r"[,;]eye=\(g,(\w+)=hid\.has\(g\.name\)\)=>", hub.PAGE)
    assert m, "the eye takes the slashed state as an argument: the line draws it slashed without a second control"
    flag = m.group(1)
    assert f'${{{flag}?"show":"hide"}} this column' in hub.PAGE, \
        "the title names what the click does, exactly as the header's does"
    assert f"{flag}?'<path d=\"M3 3l18 18\"/>':\"\"" in hub.PAGE, "the flag is what draws the slash"
    badge = _page_line(hub, "const badge=")
    # re-anchored (#2287): the badge shows the count against the column's card limit, through the one colCount rule
    assert "colCount(g.n,COLS[g.name])" in badge and "fits[g.name].length" in badge, "one badge, read by the header and by the line"
    assert "badge(g)" in _page_line(hub, "const one=")


def test_the_lines_eye_reuses_the_boards_one_click_handler(hub):
    assert hub.PAGE.count('const t=e.target.closest("[data-eye]")') == 1, \
        "the line's eye adds no second handler (#1809); the accordion header's handler only steps aside for it"
    m = re.search(r'document\.addEventListener\("click",e=>\{const t=e\.target\.closest\("\[data-eye\]"\).*?\},true\);',
                  hub.PAGE, re.S)
    assert m, "the handler the header's eye already used is still there"
    handler = m.group(0)
    assert ".hidline" in handler and "bo.kept" in handler, "the line's eye keeps the column shown"
    assert "bo.hidden" in handler, "... and the header's eye is still the one that writes the user's hidden set"


def test_the_divider_is_the_only_thing_between_the_chips_and_the_lines(hub):
    assert _prop(_css(hub, ".hidbar .hidsep"), "border-top"), "the divider the auto-hidden lines sit under"


def test_the_line_is_quieter_than_a_chip_and_wears_the_columns_own_colour(hub):
    chip, line = _css(hub, CHIP), _css(hub, LINE)
    assert _prop(chip, "background"), "the chip keeps the fill that tells the two kinds apart"
    assert _prop(line, "background") in (None, "none"), "the line carries no chip fill"
    assert _prop(line, "border-left") == "2px solid var(--cc,var(--mute))", \
        "the 2px tick is the column's own colour, the value the header's dot carries (#1793)"
    tokens = (ROOT_SCRIPTS / "web-tokens.css").read_text()

    def resolve(value):
        m = re.fullmatch(r"var\((--[\w-]+)\)", value or "")
        return re.search(rf"{m.group(1)}:(#[0-9a-f]{{6}})", tokens).group(1) if m else value

    chip_colour, line_colour = resolve(_prop(chip, "color")), resolve(_prop(line, "color"))
    assert re.fullmatch(r"#[0-9a-f]{6}", chip_colour or "") and re.fullmatch(r"#[0-9a-f]{6}", line_colour or ""), \
        f"both kinds name a colour: chip {chip_colour!r}, line {line_colour!r}"
    assert _lum(line_colour) < _lum(chip_colour), \
        f"the line's text is dimmer than the chip's: {line_colour} vs {chip_colour}"


# --- what must not change ---------------------------------------------------------------------------------------

def test_the_count_is_never_flush_against_the_name_in_either_rendering(hub):
    """`Todo0` is what the empty column's header read while it dropped the gap it was laying out with (#1809)."""
    for sel in ("body.dockb .ch", "#bbody .ah"):
        gap = _prop(_css(hub, sel), "gap")
        assert gap and gap != "0", f"{sel} keeps the name and the count apart, and states the gap: {gap!r}"
    css = re.sub(r"/\*.*?\*/", "", hub.PAGE[:hub.PAGE.index("</style>")], flags=re.S)
    assert not re.search(r"\.(?:ch|ah)\{[^}]*justify-content:flex-start", css), \
        "a header that packs its spans together is `Todo0` again (#1809)"


def test_a_column_that_holds_a_card_paints_exactly_as_before(hub):
    assert "flex:1.15 1 0" in _css(hub, "body.dockb #bbody>.col.prs")
    assert "justify-content:space-between" in _css(hub, "body.dockb .ch")
    assert _prop(_css(hub, "body.dockb .col>.sc"), "display") is None, "the body of a column with cards is never hidden"
    assert "flex-direction:column" not in _css(hub, "body.dockb .ch")
