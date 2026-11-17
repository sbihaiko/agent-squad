"""The desktop board's card names its issue number (#1268).

The hub page's card builder is one client function in the hub page's script. The test extracts it from the
page text, runs it under node with no DOM, feeds the board one card, and reads the drawn card: the number
`#<n>` is visible text on the card and sits in the link that opens the issue, the same link the GitHub
icon already was (ADR-0137: the rule is pure and runs with no host).
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NODE = shutil.which("node")

STUBS = """
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const colOf = () => "#000";
const CN = {};
const SLOTS = new Map();
const specColor = () => "var(--mute)";
const cardSpec = () => null;
const cardColor = () => "var(--mute)";
const tint = () => "";
const gstrip = () => "";
const lbc = () => "";
const age = () => "";
const ageC = () => "";
const bo = {labels: []};
"""


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_card_number", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def draw(hub, card):
    src = re.search(r"^const cards=.*$", hub.PAGE, re.M).group(0)
    script = STUBS + src + f"""
const card = {json.dumps(card)};
const html = cards({{name: "Todo"}}, [card], [card]);
const anchor = (html.match(/<a class="[^"]*id ghl[^"]*"[^>]*>.*?<\\/a>/) || [""])[0];
console.log(JSON.stringify({{html, anchor}}));
"""
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.node_bound
def test_a_drawn_card_shows_its_issue_number_as_a_link_like_the_title(hub):
    out = draw(hub, {"n": 1260, "t": "Name the card", "r": "x/y"})
    assert "#1260" in out["anchor"], "the card carries no visible #<n> link: " + out["html"][:400]
    assert 'href="https://github.com/x/y/issues/1260"' in out["anchor"]
