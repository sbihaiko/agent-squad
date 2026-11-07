"""Ticket #201: a finished run leaves no child branch or worktree in the target; a landing is one work commit and one merge commit.

A run the loop started is the exception (#464, ADR-0145 rule 9): its target is a linked worktree that holds its run folder
(#90), and the loop reads the child's worktree again after the verdict -- it enters the child's commits into the run's wave
branch and reviews the child's own diff there. A tree removed at the verdict leaves that entry with nothing to read, so the
wave landed empty for every run and no card ever left the queue. The loop's own pass removes the tree once the wave landed.
"""
import pytest

from squad import cli, git
from squad import verdict as V
from squad.record import read
from tests.gitrepo import make_repo, sh, sibling_repo
from tests.test_dispatch import make, req, routed
from tests.test_waves import lands, writer


def branches(repo):
    return sh(repo, "git", "branch", "--format=%(refname:short)").split()


def worktrees(repo):
    return [ln for ln in sh(repo, "git", "worktree", "list", "--porcelain").splitlines() if ln.startswith("worktree ")]


@pytest.mark.anyio
async def test_a_completed_run_prunes_the_landed_childs_branch_and_worktree(tmp_path):
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, target=repo)
    assert (await eng.run()).verdict == "completed"
    assert (repo / "a.txt").exists() and branches(repo) == ["main"] and len(worktrees(repo)) == 1
    assert not list((tmp_path / "ws").glob("*child*"))
    assert sh(repo, "git", "status", "--porcelain") == ""


def loop_run(tmp_path, script, **kw):
    """A run of the loop's own shape (#90): a worktree of the issue branch, next to the checkout, with the run folder inside it."""
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "wt-as-464"
    sh(repo, "git", "worktree", "add", "-q", "-b", "gui/464", str(wt))
    return repo, wt, make(wt / "runs" / "run-l1", script, target=wt, run_id="run-l1", **kw)


@pytest.mark.anyio
async def test_a_loop_run_keeps_the_landed_childs_worktree_and_branch_for_the_wave_entry(tmp_path):
    """The wave entry reads the child's worktree and branch after the run's verdict, so a run that cuts a wave keeps them;
    the loop's own pass removes them once the wave landed."""
    repo, wt, eng = loop_run(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, options={"keep_children": True})
    assert (await eng.run()).verdict == "completed"
    child = wt / "runs" / "run-l1" / "ws" / "worktrees_child-route.router.1"
    assert child.is_dir() and (child / "a.txt").exists(), "the entry has no worktree to read the child's commits from"
    assert (child / ".git").exists()
    assert "squad/run-l1/worktrees_child-route.router.1" in branches(repo)


@pytest.mark.anyio
async def test_a_wave_run_whose_target_fails_ownership_keeps_the_landed_childs_worktree_and_branch(tmp_path):
    """The hold reads the run's wave, never the target's shape (#477): a target outside the run area still keeps the child
    the wave entry reads after the verdict."""
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, target=repo, options={"keep_children": True})
    assert (await eng.run()).verdict == "completed"
    child = tmp_path / "ws" / "worktrees_child-route.router.1"
    assert child.is_dir() and (child / "a.txt").exists()
    assert any(b.startswith("squad/") for b in branches(repo))
    start = next(e for e in read(tmp_path / "record.jsonl") if e["event"] == "run_start")
    assert start["engine_options"].get("keep_children") is True


@pytest.mark.anyio
@pytest.mark.heavy   # one run to a landed child, then its prune: 1.5 s here, 2 s on a CI runner
async def test_a_loop_shaped_run_with_no_wave_prunes_its_landed_children(tmp_path):
    """The shape of the target is not the hold: a run in the loop's shape that cut no wave prunes as any completed run does."""
    repo, wt, eng = loop_run(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")})
    assert (await eng.run()).verdict == "completed"
    assert not (wt / "runs" / "run-l1" / "ws" / "worktrees_child-route.router.1").exists()
    assert not any(b.startswith("squad/") for b in branches(repo))


def test_keep_children_is_an_engine_option_of_start_and_resume(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_start", lambda a: seen.append(cli._opts(a)) or 0)
    assert cli.main(["start", "--target", ".", "--keep-children", "do it"]) == 0
    assert seen == [{"keep_children": True}]


@pytest.mark.heavy   # one run to a failed verdict, then the branch and worktree it keeps: 2.0 s on a CI runner
@pytest.mark.anyio
async def test_a_failed_run_keeps_the_landed_childs_branch_and_worktree_for_resume(tmp_path):
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("a.txt"),
                          "route.router.2": writer("b.txt", cmds=["false"])}, target=repo)
    v = await eng.run()
    assert v.verdict == "failed"
    assert len(lands(tmp_path)) >= 1 and any(b.startswith("squad/") for b in branches(repo)) and len(worktrees(repo)) > 1


def test_the_resume_hint_names_the_run_folder_and_quotes_it():
    assert V._resume_ref("/a b/r1", "r1") == "'/a b/r1'" and V._resume_ref("/runs/r1", "r1") == "/runs/r1"
    assert V._resume_ref(None, "r1") == "r1" and V._resume_ref(None, None) == "<run-id>"


@pytest.mark.anyio
async def test_the_verdict_hint_of_a_run_works_from_any_directory(tmp_path):
    """The hint names the run folder, not the run id, so a resume works from any directory. It rides a failed verdict that
    still has a node to run: a run whose plan is fully folded has nothing to resume and prints no hint."""
    repo = sibling_repo(tmp_path)
    sensitive = {"files": [{"path": "src/auth/login.py"}]}     # the security gate fails this child, so `final_gate` never runs
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("a.txt"),
                          "route.router.2": writer("b.txt", diff=sensitive)}, target=repo)
    v = await eng.run()
    assert v.verdict == "failed" and v.remains == ["final_gate"]
    assert f"resume: squad resume {tmp_path.resolve()}" in v.message


@pytest.mark.anyio
@pytest.mark.heavy   # one landing written and read back: 1.4 s here, 2 s on a CI runner
async def test_a_landing_is_one_work_commit_and_one_merge_commit_not_two_landings(tmp_path):
    """Field run field3 showed two `squad: land` lines for one child: the worktree commit and the --no-ff merge share one message."""
    repo = sibling_repo(tmp_path)
    base = git.head(repo)
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, target=repo)
    await eng.run()
    rows = [ln.split() for ln in sh(repo, "git", "log", f"{base}..HEAD", "--format=%H %P").splitlines()]
    merge = [r for r in rows if len(r) == 3]
    work = [r for r in rows if len(r) == 2]
    assert len(rows) == 2 and len(merge) == 1 and len(work) == 1 and merge[0][2] == work[0][0] and merge[0][1] == base
    assert len(lands(tmp_path)) == 1


@pytest.mark.parametrize("name", ["work", "task"])
def test_work_and_task_are_aliases_of_start(name, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_start", lambda a: seen.append(a) or 0)
    assert cli.main([name, "--target", ".", "do it"]) == 0
    assert seen[0].cmd == "start" and seen[0].text == ["do it"] and seen[0].task is (name == "task")


def test_resume_takes_the_absolute_run_folder_from_another_directory(tmp_path, monkeypatch):
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    assert cli._run_folder("runs", str(run)) == run
