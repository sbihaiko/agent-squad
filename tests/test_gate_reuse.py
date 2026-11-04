"""One suite per tree, not one per stage: a later gate stage of a run reuses an earlier green verdict.

A later gate stage of the same run, on the same tree, about to run the same suite, reuses the green verdict the run
already recorded instead of running the suite again. The suite is what the stage runs, never the id of its check: an
`ac-verify` stage and a `review-command` stage that meet the same command line over the same tree share one suite.

The constraints that keep it honest:

- never across runs: the memo reads this run's record only;
- never a red verdict;
- never a different tree (its commit and its uncommitted state);
- never a different suite command;
- never a dirty tree the key cannot read, such as an untracked directory git does not expand;
- and the reuse is its own record event, `gate_reuse`, so a reader sees it instead of a silent skip.

Any doubt runs the suite. The landing gate and the nightly keep their own full run: this memo covers gate stages only.
"""
import pytest

from squad import gates
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from squad.runners.types import Scope
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh
from tests.helpers import node, router


def gate(name, key="spec"):
    return {"name": name, "kind": "gate", "check": "ac-verify", "params": {"commands_from": key}}


def _engine(run_dir, target, gates_):
    d = doc([agent("actor", output_key="spec"), *gates_], ["actor", *(g["name"] for g in gates_)])
    d["gates"].append({"id": "ac-verify", "params": ["commands_from"]})
    return Engine(from_doc(d), ScriptedAdapter({}), run_dir, target=target)


def _router_engine(run_dir, target, cmd):
    """The shipped router graph, with its review gate made to run the shell suite `cmd` as it stands."""
    d = router()
    d["run"]["deadline"] = 7200
    node(d, "review_gate")["params"] = {"argv": ["/bin/sh", "-c", cmd], "deadline": 30}
    return Engine(from_doc(d), ScriptedAdapter({}), run_dir, target=target)


async def _stage(eng, name, cmds, key="spec"):
    """One gate stage: publish the artifact its check reads, then run the gate node as the engine would."""
    eng.artifacts.put(key, None, 1, {"ac_commands": list(cmds)})
    return await eng.runners["gate"].run(eng.ctx, eng.nodes[name], 1, Scope(None, float("inf")))


def _runs(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_fix_run_keeps_no_suite_key_because_its_commands_prove_in_the_childrens_trees(tmp_path):
    """#1374: the one tree key is the target, and a fix run proves in its children's trees, which that key cannot read."""
    art = {"ac_runs": [["pytest -q", str(tmp_path / "child")]], "ac_commands": ["pytest -q"]}
    params = {"commands_from": "dispatch"}
    inp = gates.GateInput({"dispatch": art}, {}, cwd=str(tmp_path), fix_run=True)
    assert gates.suite_key("ac-verify", params, inp) is None
    inp.fix_run = False
    assert gates.suite_key("ac-verify", params, inp) is not None


def _events(run_dir, kind):
    return [e for e in read(run_dir / "record.jsonl") if e["event"] == kind]


@pytest.mark.anyio
async def test_a_green_verdict_for_the_same_tree_and_suite_is_reused_and_recorded(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}"
    eng = _engine(tmp_path / "run", target, [gate("a"), gate("b")])

    first = await _stage(eng, "a", [cmd])
    second = await _stage(eng, "b", [cmd])

    assert first.outcome == second.outcome == "pass"
    assert _runs(log) == 1, "the second stage ran the suite again"
    (reuse,) = _events(tmp_path / "run", "gate_reuse")
    assert (reuse["node"], reuse["source"]) == ("b", "a.1")
    assert reuse["sha"] == sh(target, "git", "rev-parse", "HEAD")
    out = {e["node"]: e for e in _events(tmp_path / "run", "outcome")}
    assert out["b"]["outcome"] == "pass" and out["b"]["reused_from"] == "a.1"
    assert not [e for e in _events(tmp_path / "run", "gate_result") if e["node"] == "b"], "a reused stage ran nothing"


@pytest.mark.anyio
async def test_a_review_stage_and_a_final_stage_of_one_run_run_one_suite(tmp_path):
    """The motivating case: a dispatch's review command and the final gate run the same suite on the same tree."""
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}"
    eng = _router_engine(tmp_path / "run", target, cmd)
    base = sh(target, "git", "rev-parse", "HEAD")

    review = await eng.dispatch.review(eng.nodes["route"], "route.router.1", {"prompt": "GitHub issue #7: build it"}, str(target), base, 1, False, 30)
    eng.artifacts.put("route", None, 1, {"ac_commands": [cmd]})
    final = await eng.runners["gate"].run(eng.ctx, eng.nodes["final_gate"], 1, Scope(None, float("inf")))

    assert review["verdict"] == "approve" and final.outcome == "pass"
    assert _runs(log) == 1, "the final stage ran the suite the review stage already proved"
    (reuse,) = _events(tmp_path / "run", "gate_reuse")
    assert (reuse["node"], reuse["source"]) == ("final_gate", "review_gate:route.router.1.1")
    assert {e["node"]: e for e in _events(tmp_path / "run", "outcome")}["final_gate"]["reused_from"] == "review_gate:route.router.1.1"


