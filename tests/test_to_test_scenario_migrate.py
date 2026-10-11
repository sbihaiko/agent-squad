"""ADR-0275 slice D (MesenAI #1475): `publish` migrates a flat scenario into areas in one resumable pass.

A flat map has no `"layout": "areas"` and its case sub-issues hang from the scenario, like #1286's. The runner is
the tree stand-in of the slice C tests; a duplicate issue, a duplicate link or a closed issue is an observable failure.
"""
import json

import pytest

from squad import to_test_scenario as tts
from tests.test_to_test_scenario_tree_publish import TreeGitHub, pad_plan

KNOWN = 100
CAP = 100


class CappedGitHub(TreeGitHub):
    """GitHub's cap on top of the tree stand-in: a parent already holding 100 sub-issues refuses the 101st."""

    def __call__(self, op, **kw):
        if op == "add_sub_issue":
            held = self.kids.get(kw["parent"], [])
            sub = next(n for n, i in self.issues.items() if i["id"] == kw["sub"])
            if sub not in held and len(held) >= CAP and not kw.get("replace_parent"):
                raise RuntimeError("422: Parent cannot have more than 100 sub-issues")
        return super().__call__(op, **kw)


def flat_scenario(gh, plan, path, known=KNOWN):
    """A flat publish as it was: the scenario, then `known` case records linked straight to it, and the flat map.

    The records are the first 90 and the last 10 of the plan, so a creation in an early area comes before a move in
    the last one unless the migration orders them."""
    scenario = gh("create_issue", title="Scenario", body="b", labels=[])
    state = {"parent": scenario["number"], "boarded": True, "cases": {}}
    for issue in plan["issues"][:known - 10] + plan["issues"][-10:]:
        made = gh("create_issue", title=f"⚪ {issue['title']}", body="old", labels=[])
        gh("add_sub_issue", parent=scenario["number"], sub=made["id"])
        state["cases"][issue["cases"][0]] = {"number": made["number"], "id": made["id"], "hash": issue.get("hash")}
    path.write_text(json.dumps(state), encoding="utf-8")
    return scenario["number"]


def test_a_100_record_flat_map_migrates_100_reparented_the_rest_created_and_layout_written(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    scenario = flat_scenario(gh, plan, path)
    flat_numbers = set(gh.kids[scenario])
    before = gh.creates
    out = tts.publish(plan, gh, map_path=path)
    moves = [kw for kw in gh.of("add_sub_issue") if kw.get("replace_parent")]
    assert len(moves) == KNOWN and {gh.issues[n]["id"] for n in flat_numbers} == {kw["sub"] for kw in moves}
    assert gh.creates - before == 16 + 16 + (281 - KNOWN)
    assert len(out["created"]) == 281 - KNOWN
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["layout"] == "areas" and len(saved["cases"]) == 281
    assert all(c["container"] for c in saved["cases"].values())
    assert len(gh.kids[scenario]) == 16 and not flat_numbers & set(gh.kids[scenario])
    assert sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281
    assert all(i["state"] == "open" for i in gh.issues.values())
    assert not [op for op, _ in gh.calls if op in ("close", "delete_issue", "remove_sub_issue")]


def test_the_layout_is_written_last_so_an_interrupted_migration_is_still_flat(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    flat_scenario(gh, plan, path)
    gh.fail_on_create = gh.creates + 40                 # dies while it creates the cap's leftovers
    with pytest.raises(RuntimeError):
        tts.publish(plan, gh, map_path=path)
    assert "layout" not in json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("flaky", ["create", "link"])
def test_an_interrupted_migration_resumes_with_no_duplicate_issue_or_link(tmp_path, flaky):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    flat_scenario(gh, plan, path)
    if flaky == "create":
        gh.fail_on_create = gh.creates + 40
    else:
        gh.fail_on_link = gh.links + 140
    with pytest.raises(RuntimeError):
        tts.publish(plan, gh, map_path=path)
    gh.fail_on_create = gh.fail_on_link = None
    out = tts.publish(plan, gh, map_path=path)
    assert len(gh.issues) == 1 + KNOWN + 16 + 16 + (281 - KNOWN)
    assert sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281
    assert len(gh.kids[out["parent"]]) == 16
    assert json.loads(path.read_text(encoding="utf-8"))["layout"] == "areas"
    assert all(i["state"] == "open" for i in gh.issues.values())


def test_the_records_move_before_the_leftovers_are_created(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    flat_scenario(gh, plan, path)
    mark = len(gh.calls)
    tts.publish(plan, gh, map_path=path)
    ops = [(op, kw) for op, kw in gh.calls[mark:] if op in ("create_issue", "add_sub_issue")]
    last_move = max(n for n, (op, kw) in enumerate(ops) if kw.get("replace_parent"))
    titles = {f"⚪ {i['title']}" for i in plan["issues"]}
    first_record = min(n for n, (op, kw) in enumerate(ops) if op == "create_issue" and kw["title"] in titles)
    assert last_move < first_record


def test_a_dry_run_of_a_flat_map_counts_the_move_and_the_create_and_writes_nothing(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    flat_scenario(gh, plan, path)
    mark, text = len(gh.calls), path.read_text(encoding="utf-8")
    out = tts.publish(plan, gh, map_path=path, dry_run=True)
    assert len(gh.calls) == mark and path.read_text(encoding="utf-8") == text
    assert out["counts"]["move"] == KNOWN and out["counts"]["create"] == 16 + 16 + (281 - KNOWN)


def test_a_full_parent_migrates_because_the_areas_are_linked_after_the_records_move(tmp_path):
    """#1286 as it is: the scenario holds the cap's 100 records, so an area link before the moves is a 422."""
    plan, gh, path = pad_plan(), CappedGitHub(), tmp_path / "m.json"
    scenario = flat_scenario(gh, plan, path)
    flat_numbers = set(gh.kids[scenario])
    mark = len(gh.calls)
    out = tts.publish(plan, gh, map_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["layout"] == "areas" and len(saved["cases"]) == 281
    assert len(gh.kids[scenario]) == 16 and not flat_numbers & set(gh.kids[scenario])
    links = [kw for op, kw in gh.calls[mark:] if op == "add_sub_issue"]
    assert [kw["parent"] for kw in links[-16:]] == [scenario] * 16      # the areas go last, on an emptied parent
    assert all(kw["parent"] != scenario for kw in links[:-16])
    assert len(out["created"]) == 281 - KNOWN
    assert sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281


def test_an_interrupted_full_parent_migration_resumes_and_links_the_areas_last(tmp_path):
    plan, gh, path = pad_plan(), CappedGitHub(), tmp_path / "m.json"
    scenario = flat_scenario(gh, plan, path)
    gh.fail_on_create = gh.creates + 40                 # dies among the cap's leftovers
    with pytest.raises(RuntimeError):
        tts.publish(plan, gh, map_path=path)
    assert "layout" not in json.loads(path.read_text(encoding="utf-8"))
    gh.fail_on_create = None
    mark = len(gh.calls)
    tts.publish(plan, gh, map_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    links = [kw for op, kw in gh.calls[mark:] if op == "add_sub_issue"]
    assert [kw["parent"] for kw in links[-16:]] == [scenario] * 16
    assert saved["layout"] == "areas" and len(saved["cases"]) == 281
    assert len(gh.kids[scenario]) == 16 and sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281
