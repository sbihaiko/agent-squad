"""Ticket #201: a finished run leaves no child branch or worktree in the target; a landing is one work commit and one merge commit."""
import pytest

from squad import cli, git
from squad import verdict as V
from tests.gitrepo import sh, sibling_repo
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
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("a.txt"),
                          "route.router.2": writer("b.txt", cmds=["false"])}, target=repo)
    v = await eng.run()
    assert f"resume: squad resume {tmp_path.resolve()}" in v.message


@pytest.mark.anyio
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
