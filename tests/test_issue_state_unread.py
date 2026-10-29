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
        self.reads = 0

    def read_all(self):
        self.reads += 1
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


def test_fresh_cache_go_read_rescues_missing_requested_issue_without_board_read(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": issue_state.time.time(), "repo": "owner/ariel",
                                 "items": [{"n": 801, "st": "Todo"}]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    binding = FakeBinding({"801": "Todo"}, nodes={"800": "Todo"})
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(binding))

    assert issue_state.main(["issue_state.py", "--go", "800,801"]) == 0

    assert capsys.readouterr().out.splitlines() == ["800\tTodo", "801\tTodo"]
    assert binding.reads == 0
    assert binding.asked == ["800"]


def test_fresh_cache_is_bypassed_for_another_repository(tmp_path, monkeypatch):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": issue_state.time.time(), "repo": "owner/ariel",
                                 "items": [{"n": 801, "st": "Todo"}]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(FakeBinding({"999": "Done"})))
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        assert issue_state.main(["issue_state.py", "--repo", "other/repo", "--go", "801"]) == 0
    assert out.getvalue().splitlines() == ["999\tDone", "UNREAD\t801"]


def test_missing_and_stale_cache_fall_back_to_board_read(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    calls = []

    def read(repo=None):
        calls.append(repo)
        return issue_state.State(FakeBinding({"801": "Todo"}))

    monkeypatch.setattr(issue_state, "board_state", read)
    for payload in (None, {"t": issue_state.time.time() - 301, "repo": "owner/ariel", "items": []}):
        if payload is None:
            cache.unlink(missing_ok=True)
        else:
            cache.write_text(json.dumps(payload))
        assert issue_state.main(["issue_state.py", "--go", "801"]) == 0
        assert capsys.readouterr().out.splitlines() == ["801\tTodo"]
    assert calls == [None, None]


def test_go_check_prints_the_unread_line_and_alerts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(FakeBinding({"801": "Todo"})))

    assert issue_state.main(["issue_state.py", "--go", "800,801"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert "801\tTodo" in out
    assert "UNREAD\t800" in out
    assert "800" in (tmp_path / "alerts.log").read_text()
