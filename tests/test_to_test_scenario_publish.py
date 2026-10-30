"""Publishing a scenario plan (#1225): idempotent, resumable and paced, through an injected GitHub runner.

Driven through the module's public contract only: a plan in, GitHub calls and a map file out. The runner is a fake that
records every call, so a case asserts exactly which calls a publish makes — and which it must not.
"""
import copy
import json
import re
import subprocess

import pytest

from squad.to_test_scenario import (  # noqa: F401
    CASE_LABEL,
    PARENT_LABEL,
    RateLimited,
    ScenarioError,
    TRIAGE_LABEL,
    build_plan,
    case_hash,
    issues_path,
    publish,
    read_issues,
    read_scenario,
    rest_runner,
    script_steps,
)
from tests.gui_test_helpers import script, step

SCENARIO = """\
# tiny

| ID | Precondition | Steps | Expected |
|---|---|---|---|
| launch.s0 | app not running | launch it with the pad | Home is on screen |
| launch.home | launch.s0 | press A | the library grid opens |
| launch.return | launch.home | press B | Home is back |
"""


def without_last_case(plan):
    """The plan with its last case gone from the flat view and from the tree."""
    smaller = copy.deepcopy(plan)
    last = smaller["issues"].pop()["id"]
    for area in smaller["areas"]:
        for container in area["containers"]:
            container["cases"] = [i for i in container["cases"] if i["id"] != last]
    return smaller


def scenario_file(tmp_path, text=SCENARIO):
    p = tmp_path / "tiny.md"
    p.write_text(text, encoding="utf-8")
    return p


def _rows(text=SCENARIO):
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] and cells[0][0].islower():
            out[cells[0]] = cells
    return out


def hashed(sid, text=SCENARIO):
    cells = _rows(text)[sid]
    return case_hash(cells[1], cells[2], cells[3])


def tiny_plan(tmp_path, *, granularity="case", text=SCENARIO):
    """A plan for `SCENARIO`: the conversion's output, every step recording its case's content hash."""
    scenario = read_scenario(scenario_file(tmp_path, text))
    rows = _rows(text)
    setup = [step("launch.s0", role="setup", mode="manual", content_hash=hashed("launch.s0", text))]
    steps = [step(sid, content_hash=hashed(sid, text)) for sid in rows if sid != "launch.s0"]
    d = script(*steps)
    d["batches"] = [{"id": "launch", "setup": setup, "teardown": [], "steps": steps}]
    return scenario, d, build_plan(scenario, d, granularity=granularity)


class FakeGitHub:
    """A recording stand-in for the real REST runner: no network, no `gh`, and a `next` counter for numbers."""

    def __init__(self, fail_on_create=None, rate_limited_once=None, start=100, world=None):
        self.world = world if world is not None else {"titles": {}, "kids": {}}   # what GitHub itself remembers
        self.calls = []
        self.made = []
        self.next = start
        self.creates = 0
        self.fail_on_create = fail_on_create
        self.rate_limited_once = rate_limited_once

    def __call__(self, op, **kw):
        self.calls.append((op, kw))          # every attempt, including one that fails
        if op == "create_issue":
            self.creates += 1
            if self.fail_on_create and self.creates == self.fail_on_create:
                raise RuntimeError("the connection dropped")
            if self.rate_limited_once and self.creates == self.rate_limited_once:
                self.rate_limited_once = None
                raise RateLimited(retry_after=7)
            self.next += 1
            made = {"number": self.next, "id": 900 + self.next}
            self.made.append((kw, made))
            self.world["titles"][self.next] = kw["title"]
            return made
        if op == "add_sub_issue":
            number = kw["sub"] - 900
            self.world["kids"].setdefault(kw["parent"], []).append(
                {"number": number, "id": kw["sub"], "title": self.world["titles"].get(number, "")})
        if op == "list_sub_issues":
            return list(self.world["kids"].get(kw["number"], []))
        return {}

    def again(self, start=100):
        """A new publish against the same GitHub: fresh call log, the issues and links already made."""
        return FakeGitHub(start=start, world=self.world)

    def of(self, op):
        return [kw for o, kw in self.calls if o == op]

    def ops(self):
        return [o for o, _ in self.calls]

    def created_titles(self):
        return [kw["title"] for kw, _ in self.made]

    def updated_numbers(self):
        return [kw["number"] for kw in self.of("update_issue")]


