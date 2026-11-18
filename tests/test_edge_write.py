"""The native-edge writer: one blocking edge, written only after the deadlock validator passes it (#1985).

The GitHub door (ghread) is replaced by a fake that keeps its issues in a dict. No case reaches GitHub.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MODULE = SCRIPTS / "edge_write.py"
TICKETS_DOOR = ROOT / ".agents/skills/agent-to-tickets/SKILL.md"

sys.path.insert(0, str(SCRIPTS))
import ghread  # noqa: E402

_spec = importlib.util.spec_from_file_location("edge_write", MODULE)
edge_write = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edge_write)

REPO = "o/r"


def issue(number, blocked_by=(), parent=None, state="open", issue_id=None):
    return {"id": issue_id or 1000 + number, "state": state, "parent": parent, "blocked_by": list(blocked_by)}


class FakeDoor:
    def __init__(self, issues, fail_on=None, ignore_writes=False):
        self.issues = issues
        self.fail_on = fail_on
        self.ignore_writes = ignore_writes
        self.attempts = 0
        self.posts = []

    def graphql_read(self, query, variables=None, transport=None, timeout=None, pass_name=None):
        number = variables["number"]
        if number not in self.issues:
            return ghread.Read(kind=ghread.FAILED, detail=f"no issue {number}")
        return ghread.Read(kind=ghread.ANSWER, answer={"data": {"repository": {"issue": self._shape(number)}}})

    def _shape(self, number):
        node = self.issues[number]
        kids = [n for n, v in self.issues.items() if v["parent"] == number]
        return {
            "databaseId": node["id"], "state": node["state"].upper(), "labels": {"nodes": []},
            "parent": {"number": node["parent"]} if node["parent"] else None,
            "subIssues": {"pageInfo": {"hasNextPage": False}, "nodes": [{"number": n} for n in kids]},
            "blockedBy": {"pageInfo": {"hasNextPage": False},
                          "nodes": [{"number": b, "state": self.issues[b]["state"].upper(),
                                     "repository": {"nameWithOwner": REPO}} for b in node["blocked_by"]]},
        }

    def write(self, route, method="POST", fields=None, transport=None):
        self.attempts += 1
        if self.attempts == self.fail_on:
            return ghread.Write(kind=ghread.FAILED, detail="HTTP 503 while writing the edge")
        blocked = int(route.split("/issues/")[1].split("/")[0])
        blocker = next(n for n, v in self.issues.items() if v["id"] == fields["issue_id"])
        self.posts.append((blocked, blocker))
        if not self.ignore_writes:
            self.issues[blocked]["blocked_by"].append(blocker)
        return ghread.Write(kind=ghread.DONE, answer={})


@pytest.fixture
def github(monkeypatch):
    def install(door):
        monkeypatch.setattr(ghread, "graphql_read", door.graphql_read)
        monkeypatch.setattr(ghread, "write", door.write)
        return door
    return install


def test_a_valid_edge_is_written_once_and_read_back(github):
    door = github(FakeDoor({1: issue(1), 2: issue(2)}))
    result = edge_write.add_edge(REPO, 1, 2)
    assert result["status"] == "written"
    assert door.posts == [(1, 2)]
    assert door.issues[1]["blocked_by"] == [2]


def test_an_edge_that_makes_a_cycle_is_refused_with_its_path_and_never_posted(github):
    door = github(FakeDoor({1: issue(1, blocked_by=[2]), 2: issue(2)}))
    result = edge_write.add_edge(REPO, 2, 1)
    assert result["status"] == "refused"
    assert result["path"] == ["o/r#1", "o/r#2", "o/r#1"]
    assert any("o/r#1 > o/r#2 > o/r#1" in reason for reason in result["reasons"])
    assert door.posts == []


def test_an_edge_a_chain_of_open_tickets_already_gives_is_refused_as_redundant(github):
    door = github(FakeDoor({1: issue(1, blocked_by=[2]), 2: issue(2, blocked_by=[3]), 3: issue(3)}))
    result = edge_write.add_edge(REPO, 1, 3)
    assert result["status"] == "refused" and result["verdict"] == "redundant"
    assert result["path"] == ["o/r#1", "o/r#2", "o/r#3"]
    assert "o/r#1 > o/r#2 > o/r#3" in result["reasons"][0]
    assert door.posts == []


def test_a_chain_through_a_closed_ticket_does_not_make_an_edge_redundant(github):
    door = github(FakeDoor({1: issue(1, blocked_by=[2]), 2: issue(2, blocked_by=[3], state="closed"), 3: issue(3)}))
    assert edge_write.add_edge(REPO, 1, 3)["status"] == "written"
    assert door.posts == [(1, 3)]


def test_a_written_edge_names_each_older_direct_edge_it_makes_redundant(github):
    door = github(FakeDoor({1: issue(1, blocked_by=[3]), 2: issue(2, blocked_by=[3]), 3: issue(3), 4: issue(4)}))
    assert edge_write.add_edge(REPO, 1, 2)["now_redundant"] == [3]
    assert edge_write.add_edge(REPO, 1, 4)["now_redundant"] == []
    assert door.posts == [(1, 2), (1, 4)]


def test_a_redundant_edge_makes_the_command_exit_non_zero(github, capsys):
    github(FakeDoor({1: issue(1, blocked_by=[2]), 2: issue(2, blocked_by=[3]), 3: issue(3)}))
    assert edge_write.main([REPO, "1:3"]) == 1
    assert '"verdict": "redundant"' in capsys.readouterr().out


def test_the_ticket_rules_say_when_an_edge_is_real():
    """#2259: the writer cannot see a false edge between two tickets that only share a file, so the rule states it."""
    for path in ("config/vendor-skills-patches/to-tickets.md", "vendor/skills/skills/to-tickets/SKILL.md"):
        text = (ROOT / path).read_text(encoding="utf-8")
        for marker in ("**An edge names its reason.**", "an order of meaning", "the same symbol",
                       "A shared file alone is no edge", "`redundant`", "`now_redundant`"):
            assert marker in text, f"{path} does not state: {marker}"


