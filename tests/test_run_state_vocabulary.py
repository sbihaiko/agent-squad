"""One run-state vocabulary: the hub's run badges and the run dashboard's verdict chip and legend name and paint a state alike (#1408)."""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from tests.dashboard_helpers import PAGE_SRC, run_node

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
# The verdict values a record can carry, plus the two the surfaces add: the hub's `starting`/`verified`/`unverified`, the dashboard's no-verdict.
RECORD_VERDICTS = ["completed", "failed", "needs-human"]
PALETTE = {"ok": "#3ecf8e", "err": "#ff6b6b", "warn": "#f5b041", "run": "#2dd4bf", "none": "#b79cf6", "wait": "#e8d44d"}
# The four marks of the one state table: a filled dot, a hollow dot, a bar, or none. A texture is not a mark:
# dotted against dashed reads as one texture, so a border style never names a difference (#1137).
MARKS = ("filled", "hollow", "bar", "none")
MARK_GLYPHS = {"filled": "●", "hollow": "○", "bar": "▌", "none": ""}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dash_states():
    return run_node([{"op": "legend"}])[0]["states"]


def test_hub_and_dashboard_consume_the_served_state_table(hub):
    table = json.loads((ROOT / "contrib" / "squad-goal" / "scripts" / "run-states.json").read_text())
    assert hub.RUN_STATES == table
    assert dash_states() == table


def test_each_served_row_names_state_tone_mark_and_word(hub):
    rows = json.loads((ROOT / "contrib" / "squad-goal" / "scripts" / "run-states.json").read_text())
    assert all({"state", "tone", "mark", "word"} <= row.keys() for row in rows)
    assert all(row["tone"] == row["color"] and row["word"] == row["text"] for row in rows)


def test_hub_and_dashboard_share_one_state_table_with_one_label_tone_and_mark_per_state(hub):
    hub_rows = {r["state"]: (r["label"], r["color"], r["css"], r["mark"]) for r in hub.RUN_STATES}
    dash_rows = {r["state"]: (r["label"], r["color"], r["css"], r["mark"]) for r in dash_states()}
    assert hub_rows == dash_rows
    assert len({r["state"] for r in hub.RUN_STATES}) == len(hub.RUN_STATES)
    assert {r["color"] for r in hub.RUN_STATES} <= set(PALETTE)


def test_every_row_names_one_of_the_four_marks_and_no_tone_shares_a_mark(hub):
    """The mark differs within a tone at chip size: no two rows of one tone share a mark, and every mark is one of the
    four named shapes. The glyphs are geometric shapes — a filled dot, a hollow dot, a bar — never a border texture."""
    for rows in (hub.RUN_STATES, dash_states()):
        for r in rows:
            assert r["mark"] in MARKS, r["state"]
        for tone in {r["color"] for r in rows}:
            marks = [r["mark"] for r in rows if r["color"] == tone]
            assert len(marks) == len(set(marks)), (tone, marks)
    assert set(MARK_GLYPHS) == set(MARKS)
    assert len({g for g in MARK_GLYPHS.values() if g}) == 3, "the three drawn marks are pairwise distinct shapes"


def test_every_state_the_hub_can_show_is_in_the_table(hub):
    states = {r["state"] for r in hub.RUN_STATES}
    assert {"completed", "verified", "unverified", "failed", "needs-human", "running", "starting", "stalled"} <= states
    assert hub.state_of({"verdict": None, "reasons": [], "land": False}, 99999) == "stalled"   # a silent run is the stalled state
    assert "stale" not in states   # "(stale)" is the page's one caption, never a state row (#1137)
    for v in RECORD_VERDICTS:
        assert hub.state_of({"verdict": v, "reasons": [], "land": False}, 0) in states


def test_the_hub_serves_no_legend_and_its_badge_classes_all_have_a_colour_rule(hub):
    css = hub.PAGE.split("<style>")[1].split("</style>")[0]
    for r in hub.RUN_STATES:
        assert re.search(rf"\.b\.{re.escape(r['state'].replace(' ', '-'))}\s*\{{[^}}]*color:var\(--{r['color']}\)", css), r["state"]
    assert "id=lg" not in hub.PAGE and "#lg" not in hub.PAGE and "RUN_STATE_LEGEND" not in hub.PAGE   # the hub carries no legend (#1417)
    assert "var(--none)" in css and "--none:" + PALETTE["none"] in css


def test_the_hub_completed_badge_is_green_and_verified_is_a_distinct_legend_named_treatment(hub):
    rows = {r["state"]: r for r in hub.RUN_STATES}
    assert rows["completed"]["color"] == "ok"
    assert rows["verified"]["label"] != rows["completed"]["label"] and rows["verified"]["label"].strip()
    css = hub.PAGE.split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.b\.completed\s*\{[^}]*var\(--ok\)", css)
    assert re.search(r"\.b\.verified\s*\{[^}]*font-style:italic", css) and "#56d364" not in css