class Sleeps:
    def __init__(self):
        self.seconds = []

    def __call__(self, seconds):
        self.seconds.append(seconds)


# --- a full publish: the calls, and only the calls, a publish makes --------------------------------

def test_a_full_publish_ensures_the_labels_creates_the_parent_and_links_every_case_natively(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    gh = FakeGitHub()
    out = publish(plan, gh, map_path=tmp_path / "tiny.gui-test.issues.json")
    labels = {kw["name"] for kw in gh.of("ensure_label")}
    assert CASE_LABEL in labels and PARENT_LABEL in labels
    titles = gh.created_titles()
    assert titles[0] == plan["parent"]["title"] and "scenario" in titles[0].lower()
    assert titles[1:3] == ["⚪ Area: launch", "📋 Cases"]
    assert titles[3:] == ["⚪ " + i["title"] for i in plan["issues"]]
    # The sub-issue relation names the issue's numeric id, never its number.
    made = {kw["title"]: made["id"] for kw, made in gh.made}
    container = out["map"]["areas"]["launch"]["containers"][0]["number"]
    linked = {(kw["parent"], kw["sub"]) for kw in gh.of("add_sub_issue")}
    assert linked >= {(container, made["⚪ " + i["title"]]) for i in plan["issues"]}
    assert len(gh.of("add_sub_issue")) == len(plan["issues"]) + 2      # and the area and its container, top-down
    # Only the parent reaches the board: no card is created for a sub-issue.
    assert gh.of("board") == [{"number": out["parent"], "status": "Backlog"}]


def test_a_full_publish_maps_every_case_to_its_issue_number_and_content_hash(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    gh = FakeGitHub()
    out = publish(plan, gh, map_path=tmp_path / "tiny.gui-test.issues.json")
    numbers = {kw["title"]: made["number"] for kw, made in gh.made}
    assert {k: {"number": c["number"], "hash": c["hash"]} for k, c in out["cases"].items()} == {
        i["id"]: {"number": numbers["⚪ " + i["title"]], "hash": i["hash"]} for i in plan["issues"]}
    assert all(case["hash"] for case in out["cases"].values())
    assert out["created"] == [i["id"] for i in plan["issues"]] and out["updated"] == [] and out["removed"] == []


def test_a_publish_with_no_map_path_still_publishes_and_keeps_the_map_in_memory(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    gh = FakeGitHub()
    out = publish(plan, gh)
    assert out["map_path"] is None and out["parent"] and len(out["cases"]) == len(plan["issues"])
    assert gh.of("board") == [{"number": out["parent"], "status": "Backlog"}]


def test_the_map_is_written_beside_the_script_under_the_documented_name(tmp_path):
    assert issues_path("docs/x/play-pad-only.gui-test.json").name == "play-pad-only.gui-test.issues.json"
    assert issues_path(tmp_path / "p.gui-test.json") == tmp_path / "p.gui-test.issues.json"
    _, _, plan = tiny_plan(tmp_path)
    script_path = tmp_path / "p.gui-test.json"
    script_path.write_text("{}", encoding="utf-8")
    out = publish(plan, FakeGitHub(), script_path=script_path)
    assert out["map_path"] == str(tmp_path / "p.gui-test.issues.json")
    assert read_issues(out["map_path"]) == out["map"] and out["map"]["parent"] == out["parent"]


# --- resuming: an interruption costs nothing already created ---------------------------------------

def test_an_interrupted_publish_resumes_from_the_map_and_creates_no_duplicate(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    map_path = tmp_path / "tiny.gui-test.issues.json"
    interrupted = FakeGitHub(fail_on_create=5)          # the parent, its area and container, then one case
    with pytest.raises(RuntimeError):
        publish(plan, interrupted, map_path=map_path)
    saved = read_issues(map_path)
    assert saved["parent"] and len(saved["cases"]) == 1
    assert [c["title"] for c in interrupted.of("create_issue")] == \
        [plan["parent"]["title"], "⚪ Area: launch", "📋 Cases", "⚪ " + plan["issues"][0]["title"],
         "⚪ " + plan["issues"][1]["title"]]

    resumed = interrupted.again(start=200)
    out = publish(plan, resumed, map_path=map_path)
    assert [kw["title"] for kw in resumed.of("create_issue")] == ["⚪ " + i["title"] for i in plan["issues"][1:]]
    assert out["parent"] == saved["parent"] and len(out["cases"]) == len(plan["issues"])
    assert len(resumed.of("board")) == 0                # the parent is already on the board
    # Nothing the first publish created was created again: the numbers are all different.
    assert len({c["number"] for c in out["cases"].values()}) == len(plan["issues"])
    assert out["cases"]["launch.s0"]["number"] == saved["cases"]["launch.s0"]["number"]


def test_a_publish_interrupted_before_the_board_add_finishes_the_board_add_on_resume(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    map_path = tmp_path / "tiny.gui-test.issues.json"

    class DropsOnBoard(FakeGitHub):
        def __call__(self, op, **kw):
            if op == "board":
                self.calls.append((op, kw))
                raise RuntimeError("the connection dropped")
            return super().__call__(op, **kw)

    with pytest.raises(RuntimeError):
        publish(plan, DropsOnBoard(), map_path=map_path)
    assert read_issues(map_path)["boarded"] is False
    resumed = FakeGitHub(start=200)
    out = publish(plan, resumed, map_path=map_path)
    assert resumed.of("board") == [{"number": out["parent"], "status": "Backlog"}]
    # The parent was already made; only its cases are still to create.
    assert plan["parent"]["title"] not in resumed.created_titles()


# --- pacing: a secondary rate limit is a delay, not a failure --------------------------------------

def test_a_secondary_rate_limit_backs_off_and_retries_instead_of_failing(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    sleeps, gh = Sleeps(), FakeGitHub(rate_limited_once=1)
    out = publish(plan, gh, map_path=tmp_path / "tiny.gui-test.issues.json", sleep=sleeps)
    assert sleeps.seconds and sleeps.seconds[0] == 7    # the API's own retry-after wins over the back-off
    titles = gh.created_titles()
    assert titles.count(plan["parent"]["title"]) == 1   # the retry created one parent, not two
    assert titles[3:] == ["⚪ " + i["title"] for i in plan["issues"]]
    assert len({c["number"] for c in out["cases"].values()}) == len(plan["issues"])


def test_the_back_off_grows_when_the_api_keeps_signalling_a_rate_limit(tmp_path):
    _, _, plan = tiny_plan(tmp_path)

    class AlwaysLimited(FakeGitHub):
        def __call__(self, op, **kw):
            self.calls.append((op, kw))
            if op == "create_issue":
                raise RateLimited()

    sleeps = Sleeps()
    with pytest.raises(RateLimited):
        publish(plan, AlwaysLimited(), map_path=tmp_path / "tiny.gui-test.issues.json", sleep=sleeps,
                attempts=3, backoff=2.0)
    assert sleeps.seconds == [2.0, 4.0]


# --- the second publish: it updates, and it reports a case it no longer covers ---------------------

def test_a_second_publish_updates_and_reports_a_removed_case_without_closing_it(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    map_path = tmp_path / "tiny.gui-test.issues.json"
    gh = FakeGitHub()
    first = publish(plan, gh, map_path=map_path)

    smaller = without_last_case(plan)
    again = gh.again()
    out = publish(smaller, again, map_path=map_path)
    assert again.of("create_issue") == [] and again.of("add_sub_issue") == [] and again.of("board") == []
    assert out["updated"] == ["parent"] + [i["id"] for i in smaller["issues"]]
    area = first["map"]["areas"]["launch"]["number"]
    assert set(again.updated_numbers()) == {first["parent"], area, *[c["number"] for c in first["cases"].values()][:2]}
    assert out["removed"] == [plan["issues"][-1]["id"]]
    assert "close" not in again.ops()                 # a removed case is reported, never silently closed
    assert set(out["cases"]) == {i["id"] for i in smaller["issues"]}


def test_a_case_whose_text_changed_is_updated_with_its_new_hash_not_recreated(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    map_path = tmp_path / "tiny.gui-test.issues.json"
    gh = FakeGitHub()
    first = publish(plan, gh, map_path=map_path)

    changed = SCENARIO.replace("| Home is back |", "| Home is back with the ring on the opener |")
    _, _, plan2 = tiny_plan(tmp_path, text=changed)
    again = gh.again()
    out = publish(plan2, again, map_path=map_path)
    assert again.of("create_issue") == []
    assert out["cases"]["launch.return"]["number"] == first["cases"]["launch.return"]["number"]
    assert out["cases"]["launch.return"]["hash"] != first["cases"]["launch.return"]["hash"]
    assert read_issues(map_path) == out["map"]


# --- what is published is en-US, with no attribution line ------------------------------------------

# What a published body must never carry: a Portuguese sentence under English prose, or a tool's signature line.
_ATTRIBUTION = re.compile(r"co-authored-by|generated with|claude\b|🤖", re.I)
_PT_BR = re.compile(r"\b(não|uma|para|com|sem|caso|casos|jogo|tela|janela|botão|arquivo|quando|então|também|deve|"
                    r"executa|verifica)\b", re.I)


def test_every_published_string_is_en_us_and_carries_no_attribution_line(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    gh = FakeGitHub()
    publish(plan, gh, map_path=tmp_path / "tiny.gui-test.issues.json")
    bodies = [kw["body"] for kw in gh.of("create_issue")] + [kw["title"] for kw in gh.of("create_issue")]
    assert bodies
    for body in bodies:
        assert not _ATTRIBUTION.search(body), body[:200]
        assert not _PT_BR.search(body), body[:200]
    assert _ATTRIBUTION.search("x\nCo-Authored-By: someone") and _PT_BR.search("o caso falhou")


# --- the real runner: REST through `gh api`, and a secondary rate limit is not a failure -----------

def test_rest_runner_creates_and_links_issues_over_rest_and_adds_only_what_it_is_asked_to():
    seen = []

    def shell(args, **kw):
        seen.append(args)
        if args[:3] == ["gh", "project", "item-add"]:
            return json.dumps({"id": "PVTI_x"})
        if args[4].endswith("/issues"):
            return json.dumps({"number": 8, "id": 800})
        return "{}"

    gh = rest_runner("sbihaiko/MesenAI", shell=shell, project="users/sbihaiko/projects/3")
    gh("ensure_label", name="Test-Case")
    made = gh("create_issue", title="Test case a.b", body="body", labels=["Test-Case", "wave:01", "enhancement"])
    assert made == {"number": 8, "id": 800}
    gh("add_sub_issue", parent=7, sub=made["id"])
    gh("board", number=7)

    assert seen[0] == ["gh", "label", "create", "Test-Case", "--force", "--repo", "sbihaiko/MesenAI"]
    post = seen[1]
    assert post[:5] == ["gh", "api", "--method", "POST", "repos/sbihaiko/MesenAI/issues"]
    assert "labels[]=Test-Case" in post and "labels[]=wave:01" in post and "labels[]=enhancement" in post
    sub = next(a for a in seen if a[4].endswith("/sub_issues"))
    assert sub[4] == "repos/sbihaiko/MesenAI/issues/7/sub_issues" and "sub_issue_id=800" in sub
    assert sub[sub.index("sub_issue_id=800") - 1] == "-F"   # an integer field: `-f` would send a string, a 422
    item = next(a for a in seen if a[:3] == ["gh", "project", "item-add"])
    assert item[3:6] == ["3", "--owner", "sbihaiko"] and item[item.index("--url") + 1].endswith("/issues/7")
    assert not [a for a in seen if a[:3] == ["gh", "project", "item-edit"]]


def test_the_parent_is_boarded_in_backlog_before_ready_for_agent_is_added(tmp_path):
    """#895: a card is Todo the moment it joins the board, and Todo plus ready-for-agent is the dispatch signal."""
    _, _, plan = tiny_plan(tmp_path)
    gh = FakeGitHub()
    out = publish(plan, gh, map_path=tmp_path / "tiny.gui-test.issues.json")
    parent = out["parent"]
    created = next(kw for kw in gh.of("create_issue") if kw["title"] == plan["parent"]["title"])
    assert TRIAGE_LABEL not in created["labels"]
    board_at = next(i for i, (op, _) in enumerate(gh.calls) if op == "board")
    assert gh.calls[board_at][1] == {"number": parent, "status": "Backlog"}
    labelled = [i for i, (op, kw) in enumerate(gh.calls)
                if op == "update_issue" and kw["number"] == parent and TRIAGE_LABEL in kw["labels"]]
    assert labelled and labelled[0] > board_at


def test_a_resumed_publish_adds_ready_for_agent_to_the_parent_only_after_its_board_status(tmp_path):
    _, _, plan = tiny_plan(tmp_path)
    map_path = tmp_path / "tiny.gui-test.issues.json"

    class DropsOnBoard(FakeGitHub):
        def __call__(self, op, **kw):
            if op == "board":
                self.calls.append((op, kw))
                raise RuntimeError("the connection dropped")
            return super().__call__(op, **kw)

    with pytest.raises(RuntimeError):
        publish(plan, DropsOnBoard(), map_path=map_path)
    resumed = FakeGitHub(start=200)
    out = publish(plan, resumed, map_path=map_path)
    board_at = next(i for i, (op, _) in enumerate(resumed.calls) if op == "board")
    labelled = [i for i, (op, kw) in enumerate(resumed.calls)
                if op == "update_issue" and kw["number"] == out["parent"] and TRIAGE_LABEL in kw["labels"]]
    assert labelled and labelled[0] > board_at


def test_rest_runner_sets_a_named_status_by_its_field_and_option_ids_never_as_text():
    seen = []

    def shell(args, **kw):
        seen.append(args)
        if args[:3] == ["gh", "project", "item-add"]:
            return json.dumps({"id": "PVTI_x"})
        if args[:3] == ["gh", "project", "view"]:
            return json.dumps({"id": "PVT_p"})
        if args[:3] == ["gh", "project", "field-list"]:
            return json.dumps({"fields": [
                {"id": "F_title", "name": "Title", "options": []},
                {"id": "F_status", "name": "Status",
                 "options": [{"id": "O_todo", "name": "Todo"}, {"id": "O_backlog", "name": "Backlog"}]}]})
        return "{}"

    gh = rest_runner("sbihaiko/MesenAI", shell=shell, project="users/sbihaiko/projects/3")
    gh("board", number=7, status="Backlog")
    edit = next(a for a in seen if a[:3] == ["gh", "project", "item-edit"])
    assert edit[edit.index("--id") + 1] == "PVTI_x" and edit[edit.index("--project-id") + 1] == "PVT_p"
    assert edit[edit.index("--field-id") + 1] == "F_status"
    assert edit[edit.index("--single-select-option-id") + 1] == "O_backlog"
    assert "--text" not in edit


def test_rest_runner_names_a_status_the_board_does_not_offer_and_writes_nothing():
    seen = []

    def shell(args, **kw):
        seen.append(args)
        if args[:3] == ["gh", "project", "item-add"]:
            return json.dumps({"id": "PVTI_x"})
        if args[:3] == ["gh", "project", "field-list"]:
            return json.dumps({"fields": [{"id": "F_status", "name": "Status", "options": [{"id": "O_todo", "name": "Todo"}]}]})
        return json.dumps({"id": "PVT_p"})

    gh = rest_runner("sbihaiko/MesenAI", shell=shell, project="users/sbihaiko/projects/3")
    with pytest.raises(ValueError, match="no Status option 'Backlog'"):
        gh("board", number=7, status="Backlog")
    assert not [a for a in seen if a[:3] == ["gh", "project", "item-edit"]]


def test_rest_runner_turns_a_secondary_rate_limit_answer_into_a_back_off():
    def shell(args, **kw):
        raise subprocess.CalledProcessError(1, args, stderr="gh: You have exceeded a secondary rate limit")

    gh = rest_runner("sbihaiko/MesenAI", shell=shell)
    with pytest.raises(RateLimited):
        gh("create_issue", title="t", body="b", labels=["Test-Case", "enhancement"])


def test_a_plan_over_the_sub_issue_cap_is_refused_before_anything_is_created(tmp_path):
    """GitHub allows 100 sub-issues per parent: the 101st create would leave an issue linked to nothing."""
    _, _, plan = tiny_plan(tmp_path)
    plan.pop("areas")                                   # a flat plan: every case is a sub-issue of the scenario
    plan["issues"] = [dict(plan["issues"][0], id=f"c{n}") for n in range(101)]
    gh = FakeGitHub()
    with pytest.raises(ScenarioError, match="100 sub-issues"):
        publish(plan, gh, map_path=tmp_path / "m.json")
    assert gh.calls == []
