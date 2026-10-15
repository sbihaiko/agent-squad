"""#1481: one `Test-<Kind>` type label per scenario issue, applied by publish and write-back, converging on re-publish.

The runner models GitHub's issues, their labels (a PATCH replaces them all) and the sub-issue tree. No network.
"""
import json

from squad import to_test_scenario as tts
from tests.test_gui_test_scenario import script
from tests.test_to_test_scenario_areas import batch
from tests.test_to_test_scenario_tree_publish import TreeGitHub
from tests.test_to_test_scenario_write_back_areas import results

OLD = {"testcase", "testarea", "testrun", "gui-test-run"}


class LabelGitHub(TreeGitHub):
    def __call__(self, op, **kw):
        if op == "create_issue":
            out = super().__call__(op, **kw)
            self.issues[out["number"]]["labels"] = list(kw["labels"])
            return out
        if op == "update_issue":
            self.calls.append((op, kw))
            self.issues[kw["number"]].update(title=kw["title"], body=kw["body"], labels=list(kw["labels"]))
            return {}
        if op == "set_labels":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["labels"] = list(kw["labels"])
            return {}
        if op == "close":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["state"] = "closed"
            return {}
        if op == "set_body":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["body"] = kw["body"]
            return {}
        if op == "issue_state":
            held = self.issues[kw["number"]]
            return {"state": held["state"], "verdict": None, "title": held["title"], "labels": list(held["labels"])}
        return super().__call__(op, **kw)

    def by_title(self):
        return {i["title"]: i["labels"] for i in self.issues.values()}


def published(tmp_path):
    plan = tts.build_plan(script(batches=[batch("aa-x", 2), batch("bb-x", 1)]))
    gh, path = LabelGitHub(), tmp_path / "m.gui-test.issues.json"
    tts.publish(plan, gh, map_path=path)
    return plan, gh, path


def kind(labels):
    return sorted(name for name in labels if name.startswith("Test-"))


def test_publish_gives_every_issue_exactly_one_type_label(tmp_path):
    _, gh, _ = published(tmp_path)
    seen = {}
    for title, labels in gh.by_title().items():
        head = "Test scenario" if title.startswith("Test scenario") else title.split(" ")[1].rstrip(":")
        seen.setdefault(head, set()).add(tuple(kind(labels)))
    assert seen["Test scenario"] == {("Test-Scenario",)}
    assert seen["Area"] == {("Test-Area",)}
    assert seen["Cases"] == {("Test-Group",)}
    assert {("Test-Case",)} == {t for h, s in seen.items() if h not in ("Test scenario", "Area", "Cases") for t in s}
    assert not any(OLD & set(labels) for labels in gh.by_title().values())
    assert all(set(labels) & tts.ISSUE_TYPE_LABELS for labels in gh.by_title().values())
    assert {"Test-Scenario", "Test-Area", "Test-Group", "Test-Case"} <= {c["name"] for c in gh.of("ensure_label")}


def test_republish_replaces_old_labels_keeps_others_and_the_second_pass_is_a_no_op(tmp_path):
    plan, gh, path = published(tmp_path)
    for issue in gh.issues.values():
        issue["labels"] = [("testarea" if name == "Test-Area" else "testcase" if name == "Test-Case" else name)
                           for name in issue["labels"] if name != "Test-Group"] + (["custom"] if "Test-Area" in issue["labels"] else [])
        if issue["title"].startswith("📋"):
            issue["labels"] = ["gui-test-run", "custom"]
    tts.publish(plan, gh, map_path=path)
    by = gh.by_title()
    assert all(not (OLD & set(labels)) for labels in by.values())
    assert all(len(kind(labels)) == 1 for labels in by.values())
    assert all("custom" in labels for title, labels in by.items() if title.startswith("⚪ Area") or title.startswith("📋"))
    before = json.dumps(gh.by_title(), sort_keys=True)
    gh.calls.clear()
    tts.publish(plan, gh, map_path=path)
    assert gh.of("set_labels") == []
    assert json.dumps(gh.by_title(), sort_keys=True) == before


def test_write_back_labels_run_issues_test_run_and_their_month_container_test_group(tmp_path):
    plan, gh, path = published(tmp_path)
    tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", {"AAX-001": "passed"}), path, gh)
    by = gh.by_title()
    runs = [labels for title, labels in by.items() if " Run 2026" in title]
    months = [labels for title, labels in by.items() if title.startswith("📅")]
    assert runs and all(kind(labels) == ["Test-Run"] and "testrun" not in labels for labels in runs)
    assert months and all(kind(labels) == ["Test-Group"] for labels in months)
    assert all(set(labels) & tts.ISSUE_TYPE_LABELS for labels in runs + months)
