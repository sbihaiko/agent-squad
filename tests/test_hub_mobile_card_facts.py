"""Card facts on the phone card face (#1444, SPEC 1115).

A phone card reads the same board row the desktop card reads: the PR badge and CI rollup for its
PR/CI chip, the blockers for its blocked marker, `ho` for its running marker and `o` for its age.
No new field and no phone-only rule: the parity test pins both renderers to the same row fields.

The phone card is checked two ways: the field parity against the desktop card's renderer, and the
chip text the renderer draws for each row shape, run under node over a stub DOM.
"""
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
MOBILE_HTML = SCRIPTS / "hub-mobile.html"
NODE = "node"

# The row fields both card faces read, by fact (#1444). The desktop chip names the badge (`pb`)
# and the CI rollup; the no-PR read and the linked PRs ride `prf`, the blockers `b`, a live run
# `ho`, the opened date `o`.
SHARED_FIELDS = ("pb", "rollup", "prf", "b", "ho", "o")

STUB = (
    "const mk=()=>({className:'',dataset:{},textContent:'',children:[],classList:{add(){}},"
    "setAttribute(){},append(...c){this.children.push(...c)}});"
    "const document={createElement:mk};"
    "const specColor=()=>'#000';const mSlots=()=>({});const cardSpec=x=>x.sp?x.n:x.pa;const cardColor=()=>'#000';"
    "const reasons={};"
    "const txt=x=>x.textContent+x.children.map(txt).join('');"
)


def _hub():
    import importlib.util
    import os
    import tempfile
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_card_facts", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub():
    return _hub()


def _phone_card_source():
    fn = re.search(r"^function cardButton\(.*?\n\}\n", MOBILE_HTML.read_text(), re.M | re.S)
    assert fn, "the phone's card renderer is gone"
    return fn.group(0)


@pytest.mark.node_bound
def test_the_phone_and_desktop_cards_name_the_same_row_fields(hub):
    """Both card faces read one row's fields, so the two cannot drift (#1444)."""
    desktop = re.search(r"^const cards=.*$", hub.PAGE, re.M)
    assert desktop, "the desktop card renderer is gone"
    phone = _phone_card_source()
    for field in SHARED_FIELDS:
        assert f"x.{field}" in desktop.group(0), f"the desktop card does not read the row's {field}"
        assert f"x.{field}" in phone, f"the phone card does not read the row's {field}"


def _card_text(row):
    script = STUB + _phone_card_source() + f"console.log(JSON.stringify(txt(cardButton({json.dumps(row)},0))));"
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _iso(**kw):
    return (datetime.now(UTC) - timedelta(**kw)).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.mark.node_bound
def test_a_pr_card_draws_its_badge_and_ci_chip():
    text = _card_text({"n": 31, "t": "a pull request", "l": [], "kind": "pr",
                       "pb": "reviewing", "rollup": "pending"})
    assert "reviewing #31 · CI pending" in text, text


@pytest.mark.node_bound
def test_a_ci_state_the_row_does_not_carry_reads_unknown_never_passing():
    text = _card_text({"n": 31, "t": "a pull request", "l": [], "kind": "pr", "pb": "ready"})
    assert "CI unknown" in text, text
    assert "CI pass" not in text, text


@pytest.mark.node_bound
def test_a_card_with_no_pr_says_so():
    text = _card_text({"n": 5, "t": "a ticket", "l": []})
    assert "no PR" in text, text


@pytest.mark.node_bound
def test_an_issue_draws_the_prs_that_close_it():
    text = _card_text({"n": 5, "t": "a ticket", "l": [],
                       "prf": [{"n": 31, "ci": "pass"}, {"n": 32, "ci": "fail"}]})
    assert "#31 · CI pass" in text and "#32 · CI fail" in text, text


@pytest.mark.node_bound
def test_blocked_and_running_markers_ride_the_row():
    text = _card_text({"n": 5, "t": "a ticket", "l": [], "b": [9], "ho": "run-a"})
    assert "blocked" in text and "running" in text, text
    clear = _card_text({"n": 5, "t": "a ticket", "l": []})
    assert "blocked" not in clear and "running" not in clear, clear


@pytest.mark.node_bound
def test_the_age_reads_one_short_rung():
    minutes = _card_text({"n": 5, "t": "a ticket", "l": [], "o": _iso(minutes=45)})
    hours = _card_text({"n": 5, "t": "a ticket", "l": [], "o": _iso(hours=5)})
    days = _card_text({"n": 5, "t": "a ticket", "l": [], "o": _iso(days=3)})
    assert "45m" in minutes, minutes
    assert "5h" in hours and "45m" not in hours, hours
    assert "3d" in days and "h" not in days.split("3d")[0], days
