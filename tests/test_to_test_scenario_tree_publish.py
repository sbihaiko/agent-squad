"""ADR-0275 slice C (MesenAI #1474): `publish` creates and updates the whole area tree, idempotently.

Driven through `publish`, `read_issues`, `read_map` and the `publish` command; the runner is a stand-in that models
GitHub's sub-issue tree, so a duplicate issue or a duplicate link is an observable failure. No network.
"""
import json

import pytest

from squad import to_test_scenario as tts
from tests.test_gui_test_scenario import script
from tests.test_to_test_scenario_areas import batch
from tests.test_to_test_scenario_publish import scenario_file, tiny_plan

DASHBOARD = "<!-- gui-test:dashboard:start -->\n| latest | ✅ |\n<!-- gui-test:dashboard:end -->"


class TreeGitHub:
    """GitHub's issues and sub-issue links in memory: a second link of the same issue is a 422, as on GitHub."""

    def __init__(self, start=100, fail_on_create=None, fail_on_link=None):
        self.calls, self.issues, self.kids = [], {}, {}
        self.next, self.creates, self.links = start, 0, 0
        self.fail_on_create, self.fail_on_link = fail_on_create, fail_on_link

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
        if op == "create_issue":
            self.creates += 1
            if self.fail_on_create and self.creates == self.fail_on_create:
                raise RuntimeError("the connection dropped")
            self.next += 1
            self.issues[self.next] = {"id": 900 + self.next, "title": kw["title"], "body": kw["body"], "state": "open"}
            return {"number": self.next, "id": 900 + self.next}
        if op == "add_sub_issue":
            self.links += 1
            if self.fail_on_link and self.links == self.fail_on_link:
                raise RuntimeError("the connection dropped")
            sub = next(n for n, i in self.issues.items() if i["id"] == kw["sub"])
            if kw.get("replace_parent"):
                for held in self.kids.values():
                    if sub in held:
                        held.remove(sub)
            elif any(sub in held for held in self.kids.values()):
                raise RuntimeError("422: the issue already has a parent")
            self.kids.setdefault(kw["parent"], []).append(sub)
            return {}
        if op == "list_sub_issues":
            return [{"number": n, "id": self.issues[n]["id"], "title": self.issues[n]["title"]}
                    for n in self.kids.get(kw["number"], [])]
        if op == "issue_body":
            return self.issues[kw["number"]]["body"]
        if op == "issue_state":
            held = self.issues[kw["number"]]
            return {"state": held["state"], "verdict": None, "title": held["title"]}
        if op == "update_issue":
            self.issues[kw["number"]].update(title=kw["title"], body=kw["body"])
            return {}
        return {}

    def of(self, op):
        return [kw for o, kw in self.calls if o == op]

    def titled(self, text):
        return [n for n, i in self.issues.items() if tts.title_base(i["title"]).endswith(text)]


def pad_plan(**over):
    """16 areas, 281 cases: fifteen areas of 17 cases and one of 26."""
    batches = [batch(f"k{n:02d}-x", 26 if n == 16 else 17) for n in range(1, 17)]
    return tts.build_plan(script(batches=batches, **over))


def case_numbers(gh, plan):
    by_title = {tts.title_base(h["title"]): n for n, h in gh.issues.items()}
    return {i["cases"][0]: by_title[i["title"]] for i in plan["issues"]}


