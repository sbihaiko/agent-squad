"""One run-state vocabulary: the hub's run badges and the run dashboard's verdict chip and legend name and paint a state alike (#1408)."""
import importlib.util
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
PALETTE = {"ok": "#3ecf8e", "err": "#ff6b6b", "warn": "#f5b041", "run": "#2dd4bf", "none": "#b79cf6"}


@pytest.fixture(scope="module")
def hub():
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dash_states():
    return run_node([{"op": "legend"}])[0]["states"]


def legend_run_rows():
    """The run-state rows of the side legend, as the page builds them: the pill the hub's sidebar draws for that state, then the short description."""
    return run_node([{"op": "legend"}])[0].get("run_states", [])


def test_hub_and_dashboard_share_one_state_table_with_one_label_and_one_colour_per_state(hub):
    hub_rows = {r["state"]: (r["label"], r["color"], r["css"]) for r in hub.RUN_STATES}
    dash_rows = {r["state"]: (r["label"], r["color"], r["css"]) for r in dash_states()}
    assert hub_rows == dash_rows
    assert len({r["state"] for r in hub.RUN_STATES}) == len(hub.RUN_STATES)
    assert {r["color"] for r in hub.RUN_STATES} <= set(PALETTE)


def test_every_state_the_hub_can_show_is_in_the_table(hub):
    states = {r["state"] for r in hub.RUN_STATES}
    assert {"completed", "verified", "unverified", "failed", "needs-human", "running", "starting", "stalled"} <= states
    assert hub.state_of({"verdict": None, "reasons": [], "land": False}, 99999) == "stalled"   # one name for stale/stalled
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
    mapped = {k.strip(): v for k, v in re.findall(r'"?([a-z -]+)"?: "(v-[a-z]+)"', src.split("var VERDICT_CLASS")[1].split("function renderChrome")[0])}
    for v in RECORD_VERDICTS + ["running", "stalled", "no verdict yet"]:
        assert v in chips and mapped.get(v) == chips[v], v
    assert mapped["needs-human"] != "v-bad" and mapped["completed"] != "v-bad"
    assert {r["label"] for r in dash_states() if r["state"] == "needs-human"} == {"Run needs a person"}
    painted = dict(re.findall(r"#vchip\.(v-[a-z]+) \{ border-color:var\(--([a-z]+)\)", src))
    color = {r["state"]: r["color"] for r in dash_states()}
    assert all(painted[chips[s]] == color[s] for s in chips)


def test_the_legend_draws_the_sidebar_pill_for_every_state_in_the_hub_table_order(hub):
    """One legend row per state, in the hub's order, drawing the same pill: text, colour and border style, then the short description."""
    want = [(r["state"], hub.badge_of(r["state"])["text"], r["color"], r["css"], r["label"]) for r in hub.RUN_STATES]
    got = [(r.get("state"), r.get("text"), r.get("color"), r.get("css"), r.get("label")) for r in legend_run_rows()]
    assert got == want


def test_no_legend_row_names_a_state_no_surface_shows(hub):
    rows = legend_run_rows()
    assert len(rows) == len({r.get("state") for r in rows})   # one row per state
    assert {r.get("state") for r in rows} - {r["state"] for r in hub.RUN_STATES} == set()


def test_the_dashboard_prints_the_same_text_the_hub_badges_the_state_with(hub):
    """The run header pill and the sidebar pill read one text: the dashboard's map comes from the one table."""
    text = run_node([{"op": "legend"}])[0].get("state_text", {})
    assert set(text) == {r["state"] for r in hub.RUN_STATES}
    for r in hub.RUN_STATES:
        assert text[r["state"]] == hub.badge_of(r["state"])["text"], r["state"]


def test_the_page_paints_every_legend_pill_the_way_the_hub_paints_its_badge(hub):
    src = PAGE_SRC()
    for r in hub.RUN_STATES:
        rule = re.search(rf"\.b\.{re.escape(r['state'].replace(' ', '-'))}\s*\{{[^}}]*color:var\(--{r['color']}\)", src)
        assert rule, r["state"]
        for prop in (p for p in r["css"].split(";") if p):
            assert prop in rule.group(0), (r["state"], prop)
    assert "core.STATE_TEXT[st.state]" in src   # the run header chip takes its words from the one table