def test_no_record_verdict_falls_through_to_the_failure_style_on_the_dashboard():
    src = PAGE_SRC()
    chips = {r["state"]: r["cls"] for r in dash_states() if not r.get("hub_only")}
    for v in RECORD_VERDICTS + ["running", "stalled", "no verdict yet"]:
        assert v in chips, v
    assert "Object.fromEntries(core.RUN_STATES" in src   # the class map is derived from the one table: the page maps no state fact of its own
    assert '{ completed: "v-ok"' not in src
    panel = (ROOT / "dashboard" / "page" / "panel.js").read_text()
    assert 'core.RUN_STATES.find((row) => row.state === st.state)' in panel
    assert '{ completed: "state-completed"' not in panel
    assert chips["needs-human"] != "v-bad" and chips["completed"] != "v-bad"   # a record verdict never falls through to the failure style
    assert {r["label"] for r in dash_states() if r["state"] == "needs-human"} == {"Run needs a person"}
    painted = dict(re.findall(r"#vchip\.(v-[a-z]+) \{ border-color:var\(--([a-z]+)\)", src))
    color = {r["state"]: r["color"] for r in dash_states()}
    assert all(painted[chips[s]] == color[s] for s in chips)


def test_the_legend_draws_no_run_state_row(hub):
    """The owner removed the run-state group from the side legend (2026-10-11): the strip lists node outcomes and interaction only.

    The vocabulary itself is untouched: the states stay one table, shared with the hub, and the surfaces that print a state draw it from there."""
    menu = (ROOT / "dashboard" / "page" / "menu.js").read_text()
    assert "Run states" not in menu and "runPill" not in menu and "RUN_LEGEND" not in menu
    # Node kinds joins the two groups the owner kept (#914 names each kind by icon and colour), and the order holds.
    assert re.findall(r'\["(\w+)", "([^"]+)"\]', menu)[:3] == [("node", "Node outcomes"), ("kind", "Node kinds"), ("interaction", "Interaction")]
    assert ".rlg" not in PAGE_SRC()
    assert {r["state"] for r in dash_states()} == {r["state"] for r in hub.RUN_STATES}


def test_the_dashboard_prints_the_same_text_the_hub_badges_the_state_with(hub):
    """The run header pill and the sidebar pill read one text: the dashboard's map comes from the one table."""
    text = run_node([{"op": "legend"}])[0].get("state_text", {})
    assert set(text) == {r["state"] for r in hub.RUN_STATES}
    for r in hub.RUN_STATES:
        assert text[r["state"]] == hub.badge_of(r["state"])["text"], r["state"]


def test_the_page_paints_every_state_pill_the_way_the_hub_paints_its_badge(hub):
    src = PAGE_SRC()
    for r in hub.RUN_STATES:
        rule = re.search(rf"\.b\.{re.escape(r['state'].replace(' ', '-'))}\s*\{{([^}}]*)\}}", src)
        assert rule, r["state"]
        body = rule.group(1)
        assert f"color:var(--{r['color']})" in body, r["state"]
        for prop in (p for p in r["css"].split(";") if p):
            assert prop in body, (r["state"], prop)
    assert "core.STATE_TEXT[st.state]" in src   # the run header chip takes its words from the one table


def test_the_hub_badge_draws_its_row_mark_before_the_word(hub):
    """Every hub badge carries its row's mark at chip size: a filled dot, a hollow dot or a bar drawn before the word;
    `none` draws nothing. The mark is a shape, never a border texture."""
    css = hub.PAGE.split("<style>")[1].split("</style>")[0]
    for r in hub.RUN_STATES:
        state = r["state"].replace(" ", "-")
        m = re.search(rf"\.b\.{re.escape(state)}::before\s*\{{([^}}]*)\}}", css)
        if r["mark"] == "none":
            assert not m, r["state"]
        else:
            assert m and f'content:"{MARK_GLYPHS[r["mark"]]} "' in m.group(1), r["state"]


def test_the_phone_run_row_draws_the_shared_state_word_and_mark(hub):
    """The phone maps no state fact of its own: the served row names its state, and the page paints it with the one
    state table's chip — the same rule the dashboard badge uses — so a state reads alike on every surface (#1142)."""
    src = Path(hub.__file__).read_text()
    assert '"mark": b["mark"]' in src and '"tone": b["color"]' in src   # every served run row carries its state's tone and mark
    mobile = hub.MOBILE_PAGE.decode() if isinstance(hub.MOBILE_PAGE, bytes) else hub.MOBILE_PAGE
    for r in hub.RUN_STATES:
        assert hub.run_state_css(r) in mobile, r["state"]   # the shared chip rule reaches the phone
    assert 'className = "run-state b " + String(r.state || "").replace(/ /g, "-")' in mobile
    assert "state.textContent = r.badge" in mobile
    assert "dataset.tone" not in mobile and "dataset.mk" not in mobile, "the phone keeps a second paint table of its own"


def test_the_dashboard_chip_draws_its_row_mark_and_reads_the_class_from_the_table(hub):
    src = PAGE_SRC()
    for mark in ("filled", "hollow", "bar"):
        assert re.search(rf"#vchip\[data-mk={mark}\]::before", src), mark
    assert 'chip.dataset.mk = (row && row.mark) || "none"' in src


def test_a_no_repro_run_reads_neutral_and_apart_from_failed(hub):
    rows = {r["state"]: r for r in hub.RUN_STATES}
    assert rows["no-repro"]["color"] == "edge" and rows["no-repro"]["color"] != rows["failed"]["color"]
    assert hub.state_of({"verdict": "no-repro", "reasons": [], "land": False}, 0) == "no-repro"
    assert hub.badge_of("no-repro")["color"] == "edge"
