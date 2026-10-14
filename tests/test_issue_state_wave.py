"""A wave marked as a delivery becomes a milestone, and its label follows the milestone (#303).

A wave is an ordering label (`wave:NN`) by default. When the owner marks a wave as a delivery, one milestone
carries it and the wave label is derived from the milestone, never kept beside it. The carrier is the
binding's: a caller names the wave and its issues. A wave that spans repositories is refused and stays a label.
A milestone the owner did not date has no due date. Every write is proven by the read that follows it. The
carrier is a stub gh that keeps milestones, and each issue's milestone and labels; no test writes live.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_wave", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)


class Repo:
    """A stub gh over repository x/y. `drop` makes an issue PATCH answer ok and never land."""

    def __init__(self, drop=False, milestones=None, labels=("wave:7", "bug")):
        self.drop = drop
        self.milestones = list(milestones or [])
        self.issues = {n: {"milestone": None, "labels": list(labels)} for n in (10, 11)}
        self.calls = []

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        self.calls.append((s, input))
        out = ""
        if "/milestones" in s and "POST" in s:
            body = json.loads(input)
            m = {"number": len(self.milestones) + 1, "title": body["title"], "due_on": body.get("due_on")}
            self.milestones.append(m)
            out = json.dumps(m)
        elif "/milestones" in s:
            out = json.dumps(self.milestones)
        elif "/labels/" in s and "DELETE" in s:
            n = int(s.split("/issues/")[1].split("/")[0])
            self.issues[n]["labels"].remove(s.rsplit("/labels/", 1)[1].replace("%3A", ":"))
        elif "/issues/" in s and "PATCH" in s:
            n = int(s.split("/issues/")[1].split()[0])
            if not self.drop:
                self.issues[n]["milestone"] = json.loads(input)["milestone"]
            out = "{}"
        elif "/issues/" in s:
            n = int(s.split("/issues/")[1].split()[0])
            i = self.issues[n]
            ms = next((m for m in self.milestones if m["number"] == i["milestone"]), None)
            out = json.dumps({"number": n, "milestone": ms, "labels": [{"name": x} for x in i["labels"]]})
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, repo):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=repo, env={"SG_BOARD_READ_BACKOFF": "0"})


def test_a_wave_marked_as_a_delivery_gets_one_milestone_and_its_label_follows_it(tmp_path):
    repo = Repo()
    state = seam(tmp_path, repo)
    assert state.wave(10) == "wave:7"   # by default the label orders
    got = state.deliver_wave("wave:7", [10, 11])
    assert got["milestone"] == "wave:7" and got["issues"] == [10, 11]
    assert [m["title"] for m in repo.milestones] == ["wave:7"]
    assert all(i["milestone"] == 1 for i in repo.issues.values())
    assert all("wave:7" not in i["labels"] and "bug" in i["labels"] for i in repo.issues.values())   # the label is derived, never kept beside it
    assert state.wave(10) == "wave:7" and state.wave(11) == "wave:7"
    state.deliver_wave("wave:7", [10, 11])
    assert len(repo.milestones) == 1   # one milestone per wave, however often it is marked


def test_a_wave_that_spans_repositories_is_refused_and_stays_a_label(tmp_path):
    repo = Repo()
    state = seam(tmp_path, repo)
    with pytest.raises(issue_state.WaveSpansRepositories) as e:
        state.deliver_wave("wave:7", [10, "other/repo#5"])
    assert "x/y" in str(e.value) and "other/repo" in str(e.value)
    assert repo.calls == [] and repo.milestones == []
    assert state.wave(10) == "wave:7"


def test_no_due_date_is_set_on_a_milestone_the_owner_did_not_date(tmp_path):
    repo = Repo()
    seam(tmp_path, repo).deliver_wave("wave:7", [10])
    assert "due_on" not in json.loads(next(i for s, i in repo.calls if "POST" in s and "/milestones" in s))
    repo = Repo()
    seam(tmp_path, repo).deliver_wave("wave:7", [10], due="2026-12-01T00:00:00Z")
    assert repo.milestones[0]["due_on"] == "2026-12-01T00:00:00Z"


def test_a_milestone_write_the_next_read_does_not_carry_is_a_named_failure(tmp_path):
    repo = Repo(drop=True)
    state = seam(tmp_path, repo)
    with pytest.raises(issue_state.NotCarried) as e:
        state.deliver_wave("wave:7", [10])
    assert "wave:7" in str(e.value)
    assert "wave:7" in repo.issues[10]["labels"]   # a label is never dropped for a milestone no read carries
