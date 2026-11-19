"""Every issue writer sets the birth Priority and holds the card until it reads back (#1978, ADR-0144).

A spec or a task is born low in Backlog. A bug is born high in Todo, a loop-bug urgent in Todo. The shell writers ask
the one table (priority_backfill.py) for the Priority, and the seam writes it only when it changes, then reads it back.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PATCHES = ROOT / "config" / "vendor-skills-patches"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


issue_state = _load("issue_state_birth", SCRIPTS / "issue_state.py")

OPTIONS = ["urgent", "high", "normal", "low"]
FIELDS = [{"id": "F_PRIO", "name": "Priority", "dataType": "SINGLE_SELECT",
           "options": [{"id": f"O_{name}", "name": name} for name in OPTIONS]}]


class Board:
    """A stub gh over cards that carry a Priority name or None. `drop` makes a PATCH answer ok and never land."""

    def __init__(self, cards, drop=False):
        self.cards = dict(cards)
        self.drop = drop
        self.patches = []

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        if "-X" in cmd and "PATCH" in cmd:
            url = cmd[cmd.index("PATCH") + 1]
            self.patches.append(url)
            if not self.drop:
                self.cards[int(url.rsplit("/", 1)[1].lstrip("I"))] = json.loads(input)["fields"][0]["value"][2:]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "/fields?" in s:
            out = json.dumps(FIELDS)
        elif "/items?" in s:
            items = [{"id": f"I{n}", "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y"},
                      "fields": [{"id": "F_PRIO", "name": "Priority", "value": {"name": v} if v else None}]}
                     for n, v in self.cards.items()]
            out = subprocess.run(["jq", "-r", cmd[cmd.index("--jq") + 1]], input=json.dumps(items),
                                 capture_output=True, text=True, check=True).stdout
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def state(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board,
                                   env={"SG_BOARD_READ_BACKOFF": "0"})


@pytest.mark.parametrize("labels, priority", [
    (["bug"], "high"),
    (["bug", "loop-bug"], "urgent"),
    (["bug", "loop-bug:landing"], "urgent"),
    (["spec"], "low"),
    ([], "low"),
])
def test_the_cli_prints_the_birth_priority_for_the_shell_writers(labels, priority):
    out = subprocess.run([sys.executable, str(SCRIPTS / "priority_backfill.py"), *labels],
                         capture_output=True, text=True, check=True).stdout
    assert out.split() == [priority]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the stub board filters its items with jq")
def test_a_new_priority_is_written_once_and_read_back(tmp_path):
    board = Board({42: None})

    assert state(tmp_path, board).set_priority(42, "high") is True
    assert board.cards[42] == "high" and board.patches == ["/users/o/projectsV2/4/items/I42"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the stub board filters its items with jq")
def test_an_unchanged_priority_causes_no_board_write(tmp_path):
    board = Board({42: "urgent"})

    assert state(tmp_path, board).set_priority(42, "urgent") is False
    assert board.patches == []


@pytest.mark.skipif(shutil.which("jq") is None, reason="the stub board filters its items with jq")
def test_a_priority_that_does_not_read_back_is_named_never_written(tmp_path):
    board = Board({42: None}, drop=True)

    with pytest.raises(issue_state.NotCarried):
        state(tmp_path, board).set_priority(42, "high")


def test_the_to_tickets_patch_states_the_publish_hold_and_no_label_guard():
    text = (PATCHES / "to-tickets.md").read_text()
    order = text.split("**Publish order, per ticket.**", 1)[1].split("\n\n", 1)[0]
    assert "publish hold" in order and "Priority low" in order and "parent link" in order
    assert "ready-for-agent" not in order


def test_the_to_spec_patch_births_a_spec_low_in_backlog():
    text = (PATCHES / "to-spec.md").read_text()
    assert "Priority low" in text and "Backlog" in text and "publish hold" in text


@pytest.mark.parametrize("patch,kind", [("to-spec.md", "spec"), ("to-tickets.md", "ticket")])
def test_each_publish_patch_gates_the_card_on_the_backlog_limit(patch, kind):
    # #2436: a full Backlog holds the card out, so the publisher runs the gate and reports its refusal.
    text = (PATCHES / patch).read_text()
    gate = text.split("**A full Backlog refuses the publish.**", 1)[1].split("\n\n", 1)[0]
    assert f"backlog-gate.sh {kind}" in gate and "add no card" in gate


@pytest.mark.parametrize("skill,kind", [("to-spec", "spec"), ("to-tickets", "ticket")])
def test_the_vendored_copy_carries_the_backlog_gate(skill, kind):
    text = (ROOT / "vendor" / "skills" / "skills" / skill / "SKILL.md").read_text()
    assert f"backlog-gate.sh {kind}" in text


@pytest.mark.parametrize("skill", ["to-spec", "to-tickets"])
def test_the_vendored_copy_carries_the_birth_rule(skill):
    text = (ROOT / "vendor" / "skills" / "skills" / skill / "SKILL.md").read_text()
    assert "Priority low" in text and "publish hold" in text
