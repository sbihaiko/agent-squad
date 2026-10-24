"""A go-labelled issue with no readable card is named, and a node id known from an earlier read rescues it (#800)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)


class FakeBinding:
    name = "fake"

    def __init__(self, carried, nodes=None):
        self.carried = carried
        self.nodes = nodes or {}
        self.asked = []

    def read_all(self):
        return dict(self.carried)

    def node_status(self, issue):
        self.asked.append(str(issue))
        return self.nodes.get(str(issue))


def test_unread_names_a_go_issue_the_board_read_does_not_carry():
    binding = FakeBinding({"801": "Todo"})

    carried, rescued, missing = issue_state.State(binding).unread([800, 801, 802])

    assert carried == {"801": "Todo"}
    assert rescued == {}
    assert missing == ["800", "802"]


def test_unread_rescues_a_missing_card_that_its_node_id_resolves_in_todo():
    binding = FakeBinding({"801": "Todo"}, nodes={"800": "Todo"})

    _, rescued, missing = issue_state.State(binding).unread([800, 801])

    assert rescued == {"800": "Todo"}
    assert missing == []


def test_unread_reads_no_node_for_a_card_the_board_read_carries():
    binding = FakeBinding({"801": "Todo"}, nodes={"801": "Testing"})

    issue_state.State(binding).unread([801])

    assert binding.asked == []


def test_node_status_reads_the_status_of_the_item_its_known_id_names(tmp_path):
    calls = []
    answer = {"data": {"node": {"isArchived": False, "content": {"number": 800},
                                "fieldValueByName": {"name": "Todo"}}}}

    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(answer), stderr="")

    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    (tmp_path / b._known_file).write_text(json.dumps({"800": "PVTI_x"}))

    assert b.node_status(800) == "Todo"
    assert any("PVTI_x" in " ".join(a) for a in calls)


def test_node_status_without_a_known_id_makes_no_call(tmp_path):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))

    assert b.node_status(800) is None
    assert calls == []


def test_a_node_id_seen_in_one_read_is_kept_for_the_next(tmp_path):
    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=None, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    b.items_map = {"800": "PVTI_x", "801": "PVTI_y"}

    b._save_items()
    b.items_map = {"801": "PVTI_y"}   # a later read no longer carries 800
    b._save_items()

    assert json.loads((tmp_path / b._known_file).read_text()) == {"800": "PVTI_x", "801": "PVTI_y"}


def test_go_check_prints_the_unread_line_and_alerts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(FakeBinding({"801": "Todo"})))

    assert issue_state.main(["issue_state.py", "--go", "800,801"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert "801\tTodo" in out
    assert "UNREAD\t800" in out
    assert "800" in (tmp_path / "alerts.log").read_text()