def test_a_281_case_plan_publishes_16_areas_and_their_containers_top_down(tmp_path):
    plan, gh = pad_plan(), TreeGitHub()
    out = tts.publish(plan, gh, map_path=tmp_path / "m.json")
    assert len(plan["issues"]) == 281 and len(plan["areas"]) == 16
    assert gh.creates == 1 + 16 + 16 + 281
    scenario = out["parent"]
    assert [tts.title_base(gh.issues[n]["title"]) for n in gh.kids[scenario]] == [a["title"] for a in plan["areas"]]
    cases = case_numbers(gh, plan)
    for area in plan["areas"]:
        number = out["map"]["areas"][area["key"]]["number"]
        assert [gh.issues[n]["title"] for n in gh.kids[number]] == ["📋 Cases"]
        container = gh.kids[number][0]
        assert gh.kids[container] == [cases[i["cases"][0]] for c in area["containers"] for i in c["cases"]]
    assert not set(cases.values()) & set(gh.kids[scenario])     # a record is never linked to the scenario
    assert sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281
    assert len(gh.of("board")) == 1 and gh.of("board") == [{"number": scenario}]


def test_the_map_gains_layout_areas_and_each_cases_area_and_container(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    out = tts.publish(plan, gh, map_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["layout"] == "areas" and set(saved["areas"]) == {a["key"] for a in plan["areas"]}
    first = saved["areas"]["k01"]
    assert first["number"] and len(first["containers"]) == 1 and first["runs"] == {}
    case = saved["cases"]["K01X-001"]
    assert (case["area"], case["container"]) == ("k01", first["containers"][0]["number"])
    assert tts.read_issues(path)["areas"] == saved["areas"] and out["map"] == saved


def test_read_issues_and_read_map_keep_every_key_of_the_map(tmp_path):
    path = tmp_path / "m.json"
    doc = {"parent": 7, "boarded": True, "layout": "areas", "note": {"kept": 1},
           "areas": {"k": {"number": 8, "containers": [], "runs": {"2026-10": [{"number": 9}]}}},
           "cases": {"A-1": {"number": 10, "hash": "h", "area": "k", "container": 11, "extra": "x"}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    read = tts.read_issues(path)
    assert read["cases"]["A-1"] == doc["cases"]["A-1"] and read["areas"] == doc["areas"]
    assert read["layout"] == "areas" and read["note"] == {"kept": 1}
    mapped = tts.read_map(path)
    assert mapped["issues"] == {"A-1": 10} and mapped["parent"] == 7
    assert mapped["areas"] == doc["areas"] and mapped["cases"] == doc["cases"] and mapped["layout"] == "areas"


@pytest.mark.parametrize("flaky", [{"fail_on_create": 60}, {"fail_on_link": 60}])
def test_an_interrupted_publish_resumes_with_no_duplicate_issue_or_link(tmp_path, flaky):
    plan, path = pad_plan(), tmp_path / "m.json"
    gh = TreeGitHub(**flaky)
    with pytest.raises(RuntimeError):
        tts.publish(plan, gh, map_path=path)
    gh.fail_on_create = gh.fail_on_link = None
    tts.publish(plan, gh, map_path=path)                         # the same GitHub: its issues and links survive
    assert len(gh.issues) == 1 + 16 + 16 + 281                   # the failed create made nothing
    titles = [i["title"] for i in gh.issues.values()]
    assert len(titles) == len(set(titles)) + 15                  # the 16 containers share one title, nothing else
    assert sum(len(v) for v in gh.kids.values()) == 16 + 16 + 281


def test_a_republish_keeps_emoji_dashboards_and_record_state(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    out = tts.publish(plan, gh, map_path=path)
    area = out["map"]["areas"]["k02"]["number"]
    case = out["map"]["cases"]["K02X-001"]["number"]
    gh.issues[area]["title"] = "❌ " + tts.title_base(gh.issues[area]["title"])
    gh.issues[area]["body"] += "\n" + DASHBOARD
    gh.issues[out["parent"]]["body"] += "\n" + DASHBOARD
    gh.issues[case].update(title="✅ " + tts.title_base(gh.issues[case]["title"]), state="closed")
    before = len(gh.calls)
    again = tts.publish(plan, gh, map_path=path)
    assert [op for op, _ in gh.calls[before:] if op in ("create_issue", "add_sub_issue")] == []
    assert gh.issues[area]["title"].startswith("❌ Area: k02") and DASHBOARD in gh.issues[area]["body"]
    assert DASHBOARD in gh.issues[out["parent"]]["body"] and gh.issues[out["parent"]]["body"].count("dashboard:start") == 1
    assert gh.issues[case]["title"].startswith("✅ ") and gh.issues[case]["state"] == "closed"
    assert again["created"] == [] and again["map"] == out["map"]


def test_a_moved_case_is_reparented_and_its_map_entry_rewritten(tmp_path):
    def plan_with(area):
        return tts.build_plan(script(batches=[batch("k01-x", 2), batch("k02-y", 2, area=area)]))

    gh, path = TreeGitHub(), tmp_path / "m.json"
    first = tts.publish(plan_with("k02"), gh, map_path=path)
    moved = first["map"]["cases"]["K02Y-001"]
    out = tts.publish(plan_with("k01"), gh, map_path=path)
    target = out["map"]["areas"]["k01"]["containers"][0]["number"]
    assert out["map"]["cases"]["K02Y-001"]["area"] == "k01" and out["map"]["cases"]["K02Y-001"]["container"] == target
    assert out["map"]["cases"]["K02Y-001"]["number"] == moved["number"]
    assert {"parent": target, "sub": gh.issues[moved["number"]]["id"], "replace_parent": True} in gh.of("add_sub_issue")
    assert moved["number"] in gh.kids[target] and moved["number"] not in gh.kids[moved["container"]]
    assert out["removed_areas"] == ["k02"]


def test_an_area_dropped_from_the_plan_is_reported_and_left_open(tmp_path):
    gh, path = TreeGitHub(), tmp_path / "m.json"
    tts.publish(tts.build_plan(script(batches=[batch("k01-x", 1), batch("k02-y", 1)])), gh, map_path=path)
    out = tts.publish(tts.build_plan(script(batches=[batch("k01-x", 1)])), gh, map_path=path)
    assert out["removed_areas"] == ["k02"]
    assert not [kw for kw in gh.calls if kw[0] in ("close", "remove_sub_issue")]
    assert "k02" in out["map"]["areas"] and out["removed"] == ["K02Y-001"]


def test_a_lost_map_adopts_the_existing_containers_and_cases_instead_of_creating_them(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    first = tts.publish(plan, gh, map_path=path)
    lost = {"parent": first["parent"], "boarded": True,
            "areas": {k: {"number": v["number"]} for k, v in first["map"]["areas"].items()}}
    path.write_text(json.dumps(lost), encoding="utf-8")
    again = tts.publish(plan, gh, map_path=path)
    assert gh.creates == 1 + 16 + 16 + 281
    assert again["map"]["areas"]["k03"]["containers"] == first["map"]["areas"]["k03"]["containers"]
    assert again["map"]["cases"] == first["map"]["cases"]


def test_a_dry_run_counts_what_it_would_do_and_writes_nothing(tmp_path):
    plan, gh, path = pad_plan(), TreeGitHub(), tmp_path / "m.json"
    out = tts.publish(plan, gh, map_path=path, dry_run=True)
    assert gh.calls == [] and not path.exists()
    assert out["counts"] == {"create": 1 + 16 + 16 + 281, "link": 16 + 16 + 281, "move": 0, "update": 0}


def test_the_dry_run_flag_of_the_publish_command_prints_the_counts_and_writes_no_map(tmp_path, capsys):
    scenario_file(tmp_path)
    _, d, _ = tiny_plan(tmp_path)
    script_path = tmp_path / "tiny.gui-test.json"
    script_path.write_text(json.dumps(d), encoding="utf-8")
    gh = TreeGitHub()
    assert tts.main(["publish", str(tmp_path / "tiny.md"), str(script_path), "--dry-run"], github=gh) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] is True and printed["counts"]["create"] == 1 + 1 + 1 + 3
    assert gh.calls == [] and not tts.issues_path(script_path).exists()
