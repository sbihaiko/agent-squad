"""A labelled or sorted issue list is never answered by the whole open list (#847).

The aggregate fact for `issues?state=open` is every open issue. A route that filters it further (`labels=`, `sort=`,
`direction=`) must stay a REST list: answering it with the whole fact handed a debt card to the bug pass.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _ghread():
    spec = importlib.util.spec_from_file_location("ghread_under_test", SCRIPTS / "ghread.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_plain_open_list_is_the_aggregate_fact():
    ghread = _ghread()
    assert ghread.aggregate("repos/o/r/issues?state=open") is not None
    assert ghread.aggregate("repos/o/r/issues?state=open&per_page=100") is not None


def test_a_labelled_open_list_is_a_rest_list_not_the_whole_open_list():
    ghread = _ghread()
    assert ghread.aggregate("repos/o/r/issues?state=open&labels=bug&per_page=100") is None
    assert ghread.aggregate("repos/o/r/issues?state=open&labels=bug&per_page=100&sort=created&direction=asc") is None
    assert ghread.transport_name("repos/o/r/issues?state=open&labels=debt") == "REST"


def test_an_issue_fact_carries_its_creation_time_for_the_cluster_window():
    # cluster.py joins two issues by their titles' area only within the creation window; a fact without created_at
    # silently splits every cluster into one child per symptom.
    ghread = _ghread()
    node = {"number": 7, "title": "a: b", "body": "", "state": "OPEN", "createdAt": "2026-10-20T10:00:00Z",
            "labels": {"nodes": []}, "blockedBy": {"nodes": []}, "subIssuesSummary": {"total": 0, "completed": 0},
            "subIssues": {"nodes": []}, "comments": {"nodes": []}}
    assert ghread._issue(node)["created_at"] == "2026-10-20T10:00:00Z"
