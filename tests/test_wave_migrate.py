"""The wave-label migration: proposals come from declared blockers, approved edges go through the validator, and a label
goes only after every approved edge reads back and only under the owner's grant (#1989).

The GitHub door (ghread) is replaced by a fake that keeps its issues in a dict. No case reaches GitHub.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MODULE = SCRIPTS / "wave_migrate.py"

sys.path.insert(0, str(SCRIPTS))
import ghread  # noqa: E402

_spec = importlib.util.spec_from_file_location("wave_migrate", MODULE)
wave_migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wave_migrate)

REPO = "o/r"


def issue(number, blocked_by=(), parent=None, state="open", labels=(), body="", title=None):
    return {"id": 1000 + number, "state": state, "parent": parent, "blocked_by": list(blocked_by),
            "labels": list(labels), "body": body, "title": title or f"issue {number}"}


class FakeDoor:
    def __init__(self, issues, ignore_writes=False):
        self.issues = issues
        self.ignore_writes = ignore_writes
        self.writes = []

    def read(self, route, transport=None):
        rows = [{"number": n, "title": v["title"], "body": v["body"], "state": v["state"], "repo": REPO,
                 "labels": [{"name": label} for label in v["labels"]]}
                for n, v in self.issues.items() if v["state"] == "open"]
        return ghread.Read(kind=ghread.ANSWER, answer=rows)

    def graphql_read(self, query, variables=None, transport=None, timeout=None, pass_name=None):
        number = variables["number"]
        if number not in self.issues:
            return ghread.Read(kind=ghread.FAILED, detail=f"no issue {number}")
        return ghread.Read(kind=ghread.ANSWER, answer={"data": {"repository": {"issue": self._shape(number)}}})

    def _shape(self, number):
        node = self.issues[number]
        kids = [n for n, v in self.issues.items() if v["parent"] == number]
        return {
            "databaseId": node["id"], "state": node["state"].upper(),
            "labels": {"nodes": [{"name": label} for label in node["labels"]]},
            "parent": {"number": node["parent"]} if node["parent"] else None,
            "subIssues": {"pageInfo": {"hasNextPage": False}, "nodes": [{"number": n} for n in kids]},
            "blockedBy": {"pageInfo": {"hasNextPage": False},
                          "nodes": [{"number": b, "state": self.issues[b]["state"].upper(),
                                     "repository": {"nameWithOwner": REPO}} for b in node["blocked_by"]]},
        }

    def write(self, route, method="POST", fields=None, transport=None):
        self.writes.append({"method": method, "route": route, "fields": fields})
        if method == "DELETE":
            number = int(route.split("/issues/")[1].split("/")[0])
            self.issues[number]["labels"] = [label for label in self.issues[number]["labels"]
                                             if label != route.rsplit("/", 1)[1]]
            return ghread.Write(kind=ghread.DONE, answer={})
        blocked = int(route.split("/issues/")[1].split("/")[0])
        blocker = next(n for n, v in self.issues.items() if v["id"] == fields["issue_id"])
        if not self.ignore_writes:
            self.issues[blocked]["blocked_by"].append(blocker)
        return ghread.Write(kind=ghread.DONE, answer={})

    def posts(self):
        return [(int(w["route"].split("/issues/")[1].split("/")[0]), next(n for n, v in self.issues.items() if v["id"] == w["fields"]["issue_id"]))
                for w in self.writes if w["method"] == "POST"]

    def deletes(self):
        return [w["route"].rsplit("/", 1)[1] for w in self.writes if w["method"] == "DELETE"]


@pytest.fixture
def github(monkeypatch):
    def install(door):
        monkeypatch.setattr(ghread, "read", door.read)
        monkeypatch.setattr(ghread, "graphql_read", door.graphql_read)
        monkeypatch.setattr(ghread, "write", door.write)
        return door
    return install


def approved(tmp_path, pairs):
    path = tmp_path / "approved.json"
    path.write_text(json.dumps([{"blocked": b, "blocker": k} for b, k in pairs]), encoding="utf-8")
    return path


def test_the_dry_run_lists_every_open_card_that_carries_a_wave_label(github):
    door = github(FakeDoor({
        1: issue(1, labels=["wave:01"]),
        2: issue(2, labels=["bug", "wave:02"]),
        3: issue(3, labels=["bug"]),
        4: issue(4, state="closed", labels=["wave:01"]),
    }))
    result = wave_migrate.migrate(REPO)
    assert [(c["number"], c["wave"]) for c in result["cards"]] == [(1, 1), (2, 2)]
    assert result["mode"] == "dry-run"
    assert door.writes == []


def test_a_wave_number_alone_proposes_no_edge(github):
    door = github(FakeDoor({1: issue(1, labels=["wave:01"]), 2: issue(2, labels=["wave:02"])}))
    result = wave_migrate.migrate(REPO)
    assert result["proposed"] == []
    assert door.writes == []


def test_a_declared_blocker_is_only_a_proposal_in_the_dry_run(github):
    door = github(FakeDoor({
        1: issue(1, labels=["wave:01"]),
        2: issue(2, labels=["wave:01"]),
        3: issue(3, labels=["wave:02"], body="Work.\n\nBlocked by #1, #2\n"),
    }))
    result = wave_migrate.migrate(REPO)
    assert {(p["blocked"], p["blocker"]) for p in result["proposed"]} == {(3, 1), (3, 2)}
    assert door.writes == []


def test_the_approved_file_is_the_only_source_of_writes(github, tmp_path):
    door = github(FakeDoor({
        1: issue(1, labels=["wave:01"]),
        2: issue(2, labels=["wave:01"]),
        3: issue(3, labels=["wave:02"], body="Blocked by #1, #2"),
    }))
    result = wave_migrate.migrate(REPO, approved=approved(tmp_path, [(3, 1)]))
    assert door.posts() == [(3, 1)]
    assert [w["status"] for w in result["writes"]] == ["written"]


def test_an_approved_edge_that_makes_a_cycle_is_refused_and_never_posted(github, tmp_path):
    door = github(FakeDoor({
        1: issue(1, blocked_by=[2], labels=["wave:01"]),
        2: issue(2, labels=["wave:01"]),
    }))
    result = wave_migrate.migrate(REPO, approved=approved(tmp_path, [(2, 1)]), owner_grant=True)
    assert result["writes"][0]["status"] == "refused"
    assert result["writes"][0]["path"] == ["o/r#1", "o/r#2", "o/r#1"]
    assert door.posts() == []
    assert door.deletes() == []


def test_no_label_is_removed_before_the_edges_read_back(github, tmp_path):
    door = github(FakeDoor({
        1: issue(1, labels=["wave:01"]),
        3: issue(3, labels=["wave:02"]),
    }, ignore_writes=True))
    result = wave_migrate.migrate(REPO, approved=approved(tmp_path, [(3, 1)]), owner_grant=True)
    assert result["writes"][0]["status"] == "unconfirmed"
    assert result["removal"] == "skipped"
    assert door.deletes() == []


def test_the_label_removal_waits_for_the_owners_grant(github, tmp_path):
    door = github(FakeDoor({
        1: issue(1, labels=["wave:01"]),
        3: issue(3, labels=["wave:02"]),
    }))
    result = wave_migrate.migrate(REPO, approved=approved(tmp_path, [(3, 1)]))
    assert result["writes"][0]["status"] == "written"
    assert result["removal"] == "held-for-grant"
    assert door.deletes() == []

    result = wave_migrate.migrate(REPO, approved=approved(tmp_path, [(3, 1)]), owner_grant=True)
    assert result["writes"][0]["status"] == "exists"
    assert result["removal"] == "done"
    assert sorted(door.deletes()) == ["wave:01", "wave:02"]
    assert door.issues[1]["labels"] == [] and door.issues[3]["labels"] == []


def test_the_command_line_never_sets_the_grant_by_default(github, tmp_path, capsys):
    door = github(FakeDoor({1: issue(1, labels=["wave:01"]), 3: issue(3, labels=["wave:02"])}))
    assert wave_migrate.main([REPO, "--approved", str(approved(tmp_path, [(3, 1)]))]) == 0
    assert json.loads(capsys.readouterr().out)["removal"] == "held-for-grant"
    assert door.deletes() == []
