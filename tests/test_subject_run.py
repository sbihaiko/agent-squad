"""The subject run harness on a tiny fake subject and a scripted adapter. No live call.

Guards: the subject repo is never written (a run lands in a clone); the held-out test stays outside the
project until verify; a wrong patch fails ac-verify and the verdict is failed.
"""
import json

import pytest

from squad import record, subject
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import load
from tests.gitrepo import make_repo, sh

pytestmark = pytest.mark.anyio

HELD = "from billing.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def fake_subject(tmp_path):
    repo = make_repo(tmp_path / "subject")
    (repo / "billing").mkdir()
    (repo / "billing" / "__init__.py").write_text("")
    (repo / "tests" / "api").mkdir(parents=True)
    (repo / "tests" / "api" / "__init__.py").write_text("")
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "issues").mkdir()
    (repo / "issues" / "01-add.md").write_text("# Add\n\nStatus: open\n")
    (repo / "CLAUDE.md").write_text("rules\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "subject")
    bench = tmp_path / "bench"
    (bench / "acceptance").mkdir(parents=True)
    (bench / "acceptance" / "test_issue_01.py").write_text(HELD)
    (bench / "issues.json").write_text(json.dumps([{"issue": 1, "title": "Add", "blocked_by": [], "acceptance_test": "acceptance/test_issue_01.py"}]))
    return repo, bench


def write_calc(body):
    def effect(req):
        (__import__("pathlib").Path(req.workspace) / "billing" / "calc.py").write_text(body)
    return effect


def script(prep, effect):
    return {"spec_draft": {"output": {"spec": {"ac_commands": prep.ac_commands}}, "spend": 0.1, "turns": 2},
            "actor": {"output": {"diff": {"files": [{"path": "billing/calc.py"}]}}, "spend": 0.2, "turns": 5, "effect": effect}}


@pytest.mark.heavy  # clones a repo; CI load pushed it to 2.3 s
def test_prepare_clones_the_subject_and_builds_a_valid_graph(tmp_path):
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    assert (prep.target / "issues" / "01-add.md").exists() and prep.target != repo
    assert not (prep.target / "tests" / "api" / "test_issue_01.py").exists()   # held out
    g = load(prep.graph_path)
    models = {n["model"] for n in g.doc["nodes"] if n["kind"] == "agent"}
    cfg = subject.load_config()            # the models come from config/subject.yaml, never from the code
    assert models == {cfg["issue"]["model"]}
    assert {n.get("effort") for n in g.doc["nodes"] if n["kind"] == "agent"} == {cfg["issue"]["effort"]}
    assert "issues/01-add.md" in next(n for n in g.doc["nodes"] if n["name"] == "actor")["instructions"]


def test_prepare_takes_relative_paths_and_every_path_it_returns_is_absolute(tmp_path, monkeypatch):
    repo, bench = fake_subject(tmp_path)
    monkeypatch.chdir(tmp_path)
    prep = subject.prepare(1, repo, bench.relative_to(tmp_path), "out", runs_dir="runs")
    assert prep.run_dir.is_absolute() and prep.target.is_absolute() and prep.graph_path.is_absolute()


@pytest.mark.heavy
async def test_a_correct_patch_passes_the_held_out_test_and_the_verdict_is_completed(tmp_path):
    repo, bench = fake_subject(tmp_path)
    before = sh(repo, "git", "rev-parse", "HEAD")
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    eng = Engine(load(prep.graph_path), ScriptedAdapter(script(prep, write_calc("def add(a, b):\n    return a + b\n"))),
                 prep.run_dir, target=prep.target, graph_path=prep.graph_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0)
    assert sh(repo, "git", "rev-parse", "HEAD") == before and not sh(repo, "git", "status", "--porcelain")   # subject untouched
    assert (prep.target / "billing" / "calc.py").exists()   # merged into the clone


@pytest.mark.heavy
async def test_a_wrong_patch_fails_ac_verify_and_the_verdict_is_failed(tmp_path):
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    eng = Engine(load(prep.graph_path), ScriptedAdapter(script(prep, write_calc("def add(a, b):\n    return a - b\n"))),
                 prep.run_dir, target=prep.target, graph_path=prep.graph_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("failed", 1)
    outs = {e["node"]: e for e in record.read(prep.run_dir / "record.jsonl") if e["event"] == "outcome"}
    assert outs["ac_verify"]["outcome"] == "fail"


def test_an_issue_with_an_open_blocker_is_refused(tmp_path):
    repo, bench = fake_subject(tmp_path)
    (bench / "issues.json").write_text(json.dumps([{"issue": 1, "title": "Add", "blocked_by": [2], "acceptance_test": "acceptance/test_issue_01.py"}]))
    with pytest.raises(subject.Refused):
        subject.prepare(1, repo, bench, tmp_path / "out")


def test_the_held_out_file_name_cannot_collide_with_a_test_the_actor_writes(tmp_path):
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    assert "tests/api/test_zz_heldout_01.py" in prep.ac_commands[0]


# ---------------------------------------------------------------- chained runs
# Guards: a blocked issue starts only when each blocker has a completed run record; the blocker's merge commit comes
# from that record, never from a model's claim; the fixture and the subject are never written.

def chain_subject(tmp_path, blocked_by=(1,)):
    repo, bench = fake_subject(tmp_path)
    (repo / "issues" / "02-sub.md").write_text("# Sub\n\nStatus: open\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "issue 2")
    (bench / "acceptance" / "test_issue_02.py").write_text("from billing.calc import add, sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n    assert add(1, 2) == 3\n")
    rows = [{"issue": 1, "title": "Add", "blocked_by": [], "acceptance_test": "acceptance/test_issue_01.py"},
            {"issue": 2, "title": "Sub", "blocked_by": list(blocked_by), "acceptance_test": "acceptance/test_issue_02.py"}]
    (bench / "issues.json").write_text(json.dumps(rows))
    return repo, bench


def tree_digest(root):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


async def run_issue(prep, body):
    eng = Engine(load(prep.graph_path), ScriptedAdapter(script(prep, write_calc(body))), prep.run_dir, target=prep.target, graph_path=prep.graph_path)
    return await eng.run()


ADD = "def add(a, b):\n    return a + b\n"
ADD_SUB = ADD + "\n\ndef sub(a, b):\n    return a - b\n"


async def complete_issue_1(repo, bench, runs):
    prep = subject.prepare(1, repo, bench, runs / "subject-01-20260930-000001", runs_dir=runs)
    assert (await run_issue(prep, ADD)).verdict == "completed"
    return prep


@pytest.mark.heavy
async def test_a_second_clone_holds_the_first_issues_change_and_the_fixture_is_unchanged(tmp_path):
    repo, bench = chain_subject(tmp_path)
    runs = tmp_path / "runs"
    first = await complete_issue_1(repo, bench, runs)
    fixture, head = tree_digest(bench), sh(repo, "git", "rev-parse", "HEAD")
    second = subject.prepare(2, repo, bench, runs / "subject-02-20260930-000002", runs_dir=runs)
    assert (second.target / "billing" / "calc.py").read_text() == (first.target / "billing" / "calc.py").read_text()
    assert [c["issue"] for c in second.chain] == [1]
    assert tree_digest(bench) == fixture and sh(repo, "git", "rev-parse", "HEAD") == head and not sh(repo, "git", "status", "--porcelain")
    assert (await run_issue(second, ADD_SUB)).verdict == "completed"   # the held-out test of issue 2 needs the change of issue 1


def test_a_blocker_with_no_run_record_refuses_and_names_the_blocker(tmp_path):
    repo, bench = chain_subject(tmp_path)
    with pytest.raises(subject.Refused, match=r"\[1\]"):
        subject.prepare(2, repo, bench, tmp_path / "out", runs_dir=tmp_path / "runs")
    assert not (tmp_path / "out").exists()


def test_a_blocker_the_tree_holds_needs_no_completed_run(tmp_path):
    repo, bench = chain_subject(tmp_path)
    prep = subject.prepare(2, repo, bench, tmp_path / "out", runs_dir=tmp_path / "runs", holds={1})   # a chained run
    assert prep.chain == [] and json.loads((prep.run_dir / "holds.json").read_text()) == {"held": [1]}


@pytest.mark.heavy
async def test_a_blocker_whose_run_failed_refuses(tmp_path):
    repo, bench = chain_subject(tmp_path)
    runs = tmp_path / "runs"
    prep = subject.prepare(1, repo, bench, runs / "subject-01-20260930-000001", runs_dir=runs)
    assert (await run_issue(prep, "def add(a, b):\n    return a - b\n")).verdict == "failed"
    with pytest.raises(subject.Refused, match=r"\[1\]"):
        subject.prepare(2, repo, bench, tmp_path / "out", runs_dir=runs)


@pytest.mark.heavy
async def test_a_chain_applies_every_blocker_in_dependency_order(tmp_path):
    repo, bench = chain_subject(tmp_path)
    runs = tmp_path / "runs"
    await complete_issue_1(repo, bench, runs)
    two = subject.prepare(2, repo, bench, runs / "subject-02-20260930-000002", runs_dir=runs)
    assert (await run_issue(two, ADD_SUB)).verdict == "completed"
    rows = json.loads((bench / "issues.json").read_text())
    rows.append({"issue": 3, "title": "Third", "blocked_by": [2, 1], "acceptance_test": "acceptance/test_issue_02.py"})
    (bench / "issues.json").write_text(json.dumps(rows))
    (repo / "issues" / "03-third.md").write_text("# Third\n\nStatus: open\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "issue 3")
    three = subject.prepare(3, repo, bench, runs / "subject-03-20260930-000003", runs_dir=runs)
    assert [c["issue"] for c in three.chain] == [1, 2]
    assert "def sub" in (three.target / "billing" / "calc.py").read_text()


def test_prepare_set_makes_one_clone_for_several_issues_blockers_first(tmp_path):
    repo, bench = fake_subject(tmp_path)
    (repo / "issues" / "02-sub.md").write_text("# Sub\n\nStatus: open\n")
    (repo / "issues" / "03-mul.md").write_text("# Mul\n\nStatus: open\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "more issues")
    for n in (2, 3):
        (bench / "acceptance" / f"test_issue_0{n}.py").write_text(HELD)
    rows = json.loads((bench / "issues.json").read_text())
    rows += [{"issue": 2, "title": "Sub", "blocked_by": [3], "acceptance_test": "acceptance/test_issue_02.py"},
             {"issue": 3, "title": "Mul", "blocked_by": [], "acceptance_test": "acceptance/test_issue_03.py"}]
    (bench / "issues.json").write_text(json.dumps(rows))
    p = subject.prepare_set([2, 3], repo, bench, tmp_path / "set")
    assert p.issues == [3, 2] and p.blocked_by == {3: [], 2: [3]} and p.target != repo
    assert p.issue_files[2] == "issues/02-sub.md" and "test_zz_heldout_02.py" in p.ac_commands[2] and "rm -f tests/api/test_zz_heldout_02.py" in p.ac_commands[2]
    assert not (p.target / "tests" / "api" / "test_zz_heldout_02.py").exists()      # held out until a command copies it in
    with pytest.raises(subject.Refused, match=r"blocked by open issues outside the set \[3\]"):
        subject.prepare_set([2], repo, bench, tmp_path / "other")


def test_an_issue_the_router_gave_up_on_is_undelivered_even_when_the_run_completed():
    from squad import subject

    def acc(child, task):
        return {"event": "spawn_accepted", "child": child, "definition": {"task": task}}

    def land(child, outcome="pass", held_out="pass"):
        return {"event": "land", "node": child, "outcome": outcome, "effect": {"held_out": held_out}}
    ev = [acc("c1", "1"), acc("c2", "2"), acc("c3", "3"), acc("c4", "3"), land("c1"), land("c2", held_out="fail"), land("c3", "fail", None), land("c4")]
    assert subject.undelivered(ev, [1, 2, 3, 4]) == [2, 4]   # 2 landed red, 3 was repaired, 4 was never asked for


def test_delivery_is_judged_by_the_held_out_test_on_the_final_tree_not_by_the_task_field():    # #153
    from squad import subject
    acc = lambda c, task, prompt: {"event": "spawn_accepted", "child": c, "definition": {"task": task, "prompt": prompt}}  # noqa: E731
    land = lambda c: {"event": "land", "node": c, "outcome": "pass", "effect": {"held_out": "pass"}}  # noqa: E731
    # the router left `task` empty on both children: only the prompt names the issue
    ev = [acc("c1", None, "Do issues/08-x.md"), acc("c2", "", "Do issues/09-y.md"), land("c1"), land("c2")]
    assert subject.undelivered(ev, [8, 9]) == []
    assert subject.undelivered(ev, [8, 9, 10], passing={8, 9}) == [10]
    assert subject.undelivered([], [8, 9], passing={8, 9}) == []                  # the tree decides: no land needed to be delivered
    assert subject.delivery_mismatches([], [8, 9], {8, 9}) == [8, 9]               # a pass with no land is recorded as a mismatch
    assert subject.delivery_mismatches(ev, [8, 9], {8, 9}) == []
    folded = [acc("c3", "13, 21", "x"), land("c3")]
    assert subject.undelivered(folded, [13, 21]) == []


@pytest.mark.heavy
async def test_a_run_made_through_prepare_resumes_by_folder_name_with_no_run_dir(tmp_path):
    from squad import cli, launch
    from squad.engine_errors import ResumeRefused
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "runs" / "subject-01-x")
    sc = script(prep, write_calc("def add(a, b):\n    return a + b\n"))
    eng = launch.launch(load(prep.graph_path), prep.run_dir, adapter=ScriptedAdapter(sc), graph_path=prep.graph_path, target=prep.target)
    assert (await eng.run()).exit_code == 0 and (prep.run_dir / "record.jsonl").exists()
    sfile = tmp_path / "s.json"
    plain = {k: {f: v for f, v in row.items() if f != "effect"} for k, row in sc.items()}      # an effect is a function: not JSON
    sfile.write_text(json.dumps({"script": plain, "repeat_last": True}))
    argv = ["resume", "subject-01-x", "--runs-dir", str(tmp_path / "runs"), "--script", str(sfile)]
    with pytest.raises(ResumeRefused, match="graph ran to its end"):      # the record is found by folder name; a finished run refuses, a missing record would return a failure instead
        await cli._resume(cli.parser().parse_args(argv))
