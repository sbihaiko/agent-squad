"""One gate per tree, not one per stage: a later stage of a run reuses an earlier green verdict.

A measured 51-minute run ran the same fast battery three times on one tree: the child's own prepush, the run's
`review_gate`, then its `final_gate`. A gate stage that finds a green verdict for the tree it is about to gate, and for
the same suite command, recorded earlier in this same run, now reuses it instead of running the suite again.

The constraints that keep it honest:

- never across runs: the memo reads this run's record only;
- never a red verdict;
- never a different tree sha (the commit and its uncommitted state);
- never a different suite command;
- and the reuse is its own record event, `gate_reuse`, so a reader sees it instead of a silent skip.

Any doubt runs the suite. The landing gate and the nightly keep their own full run: this memo covers gate stages only.
"""
import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from squad.runners.types import Scope
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh


def gate(name, key="spec"):
    return {"name": name, "kind": "gate", "check": "ac-verify", "params": {"commands_from": key}}


def _engine(run_dir, target, gates):
    d = doc([agent("actor", output_key="spec"), *gates], ["actor", *(g["name"] for g in gates)])
    d["gates"].append({"id": "ac-verify", "params": ["commands_from"]})
    return Engine(from_doc(d), ScriptedAdapter({}), run_dir, target=target)


async def _stage(eng, name, cmds, key="spec"):
    """One gate stage: publish the artifact its check reads, then run the gate node as the engine would."""
    eng.artifacts.put(key, None, 1, {"ac_commands": list(cmds)})
    return await eng.runners["gate"].run(eng.ctx, eng.nodes[name], 1, Scope(None, float("inf")))


def _runs(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


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
    out = {e["node"]: e for e in _events(tmp_path / "run", "outcome") if e["event"] == "outcome"}
    assert out["b"]["outcome"] == "pass" and out["b"]["reused_from"] == "a.1"
    assert not [e for e in _events(tmp_path / "run", "gate_result") if e["node"] == "b"], "a reused stage ran nothing"


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
