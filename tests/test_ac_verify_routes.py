"""#862: the final gate routes each held-out command to the tree it reads. A git-history command runs only in its child's
tree; every other command runs only in the target. A red target result is never replaced by another tree's green."""
import pytest

from squad import gates
from squad.gates import GateInput
from tests.gitrepo import sh

SUBJECT = 'test "$(git log -1 --format=%s)" = "{}"'


def verify(cwd, runs):
    """The final gate over one dispatch artifact that carries `ac_runs`: a list of [command, tree] pairs."""
    art = {"ac_runs": runs, "ac_commands": [c for c, _ in runs]}
    return gates.ac_verify({"commands_from": "dispatch"}, GateInput(artifacts={"dispatch": art}, facts={}, cwd=str(cwd)))


def repo_with_head(path, subject):
    """A repository whose HEAD commit has the given subject."""
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "git", "init", "-q", "-b", "main")
    sh(path, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", subject)
    return path


@pytest.mark.parametrize("cmd", [
    "git log -1 --format=%s", 'test "$(git log -1 --format=%s)" = "x"', "git -C sub log -1", "git --no-pager show HEAD",
    "git rev-parse HEAD", "git rev-list --count HEAD", "git blame a.txt", "git merge-base main HEAD",
    "git diff HEAD~1", "git diff main..dev", "git cherry -v main", "git show HEAD:a.txt",
])
def test_a_git_history_command_is_routed_to_its_child_tree(cmd):
    from squad.workcmd import reads_history
    assert reads_history(cmd)


@pytest.mark.parametrize("cmd", [
    "test ! -e b.txt", "git status --porcelain", "git diff --stat", "git commit -qm log", "echo git log", "pytest -q",
    "git -c user.name=t commit -m show",
])
def test_any_other_command_runs_in_the_target(cmd):
    from squad.workcmd import reads_history
    assert not reads_history(cmd)


def test_a_history_command_passes_in_its_child_tree_when_the_target_head_is_a_merge(tmp_path):
    child = repo_with_head(tmp_path / "child", "child one")
    target = repo_with_head(tmp_path / "target", "merge of child one and child two")
    r = verify(target, [[SUBJECT.format("child one"), str(child)]])
    assert r.ok, r.findings


def test_a_history_command_red_in_its_child_tree_names_that_tree(tmp_path):
    child = repo_with_head(tmp_path / "route.router.1", "child one")
    target = repo_with_head(tmp_path / "target", "merge")
    cmd = SUBJECT.format("other")
    r = verify(target, [[cmd, str(child)]])
    assert not r.ok and r.findings == [f"ac_failed:{cmd} @route.router.1"]


def test_a_plain_command_green_in_the_child_tree_and_red_in_the_target_stays_red(tmp_path):
    """The reviewer's case: a child's check passes in its own tree and fails once a sibling's file is merged in."""
    child = tmp_path / "child"
    child.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "b.txt").write_text("merged from a sibling\n")
    cmd = "test ! -e b.txt"
    r = verify(target, [[cmd, str(child)]])
    assert not r.ok and r.findings == [f"ac_failed:{cmd}"]


def test_a_history_command_whose_child_tree_is_gone_fails_as_child_tree_missing(tmp_path):
    target = repo_with_head(tmp_path / "target", "merge")
    cmd = SUBJECT.format("child one")
    r = verify(target, [[cmd, str(tmp_path / "gone")]])
    assert not r.ok and r.findings == [f"child_tree_missing:{cmd} @gone"]
    assert "gone" in r.evidence[f"child_tree_missing:{cmd} @gone"]


def test_a_history_command_with_no_child_tree_recorded_fails_as_child_tree_missing(tmp_path):
    target = repo_with_head(tmp_path / "target", "merge")
    cmd = SUBJECT.format("child one")
    r = verify(target, [[cmd, None]])
    assert not r.ok and r.findings == [f"child_tree_missing:{cmd}"]


def test_a_history_command_never_runs_in_the_target_when_its_child_tree_is_missing(tmp_path):
    """No silent fallback: the target's HEAD could satisfy the command, and the gate must not ask it."""
    target = repo_with_head(tmp_path / "target", "child one")
    r = verify(target, [[SUBJECT.format("child one"), str(tmp_path / "gone")]])
    assert not r.ok and r.findings[0].startswith("child_tree_missing:")


def test_a_shared_plain_command_runs_once_in_the_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    cmd = "echo run >> runs.txt"
    r = verify(target, [[cmd, None], [cmd, None]])
    assert r.ok and (target / "runs.txt").read_text().count("run") == 1


def test_evidence_of_one_command_in_two_child_trees_is_kept_per_tree(tmp_path):
    a = repo_with_head(tmp_path / "route.router.1", "subject-a")
    b = repo_with_head(tmp_path / "route.router.2", "subject-b")
    target = repo_with_head(tmp_path / "target", "merge")
    cmd = "git log -1 --format=%s; exit 1"
    r = verify(target, [[cmd, str(a)], [cmd, str(b)]])
    got = {k: v for k, v in r.evidence.items()}
    assert sorted(r.findings) == sorted([f"ac_failed:{cmd} @route.router.1", f"ac_failed:{cmd} @route.router.2"])
    assert "subject-a" in got[f"ac_failed:{cmd} @route.router.1"] and "subject-b" in got[f"ac_failed:{cmd} @route.router.2"]


def test_a_non_dispatch_artifact_runs_every_command_in_the_target(tmp_path):
    """A spec's commands name no child: they run in the target as before, history or not."""
    target = repo_with_head(tmp_path / "target", "merge")
    r = gates.ac_verify({"commands_from": "spec"}, GateInput(artifacts={"spec": {"ac_commands": [SUBJECT.format("merge")]}}, facts={}, cwd=str(target)))
    assert r.ok, r.findings



def test_a_dispatch_artifact_with_no_ac_runs_never_runs_a_history_command_in_the_target(tmp_path):
    """#867: an artifact an older engine wrote has the dispatch keys but no `ac_runs`, so it names no child tree. Its history
    command must fail as child_tree_missing; it must never run in the target, whose HEAD can satisfy it."""
    target = repo_with_head(tmp_path / "target", "child one")
    cmd = SUBJECT.format("child one")
    art = {"ac_commands": [cmd], "children": ["route.router.1"], "ledger": [{"child": "route.router.1", "outcome": "pass"}]}
    r = gates.ac_verify({"commands_from": "route"}, GateInput(artifacts={"route": art}, facts={}, cwd=str(target)))
    assert not r.ok and r.findings == [f"child_tree_missing:{cmd}"]
