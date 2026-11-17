"""Guard for #2072: a ticket's premises and decisions are checked before dispatch (ADR-0155, ADR-0142).

A ticket states its premises as `Premise: <claim> (<path:line>)`, the decisions it cites as `Decision <name>: "<text>"`,
and the decisions it needs as `Needs decision: <name>`. The check runs once for each content revision of a ticket and
keeps its evidence; a later dispatch of the same revision reuses that evidence. A needed decision with no cited source
holds the ticket, and the hold line names the decision.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from squad import ticket_premise as tp

ROOT = Path(__file__).resolve().parent.parent

BODY = (
    "Build the export.\n"
    "Premise: the registry resolves the spelling (squad/module.py:3)\n"
    "Premise: the flow holds one run (docs/design/flow.md:3)\n"
    "Decision export-format: \"Write the export as one JSON object per line.\"\n"
    "Needs decision: export-format\n"
)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "squad").mkdir()
    (tmp_path / "squad/module.py").write_text("line one\nline two\nthe registry decides the route\n")
    (tmp_path / "docs/design").mkdir(parents=True)
    (tmp_path / "docs/design/flow.md").write_text("# Flow\n\nThe loop holds one run.\n")
    return tmp_path


def test_check_runs_once_per_revision_and_reuses_its_evidence(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    calls = []
    real = tp.check

    def counting(body, root):
        calls.append(body)
        return real(body, root)

    monkeypatch.setattr(tp, "check", counting)
    first, reused_first = tp.evidence(2072, BODY, state, repo)
    second, reused_second = tp.evidence(2072, BODY, state, repo)
    assert len(calls) == 1
    assert reused_first is False
    assert reused_second is True
    assert second == first


def test_a_new_revision_runs_the_check_again(repo, tmp_path, monkeypatch):
    state = tmp_path / "state"
    calls = []
    real = tp.check

    def counting(body, root):
        calls.append(body)
        return real(body, root)

    monkeypatch.setattr(tp, "check", counting)
    tp.evidence(2072, BODY, state, repo)
    ev, reused = tp.evidence(2072, BODY + "A new line.\n", state, repo)
    assert len(calls) == 2
    assert reused is False
    assert ev["revision"] == tp.revision(BODY + "A new line.\n")


def test_the_check_reads_the_cited_files(repo):
    ev = tp.check(BODY, repo)
    states = {row["premise"]: row["state"] for row in ev["rows"]}
    assert states["the registry resolves the spelling"] == "proven"
    assert states["the flow holds one run"] == "proven"


def test_a_citation_that_the_repository_lacks_stays_open(repo):
    body = "Premise: the module exists (squad/missing.py:1)\n"
    ev = tp.check(body, repo)
    assert ev["rows"][0]["state"] == "open"


def test_a_cited_decision_is_carried_verbatim(repo):
    ev = tp.check(BODY, repo)
    assert tp.decision_lines(ev) == ['DECISION export-format: "Write the export as one JSON object per line."']
    assert tp.hold_line(2072, ev) == ""


def test_a_needed_decision_with_no_source_holds_and_names_it(repo):
    body = "Build the export.\nNeeds decision: export-format\n"
    ev = tp.check(body, repo)
    assert tp.decision_lines(ev) == []
    assert tp.hold_line(2072, ev) == 'HOLD #2072: missing decision "export-format"'


def test_the_cli_exits_one_on_a_hold_and_names_the_decision(repo, tmp_path):
    issue = tmp_path / "issue-2072.json"
    issue.write_text(json.dumps({"number": 2072, "body": "Build the export.\nNeeds decision: export-format\n"}))
    run = subprocess.run([sys.executable, "-m", "squad.ticket_premise", "hold", str(issue),
                          "--state", str(tmp_path / "state"), "--root", str(repo)],
                         cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == tp.EX_HOLD
    assert run.stdout.strip() == 'HOLD #2072: missing decision "export-format"'


def test_the_cli_prints_the_cited_decision_and_exits_zero(repo, tmp_path):
    issue = tmp_path / "issue-2072.json"
    issue.write_text(json.dumps({"number": 2072, "body": BODY}))
    args = [sys.executable, "-m", "squad.ticket_premise"]
    common = ["--state", str(tmp_path / "state"), "--root", str(repo)]
    hold = subprocess.run(args + ["hold", str(issue)] + common, cwd=ROOT, capture_output=True, text=True)
    decisions = subprocess.run(args + ["decisions", str(issue)] + common, cwd=ROOT, capture_output=True, text=True)
    assert hold.returncode == 0
    assert decisions.stdout.strip() == 'DECISION export-format: "Write the export as one JSON object per line."'


def test_the_child_checklist_follows_adr_0000_on_amendments():
    checklist = (ROOT / "contrib/squad-goal/scripts/req-checklist.txt").read_text()
    assert "do not amend an accepted ADR" not in checklist
    assert "(ADR-0000)" in checklist
