"""The board highlights a run's cards and a card's runs (#906).

Selecting a run marks the board cards of its issues and its fix PR, in the hub's own repository, and dims the rest. Selecting
a card marks the runs that name its issue. A match the board hides (a collapsed swimlane, a filter) is counted as "N related".
The rule is pure JavaScript in the hub page, run here under node with no browser. The selection is never stored, and the
page reads nothing new: the rule works on the board and run rows the page already holds.
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
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    for k, v in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": tempfile.mkdtemp(),
                 "SG_HUB_DIR": tempfile.mkdtemp()}.items():
        os.environ.setdefault(k, v)
    spec = importlib.util.spec_from_file_location("hub_run_highlight", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def block(hub):
    m = re.search(r"// highlight:begin\n(.*?)// highlight:end", hub.PAGE, re.S)
    assert m, "the page carries the highlight rule between its begin and end markers"
    return m.group(1)


def _node(block, script):
    if NODE is None:
        pytest.skip("node is required")
    src = 'const HUB_REPO = "x/y";\n' + block + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


BOARD = [
    {"n": 5, "r": "x/y", "kind": "issue"},          # a run's issue, in an open lane
    {"n": 6, "r": "x/y", "kind": "issue"},          # not the run's
    {"n": 7, "r": "x/y", "kind": "issue"},          # a run's issue, in a collapsed lane
    {"n": 9, "r": "x/y", "kind": "pr"},             # the run's fix PR
    {"n": 5, "r": "other/repo", "kind": "issue"},   # the same number in another repository
    {"n": 10, "r": "x/y", "kind": "pr"},            # not the run's
]
RUNS = [
    {"id": "a", "issues": ["5", "7"], "fix": None},
    {"id": "b", "issues": ["8"], "fix": None},
    {"id": "c", "issues": ["3"], "fix": 7},
]


def test_a_run_selection_marks_exactly_its_cards(hub, block):
    out = _node(block, f"""
const items = {json.dumps(BOARD)};
const run = {{issues: ["5", "7"], fix: 9}};
console.log(JSON.stringify(hlCards(run, items).map(hlKey)));
""")
    assert out == ["x/y#issue#5", "x/y#issue#7", "x/y#pr#9"]


def test_a_card_selection_marks_the_runs_that_name_its_issue(hub, block):
    out = _node(block, f"""
const runs = {json.dumps(RUNS)};
console.log(JSON.stringify({{
  issue: hlRuns({{n: 7, r: "x/y", kind: "issue"}}, runs).map(r => r.id),
  pr: hlRuns({{n: 7, r: "x/y", kind: "pr"}}, runs).map(r => r.id),
}}));
""")
    assert out == {"issue": ["a"], "pr": ["c"]}


def test_a_hidden_match_is_counted_as_related(hub, block):
    out = _node(block, f"""
const items = {json.dumps(BOARD)};
const run = {{issues: ["5", "7"], fix: 9}};
const rendered = new Set(["x/y#issue#5", "x/y#pr#9"]);
console.log(JSON.stringify(hlHidden(hlCards(run, items), rendered).map(hlKey)));
""")
    assert out == ["x/y#issue#7"]


def test_no_selection_marks_nothing(hub, block):
    out = _node(block, f"""
const items = {json.dumps(BOARD)};
console.log(JSON.stringify([hlCards(null, items).length, hlRuns(null, {json.dumps(RUNS)}).length]));
""")
    assert out == [0, 0]


def test_the_rule_never_stores_or_reads(hub, block):
    assert "localStorage" not in block
    assert "fetch(" not in block


def test_the_page_scripts_still_parse(hub):
    if NODE is None:
        pytest.skip("node is required")
    for script in re.findall(r"<script>(.*?)</script>", hub.PAGE, re.S):
        p = subprocess.run([NODE, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=script,
                           capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
