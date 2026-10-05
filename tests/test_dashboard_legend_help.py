"""The legend and the help overlay (#85, #118)."""
import re

from tests.dashboard_helpers import PAGE_SRC, run_node


def controls_in(src):
    """Every mouse and keyboard control the page's code answers, as a token. The help rows must cover exactly this set."""
    found = set()
    for event, token in (("wheel", "wheel"), ("contextmenu", "contextmenu")):
        if f'addEventListener("{event}"' in src:
            found.add(token)
    if '$("map").addEventListener("pointerdown", startPan)' in src:
        found.add("pan")
    if "startDrag(" in src:
        found.add("drag-node")
    if "startLink(" in src:
        found.add("drag-port")
    if '$("edges").addEventListener("pointerdown"' in src:
        found.add("select-link")
    if "wireSquares(g, gr)" in src and 'querySelectorAll("tr[data-child]")' in src:
        found.add("select-square")
    if "S.lastTap" in src:
        found.add("expand-group")
    if "S.lastDown" in src:
        found.add("double-press")
    found |= {f"key:{k}" for k in re.findall(r'e\.key === "([^"]+)"', src)}
    found |= {f"key:{k}" for k in re.findall(r'\bk === "([a-z])"', src)}
    return found


def test_the_legend_has_a_row_for_every_card_color_the_page_paints_and_no_other():
    legend = run_node([{"op": "legend"}])[0]["legend"]
    painted = set(re.findall(r"\.card\.([a-z][a-z-]*) rect", PAGE_SRC()))
    listed = {c for row in legend for c in [row["cls"], *row.get("also", [])]}
    assert listed == painted, f"legend only: {sorted(listed - painted)}; painted with no legend row: {sorted(painted - listed)}"
    assert all(row["label"].strip() for row in legend)


def test_the_legend_swatches_use_the_card_classes_so_a_swatch_cannot_drift_from_its_color():
    src = PAGE_SRC()
    assert 'id="legend"' in src
    assert "core.LEGEND" in src  # the page reads the rows from the core, never a copy
    assert '<g class="card ${' in src  # a swatch is a card group, so the card CSS paints it


def test_the_help_lists_every_mouse_and_keyboard_control_the_page_answers_and_no_other():
    rows = run_node([{"op": "legend"}])[0]["help"]
    covered = {t for row in rows for t in row["covers"]}
    found = controls_in(PAGE_SRC())
    assert covered == found, f"help only: {sorted(covered - found)}; answered with no help row: {sorted(found - covered)}"
    assert all(row["how"].strip() and row["what"].strip() for row in rows)


def test_every_button_and_file_label_in_the_page_has_a_title_because_the_help_lists_the_buttons_from_their_titles():
    src = PAGE_SRC()
    static = src[: src.index("<script>")]  # the buttons in the markup; the help reads their titles from the page
    bare = [m for m in re.findall(r"<button\b[^>]*>|<label class=\"btn\"[^>]*>", static) if "title=" not in m]
    assert not bare, bare


def test_the_verdict_chip_legend_lists_exactly_the_chip_classes_the_css_paints_and_the_chip_can_take():
    chips = run_node([{"op": "legend"}])[0]["chips"]
    listed = [row["cls"] for row in chips]
    painted = set(re.findall(r"#vchip\.(v-[a-z]+)", PAGE_SRC()))
    mapped = set(re.findall(r'"(v-[a-z]+)"', PAGE_SRC().split("var VERDICT_CLASS")[1].split("function renderChrome")[0])) | {"v-bad"}
    assert set(listed) == painted == mapped and len(set(listed)) == len(listed)
    assert all(row["label"].strip() for row in chips)


def test_no_verdict_yet_has_its_own_color_apart_from_the_amber_of_a_deadline_or_a_stall():
    src = PAGE_SRC()
    assert '"no verdict yet": "v-none"' in src
    color = dict(re.findall(r"#vchip\.(v-[a-z]+) \{ border-color:var\((--[a-z]+)\)", src))
    assert color["v-none"] != color["v-wait"] and color["v-none"] not in {color[c] for c in color if c != "v-none"}
    assert "--none" in src.split(".state-none")[1][:60]


def test_every_legend_state_row_carries_the_glyph_its_card_shows():
    legend = run_node([{"op": "legend"}])[0]["legend"]
    state = {"o-pass": "passed", "o-fail": "failed", "o-deadline": "deadline", "o-needs-human": "needs-human", "o-not-run": "not-run", "o-started": "started", "running": "running"}
    marks = run_node([{"op": "mark", "state": s} for s in state.values()])
    by = {row["cls"]: row.get("glyph") for row in legend}
    assert [by[c] for c in state] == [m["glyph"] for m in marks]
