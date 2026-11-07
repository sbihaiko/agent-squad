"""#1574: a probe child that changes nothing passes its review, and a hold keeps its reason and its needs-human end.

The review command is a stub that returns only an exit code and one stderr line, so the cases read the engine's rules and
never `runreview.sh` (its split is `contrib/squad-goal/scripts/test-runreview.sh`). Exit 7 is a probe with nothing to review
(`review_nochange`, merged as a pass); exit 1 is a hold that carries the first stderr line into the `review_gate` reasons, the
ledger row and the verdict; an exit no case expects (9) is a hold, so a crash never passes as a probe; a hold whose issue ledger
ended needs-human is the verdict `needs-human` (ADR-0029, exit 10), never `failed`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from squad import gates, verdict
from squad.runners import dispatch as D
from tests.gitrepo import sh, sibling_repo
from tests.test_dispatch import CHAINS, ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"
SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
LINE = "RUNREVIEW run-1 route.router.1: the probe has no tree change"


def stub(tmp_path, code, err=LINE):
    """A review command that writes its kind to kind.log, one line to stderr, and exits with `code`."""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho "$SG_CHILD_KIND" >> "{tmp_path}/kind.log"\necho "{err}" >&2\necho "second line" >&2\nexit {code}\n')
    p.chmod(0o755)
    return str(p)


def run_one(tmp_path, code, kind="coding", spawn=None):
    repo = sibling_repo(tmp_path / "t")
    sh(repo, "git", "remote", "add", "origin", "https://github.com/x/y")
    eng = make(tmp_path, {"router": routed(req(task="issue #1574", kind=kind)), CHILD: spawn or writer("a.txt")},
               tweak=gated(stub(tmp_path, code), 30), target=repo, kind_chains={**CHAINS, "probe": CHAINS["coding"]})
    return eng, repo


def ledger_rows(tmp_path):
    return [r for f in sorted((tmp_path / "artifacts").glob("route*.json")) for r in (json.loads(f.read_text()).get("ledger") or [])]


def test_the_probe_exit_is_seven():
    assert gates.REVIEW_NOCHANGE_PROBE_EXIT == D.REVIEW_NOCHANGE_EXIT == 7


@pytest.mark.anyio
@pytest.mark.heavy
async def test_exit_seven_is_review_nochange_and_the_child_merges(tmp_path):
    eng, repo = run_one(tmp_path, 7, kind="probe")
    assert (await eng.run()).verdict == "completed"
    assert (tmp_path / "kind.log").read_text().split() == ["probe"]      # the dispatch names the child's kind to the command
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and land["reasons"] == ["review_nochange"]
    assert land["effect"]["review"]["verdict"] == "no_change"
    assert (repo / "a.txt").exists()
    [gate] = [e for e in ev(tmp_path, "outcome") if e["node"] == "child_review"]
    assert gate["outcome"] == "pass"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_exit_one_holds_with_the_first_stderr_line_in_gate_ledger_and_verdict(tmp_path):
    eng, repo = run_one(tmp_path, 1)
    v = await eng.run()
    assert v.verdict == "failed" and not (repo / "a.txt").exists()
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_held", f"why: {LINE}"]
    [gate] = [e for e in ev(tmp_path, "outcome") if e["node"] == "child_review"]
    assert gate["outcome"] == "fail" and gate["reasons"] == [f"review_held:{CHILD}: {LINE}"]
    assert [r["reasons"] for r in ledger_rows(tmp_path)] == [["review_held", f"why: {LINE}"]]
    assert f"why: {LINE}" in v.reasons and "second line" not in " ".join(v.reasons)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_an_exit_no_case_expects_is_a_hold_never_a_probe_pass(tmp_path):
    eng, repo = run_one(tmp_path, 9, kind="probe")
    v = await eng.run()
    assert v.verdict == "failed"
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"][0] == "review_held" and "review_nochange" not in land["reasons"]
    assert not (repo / "a.txt").exists()


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_hold_with_the_ledger_at_needs_human_is_the_needs_human_verdict(tmp_path, monkeypatch):
    st = tmp_path / "state"
    monkeypatch.setenv("SG_STATE", str(st))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_STATE=str(st), SG_REPO="x/y")
    for rnd in (1, 2, 3):      # three CHANGES in a row end the issue needs-human (ADR-0151 rule 1)
        p = subprocess.run([sys.executable, str(SCRIPTS / "issue_ledger.py"), "review", "x/y#1574", f"r{rnd}", "changes", f"finding {rnd}"],
                           env=env, capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stderr
    eng, _ = run_one(tmp_path, 1)
    streak = "RUNREVIEW run-1 route.router.1: the review streak ended #1574 needs-human, so the run holds its child (frozen:owner)"
    (tmp_path / "review.sh").write_text(f'#!/bin/sh\necho "{streak}" >&2\nexit 1\n')
    v = await eng.run()
    assert v.verdict == "needs-human" and v.exit_code == verdict.EXIT["needs-human"] == 10, v.reasons
    assert f"review_needs_human:{CHILD}" in v.reasons or "review_needs_human" in v.reasons
    assert not any(streak in r for r in v.reasons)      # the reason text attaches to a plain hold only
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "needs-human" and land["reasons"] == ["review_needs_human"]
    [gate] = [e for e in ev(tmp_path, "outcome") if e["node"] == "child_review"]
    assert gate["outcome"] == "needs-human"


def test_the_router_prompt_emits_probe_for_a_report_deliverable():
    root = Path(__file__).resolve().parent.parent
    from squad.workflows import DYNAMIC_SEED
    seed = next(n for n in json.loads(DYNAMIC_SEED.read_text())["nodes"] if n["name"] == "router")["instructions"]
    for text in ((root / "prompts" / "router.md").read_text(), seed):
        assert "`probe`" in text and "report or an issue comment" in text and "never `coding`" in text
    doc = json.loads(DYNAMIC_SEED.read_text())
    assert "probe" in next(n for n in doc["nodes"] if n["kind"] == "dispatch")["allow"]["kinds"]


def test_launch_resolves_a_probe_chain_even_from_a_file_with_none(tmp_path):
    from squad import launch
    assert "probe" in launch.KINDS
    p = tmp_path / "models.json"
    p.write_text(json.dumps({"schema_version": 2, "chains": {k: [{"id": "claude-sonnet-5-5"}] for k in ("router", "coding", "review", "vision")}}))
    assert launch.kind_chains(p)["probe"] == launch.kind_chains(p)["coding"]


def test_the_carried_reason_is_the_last_runreview_line_else_the_first_line():
    first_line = D.Dispatch.first_line
    assert first_line("childreview noise\nRUNREVIEW r c: old\nRUNREVIEW r c: no verdict (#1)\n") == "RUNREVIEW r c: no verdict (#1)"
    assert first_line("\n  plain reason\nsecond") == "plain reason"
    assert first_line(None) == ""