@pytest.mark.anyio
async def test_a_red_verdict_is_never_reused(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}; false"
    eng = _engine(tmp_path / "run", target, [gate("a"), gate("b")])

    first = await _stage(eng, "a", [cmd])
    second = await _stage(eng, "b", [cmd])

    assert first.outcome == second.outcome == "fail"
    assert _runs(log) == 2, "a red verdict answered for a later stage"
    assert not _events(tmp_path / "run", "gate_reuse")
    kept = {e["node"]: e["outcome"] for e in _events(tmp_path / "run", "gate_result")}
    assert kept == {"a": "fail", "b": "fail"}


@pytest.mark.anyio
async def test_a_different_suite_is_never_reused(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    one = f"printf 'run\\n' >> {log} # one"
    two = f"printf 'run\\n' >> {log} # two"
    eng = _engine(tmp_path / "run", target, [gate("a", "spec1"), gate("b", "spec2")])

    await _stage(eng, "a", [one], "spec1")
    await _stage(eng, "b", [two], "spec2")

    assert _runs(log) == 2, "another suite command reused one verdict"
    assert not _events(tmp_path / "run", "gate_reuse")


@pytest.mark.anyio
async def test_a_different_tree_sha_is_never_reused(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}"
    eng = _engine(tmp_path / "run", target, [gate("a"), gate("b")])

    await _stage(eng, "a", [cmd])
    sh(target, "git", "commit", "--allow-empty", "-m", "moved")
    await _stage(eng, "b", [cmd])

    assert _runs(log) == 2, "a verdict from another tree sha answered"
    assert not _events(tmp_path / "run", "gate_reuse")


@pytest.mark.anyio
async def test_a_dirty_tree_the_key_cannot_read_skips_the_reuse(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}"
    eng = _engine(tmp_path / "run", target, [gate("a"), gate("b")])

    await _stage(eng, "a", [cmd])
    nested = target / "nested"
    nested.mkdir()
    sh(nested, "git", "init", "-q", "-b", "main")     # git lists this as one untracked directory, which the key cannot read
    sh(nested, "git", "config", "user.email", "t@t")
    sh(nested, "git", "config", "user.name", "t")
    (nested / "x.txt").write_text("x\n")
    sh(nested, "git", "add", "-A")
    sh(nested, "git", "commit", "-q", "-m", "nested")
    assert gates.tree_key(str(target)) is None

    await _stage(eng, "b", [cmd])

    assert _runs(log) == 2, "a dirty tree the key could not read reused a verdict"
    assert not _events(tmp_path / "run", "gate_reuse")


def test_a_quoted_untracked_name_counts_by_its_contents(tmp_path):
    """`porcelain` quotes a name with a space; read quoted, the path never resolves and two contents give one key."""
    repo = make_repo(tmp_path / "target")
    f = repo / "my file.txt"
    f.write_text("A\n")
    first = gates.tree_key(str(repo))
    f.write_text("B\n")
    second = gates.tree_key(str(repo))

    assert first is not None and second is not None
    assert first[0] == second[0]
    assert first[1] != second[1], "two contents of one quoted untracked name gave one key"


@pytest.mark.anyio
async def test_another_runs_verdict_is_never_reused(tmp_path):
    target = make_repo(tmp_path / "target")
    log = tmp_path / "runs.log"
    cmd = f"printf 'run\\n' >> {log}"

    one = _engine(tmp_path / "run-one", target, [gate("a")])
    await _stage(one, "a", [cmd])
    two = _engine(tmp_path / "run-two", target, [gate("a")])
    await _stage(two, "a", [cmd])

    assert _runs(log) == 2, "a verdict from another run answered"
    assert not _events(tmp_path / "run-two", "gate_reuse")
    assert [e["node"] for e in _events(tmp_path / "run-two", "gate_result")] == ["a"]
