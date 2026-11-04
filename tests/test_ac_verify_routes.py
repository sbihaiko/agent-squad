"""#862: the final gate routes each held-out command to the tree it reads. A git-history command runs only in its child's
tree; every other command runs only in the target. A red target result is never replaced by another tree's green."""
from types import SimpleNamespace

import anyio
import pytest

from squad import gates, workcmd
from squad.engine import Engine
from squad.gates import GateInput
from squad.runners.dispatch import Dispatch
from tests.gitrepo import sh

SUBJECT = 'test "$(git log -1 --format=%s)" = "{}"'


def verify(cwd, runs):
    """The final gate over one dispatch artifact that carries `ac_runs`: a list of [command, tree] pairs."""
    art = {"ac_runs": runs, "ac_commands": [c for c, _ in runs]}
    return gates.ac_verify({"commands_from": "dispatch"}, GateInput(artifacts={"dispatch": art}, facts={}, cwd=str(cwd)))


def verify_fix(cwd, runs):
    """The final gate of a fix run (#1374): the same dispatch artifact, over a run that repairs a work branch."""
    art = {"ac_runs": runs, "ac_commands": [c for c, _ in runs]}
    return gates.ac_verify({"commands_from": "dispatch"},
                           GateInput(artifacts={"dispatch": art}, facts={}, cwd=str(cwd), fix_run=True))


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


# #1374: a fix run repairs a work branch, and its target is cut from the base, so it never carries the branch's work. Its
# acceptance commands prove in the tree its child wrote, as a history command does (#862), and never fall back to the target.


def test_a_fix_run_proves_a_plain_command_in_the_child_tree(tmp_path):
    """The fix adds a file, and an acceptance command names it: the target, cut from the base, cannot hold it, the child can."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "fix_marker.py").write_text("")
    target = tmp_path / "target"
    target.mkdir()
    cmd = "test -f fix_marker.py"
    r = verify_fix(target, [[cmd, str(child)]])
    assert r.ok, r.findings


def test_a_fix_run_never_falls_back_to_the_target_for_a_plain_command(tmp_path):
    """No fallback (#862's rule): a file the target holds and the branch does not proves nothing about the branch."""
    child = tmp_path / "child"
    child.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "merged.txt").write_text("")
    cmd = "test -f merged.txt"
    r = verify_fix(target, [[cmd, str(child)]])
    assert not r.ok and r.findings == [f"ac_failed:{cmd} @child"]


def test_a_fix_run_whose_child_tree_is_gone_names_it(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "fix_marker.py").write_text("")
    cmd = "test -f fix_marker.py"
    r = verify_fix(target, [[cmd, str(tmp_path / "gone")]])
    assert not r.ok and r.findings == [f"child_tree_missing:{cmd} @gone"]


def test_a_fix_run_with_no_child_tree_recorded_names_it_and_never_runs_in_the_target(tmp_path):
    """The target holds the file, and the gate must not ask it: no recorded tree is named, never silently replaced."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "fix_marker.py").write_text("")
    cmd = "test -f fix_marker.py"
    r = verify_fix(target, [[cmd, None]])
    assert not r.ok and r.findings == [f"child_tree_missing:{cmd}"]


def test_a_run_that_is_not_a_fix_run_still_proves_a_plain_command_in_the_target(tmp_path):
    """An implement run is unchanged: its router lands the children into the target before the gate."""
    child = tmp_path / "child"
    child.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "merged.txt").write_text("")
    cmd = "test -f merged.txt"
    r = verify(target, [[cmd, str(child)]])
    assert r.ok, r.findings


def test_the_fix_run_mark_is_read_from_the_run_start_the_start_recorded():
    """#1374: the run reads the option `run_start` recorded, so a resume gives the same answer the start gave."""
    def mark(events):
        eng = Engine.__new__(Engine)          # the reader touches the record only
        eng.record = SimpleNamespace(events=events)
        return eng.fix_run

    start = {"event": "run_start", "engine_options": {"fix_run": True}}
    assert mark([start, {"event": "resume"}])
    assert not mark([{"event": "run_start", "engine_options": {"keep_children": True}}])
    assert not mark([])


def test_the_dispatch_artifact_names_the_child_tree_for_a_fix_runs_plain_command(tmp_path):
    """#1374: the gate can route a plain command to the branch only when the artifact names that tree."""
    tree = tmp_path / "child"
    tree.mkdir()
    d = Dispatch.__new__(Dispatch)
    d.children = {"c1": {"definition": {"name": "c1"}}}
    d.e = SimpleNamespace(fix_run=True, artifacts={"c1": {"ac_commands": ["pytest -q"]}},
                          runs=SimpleNamespace(workspace=lambda name, definition: tree))
    d.ledger = lambda name: [{"child": "c1", "outcome": "pass"}]
    assert d.payload("route")[2] == [["pytest -q", str(tree)]]
    d.e.fix_run = False
    assert d.payload("route")[2] == [["pytest -q", None]]


def test_the_landing_check_proves_a_fix_runs_plain_command_in_the_child_tree(tmp_path, monkeypatch):
    """#1374: the same rule at the landing check, so a plain command reads the branch under repair there too."""
    tree, target = tmp_path / "child", tmp_path / "target"
    tree.mkdir()
    target.mkdir()
    seen = []

    def fake_run(c, w, **kw):
        seen.append(w)
        return workcmd.CmdResult(True)

    monkeypatch.setattr(workcmd, "run", fake_run)
    d = Dispatch.__new__(Dispatch)
    d.e = SimpleNamespace(fix_run=True, target=str(target))
    d.landed_commands = lambda extra=None: [("c1", ["pytest -q"])]
    d.child_tree = lambda name: str(tree)
    got, _ = anyio.run(d.ac_survey)
    assert got == {"c1": True} and seen == [str(tree)]
