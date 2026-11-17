"""The native-edge writer: one blocking edge, written only after the deadlock validator passes it (#1985).

Every case uses a fake `gh` that keeps its issues in a dict. No case reaches GitHub.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MODULE = SCRIPTS / "edge_write.py"
TICKETS_DOOR = ROOT / ".agents/skills/agent-to-tickets/SKILL.md"

sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("edge_write", MODULE)
edge_write = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edge_write)

REPO = "o/r"
URL = "https://api.github.com/repos/o/r"


def issue(number, blocked_by=(), parent=None, state="open", issue_id=None):
    return {"id": issue_id or 1000 + number, "state": state, "parent": parent, "blocked_by": list(blocked_by)}


class FakeGh:
    def __init__(self, issues, fail_on=None, ignore_writes=False):
        self.issues = issues
        self.fail_on = fail_on
        self.ignore_writes = ignore_writes
        self.attempts = 0
        self.posts = []

    def __call__(self, argv):
        if "--method" in argv:
            return self._post(argv)
        route = argv[1].split("?")[0]
        number = int(route.split("/issues/")[1].split("/")[0])
        if number not in self.issues:
            raise edge_write.GhFailed(f"HTTP 404 for issue {number}")
        node = self.issues[number]
        if route.endswith("/dependencies/blocked_by"):
            return json.dumps([{"number": b, "state": self.issues[b]["state"], "repository_url": URL} for b in node["blocked_by"]])
        if route.endswith("/sub_issues"):
            kids = [n for n, v in self.issues.items() if v["parent"] == number]
            return json.dumps([{"number": n, "state": self.issues[n]["state"]} for n in kids])
        parent = f"{URL}/issues/{node['parent']}" if node["parent"] else None
        return json.dumps({"id": node["id"], "number": number, "state": node["state"], "labels": [],
                           "parent_issue_url": parent})

    def _post(self, argv):
        self.attempts += 1
        if self.attempts == self.fail_on:
            raise edge_write.GhFailed("HTTP 503 while writing the edge")
        blocked = int(argv[3].split("/issues/")[1].split("/")[0])
        blocker_id = int(argv[-1].split("=")[1])
        blocker = next(n for n, v in self.issues.items() if v["id"] == blocker_id)
        self.posts.append((blocked, blocker))
        if not self.ignore_writes:
            self.issues[blocked]["blocked_by"].append(blocker)
        return "{}"


def test_a_valid_edge_is_written_once_and_read_back():
    fake = FakeGh({1: issue(1), 2: issue(2)})
    result = edge_write.add_edge(REPO, 1, 2, run=fake)
    assert result["status"] == "written"
    assert fake.posts == [(1, 2)]
    assert fake.issues[1]["blocked_by"] == [2]


def test_an_edge_that_makes_a_cycle_is_refused_with_its_path_and_never_posted():
    fake = FakeGh({1: issue(1, blocked_by=[2]), 2: issue(2)})
    result = edge_write.add_edge(REPO, 2, 1, run=fake)
    assert result["status"] == "refused"
    assert result["path"] == ["o/r#1", "o/r#2", "o/r#1"]
    assert any("o/r#1 > o/r#2 > o/r#1" in reason for reason in result["reasons"])
    assert fake.posts == []


def test_an_edge_that_already_exists_is_not_written_again():
    fake = FakeGh({1: issue(1, blocked_by=[2]), 2: issue(2)})
    assert edge_write.add_edge(REPO, 1, 2, run=fake)["status"] == "exists"
    assert fake.posts == []


def test_an_unreadable_blocker_refuses_the_write_before_any_post():
    fake = FakeGh({1: issue(1)})
    with pytest.raises(edge_write.GhFailed):
        edge_write.add_edge(REPO, 1, 99, run=fake)
    assert fake.posts == []


def test_a_closure_past_its_cap_is_unknown_and_refused(monkeypatch):
    monkeypatch.setattr(edge_write, "CLOSURE_CAP", 1)
    fake = FakeGh({1: issue(1), 2: issue(2)})
    result = edge_write.add_edge(REPO, 1, 2, run=fake)
    assert result["status"] == "refused" and result["verdict"] == "unknown"
    assert fake.posts == []


def test_a_write_that_does_not_read_back_is_not_reported_as_written():
    fake = FakeGh({1: issue(1), 2: issue(2)}, ignore_writes=True)
    with pytest.raises(edge_write.WriteUnconfirmed):
        edge_write.add_edge(REPO, 1, 2, run=fake)


def test_a_failed_publish_reruns_and_writes_each_missing_edge_once():
    fake = FakeGh({1: issue(1), 2: issue(2), 3: issue(3)}, fail_on=2)
    with pytest.raises(edge_write.GhFailed):
        edge_write.publish_edges(REPO, [(1, 2), (1, 3)], run=fake)
    assert fake.posts == [(1, 2)]
    fake.fail_on = None
    results = edge_write.publish_edges(REPO, [(1, 2), (1, 3)], run=fake)
    assert [r["status"] for r in results] == ["exists", "written"]
    assert fake.posts == [(1, 2), (1, 3)]


def test_the_strict_read_never_goes_through_the_throttle_snapshot():
    text = MODULE.read_text(encoding="utf-8")
    for name in ("ghthrottle", "gh_aggregate", "SG_GHSNAP", "gh_snapshot"):
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