def test_an_edge_that_already_exists_is_not_written_again(github):
    door = github(FakeDoor({1: issue(1, blocked_by=[2]), 2: issue(2)}))
    assert edge_write.add_edge(REPO, 1, 2)["status"] == "exists"
    assert door.posts == []


def test_an_unreadable_blocker_refuses_the_write_before_any_post(github):
    door = github(FakeDoor({1: issue(1)}))
    with pytest.raises(edge_write.GhFailed):
        edge_write.add_edge(REPO, 1, 99)
    assert door.posts == []


def test_a_closure_past_its_cap_is_unknown_and_refused(github, monkeypatch):
    monkeypatch.setattr(edge_write, "CLOSURE_CAP", 1)
    door = github(FakeDoor({1: issue(1), 2: issue(2)}))
    result = edge_write.add_edge(REPO, 1, 2)
    assert result["status"] == "refused" and result["verdict"] == "unknown"
    assert door.posts == []


def test_a_write_that_does_not_read_back_is_not_reported_as_written(github):
    github(FakeDoor({1: issue(1), 2: issue(2)}, ignore_writes=True))
    with pytest.raises(edge_write.WriteUnconfirmed):
        edge_write.add_edge(REPO, 1, 2)


def test_a_failed_publish_reruns_and_writes_each_missing_edge_once(github):
    door = github(FakeDoor({1: issue(1), 2: issue(2), 3: issue(3)}, fail_on=2))
    with pytest.raises(edge_write.GhFailed):
        edge_write.publish_edges(REPO, [(1, 2), (1, 3)])
    assert door.posts == [(1, 2)]
    door.fail_on = None
    results = edge_write.publish_edges(REPO, [(1, 2), (1, 3)])
    assert [r["status"] for r in results] == ["exists", "written"]
    assert door.posts == [(1, 2), (1, 3)]


def test_the_strict_read_never_goes_through_the_throttle_snapshot():
    text = MODULE.read_text(encoding="utf-8")
    for name in ("ghthrottle", "gh_aggregate", "SG_GHSNAP", "gh_snapshot", "aggregate("):
        assert name not in text, name


def test_the_to_tickets_door_writes_no_wave_label_and_uses_native_edges():
    text = TICKETS_DOOR.read_text(encoding="utf-8")
    assert "wave:" not in text
    assert "edge_write.py" in text
    assert "Premise: <premise>" in text
    assert "--refute" in text


def test_the_tracker_states_the_native_edge_contract_and_no_wave_label():
    text = (ROOT / "docs/agents/issue-tracker.md").read_text(encoding="utf-8")
    assert "wave:" not in text
    assert "edge_write.py" in text


@pytest.mark.parametrize("patch", ["to-tickets.md", "to-spec.md"])
def test_each_patch_states_the_contract_and_names_what_it_overrides(patch):
    text = (ROOT / "config/vendor-skills-patches" / patch).read_text(encoding="utf-8")
    assert "edge_write.py" in text
    assert "overrides" in text


def test_the_to_tickets_patch_quotes_the_upstream_sentence_it_overrides():
    upstream = (ROOT / "vendor/skills/skills/to-tickets/SKILL.md").read_text(encoding="utf-8")
    quoted = "otherwise set each ticket's \"Blocked by\" to the blocking issues"
    assert quoted in upstream
    assert quoted in (ROOT / "config/vendor-skills-patches/to-tickets.md").read_text(encoding="utf-8")
